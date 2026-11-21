"""Token buckets, provider pauses from headers, and the request log audit."""

import random

import pytest
from helpers import make_store

from agentqueue import ratelimit
from agentqueue.errors import PermanentError
from agentqueue.ratelimit import Limits, RateWait, acquire, audit_calls


def lim(**kw):
    base = dict(provider="p", rpm=60, rpd=1000, tpm=6000, burst_requests=5, burst_tokens=600)
    base.update(kw)
    return Limits(**base)


def test_bucket_math_caps_every_window():
    lm = lim()
    b = lm.buckets()
    assert b["rpm"] == (5, 55 / 60)
    assert b["tpm"] == (600, 5400 / 60)


def test_naive_bucket_can_double_the_limit():
    """Broken case: capacity = limit and rate = limit/60 lets 2x the limit into one minute."""
    calls = [(0.0, 1)] * 60 + [(59.9 + i / 1000, 1) for i in range(59)]
    naive = lim(burst_requests=60)
    assert naive.buckets()["rpm"] == (60, 0.0)
    assert audit_calls(calls, lim()) != []


def test_acquire_waits_when_budget_is_gone(tmp_path):
    st = make_store(tmp_path)
    lm = lim()
    for _ in range(5):
        acquire(st, lm, 10)
    with pytest.raises(RateWait) as w:
        acquire(st, lm, 10)
    assert w.value.wait == pytest.approx(60 / 55, rel=1e-6)
    st.clock.advance(w.value.wait)
    acquire(st, lm, 10)


def test_tokens_per_minute_budget(tmp_path):
    st = make_store(tmp_path)
    lm = lim(rpm=1000, burst_requests=100)
    acquire(st, lm, 500)
    with pytest.raises(RateWait) as w:
        acquire(st, lm, 500)
    assert "rate budget" in w.value.reason


def test_call_larger_than_burst_is_permanent(tmp_path):
    st = make_store(tmp_path)
    with pytest.raises(PermanentError):
        acquire(st, lim(), 601)


def test_scheduler_log_never_breaks_a_bucket(tmp_path):
    """Hammer acquire from a greedy client and audit the log it wrote."""
    st = make_store(tmp_path)
    lm = lim()
    r = random.Random(3)
    for _ in range(3000):
        try:
            acquire(st, lm, r.randint(1, 200))
        except RateWait as w:
            st.clock.advance(w.wait * r.uniform(0.0, 1.2))
        st.clock.advance(r.uniform(0, 0.3))
    log = st.conn.execute("SELECT reserved_at, tokens FROM calls").fetchall()
    assert len(log) > 300
    assert audit_calls([tuple(x) for x in log], lm) == []


def test_audit_catches_a_burst():
    calls = [(0.0, 1)] * 6
    assert any("rpm" in p for p in audit_calls(calls, lim()))


def test_retry_after_pauses_the_provider(tmp_path):
    st = make_store(tmp_path)
    lm = lim()
    cid = acquire(st, lm, 1)
    ratelimit.report(st, lm, cid, "rate_limited", {"Retry-After": "2"})
    with pytest.raises(RateWait) as w:
        acquire(st, lm, 1)
    assert w.value.wait == pytest.approx(2.0)
    st.clock.advance(2.01)
    acquire(st, lm, 1)


@pytest.mark.parametrize(
    "headers,wait",
    [
        ({"retry-after": "1.5"}, 1.5),
        ({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "6m0s"}, 360.0),
        ({"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "250ms"}, 0.25),
        ({"x-ratelimit-remaining-requests": "5", "x-ratelimit-reset-requests": "9s"}, None),
        ({"retry-after": "soon"}, None),
        ({}, None),
    ],
)
def test_pause_from_headers(headers, wait):
    got = ratelimit.pause_from_headers(headers)
    assert got == (pytest.approx(wait) if wait is not None else None)


def test_parse_limits_from_toml(tmp_path):
    p = tmp_path / "limits.toml"
    p.write_text(
        '[providers.groq]\nrpm = 30\nrpd = 14400\ntpm = 6000\nurl = "http://127.0.0.1:1"\n'
    )
    got = ratelimit.load_limits(p)["groq"]
    assert (got.rpm, got.burst_requests, got.burst_tokens, got.url) == (
        30,
        3,
        600,
        "http://127.0.0.1:1",
    )


def test_bad_burst_is_rejected():
    with pytest.raises(ValueError):
        ratelimit.parse_limits(
            {"providers": {"x": {"rpm": 5, "rpd": 5, "tpm": 5, "burst_requests": 9}}}
        )


def test_buckets_survive_a_new_store_handle(tmp_path):
    """Broken case: buckets kept in memory refill to full when a worker restarts."""
    st = make_store(tmp_path)
    lm = lim()
    for _ in range(5):
        acquire(st, lm, 1)
    from agentqueue.store import Store

    st2 = Store(st.path, st.clock)
    with pytest.raises(RateWait):
        acquire(st2, lm, 1)

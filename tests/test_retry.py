"""Retry policy by error class, full-jitter backoff, the circuit breaker, dead letters."""

import random

import pytest
from helpers import limits, make_store, rng

from agentqueue import ratelimit, worker
from agentqueue.errors import (
    PermanentError,
    RateLimited,
    ServerError,
    TransientError,
    backoff,
    classify_status,
    retry_delay,
)
from agentqueue.ratelimit import RateWait


@pytest.mark.parametrize(
    "status,cls,retry",
    [
        (429, RateLimited, True),
        (500, ServerError, True),
        (503, ServerError, True),
        (400, PermanentError, False),
        (404, PermanentError, False),
    ],
)
def test_classify(status, cls, retry):
    e = classify_status(status)
    assert isinstance(e, cls) and e.retryable is retry


def test_backoff_is_full_jitter_and_capped():
    r = random.Random(1)
    for attempt in range(1, 12):
        cap = min(30.0, 0.5 * 2 ** (attempt - 1))
        xs = [backoff(attempt, r) for _ in range(200)]
        assert all(0 <= x < cap for x in xs) and max(xs) > cap * 0.8 and min(xs) < cap * 0.2


def test_retry_after_is_a_floor():
    e = RateLimited("429", retry_after=7.0)
    assert retry_delay(e, 1, random.Random(0)) == 7.0


ERRS = {"kind": None}


@worker.job("t_err")
def t_err(ctx, payload):
    return ctx.step("s", lambda: (_ for _ in ()).throw(ERRS["kind"]))


@pytest.mark.parametrize(
    "err,state",
    [
        (PermanentError("400"), "failed"),
        (ServerError("500"), "waiting"),
        (TransientError("t"), "waiting"),
        (RateLimited("429", 3), "waiting"),
    ],
)
def test_error_class_decides(tmp_path, err, state):
    ERRS["kind"] = err
    st = make_store(tmp_path)
    jid, _ = st.submit("t_err", {}, "k")
    worker.run_once(st, {}, rng())
    assert st.job(jid)["state"] == state


def test_exhausted_retries_go_to_dead_letters_and_requeue(tmp_path):
    ERRS["kind"] = ServerError("500 again")
    st = make_store(tmp_path)
    jid, _ = st.submit("t_err", {}, "k", max_attempts=3)
    for _ in range(3):
        worker.run_once(st, {}, rng())
        st.clock.advance(100)
    assert st.job(jid)["state"] == "dead"
    dl = st.dead_letters()
    assert len(dl) == 1 and "500 again" in dl[0]["error"] and dl[0]["attempts"] == 3
    assert st.requeue(jid) and st.job(jid)["state"] == "queued"


def test_rate_wait_does_not_use_an_attempt(tmp_path):
    ERRS["kind"] = RateWait(5, "rate budget")
    st = make_store(tmp_path)
    jid, _ = st.submit("t_err", {}, "k", max_attempts=1)
    for _ in range(4):
        worker.run_once(st, {}, rng())
        st.clock.advance(10)
    row = st.job(jid)
    assert row["state"] == "waiting" and row["attempts"] == 0


def test_breaker_opens_after_failures_and_half_opens(tmp_path):
    """Broken case: a provider that is down keeps getting hammered by every job."""
    st = make_store(tmp_path)
    lm = limits()["fake"]  # breaker_failures=3, cooldown 5 s
    for _ in range(3):
        cid = ratelimit.acquire(st, lm, 1)
        ratelimit.report(st, lm, cid, "server_error")
    with pytest.raises(RateWait) as w:
        ratelimit.acquire(st, lm, 1)
    assert w.value.reason == "circuit open" and w.value.wait == pytest.approx(5.0)
    st.clock.advance(5)
    trial = ratelimit.acquire(st, lm, 1)  # one trial is allowed
    with pytest.raises(RateWait):
        ratelimit.acquire(st, lm, 1)  # others wait while it runs
    ratelimit.report(st, lm, trial, "ok")
    ratelimit.acquire(st, lm, 1)  # closed again


def test_failed_trial_reopens(tmp_path):
    st = make_store(tmp_path)
    lm = limits()["fake"]
    for _ in range(3):
        ratelimit.report(st, lm, ratelimit.acquire(st, lm, 1), "timeout")
    st.clock.advance(5)
    ratelimit.report(st, lm, ratelimit.acquire(st, lm, 1), "server_error")
    with pytest.raises(RateWait) as w:
        ratelimit.acquire(st, lm, 1)
    assert w.value.reason == "circuit open"


def test_lost_trial_does_not_stick_half_open(tmp_path):
    """Broken case: the trial call's worker dies and the breaker stays half open forever."""
    st = make_store(tmp_path)
    lm = limits()["fake"]
    for _ in range(3):
        ratelimit.report(st, lm, ratelimit.acquire(st, lm, 1), "server_error")
    st.clock.advance(5)
    ratelimit.acquire(st, lm, 1)  # trial taken, worker dies, no report
    st.clock.advance(5)
    ratelimit.acquire(st, lm, 1)  # a new trial starts


def test_429_does_not_trip_the_breaker(tmp_path):
    st = make_store(tmp_path)
    lm = limits()["fake"]
    for _ in range(10):
        ratelimit.report(st, lm, ratelimit.acquire(st, lm, 1), "rate_limited")
    row = st.conn.execute("SELECT breaker FROM providers").fetchone()
    assert row["breaker"] == "closed"

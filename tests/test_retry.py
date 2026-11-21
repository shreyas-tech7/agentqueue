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


@worker.job("t_two_calls")
def t_two_calls(ctx, payload):
    return ctx.step("both", lambda: [ctx.model("one"), ctx.model("two")])


def test_step_with_two_calls_and_burst_of_one_finishes(tmp_path):
    """Broken case: a rate wait threw the step away, the replay re-sent call 1, for ever."""
    from agentqueue.fakeprovider import FakeProvider

    with FakeProvider() as fp:
        st = make_store(tmp_path)
        lim = limits(fp.url, rpm=10, burst_requests=1)
        jid, _ = st.submit("t_two_calls", {}, "k", max_attempts=2)
        for _ in range(10):
            worker.run_once(st, lim, rng())
            if st.job(jid)["state"] == "succeeded":
                break
            st.clock.advance(10)
        assert st.job(jid)["state"] == "succeeded"
        assert fp.snapshot()["requests"] == 2  # each call went out once


def test_lost_lease_during_finish_does_not_kill_the_worker(tmp_path):
    """Broken case: finish() raised LostLease out of run_once and the worker process died."""
    ERRS["kind"] = TransientError("t")
    st = make_store(tmp_path)
    jid, _ = st.submit("t_slow", {}, "k")

    @worker.job("t_slow")
    def t_slow(ctx, payload):
        st.clock.advance(100)  # the lease runs out while the job runs
        other = st.claim(lease_s=30)  # another worker takes over
        assert other is not None
        raise TransientError("late")

    assert worker.run_once(st, {}, rng(), lease_s=1) is True
    assert st.job(jid)["state"] == "running"  # still owned by the other worker


def test_bad_reply_is_a_server_error(tmp_path):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b"not json"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st = make_store(tmp_path)
        jid, _ = st.submit("t_two_calls", {}, "k")
        worker.run_once(st, limits(f"http://127.0.0.1:{srv.server_address[1]}"), rng())
        row = st.job(jid)
        assert row["state"] == "waiting" and "bad reply" in row["last_error"]
    finally:
        srv.shutdown()


@worker.job("t_not_json")
def t_not_json(ctx, payload):
    return {1, 2}


def test_non_json_result_fails_permanently(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("t_not_json", {}, "k")
    worker.run_once(st, {}, rng())
    assert st.job(jid)["state"] == "failed"

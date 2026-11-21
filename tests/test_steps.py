"""Durable steps and replay."""

import pytest
from helpers import limits, make_store, rng

from agentqueue import worker
from agentqueue.worker import REGISTRY, Context, Crasher, DuplicateStep

CALLS = []


@worker.job("t_three")
def three(ctx, payload):
    a = ctx.step("a", lambda: CALLS.append("a") or 1)
    b = ctx.step("b", lambda: CALLS.append("b") or a + 1)
    if payload.get("boom") and not payload.get("_done"):
        raise RuntimeError("crash between steps")
    return ctx.step("c", lambda: CALLS.append("c") or b + 1)


def test_finished_steps_replay_without_running(tmp_path):
    """Broken case: after a crash every step runs again and repeats paid model calls."""
    CALLS.clear()
    st = make_store(tmp_path)
    jid, _ = st.submit("t_three", {"boom": True}, "k")
    worker.run_once(st, {}, rng())
    assert CALLS == ["a", "b"] and st.job(jid)["state"] == "waiting"
    # The fix ships: the job code no longer raises. Only step c is new work.
    with st.tx() as c:
        c.execute(
            "UPDATE jobs SET payload = ? WHERE id = ?", ('{"boom": true, "_done": true}', jid)
        )
    st.clock.advance(60)
    worker.run_once(st, {}, rng())
    assert CALLS == ["a", "b", "c"]
    assert st.job(jid)["state"] == "succeeded" and st.job(jid)["result"] == "3"


def test_step_results_are_saved(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("t_three", {}, "k")
    worker.run_once(st, {}, rng())
    assert st.load_steps(jid) == {"a": 1, "b": 2, "c": 3}


def test_duplicate_step_name_fails_the_job(tmp_path):
    @worker.job("t_dup")
    def dup(ctx, payload):
        ctx.step("x", lambda: 1)
        ctx.step("x", lambda: 2)

    st = make_store(tmp_path)
    jid, _ = st.submit("t_dup", {}, "k")
    worker.run_once(st, {}, rng())
    assert st.job(jid)["state"] == "failed"
    assert "duplicate step" in st.job(jid)["last_error"]


def test_step_result_must_be_json(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    ctx = Context(st, jb, {}, Crasher(None), 30)
    with pytest.raises(TypeError):
        ctx.step("s", lambda: object())
    assert st.load_steps(jid) == {}
    with pytest.raises(DuplicateStep):
        ctx.step("s", lambda: 1)


def test_emit_and_model_outside_a_step_are_refused(tmp_path):
    st = make_store(tmp_path)
    st.submit("k", {}, "k")
    ctx = Context(st, st.claim(), limits(), Crasher(None), 30)
    with pytest.raises(RuntimeError):
        ctx.emit("sqlite:x", {})
    with pytest.raises(RuntimeError):
        ctx.model("hi")


def test_replay_key_is_job_plus_step_name(tmp_path):
    st = make_store(tmp_path)
    j1, _ = st.submit("t_three", {}, "k1")
    j2, _ = st.submit("t_three", {}, "k2")
    worker.run_once(st, {}, rng())
    assert st.load_steps(j1) and not st.load_steps(j2)


def test_registry_has_examples():
    import agentqueue.examples  # noqa: F401

    for kind in ("summarize", "classify", "send_email", "bench", "bench_naive"):
        assert kind in REGISTRY

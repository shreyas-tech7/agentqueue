"""Every transition in the table, every illegal one, and a crash in each state."""

import pytest
from helpers import make_store, rng

from agentqueue import worker
from agentqueue.states import STATES, TERMINAL, TRANSITIONS, BadTransition, next_state


@pytest.mark.parametrize("pair,new", sorted(TRANSITIONS.items()))
def test_each_listed_transition(pair, new):
    assert next_state(*pair) == new


def test_every_other_pair_is_refused():
    """Broken case: a job moved from succeeded back to running would run its steps again."""
    events = {e for _, e in TRANSITIONS}
    for s in STATES:
        for e in events:
            if (s, e) not in TRANSITIONS:
                with pytest.raises(BadTransition):
                    next_state(s, e)


def test_terminal_states_only_leave_by_requeue():
    for (s, e), _ in TRANSITIONS.items():
        if s in TERMINAL:
            assert e == "requeue"
    assert not any(s == "succeeded" for s, _ in TRANSITIONS)


def test_store_refuses_illegal_move(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "a")
    with st.tx() as c, pytest.raises(BadTransition):
        st._move(c, jid, "queued", "succeed")


# A crash in each state: the worker process vanishes and leaves the row as it was.
def _crash_in(st, state):
    jid, _ = st.submit("k", {}, f"key-{state}", max_attempts=3)
    jb = st.claim(lease_s=5)
    if state == "running":
        pass
    elif state == "waiting":
        st.finish(jid, jb.lease_token, "retry", error="x", run_after=st.clock.now() + 1)
    elif state == "succeeded":
        st.finish(jid, jb.lease_token, "succeed", result=1)
    elif state == "failed":
        st.finish(jid, jb.lease_token, "fail", error="bad input")
    elif state == "dead":
        st.finish(jid, jb.lease_token, "exhaust", error="gave up")
    elif state == "queued":
        st.finish(jid, jb.lease_token, "rate_wait", error="w", run_after=0)
        with st.tx() as c:
            c.execute("UPDATE jobs SET state='queued' WHERE id=?", (jid,))
    return jid


@pytest.mark.parametrize("state", STATES)
def test_crash_in_each_state_recovers(tmp_path, state):
    st = make_store(tmp_path)
    jid = _crash_in(st, state)
    assert st.job(jid)["state"] == state
    st.clock.advance(10)  # past any lease and retry time
    got = st.claim(lease_s=5)
    if state in TERMINAL:
        assert got is None and st.job(jid)["state"] == state
    else:
        assert got is not None and got.id == jid and st.job(jid)["state"] == "running"


def test_crash_in_running_with_no_attempts_left_goes_dead(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "a", max_attempts=1)
    st.claim(lease_s=5)
    st.clock.advance(10)
    assert st.claim() is None
    assert st.job(jid)["state"] == "dead"
    assert "worker died" in st.dead_letters()[0]["error"]


def test_dead_owner_pid_is_recovered_before_the_lease_ends(tmp_path):
    """Broken case: a job whose worker died stays stuck in running until a long lease ends."""
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "a")
    st.claim(lease_s=3600)
    with st.tx() as c:
        c.execute("UPDATE jobs SET owner_pid = 999999999 WHERE id = ?", (jid,))
    assert st.claim().id == jid


def test_live_owner_keeps_the_job(tmp_path):
    st = make_store(tmp_path)
    st.submit("k", {}, "a")
    st.claim(lease_s=3600)
    assert st.claim() is None


def test_requeue_resets_attempts_and_keeps_steps(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "a", max_attempts=1)
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "s1", 1, [], [])
    st.finish(jid, jb.lease_token, "exhaust", error="x")
    assert st.requeue(jid) and not st.requeue(jid)
    row = st.job(jid)
    assert row["state"] == "queued" and row["attempts"] == 0
    assert st.load_steps(jid) == {"s1": 1} and st.dead_letters() == []


def test_lost_lease_blocks_old_worker(tmp_path):
    """Broken case: a worker presumed dead commits over the job's new owner."""
    from agentqueue.store import LostLease

    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "a")
    old = st.claim(lease_s=1)
    st.clock.advance(2)
    new = st.claim(lease_s=30)
    assert new.id == jid and new.lease_token != old.lease_token
    with pytest.raises(LostLease):
        st.commit_step(jid, old.lease_token, "s", 1, [], [])
    with pytest.raises(LostLease):
        st.finish(jid, old.lease_token, "succeed", result=1)


def test_unknown_kind_fails_permanently(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("no-such-kind", {}, "a")
    worker.run_once(st, {}, rng())
    assert st.job(jid)["state"] == "failed"

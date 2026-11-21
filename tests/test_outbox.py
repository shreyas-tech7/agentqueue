"""The outbox commits with its step, and the dispatcher sends each entry until it lands."""

import pytest
from helpers import make_store, rng

import agentqueue.examples  # noqa: F401  (registers the example jobs)
from agentqueue import worker
from agentqueue.errors import PermanentError
from agentqueue.receiver import SqliteReceiver, send
from agentqueue.store import LostLease


def test_outbox_entry_commits_with_step(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "send", "ok", [], [("sqlite:r", {"a": 1}), ("sqlite:r", 2)])
    rows = st.conn.execute("SELECT dedupe_key, state FROM outbox ORDER BY idx").fetchall()
    sid = st.store_id
    assert [tuple(r) for r in rows] == [
        (f"{sid}:{jid}:send:0", "pending"),
        (f"{sid}:{jid}:send:1", "pending"),
    ]


def test_dedupe_keys_differ_between_stores(tmp_path):
    """Broken case: a reset database reuses key 1:send:0 and the receiver drops a new email."""
    a, b = make_store(tmp_path, "a.sqlite"), make_store(tmp_path, "b.sqlite")
    assert a.store_id != b.store_id
    assert make_store(tmp_path, "a.sqlite").store_id == a.store_id  # stable for one file


def test_failed_step_commit_writes_no_outbox(tmp_path):
    """Broken case: an email queued by a step whose result was never saved goes out anyway."""
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim(lease_s=1)
    st.clock.advance(2)
    st.claim()  # another worker takes over
    with pytest.raises(LostLease):
        st.commit_step(jid, jb.lease_token, "send", "ok", [], [("sqlite:r", {})])
    assert st.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    assert st.load_steps(jid) == {}


def test_dispatcher_delivers_and_marks_sent(tmp_path):
    st = make_store(tmp_path)
    target = f"sqlite:{tmp_path / 'r.sqlite'}"
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "send", "ok", [], [(target, {"to": "x"})])
    assert worker.dispatch_once(st, rng())
    assert not worker.dispatch_once(st, rng())
    assert SqliteReceiver(tmp_path / "r.sqlite").stats() == {"effects": 1, "deliveries": 1}
    assert worker.outbox_pending(st) == 0


def test_receiver_dedupes_redelivery(tmp_path):
    """Broken case: the dispatcher crashes after the send and before the mark, then resends."""
    r = SqliteReceiver(tmp_path / "r.sqlite")
    assert r.deliver("1:send:0", {"x": 1})
    assert not r.deliver("1:send:0", {"x": 1})
    assert r.stats() == {"effects": 1, "deliveries": 2}


def test_dispatch_failure_backs_off(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "send", "ok", [], [("http://127.0.0.1:9/hook", {})])
    assert worker.dispatch_once(st, rng())
    row = st.conn.execute("SELECT * FROM outbox").fetchone()
    assert row["state"] == "pending" and row["attempts"] == 1 and "send failed" in row["last_error"]
    assert row["lease_until"] > st.clock.now()


def test_send_rejects_unknown_scheme():
    with pytest.raises(PermanentError):
        send("file:///etc/passwd", "k", {})


def test_permanent_send_error_goes_dead_and_work_exits(tmp_path):
    """Broken case: a bad target retries for ever and `work --until-empty` never returns."""
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "send", "ok", [], [("ftp://nope", {})])
    st.finish(jid, jb.lease_token, "succeed", result="ok")
    worker.work(st, {}, until_empty=True, max_loops=50)
    assert worker.outbox_pending(st) == 0
    dead = worker.outbox_dead(st)
    assert len(dead) == 1 and "unsupported" in dead[0]["last_error"]
    assert st.requeue_outbox() == 1 and worker.outbox_pending(st) == 1


def test_outbox_gives_up_after_max_attempts(tmp_path):
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "send", "ok", [], [("http://127.0.0.1:9/hook", {})])
    for _ in range(worker.OUTBOX_MAX_ATTEMPTS):
        st.clock.advance(60)
        worker.dispatch_once(st, rng())
    assert worker.outbox_pending(st) == 0 and len(worker.outbox_dead(st)) == 1


def test_send_email_example_end_to_end(tmp_path):
    from helpers import limits

    from agentqueue.fakeprovider import FakeProvider

    with FakeProvider() as fp:
        st = make_store(tmp_path)
        jid, _ = st.submit(
            "send_email", {"topic": "launch", "to": "a@b.c", "target": fp.url + "/receive"}, "k"
        )
        worker.work(st, limits(fp.url), until_empty=True, max_loops=200)
        assert st.job(jid)["state"] == "succeeded"
        snap = fp.snapshot()
        assert snap["effects"] == 1 and snap["deliveries"] == 1

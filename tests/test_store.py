import threading

from helpers import make_store

from agentqueue.store import Store, connect


def test_wal_mode(tmp_path):
    c = connect(tmp_path / "x.sqlite")
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_schema_is_idempotent(tmp_path):
    a = make_store(tmp_path)
    a.submit("k", {}, "x")
    b = Store(a.path, a.clock)
    assert b.counts() == {"queued": 1}


def test_two_threads_never_claim_the_same_job(tmp_path):
    """Broken case: a SELECT then UPDATE without BEGIN IMMEDIATE lets two workers take one job."""
    st = make_store(tmp_path)
    for i in range(50):
        st.submit("k", {}, f"j{i}")
    got = []
    lock = threading.Lock()

    def run():
        s = Store(st.path, st.clock)
        while (j := s.claim()) is not None:
            with lock:
                got.append(j.id)

    ts = [threading.Thread(target=run) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    assert sorted(got) == list(range(1, 51))


def test_rollback_on_error(tmp_path):
    st = make_store(tmp_path)
    try:
        with st.tx() as c:
            c.execute("INSERT INTO steps VALUES (1, 'a', '1', '[]', 0)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert st.conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 0

import pytest
from helpers import make_store

from agentqueue.store import IdempotencyConflict


def test_same_key_makes_one_job(tmp_path):
    """Broken case: a retried submit after a network blip enqueues the job twice."""
    st = make_store(tmp_path)
    a = st.submit("summarize", {"docs": ["x"]}, "order-42")
    b = st.submit("summarize", {"docs": ["x"]}, "order-42")
    assert a == (a[0], True) and b == (a[0], False)
    assert st.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_same_key_from_two_processes_makes_one_job(tmp_path):
    import subprocess
    import sys

    code = (
        "import sys; from agentqueue.store import Store; "
        "s = Store(sys.argv[1]); print(s.submit('k', {'a': 1}, 'same')[0])"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path / "q.sqlite")], stdout=subprocess.PIPE
        )
        for _ in range(6)
    ]
    ids = {p.communicate(timeout=60)[0].strip() for p in procs}
    assert len(ids) == 1
    st = make_store(tmp_path)
    assert st.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_same_key_other_payload_is_an_error(tmp_path):
    st = make_store(tmp_path)
    st.submit("summarize", {"docs": ["x"]}, "k")
    with pytest.raises(IdempotencyConflict):
        st.submit("summarize", {"docs": ["y"]}, "k")


def test_payload_key_order_does_not_matter(tmp_path):
    st = make_store(tmp_path)
    a, _ = st.submit("k", {"a": 1, "b": 2}, "x")
    b, created = st.submit("k", {"b": 2, "a": 1}, "x")
    assert a == b and not created

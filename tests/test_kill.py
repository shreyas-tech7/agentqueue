"""Done means 2: SIGKILL the worker at random points in at least 200 runs."""

import sqlite3

from helpers import make_store, rng

from agentqueue import chaos, worker
from agentqueue.receiver import SqliteReceiver
from agentqueue.worker import Context, Crasher


def test_200_kill_runs_keep_every_invariant():
    res = chaos.kill_suite(200, base_seed=0)
    assert res["runs"] == 200
    assert res["runs_with_a_kill"] == 200, "every run kills at least one worker"
    assert res["bad_end_states"] == 0, "every job ends as succeeded or dead"
    assert res["hung_workers"] == 0, "no worker ran past its timeout"
    assert res["finished_step_reruns"] == 0, "no finished step runs twice"
    assert res["missing_effects"] == 0, "every succeeded job's side effect reached the receiver"
    assert res["succeeded_jobs"] + res["dead_jobs"] == 600


def _setup(tmp_path):
    exec_log = tmp_path / "exec.sqlite"
    e = sqlite3.connect(exec_log)
    e.execute("CREATE TABLE execs (job_id INTEGER, step TEXT, already_done INTEGER)")
    e.commit()
    e.close()
    SqliteReceiver(tmp_path / "r.sqlite")
    payload = {
        "n": 0,
        "steps": 2,
        "exec_log": str(exec_log),
        "target": f"sqlite:{tmp_path}/r.sqlite",
    }
    return exec_log, payload


def test_the_rerun_detector_catches_a_broken_replay(tmp_path):
    """Broken case: replay ignores saved results. The exec log must flag the rerun."""
    exec_log, payload = _setup(tmp_path)
    st = make_store(tmp_path)
    jid, _ = st.submit("killtest", payload, "k")
    jb = st.claim()
    ctx = Context(st, jb, {}, Crasher(None), 30)
    ctx.step("s0", lambda: 1)  # s0 is now committed
    broken = Context(st, jb, {}, Crasher(None), 30)
    broken.done = {}  # the bug: forget what was saved
    try:
        chaos.killtest(broken, payload)
    except sqlite3.IntegrityError:
        pass  # the primary key also refuses the second commit
    flagged = sqlite3.connect(exec_log).execute("SELECT SUM(already_done) FROM execs").fetchone()[0]
    assert flagged == 1


def test_a_healthy_replay_flags_nothing(tmp_path):
    exec_log, payload = _setup(tmp_path)
    st = make_store(tmp_path)
    jid, _ = st.submit("killtest", payload, "k")
    worker.run_once(st, {}, rng())
    assert st.job(jid)["state"] == "succeeded"
    flagged = sqlite3.connect(exec_log).execute("SELECT SUM(already_done) FROM execs").fetchone()[0]
    assert flagged == 0


def test_crasher_kills_at_the_chosen_hit(tmp_path):
    import subprocess
    import sys

    code = "from agentqueue.worker import Crasher\nc = Crasher(3)\nfor i in range(5):\n    c.hit('x')\n    print(i, flush=True)\n"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert p.returncode == -9 and p.stdout.split() == ["0", "1"]

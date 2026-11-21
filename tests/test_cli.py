import json

from helpers import make_store

from agentqueue import cli
from agentqueue.fakeprovider import FakeProvider


def run(capsys, *args):
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def test_submit_twice_same_key(tmp_path, capsys):
    db = str(tmp_path / "q.sqlite")
    _, a = run(
        capsys,
        "submit",
        "--db",
        db,
        "--kind",
        "classify",
        "--key",
        "t1",
        "--payload",
        '{"ticket": "x"}',
    )
    _, b = run(
        capsys,
        "submit",
        "--db",
        db,
        "--kind",
        "classify",
        "--key",
        "t1",
        "--payload",
        '{"ticket": "x"}',
    )
    assert json.loads(a) == {"job_id": 1, "created": True}
    assert json.loads(b) == {"job_id": 1, "created": False}
    code, _ = run(
        capsys,
        "submit",
        "--db",
        db,
        "--kind",
        "classify",
        "--key",
        "t1",
        "--payload",
        '{"ticket": "y"}',
    )
    assert code == 2


def test_work_status_requeue(tmp_path, capsys):
    db = str(tmp_path / "q.sqlite")
    with FakeProvider() as fp:
        lim = tmp_path / "limits.toml"
        lim.write_text(
            f'[providers.fake]\nurl = "{fp.url}"\nrpm = 600\nrpd = 10000\ntpm = 100000\n'
        )
        run(
            capsys,
            "submit",
            "--db",
            db,
            "--kind",
            "classify",
            "--key",
            "a",
            "--payload",
            '{"ticket": "x"}',
        )
        run(capsys, "submit", "--db", db, "--kind", "nope", "--key", "b")
        code, out = run(
            capsys, "work", "--db", db, "--limits", str(lim), "--until-empty", "--poll", "0.01"
        )
        assert code == 0
        code, out = run(capsys, "status", "--db", db, "--limits", str(lim))
        assert "succeeded  1" in out and "failed     1" in out
        assert "dead letters: 1" in out and "no job function" in out
        assert "of 600 rpm" in out and "breaker closed" in out
        code, out = run(capsys, "requeue", "--db", db, "--all")
        assert code == 0 and "requeued 1" in out
        assert make_store(tmp_path).counts()["queued"] == 1


def test_dispatch_command(tmp_path, capsys):
    db = tmp_path / "q.sqlite"
    st = make_store(tmp_path)
    jid, _ = st.submit("k", {}, "k")
    jb = st.claim()
    st.commit_step(jid, jb.lease_token, "s", 1, [], [(f"sqlite:{tmp_path}/r.sqlite", {"a": 1})])
    code, out = run(capsys, "dispatch", "--db", str(db))
    assert "dispatched 1" in out and "0 pending" in out


def test_help_lists_every_command(capsys):
    p = cli.build_parser()
    text = p.format_help()
    for c in ("submit", "work", "dispatch", "status", "requeue", "bench", "killtest", "readme"):
        assert c in text

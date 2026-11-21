"""The command line: submit, work, dispatch, status, requeue, bench, killtest, readme."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from agentqueue import ratelimit, worker
from agentqueue.store import IdempotencyConflict, Store

DEFAULT_DB = "agentqueue.sqlite"


def _limits(path: str | None):
    return ratelimit.load_limits(path) if path else {}


def _load_jobs(mods: str) -> None:
    for m in filter(None, mods.split(",")):
        importlib.import_module(m.strip())


def cmd_submit(a) -> int:
    st = Store(a.db)
    try:
        jid, created = st.submit(a.kind, json.loads(a.payload), a.key, a.max_attempts)
    except IdempotencyConflict as e:
        print(f"conflict: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"job_id": jid, "created": created}))
    return 0


def cmd_work(a) -> int:
    _load_jobs(a.jobs)
    st = Store(a.db)
    n = worker.work(
        st,
        _limits(a.limits),
        dispatch=not a.no_dispatch,
        until_empty=a.until_empty,
        poll_s=a.poll,
        seed=a.seed,
        lease_s=a.lease,
    )
    print(f"ran {n} job attempts")
    return 0


def cmd_dispatch(a) -> int:
    import random

    st = Store(a.db)
    rng = random.Random(a.seed)
    n = 0
    while worker.dispatch_once(st, rng):
        n += 1
    print(f"dispatched {n} outbox attempts, {worker.outbox_pending(st)} pending")
    return 0


def status_text(st: Store, limits: dict) -> str:
    now = st.clock.now()
    counts = st.counts()
    lines = ["queue:"]
    for s in ("queued", "waiting", "running", "succeeded", "failed", "dead"):
        lines.append(f"  {s:<10} {counts.get(s, 0)}")
    lines.append(f"  outbox pending {worker.outbox_pending(st)}")
    lines.append("rates (last 60 s, last 24 h):")
    provs = {r[0] for r in st.conn.execute("SELECT DISTINCT provider FROM calls")} | set(limits)
    for p in sorted(provs):
        m = st.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens), 0) FROM calls WHERE provider = ? AND reserved_at > ?",
            (p, now - 60),
        ).fetchone()
        d = st.conn.execute(
            "SELECT COUNT(*) FROM calls WHERE provider = ? AND reserved_at > ?", (p, now - 86400)
        ).fetchone()[0]
        lim = limits.get(p)
        cap = f" of {lim.rpm} rpm, {lim.tpm} tpm, {lim.rpd} rpd" if lim else ""
        br = st.conn.execute(
            "SELECT breaker, paused_until FROM providers WHERE provider = ?", (p,)
        ).fetchone()
        extra = ""
        if br:
            extra = f", breaker {br['breaker']}"
            if br["paused_until"] > now:
                extra += f", paused {br['paused_until'] - now:.1f} s"
        lines.append(f"  {p}: {m[0]} req/min, {m[1]} tok/min, {d} req/day{cap}{extra}")
    dl = st.dead_letters()
    lines.append(f"dead letters: {len(dl)}")
    for r in dl:
        lines.append(
            f"  job {r['job_id']} [{r['state']}] {r['kind']} key={r['idem_key']}: {r['error']}"
        )
    return "\n".join(lines)


def cmd_status(a) -> int:
    print(status_text(Store(a.db), _limits(a.limits)))
    return 0


def cmd_requeue(a) -> int:
    st = Store(a.db)
    ids = [r["job_id"] for r in st.dead_letters()] if a.all else a.job_ids
    done = [j for j in ids if st.requeue(j)]
    print(f"requeued {len(done)} jobs: {done}")
    return 0 if len(done) == len(ids) else 1


def _seeds(text: str) -> list[int]:
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in text.split(",")]


def cmd_bench(a) -> int:
    from agentqueue import chaos

    def progress(r):
        print(
            f"{r['level']:<7}{' naive' if r['naive'] else ''} seed {r['seed']}: "
            f"{r['succeeded']}/{r['jobs']} done, {r['kills']} kills, "
            f"{r['wasted_calls']} wasted, {r['duplicate_effects']} dup effects, "
            f"{len(r['limit_violations'])} limit violations, {r['wall_s']:.1f} s",
            flush=True,
        )

    res = chaos.bench_suite(
        a.levels.split(","),
        _seeds(a.seeds),
        n_jobs=a.jobs,
        naive_levels=[x for x in a.naive.split(",") if x],
        progress=progress,
    )
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2) + "\n")
    print(f"wrote {a.out}")
    return 0


def cmd_killtest(a) -> int:
    from agentqueue import chaos

    res = chaos.kill_suite(a.runs, a.seed)
    res["meta"] = chaos.machine()
    keep = {k: v for k, v in res.items() if k != "per_run"}
    print(json.dumps(keep, indent=2))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(keep, indent=2) + "\n")
    ok = res["bad_end_states"] == 0 and res["finished_step_reruns"] == 0
    return 0 if ok and res["missing_effects"] == 0 else 1


def cmd_readme(a) -> int:
    from agentqueue import report

    return report.main(check=a.check)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentqueue", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        if name not in ("bench", "killtest", "readme"):
            sp.add_argument("--db", default=DEFAULT_DB)
        return sp

    s = add("submit", cmd_submit, "submit a job with an idempotency key")
    s.add_argument("--kind", required=True)
    s.add_argument("--key", required=True, help="idempotency key")
    s.add_argument("--payload", default="{}", help="JSON")
    s.add_argument("--max-attempts", type=int, default=8)

    w = add("work", cmd_work, "run the worker loop (and the dispatcher)")
    w.add_argument("--limits", help="TOML file with provider limits")
    w.add_argument("--jobs", default="agentqueue.examples", help="modules that register jobs")
    w.add_argument("--until-empty", action="store_true")
    w.add_argument("--no-dispatch", action="store_true")
    w.add_argument("--poll", type=float, default=0.05)
    w.add_argument("--lease", type=float, default=30.0)
    w.add_argument("--seed", type=int)

    d = add("dispatch", cmd_dispatch, "send every due outbox entry once")
    d.add_argument("--seed", type=int, default=0)

    s = add("status", cmd_status, "queue depth, current rates, dead letters")
    s.add_argument("--limits")

    r = add("requeue", cmd_requeue, "move dead or failed jobs back to the queue")
    r.add_argument("job_ids", nargs="*", type=int)
    r.add_argument("--all", action="store_true")

    b = add("bench", cmd_bench, "the chaos benchmark")
    b.add_argument("--levels", default="low,medium,high")
    b.add_argument("--seeds", default="1-5")
    b.add_argument("--jobs", type=int, default=60)
    b.add_argument(
        "--naive", default="medium,high", help="levels to also run the no-outbox baseline"
    )
    b.add_argument("--out", default="results/bench.json")

    k = add("killtest", cmd_killtest, "SIGKILL workers at random points and check the invariants")
    k.add_argument("--runs", type=int, default=200)
    k.add_argument("--seed", type=int, default=0)
    k.add_argument("--out")

    m = add("readme", cmd_readme, "write README.md and docs/index.html from results/")
    m.add_argument("--check", action="store_true", help="fail if the files are out of date")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)

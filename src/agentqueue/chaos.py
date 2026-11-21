"""Kill tests and the chaos benchmark.

Both run real worker processes and SIGKILL them. The kill test uses only local SQLite files.
The benchmark adds the fake provider with injected faults.
"""

from __future__ import annotations

import json
import os
import platform
import random
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from agentqueue.fakeprovider import FakeProvider, Faults
from agentqueue.ratelimit import audit_calls, parse_limits
from agentqueue.receiver import SqliteReceiver
from agentqueue.store import Store
from agentqueue.worker import job

# ---- the instrumented job for kill tests ------------------------------------------------


@job("killtest")
def killtest(ctx, payload):
    """Each step logs that it ran and whether its own result was already committed.

    A step whose result is committed must never run again. If it does, the log shows it.
    """
    out = []
    for i in range(payload["steps"]):
        name = f"s{i}"

        def body(name=name, i=i):
            c = sqlite3.connect(ctx.store.path, timeout=30)
            done = c.execute(
                "SELECT COUNT(*) FROM steps WHERE job_id = ? AND name = ?", (ctx.job.id, name)
            ).fetchone()[0]
            c.close()
            e = sqlite3.connect(payload["exec_log"], timeout=30, isolation_level=None)
            e.execute("INSERT INTO execs VALUES (?, ?, ?)", (ctx.job.id, name, done))
            e.close()
            return i * 10 + payload["n"]

        out.append(ctx.step(name, body))

    def notify():
        ctx.emit(payload["target"], {"job": payload["n"], "sum": sum(out)})
        return "queued"

    ctx.step("notify", notify)
    return sum(out)


def _worker_cmd(db, extra=()) -> list[str]:
    return [
        sys.executable,
        "-m",
        "agentqueue",
        "work",
        "--db",
        str(db),
        "--jobs",
        "agentqueue.chaos,agentqueue.examples",
        "--until-empty",
        "--poll",
        "0.005",
        *extra,
    ]


def _env(crash_at: int | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "AGENTQUEUE_CRASH_AT"}
    if crash_at is not None:
        env["AGENTQUEUE_CRASH_AT"] = str(crash_at)
    return env


def kill_run(seed: int, workdir: str | Path, jobs: int = 3, steps: int = 4) -> dict:
    """One kill-test run. Workers die by SIGKILL at points picked by the seed."""
    workdir = Path(workdir)
    rng = random.Random(seed)
    db = workdir / "q.sqlite"
    exec_log = workdir / "exec.sqlite"
    rec = workdir / "r.sqlite"
    e = sqlite3.connect(exec_log)
    e.execute("CREATE TABLE IF NOT EXISTS execs (job_id INTEGER, step TEXT, already_done INTEGER)")
    e.commit()
    e.close()
    SqliteReceiver(rec)
    st = Store(db)
    for n in range(jobs):
        st.submit(
            "killtest",
            {"n": n, "steps": steps, "exec_log": str(exec_log), "target": f"sqlite:{rec}"},
            f"kill-{seed}-{n}",
            max_attempts=12,
        )
    kills = launches = hangs = 0
    for launch in range(12):
        if st.all_terminal() and _pending(st) == 0:
            break
        launches += 1
        last = launch == 11
        mode = "clean" if last else rng.choice(["self", "self", "self", "external"])
        crash_at = rng.randint(1, 3 * jobs * (steps + 2)) if mode == "self" else None
        p = subprocess.Popen(
            _worker_cmd(db, ["--lease", "2", "--seed", str(seed)]),
            env=_env(crash_at),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if mode == "external":
            time.sleep(rng.uniform(0.0, 0.4))
            if p.poll() is None:
                p.send_signal(signal.SIGKILL)
        try:
            _, err = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            # A worker that never exits is a bug (for example an outbox entry that retries for
            # ever). Count it apart, so the kill below is not mistaken for a planned kill.
            p.kill()
            _, err = p.communicate(timeout=10)
            hangs += 1
            continue
        if p.returncode == -signal.SIGKILL:
            kills += 1
        elif p.returncode != 0:
            raise RuntimeError(f"worker exited {p.returncode}: {err.decode()[-2000:]}")
    states = st.counts()
    e = sqlite3.connect(exec_log)
    execs = e.execute("SELECT COUNT(*), COALESCE(SUM(already_done), 0) FROM execs").fetchone()
    e.close()
    committed = st.conn.execute("SELECT COUNT(*) FROM steps WHERE name LIKE 's%'").fetchone()[0]
    want_keys = {
        r[0]
        for r in st.conn.execute(
            "SELECT o.dedupe_key FROM outbox o JOIN jobs j ON j.id = o.job_id"
            " WHERE j.state = 'succeeded'"
        )
    }
    r = sqlite3.connect(rec)
    got = dict(r.execute("SELECT dedupe_key, deliveries FROM receipts").fetchall())
    r.close()
    st.close()
    return {
        "seed": seed,
        "launches": launches,
        "kills": kills,
        "hangs": hangs,
        "states": states,
        "ended_ok": set(states) <= {"succeeded", "dead"},
        "step_runs": execs[0],
        "finished_step_reruns": execs[1],
        "steps_committed": committed,
        "missing_effects": len(want_keys - set(got)),
        "effects": len(got),
        "redeliveries": sum(v - 1 for v in got.values()),
    }


def _pending(st) -> int:
    return st.conn.execute("SELECT COUNT(*) FROM outbox WHERE state = 'pending'").fetchone()[0]


def kill_suite(runs: int, base_seed: int = 0, threads: int = 4) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    def one(seed):
        with tempfile.TemporaryDirectory(prefix="aq-kill-") as d:
            return kill_run(seed, d)

    with ThreadPoolExecutor(threads) as ex:
        results = list(ex.map(one, range(base_seed, base_seed + runs)))
    return {
        "runs": runs,
        "base_seed": base_seed,
        "kills": sum(r["kills"] for r in results),
        "hung_workers": sum(r["hangs"] for r in results),
        "runs_with_a_kill": sum(r["kills"] > 0 for r in results),
        "bad_end_states": sum(not r["ended_ok"] for r in results),
        "finished_step_reruns": sum(r["finished_step_reruns"] for r in results),
        "unfinished_step_reruns": sum(r["step_runs"] - r["steps_committed"] for r in results),
        "missing_effects": sum(r["missing_effects"] for r in results),
        "redeliveries": sum(r["redeliveries"] for r in results),
        "dead_jobs": sum(r["states"].get("dead", 0) for r in results),
        "succeeded_jobs": sum(r["states"].get("succeeded", 0) for r in results),
        "per_run": results,
    }


# ---- the chaos benchmark -----------------------------------------------------------------

LEVELS = {
    "low": dict(
        faults=Faults(
            rate_429=0.02, rate_500=0.02, rate_timeout=0.01, rate_slow=0.02, rate_drop=0.01
        ),
        kill_every_s=4.0,
        crash_span=3000,
    ),
    "medium": dict(
        faults=Faults(
            rate_429=0.05, rate_500=0.05, rate_timeout=0.02, rate_slow=0.05, rate_drop=0.02
        ),
        kill_every_s=2.0,
        crash_span=1000,
    ),
    "high": dict(
        faults=Faults(
            rate_429=0.10, rate_500=0.10, rate_timeout=0.05, rate_slow=0.10, rate_drop=0.05
        ),
        kill_every_s=1.0,
        crash_span=400,
    ),
}
for _lv in LEVELS.values():
    _lv["faults"].timeout_s = 0.6
    _lv["faults"].retry_after_s = 0.2

BENCH_LIMITS = {
    "rpm": 900,
    "rpd": 1_000_000,
    "tpm": 60_000,
    "burst_requests": 15,
    "burst_tokens": 1500,
    "breaker_failures": 5,
    "breaker_cooldown_s": 1.0,
    "timeout_s": 0.4,
}


def _limits_toml(url: str) -> str:
    lines = ["[providers.fake]", f'url = "{url}"']
    lines += [f"{k} = {v}" for k, v in BENCH_LIMITS.items()]
    return "\n".join(lines) + "\n"


def bench_run(
    level: str,
    seed: int,
    workdir: str | Path,
    n_jobs: int = 60,
    model_steps: int = 3,
    workers: int = 4,
    naive: bool = False,
    deadline_s: float = 240.0,
) -> dict:
    workdir = Path(workdir)
    cfg = LEVELS[level]
    rng = random.Random(seed * 1000 + 7)
    with FakeProvider(cfg["faults"], seed=seed) as fp:
        (workdir / "limits.toml").write_text(_limits_toml(fp.url))
        db = workdir / "q.sqlite"
        st = Store(db)
        kind = "bench_naive" if naive else "bench"
        for n in range(n_jobs):
            st.submit(
                kind,
                {"n": n, "model_steps": model_steps, "target": fp.url + "/receive"},
                f"bench-{level}-{seed}-{n}",
                max_attempts=10,
            )
        cmd = _worker_cmd(db, ["--limits", str(workdir / "limits.toml")])
        log = open(workdir / "workers.log", "ab")  # noqa: SIM115

        # Two kinds of kill. The parent SIGKILLs a random worker at random times. And each
        # worker gets a crash point picked by the seed (AGENTQUEUE_CRASH_AT), so kills also
        # land in the narrow gaps between a call, a send and a commit.
        def crash_at():
            return rng.randint(1, cfg["crash_span"])

        procs = [_start(cmd, i, seed, log, crash_at()) for i in range(workers)]
        t0 = time.time()
        next_kill = t0 + rng.expovariate(1 / cfg["kill_every_s"])
        kills = self_kills = starts = 0
        try:
            while time.time() - t0 < deadline_s:
                if st.all_terminal() and _pending(st) == 0:
                    break
                if time.time() >= next_kill:
                    i = rng.randrange(workers)
                    if procs[i].poll() is None:
                        procs[i].send_signal(signal.SIGKILL)
                        procs[i].wait(timeout=10)
                        procs[i].killed_by_parent = True
                        kills += 1
                    next_kill = time.time() + rng.expovariate(1 / cfg["kill_every_s"])
                for i, p in enumerate(procs):
                    if p.poll() is not None and not (st.all_terminal() and _pending(st) == 0):
                        if p.returncode == -signal.SIGKILL and not getattr(
                            p, "killed_by_parent", False
                        ):
                            self_kills += 1
                        starts += 1
                        procs[i] = _start(cmd, i, seed * 1000 + starts, log, crash_at())
                time.sleep(0.02)
            wall = time.time() - t0
        finally:
            for p in procs:
                if p.poll() is None:
                    p.send_signal(signal.SIGKILL)
                p.wait(timeout=10)
            log.close()
        snap = fp.snapshot()
    lim = parse_limits({"providers": {"fake": BENCH_LIMITS}})["fake"]
    calls = [tuple(r) for r in st.conn.execute("SELECT reserved_at, tokens FROM calls")]
    used = sum(len(json.loads(r[0])) for r in st.conn.execute("SELECT call_ids FROM steps"))
    rows = st.conn.execute("SELECT state, created_at, finished_at FROM jobs").fetchall()
    lat = sorted(r["finished_at"] - r["created_at"] for r in rows if r["state"] == "succeeded")
    succeeded = sum(r["state"] == "succeeded" for r in rows)
    want_keys = {
        r[0]
        for r in st.conn.execute(
            "SELECT o.dedupe_key FROM outbox o JOIN jobs j ON j.id = o.job_id"
            " WHERE j.state = 'succeeded'"
        )
    }
    span = (max(c[0] for c in calls) - min(c[0] for c in calls)) if len(calls) > 1 else 0.0
    st.close()
    return {
        "level": level,
        "seed": seed,
        "naive": naive,
        "jobs": n_jobs,
        "succeeded": succeeded,
        "dead": sum(r["state"] == "dead" for r in rows),
        "not_finished": sum(r["state"] not in ("succeeded", "dead", "failed") for r in rows),
        "completion_rate": succeeded / n_jobs,
        "kills": kills + self_kills,
        "parent_kills": kills,
        "crash_point_kills": self_kills,
        "requests_sent": len(calls),
        "provider_requests": snap["requests"],
        "provider_completions": snap["completions"],
        "wasted_calls": snap["completions"] - used,
        "duplicate_effects": snap["naive_duplicates"] if naive else snap["duplicate_effects"],
        "missing_effects": 0 if naive else len(want_keys - set(fp.stats.effects)),
        "redeliveries": snap["redeliveries"],
        "effects_applied": snap["naive_effects"] if naive else snap["effects"],
        "limit_violations": audit_calls(calls, lim),
        "p50_latency_s": _pct(lat, 50),
        "p95_latency_s": _pct(lat, 95),
        "wall_s": wall,
        "requests_per_min": 60 * len(calls) / span if span else 0.0,
        "rpm_limit": lim.rpm,
        "faults": snap["faults"],
    }


def _start(cmd, i, seed, log, crash_at=None):
    return subprocess.Popen(
        [*cmd, "--seed", str(seed * 10 + i)],
        stdout=subprocess.DEVNULL,
        stderr=log,
        env=_env(crash_at),
    )


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    k = (len(xs) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


T95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def interval(xs: list[float]) -> dict:
    """Mean and a 95% t interval over seeds. With one value there is no interval."""
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return {"mean": m, "lo": None, "hi": None, "n": len(xs)}
    half = T95.get(len(xs), 1.96) * statistics.stdev(xs) / len(xs) ** 0.5
    return {"mean": m, "lo": m - half, "hi": m + half, "n": len(xs)}


METRICS = (
    "completion_rate",
    "wasted_calls",
    "duplicate_effects",
    "missing_effects",
    "redeliveries",
    "p50_latency_s",
    "p95_latency_s",
    "requests_per_min",
    "kills",
)


def summarize(runs: list[dict]) -> dict:
    out = {}
    for key in sorted({(r["level"], r["naive"]) for r in runs}):
        group = [r for r in runs if (r["level"], r["naive"]) == key]
        name = f"{key[0]}{'-naive' if key[1] else ''}"
        out[name] = {
            "level": key[0],
            "naive": key[1],
            "seeds": [r["seed"] for r in group],
            "limit_violations": sum(len(r["limit_violations"]) for r in group),
            "not_finished": sum(r["not_finished"] for r in group),
            **{m: interval([r[m] for r in group if r[m] is not None]) for m in METRICS},
        }
    return out


def machine() -> dict:
    cpu = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "cpu": cpu,
        "cores": os.cpu_count(),
        "python": platform.python_version(),
        "date": date.today().isoformat(),
    }


def bench_suite(levels, seeds, n_jobs=60, naive_levels=(), progress=None) -> dict:
    runs = []
    plan = [(lv, s, False) for lv in levels for s in seeds]
    plan += [(lv, s, True) for lv in naive_levels for s in seeds]
    for lv, s, naive in plan:
        with tempfile.TemporaryDirectory(prefix="aq-bench-") as d:
            r = bench_run(lv, s, d, n_jobs=n_jobs, naive=naive)
        runs.append(r)
        if progress:
            progress(r)
    return {
        "meta": {
            **machine(),
            "model": "none: the local fake provider, no real model",
            "jobs_per_run": n_jobs,
            "model_steps_per_job": 3,
            "workers": 4,
            "limits": BENCH_LIMITS,
            "levels": {
                k: {
                    "kill_every_s": v["kill_every_s"],
                    "crash_span": v["crash_span"],
                    **vars(v["faults"]),
                }
                for k, v in LEVELS.items()
            },
            "seeds": list(seeds),
        },
        "summary": summarize(runs),
        "runs": runs,
    }

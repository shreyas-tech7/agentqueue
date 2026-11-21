"""Write README.md and docs/index.html from the result files. Nobody types the numbers.

`agentqueue readme` rewrites both files. `agentqueue readme --check` fails if either one
differs from what the result files say, and CI runs the check.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docs" / "templates" / "README.md.tmpl"
BENCH = ROOT / "results" / "bench.json"
KILL = ROOT / "results" / "killtest.json"
LEVEL_ORDER = ("low", "medium", "high")


def _ci(m: dict, fmt: str = "{:.2f}", scale: float = 1.0) -> str:
    mean = fmt.format(m["mean"] * scale)
    if m["lo"] is None:
        return mean
    lo, hi = fmt.format(m["lo"] * scale), fmt.format(m["hi"] * scale)
    if lo == hi == mean:
        return f"{mean} (same on every seed)"
    return f"{mean} [{lo}, {hi}]"


def results_rows(bench: dict) -> list[list[str]]:
    rows = []
    for lv in LEVEL_ORDER:
        s = bench["summary"].get(lv)
        if not s:
            continue
        rows.append(
            [
                lv,
                _ci(s["completion_rate"], "{:.1f}%", 100),
                _ci(s["wasted_calls"], "{:.1f}"),
                _ci(s["duplicate_effects"], "{:.0f}"),
                _ci(s["p50_latency_s"], "{:.1f} s"),
                _ci(s["p95_latency_s"], "{:.1f} s"),
                _ci(s["requests_per_min"], "{:.0f}"),
                str(s["limit_violations"]),
                _ci(s["kills"], "{:.1f}"),
            ]
        )
    return rows


HEADERS = [
    "Fault level",
    "Completion rate",
    "Wasted calls per run",
    "Duplicate effects",
    "p50 job latency",
    "p95 job latency",
    "Requests per min",
    "Limit breaks",
    "Worker kills per run",
]


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def naive_rows(bench: dict) -> list[list[str]]:
    rows = []
    for lv in LEVEL_ORDER:
        a, b = bench["summary"].get(lv), bench["summary"].get(f"{lv}-naive")
        if a and b:
            rows.append(
                [
                    lv,
                    "outbox, dedupe key",
                    _ci(a["duplicate_effects"], "{:.0f}"),
                    _ci(a["redeliveries"], "{:.1f}"),
                ]
            )
            rows.append(
                [
                    lv,
                    "inline send, no key",
                    _ci(b["duplicate_effects"], "{:.1f}"),
                    "not tracked",
                ]
            )
    return rows


def fault_rows(bench: dict) -> list[list[str]]:
    rows = []
    for lv in LEVEL_ORDER:
        f = bench["meta"]["levels"][lv]
        rows.append(
            [
                lv,
                f"{f['rate_429']:.0%}",
                f"{f['rate_500']:.0%}",
                f"{f['rate_timeout']:.0%}",
                f"{f['rate_slow']:.0%}",
                f"{f['rate_drop']:.0%}",
                f"parent kill every {f['kill_every_s']:g} s on average, crash point 1 to {f['crash_span']}",
            ]
        )
    return rows


def cost(bench: dict) -> str:
    lines = []
    for lv in LEVEL_ORDER:
        runs = [r for r in bench["runs"] if r["level"] == lv and not r["naive"]]
        if not runs:
            continue
        req = sum(r["requests_sent"] for r in runs) / sum(r["succeeded"] for r in runs)
        waste = sum(r["wasted_calls"] for r in runs) / sum(r["succeeded"] for r in runs)
        steps = bench["meta"]["model_steps_per_job"]
        lines.append(f"| {lv} | {steps} | {req:.2f} | {waste:.2f} |")
    head = (
        "| Fault level | Model calls a job needs | Requests sent per finished job |"
        " Wasted calls per finished job |\n| --- | --- | --- | --- |"
    )
    return head + "\n" + "\n".join(lines)


def kill_line(kill: dict | None) -> str:
    if not kill:
        return "The kill test has not been run yet."
    return (
        f"`agentqueue killtest` ran {kill['runs']} runs on {kill['meta']['date']}"
        f" (seeds {kill['base_seed']} to {kill['base_seed'] + kill['runs'] - 1})."
        f" {kill['runs_with_a_kill']} runs lost a worker to SIGKILL, {kill['kills']} kills in all."
        f" {kill['succeeded_jobs']} jobs succeeded and {kill['dead_jobs']} went dead."
        f" Jobs left in another state: {kill['bad_end_states']}."
        f" Finished steps that ran again: {kill['finished_step_reruns']}."
        f" Unfinished steps that ran again after a kill: {kill['unfinished_step_reruns']}."
        f" Side effects missing at the receiver: {kill['missing_effects']}."
        f" Redeliveries the receiver caught by dedupe key: {kill['redeliveries']}."
    )


def meta_line(bench: dict) -> str:
    m = bench["meta"]
    seeds = ", ".join(str(s) for s in m["seeds"])
    return (
        f"Run on {m['date']} on {m['cpu']}, {m['cores']} cores, Python {m['python']}."
        f" Model: {m['model']}. Seeds: {seeds}. Each run: {m['jobs_per_run']} jobs,"
        f" {m['model_steps_per_job']} model steps and 1 side effect per job,"
        f" {m['workers']} worker processes, limits {m['limits']['rpm']} requests per minute"
        f" and {m['limits']['tpm']} tokens per minute."
    )


def render_readme() -> str:
    bench = json.loads(BENCH.read_text())
    kill = json.loads(KILL.read_text()) if KILL.exists() else None
    t = TEMPLATE.read_text()
    fills = {
        "{{META}}": meta_line(bench),
        "{{RESULTS_TABLE}}": md_table(HEADERS, results_rows(bench)),
        "{{FAULT_TABLE}}": md_table(
            ["Level", "429", "500", "Timeout", "Slow", "Dropped", "Worker SIGKILL"],
            fault_rows(bench),
        ),
        "{{NAIVE_TABLE}}": md_table(
            [
                "Fault level",
                "Side effect path",
                "Duplicate effects per run",
                "Redeliveries per run",
            ],
            naive_rows(bench),
        ),
        "{{COST_TABLE}}": cost(bench),
        "{{KILL_LINE}}": kill_line(kill),
    }
    for k, v in fills.items():
        t = t.replace(k, v)
    return t


def render_index() -> str:
    bench = json.loads(BENCH.read_text())
    kill = json.loads(KILL.read_text()) if KILL.exists() else None

    def table(headers, rows):
        h = "".join(f"<th>{html.escape(x)}</th>" for x in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows
        )
        return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agentqueue results</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color: #1f2328; background: #fff; }}
table {{ border-collapse: collapse; margin: 1rem 0; width: 100%; }}
th, td {{ border: 1px solid #d0d7de; padding: 0.4rem 0.6rem; text-align: left; }}
th {{ background: #f6f8fa; }}
@media (prefers-color-scheme: dark) {{ body {{ color: #e6edf3; background: #0d1117; }} th {{ background: #161b22; }} th, td {{ border-color: #30363d; }} }}
</style>
</head>
<body>
<h1 id="results">agentqueue chaos benchmark</h1>
<p>{html.escape(meta_line(bench))}</p>
<p>Brackets hold a 95% t interval over the seeds. The provider is a local fake with injected faults, so these numbers measure the runner, not any model.</p>
<h2 id="main">Results by fault level</h2>
{table(HEADERS, results_rows(bench))}
<h2 id="faults">Fault rates</h2>
{table(["Level", "429", "500", "Timeout", "Slow", "Dropped", "Worker SIGKILL"], fault_rows(bench))}
<h2 id="baseline">Outbox against an inline send</h2>
{table(["Fault level", "Side effect path", "Duplicate effects per run", "Redeliveries per run"], naive_rows(bench))}
<h2 id="kill">Kill test</h2>
<p>{html.escape(kill_line(kill))}</p>
<p>Source files: <a href="../results/bench.json">results/bench.json</a> and <a href="../results/killtest.json">results/killtest.json</a>. Back to the <a href="../README.md">README</a>.</p>
</body>
</html>
"""


def main(check: bool = False) -> int:
    targets = {ROOT / "README.md": render_readme(), ROOT / "docs" / "index.html": render_index()}
    stale = [p for p, text in targets.items() if not p.exists() or p.read_text() != text]
    if check:
        for p in stale:
            print(f"out of date: {p.relative_to(ROOT)}. Run `agentqueue readme`.")
        return 1 if stale else 0
    for p, text in targets.items():
        p.write_text(text)
        print(f"wrote {p.relative_to(ROOT)}")
    return 0

# Project context: agentqueue

## What it is
A runner for LLM jobs that survive crashes, stay inside free-tier rate limits and never apply a side effect twice, as long as the receiver checks the dedupe key. All state lives in one SQLite file in WAL mode. Standard library only.

## Status
v0.1.0-rc1 release candidate on branch `claude/build-agentqueue`. All features in `feature_list.json` pass. The human steps are in `docs/HUMAN_STEPS.md`.

## Stack
Python 3.11+, `sqlite3`, `http.server`, `urllib`, `subprocess`, `tomllib`. Dev tools: pytest, ruff. No Redis, no Celery, no Docker.

## Commands
- `./init.sh`: venv, install, lint, link check, every test offline.
- `agentqueue submit|work|dispatch|status|requeue`: the runner.
- `agentqueue killtest --runs 200 --out results/killtest.json`: the SIGKILL test.
- `agentqueue bench --out results/bench.json`: the chaos benchmark, about 6 minutes.
- `agentqueue readme [--check]`: writes README.md and docs/index.html from `results/`.
- `python scripts/make_social_preview.py`, `python scripts/check_links.py`.

## Layout
- `src/agentqueue/store.py`: schema, claims, leases, steps, finish, requeue.
- `src/agentqueue/states.py`: the state machine table.
- `src/agentqueue/worker.py`: Context (`step`, `model`, `emit`), `run_once`, `dispatch_once`, `work`.
- `src/agentqueue/ratelimit.py`: token buckets, header pauses, breaker, log audit.
- `src/agentqueue/errors.py`: error classes, backoff.
- `src/agentqueue/receiver.py`: sending outbox entries, the SQLite receiver.
- `src/agentqueue/fakeprovider.py`: fault-injecting provider and receiver on 127.0.0.1.
- `src/agentqueue/chaos.py`: kill tests and the benchmark.
- `src/agentqueue/report.py`: README and results page generator. Template in `docs/templates/`.
- `src/agentqueue/examples.py`: summarize, classify, send_email, bench jobs.
- `tests/`: one file per area. `conftest.py` blocks non-local network.

## Rules
- Tests run offline. Every subprocess has a timeout. Tests write only to temp dirs.
- Every state change goes through `states.next_state`.
- Job code is deterministic between steps.
- Numbers in the README come only from `results/`.

## Skills that fit
superpowers TDD, systematic-debugging, verification-before-completion. Ponytail rules by hand (small and plain).

## Do not read
`.venv/`, `logs/`, `docs/build/BUILD_PROMPT.md` unless you need the brief.

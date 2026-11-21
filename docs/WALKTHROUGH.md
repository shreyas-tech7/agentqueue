# Walkthrough

A tour of every file. Short sentences, one idea each.

## The big picture

You submit a job. The job waits in a SQLite file. A worker takes it. The worker runs the job one step at a time. Each finished step is saved. If the worker dies, another worker starts the job again. The saved steps are skipped. Side effects wait in an outbox. A dispatcher sends them. The receiver ignores repeats.

## src/agentqueue

- `__init__.py` holds the version.
- `__main__.py` lets you run `python -m agentqueue`.
- `clock.py` has two clocks. `RealClock` reads real time. `FakeClock` moves only when a test says so. Every part of the code asks a clock for time, so tests run fast.
- `states.py` is the state machine. It is one table: a state and an event give the next state. `next_state` raises an error for any move that is not in the table.
- `store.py` is the SQLite store. `connect` turns on WAL mode. `Store.tx` opens `BEGIN IMMEDIATE`, which takes the write lock at once. `submit` uses `INSERT OR IGNORE` on the idempotency key. `claim` first recovers jobs whose worker died, then takes the next runnable job and hands out a lease token. `commit_step` saves a step result and its outbox entries together. `finish` moves the job to its next state. `requeue` brings a dead job back. Every write checks the lease token first.
- `worker.py` runs jobs. `@job("kind")` registers a function. `Context.step` returns a saved result or runs the step and saves it. `Context.model` calls the provider inside the rate budget. `Context.emit` adds an outbox entry to the current step. `run_once` claims one job and sorts out what happened. `dispatch_once` sends one outbox entry. `work` loops over both. `Crasher` is the test hook that kills the process at a chosen point.
- `ratelimit.py` keeps the limits. `Limits` holds one provider's numbers. `acquire` takes one request and some tokens from three buckets, or raises `RateWait`. `report` records how the call ended and moves the circuit breaker. `pause_from_headers` reads `retry-after` and `x-ratelimit-*`. `audit_calls` replays a request log through the buckets and the windows.
- `errors.py` names the error classes. `classify_status` turns an HTTP status into one. `backoff` picks a full-jitter wait. `retry_delay` makes `retry-after` a floor.
- `receiver.py` sends outbox entries. `send` posts to an HTTP target with an `Idempotency-Key` header, or writes to a SQLite receiver. `SqliteReceiver` applies each key once and counts deliveries.
- `fakeprovider.py` is the fake model server. `Faults` holds the fault rates. `FakeProvider` serves on 127.0.0.1. It also has a receiver at `/receive` and counters at `/stats`. Run it by hand with `python -m agentqueue.fakeprovider`.
- `examples.py` has the example jobs: `summarize`, `classify`, `send_email`, `bench` and the baseline `bench_naive`.
- `chaos.py` holds the kill test and the benchmark. `killtest` is a job that logs each step run and whether its result was already saved. `kill_run` runs one seed and kills workers. `kill_suite` runs many seeds in parallel. `bench_run` runs one benchmark run with the fake provider and four workers. `bench_suite` runs the grid. `interval` makes the 95% t interval.
- `report.py` writes README.md and docs/index.html from `results/`. `main(check=True)` fails if they are out of date.
- `cli.py` is the command line: `submit`, `work`, `dispatch`, `status`, `requeue`, `bench`, `killtest` and `readme`.

## tests

- `conftest.py` blocks any connection that is not local. It also puts each test in a temp folder.
- `helpers.py` makes a store with a fake clock and a set of limits.
- `test_network_guard.py` proves the guard blocks a remote host.
- `test_clock.py`, `test_store.py`, `test_states.py`, `test_steps.py`, `test_idempotency.py`, `test_outbox.py`, `test_ratelimit.py`, `test_retry.py`, `test_fakeprovider.py`, `test_cli.py`: one file per area.
- `test_kill.py` runs the 200 kill runs. It also proves the rerun detector works on a broken replay.
- `test_bench.py` runs a small live benchmark and checks the shipped results file.
- `test_readme.py` checks that the README matches the results.
- `test_repo.py` checks the files, the README order, the writing rules and the CI shape.

## Other files

- `init.sh` is the one command that sets up and tests everything.
- `examples/limits.toml` shows the limits format.
- `scripts/check_links.py` checks links in the docs. `scripts/make_social_preview.py` draws the social preview with no image library.
- `docs/templates/README.md.tmpl` is the README with blanks for the numbers.
- `results/` holds the benchmark and kill test results.
- `.github/workflows/ci.yml` runs `init.sh` on Python 3.11. `bench.yml` runs the benchmark by hand.

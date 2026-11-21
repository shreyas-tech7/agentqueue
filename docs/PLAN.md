# Plan

Built from Part 2 of docs/build/BUILD_PROMPT.md. The brief is the spec, so there were no question rounds. Assumptions are listed at the end and in DECISIONS.md.

## Goal
A one-machine runner for LLM jobs with durable steps, an outbox, rate budgets per provider, retries, a dead-letter flow, and a chaos benchmark that proves the claims with numbers.

## Tasks, in build order
1. Initializer, the injectable clock (`clock.py`), the SQLite store and schema (`store.py`). Test: WAL mode, schema reruns, two threads never claim one job.
2. The state machine table (`states.py`). Test every listed transition, refuse every other pair, crash in each state.
3. Durable steps and replay (`worker.Context.step`). Test: finished steps do not rerun, results are saved, duplicate step names fail.
4. Idempotency keys (`Store.submit`). Test: same key one job, also from six processes at once, other payload is a conflict.
5. Outbox and dispatcher (`commit_step`, `dispatch_once`, `receiver.py`). Test: entries commit with the step, nothing commits when the lease is lost, the receiver dedupes.
6. Token buckets and the scheduler (`ratelimit.py`). Test: a greedy client never breaks the audit, headers pause the provider, buckets survive a new store handle.
7. Retries, backoff, the breaker, dead letters (`errors.py`, `worker._fail`, `ratelimit.report`). Test each error class and each breaker move.
8. The fake provider (`fakeprovider.py`). Test each fault, determinism by seed, loopback only.
9. The kill tests (`chaos.kill_run`, `kill_suite`). Test: 200 runs, invariants hold, the rerun detector catches a broken replay.
10. The chaos benchmark and the README generator (`chaos.bench_suite`, `report.py`). Test: zero duplicates, zero limit breaks, three levels by five seeds, README numbers match.
11. Docs, the study-later kit, the release check and the hand-off.

## Assumptions
- `failed` is terminal for permanent errors, `dead` for exhausted retries. Done means 2 asks for succeeded or dead, and the kill test injects no permanent errors.
- A rate wait does not use an attempt.
- The benchmark measures throughput as requests reserved per minute over the span of the run, against the 900 per minute limit.
- "Wasted calls" counts provider replies that no committed step used.

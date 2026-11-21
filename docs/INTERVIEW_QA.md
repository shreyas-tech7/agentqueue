# Interview questions and answers

Each answer points to the code and the result files. The numbers live in `results/`, so look them up there instead of trusting a copy.

**1. What is the difference between idempotency and exactly once?**
Idempotency means doing a thing twice has the same effect as doing it once. Exactly once means the thing happens one time. agentqueue gets idempotent effects from at-least-once delivery plus a dedupe key: `worker.dispatch_once` may send an entry twice, and `receiver.SqliteReceiver.deliver` or the fake receiver drop the repeat. Exactly once delivery to a receiver that cannot dedupe is not possible in general. The sender can crash after the receiver acted and before it heard back. Then it must pick between a resend (maybe twice) and no resend (maybe never). The kill test counts the repeats the receiver caught in `results/killtest.json` under `redeliveries`.

**2. Why does the outbox entry commit in the same transaction as the step?**
`Store.commit_step` writes the step row and the outbox rows inside one `BEGIN IMMEDIATE`. If they committed apart, a crash between them gives one of two bugs: a saved step with a lost email, so the email never goes, or a queued email with no saved step, so the step runs again and queues a second email. One transaction makes both rows appear together or not at all. `test_outbox.test_failed_step_commit_writes_no_outbox` shows nothing leaks when the commit fails.

**3. How does a token bucket work?**
A bucket holds up to `capacity` tokens and refills at `rate` per second. Each request takes tokens. No tokens, no request. `ratelimit.acquire` computes the level from the stored level plus the refill since the last update. agentqueue sets `capacity = burst` and `rate = (limit - burst) / window`, so no window of that length can see more than the limit. The common shape, capacity equal to the limit, can let twice the limit through one window. `test_ratelimit.test_naive_bucket_can_double_the_limit` shows it.

**4. Why does replay need deterministic job code?**
`Context.step` finds saved results by the job ID and the step name. Replay runs the job function from the top. If the code reads the clock or a random number outside a step, it can take another branch on replay and ask for different step names. Then saved work is skipped and new work runs. Anything that varies goes inside a step, where its value is saved.

**5. What does a circuit breaker prevent?**
It stops every job from hammering a provider that is down. After `breaker_failures` server errors or timeouts in a row, `ratelimit.report` opens the breaker. `acquire` then raises `RateWait` until the cooldown ends. Then one trial call goes through. Success closes the breaker. Failure opens it again. A 429 does not count, because a 429 means the provider is healthy and we were too fast. Tests in `test_retry.py` cover each move.

**6. What happens when a worker dies mid-step?**
Its job stays `running` with a lease. The next `Store.claim` calls `recover_stale`. It returns the job to `queued` when the lease ended or when the owner pid is gone. The next worker replays the saved steps and reruns only the step that was running. `results/killtest.json` shows `finished_step_reruns` and `unfinished_step_reruns` over 200 runs.

**7. How do you stop a worker that was presumed dead from writing over the new owner?**
Every claim hands out a new `lease_token`. `Store._check_lease` runs at the start of every write and raises `LostLease` if the token changed. `test_states.test_lost_lease_blocks_old_worker` proves it.

**8. Why SQLite and why WAL mode?**
One machine, one file, no server to run. WAL mode lets readers keep reading while one writer writes. `BEGIN IMMEDIATE` takes the write lock at the start, so two workers cannot both read a job as free and both take it. `test_store.test_two_threads_never_claim_the_same_job` checks it.

**9. How do the retries work?**
`errors.classify_status` sorts errors. 429, 5xx and timeouts retry. Other 4xx do not, because the same request fails the same way. `errors.backoff` picks a wait uniformly between zero and `base * 2^(attempt-1)`, capped. That is full jitter, which spreads out clients that failed together. A `retry-after` header sets a floor.

**10. What is the dead-letter table for?**
It keeps jobs that ran out of attempts (`dead`) or hit a permanent error (`failed`), with the last error. `agentqueue status` lists them. `agentqueue requeue` sends them back with fresh attempts, and they resume after their last finished step.

**11. How do you know the scheduler never breaks a limit?**
Each request is written to the `calls` table in the same transaction that takes its tokens. `ratelimit.audit_calls` replays that log through every bucket and also counts every sliding window. The benchmark audits every run, and `results/bench.json` has `limit_violations` per run. Be clear about what this proves: it checks the scheduler's own reservation log, not the moment each request reached the provider. And a benchmark run sends a few hundred requests, so the minute and day windows are never close to full. The bucket audit, which the start-up burst does push to its edge, is the part that tests something.

**12. What does the chaos benchmark measure?**
`chaos.bench_run` starts the fake provider with fault rates, four worker processes, a parent that SIGKILLs a random worker on a random schedule, and a seeded crash point in every worker process. It reports completion rate, wasted calls, duplicate effects at the receiver, p50 and p95 job latency and requests per minute. `bench_suite` runs three fault levels by five seeds. The README table comes from `results/bench.json`.

**13. What are wasted calls?**
Replies the provider produced that no saved step used: the worker died before it saved them, or the client gave up first. The benchmark computes provider completions minus the call ids stored in committed steps. On a paid API each one is money spent for nothing. Durable steps keep this to the calls that were in flight at the moment of a crash.

**14. What did the baseline show?**
`examples.bench_naive` does the same work but sends the side effect inline inside the step, with no dedupe key. When a kill lands between the send and the step commit, the step reruns and the receiver gets a second effect. Compare `summary.medium` and `summary.medium-naive` in `results/bench.json`.

**15. How do the kill tests pick kill points?**
`worker.Crasher` counts crash points: after a claim, before and after a step function, after a step commit, before the finish, and on both sides of an outbox send. With `AGENTQUEUE_CRASH_AT=k` the worker SIGKILLs itself at hit k. `chaos.kill_run` picks k from the seed, and kills a quarter of the launches from outside after a random delay. Every seed is reproducible.

**16. How does it handle a provider that says slow down?**
`ratelimit.pause_from_headers` reads `retry-after` and `x-ratelimit-remaining-*` with `x-ratelimit-reset-*`. `report` stores a `paused_until` time for the provider. `acquire` raises `RateWait` until then, and the job waits without losing an attempt.

**17. When should a team use Temporal or DBOS instead?**
When workers run on more than one machine, when workflows last weeks and their code changes under them, when they want a UI and history search, or when a team runs it in production with on-call. agentqueue is for one builder on one machine.

**18. How do you keep tests fast and stable with real time in the code?**
Everything reads time from an injected clock. Unit tests use `FakeClock` and move time by hand. Only the kill test and the benchmark use real processes and real time, and every subprocess has a timeout.

**19. What did the code review catch?**

A step that makes two model calls, with a burst of one, looped for ever. Call 1 took the only request, call 2 hit a rate wait, and the old code threw the whole step away, so the replay sent call 1 again. `worker.Context._acquire` now waits in place once a call in the step has gone out, renewing the lease, and `tests/test_retry.py::test_step_with_two_calls_and_burst_of_one_finishes` checks that each call goes out once. The review also found a burst equal to its limit (refill rate 0), a lease loss that killed the worker, and an outbox that could retry for ever. docs/RELEASE_CHECK.md lists every finding.

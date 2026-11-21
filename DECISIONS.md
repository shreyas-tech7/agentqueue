# Decisions

One line per choice: what, the options, the reason.

- Name: `agentqueue`. PyPI and npm both answered 404 on 2026-11-21. A web search found only a small Go package (ka2n/agentqueue) and an unrelated docs site, nothing well known. So the brief's first pick stays, and no rename step is needed.
- Workdir: the repo was already cloned at /home/user/agentqueue by the session, so the build used that folder instead of ~/work/agentqueue. Same remotes.
- Job states: `failed` is terminal for permanent errors (400 class, unknown job kind, duplicate step name). `dead` is for retryable errors that ran out of attempts and for workers that died with no attempts left. Options: one terminal error state, or a transient failed state. Two terminal states tell an operator whether a retry could ever help.
- `waiting` covers both retry backoff and rate waits. A rate wait gives the attempt back, because the job did nothing wrong.
- Rate state lives in SQLite (tables `buckets`, `providers`, `calls`), not in memory. Options: in-memory buckets per worker. A restarted worker with in-memory buckets starts full and can break the limit. A test proves it.
- Bucket shape: capacity `burst`, refill `(limit - burst) / window`. Options: capacity = limit and refill = limit/window. The common shape lets up to twice the limit through one window. A test shows that case.
- Day bucket burst defaults to half the daily limit, so a day's quota is not forced into an even trickle.
- The request is reserved in the `calls` table in the same transaction that takes the tokens. So the log the audit checks is the log the buckets used, crash or not.
- A 429 pauses the provider (retry-after, x-ratelimit headers) but does not count toward the breaker. Server errors and timeouts do. A 429 means the provider is healthy.
- A half-open breaker that never hears back from its trial call allows a new trial after the cooldown. Otherwise a crash mid-trial would block the provider forever.
- Recovery of a dead worker: a running job is reclaimed when its lease ends or when its owner pid is gone (one machine only, so `os.kill(pid, 0)` works). The outbox uses the same rule. This keeps the kill tests fast without short leases.
- Lease tokens fence old workers: every write checks the token, so a worker presumed dead cannot commit over the new owner.
- Kill points: the worker SIGKILLs itself at the k-th crash point (`AGENTQUEUE_CRASH_AT`), with k picked by the seed, and a quarter of launches are killed from outside after a random delay. Options: only random-time kills. Seeded crash points hit every boundary, including the gap between a send and its mark, and stay reproducible. This is the brief's own fallback, used from the start.
- The rerun check: each kill-test step looks in the store for its own committed result before it runs and logs what it saw. A test proves the check catches a broken replay.
- Wasted calls = replies the fake provider generated minus model calls that a committed step used. This counts replies lost to a kill and replies the client timed out on.
- The benchmark also runs a no-outbox baseline (inline send, no dedupe key) at the medium and high levels, to show what the outbox prevents. Options: no baseline. Numbers beat claims.
- Intervals: mean and 95% t interval over the 5 seeds per level. A value that is the same on every seed is labeled so, with no invented width.
- Benchmark limits: 900 requests per minute and 60,000 tokens per minute, so a run takes about 15 to 30 seconds and the whole suite about 6 minutes on 4 cores.
- Skills: ponytail, caveman and humanizer were not installed with `npx skills add`. Earlier builds in this project had that install denied by the permission system, and the build rules say not to retry a denied step in another form. Their rules were applied by hand. The superpowers skills were already in the session.
- The README is a template in `docs/templates/README.md.tmpl` filled by `agentqueue readme`. CI runs `readme --check`.
- Code review (in place of `ponytail-review`, which was not installed): a fresh reviewer subagent read `src/` and found 3 must-fix and 6 should-fix items. All 3 must-fix items, 5 of the 6 should-fix items and 3 of the 5 nits were fixed, each with a test. The token estimate nit is now spelled out in the README Limits. The list is in docs/RELEASE_CHECK.md.
- A rate wait inside a step that already sent a model call waits in place (up to 120 s), renewing the lease. Options: throw the step away (the old way), or charge an attempt. Throwing it away could loop for ever when one step needs more than one burst, and re-bills the call each loop. A longer wait still ends the attempt, so a job can never loop without using attempts.
- Every burst must be below its limit. A burst equal to its limit gives a refill rate of 0, which starved the provider for good. A bucket that can never refill now raises a permanent error.
- The lease renews before every model call, not only at step commits. A step with several slow calls could outlive its lease and run twice at once.
- The outbox has a dead list. A permanent send error, or 20 failed tries, moves an entry there, so `work --until-empty` can finish. `requeue --outbox` sends them again.
- Dedupe keys carry a random store id first: `store:job:step:index`. The brief fixed the key as job, step and index. Those three are still in it. Without the store id, a reset database or a second queue reuses `1:send:0`, and the receiver drops a real new effect as a repeat.
- Not fixed: the pid check assumes one process namespace. The README Limits section says not to share one file across containers. Recovery scans running jobs on each claim, which is fine at this scale.

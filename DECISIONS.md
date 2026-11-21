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
- The benchmark also runs a no-outbox baseline (inline send, no dedupe key) at the medium level, to show what the outbox prevents. Options: no baseline. Numbers beat claims.
- Intervals: mean and 95% t interval over the 5 seeds per level. A value that is the same on every seed is labeled so, with no invented width.
- Benchmark limits: 900 requests per minute and 60,000 tokens per minute, so a run takes about 15 to 30 seconds and the whole suite about 6 minutes on 4 cores.
- Skills: ponytail, caveman and humanizer were not installed with `npx skills add`. Earlier builds in this project had that install denied by the permission system, and the build rules say not to retry a denied step in another form. Their rules were applied by hand. The superpowers skills were already in the session.
- The README is a template in `docs/templates/README.md.tmpl` filled by `agentqueue readme`. CI runs `readme --check`.

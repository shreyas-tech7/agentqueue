# Changelog

## v0.1.0-rc1 (2026-11-21)

Release commit: RELEASE_SHA_PENDING

First release candidate.

- SQLite job store in WAL mode with idempotency keys and leases fenced by a token.
- Job state machine as an explicit table.
- Durable steps with replay. The replay key is the job ID plus the step name.
- Outbox committed with each step, dispatcher with dedupe keys `store:job:step:index`, and a dead list for entries that cannot be sent.
- Token buckets per provider for requests per minute, requests per day and tokens per minute, kept in the store. Pauses from `retry-after` and `x-ratelimit-*` headers.
- Retries by error class with full-jitter backoff, a circuit breaker per provider, a dead-letter table and `requeue`.
- Fake provider with seeded fault injection, on 127.0.0.1.
- Kill test (200 SIGKILL runs) and the chaos benchmark with a no-outbox baseline.
- Commands: submit, work, dispatch, status, requeue, bench, killtest, readme.

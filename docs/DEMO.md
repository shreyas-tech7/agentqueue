# Demo

A real terminal session from 2026-11-21, copied as it ran. No keys, no sign-in, no network beyond 127.0.0.1.

Setup: a fake provider on port 8765 that answers 30% of model requests with 429, and `examples/limits.toml` with its port changed to 8765.

```bash
python -m agentqueue.fakeprovider --port 8765 --seed 3 --rate-429 0.3 &
sed 's/8000/8765/' examples/limits.toml > limits.toml
```

What the session shows:

- The same idempotency key twice gives one job. The same key with other input is refused.
- One 429 came back. The job waited out the provider's pause and finished.
- The email went out once through the outbox, with a dedupe key.
- A job with a target the dispatcher cannot send to still succeeds, and its side effect lands on the outbox's dead list with the reason, so `work --until-empty` still ends. `agentqueue requeue --outbox` would send it again.
- A short kill test at the end.

```text
$ agentqueue submit --kind classify --key ticket-1 --payload '{"ticket": "I was charged twice"}'
{"job_id": 1, "created": true}

$ agentqueue submit --kind classify --key ticket-1 --payload '{"ticket": "I was charged twice"}'
{"job_id": 1, "created": false}

$ agentqueue submit --kind classify --key ticket-1 --payload '{"ticket": "something else"}'
conflict: key 'ticket-1' belongs to job 1 with other input

$ agentqueue submit --kind send_email --key launch-mail --payload '{"topic": "launch", "to": "team@example.com", "target": "http://127.0.0.1:8765/receive"}'
{"job_id": 2, "created": true}

$ agentqueue submit --kind send_email --key bad-target --payload '{"topic": "x", "to": "a@example.com", "target": "ftp://nowhere"}'
{"job_id": 3, "created": true}

$ agentqueue work --limits limits.toml --until-empty
ran 7 job attempts

$ agentqueue status --limits limits.toml
queue:
  queued     0
  waiting    0
  running    0
  succeeded  3
  failed     0
  dead       0
  outbox pending 0
  outbox dead 1
rates (last 60 s, last 24 h):
  fake: 4 req/min, 292 tok/min, 4 req/day of 30 rpm, 6000 tpm, 1000 rpd, breaker closed
dead letters: 0
dead outbox entries: 1
  outbox 4f9436158628fd03:3:send:0 to ftp://nowhere: PermanentError: unsupported target 'ftp://nowhere'

$ curl -s http://127.0.0.1:8765/stats
{"requests": 4, "completions": 3, "faults": {"429": 1, "ok": 3}, "deliveries": 1, "redeliveries": 0, "effects": 1, "duplicate_effects": 0, "naive_effects": 0}
$ agentqueue killtest --runs 20 --seed 500 | head -14
{
  "runs": 20,
  "base_seed": 500,
  "kills": 34,
  "hung_workers": 0,
  "runs_with_a_kill": 15,
  "bad_end_states": 0,
  "finished_step_reruns": 0,
  "unfinished_step_reruns": 2,
  "missing_effects": 0,
  "redeliveries": 4,
  "dead_jobs": 0,
  "succeeded_jobs": 60,
  "meta": {

```

The full kill test is `agentqueue killtest --runs 200`. Its last result is in [results/killtest.json](../results/killtest.json).

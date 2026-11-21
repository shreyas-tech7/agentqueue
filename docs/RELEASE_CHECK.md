# Release check

The steps from section 11 of the build prompt, run on 2026-11-21 before v0.1.0-rc1.

## 1. Code review

`ponytail-review` was not installed (see DECISIONS.md). In its place a fresh reviewer subagent, which did not write the code, read all of `src/` and confirmed its two biggest findings with small scripts. What it found and what was done:

| Finding | Kind | Done |
| --- | --- | --- |
| A step with two model calls and a burst of one looped for ever: a rate wait threw the step away and the replay sent call 1 again | must-fix | Fixed. A step that already sent a call waits in place, up to 120 s, and renews its lease. Test `test_step_with_two_calls_and_burst_of_one_finishes` |
| A burst equal to its limit gave a refill rate of 0, so rpm = 1 starved the provider for good | must-fix | Fixed. Bursts must be below the limit, defaults respect that, and a bucket that cannot refill raises a permanent error. Two tests in `test_ratelimit.py` |
| `LostLease` raised by `finish` escaped `run_once` and killed the worker | must-fix | Fixed. Test `test_lost_lease_during_finish_does_not_kill_the_worker` |
| The lease renewed only at step commits | should-fix | Fixed. It renews before every model call |
| Outbox entries retried for ever, so `work --until-empty` could hang | should-fix | Fixed. Dead list, `requeue --outbox`, two tests. The kill test now counts a hung worker apart from a kill |
| Dedupe keys were unique only inside one SQLite file | should-fix | Fixed. Keys start with a random store id. Test `test_dedupe_keys_differ_between_stores` |
| The pid check assumes one process namespace | should-fix | Not fixed. Written in the README Limits |
| Retry-After as an HTTP date was ignored | should-fix | Fixed. Test `test_retry_after_as_http_date` |
| A reply that is not JSON counted as a timeout | should-fix | Fixed. It is a server error now. Test `test_bad_reply_is_a_server_error` |
| A job result that is not JSON retried until dead | nit | Fixed. It fails at once. Test `test_non_json_result_fails_permanently` |
| A failed COMMIT left the transaction open | nit | Fixed in `Store.tx` |
| A new SQLite receiver object for every delivery | nit | Fixed, cached per path |
| The tokens-per-minute promise holds only for the token estimate | nit | Written in the README Limits |
| Recovery scans running jobs on every claim | nit | Not changed. Cheap at this scale |

The full `./init.sh` run then showed one more real bug: six processes that open a brand new SQLite file at once can get "database is locked" from `PRAGMA journal_mode=WAL`, which does not wait on the busy timeout. `store.connect` now retries it. The two-process idempotency test passed 15 runs in a row after the fix.

## 2. Security audit

Skipped, as the build prompt says. No sign-in, personal data or payments. Steps 1 and 3 cover it.

## 3. Tools

- `gitleaks git -v .`: no leaks found in the full history.
- `pip-audit` on the installed dependencies: no known vulnerabilities. The package itself is not on PyPI, so it was skipped.
- `ruff check --select S`: 11 notes, each checked by hand. `S311` (5): `random` is used for jitter and seeded fault plans, not for secrets. `S603` (2): the benchmark starts `sys.executable` with a fixed argument list. `S310` (3): the receiver accepts only http and https, the model call now refuses a provider url that is not http or https, and the naive baseline posts to the benchmark's own local receiver. `S608` (1): the column names in `Store._move` come from code, never from input.
- `actionlint`: no findings on both workflows.
- `scripts/check_links.py`: no broken local links. Three outside links are not fetched by the script: the CI badge, which works once the repo has a run on GitHub, and two articles.

## 4. Humanizer

Not installed (see DECISIONS.md). Its rules were applied by hand to README.md and docs/*.md: short plain sentences, no filler words, no dashes. `tests/test_repo.py::test_writing_rules` checks the dash, semicolon and filler rules on every prose file.

## 5. Outside check

A second fresh subagent cloned the repo to /tmp/outside-check, ran `./init.sh` there, checked each Done-means item with its own commands and edited nothing. It also ran the tests in a network namespace with only loopback, and they passed. It passed items 2 to 7 and failed item 1 only on the release SHA, which the release commit fills in. What it raised, and what was done:

| Finding | Done |
| --- | --- |
| The outbox's "0 duplicate effects" was a constant in the fake receiver, not a measurement | Fixed. The receiver now counts the same effect body applied under two different keys. Test `test_duplicate_effects_are_measured`. The benchmark was run again |
| Only 169 of the 200 kill runs actually killed a worker | Fixed. The first worker in every run now dies at a crash point that is sure to be reached. The test asks for a kill in all 200 runs |
| The rate audit checks the scheduler's own log, and the minute and day windows never fill in the benchmark | Written plainly in INTERVIEW_QA.md question 11 |
| Job latency is mostly queue wait, since all jobs are submitted at once | Written under the README results table |
| Requests per minute above 900 at the low level reads like a limit break | Explained under the README results table |
| One high-level run left a job dead, and the README did not say why | Explained under the README results table |
| The inline baseline barely differs from the outbox | Already said plainly in the README. Not changed |
| The tool results in this file could not be checked from the clone | Not changed. They were run in the build session as listed in section 3 |

## 6. Final state

After these fixes the benchmark (all levels, seeds 1 to 5, both baselines) and the 200-run kill test were run again on the final code, then `agentqueue readme`. `./init.sh` passed every test except `test_release_docs`, which needs the release SHA, and the release commit is followed by one commit that writes it. Every `passes` flag in feature_list.json was set from a passing check.

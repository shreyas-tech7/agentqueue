# Blockers

Each entry: what failed, the exact error, what was done instead.

1. No `gh`, no pull requests, no tag pushes from this machine. The proxy refuses GraphQL and tag pushes. Instead: the release SHA is in CHANGELOG.md and docs/HUMAN_STEPS.md, the About text is in docs/GITHUB_ABOUT.md and the topics are in docs/topics.txt. The coordinator session opens the PR and makes the pre-release.
2. Model hosts are blocked: `curl https://api.groq.com` and `https://openrouter.ai` gave `CONNECT tunnel failed, response 403` on 2026-11-21. There is no provider key. Record mode is not part of Done means, so the Stretch item (one recorded run of 20 jobs on a free provider) is not done. Manual step in docs/HUMAN_STEPS.md.
3. Skill installs (ponytail, caveman, humanizer) were not attempted, see DECISIONS.md. Their rules were applied by hand. The review in the release check used a subagent instead of `ponytail-review`.

Bug triage (frequency x severity, fixed when 5 or more):

- Live bug, fixed: the dispatcher kept a 10 second lease on an outbox entry after its process died, so a kill stalled delivery for 10 seconds (4 x 2 = 8). The outbox now records the owner pid and reclaims the entry when the pid is gone.
- Live bug, fixed: a half-open breaker whose trial worker died stayed half open forever (2 x 5 = 10). A new trial now starts after the cooldown. Test: `test_lost_trial_does_not_stick_half_open`.
- Live bug, fixed: the first day bucket shape gave a day burst equal to the minute burst, so a 1000 per day limit allowed about 41 requests an hour (3 x 3 = 9). The day burst is now its own setting with default rpd / 2.
- Test gap, open (1 x 2 = 2): replay does not check that the step names on replay match the saved order. Code that changes its step names between runs gets new steps, not an error. The README limits section tells users to keep job code deterministic.
- Live bug, fixed (review): a step with two model calls and a burst of one looped for ever and re-sent call 1 each loop (3 x 5 = 15). Test: `test_step_with_two_calls_and_burst_of_one_finishes`.
- Live bug, fixed (review): a burst equal to its limit starved the provider for good (2 x 5 = 10). Bursts must be below the limit.
- Live bug, fixed (review): `LostLease` from `finish` killed the worker process (2 x 3 = 6).
- Live bug, fixed (review): an outbox entry with a bad target retried for ever and `work --until-empty` never ended (2 x 4 = 8). Dead list added.
- Flake, fixed: six processes opening a new SQLite file at once could get "database is locked" from the WAL pragma (2 x 3 = 6). `store.connect` retries it.

# Progress

## Done
- Initializer, memory files, CI, secret scan hook.
- Store, clock, state machine, durable steps, idempotency, outbox and dispatcher with a dead list, token buckets, retries, breaker, dead letters, fake provider, CLI.
- Kill test: 200 SIGKILL runs in tests/test_kill.py, a kill in every run.
- Chaos benchmark with a no-outbox baseline, run on the final code. README and results page generated from results/.
- Code review and outside check, every must-fix item fixed with a test. See docs/RELEASE_CHECK.md.
- Docs: PLAN, WALKTHROUGH, INTERVIEW_QA, TOOLING, ENVIRONMENT, DEMO, RELEASE_CHECK, HUMAN_STEPS, COWORK_PROMPT.
- Release commit and the commit that writes its SHA.

## Next
- Human steps in docs/HUMAN_STEPS.md: PR, merge, tag v0.1.0-rc1, About box, topics, public repo when ready.

## Open blockers
See BLOCKERS.md: no gh, PR or tags from this machine, no provider key.

## Last ./init.sh
2026-11-21: all checks pass once the release SHA is written.

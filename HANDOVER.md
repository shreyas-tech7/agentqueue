# Handover

If this session stops, the next one starts here.

- Branch: `claude/build-agentqueue` on origin (shreyas-tech7/agentqueue) and on the backup remote.
- Read PROGRESS.md for done and next.
- Commands:
  - `./init.sh` (all tests offline, about 1 minute)
  - `.venv/bin/agentqueue bench --out results/bench.json` (about 15 minutes, run it with nohup)
  - `.venv/bin/agentqueue killtest --runs 200 --out results/killtest.json`
  - `.venv/bin/agentqueue readme`
- Open decisions: none. See DECISIONS.md for the ones made.

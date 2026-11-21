# Contributing

Thanks for looking. agentqueue is small on purpose, so please open an issue before a large change.

1. Run `./init.sh`. It makes `.venv`, installs the project with its dev tools, lints, checks links and runs every test offline.
2. Write a test that fails first. Name the broken case in its docstring.
3. Keep the standard library as the only runtime dependency.
4. Every state change goes through `states.next_state`. Add a row to the table if you need a new move, and test it.
5. Never type numbers into README.md. Edit `docs/templates/README.md.tmpl`, rerun the benchmark if needed, then run `agentqueue readme`.
6. Run `gitleaks git --pre-commit --redact --staged` before you commit. The pre-commit config has the same hook.

Tests must not reach the network. `tests/conftest.py` fails any connection to a host that is not local.

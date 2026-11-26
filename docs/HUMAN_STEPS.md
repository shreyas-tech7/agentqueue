# Human steps

Steps that only Shreyas can do, in order. The build session could not do them: the proxy refuses pull requests, tags and repo settings from the build machine.

Release commit: ab8aa98bf69ecda7356b430535650077755cb05a
Branch: `claude/build-agentqueue`

1. Open a pull request from `claude/build-agentqueue` into `main` at https://github.com/shreyas-tech7/agentqueue/pulls. Read the diff. Merge with a merge commit, so the release commit stays in the history of `main`. (The coordinator session may already have done this.)
2. Tag the release commit:

   ```bash
   git fetch origin
   git tag -a v0.1.0-rc1 ab8aa98bf69ecda7356b430535650077755cb05a -m "Release candidate 1"
   git push origin v0.1.0-rc1
   ```

   Or create a pre-release with that tag on the GitHub releases page.
3. Run the secret scan once more on a fresh clone: `gitleaks git -v .`
4. Fill in the About box with the text in docs/GITHUB_ABOUT.md and add the topics in docs/topics.txt, if the coordinator could not.
5. Upload `docs/social-preview.png` under Settings, General, Social preview.
6. Make the repo public. Done 2026-11-26: the repo is public.
7. Turn on Pages from the `main` branch, folder `/docs`, so docs/index.html is the results page.
8. Pin the repo or list it in your profile README.
9. Publish to PyPI with trusted publishing, if you want the package public. The name `agentqueue` was free on 2026-11-21.
10. For a live run, point the runner at a real free provider: add a `[providers.<name>]` block to a copy of examples/limits.toml with the provider's real limits and URL, set `AGENTQUEUE_API_KEY` in your shell, and run a few jobs. Check the free limits first in cheahjs/free-llm-api-resources. OmniRoute or LiteLLM can sit in front as a router, but both need keys, so they stay manual.
11. Decide whether `docs/build/BUILD_PROMPT.md` stays public.

Human checks still listed as "not done yet" in the README:

- Read the diff of `src/agentqueue/store.py` and `src/agentqueue/worker.py`.
- Run `./init.sh` and `agentqueue killtest` on your own machine.
- Run one job against a real free provider.

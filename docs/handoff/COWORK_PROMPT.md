# Prompt for Claude Cowork

Paste everything below the line into Claude Cowork.

---

You are helping Shreyas finish the browser steps for his private GitHub repo shreyas-tech7/agentqueue. Do not ask questions. Do not stop without a result. When something fails, write down what failed and move on to the next step.

Rules:

- Do not make the repo public.
- Do not publish a package to PyPI.
- Do not enter API keys or secrets anywhere.
- Do not sign in for Shreyas. If a page needs a sign-in he has not done, skip that step and note it.
- Do not pay for anything.

Steps (all free and all can be undone):

1. Open https://github.com/shreyas-tech7/agentqueue/actions and check that the latest CI run on `main` or on `claude/build-agentqueue` is green. Write down the run and its result.
2. Open the repo settings. Check the About box. If the description is empty, set it to the text in docs/GITHUB_ABOUT.md and add the topics from docs/topics.txt.
3. Under Settings, General, Social preview, upload docs/social-preview.png from the repo.
4. Open the releases page and check whether a pre-release v0.1.0-rc1 exists. If not, write that down. Do not create a tag yourself unless Shreyas said so.

When you finish, write the next prompt for Claude Code. It must hold:

- The context: agentqueue is a crash-safe LLM job runner in one SQLite file. The branch is `claude/build-agentqueue`.
- What is complete, from steps 1 to 4 above.
- What is still open, from docs/HUMAN_STEPS.md.
- This resume launcher: "Read docs/build/BUILD_PROMPT.md and follow it. Resume from PROGRESS.md on branch claude/build-agentqueue. Do not ask questions. Do not stop until every Done-means item is true or blocked with a fallback."

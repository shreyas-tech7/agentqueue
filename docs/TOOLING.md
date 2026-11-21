# Tooling

| Tool | Source | Version | Why | Check result |
| --- | --- | --- | --- | --- |
| superpowers skills | already in the session (claude.ai account) | as installed | writing-plans, TDD, systematic-debugging, verification-before-completion | applied inline, one feature at a time |
| ponytail, caveman, humanizer, find-skills | `npx skills add ...` | none | code style, terse progress, doc pass, skill search | not installed. Earlier builds in this project had the install denied by the permission system, so it was not retried. Their rules were applied by hand |
| gitleaks | `go install github.com/zricethezav/gitleaks/v8@latest` | v8.30.1 | secret scan before each commit and on full history | installed, no leaks found |
| actionlint | `go install github.com/rhysd/actionlint/cmd/actionlint@latest` | v1.7.12 | workflow lint | installed, clean |
| pip-audit | `uv tool install pip-audit` | 2.10.1 | dependency audit | installed, see docs/RELEASE_CHECK.md |
| ruff | PyPI, dev extra | at install time | lint, format, `--select S` | runs in init.sh |
| graphify, cisco-ai-skill-scanner, pre-commit | `uv tool install` | not installed | not needed: under 30 source files, no new skills installed, gitleaks runs directly | skipped |
| security-audit | session skill list | not used | the brief says skip it: no sign-in, personal data or payments | skipped |

Repos from /mnt/project-files/portfolio/GITHUB_REPOS.md, checked for fit:

| Repo | Used? | Why |
| --- | --- | --- |
| obra/superpowers | yes | the plan, TDD, debugging and verification skills, already in the session |
| DietrichGebert/ponytail | rules by hand | least code: standard library only, one module per concern |
| JuliusBrussee/caveman | no | progress chat only, nothing in the repo |
| Graphify-Labs/graphify | no | fewer than 30 source files |
| vercel-labs/skills (find-skills) | no | no missing skill to find |
| affaan-m/ECC, VoltAgent/awesome-agent-skills | no | no new skill needed |
| microsoft/playwright-mcp | no | no web UI, the results page is static HTML |
| unclecode/crawl4ai | no | no crawling |
| blader/humanizer | rules by hand | the docs pass in the release check followed its rules |
| wheresryan22/anatomy | no | Stretch only |
| cloudflare/security-audit-skill | no | no sign-in, personal data or money |
| OmniRoute, litellm | no | need keys. Listed in docs/HUMAN_STEPS.md for a live run |
| cheahjs/free-llm-api-resources | no | no record mode in this run |
| PurpleLlama, Prompt Guard 2, deberta injection model, garak, mcp-scanner | no | no prompt input from untrusted users in this project |
| cisco-ai-defense/skill-scanner | no | no new skill to scan |
| gitleaks/gitleaks | yes | secret scan |
| google/osv-scanner | no | api.osv.dev not probed, pip-audit covers the dev dependencies |
| semgrep/semgrep | no | `ruff check --select S` covers the security rules |
| rtk-ai/rtk | no | optional, not needed |
| NousResearch/hermes-agent | idea only | the runner is meant to sit under an agent fleet like it later |
| coreyhaines31/marketingskills | no | README written by hand to the writing rules |

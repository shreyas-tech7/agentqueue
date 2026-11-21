# Environment

Probes run from the build VM. Each line: host, result, date.

| Host | Result | Date |
| --- | --- | --- |
| pypi.org/pypi/agentqueue/json | 404 (name free) | 2026-11-21 |
| registry.npmjs.org/agentqueue | 404 (name free) | 2026-11-21 |
| huggingface.co | proxy 403, blocked | 2026-11-21 |
| api.groq.com | proxy 403, blocked | 2026-11-21 |
| openrouter.ai | proxy 403, blocked | 2026-11-21 |

The brief needs no model provider for Done means. Every test and the benchmark use the local fake provider.

Machine: Intel(R) Xeon(R) Processor @ 2.10GHz, 4 cores, Python 3.13 on the VM, 2026-11-21. CI uses Python 3.11.

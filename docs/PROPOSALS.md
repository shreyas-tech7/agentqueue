# Proposals

Ideas that are not in the brief. Shreyas picks what gets built.

- An OpenAI-compatible adapter in record mode with one recorded run of 20 jobs on a free provider (the brief's Stretch item).
- A replay check that the step names on replay match the saved order, so a job whose code changed fails loud.
- Lease renewal from a heartbeat thread for steps that run longer than the lease.
- A `prune` command that deletes finished jobs older than N days.
- Priorities, so a small urgent job can pass a long batch.

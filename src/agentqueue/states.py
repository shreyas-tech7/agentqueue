"""The job state machine, written as an explicit table.

Every state change in the store goes through `next_state`. A change that is not in the
table raises `BadTransition`, so a bug cannot move a job somewhere it should never be.
"""

from __future__ import annotations

STATES = ("queued", "running", "waiting", "succeeded", "failed", "dead")
TERMINAL = ("succeeded", "failed", "dead")

# (state, event) -> new state
TRANSITIONS: dict[tuple[str, str], str] = {
    ("queued", "claim"): "running",  # a worker takes the job inside BEGIN IMMEDIATE
    ("waiting", "claim"): "running",  # the retry time or rate wait has passed
    ("running", "succeed"): "succeeded",  # the job function returned
    ("running", "retry"): "waiting",  # retryable error (429, 5xx, timeout), attempts left
    ("running", "rate_wait"): "waiting",  # no rate budget or the breaker is open, no attempt used
    ("running", "fail"): "failed",  # permanent error (400 class), no retry helps
    ("running", "exhaust"): "dead",  # retryable error, but no attempts left
    ("running", "recover"): "queued",  # the worker died: lease expired or its pid is gone
    ("running", "recover_exhaust"): "dead",  # the worker died and no attempts are left
    ("failed", "requeue"): "queued",
    ("dead", "requeue"): "queued",
}

EVENTS = sorted({e for _, e in TRANSITIONS})


class BadTransition(ValueError):
    pass


def next_state(state: str, event: str) -> str:
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise BadTransition(f"no transition from {state!r} on {event!r}") from None

"""Error classes and the retry policy.

The class of an error decides what happens next:

| Error | Retry? | Why |
| --- | --- | --- |
| 429 rate limited | yes, after retry-after | the provider is fine, we were too fast |
| 5xx server error | yes, with backoff | the provider had a bad moment |
| timeout or dropped connection | yes, with backoff | the request may never have arrived |
| other 4xx | no | the request itself is wrong, a retry sends the same wrong request |
"""

from __future__ import annotations

import random


class JobError(Exception):
    retryable = True
    outcome = "server_error"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimited(JobError):
    outcome = "rate_limited"


class ServerError(JobError):
    outcome = "server_error"


class TransientError(JobError):
    """Timeout, reset or dropped connection."""

    outcome = "timeout"


class PermanentError(JobError):
    retryable = False
    outcome = "client_error"


def classify_status(status: int, body: str = "", retry_after: float | None = None) -> JobError:
    if status == 429:
        return RateLimited(f"429 {body[:200]}", retry_after)
    if status >= 500:
        return ServerError(f"{status} {body[:200]}", retry_after)
    return PermanentError(f"{status} {body[:200]}")


def backoff(attempt: int, rng: random.Random, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with full jitter: a uniform pick in [0, min(cap, base * 2^attempt)).

    Full jitter spreads retries out, so many clients that failed together do not all come
    back at the same moment.
    """
    return rng.uniform(0.0, min(cap, base * (2 ** max(attempt - 1, 0))))


def retry_delay(err: JobError, attempt: int, rng: random.Random, **kw) -> float:
    delay = backoff(attempt, rng, **kw)
    if err.retry_after is not None:
        delay = max(delay, err.retry_after)
    return delay

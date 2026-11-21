import random

from agentqueue.clock import FakeClock
from agentqueue.ratelimit import Limits
from agentqueue.store import Store


def make_store(tmp_path, name="q.sqlite"):
    return Store(tmp_path / name, FakeClock())


def limits(url="", **kw):
    base = dict(
        provider="fake",
        rpm=600,
        rpd=100_000,
        tpm=200_000,
        burst_requests=10,
        burst_tokens=4000,
        breaker_failures=3,
        breaker_cooldown_s=5.0,
        url=url,
        timeout_s=0.5,
    )
    base.update(kw)
    return {"fake": Limits(**base)}


def rng(seed=0):
    return random.Random(seed)

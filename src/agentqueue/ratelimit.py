"""Rate limits per provider: token buckets, provider pauses and a circuit breaker.

All of this state lives in the SQLite store, so it survives a worker crash and is shared by
every worker process on the machine.

A bucket with capacity `burst` and refill rate `(limit - burst) / window` can never let more
than `limit` units through in any window of `window` seconds: the most a window can see is the
bucket's level at its start (at most `burst`) plus what refills during it.
A bucket with capacity `limit` and rate `limit / window`, which is the common setup, can let
up to twice the limit through one window, so agentqueue does not use it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from agentqueue.errors import PermanentError

WINDOWS = {"rpm": 60.0, "rpd": 86_400.0, "tpm": 60.0}


@dataclass(frozen=True)
class Limits:
    provider: str
    rpm: int
    rpd: int
    tpm: int
    burst_requests: int
    burst_tokens: int
    breaker_failures: int = 5
    breaker_cooldown_s: float = 10.0
    url: str = ""
    timeout_s: float = 10.0

    def buckets(self) -> dict[str, tuple[float, float]]:
        """dim -> (capacity, refill per second)."""
        br = min(self.burst_requests, self.rpm, self.rpd)
        return {
            "rpm": (br, (self.rpm - br) / WINDOWS["rpm"]),
            "rpd": (br, (self.rpd - br) / WINDOWS["rpd"]),
            "tpm": (self.burst_tokens, (self.tpm - self.burst_tokens) / WINDOWS["tpm"]),
        }


def load_limits(path: str | Path) -> dict[str, Limits]:
    data = tomllib.loads(Path(path).read_text())
    return parse_limits(data)


def parse_limits(data: dict) -> dict[str, Limits]:
    out = {}
    for name, p in data.get("providers", {}).items():
        rpm, rpd, tpm = int(p["rpm"]), int(p["rpd"]), int(p["tpm"])
        lim = Limits(
            provider=name,
            rpm=rpm,
            rpd=rpd,
            tpm=tpm,
            burst_requests=int(p.get("burst_requests", max(1, rpm // 10))),
            burst_tokens=int(p.get("burst_tokens", max(1, tpm // 10))),
            breaker_failures=int(p.get("breaker_failures", 5)),
            breaker_cooldown_s=float(p.get("breaker_cooldown_s", 10.0)),
            url=str(p.get("url", "")),
            timeout_s=float(p.get("timeout_s", 10.0)),
        )
        if not (0 < lim.burst_requests <= rpm and 0 < lim.burst_tokens <= tpm):
            raise ValueError(f"{name}: bursts must be above 0 and at most the limit")
        out[name] = lim
    return out


class RateWait(Exception):
    """No budget now. Try again after `wait` seconds. This does not use an attempt."""

    def __init__(self, wait: float, reason: str):
        super().__init__(f"{reason}: wait {wait:.2f}s")
        self.wait = max(wait, 0.0)
        self.reason = reason


def _provider_row(c, provider: str):
    c.execute("INSERT OR IGNORE INTO providers (provider) VALUES (?)", (provider,))
    return c.execute("SELECT * FROM providers WHERE provider = ?", (provider,)).fetchone()


def acquire(store, lim: Limits, tokens: int, job_id=None, step_name=None) -> int:
    """Reserve one request and `tokens` tokens, or raise RateWait. Returns the call id.

    The reservation is written to the calls table in the same transaction that takes the
    tokens, so the request log and the buckets always agree, even after a crash.
    """
    if tokens > lim.burst_tokens:
        raise PermanentError(
            f"a call of {tokens} tokens can never fit burst_tokens={lim.burst_tokens}"
        )
    now = store.clock.now()
    with store.tx() as c:
        p = _provider_row(c, lim.provider)
        if p["paused_until"] > now:
            raise RateWait(p["paused_until"] - now, "provider asked us to wait")
        if p["breaker"] == "open":
            reopen = p["opened_at"] + lim.breaker_cooldown_s
            if now < reopen:
                raise RateWait(reopen - now, "circuit open")
            c.execute(
                "UPDATE providers SET breaker = 'half_open', opened_at = ? WHERE provider = ?",
                (now, lim.provider),
            )
        elif p["breaker"] == "half_open":
            # One trial call is in flight. If its worker died, a new trial starts after the
            # cooldown, so a crash cannot leave the breaker stuck half open.
            retrial = p["opened_at"] + lim.breaker_cooldown_s
            if now < retrial:
                raise RateWait(retrial - now, "circuit half open, trial in flight")
            c.execute("UPDATE providers SET opened_at = ? WHERE provider = ?", (now, lim.provider))
        need = {"rpm": 1.0, "rpd": 1.0, "tpm": float(tokens)}
        levels = {}
        wait = 0.0
        for dim, (cap, rate) in lim.buckets().items():
            row = c.execute(
                "SELECT level, updated FROM buckets WHERE provider = ? AND dim = ?",
                (lim.provider, dim),
            ).fetchone()
            level = cap if row is None else min(cap, row["level"] + (now - row["updated"]) * rate)
            levels[dim] = level
            if level < need[dim]:
                wait = max(wait, (need[dim] - level) / rate if rate > 0 else 3600.0)
        if wait > 0:
            raise RateWait(wait, "rate budget")
        for dim, level in levels.items():
            c.execute(
                "INSERT OR REPLACE INTO buckets (provider, dim, level, updated) VALUES (?, ?, ?, ?)",
                (lim.provider, dim, level - need[dim], now),
            )
        cur = c.execute(
            "INSERT INTO calls (provider, job_id, step_name, reserved_at, tokens) VALUES (?, ?, ?, ?, ?)",
            (lim.provider, job_id, step_name, now, tokens),
        )
        return cur.lastrowid


_DUR = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")


def parse_duration(value: str | None) -> float | None:
    """Parse '2', '1.5', '6m0s', '250ms' into seconds. None if it cannot."""
    if value is None:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    parts = _DUR.findall(value)
    if not parts or "".join(a + b for a, b in parts) != value:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(a) * scale[b] for a, b in parts)


def pause_from_headers(headers: dict[str, str]) -> float | None:
    """Seconds the provider wants us to wait, from retry-after or x-ratelimit-* headers."""
    h = {k.lower(): v for k, v in headers.items()}
    waits = []
    ra = parse_duration(h.get("retry-after"))
    if ra is not None:
        waits.append(ra)
    for kind in ("requests", "tokens"):
        if h.get(f"x-ratelimit-remaining-{kind}", "").strip() == "0":
            reset = parse_duration(h.get(f"x-ratelimit-reset-{kind}"))
            if reset is not None:
                waits.append(reset)
    return max(waits) if waits else None


def report(store, lim: Limits, call_id: int, outcome: str, headers=None) -> None:
    """Record how a call ended and update the provider pause and the breaker.

    outcome: 'ok', 'rate_limited', 'server_error', 'timeout', 'client_error'.
    A 429 is the provider working as designed, so it pauses the provider but does not count
    toward the breaker. Server errors and timeouts do.
    """
    now = store.clock.now()
    pause = pause_from_headers(headers or {})
    with store.tx() as c:
        p = _provider_row(c, lim.provider)
        c.execute(
            "UPDATE calls SET status = ?, finished_at = ? WHERE id = ?", (outcome, now, call_id)
        )
        if pause:
            c.execute(
                "UPDATE providers SET paused_until = MAX(paused_until, ?) WHERE provider = ?",
                (now + pause, lim.provider),
            )
        if outcome in ("ok", "client_error"):
            c.execute(
                "UPDATE providers SET breaker = 'closed', failures = 0 WHERE provider = ?",
                (lim.provider,),
            )
        elif outcome in ("server_error", "timeout"):
            failures = p["failures"] + 1
            opens = p["breaker"] == "half_open" or failures >= lim.breaker_failures
            c.execute(
                "UPDATE providers SET failures = ?, breaker = ?, opened_at = ? WHERE provider = ?",
                (
                    failures,
                    "open" if opens else p["breaker"],
                    now if opens else p["opened_at"],
                    lim.provider,
                ),
            )
        elif outcome == "rate_limited" and p["breaker"] == "half_open":
            c.execute("UPDATE providers SET breaker = 'closed' WHERE provider = ?", (lim.provider,))


def audit_calls(calls: list[tuple[float, int]], lim: Limits, eps: float = 1e-9) -> list[str]:
    """Check a request log against each token bucket and each limit window.

    calls: (reserved_at, tokens) pairs. Returns a list of violations, empty when clean.
    """
    calls = sorted(calls)
    problems = []
    for dim, (cap, rate) in lim.buckets().items():
        level, last = cap, None
        for t, tok in calls:
            if last is not None:
                level = min(cap, level + (t - last) * rate)
            last = t
            need = tok if dim == "tpm" else 1
            if level + eps < need:
                problems.append(f"{dim}: bucket short at t={t:.3f} ({level:.3f} < {need})")
            level -= need
    for dim, window in WINDOWS.items():
        limit = getattr(lim, dim)
        j, total = 0, 0
        for i, (t, tok) in enumerate(calls):
            total += tok if dim == "tpm" else 1
            while calls[j][0] < t - window:
                total -= calls[j][1] if dim == "tpm" else 1
                j += 1
            if total > limit:
                problems.append(f"{dim}: {total} > {limit} in the window ending t={t:.3f}")
                break
    return problems

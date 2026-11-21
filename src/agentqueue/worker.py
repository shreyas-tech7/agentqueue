"""The worker: claim a job, replay its finished steps, run the next step, commit.

A job is a plain function `fn(ctx, payload)`. Inside it, `ctx.step(name, fn)` runs `fn` once
and saves its result. After a crash the same job function runs again from the top, and every
`ctx.step` that already finished returns its saved result without calling `fn`. This only
works if the job function makes the same calls in the same order every time, so it must not
read the clock or a random number outside a step.
"""

from __future__ import annotations

import json
import os
import random
import signal
import urllib.error
import urllib.request
from collections.abc import Callable

from agentqueue import ratelimit, receiver
from agentqueue.errors import (
    JobError,
    PermanentError,
    ServerError,
    TransientError,
    classify_status,
)
from agentqueue.ratelimit import Limits, RateWait
from agentqueue.store import LostLease, Store, pid_alive

# A rate wait inside a step that already sent a model call is waited out in place, up to this
# many seconds, so the step does not throw away (and pay again for) the calls it already made.
MAX_INLINE_WAIT_S = 120.0
OUTBOX_MAX_ATTEMPTS = 20

REGISTRY: dict[str, Callable] = {}
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def job(kind: str):
    """Register a job function under a kind name."""

    def deco(fn):
        REGISTRY[kind] = fn
        return fn

    return deco


class Crasher:
    """Test hook. With AGENTQUEUE_CRASH_AT=k the process SIGKILLs itself at the k-th crash point.

    Crash points sit at every boundary that matters: after a claim, before and after a step
    function, after a step commit, before the finish, and on both sides of an outbox send.
    """

    def __init__(self, at: int | None = None):
        env = os.environ.get("AGENTQUEUE_CRASH_AT")
        self.at = at if at is not None else (int(env) if env else None)
        self.hits = 0

    def hit(self, point: str) -> None:
        if self.at is None:
            return
        self.hits += 1
        if self.hits == self.at:
            os.kill(os.getpid(), signal.SIGKILL)


class DuplicateStep(Exception):
    """Two ctx.step calls with the same name in one job: replay could not tell them apart."""


class Context:
    def __init__(self, store: Store, jb, limits: dict[str, Limits], crash: Crasher, lease_s: float):
        self.store = store
        self.job = jb
        self.limits = limits
        self.crash = crash
        self.lease_s = lease_s
        self.done = store.load_steps(jb.id)
        self.seen: set[str] = set()
        self._calls: list[int] | None = None
        self._step: str | None = None
        self._outbox: list[tuple[str, object]] | None = None
        self.replayed = 0
        self.ran = 0

    def step(self, name: str, fn: Callable[[], object]):
        if name in self.seen:
            raise DuplicateStep(name)
        self.seen.add(name)
        if name in self.done:
            self.replayed += 1
            return self.done[name]
        self._calls, self._outbox, self._step = [], [], name
        try:
            self.crash.hit("before_step")
            result = fn()
            json.dumps(result)  # results must be JSON, fail here and not at commit time
            self.crash.hit("after_step_fn")
            self.store.commit_step(
                self.job.id, self.job.lease_token, name, result, self._calls, self._outbox,
                self.lease_s,
            )  # fmt: skip
        finally:
            self._calls, self._outbox = None, None
        self.crash.hit("after_step_commit")
        self.done[name] = result
        self.ran += 1
        return result

    def emit(self, target: str, payload: object) -> None:
        """Record a side effect. It commits with the step result and the dispatcher sends it."""
        if self._outbox is None:
            raise RuntimeError("ctx.emit must run inside ctx.step")
        self._outbox.append((target, payload))

    def model(self, prompt: str, max_tokens: int = 64, provider: str = "fake") -> str:
        """Call an OpenAI-style chat endpoint inside the rate budget. Must run inside a step."""
        if self._calls is None:
            raise RuntimeError("ctx.model must run inside ctx.step")
        lim = self.limits[provider]
        if not lim.url.startswith(("http://", "https://")):
            raise PermanentError(f"provider {provider!r} needs an http or https url")
        tokens = len(prompt) // 4 + 1 + max_tokens
        call_id = self._acquire(lim, tokens)
        body = {
            "model": "fake-1",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            lim.url.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **_auth_header()},
            method="POST",
        )
        try:
            with _NO_PROXY.open(req, timeout=lim.timeout_s) as resp:
                raw = resp.read()
                headers = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            headers = dict(e.headers.items()) if e.headers else {}
            err = classify_status(
                e.code,
                e.read().decode(errors="replace"),
                ratelimit.parse_duration(headers.get("retry-after") or headers.get("Retry-After")),
            )
            ratelimit.report(self.store, lim, call_id, err.outcome, headers)
            raise err from None
        except (TimeoutError, OSError, urllib.error.URLError) as e:
            ratelimit.report(self.store, lim, call_id, "timeout")
            raise TransientError(f"call failed: {type(e).__name__}: {e}") from None
        try:
            text = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            # The provider answered, but not with a chat reply. Count it as a server fault.
            ratelimit.report(self.store, lim, call_id, "server_error", headers)
            raise ServerError(f"bad reply: {type(e).__name__}: {e}") from None
        ratelimit.report(self.store, lim, call_id, "ok", headers)
        self._calls.append(call_id)
        return text

    def _acquire(self, lim: Limits, tokens: int) -> int:
        """Take rate budget for one call, and renew the lease, since the call may take a while.

        With no call sent yet in this step, a RateWait ends the attempt and the job waits in the
        queue. Once a call went out, giving up would throw that call away and make the replay
        send it again, which can loop for ever when one step needs more than one burst. So the
        wait happens here, in place, up to MAX_INLINE_WAIT_S. A longer wait uses an attempt.
        """
        waited = 0.0
        while True:
            self.store.renew(self.job.id, self.job.lease_token, self.lease_s)
            try:
                return ratelimit.acquire(self.store, lim, tokens, self.job.id, self._step)
            except RateWait as w:
                if not self._calls:
                    raise
                if waited + w.wait > MAX_INLINE_WAIT_S:
                    w.charged = True
                    raise
                self.store.renew(self.job.id, self.job.lease_token, self.lease_s + w.wait)
                self.store.clock.sleep(w.wait + 0.001)
                waited += w.wait + 0.001


def _auth_header() -> dict[str, str]:
    key = os.environ.get("AGENTQUEUE_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def run_once(
    store: Store,
    limits: dict[str, Limits],
    rng: random.Random,
    crash: Crasher | None = None,
    lease_s: float = 30.0,
) -> bool:
    """Claim and run one job. Returns False when nothing was runnable."""
    crash = crash or Crasher(None)
    jb = store.claim(lease_s)
    if jb is None:
        return False
    crash.hit("after_claim")
    ctx = Context(store, jb, limits, crash, lease_s)
    try:
        _run(store, jb, ctx, rng, crash)
    except LostLease:
        pass  # another worker owns the job now. Its replay picks up our committed steps.
    return True


def _run(store: Store, jb, ctx: Context, rng: random.Random, crash: Crasher) -> None:
    now = store.clock.now
    try:
        fn = REGISTRY.get(jb.kind)
        if fn is None:
            raise PermanentError(f"no job function for kind {jb.kind!r}")
        result = fn(ctx, jb.payload)
        try:
            json.dumps(result)
        except (TypeError, ValueError) as e:
            raise PermanentError(f"the job result is not JSON: {e}") from None
        crash.hit("before_finish")
        store.finish(jb.id, jb.lease_token, "succeed", result=result)
    except LostLease:
        raise
    except RateWait as w:
        if getattr(w, "charged", False):  # a long wait after calls went out uses an attempt
            _fail(store, jb, TransientError(str(w), retry_after=w.wait), rng, now)
        else:
            store.finish(jb.id, jb.lease_token, "rate_wait", error=str(w), run_after=now() + w.wait)
    except JobError as e:
        _fail(store, jb, e, rng, now)
    except DuplicateStep as e:
        store.finish(jb.id, jb.lease_token, "fail", error=f"duplicate step name {e}")
    except Exception as e:  # a bug or a local error: retry, the attempt limit stops a loop
        _fail(store, jb, TransientError(f"{type(e).__name__}: {e}"), rng, now)


def _fail(store, jb, err: JobError, rng, now) -> None:
    from agentqueue.errors import retry_delay

    msg = f"{type(err).__name__}: {err}"
    if not err.retryable:
        store.finish(jb.id, jb.lease_token, "fail", error=msg)
    elif jb.attempts >= jb.max_attempts:
        store.finish(jb.id, jb.lease_token, "exhaust", error=msg)
    else:
        delay = retry_delay(err, jb.attempts, rng)
        store.finish(jb.id, jb.lease_token, "retry", error=msg, run_after=now() + delay)


def dispatch_once(
    store: Store, rng: random.Random, crash: Crasher | None = None, lease_s: float = 10.0
) -> bool:
    """Send one pending outbox entry. Returns False when nothing was due."""
    from agentqueue.errors import backoff

    crash = crash or Crasher(None)
    now = store.clock.now()
    with store.tx() as c:
        # Due entries, plus entries held by a dispatcher whose process is gone.
        rows = c.execute(
            "SELECT * FROM outbox WHERE state = 'pending' AND (lease_until <= ? OR owner_pid"
            " IS NOT NULL) ORDER BY lease_until <= ? DESC, id LIMIT 50",
            (now, now),
        ).fetchall()
        row = next(
            (r for r in rows if r["lease_until"] <= now or not pid_alive(r["owner_pid"])), None
        )
        if row is None:
            return False
        c.execute(
            "UPDATE outbox SET lease_until = ?, attempts = attempts + 1, owner_pid = ? WHERE id = ?",
            (now + lease_s, os.getpid(), row["id"]),
        )
    try:
        receiver.send(row["target"], row["dedupe_key"], json.loads(row["payload"]))
    except Exception as e:
        attempts = row["attempts"] + 1
        give_up = isinstance(e, PermanentError) or attempts >= OUTBOX_MAX_ATTEMPTS
        delay = backoff(attempts, rng, base=0.2, cap=10.0)
        with store.tx() as c:
            c.execute(
                "UPDATE outbox SET last_error = ?, lease_until = ?, owner_pid = NULL, state = ?"
                " WHERE id = ?",
                (
                    f"{type(e).__name__}: {e}",
                    store.clock.now() + delay,
                    "dead" if give_up else "pending",
                    row["id"],
                ),
            )
        return True
    crash.hit("dispatch_after_send")
    with store.tx() as c:
        c.execute(
            "UPDATE outbox SET state = 'sent', sent_at = ?, owner_pid = NULL WHERE id = ?",
            (store.clock.now(), row["id"]),
        )
    crash.hit("dispatch_after_mark")
    return True


def outbox_pending(store: Store) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM outbox WHERE state = 'pending'").fetchone()[0]


def outbox_dead(store: Store) -> list:
    """Outbox entries the dispatcher gave up on: a permanent error or too many attempts."""
    return store.conn.execute(
        "SELECT id, job_id, dedupe_key, target, attempts, last_error FROM outbox"
        " WHERE state = 'dead' ORDER BY id"
    ).fetchall()


def work(
    store: Store,
    limits: dict[str, Limits],
    *,
    dispatch: bool = True,
    until_empty: bool = False,
    poll_s: float = 0.02,
    max_loops: int | None = None,
    seed: int | None = None,
    lease_s: float = 30.0,
) -> int:
    """The worker loop. Returns the number of jobs it ran."""
    rng = random.Random(seed if seed is not None else os.getpid())
    crash = Crasher()
    ran = loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        did = run_once(store, limits, rng, crash, lease_s)
        ran += did
        sent = dispatch and dispatch_once(store, rng, crash)
        if did or sent:
            continue
        if until_empty and store.all_terminal() and (not dispatch or outbox_pending(store) == 0):
            break
        store.clock.sleep(poll_s)
    return ran

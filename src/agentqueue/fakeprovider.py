"""A fake model provider and side-effect receiver for tests and the benchmark.

It listens on 127.0.0.1 only. A seeded random generator picks a fault for each model request:
429 with a retry-after header, 500, a timeout (it answers after the client gave up), a slow
reply, or a dropped connection. The same seed gives the same fault sequence for the same
arrival order.

POST /v1/chat/completions  an OpenAI-style chat reply, made up from a hash of the prompt
POST /receive              a side-effect receiver that dedupes on the Idempotency-Key header
GET  /stats                counters as JSON
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Faults:
    rate_429: float = 0.0
    rate_500: float = 0.0
    rate_timeout: float = 0.0
    rate_slow: float = 0.0
    rate_drop: float = 0.0
    retry_after_s: float = 0.2
    timeout_s: float = 1.0  # how long a "timeout" reply sleeps. Set it above the client timeout
    slow_s: float = 0.15
    latency_s: float = 0.005

    def pick(self, r: float) -> str:
        edge = 0.0
        for name in ("429", "500", "timeout", "slow", "drop"):
            edge += getattr(self, f"rate_{name}")
            if r < edge:
                return name
        return "ok"


@dataclass
class Stats:
    requests: int = 0
    completions: int = 0  # replies the provider generated, delivered or not
    faults: dict[str, int] = field(default_factory=dict)
    deliveries: dict[str, int] = field(default_factory=dict)
    effects: dict[str, object] = field(default_factory=dict)
    naive_effects: int = 0  # deliveries that carried no dedupe key: each one is an effect
    logical: dict[str, int] = field(default_factory=dict)  # effect body -> keys that applied it

    def as_dict(self) -> dict:
        return {
            "requests": self.requests,
            "completions": self.completions,
            "faults": dict(self.faults),
            "deliveries": sum(self.deliveries.values()),
            "redeliveries": sum(n - 1 for n in self.deliveries.values()),
            "effects": len(self.effects),
            # The same effect body applied under two different keys: a real duplicate that the
            # key check cannot catch. Measured, not assumed.
            "duplicate_effects": sum(n - 1 for n in self.logical.values()),
            "naive_effects": self.naive_effects,
        }


def fake_reply(prompt: str, max_tokens: int) -> tuple[str, int]:
    h = hashlib.sha256(prompt.encode()).hexdigest()
    words = ["alpha", "beta", "gamma", "delta", "omega", "sigma", "kappa", "zeta"]
    n = min(max_tokens, 12)
    text = " ".join(words[int(h[i * 2 : i * 2 + 2], 16) % len(words)] for i in range(n))
    return text, len(prompt) // 4 + n


class FakeProvider:
    def __init__(self, faults: Faults | None = None, seed: int = 0, port: int = 0):
        self.faults = faults or Faults()
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.stats = Stats()
        self.naive_keys: dict[str, int] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # keep test output clean
                pass

            def _json(self, code: int, obj, headers=None):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                if self.path == "/stats":
                    with outer.lock:
                        self._json(200, outer.stats.as_dict())
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/v1/chat/completions":
                    outer._chat(self, data)
                elif self.path == "/receive":
                    outer._receive(self, data)
                else:
                    self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def _chat(self, h, data):
        with self.lock:
            self.stats.requests += 1
            fault = self.faults.pick(self.rng.random())
            self.stats.faults[fault] = self.stats.faults.get(fault, 0) + 1
        f = self.faults
        time.sleep(f.latency_s)
        if fault == "429":
            h._json(
                429,
                {"error": "rate limited"},
                {
                    "retry-after": str(f.retry_after_s),
                    "x-ratelimit-remaining-requests": "0",
                    "x-ratelimit-reset-requests": f"{f.retry_after_s}s",
                },
            )
            return
        if fault == "500":
            h._json(500, {"error": "server error"})
            return
        if fault == "drop":
            h.close_connection = True
            return
        if fault == "timeout":
            time.sleep(f.timeout_s)
        elif fault == "slow":
            time.sleep(f.slow_s)
        prompt = " ".join(m.get("content", "") for m in data.get("messages", []))
        text, used = fake_reply(prompt, int(data.get("max_tokens", 16)))
        with self.lock:
            self.stats.completions += 1
        h._json(
            200,
            {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"total_tokens": used},
            },
            {"x-ratelimit-remaining-requests": "1000"},
        )

    def _receive(self, h, data):
        key = h.headers.get("Idempotency-Key")
        with self.lock:
            if key:
                self.stats.deliveries[key] = self.stats.deliveries.get(key, 0) + 1
                if key not in self.stats.effects:  # a new key: the receiver applies the effect
                    self.stats.effects[key] = data
                    tag = json.dumps(data, sort_keys=True)
                    self.stats.logical[tag] = self.stats.logical.get(tag, 0) + 1
            else:
                self.stats.naive_effects += 1
                tag = json.dumps(data, sort_keys=True)
                self.naive_keys[tag] = self.naive_keys.get(tag, 0) + 1
        h._json(200, {"ok": True})

    def snapshot(self) -> dict:
        with self.lock:
            out = self.stats.as_dict()
            out["naive_duplicates"] = sum(n - 1 for n in self.naive_keys.values())
            return out


def main(argv=None) -> int:
    """Serve the fake provider on 127.0.0.1 until Ctrl-C. For trying agentqueue by hand."""
    import argparse

    p = argparse.ArgumentParser(prog="python -m agentqueue.fakeprovider")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--seed", type=int, default=0)
    for name in ("429", "500", "timeout", "slow", "drop"):
        p.add_argument(f"--rate-{name}", type=float, default=0.0)
    a = p.parse_args(argv)
    faults = Faults(
        **{f"rate_{n}": getattr(a, f"rate_{n}") for n in ("429", "500", "timeout", "slow", "drop")}
    )
    fp = FakeProvider(faults, seed=a.seed, port=a.port)
    print(f"fake provider on {fp.url}  (model: POST /v1/chat/completions, receiver: POST /receive)")
    try:
        fp.server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        fp.server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

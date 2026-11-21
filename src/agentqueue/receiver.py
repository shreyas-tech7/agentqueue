"""Receivers for side effects, and the client that sends outbox entries to them.

A receiver gets each entry with its dedupe key. It applies the effect only the first time it
sees a key. That check is what turns at-least-once delivery into a single effect.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from agentqueue.errors import PermanentError, TransientError, classify_status

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SqliteReceiver:
    """A receiver in its own SQLite file. Tests use it, so the kill tests need no network."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        c = self._conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS receipts (dedupe_key TEXT PRIMARY KEY, payload TEXT,"
            " deliveries INTEGER NOT NULL)"
        )
        c.close()

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def deliver(self, key: str, payload: object) -> bool:
        """Returns True if the effect was applied now, False if it was a duplicate."""
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT 1 FROM receipts WHERE dedupe_key = ?", (key,)).fetchone()
            if row:
                c.execute(
                    "UPDATE receipts SET deliveries = deliveries + 1 WHERE dedupe_key = ?", (key,)
                )
            else:
                c.execute("INSERT INTO receipts VALUES (?, ?, 1)", (key, json.dumps(payload)))
            c.execute("COMMIT")
            return row is None
        finally:
            c.close()

    def stats(self) -> dict[str, int]:
        c = self._conn()
        try:
            n, d = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(deliveries), 0) FROM receipts"
            ).fetchone()
        finally:
            c.close()
        return {"effects": n, "deliveries": d}


_RECEIVERS: dict[str, SqliteReceiver] = {}


def send(target: str, key: str, payload: object, timeout: float = 5.0) -> None:
    """Deliver one entry. Raises a JobError on failure. PermanentError means do not retry."""
    if target.startswith("sqlite:"):
        path = target[len("sqlite:") :]
        if path not in _RECEIVERS:
            _RECEIVERS[path] = SqliteReceiver(path)
        _RECEIVERS[path].deliver(key, payload)
        return
    if not target.startswith(("http://", "https://")):
        raise PermanentError(f"unsupported target {target!r}")
    req = urllib.request.Request(
        target,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
        method="POST",
    )
    try:
        with _NO_PROXY.open(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise classify_status(e.code, e.read().decode(errors="replace")) from None
    except (OSError, urllib.error.URLError) as e:
        raise TransientError(f"send failed: {e}") from None

"""The job store: one SQLite file in WAL mode.

Every write runs inside `BEGIN IMMEDIATE`, so two workers never claim the same job.
A claim hands out a lease token. Every later write for that job checks the token first,
so a worker that lost its lease (it was presumed dead) cannot write over the new owner.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agentqueue.clock import RealClock
from agentqueue.states import TERMINAL, next_state

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    idem_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    run_after REAL NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_until REAL,
    owner_pid INTEGER,
    last_error TEXT,
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_runnable ON jobs (state, run_after);
CREATE TABLE IF NOT EXISTS steps (
    job_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    result TEXT NOT NULL,
    call_ids TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    idx INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    target TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    owner_pid INTEGER,
    last_error TEXT,
    created_at REAL NOT NULL,
    sent_at REAL
);
CREATE TABLE IF NOT EXISTS dead_letters (
    job_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    error TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    job_id INTEGER,
    step_name TEXT,
    reserved_at REAL NOT NULL,
    tokens INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS calls_time ON calls (provider, reserved_at);
CREATE TABLE IF NOT EXISTS buckets (
    provider TEXT NOT NULL,
    dim TEXT NOT NULL,
    level REAL NOT NULL,
    updated REAL NOT NULL,
    PRIMARY KEY (provider, dim)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    provider TEXT PRIMARY KEY,
    paused_until REAL NOT NULL DEFAULT 0,
    breaker TEXT NOT NULL DEFAULT 'closed',
    failures INTEGER NOT NULL DEFAULT 0,
    opened_at REAL NOT NULL DEFAULT 0
);
"""


class IdempotencyConflict(ValueError):
    """The key exists already, with a different kind or payload."""


class LostLease(RuntimeError):
    """This worker no longer owns the job. Another worker took it over."""


@dataclass
class Job:
    id: int
    idem_key: str
    kind: str
    payload: object
    state: str
    attempts: int
    max_attempts: int
    lease_token: str | None


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Switching a new file to WAL can fail with "database is locked" when several processes
    # open it at the same moment, and that pragma does not wait on the busy timeout. Retry.
    for i in range(200):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == 199:
                raise
            time.sleep(0.01 + 0.001 * i)
    # NORMAL in WAL mode survives a process crash (SIGKILL). It can lose the last commits on
    # power loss. FULL would also cover power loss at the cost of an fsync per commit.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Store:
    def __init__(self, path: str | Path, clock=None):
        self.path = str(path)
        self.clock = clock or RealClock()
        self.conn = connect(path)
        self.conn.executescript(SCHEMA)
        # A random id for this store, made once. It prefixes every dedupe key, so a second
        # store (a reset database, another queue) never reuses a key a receiver has seen.
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO meta VALUES ('store_id', ?)", (secrets.token_hex(8),))
            self.store_id = c.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. BEGIN IMMEDIATE takes the write lock up front."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    # ---- submit -------------------------------------------------------------------------
    def submit(
        self, kind: str, payload: object, key: str, max_attempts: int = 8
    ) -> tuple[int, bool]:
        """Create a job, or return the job that already has this idempotency key.

        Returns (job_id, created). The same key with a different kind or payload raises
        IdempotencyConflict, because silently returning the old job would hide a bug.
        """
        body = json.dumps(payload, sort_keys=True)
        now = self.clock.now()
        with self.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO jobs (idem_key, kind, payload, state, max_attempts,"
                " created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (key, kind, body, max_attempts, now, now),
            )
            created = cur.rowcount == 1
            row = c.execute(
                "SELECT id, kind, payload FROM jobs WHERE idem_key = ?", (key,)
            ).fetchone()
        if not created and (row["kind"] != kind or row["payload"] != body):
            raise IdempotencyConflict(f"key {key!r} belongs to job {row['id']} with other input")
        return row["id"], created

    # ---- claim and recovery -------------------------------------------------------------
    def _move(self, c, job_id: int, state: str, event: str, **fields) -> str:
        new = next_state(state, event)
        sets = ["state = ?", "updated_at = ?"]
        args: list = [new, self.clock.now()]
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            args.append(v)
        if new in TERMINAL:
            sets.append("finished_at = ?")
            args.append(self.clock.now())
        if new != "running":
            sets += ["lease_token = NULL", "lease_until = NULL", "owner_pid = NULL"]
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*args, job_id))
        if new in ("dead", "failed"):
            c.execute(
                "INSERT OR REPLACE INTO dead_letters VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    new,
                    fields.get("last_error") or "unknown",
                    fields.get("attempts", 0),
                    self.clock.now(),
                ),
            )
        return new

    def recover_stale(self, c) -> int:
        """Return running jobs whose worker died to the queue, or to dead if out of attempts."""
        now = self.clock.now()
        rows = c.execute(
            "SELECT id, attempts, max_attempts, lease_until, owner_pid FROM jobs"
            " WHERE state = 'running'"
        ).fetchall()
        moved = 0
        for r in rows:
            if (
                r["lease_until"] is not None
                and r["lease_until"] > now
                and pid_alive(r["owner_pid"])
            ):
                continue
            if r["attempts"] >= r["max_attempts"]:
                self._move(
                    c,
                    r["id"],
                    "running",
                    "recover_exhaust",
                    last_error="worker died and no attempts are left",
                    attempts=r["attempts"],
                )
            else:
                self._move(c, r["id"], "running", "recover", run_after=now)
            moved += 1
        return moved

    def claim(self, lease_s: float = 30.0) -> Job | None:
        now = self.clock.now()
        with self.tx() as c:
            self.recover_stale(c)
            row = c.execute(
                "SELECT * FROM jobs WHERE state = 'queued' OR (state = 'waiting' AND run_after <= ?)"
                " ORDER BY run_after, id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(8)
            self._move(
                c,
                row["id"],
                row["state"],
                "claim",
                attempts=row["attempts"] + 1,
                lease_token=token,
                lease_until=now + lease_s,
                owner_pid=os.getpid(),
            )
        return Job(
            row["id"],
            row["idem_key"],
            row["kind"],
            json.loads(row["payload"]),
            "running",
            row["attempts"] + 1,
            row["max_attempts"],
            token,
        )

    def _check_lease(self, c, job_id: int, token: str):
        row = c.execute(
            "SELECT state, lease_token, attempts FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row["state"] != "running" or row["lease_token"] != token:
            raise LostLease(f"job {job_id} is no longer owned by this worker")
        return row

    def renew(self, job_id: int, token: str, lease_s: float = 30.0) -> None:
        with self.tx() as c:
            self._check_lease(c, job_id, token)
            c.execute(
                "UPDATE jobs SET lease_until = ? WHERE id = ?", (self.clock.now() + lease_s, job_id)
            )

    # ---- steps --------------------------------------------------------------------------
    def load_steps(self, job_id: int) -> dict[str, object]:
        rows = self.conn.execute(
            "SELECT name, result FROM steps WHERE job_id = ?", (job_id,)
        ).fetchall()
        return {r["name"]: json.loads(r["result"]) for r in rows}

    def commit_step(
        self,
        job_id: int,
        token: str,
        name: str,
        result: object,
        call_ids: list[int],
        outbox: list[tuple[str, object]],
        lease_s: float = 30.0,
    ) -> None:
        """Save a step result and its outbox entries in one transaction. Renews the lease."""
        now = self.clock.now()
        with self.tx() as c:
            self._check_lease(c, job_id, token)
            c.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (now + lease_s, job_id))
            c.execute(
                "INSERT INTO steps (job_id, name, result, call_ids, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, name, json.dumps(result), json.dumps(call_ids), now),
            )
            for idx, (target, body) in enumerate(outbox):
                c.execute(
                    "INSERT INTO outbox (job_id, step_name, idx, dedupe_key, target, payload,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        name,
                        idx,
                        f"{self.store_id}:{job_id}:{name}:{idx}",
                        target,
                        json.dumps(body),
                        now,
                    ),
                )

    # ---- finishing ----------------------------------------------------------------------
    def finish(
        self,
        job_id: int,
        token: str,
        event: str,
        result: object = None,
        error: str | None = None,
        run_after: float | None = None,
    ) -> str:
        with self.tx() as c:
            row = self._check_lease(c, job_id, token)
            fields: dict = {"attempts": row["attempts"]}
            if result is not None:
                fields["result"] = json.dumps(result)
            if error is not None:
                fields["last_error"] = error
            if run_after is not None:
                fields["run_after"] = run_after
            if event == "rate_wait":
                # Waiting for budget is not a failed attempt.
                fields["attempts"] = row["attempts"] - 1
            if event == "succeed":
                c.execute("DELETE FROM dead_letters WHERE job_id = ?", (job_id,))
            return self._move(c, job_id, "running", event, **fields)

    def requeue(self, job_id: int) -> bool:
        """Move a failed or dead job back to the queue with fresh attempts.

        Finished steps stay in the store, so the job resumes after its last finished step.
        """
        with self.tx() as c:
            row = c.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["state"] not in ("failed", "dead"):
                return False
            self._move(
                c, job_id, row["state"], "requeue", attempts=0, run_after=0, finished_at=None
            )
            c.execute("DELETE FROM dead_letters WHERE job_id = ?", (job_id,))
        return True

    def requeue_outbox(self) -> int:
        """Move every dead outbox entry back to pending. Returns how many moved."""
        with self.tx() as c:
            return c.execute(
                "UPDATE outbox SET state = 'pending', attempts = 0, lease_until = 0,"
                " owner_pid = NULL WHERE state = 'dead'"
            ).rowcount

    # ---- reads --------------------------------------------------------------------------
    def job(self, job_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) n FROM jobs GROUP BY state").fetchall()
        return {r["state"]: r["n"] for r in rows}

    def dead_letters(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT d.*, j.kind, j.idem_key FROM dead_letters d JOIN jobs j ON j.id = d.job_id"
            " ORDER BY d.created_at"
        ).fetchall()

    def all_terminal(self) -> bool:
        n = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state NOT IN ('succeeded', 'failed', 'dead')"
        ).fetchone()[0]
        return n == 0

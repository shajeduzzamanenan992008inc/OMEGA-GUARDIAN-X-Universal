"""Durable, bounded job queue with retries and a dead-letter queue (ENT-07/PRF-03).

Design constraints:

* **Bounded** — ``max_pending`` caps queued *and* leased jobs. Enqueueing past
  the cap raises :class:`QueueFull`, which callers must surface as overload
  (HTTP 429 / CLI exit) instead of growing memory.
* **Durable** — jobs live in SQLite (WAL, ``BEGIN IMMEDIATE`` for claim
  atomicity). A worker crash leaves a lease that :meth:`JobQueue.reap_expired`
  returns to the queue.
* **At-least-once** — claim increments ``attempts``; after ``max_attempts`` the
  job moves to the dead-letter state and is never retried automatically.
* **Idempotent submission** — an optional ``dedupe_key`` (unique) returns the
  existing job instead of creating a duplicate.
* **No secrets in records** — payloads are size-limited JSON, errors are
  truncated and credentials in URLs are redacted before storage.

The storage layer is SQLite today. A PostgreSQL backend (ENT-02) can implement
the same method surface once a driver exists in the environment; nothing in this
module assumes SQLite-specific SQL beyond ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .enterprise.database import (
    DatabaseError,
    PostgresDatabase,
    database_url_from_env,
    is_postgres_url,
)

MAX_PAYLOAD_BYTES = 32 * 1024
MAX_ERROR_CHARS = 500
DEFAULT_MAX_PENDING = 1_000
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 60
DEFAULT_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 900.0
_KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_CREDENTIAL_URL = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+(?::[^/\s@]*)?@", re.IGNORECASE)

STATE_PENDING = "pending"
STATE_LEASED = "leased"
STATE_DONE = "done"
STATE_DEAD = "dead"


class QueueError(RuntimeError):
    """Base class for queue failures."""


class QueueFull(QueueError):
    """Raised when the bounded queue is at capacity (overload, not an error)."""


class JobNotFound(QueueError):
    """Raised when a job id does not exist."""


class LeaseLost(QueueError):
    """Raised when a worker no longer owns the lease it is completing."""


def _redact_error(value: str) -> str:
    text = _CREDENTIAL_URL.sub(r"\g<scheme>***@", str(value))
    return text[:MAX_ERROR_CHARS]


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    kind: str
    payload: Mapping[str, Any]
    state: str
    attempts: int
    available_at: float
    lease_until: float | None
    worker: str | None
    last_error: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "state": self.state,
            "attempts": self.attempts,
            "available_at": self.available_at,
            "lease_until": self.lease_until,
            "worker": self.worker,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    lease_until REAL,
    worker TEXT,
    last_error TEXT,
    dedupe_key TEXT UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (state, available_at);
CREATE INDEX IF NOT EXISTS jobs_lease ON jobs (state, lease_until);
"""


class JobQueue:
    def __init__(
        self,
        db_path: Path | str,
        *,
        max_pending: int = DEFAULT_MAX_PENDING,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_pending < 1:
            raise QueueError("max_pending must be at least 1.")
        if not 1 <= max_attempts <= 100:
            raise QueueError("max_attempts must be between 1 and 100.")
        if not 1 <= lease_seconds <= 3600:
            raise QueueError("lease_seconds must be between 1 and 3600.")
        if not 0 < backoff_seconds <= MAX_BACKOFF_SECONDS:
            raise QueueError("backoff_seconds is out of range.")
        self.path = Path(db_path)
        self.max_pending = max_pending
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.backoff_seconds = backoff_seconds
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise QueueError("Refusing a symlinked queue database.")
        self._initialize()

    # ------------------------------------------------------------- plumbing --
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.executescript(_SCHEMA)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}
        return Job(
            id=str(row["id"]),
            kind=str(row["kind"]),
            payload=payload if isinstance(payload, dict) else {},
            state=str(row["state"]),
            attempts=int(row["attempts"]),
            available_at=float(row["available_at"]),
            lease_until=float(row["lease_until"]) if row["lease_until"] is not None else None,
            worker=row["worker"],
            last_error=row["last_error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    # -------------------------------------------------------------- producer --
    def enqueue(
        self,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        *,
        dedupe_key: str | None = None,
        delay_seconds: float = 0.0,
    ) -> tuple[Job, bool]:
        """Enqueue one job; returns ``(job, created)``.

        ``created`` is ``False`` when an identical ``dedupe_key`` already exists.
        """

        if not _KIND.match(str(kind)):
            raise QueueError("Job kind must be a lowercase slug.")
        body: dict[str, Any] = dict(payload or {})
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise QueueError(f"Job payload exceeds {MAX_PAYLOAD_BYTES} bytes.")
        if dedupe_key is not None and not 1 <= len(str(dedupe_key)) <= 200:
            raise QueueError("dedupe_key must be 1..200 characters.")
        if not 0 <= delay_seconds <= MAX_BACKOFF_SECONDS:
            raise QueueError("delay_seconds is out of range.")

        moment = self._clock()
        identifier = uuid.uuid4().hex
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if dedupe_key is not None:
                    existing = connection.execute(
                        "SELECT * FROM jobs WHERE dedupe_key = ?", (str(dedupe_key),)
                    ).fetchone()
                    if existing is not None:
                        connection.execute("COMMIT")
                        job = self._row(existing)
                        assert job is not None
                        return job, False
                (pending,) = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state IN (?, ?)",
                    (STATE_PENDING, STATE_LEASED),
                ).fetchone()
                if pending >= self.max_pending:
                    raise QueueFull(
                        f"Queue is at capacity ({pending}/{self.max_pending}); reject or retry later."
                    )
                connection.execute(
                    "INSERT INTO jobs (id, kind, payload, state, attempts, available_at,"
                    " dedupe_key, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (
                        identifier,
                        str(kind),
                        encoded,
                        STATE_PENDING,
                        moment + delay_seconds,
                        str(dedupe_key) if dedupe_key is not None else None,
                        moment,
                        moment,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (identifier,)
                ).fetchone()
                connection.execute("COMMIT")
            except QueueFull:
                connection.execute("ROLLBACK")
                raise
            except Exception:
                connection.execute("ROLLBACK")
                raise
        job = self._row(row)
        assert job is not None
        return job, True

    # ---------------------------------------------------------------- worker --
    def claim(self, worker: str, *, lease_seconds: int | None = None) -> Job | None:
        if not worker:
            raise QueueError("A worker identity is required.")
        lease = self.lease_seconds if lease_seconds is None else lease_seconds
        if not 1 <= lease <= 3600:
            raise QueueError("Lease must be between 1 and 3600 seconds.")
        moment = self._clock()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE state = ? AND available_at <= ?"
                    " ORDER BY available_at, created_at LIMIT 1",
                    (STATE_PENDING, moment),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                attempts = int(row["attempts"]) + 1
                connection.execute(
                    "UPDATE jobs SET state = ?, attempts = ?, worker = ?, lease_until = ?,"
                    " updated_at = ? WHERE id = ?",
                    (STATE_LEASED, attempts, worker, moment + lease, moment, row["id"]),
                )
                claimed = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self._row(claimed)

    def heartbeat(self, job_id: str, worker: str, *,
                  extend_seconds: int | None = None) -> Job:
        """Extend a live lease; raise when the worker no longer owns it."""

        lease = self.lease_seconds if extend_seconds is None else extend_seconds
        if not 1 <= lease <= 3600:
            raise QueueError("Lease extension must be between 1 and 3600 seconds.")
        moment = self._clock()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE jobs SET lease_until=?, updated_at=? WHERE id=? AND state=?"
                " AND worker=?",
                (moment + lease, moment, job_id, STATE_LEASED, worker),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT 1 FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                connection.execute("ROLLBACK")
                if row is None:
                    raise JobNotFound(f"Unknown job: {job_id}")
                raise LeaseLost("This worker no longer owns the job lease.")
            connection.execute("COMMIT")
        job = self.get(job_id)
        assert job is not None
        return job

    def complete(self, job_id: str, worker: str) -> Job:
        moment = self._clock()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise JobNotFound(f"Unknown job: {job_id}")
            if row["state"] != STATE_LEASED or row["worker"] != worker:
                connection.execute("ROLLBACK")
                raise LeaseLost("This worker no longer owns the job lease.")
            connection.execute(
                "UPDATE jobs SET state = ?, lease_until = NULL, worker = ?, updated_at = ?"
                " WHERE id = ?",
                (STATE_DONE, worker, moment, job_id),
            )
            connection.execute("COMMIT")
        finished = self.get(job_id)
        assert finished is not None
        return finished

    def fail(self, job_id: str, worker: str, error: str) -> Job:
        moment = self._clock()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise JobNotFound(f"Unknown job: {job_id}")
            if row["state"] != STATE_LEASED or row["worker"] != worker:
                connection.execute("ROLLBACK")
                raise LeaseLost("This worker no longer owns the job lease.")
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                connection.execute(
                    "UPDATE jobs SET state = ?, last_error = ?, lease_until = NULL,"
                    " worker = ?, updated_at = ? WHERE id = ?",
                    (STATE_DEAD, _redact_error(error), worker, moment, job_id),
                )
            else:
                delay = min(
                    MAX_BACKOFF_SECONDS,
                    self.backoff_seconds * (2 ** max(0, attempts - 1)),
                )
                connection.execute(
                    "UPDATE jobs SET state = ?, last_error = ?, lease_until = NULL,"
                    " worker = NULL, available_at = ?, updated_at = ? WHERE id = ?",
                    (STATE_PENDING, _redact_error(error), moment + delay, moment, job_id),
                )
            connection.execute("COMMIT")
        failed = self.get(job_id)
        assert failed is not None
        return failed

    def reap_expired(self) -> int:
        """Return jobs whose lease expired; dead-letter them past max attempts."""

        moment = self._clock()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                "SELECT id, attempts FROM jobs WHERE state = ? AND lease_until IS NOT NULL"
                " AND lease_until <= ?",
                (STATE_LEASED, moment),
            ).fetchall()
            reaped = 0
            for row in expired:
                if int(row["attempts"]) >= self.max_attempts:
                    connection.execute(
                        "UPDATE jobs SET state = ?, lease_until = NULL, worker = NULL,"
                        " last_error = ?, updated_at = ? WHERE id = ?",
                        (
                            STATE_DEAD,
                            "lease expired; retry budget exhausted",
                            moment,
                            row["id"],
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE jobs SET state = ?, lease_until = NULL, worker = NULL,"
                        " available_at = ?, updated_at = ? WHERE id = ?",
                        (STATE_PENDING, moment, moment, row["id"]),
                    )
                reaped += 1
            connection.execute("COMMIT")
        return reaped

    # ------------------------------------------------------------ inspection --
    def get(self, job_id: str) -> Job | None:
        with self._session() as connection:
            return self._row(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            )

    def list_jobs(self, *, state: str | None = None, limit: int = 100) -> list[Job]:
        if not 1 <= limit <= 1000:
            raise QueueError("limit must be between 1 and 1000.")
        query = "SELECT * FROM jobs"
        params: tuple[Any, ...] = ()
        if state is not None:
            query += " WHERE state = ?"
            params = (state,)
        query += " ORDER BY created_at, id LIMIT ?"
        params += (limit,)
        with self._session() as connection:
            rows = connection.execute(query, params).fetchall()
        return [job for job in (self._row(row) for row in rows) if job is not None]

    def stats(self) -> dict[str, Any]:
        moment = self._clock()
        with self._session() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM jobs GROUP BY state"
            ).fetchall()
            oldest = connection.execute(
                "SELECT MIN(created_at) FROM jobs WHERE state = ?", (STATE_PENDING,)
            ).fetchone()
        counts = {STATE_PENDING: 0, STATE_LEASED: 0, STATE_DONE: 0, STATE_DEAD: 0}
        for row in rows:
            counts[str(row["state"])] = int(row["total"])
        oldest_pending = oldest[0] if oldest and oldest[0] is not None else None
        return {
            "counts": counts,
            "pending": counts[STATE_PENDING],
            "leased": counts[STATE_LEASED],
            "dead": counts[STATE_DEAD],
            "capacity": self.max_pending,
            "utilization": round((counts[STATE_PENDING] + counts[STATE_LEASED]) / self.max_pending, 4),
            "saturated": counts[STATE_PENDING] + counts[STATE_LEASED] >= self.max_pending,
            "oldest_pending_age": (moment - oldest_pending) if oldest_pending else None,
        }

    # ------------------------------------------------------------- recovery --
    def retry_dead(self, job_id: str, *, approved: bool) -> Job:
        if not approved:
            raise QueueError("Requeueing a dead-letter job requires explicit approval.")
        moment = self._clock()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise JobNotFound(f"Unknown job: {job_id}")
            if row["state"] != STATE_DEAD:
                connection.execute("ROLLBACK")
                raise QueueError("Only a dead-letter job can be requeued.")
            connection.execute(
                "UPDATE jobs SET state = ?, attempts = 0, available_at = ?, last_error = NULL,"
                " updated_at = ? WHERE id = ?",
                (STATE_PENDING, moment, moment, job_id),
            )
            connection.execute("COMMIT")
        job = self.get(job_id)
        assert job is not None
        return job

    def purge_done(self, *, older_than_seconds: float = 0.0) -> int:
        if older_than_seconds < 0:
            raise QueueError("older_than_seconds cannot be negative.")
        cutoff = self._clock() - older_than_seconds
        with self._session() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE state = ? AND updated_at <= ?", (STATE_DONE, cutoff)
            )
        return int(cursor.rowcount or 0)


class PostgresJobQueue:
    """PostgreSQL implementation of the durable queue (ENT-02/ENT-07).

    Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` inside one statement, so
    multiple worker processes can claim different jobs without a global lock.
    Enqueue is serialized with a transaction-scoped advisory lock to keep the
    capacity and dedupe checks exact.
    """

    backend = "postgresql"

    def __init__(
        self,
        database_url: str,
        *,
        max_pending: int = DEFAULT_MAX_PENDING,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_pending < 1:
            raise QueueError("max_pending must be at least 1.")
        if not 1 <= max_attempts <= 100:
            raise QueueError("max_attempts must be between 1 and 100.")
        if not 1 <= lease_seconds <= 3600:
            raise QueueError("lease_seconds must be between 1 and 3600.")
        if not 0 < backoff_seconds <= MAX_BACKOFF_SECONDS:
            raise QueueError("backoff_seconds is out of range.")
        self.max_pending = max_pending
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.backoff_seconds = backoff_seconds
        self._clock = clock
        self._db = PostgresDatabase(database_url)
        self._initialize()

    @property
    def database_display(self) -> str:
        return self._db.display

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at DOUBLE PRECISION NOT NULL,
            lease_until DOUBLE PRECISION,
            worker TEXT,
            last_error TEXT,
            dedupe_key TEXT UNIQUE,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL
        );
        CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (state, available_at);
        CREATE INDEX IF NOT EXISTS jobs_lease ON jobs (state, lease_until);
        """
        with self._db.connection() as connection:
            connection.executescript(schema)

    @staticmethod
    def _row(row: Any) -> Job | None:
        return JobQueue._row(row)

    def enqueue(
        self,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        *,
        dedupe_key: str | None = None,
        delay_seconds: float = 0.0,
    ) -> tuple[Job, bool]:
        if not _KIND.match(str(kind)):
            raise QueueError("Job kind must be a lowercase slug.")
        body: dict[str, Any] = dict(payload or {})
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise QueueError(f"Job payload exceeds {MAX_PAYLOAD_BYTES} bytes.")
        if dedupe_key is not None and not 1 <= len(str(dedupe_key)) <= 200:
            raise QueueError("dedupe_key must be 1..200 characters.")
        if not 0 <= delay_seconds <= MAX_BACKOFF_SECONDS:
            raise QueueError("delay_seconds is out of range.")

        moment = self._clock()
        identifier = uuid.uuid4().hex
        with self._db.connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(?))", ("ogx.jobs.enqueue",)
            )
            if dedupe_key is not None:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE dedupe_key=?", (str(dedupe_key),)
                ).fetchone()
                if existing is not None:
                    job = self._row(existing)
                    assert job is not None
                    return job, False
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM jobs WHERE state IN (?, ?)",
                (STATE_PENDING, STATE_LEASED),
            ).fetchone()
            if row is not None and int(row["total"]) >= self.max_pending:
                raise QueueFull(
                    f"Queue is at capacity ({int(row['total'])}/{self.max_pending});"
                    " reject or retry later."
                )
            created = connection.execute(
                "INSERT INTO jobs (id, kind, payload, state, attempts, available_at,"
                " dedupe_key, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?) RETURNING *",
                (
                    identifier,
                    str(kind),
                    encoded,
                    STATE_PENDING,
                    moment + delay_seconds,
                    str(dedupe_key) if dedupe_key is not None else None,
                    moment,
                    moment,
                ),
            ).fetchone()
        job = self._row(created)
        assert job is not None
        return job, True

    def claim(self, worker: str, *, lease_seconds: int | None = None) -> Job | None:
        if not worker:
            raise QueueError("A worker identity is required.")
        lease = self.lease_seconds if lease_seconds is None else lease_seconds
        if not 1 <= lease <= 3600:
            raise QueueError("Lease must be between 1 and 3600 seconds.")
        moment = self._clock()
        with self._db.connection() as connection:
            row = connection.execute(
                "WITH candidate AS ("
                " SELECT id FROM jobs WHERE state=? AND available_at <= ?"
                " ORDER BY available_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                ") UPDATE jobs SET state=?, attempts=attempts+1, worker=?,"
                " lease_until=?, updated_at=? WHERE id=(SELECT id FROM candidate)"
                " RETURNING *",
                (STATE_PENDING, moment, STATE_LEASED, worker, moment + lease, moment),
            ).fetchone()
        return self._row(row)

    def heartbeat(self, job_id: str, worker: str, *,
                  extend_seconds: int | None = None) -> Job:
        lease = self.lease_seconds if extend_seconds is None else extend_seconds
        if not 1 <= lease <= 3600:
            raise QueueError("Lease extension must be between 1 and 3600 seconds.")
        moment = self._clock()
        with self._db.connection() as connection:
            row = connection.execute(
                "UPDATE jobs SET lease_until=?, updated_at=? WHERE id=? AND state=?"
                " AND worker=? RETURNING *",
                (moment + lease, moment, job_id, STATE_LEASED, worker),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 AS present FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                if exists is None:
                    raise JobNotFound(f"Unknown job: {job_id}")
                raise LeaseLost("This worker no longer owns the job lease.")
        job = self._row(row)
        assert job is not None
        return job

    def complete(self, job_id: str, worker: str) -> Job:
        moment = self._clock()
        with self._db.connection() as connection:
            row = connection.execute(
                "UPDATE jobs SET state=?, lease_until=NULL, worker=?, updated_at=?"
                " WHERE id=? AND state=? AND worker=? RETURNING *",
                (STATE_DONE, worker, moment, job_id, STATE_LEASED, worker),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 AS present FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                if exists is None:
                    raise JobNotFound(f"Unknown job: {job_id}")
                raise LeaseLost("This worker no longer owns the job lease.")
        job = self._row(row)
        assert job is not None
        return job

    def fail(self, job_id: str, worker: str, error: str) -> Job:
        moment = self._clock()
        with self._db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=? FOR UPDATE", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            if row["state"] != STATE_LEASED or row["worker"] != worker:
                raise LeaseLost("This worker no longer owns the job lease.")
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                updated = connection.execute(
                    "UPDATE jobs SET state=?, last_error=?, lease_until=NULL,"
                    " worker=?, updated_at=? WHERE id=? RETURNING *",
                    (STATE_DEAD, _redact_error(error), worker, moment, job_id),
                ).fetchone()
            else:
                delay = min(
                    MAX_BACKOFF_SECONDS,
                    self.backoff_seconds * (2 ** max(0, attempts - 1)),
                )
                updated = connection.execute(
                    "UPDATE jobs SET state=?, last_error=?, lease_until=NULL,"
                    " worker=NULL, available_at=?, updated_at=? WHERE id=? RETURNING *",
                    (STATE_PENDING, _redact_error(error), moment + delay, moment, job_id),
                ).fetchone()
        job = self._row(updated)
        assert job is not None
        return job

    def reap_expired(self) -> int:
        moment = self._clock()
        with self._db.connection() as connection:
            dead = connection.execute(
                "UPDATE jobs SET state=?, lease_until=NULL, worker=NULL,"
                " last_error=?, updated_at=? WHERE state=? AND lease_until IS NOT NULL"
                " AND lease_until <= ? AND attempts >= ?",
                (
                    STATE_DEAD,
                    "lease expired; retry budget exhausted",
                    moment,
                    STATE_LEASED,
                    moment,
                    self.max_attempts,
                ),
            )
            retry = connection.execute(
                "UPDATE jobs SET state=?, lease_until=NULL, worker=NULL,"
                " available_at=?, updated_at=? WHERE state=? AND lease_until IS NOT NULL"
                " AND lease_until <= ? AND attempts < ?",
                (STATE_PENDING, moment, moment, STATE_LEASED, moment, self.max_attempts),
            )
            return int(dead.rowcount or 0) + int(retry.rowcount or 0)

    def get(self, job_id: str) -> Job | None:
        with self._db.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row)

    def list_jobs(self, *, state: str | None = None, limit: int = 100) -> list[Job]:
        if not 1 <= limit <= 1000:
            raise QueueError("limit must be between 1 and 1000.")
        query = "SELECT * FROM jobs"
        params: list[Any] = []
        if state is not None:
            query += " WHERE state=?"
            params.append(state)
        query += " ORDER BY created_at, id LIMIT ?"
        params.append(limit)
        with self._db.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [job for job in (self._row(row) for row in rows) if job is not None]

    def stats(self) -> dict[str, Any]:
        moment = self._clock()
        with self._db.connection() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM jobs GROUP BY state"
            ).fetchall()
            oldest = connection.execute(
                "SELECT MIN(created_at) AS oldest FROM jobs WHERE state=?",
                (STATE_PENDING,),
            ).fetchone()
        counts = {STATE_PENDING: 0, STATE_LEASED: 0, STATE_DONE: 0, STATE_DEAD: 0}
        for row in rows:
            counts[str(row["state"])] = int(row["total"])
        oldest_pending = oldest["oldest"] if oldest else None
        return {
            "counts": counts,
            "pending": counts[STATE_PENDING],
            "leased": counts[STATE_LEASED],
            "dead": counts[STATE_DEAD],
            "capacity": self.max_pending,
            "utilization": round(
                (counts[STATE_PENDING] + counts[STATE_LEASED]) / self.max_pending, 4
            ),
            "saturated": counts[STATE_PENDING] + counts[STATE_LEASED] >= self.max_pending,
            "oldest_pending_age": (moment - float(oldest_pending)) if oldest_pending else None,
            "backend": "postgresql",
        }

    def retry_dead(self, job_id: str, *, approved: bool) -> Job:
        if not approved:
            raise QueueError("Requeueing a dead-letter job requires explicit approval.")
        moment = self._clock()
        with self._db.connection() as connection:
            row = connection.execute(
                "UPDATE jobs SET state=?, attempts=0, available_at=?, last_error=NULL,"
                " updated_at=? WHERE id=? AND state=? RETURNING *",
                (STATE_PENDING, moment, moment, job_id, STATE_DEAD),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 AS present FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                if exists is None:
                    raise JobNotFound(f"Unknown job: {job_id}")
                raise QueueError("Only a dead-letter job can be requeued.")
        job = self._row(row)
        assert job is not None
        return job

    def purge_done(self, *, older_than_seconds: float = 0.0) -> int:
        if older_than_seconds < 0:
            raise QueueError("older_than_seconds cannot be negative.")
        cutoff = self._clock() - older_than_seconds
        with self._db.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE state=? AND updated_at <= ?",
                (STATE_DONE, cutoff),
            )
            return int(cursor.rowcount or 0)

    def close(self) -> None:
        self._db.close()


def open_job_queue(
    db_path: Path | str | None = None,
    *,
    database_url: str | None = None,
    **kwargs: Any,
) -> "JobQueue | PostgresJobQueue":
    """Return the configured queue backend; PostgreSQL is opt-in via URL."""

    url = database_url or database_url_from_env()
    if is_postgres_url(url):
        assert url is not None
        return PostgresJobQueue(url, **kwargs)
    return JobQueue(db_path or default_queue_path(), **kwargs)


def run_worker(
    queue: JobQueue,
    handler: Callable[[Job], Any],
    *,
    worker_id: str,
    max_jobs: int = 100,
    stop_when_empty: bool = True,
    idle_sleep: float = 0.2,
) -> dict[str, Any]:
    """Process up to ``max_jobs``; handler exceptions go through ``fail``."""

    processed = 0
    succeeded = 0
    failed = 0
    while processed < max_jobs:
        queue.reap_expired()
        job = queue.claim(worker_id)
        if job is None:
            if stop_when_empty:
                break
            time.sleep(idle_sleep)
            continue
        processed += 1
        try:
            handler(job)
        except Exception as exc:  # handler failures are data, not crashes
            queue.fail(job.id, worker_id, f"{type(exc).__name__}: {exc}")
            failed += 1
        else:
            queue.complete(job.id, worker_id)
            succeeded += 1
    return {
        "worker": worker_id,
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "stats": queue.stats(),
    }


def default_queue_path(state_home: Path | str | None = None) -> Path:
    home = Path(state_home) if state_home is not None else Path(os.environ.get("OGX_HOME", Path.home() / ".ogx"))
    return home / "jobs" / "queue.sqlite3"

"""Portable database access for the enterprise store and durable job queue (ENT-02).

The base installation stays stdlib-only. A PostgreSQL deployment is enabled by
setting ``OGX_DATABASE_URL`` to a ``postgresql://`` URL and installing one of the
optional drivers (``psycopg`` 3 preferred, ``psycopg2`` accepted). SQLite remains
the default and the only backend used by the existing test suite unless a URL is
configured explicitly.

Design boundaries:

* This module is a *thin* DB-API adapter, not an ORM. It translates the
  question-mark placeholders used by the existing SQL to the driver's parameter
  style, exposes rows that support both ``row["column"]`` and ``row[0]``, and
  keeps transactions explicit.
* PostgreSQL connections are pooled and bounded. A connection that reports a
  broken state is discarded instead of returned to the pool.
* Migrations are versioned in ``schema_migrations`` and serialized with a
  PostgreSQL advisory lock, so two API processes starting at once cannot apply
  the same migration twice.
* No URL, password or DSN is ever logged or included in an exception message.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
DEFAULT_POOL_SIZE = 4
DEFAULT_CONNECT_TIMEOUT = 5

_PLACEHOLDER = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")
_PRAGMA = re.compile(r"^\s*PRAGMA\b.*?;\s*$", re.IGNORECASE | re.MULTILINE)
_MIGRATION_LOCK = "ogx.enterprise.schema_migrations"


class DatabaseError(RuntimeError):
    """A stable, non-secret-bearing database failure."""


class DatabaseUnavailable(DatabaseError):
    """The configured backend cannot be reached."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_database_url(value: str) -> str:
    """Return a URL safe for logs: credentials and query values are removed."""

    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return "<invalid database url>"
    if not parsed.scheme or not parsed.hostname:
        return "<database url>"
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    database = parsed.path.lstrip("/") or "<default>"
    return f"{parsed.scheme}://{host}/{database}"


class Row(Mapping[str, Any]):
    """A result row that behaves like both ``sqlite3.Row`` and a mapping."""

    __slots__ = ("_columns", "_values", "_index")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(str(column) for column in columns)
        self._values = tuple(values)
        self._index = {name: position for position, name in enumerate(self._columns)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Row({dict(self)!r})"

    def keys(self) -> list[str]:  # type: ignore[override]
        return list(self._columns)

    def values(self) -> list[Any]:  # type: ignore[override]
        return list(self._values)

    def items(self):  # type: ignore[override]
        return zip(self._columns, self._values)

    def get(self, key: str, default: Any = None) -> Any:
        position = self._index.get(str(key))
        return default if position is None else self._values[position]


class _EmptyCursor:
    """A no-op cursor for transaction-control statements handled by the adapter."""

    rowcount = 0
    lastrowid = None
    description = None

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def __iter__(self):
        return iter(())


class Cursor:
    def __init__(self, cursor: Any, *, columns: Sequence[str] | None = None) -> None:
        self._cursor = cursor
        self._columns = list(columns or [])

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", 0) or 0)

    @property
    def lastrowid(self) -> Any:
        return getattr(self._cursor, "lastrowid", None)

    @property
    def description(self) -> Any:
        return getattr(self._cursor, "description", None)

    def _row(self, value: Any) -> Row | None:
        if value is None:
            return None
        if isinstance(value, Row):
            return value
        if isinstance(value, Mapping):
            return Row(self._columns or list(value.keys()), list(value.values()))
        if isinstance(value, (tuple, list)):
            return Row(self._columns, value)
        return Row(self._columns, [value])

    def fetchone(self) -> Row | None:
        return self._row(self._cursor.fetchone())

    def fetchall(self) -> list[Row]:
        return [row for row in (self._row(value) for value in self._cursor.fetchall()) if row]

    def __iter__(self) -> Iterator[Row]:
        for value in self._cursor:
            row = self._row(value)
            if row is not None:
                yield row


class PostgresConnection:
    """A pooled PostgreSQL connection with sqlite-like ``execute`` ergonomics."""

    def __init__(self, connection: Any, pool: "PostgresDatabase") -> None:
        self._connection = connection
        self._pool = pool
        self._closed = False
        self._broken = False

    @property
    def backend(self) -> str:
        return "postgresql"

    def _cursor(self, *, dictionary: bool = True) -> Any:
        if dictionary:
            try:
                extras = import_module("psycopg2.extras")
                return self._connection.cursor(cursor_factory=extras.RealDictCursor)
            except ImportError:  # psycopg 3
                try:
                    rows = import_module("psycopg.rows")
                    return self._connection.cursor(row_factory=rows.dict_row)
                except ImportError:
                    return self._connection.cursor()
        return self._connection.cursor()

    @staticmethod
    def _translate(sql: str) -> str:
        # The project SQL uses no literal question marks; this conservative
        # substitution also refuses to touch quoted strings.
        return _PLACEHOLDER.sub("%s", sql)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        statement = str(sql).strip()
        upper = statement.upper()
        if upper.startswith("BEGIN IMMEDIATE"):
            # PostgreSQL starts a transaction implicitly. The outer context
            # manager owns commit/rollback, so this is intentionally a no-op.
            return _EmptyCursor()
        if upper in {"BEGIN", "COMMIT", "ROLLBACK"}:
            if upper == "BEGIN":
                return _EmptyCursor()
            if upper == "COMMIT":
                self._connection.commit()
            else:
                self._connection.rollback()
            return _EmptyCursor()
        cursor = self._cursor(dictionary="SELECT" in upper or "RETURNING" in upper)
        try:
            cursor.execute(self._translate(statement), tuple(params))
        except Exception as exc:  # pragma: no cover - driver-specific
            self._broken = True
            raise DatabaseError(f"PostgreSQL statement failed: {type(exc).__name__}") from None
        columns = [column.name for column in (cursor.description or [])]
        return Cursor(cursor, columns=columns)

    def executescript(self, sql: str) -> None:
        script = _PRAGMA.sub("", str(sql))
        cursor = self._connection.cursor()
        try:
            cursor.execute(script)
        except Exception as exc:  # pragma: no cover - driver-specific
            self._broken = True
            raise DatabaseError(f"PostgreSQL schema apply failed: {type(exc).__name__}") from None
        finally:
            cursor.close()

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                try:
                    self._connection.rollback()
                except Exception:
                    self._broken = True
        finally:
            self.close()
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pool.release(self._connection, broken=self._broken)

    @property
    def closed(self) -> bool:
        return self._closed or bool(getattr(self._connection, "closed", 0))


class PostgresDatabase:
    """Bounded, thread-safe PostgreSQL connection pool and migration runner."""

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        if not str(url).lower().startswith(("postgresql://", "postgres://")):
            raise DatabaseError("OGX_DATABASE_URL must be a postgresql:// URL.")
        self._url = str(url)
        self._pool_size = max(1, min(int(pool_size), 32))
        self._connect_timeout = max(1, min(int(connect_timeout), 60))
        self._available: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=self._pool_size)
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False
        self._driver = self._load_driver()
        self._display = redact_database_url(self._url)

    @staticmethod
    def _load_driver():
        try:
            psycopg = import_module("psycopg")
            return ("psycopg", psycopg)
        except ImportError:
            pass
        try:
            psycopg2 = import_module("psycopg2")
            return ("psycopg2", psycopg2)
        except ImportError:
            raise DatabaseError(
                "A PostgreSQL driver is required: install the optional "
                "'omega-guardian-x[postgres]' extra (psycopg 3 or psycopg2)."
            ) from None

    @property
    def display(self) -> str:
        return self._display

    def _new_connection(self) -> Any:
        driver = self._driver[1]
        try:
            if self._driver[0] == "psycopg":
                connection = driver.connect(self._url, connect_timeout=self._connect_timeout)
                connection.autocommit = False
            else:
                connection = driver.connect(self._url, connect_timeout=self._connect_timeout)
                connection.autocommit = False
            return connection
        except Exception as exc:  # pragma: no cover - driver-specific
            raise DatabaseUnavailable(
                f"PostgreSQL is unavailable ({type(exc).__name__}); "
                "check OGX_DATABASE_URL and the server."
            ) from None

    def _acquire(self) -> Any:
        if self._closed:
            raise DatabaseError("The PostgreSQL pool is closed.")
        try:
            return self._available.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self._pool_size:
                self._created += 1
                create = True
            else:
                create = False
        if create:
            try:
                return self._new_connection()
            except Exception:
                with self._lock:
                    self._created -= 1
                raise
        try:
            return self._available.get(timeout=self._connect_timeout)
        except queue.Empty:
            raise DatabaseUnavailable("PostgreSQL connection pool is exhausted.") from None

    def release(self, connection: Any, *, broken: bool = False) -> None:
        if self._closed:
            try:
                connection.close()
            except Exception:
                pass
            return
        healthy = not broken and not int(getattr(connection, "closed", 0))
        if not healthy:
            try:
                connection.close()
            except Exception:
                pass
            with self._lock:
                self._created = max(0, self._created - 1)
            return
        try:
            self._available.put_nowait(connection)
        except queue.Full:
            try:
                connection.close()
            except Exception:
                pass
            with self._lock:
                self._created = max(0, self._created - 1)

    @contextmanager
    def connection(self) -> Iterator[PostgresConnection]:
        raw = self._acquire()
        adapter = PostgresConnection(raw, self)
        try:
            yield adapter
        except Exception:
            try:
                adapter.rollback()
            except Exception:
                adapter._broken = True
            adapter.close()
            raise
        else:
            adapter.commit()
            adapter.close()

    def connect(self) -> PostgresConnection:
        """Return a connection whose ``close`` releases it to the pool."""

        return PostgresConnection(self._acquire(), self)

    def ping(self) -> dict[str, Any]:
        started = time.monotonic()
        with self.connection() as connection:
            row = connection.execute("SELECT 1 AS ok").fetchone()
        return {
            "backend": "postgresql",
            "ok": bool(row and row["ok"] == 1),
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "pool_size": self._pool_size,
            "display": self._display,
        }

    def migrate(self, schema_sql: str) -> int:
        """Apply the initial schema and record its version exactly once."""

        with self.connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (_MIGRATION_LOCK,)
            )
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version INTEGER PRIMARY KEY,"
                " applied_at TEXT NOT NULL,"
                " description TEXT NOT NULL);"
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            current = int(row["version"]) if row else 0
            if current < SCHEMA_VERSION:
                connection.executescript(schema_sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, description)"
                    " VALUES(%s, %s, %s) ON CONFLICT(version) DO NOTHING",
                    (SCHEMA_VERSION, _now(), "initial enterprise schema"),
                )
            return max(current, SCHEMA_VERSION)

    def schema_version(self) -> int:
        with self.connection() as connection:
            try:
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
                ).fetchone()
            except DatabaseError:
                return 0
        return int(row["version"]) if row else 0

    def table_names(self) -> set[str]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT tablename AS name FROM pg_tables WHERE schemaname='public'"
            ).fetchall()
        return {str(row["name"]) for row in rows}

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                connection = self._available.get_nowait()
            except queue.Empty:
                break
            try:
                connection.close()
            except Exception:
                pass
        with self._lock:
            self._created = 0


def database_url_from_env() -> str | None:
    value = os.environ.get("OGX_DATABASE_URL", "").strip()
    return value or None


def is_postgres_url(value: str | None) -> bool:
    return bool(value) and str(value).lower().startswith(("postgresql://", "postgres://"))


def describe_backend(store: Any) -> dict[str, Any]:
    """Small helper used by the CLI and readiness probes."""

    backend = getattr(store, "backend", "sqlite")
    if backend == "postgresql":
        database = getattr(store, "_postgres", None)
        return {"backend": "postgresql", "display": getattr(database, "display", "postgresql")}
    return {"backend": "sqlite", "display": str(getattr(store, "db_path", ""))}


__all__ = [
    "Cursor",
    "DatabaseError",
    "DatabaseUnavailable",
    "PostgresConnection",
    "PostgresDatabase",
    "Row",
    "SCHEMA_VERSION",
    "database_url_from_env",
    "describe_backend",
    "is_postgres_url",
    "redact_database_url",
]

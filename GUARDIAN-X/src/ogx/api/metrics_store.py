"""Pluggable metrics storage for the REST API (ENT-09, persistent observability).

The :class:`Metrics` registry in :mod:`ogx.api.observability` keeps a hot,
in-memory view for low-latency reads. This module adds a storage boundary so an
operator can choose where (and whether) aggregates are persisted:

* :class:`InMemoryMetricsStorage` -- the default; no persistence, no I/O.
* :class:`SqliteMetricsStorage` -- bounded, durable, concurrency-safe SQLite
  backend with configurable retention and safe cleanup.

Design guarantees:

* Writes are best-effort: a storage failure is recorded in the backend's health
  state but never raised into the request path.
* Cardinality is bounded: at most ``max_routes`` distinct route labels are kept;
  overflow is folded into a single ``{overflow}`` bucket.
* Retention is enforced both on write (opportunistic cleanup) and on demand
  via :meth:`cleanup`.
* Health is reported independently of application readiness.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


_DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600
_DEFAULT_MAX_ROWS = 10_000
_DEFAULT_MAX_ROUTES = 256
_DEFAULT_CLEANUP_INTERVAL = 300.0


class MetricsStorage(Protocol):
    """Storage boundary for metric aggregates."""

    def record_request(self, label: str, status: int, duration: float) -> None: ...

    def record_error(self, code: str) -> None: ...

    def load_snapshot(self) -> dict[str, Any]: ...

    def cleanup(self) -> int: ...

    @property
    def healthy(self) -> bool: ...

    def close(self) -> None: ...


@dataclass
class _StorageHealth:
    healthy: bool = True
    last_error: str = ""
    last_error_at: float = 0.0


class InMemoryMetricsStorage:
    """Non-persistent storage used as the default backend."""

    def __init__(self) -> None:
        self._health = _StorageHealth()
        self._lock = threading.Lock()
        self._routes: dict[str, dict[str, Any]] = {}
        self._status: dict[int, int] = {}
        self._errors: dict[str, int] = {}
        self._total = 0

    def record_request(self, label: str, status: int, duration: float) -> None:
        with self._lock:
            entry = self._routes.setdefault(
                label, {"count": 0, "errors_5xx": 0, "total_seconds": 0.0, "max_seconds": 0.0}
            )
            entry["count"] += 1
            if status >= 500:
                entry["errors_5xx"] += 1
            entry["total_seconds"] += duration
            if duration > entry["max_seconds"]:
                entry["max_seconds"] = duration
            self._status[status] = self._status.get(status, 0) + 1
            self._total += 1

    def record_error(self, code: str) -> None:
        label = str(code or "unknown")[:48]
        with self._lock:
            self._errors[label] = self._errors.get(label, 0) + 1

    def load_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "routes": {k: dict(v) for k, v in self._routes.items()},
                "status_codes": dict(self._status),
                "error_codes": dict(self._errors),
                "requests_total": self._total,
            }

    def cleanup(self) -> int:
        return 0

    @property
    def healthy(self) -> bool:
        return self._health.healthy

    def close(self) -> None:
        return None


class SqliteMetricsStorage:
    """Bounded, durable, concurrency-safe SQLite metrics backend."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS metric_routes (
        label TEXT PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        total_seconds REAL NOT NULL DEFAULT 0.0,
        max_seconds REAL NOT NULL DEFAULT 0.0,
        last_seen REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS metric_status (
        status INTEGER PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS metric_errors (
        code TEXT PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS metric_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(
        self,
        db_path: "str | Path",
        *,
        retention_seconds: int = _DEFAULT_RETENTION_SECONDS,
        max_rows: int = _DEFAULT_MAX_ROWS,
        max_routes: int = _DEFAULT_MAX_ROUTES,
        cleanup_interval: float = _DEFAULT_CLEANUP_INTERVAL,
    ) -> None:
        self.db_path = Path(db_path)
        self.retention_seconds = max(60, int(retention_seconds))
        self.max_rows = max(1, int(max_rows))
        self.max_routes = max(1, int(max_routes))
        self.cleanup_interval = max(10.0, float(cleanup_interval))
        self._lock = threading.RLock()
        self._health = _StorageHealth()
        self._last_cleanup = 0.0
        self._total = 0
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            if self.db_path.is_symlink():
                raise ValueError("Metrics database path must not be a symlink.")
            with self._connect() as conn:
                conn.executescript(self._SCHEMA)
                conn.commit()
                row = conn.execute(
                    "SELECT value FROM metric_meta WHERE key='requests_total'"
                ).fetchone()
                if row is not None:
                    try:
                        self._total = int(row[0])
                    except (TypeError, ValueError):
                        self._total = 0
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_unhealthy(exc)

    # ----------------------------- internals
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def _mark_unhealthy(self, exc: BaseException) -> None:
        self._health.healthy = False
        self._health.last_error = str(exc)[:200]
        self._health.last_error_at = time.time()

    def _mark_healthy(self) -> None:
        self._health.healthy = True
        self._health.last_error = ""
        self._health.last_error_at = 0.0

    def _maybe_cleanup(self, conn: sqlite3.Connection) -> None:
        now = time.time()
        # Always enforce the hard cardinality/row bound, even when the
        # time-gated retention pass is skipped.
        try:
            count = conn.execute("SELECT COUNT(*) FROM metric_routes").fetchone()[0]
            if count > self.max_rows:
                excess = count - self.max_rows
                conn.execute(
                    "DELETE FROM metric_routes WHERE label IN ("
                    "SELECT label FROM metric_routes ORDER BY last_seen ASC LIMIT ?)",
                    (excess,),
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_unhealthy(exc)
            return
        if now - self._last_cleanup < self.cleanup_interval:
            return
        self._last_cleanup = now
        try:
            cutoff = now - self.retention_seconds
            conn.execute("DELETE FROM metric_routes WHERE last_seen < ?", (cutoff,))
            conn.commit()
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_unhealthy(exc)

    def _bounded_label(self, label: str, conn: sqlite3.Connection) -> str:
        label = str(label or "/")[:120]
        try:
            exists = conn.execute(
                "SELECT 1 FROM metric_routes WHERE label=?", (label,)
            ).fetchone()
            if exists:
                return label
            count = conn.execute("SELECT COUNT(*) FROM metric_routes").fetchone()[0]
            if count >= self.max_routes:
                return "{overflow}"
        except Exception:  # pragma: no cover - defensive
            return label
        return label

    # ----------------------------- public API
    def record_request(self, label: str, status: int, duration: float) -> None:
        try:
            with self._lock:
                with self._connect() as conn:
                    now = time.time()
                    bounded = self._bounded_label(label, conn)
                    conn.execute(
                        "INSERT INTO metric_routes(label,count,errors,total_seconds,max_seconds,last_seen) "
                        "VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(label) DO UPDATE SET "
                        "count=count+1, "
                        "errors=errors+CASE WHEN ? >= 500 THEN 1 ELSE 0 END, "
                        "total_seconds=total_seconds+?, "
                        "max_seconds=MAX(max_seconds,?), "
                        "last_seen=?",
                        (bounded, 1, 1 if status >= 500 else 0, float(duration),
                         float(duration), now, status, float(duration), float(duration), now),
                    )
                    conn.execute(
                        "INSERT INTO metric_status(status,count) VALUES(?,1) "
                        "ON CONFLICT(status) DO UPDATE SET count=count+1",
                        (int(status),),
                    )
                    self._total += 1
                    conn.execute(
                        "INSERT INTO metric_meta(key,value) VALUES('requests_total',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=?",
                        (str(self._total), str(self._total)),
                    )
                    self._maybe_cleanup(conn)
                    conn.commit()
            self._mark_healthy()
        except Exception as exc:
            self._mark_unhealthy(exc)

    def record_error(self, code: str) -> None:
        label = str(code or "unknown")[:48]
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO metric_errors(code,count) VALUES(?,1) "
                        "ON CONFLICT(code) DO UPDATE SET count=count+1",
                        (label,),
                    )
                    conn.commit()
            self._mark_healthy()
        except Exception as exc:
            self._mark_unhealthy(exc)

    def load_snapshot(self) -> dict[str, Any]:
        try:
            with self._lock:
                with self._connect() as conn:
                    routes = {}
                    for row in conn.execute(
                        "SELECT label,count,errors,total_seconds,max_seconds FROM metric_routes"
                    ):
                        routes[str(row["label"])] = {
                            "count": int(row["count"]),
                            "errors_5xx": int(row["errors"]),
                            "total_seconds": round(float(row["total_seconds"]), 6),
                            "max_seconds": round(float(row["max_seconds"]), 6),
                            "mean_seconds": (
                                round(float(row["total_seconds"]) / int(row["count"]), 6)
                                if row["count"] else 0.0
                            ),
                        }
                    status = {
                        int(row["status"]): int(row["count"])
                        for row in conn.execute("SELECT status,count FROM metric_status")
                    }
                    errors = {
                        str(row["code"]): int(row["count"])
                        for row in conn.execute("SELECT code,count FROM metric_errors")
                    }
                    total_row = conn.execute(
                        "SELECT value FROM metric_meta WHERE key='requests_total'"
                    ).fetchone()
                    total = int(total_row[0]) if total_row else self._total
            self._mark_healthy()
            return {
                "routes": routes,
                "status_codes": status,
                "error_codes": errors,
                "requests_total": total,
            }
        except Exception as exc:
            self._mark_unhealthy(exc)
            return {"routes": {}, "status_codes": {}, "error_codes": {}, "requests_total": 0}

    def cleanup(self) -> int:
        removed = 0
        try:
            with self._lock:
                with self._connect() as conn:
                    before = conn.execute("SELECT COUNT(*) FROM metric_routes").fetchone()[0]
                    self._maybe_cleanup_force(conn)
                    after = conn.execute("SELECT COUNT(*) FROM metric_routes").fetchone()[0]
                    removed = max(0, before - after)
            self._mark_healthy()
        except Exception as exc:
            self._mark_unhealthy(exc)
        return removed

    def _maybe_cleanup_force(self, conn: sqlite3.Connection) -> None:
        cutoff = time.time() - self.retention_seconds
        conn.execute("DELETE FROM metric_routes WHERE last_seen < ?", (cutoff,))
        count = conn.execute("SELECT COUNT(*) FROM metric_routes").fetchone()[0]
        if count > self.max_rows:
            excess = count - self.max_rows
            conn.execute(
                "DELETE FROM metric_routes WHERE label IN ("
                "SELECT label FROM metric_routes ORDER BY last_seen ASC LIMIT ?)",
                (excess,),
            )
        conn.commit()

    @property
    def healthy(self) -> bool:
        return self._health.healthy

    def close(self) -> None:
        return None


def create_storage(kind: str = "memory", **kwargs: Any) -> MetricsStorage:
    """Factory for a metrics storage backend."""

    normalized = (kind or "memory").strip().lower()
    if normalized in {"memory", "inmemory", "in-memory"}:
        return InMemoryMetricsStorage()
    if normalized in {"sqlite", "persistent"}:
        db_path = kwargs.pop("db_path", None)
        if db_path is None:
            raise ValueError("sqlite metrics storage requires db_path")
        return SqliteMetricsStorage(db_path, **kwargs)
    raise ValueError(f"Unknown metrics storage kind: {kind}")


__all__ = [
    "MetricsStorage",
    "InMemoryMetricsStorage",
    "SqliteMetricsStorage",
    "create_storage",
]

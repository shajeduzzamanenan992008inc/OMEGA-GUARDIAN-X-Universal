"""Local error tracking: fingerprinting, de-duplication, redaction, bounded retention (SQLite, stdlib only)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from ogx.kernel.config import state_dir
from ogx.kernel.redact import redact_extended, redact_mapping

_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
  fingerprint TEXT PRIMARY KEY, exc_type TEXT NOT NULL, message TEXT NOT NULL, level TEXT NOT NULL,
  first_seen REAL NOT NULL, last_seen REAL NOT NULL, count INTEGER NOT NULL, last_stack TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL, ts REAL NOT NULL, context TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_fp ON events(fingerprint, id);
"""
_NUMBERS = re.compile(r"\d+")


def default_db_path() -> Path:
    return state_dir() / "errors.db"


def fingerprint_of(exc: BaseException) -> str:
    kind = f"{type(exc).__module__}.{type(exc).__qualname__}"
    frames = traceback.extract_tb(exc.__traceback__)[-3:]
    where = "|".join(f"{os.path.basename(f.filename)}:{f.name}" for f in frames)
    seed = f"{kind}|{where}" if where else f"{kind}|{_NUMBERS.sub('#', str(exc))[:200]}"
    return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16]


class ErrorTracker:
    def __init__(self, path: str | Path | None = None, *, max_events: int = 2000, clock: Callable[[], float] = time.time):
        self.path = Path(path).expanduser() if path else default_db_path()
        self.max_events, self._clock, self._lock = max_events, clock, threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, contextlib.closing(self._connect()) as db:
            db.executescript(_SCHEMA)
            db.commit()
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def capture(self, exc: BaseException, *, context: dict | None = None, level: str = "error") -> str:
        """Record an exception. Never raises: tracking must not take the host app down."""
        try:
            fingerprint = fingerprint_of(exc)
            message = redact_extended(str(exc))[:500]
            stack = redact_extended("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))[-8000:]
            ctx = json.dumps(redact_mapping(context or {}), ensure_ascii=False, default=str)[:4000]
            now = self._clock()
            with self._lock, contextlib.closing(self._connect()) as db:
                row = db.execute("SELECT count FROM groups WHERE fingerprint=?", (fingerprint,)).fetchone()
                if row:
                    db.execute("UPDATE groups SET count=count+1,last_seen=?,message=?,last_stack=? WHERE fingerprint=?",
                               (now, message, stack, fingerprint))
                else:
                    db.execute("INSERT INTO groups VALUES(?,?,?,?,?,?,?,?)",
                               (fingerprint, f"{type(exc).__module__}.{type(exc).__qualname__}", message, level, now, now, 1, stack))
                db.execute("INSERT INTO events(fingerprint,ts,context) VALUES(?,?,?)", (fingerprint, now, ctx))
                db.execute("DELETE FROM events WHERE id <= (SELECT COALESCE(MAX(id),0) FROM events) - ?", (self.max_events,))
                db.commit()
            return fingerprint
        except Exception:
            return ""

    def groups(self, limit: int = 50) -> list[dict]:
        with self._lock, contextlib.closing(self._connect()) as db:
            rows = db.execute("SELECT fingerprint,exc_type,message,level,first_seen,last_seen,count FROM groups "
                              "ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
        keys = ("fingerprint", "type", "message", "level", "first_seen", "last_seen", "count")
        return [dict(zip(keys, row)) for row in rows]

    def group(self, fingerprint: str, events: int = 5) -> dict | None:
        with self._lock, contextlib.closing(self._connect()) as db:
            row = db.execute("SELECT fingerprint,exc_type,message,level,first_seen,last_seen,count,last_stack FROM groups "
                             "WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not row:
                return None
            recent = db.execute("SELECT ts,context FROM events WHERE fingerprint=? ORDER BY id DESC LIMIT ?",
                                (fingerprint, events)).fetchall()
        keys = ("fingerprint", "type", "message", "level", "first_seen", "last_seen", "count", "stack")
        item = dict(zip(keys, row))
        item["recent"] = [{"ts": ts, "context": json.loads(ctx or "{}")} for ts, ctx in recent]
        return item

    def clear(self) -> int:
        with self._lock, contextlib.closing(self._connect()) as db:
            count = db.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
            db.executescript("DELETE FROM events; DELETE FROM groups;")
            db.commit()
        return count


def install_excepthooks(tracker: ErrorTracker) -> Callable[[], None]:
    """Opt-in: record uncaught exceptions (main thread and threads), then defer to the previous hooks."""
    previous_sys, previous_thread = sys.excepthook, threading.excepthook

    def on_exception(exc_type, exc, tb):
        if exc is not None:
            tracker.capture(exc, context={"source": "sys.excepthook"}, level="critical")
        previous_sys(exc_type, exc, tb)

    def on_thread_exception(args):
        if args.exc_value is not None:
            tracker.capture(args.exc_value, context={"source": "threading.excepthook", "thread": getattr(args.thread, "name", "")}, level="critical")
        if previous_thread:
            previous_thread(args)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception

    def uninstall() -> None:
        sys.excepthook = previous_sys
        if previous_thread:
            threading.excepthook = previous_thread
    return uninstall


class ErrorTrackingMiddleware:
    """WSGI: record exceptions raised by the app or while streaming its response, then re-raise unchanged."""

    def __init__(self, app, tracker: ErrorTracker | None = None):
        self.app, self.tracker = app, tracker or ErrorTracker()

    def _ctx(self, environ: dict) -> dict:
        return {"method": environ.get("REQUEST_METHOD"), "path": environ.get("PATH_INFO"),
                "request_id": environ.get("omega.request_id"), "source": "wsgi"}

    def __call__(self, environ, start_response):
        try:
            result = self.app(environ, start_response)
        except Exception as exc:
            self.tracker.capture(exc, context=self._ctx(environ))
            raise
        return self._iterate(result, environ)

    def _iterate(self, result, environ):
        try:
            yield from result
        except Exception as exc:
            self.tracker.capture(exc, context=self._ctx(environ))
            raise
        finally:
            close = getattr(result, "close", None)
            if close:
                close()


class ErrorTrackingASGI:
    def __init__(self, app, tracker: ErrorTracker | None = None):
        self.app, self.tracker = app, tracker or ErrorTracker()

    async def __call__(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            self.tracker.capture(exc, context={"method": scope.get("method"), "path": scope.get("path"),
                                               "request_id": (scope.get("state") or {}).get("omega_request_id"), "source": "asgi"})
            raise

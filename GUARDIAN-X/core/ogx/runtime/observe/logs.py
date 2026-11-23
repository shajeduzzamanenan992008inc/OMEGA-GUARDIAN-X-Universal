"""Centralised, structured, redacted logging for any host app (stdlib `logging` only).

Zero-conflict by design: only the ``omega`` logger namespace is configured, the root logger is never touched
unless you pass ``capture_root=True``. Output is JSON lines in the per-host state directory (outside the project).
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import logging.handlers
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ogx.kernel.config import state_dir
from ogx.kernel.redact import redact_extended, redact_mapping

ROOT_NAME = "omega"
LEVEL_ENV, FILE_ENV = "OMEGA_LOG_LEVEL", "OMEGA_LOG_FILE"
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("omega_log_context", default={})
_MARK = "_omega_managed"


def default_log_path() -> Path:
    return state_dir() / "logs" / "omega.jsonl"


def get_logger(name: str = "app") -> logging.Logger:
    return logging.getLogger(f"{ROOT_NAME}.{name}")


@contextlib.contextmanager
def log_context(**fields) -> Iterator[None]:
    """Attach correlation fields (request_id, user, job...) to every record emitted inside the block."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "message": redact_extended(record.getMessage()),
        }
        ctx = _context.get()
        if ctx:
            payload["ctx"] = redact_mapping(ctx)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload["fields"] = redact_mapping(fields)
        if record.exc_info:
            payload["exception"] = redact_extended("".join(traceback.format_exception(*record.exc_info)))[-8000:]
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", None)
        extra = f" {json.dumps(redact_mapping(fields), ensure_ascii=False, default=str)}" if isinstance(fields, dict) else ""
        return f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: " \
               f"{redact_extended(record.getMessage())}{extra}"


def _remove_managed(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False):
            logger.removeHandler(handler)
            handler.close()


def configure(path: str | Path | None = None, level: str | int | None = None, *, console: bool = False,
              max_bytes: int = 5_000_000, backups: int = 5, capture_root: bool = False) -> Path | None:
    """Install rotating JSON file logging (+ optional console). Idempotent. Returns the log file path."""
    wanted = level if level is not None else os.environ.get(LEVEL_ENV, "INFO")
    numeric = wanted if isinstance(wanted, int) else logging.getLevelName(str(wanted).upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {wanted!r}")
    target = Path(path or os.environ.get(FILE_ENV) or default_log_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(target.parent, 0o700)
    handlers: list[logging.Handler] = []
    file_handler = logging.handlers.RotatingFileHandler(target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    handlers.append(file_handler)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(TextFormatter())
        handlers.append(stream)
    for handler in handlers:
        setattr(handler, _MARK, True)
        handler.setLevel(numeric)
    scopes = [logging.getLogger(ROOT_NAME)] + ([logging.getLogger()] if capture_root else [])
    for logger in scopes:
        _remove_managed(logger)
        for handler in handlers:
            logger.addHandler(handler)
    omega = logging.getLogger(ROOT_NAME)
    omega.setLevel(numeric)
    omega.propagate = False  # own handlers only; the same handlers sit on root when capture_root=True
    if capture_root:
        logging.getLogger().setLevel(min(logging.getLogger().level or numeric, numeric))
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)
    return target


def read_events(path: str | Path | None = None, limit: int = 50, min_level: str = "DEBUG") -> list[dict]:
    """Return the last `limit` JSON events at or above `min_level` (reads only the file tail)."""
    target = Path(path).expanduser() if path else default_log_path()
    if not target.is_file():
        return []
    floor = logging.getLevelName(min_level.upper())
    floor = floor if isinstance(floor, int) else logging.DEBUG
    with target.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 512_000))
        raw = stream.read().decode("utf-8", errors="replace")
    events: list[dict] = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and logging.getLevelName(str(item.get("level", "DEBUG"))) >= floor:
            events.append(item)
    return events[-max(1, limit):]

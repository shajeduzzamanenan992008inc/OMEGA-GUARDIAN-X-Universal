"""Debugging utilities: environment snapshot, timers, thread dumps, faulthandler. Values are redacted."""
from __future__ import annotations

import contextlib
import faulthandler
import functools
import gc
import logging
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

from ogx.kernel.config import state_dir
from ogx.kernel.redact import redact_extended

_started = time.time()
_fault_file = None


def snapshot() -> dict:
    """Support-friendly runtime facts. Environment *names* only (never values); argv redacted."""
    return {
        "python": sys.version.split()[0], "implementation": platform.python_implementation(),
        "platform": platform.platform(), "executable": sys.executable, "pid": os.getpid(), "cwd": os.getcwd(),
        "argv": [redact_extended(a) for a in sys.argv], "uptime_seconds": round(time.time() - _started, 1),
        "threads": threading.active_count(), "modules_loaded": len(sys.modules), "gc_counts": list(gc.get_count()),
        "omega_env_names": sorted(k for k in os.environ if k.startswith(("OGX_", "OMEGA_"))),
        "state_dir": str(state_dir()),
    }


class Timer:
    """`with Timer("db query") as t: ...` then `t.ms`. Logs at DEBUG to omega.debug when a logger is given."""

    def __init__(self, label: str = "block", logger: logging.Logger | None = None):
        self.label, self.logger, self.ms = label, logger, 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000
        if self.logger:
            self.logger.debug("%s took %.2f ms", self.label, self.ms, extra={"fields": {"label": self.label, "ms": round(self.ms, 3)}})


def timed(logger: logging.Logger | None = None, label: str | None = None):
    """Decorator form of Timer."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with Timer(label or fn.__qualname__, logger or logging.getLogger("omega.debug")):
                return fn(*args, **kwargs)
        return inner
    return wrap


def dump_threads() -> str:
    """Stack of every live thread: the first thing to look at for hangs and deadlocks."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append(f"--- thread {names.get(ident, '?')} ({ident}) ---\n" + "".join(traceback.format_stack(frame)))
    return redact_extended("\n".join(parts))


def enable_faulthandler(path: str | Path | None = None) -> Path:
    """Opt-in: write native crash tracebacks (segfaults, fatal errors) to a file in the state directory."""
    global _fault_file
    target = Path(path).expanduser() if path else state_dir() / "logs" / "faulthandler.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    _fault_file = open(target, "a", encoding="utf-8")
    faulthandler.enable(file=_fault_file, all_threads=True)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)
    return target

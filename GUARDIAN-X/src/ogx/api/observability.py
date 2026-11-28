"""Operational metrics for the REST API (ENT-09).

The API records a bounded set of request counters so an operator can answer
"is it healthy, how slow is it, what is failing" without a metrics service:

* requests by route label, status class and exact status code,
* cumulative and maximum request duration per route,
* in-flight gauge, process start time and version.

Route labels are derived from the *shape* of the path (``/api/v1/hosts/{id}``),
never the concrete id, so the label set cannot grow with traffic. Only
aggregates are exposed; no tenant id, subject, token or body is recorded, and the
metrics endpoint requires authentication.

Honest limits: this is per-process, in-memory and lost on restart. Multi-worker
deployments need a real metrics backend (Prometheus, OTLP), which is not bundled.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .metrics_store import InMemoryMetricsStorage


def route_label(path: str) -> str:
    """Collapse concrete identifiers so metric cardinality stays bounded."""

    parts = [part for part in path.split("/") if part]
    if parts[:2] == ["api", "v1"]:
        rest = parts[2:]
        if rest and rest[0] == "organizations":
            if len(rest) >= 2:
                rest[1] = "{id}"
            if len(rest) >= 4:
                rest[3] = "{sub}"
        elif rest and rest[0] == "hosts" and len(rest) >= 2:
            rest[1] = "{id}"
        label = "/api/v1/" + "/".join(rest)
    else:
        label = "/" + "/".join(parts)
    return label[:120] or "/"


@dataclass
class _RouteStats:
    count: int = 0
    errors: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0

    def observe(self, status: int, duration: float) -> None:
        self.count += 1
        if status >= 500:
            self.errors += 1
        self.total_seconds += duration
        if duration > self.max_seconds:
            self.max_seconds = duration

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "errors_5xx": self.errors,
            "total_seconds": round(self.total_seconds, 6),
            "max_seconds": round(self.max_seconds, 6),
            "mean_seconds": round(self.total_seconds / self.count, 6) if self.count else 0.0,
        }


@dataclass
class Metrics:
    version: str = ""
    started_at: float = field(default_factory=time.time)
    storage: Any = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _routes: dict[str, _RouteStats] = field(default_factory=dict, repr=False)
    _status: dict[int, int] = field(default_factory=dict, repr=False)
    _errors: dict[str, int] = field(default_factory=dict, repr=False)
    _inflight: int = 0
    _total: int = 0

    def __post_init__(self) -> None:
        if self.storage is None:
            self.storage = InMemoryMetricsStorage()
        self._hydrate()

    def _hydrate(self) -> None:
        try:
            snap = self.storage.load_snapshot()
        except Exception:
            return
        if not isinstance(snap, dict):
            return
        for label, stats in (snap.get("routes") or {}).items():
            entry = _RouteStats()
            entry.count = int(stats.get("count", 0) or 0)
            entry.errors = int(stats.get("errors_5xx", stats.get("errors", 0)) or 0)
            entry.total_seconds = float(stats.get("total_seconds", 0.0) or 0.0)
            entry.max_seconds = float(stats.get("max_seconds", 0.0) or 0.0)
            self._routes[str(label)] = entry
        for code, count in (snap.get("status_codes") or {}).items():
            try:
                self._status[int(code)] = int(count)
            except (TypeError, ValueError):
                continue
        for code, count in (snap.get("error_codes") or {}).items():
            self._errors[str(code)] = int(count)
        self._total = int(snap.get("requests_total", self._total) or 0)

    @property
    def healthy(self) -> bool:
        try:
            return bool(self.storage.healthy)
        except Exception:
            return True

    def begin(self) -> None:
        with self._lock:
            self._inflight += 1

    def finish(self, path: str, status: int, duration: float) -> None:
        label = route_label(path)
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._total += 1
            self._routes.setdefault(label, _RouteStats()).observe(status, duration)
            self._status[status] = self._status.get(status, 0) + 1
        try:
            self.storage.record_request(label, status, duration)
        except Exception:
            pass

    def record_error(self, code: str) -> None:
        """Count an error by its public, bounded error code (never a message)."""

        label = str(code or "unknown")[:48]
        with self._lock:
            self._errors[label] = self._errors.get(label, 0) + 1
        try:
            self.storage.record_error(label)
        except Exception:
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = {label: stats.as_dict() for label, stats in sorted(self._routes.items())}
            return {
                "version": self.version,
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "requests_total": self._total,
                "inflight": self._inflight,
                "status_codes": {str(code): count for code, count in sorted(self._status.items())},
                "error_codes": dict(sorted(self._errors.items())),
                "routes": routes,
            }

    def prometheus(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP ogx_api_requests_total Total HTTP requests handled.",
            "# TYPE ogx_api_requests_total counter",
            f"ogx_api_requests_total {snapshot['requests_total']}",
            "# HELP ogx_api_inflight Requests currently being handled.",
            "# TYPE ogx_api_inflight gauge",
            f"ogx_api_inflight {snapshot['inflight']}",
            "# HELP ogx_api_uptime_seconds Seconds since the process started serving.",
            "# TYPE ogx_api_uptime_seconds gauge",
            f"ogx_api_uptime_seconds {snapshot['uptime_seconds']}",
            "# HELP ogx_api_route_requests_total Requests per route label.",
            "# TYPE ogx_api_route_requests_total counter",
        ]
        for label, stats in snapshot["routes"].items():
            lines.append(f'ogx_api_route_requests_total{{route="{label}"}} {stats["count"]}')
        lines.extend([
            "# HELP ogx_api_route_duration_seconds_sum Cumulative request duration per route.",
            "# TYPE ogx_api_route_duration_seconds_sum counter",
        ])
        for label, stats in snapshot["routes"].items():
            lines.append(
                f'ogx_api_route_duration_seconds_sum{{route="{label}"}} {stats["total_seconds"]}'
            )
        if snapshot["error_codes"]:
            lines.extend([
                "# HELP ogx_api_errors_total Errors by public error code.",
                "# TYPE ogx_api_errors_total counter",
            ])
            for code, count in snapshot["error_codes"].items():
                lines.append(f'ogx_api_errors_total{{code="{code}"}} {count}')
        return "\n".join(lines) + "\n"


__all__ = ["Metrics", "route_label"]

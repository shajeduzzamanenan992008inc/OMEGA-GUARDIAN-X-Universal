"""Offline-capable tracing and alerting foundations (Priority C).

This module provides the *interfaces* and in-process implementation for
distributed tracing and alert evaluation without claiming an operational
backend. Live OTLP export and alert delivery are BLOCKED on external
infrastructure; only the contract, propagation, sampling and redaction logic
are implemented and tested here.

* :class:`TraceContext` -- W3C-style trace/span identifiers.
* :func:`inject_headers` / :func:`extract_headers` -- propagation across
  internal service boundaries.
* :class:`Sampler` -- configurable sampling (always, never, ratio, parent-based).
* :class:`OTLPExporter` -- exporter *interface*; :class:`NoOpExporter` is the
  default and records nothing externally.
* :class:`AlertRule` / :class:`AlertEvaluator` -- rule definitions and
  in-memory evaluation against metric snapshots.
* :func:`redact` -- remove credentials, tokens, request bodies and sensitive
  identifiers from arbitrary mappings.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol

_TRACE_CONTEXT: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "ogx_trace_context", default=None
)

_SENSITIVE_KEYS = {
    "authorization", "cookie", "set-cookie", "x-api-key", "api_key", "apikey",
    "token", "access_token", "refresh_token", "secret", "password", "passwd",
    "credential", "private_key", "client_secret", "body", "request_body",
    "payload", "ssn", "email", "subject", "tenant_id", "organization_id",
}


def _new_id(hex_chars: int) -> str:
    return uuid.uuid4().hex[:hex_chars]


@dataclass(frozen=True)
class TraceContext:
    trace_id: str = field(default_factory=lambda: _new_id(32))
    span_id: str = field(default_factory=lambda: _new_id(16))
    parent_span_id: str | None = None
    sampled: bool = True
    baggage: dict[str, str] = field(default_factory=dict)

    def child(self, *, sampled: bool | None = None) -> "TraceContext":
        return TraceContext(
            trace_id=self.trace_id,
            span_id=_new_id(16),
            parent_span_id=self.span_id,
            sampled=self.sampled if sampled is None else bool(sampled),
            baggage=dict(self.baggage),
        )

    def to_traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    @classmethod
    def from_traceparent(cls, value: str) -> "TraceContext | None":
        if not value or not isinstance(value, str):
            return None
        parts = value.strip().split("-")
        if len(parts) != 4:
            return None
        version, trace_id, span_id, flags = parts
        if version != "00":
            return None
        if len(trace_id) != 32 or len(span_id) != 16:
            return None
        try:
            int(trace_id, 16)
            int(span_id, 16)
        except ValueError:
            return None
        return cls(trace_id=trace_id, span_id=span_id, sampled=(flags == "01"))


def current_trace() -> TraceContext | None:
    return _TRACE_CONTEXT.get()


def set_current_trace(ctx: TraceContext | None) -> contextvars.Token:
    return _TRACE_CONTEXT.set(ctx)


def reset_trace(token: contextvars.Token) -> None:
    _TRACE_CONTEXT.reset(token)


class Sampler:
    """Configurable sampling policy."""

    def __init__(self, mode: str = "always", ratio: float = 1.0,
                 parent_based: bool = True) -> None:
        mode = (mode or "always").strip().lower()
        if mode not in {"always", "never", "ratio", "parent"}:
            raise ValueError("Unknown sampling mode.")
        self.mode = mode
        self.ratio = max(0.0, min(1.0, float(ratio)))
        self.parent_based = bool(parent_based)

    def decide(self, ctx: TraceContext | None = None) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False
        if ctx is not None and self.parent_based and ctx.parent_span_id is not None:
            return bool(ctx.sampled)
        if self.mode == "ratio":
            return random.random() < self.ratio
        if self.mode == "parent":
            return bool(ctx.sampled) if ctx is not None else True
        return True


def inject_headers(headers: dict[str, str], ctx: TraceContext | None = None) -> dict[str, str]:
    ctx = ctx or current_trace()
    if ctx is None:
        return dict(headers)
    out = dict(headers)
    out["traceparent"] = ctx.to_traceparent()
    if ctx.baggage:
        out["baggage"] = ",".join(f"{k}={v}" for k, v in ctx.baggage.items())
    return out


def extract_headers(headers: Mapping[str, str]) -> TraceContext | None:
    if not headers:
        return None
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    ctx = TraceContext.from_traceparent(normalized.get("traceparent", ""))
    if ctx is None:
        return None
    baggage_raw = normalized.get("baggage", "")
    baggage: dict[str, str] = {}
    for part in baggage_raw.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            baggage[key.strip()] = value.strip()
    return TraceContext(trace_id=ctx.trace_id, span_id=ctx.span_id,
                        parent_span_id=ctx.parent_span_id, sampled=ctx.sampled,
                        baggage=baggage)


def redact(value: Any, *, max_string: int = 64) -> Any:
    """Return a redacted copy of ``value`` with sensitive data removed."""

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            lowered = key_str.lower().replace("_", "-")
            if lowered in _SENSITIVE_KEYS or any(s in lowered for s in ("token", "secret", "password", "credential", "authorization")):
                out[key_str] = "[REDACTED]"
            else:
                out[key_str] = redact(item, max_string=max_string)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, max_string=max_string) for item in value]
    if isinstance(value, str):
        if len(value) > max_string:
            return value[:max_string] + "...[truncated]"
        return value
    if isinstance(value, (bytes, bytearray)):
        return "[REDACTED_BYTES]"
    return value


class OTLPExporter(Protocol):
    """Exporter interface. Implementations must not raise into callers."""

    def export_span(self, span: dict[str, Any]) -> bool: ...

    def export_metrics(self, payload: dict[str, Any]) -> bool: ...

    def shutdown(self) -> None: ...


class NoOpExporter:
    """Default exporter that records nothing externally."""

    def export_span(self, span: dict[str, Any]) -> bool:
        return True

    def export_metrics(self, payload: dict[str, Any]) -> bool:
        return True

    def shutdown(self) -> None:
        return None


@dataclass
class Span:
    name: str
    context: TraceContext
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None

    def finish(self, *, status: str = "ok", error: str | None = None,
              attributes: dict[str, Any] | None = None) -> None:
        self.ended_at = time.time()
        self.status = status
        self.error = error
        if attributes:
            self.attributes.update(attributes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.context.trace_id,
            "span_id": self.context.span_id,
            "parent_span_id": self.context.parent_span_id,
            "sampled": self.context.sampled,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": (round(self.ended_at - self.started_at, 6)
                                 if self.ended_at else None),
            "status": self.status,
            "error": self.error,
            "attributes": redact(self.attributes),
        }


class Tracer:
    """In-process tracer that records spans and forwards to an exporter."""

    def __init__(self, service_name: str = "ogx", exporter: OTLPExporter | None = None,
                 sampler: Sampler | None = None) -> None:
        self.service_name = service_name
        self.exporter = exporter or NoOpExporter()
        self.sampler = sampler or Sampler("always")
        self._spans: list[Span] = []
        self._lock = threading.Lock()

    def start_span(self, name: str, *, attributes: dict[str, Any] | None = None,
                   parent: TraceContext | None = None) -> Span:
        parent_ctx = parent or current_trace()
        if parent_ctx is not None:
            ctx = parent_ctx.child(sampled=self.sampler.decide(parent_ctx))
        else:
            ctx = TraceContext(sampled=self.sampler.decide(None))
        span = Span(name=name, context=ctx, attributes=dict(attributes or {}))
        span.attributes.setdefault("service.name", self.service_name)
        token = set_current_trace(ctx)
        span._token = token  # type: ignore[attr-defined]
        with self._lock:
            self._spans.append(span)
            # Bound memory.
            if len(self._spans) > 10_000:
                del self._spans[:1_000]
        return span

    def end_span(self, span: Span, *, status: str = "ok", error: str | None = None,
                 attributes: dict[str, Any] | None = None) -> None:
        span.finish(status=status, error=error, attributes=attributes)
        token = getattr(span, "_token", None)
        if token is not None:
            try:
                reset_trace(token)
            except Exception:
                pass
        if span.context.sampled:
            try:
                self.exporter.export_span(span.as_dict())
            except Exception:
                pass

    def recent_spans(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            items = self._spans[-max(1, int(limit)):]
        return [s.as_dict() for s in items]

    def shutdown(self) -> None:
        try:
            self.exporter.shutdown()
        except Exception:
            pass


@dataclass(frozen=True)
class AlertRule:
    name: str
    metric: str
    operator: str = ">"
    threshold: float = 0.0
    severity: str = "warning"
    description: str = ""

    def evaluate(self, value: float) -> bool:
        op = self.operator.strip()
        if op == ">":
            return value > self.threshold
        if op == ">=":
            return value >= self.threshold
        if op == "<":
            return value < self.threshold
        if op == "<=":
            return value <= self.threshold
        if op == "==":
            return value == self.threshold
        if op == "!=":
            return value != self.threshold
        raise ValueError(f"Unsupported operator: {op}")


class AlertEvaluator:
    """In-memory alert rule evaluation against a metric snapshot."""

    def __init__(self, rules: Iterable[AlertRule] | None = None) -> None:
        self._rules: list[AlertRule] = list(rules or [])
        self._lock = threading.Lock()

    def add_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules.append(rule)

    def rules(self) -> list[AlertRule]:
        with self._lock:
            return list(self._rules)

    def evaluate(self, metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
        fired: list[dict[str, Any]] = []
        flat = self._flatten(metrics)
        with self._lock:
            rules = list(self._rules)
        for rule in rules:
            value = flat.get(rule.metric)
            if value is None:
                continue
            try:
                triggered = rule.evaluate(float(value))
            except (TypeError, ValueError):
                continue
            if triggered:
                fired.append({
                    "rule": rule.name,
                    "metric": rule.metric,
                    "value": value,
                    "threshold": rule.threshold,
                    "operator": rule.operator,
                    "severity": rule.severity,
                    "description": rule.description,
                    "fired_at": time.time(),
                })
        return fired

    @staticmethod
    def _flatten(metrics: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
        out: dict[str, float] = {}
        for key, value in metrics.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, bool):
                out[path] = 1.0 if value else 0.0
            elif isinstance(value, (int, float)):
                out[path] = float(value)
            elif isinstance(value, Mapping):
                out.update(AlertEvaluator._flatten(value, path))
            elif isinstance(value, (list, tuple)):
                # Count lists as their length for simple alerting.
                out[path + "_count"] = float(len(value))
        return out


__all__ = [
    "TraceContext",
    "current_trace",
    "set_current_trace",
    "reset_trace",
    "Sampler",
    "inject_headers",
    "extract_headers",
    "redact",
    "OTLPExporter",
    "NoOpExporter",
    "Span",
    "Tracer",
    "AlertRule",
    "AlertEvaluator",
]

"""Offline-capable tracing and alerting (Priority C).

This module implements the tracing contract, W3C propagation, sampling and
redaction, a real OTLP/HTTP JSON exporter (bounded background queue, no
redirects, no proxies), in-memory alert evaluation, and HMAC-signed webhook
alert delivery with bounded retries.

A production OTLP collector or alert receiver is still external infrastructure:
the exporter and alerter are verified against loopback receivers, not against a
vendor backend. Nothing here claims a hosted observability service.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import queue
import random
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol

from ..platform.security import validate_api_base

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


# --------------------------------------------------------------- transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: an observability endpoint must not move the target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _post_json(url: str, body: bytes, *, headers: Mapping[str, str],
               timeout: float) -> tuple[int, str]:
    """POST JSON without redirects or environment proxies.

    Returns ``(status, detail)``; status ``0`` means the request never produced
    an HTTP response. Never raises for transport failures.
    """

    request = urllib.request.Request(url, data=body, method="POST")
    for key, value in headers.items():
        request.add_header(str(key), str(value))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    try:
        with opener.open(request, timeout=max(0.1, float(timeout))) as response:
            response.read(2048)
            return int(getattr(response, "status", 0) or 0), ""
    except urllib.error.HTTPError as error:
        try:
            detail = error.read(2048).decode("utf-8", "replace")
        except Exception:
            detail = ""
        return int(error.code or 0), detail[:300]
    except Exception as error:  # URLError, socket timeout, TLS failure, ...
        return 0, f"{type(error).__name__}: {str(error)[:300]}"


def _safe_header_map(headers: Mapping[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key).strip()
        text = str(value).strip()
        if not name or len(name) > 64 or not text or len(text) > 1024:
            continue
        if any(ch in name for ch in "\r\n:") or any(ch in text for ch in "\r\n"):
            continue
        if name.lower() in {"host", "content-length", "content-type", "connection"}:
            continue
        out[name] = text
    return out


def _otlp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"stringValue": ", ".join(str(item) for item in value)[:512]}
    return {"stringValue": str(value)[:512]}


def _otlp_attributes(attributes: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key, value in sorted((attributes or {}).items()):
        name = str(key)[:128]
        if not name:
            continue
        items.append({"key": name, "value": _otlp_value(value)})
    return items


def _otlp_span_payload(span: Mapping[str, Any], *, service_name: str) -> dict[str, Any]:
    started = float(span.get("started_at") or time.time())
    ended = span.get("ended_at")
    ended_at = float(ended) if ended else started
    status = str(span.get("status") or "ok")
    error = span.get("error")
    span_payload: dict[str, Any] = {
        "traceId": str(span.get("trace_id", ""))[:32],
        "spanId": str(span.get("span_id", ""))[:16],
        "name": str(span.get("name") or "span")[:200],
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(int(started * 1_000_000_000)),
        "endTimeUnixNano": str(int(ended_at * 1_000_000_000)),
        "attributes": _otlp_attributes(span.get("attributes")),
        "status": {"code": 1 if status == "ok" else 2},
    }
    parent = span.get("parent_span_id")
    if parent:
        span_payload["parentSpanId"] = str(parent)[:16]
    if error:
        span_payload["status"]["message"] = str(error)[:300]
    return {
        "resourceSpans": [{
            "resource": {
                "attributes": _otlp_attributes({"service.name": service_name})
            },
            "scopeSpans": [{
                "scope": {"name": "ogx", "version": "1"},
                "spans": [span_payload],
            }],
        }]
    }


def _flatten_numeric(metrics: Mapping[str, Any], prefix: str = "",
                     limit: int = 200) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in metrics.items():
        if len(out) >= limit:
            break
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            out[path] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            out[path] = float(value)
        elif isinstance(value, Mapping):
            out.update(_flatten_numeric(value, path, limit - len(out)))
    return out


def _otlp_metrics_payload(metrics: Mapping[str, Any], *, service_name: str,
                          now: float | None = None) -> dict[str, Any]:
    timestamp = int((time.time() if now is None else now) * 1_000_000_000)
    points = []
    for name, value in sorted(_flatten_numeric(metrics).items()):
        clean = re.sub(r"[^A-Za-z0-9_.-]+", ".", name).strip(".") or "metric"
        clean = re.sub(r"\.{2,}", ".", clean)[:200]
        points.append({
            "name": clean,
            "gauge": {"dataPoints": [{
                "asDouble": float(value),
                "timeUnixNano": str(timestamp),
            }]},
        })
    return {
        "resourceMetrics": [{
            "resource": {
                "attributes": _otlp_attributes({"service.name": service_name})
            },
            "scopeMetrics": [{
                "scope": {"name": "ogx", "version": "1"},
                "metrics": points,
            }],
        }]
    }


class HttpOTLPExporter:
    """OTLP/HTTP JSON exporter with a bounded, non-blocking delivery queue.

    The endpoint is validated with the provider SSRF policy: HTTPS only unless
    loopback HTTP is explicitly permitted. Redirects and environment proxies
    are refused. Delivery failures are counted and dropped, never raised into
    request handling.
    """

    def __init__(self, endpoint: str, *, service_name: str = "ogx",
                 timeout: float = 2.0, allow_loopback_http: bool = False,
                 headers: Mapping[str, str] | None = None,
                 queue_size: int = 256, max_attempts: int = 2,
                 backoff: float = 0.1,
                 max_body_bytes: int = 256 * 1024) -> None:
        self.endpoint = validate_api_base(
            endpoint, allow_loopback_http=allow_loopback_http
        )
        self.service_name = str(service_name or "ogx")[:64]
        self.timeout = max(0.1, float(timeout))
        self.headers = _safe_header_map(headers)
        self.max_attempts = max(1, int(max_attempts))
        self.backoff = max(0.0, float(backoff))
        self.max_body_bytes = max(1024, int(max_body_bytes))
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._closed = False
        self._lock = threading.Lock()
        self.exported = 0
        self.failed = 0
        self.dropped = 0
        self.last_error: str | None = None
        self._worker = threading.Thread(
            target=self._run, name="ogx-otlp-exporter", daemon=True
        )
        self._worker.start()

    # ---------------------------------------------------------- public API
    def export_span(self, span: dict[str, Any]) -> bool:
        return self._enqueue("traces", _otlp_span_payload(
            span, service_name=self.service_name
        ))

    def export_metrics(self, payload: dict[str, Any]) -> bool:
        return self._enqueue("metrics", _otlp_metrics_payload(
            payload, service_name=self.service_name
        ))

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until queued payloads have been attempted. Test/CLI helper."""

        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._worker.join(timeout=2.0)

    # ----------------------------------------------------------- internals
    def _enqueue(self, kind: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            self._queue.put_nowait((kind, payload))
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                kind, payload = item
                path = "/v1/traces" if kind == "traces" else "/v1/metrics"
                body = json.dumps(payload, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")
                if len(body) > self.max_body_bytes:
                    with self._lock:
                        self.dropped += 1
                    continue
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "OGX-OTLP/1.0",
                    **self.headers,
                }
                delivered = False
                for attempt in range(self.max_attempts):
                    status, detail = _post_json(
                        self.endpoint + path, body, headers=headers,
                        timeout=self.timeout,
                    )
                    if 200 <= status < 300:
                        delivered = True
                        break
                    retryable = status == 0 or status == 429 or status >= 500
                    with self._lock:
                        self.last_error = f"HTTP {status}: {detail}"[:300]
                    if not retryable:
                        break
                    if attempt + 1 < self.max_attempts:
                        time.sleep(self.backoff * (2 ** attempt))
                with self._lock:
                    if delivered:
                        self.exported += 1
                    else:
                        self.failed += 1
            except Exception as error:  # never kill the worker
                with self._lock:
                    self.failed += 1
                    self.last_error = f"{type(error).__name__}: {error}"[:300]
            finally:
                self._queue.task_done()


# ----------------------------------------------------------------- alerts


def sign_alert_payload(secret: str | bytes, timestamp: str, body: bytes) -> str:
    """Return ``sha256=<hex>`` over ``timestamp + "." + body``."""

    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    message = str(timestamp).encode("ascii", "strict") + b"." + body
    return "sha256=" + hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_alert_signature(secret: str | bytes, timestamp: str, body: bytes,
                           signature: str, *, tolerance_seconds: float = 300.0,
                           now: float | None = None) -> bool:
    """Constant-time verification with a bounded replay window."""

    try:
        sent = int(str(timestamp))
    except (TypeError, ValueError):
        return False
    moment = time.time() if now is None else float(now)
    if tolerance_seconds is not None and abs(moment - sent) > tolerance_seconds:
        return False
    expected = sign_alert_payload(secret, str(timestamp), body)
    return hmac.compare_digest(expected, str(signature))


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    attempts: int
    status: int | None = None
    error: str | None = None
    delivery_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "attempts": self.attempts,
            "status": self.status,
            "error": self.error,
            "delivery_id": self.delivery_id,
        }


class WebhookAlerter:
    """Deliver fired alerts to one HTTPS webhook with HMAC-SHA256 signing.

    Delivery is synchronous and bounded: at most ``max_alerts`` alerts, a body
    cap, a timeout per attempt and bounded retries with exponential backoff.
    Redirects are refused and 4xx responses are not retried (except 429).
    """

    def __init__(self, url: str, secret: str | bytes, *, timeout: float = 3.0,
                 max_attempts: int = 3, backoff: float = 0.2,
                 allow_loopback_http: bool = False, max_alerts: int = 100,
                 max_body_bytes: int = 256 * 1024) -> None:
        self.url = validate_api_base(url, allow_loopback_http=allow_loopback_http)
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(key) < 16:
            raise ValueError("Webhook secret must be at least 16 bytes.")
        self._secret = key
        self.timeout = max(0.1, float(timeout))
        self.max_attempts = max(1, int(max_attempts))
        self.backoff = max(0.0, float(backoff))
        self.max_alerts = max(1, int(max_alerts))
        self.max_body_bytes = max(1024, int(max_body_bytes))

    def send(self, alerts: Iterable[Mapping[str, Any]], *,
             now: float | None = None) -> DeliveryResult:
        items = [dict(item) for item in list(alerts)[: self.max_alerts]]
        if not items:
            return DeliveryResult(True, 0)
        moment = time.time() if now is None else float(now)
        payload = {
            "schema": "ogx.alerts.v1",
            "sent_at": moment,
            "alerts": redact(items),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(body) > self.max_body_bytes:
            payload["alerts"] = payload["alerts"][: max(1, self.max_alerts // 10)]
            body = json.dumps(payload, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        if len(body) > self.max_body_bytes:
            return DeliveryResult(False, 0, error="alert payload exceeds size limit")
        timestamp = str(int(moment))
        delivery_id = uuid.uuid4().hex
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OGX-Alerting/1.0",
            "X-OGX-Timestamp": timestamp,
            "X-OGX-Signature": sign_alert_payload(self._secret, timestamp, body),
            "X-OGX-Delivery": delivery_id,
        }
        last_status: int | None = None
        last_error: str | None = None
        attempts = 0
        for attempt in range(self.max_attempts):
            attempts = attempt + 1
            status, detail = _post_json(self.url, body, headers=headers,
                                        timeout=self.timeout)
            last_status = status or None
            if 200 <= status < 300:
                return DeliveryResult(True, attempts, status=status,
                                      delivery_id=delivery_id)
            last_error = f"HTTP {status}: {detail}"[:300] if status else detail
            retryable = status == 0 or status == 429 or status >= 500
            if not retryable:
                break
            if attempt + 1 < self.max_attempts:
                time.sleep(self.backoff * (2 ** attempt))
        return DeliveryResult(False, attempts, status=last_status,
                              error=last_error, delivery_id=delivery_id)


class AlertDispatcher:
    """Evaluate rules against a metric snapshot and deliver fired alerts."""

    def __init__(self, evaluator: AlertEvaluator,
                 alerter: WebhookAlerter | None = None) -> None:
        self.evaluator = evaluator
        self.alerter = alerter

    def dispatch(self, metrics: Mapping[str, Any]) -> tuple[list[dict[str, Any]],
                                                            DeliveryResult | None]:
        fired = self.evaluator.evaluate(metrics)
        if not fired or self.alerter is None:
            return fired, None
        return fired, self.alerter.send(fired)


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
    "HttpOTLPExporter",
    "sign_alert_payload",
    "verify_alert_signature",
    "DeliveryResult",
    "WebhookAlerter",
    "AlertDispatcher",
]

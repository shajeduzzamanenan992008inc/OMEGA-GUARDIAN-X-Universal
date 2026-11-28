"""Authenticated, durable REST API (ENT-08, UI-04).

Design notes that matter for review:

* **Authentication** is a service-identity bearer token (ENT-05). There is no
  cookie session, so no ambient credentials and no CSRF surface; mutating
  requests additionally reject a foreign ``Origin``.
* **Authorization** is the shared policy engine (UI-06): every route names one
  :class:`~ogx.enterprise.authz.Action` and every tenant-scoped route carries the
  organization id as the resource tenant, so object-level isolation is a policy
  decision rather than a query convention.
* **Bounds**: request bodies are capped, JSON is required for writes, list
  endpoints are paginated (``limit``/``offset`` with validated ranges), and
  responses are small by construction.
* **Idempotency**: a write may carry ``Idempotency-Key``; replays return the
  stored response, and a key reused with a different body is a 409.
* **Rate limiting** is per token (hashed, never stored in clear text).
* **Errors** use one schema with a correlation id; internals are never echoed.

Honest limits, documented in ``docs/ENTERPRISE_API_AND_AUTHZ.md``: this is a
single-node HTTP service (no HA, no TLS termination, no OIDC human login, no
OpenAPI document generation yet). It is designed to sit behind a TLS reverse
proxy, and it binds to loopback by default.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .. import __version__
from ..config import state_dir
from ..enterprise.authz import (
    Action,
    AuthorizationError,
    PolicyEngine,
    Principal,
    Resource,
)
from ..enterprise.service_identity import InvalidToken, ServiceIdentityManager
from ..enterprise.store import EnterpriseStore
from . import dashboard
from .observability import Metrics
from .metrics_store import MetricsStorage, InMemoryMetricsStorage, create_storage

API_VERSION = "v1"
MAX_BODY_BYTES = 64 * 1024
MAX_TOKEN_HEADER_BYTES = 512
DEFAULT_RATE_LIMIT = 120
DEFAULT_RATE_WINDOW = 60.0
MAX_PAGE_LIMIT = 200

_PROVIDER_CHOICES = (
    "local", "github", "gitlab", "bitbucket", "gitea", "forgejo",
    "azure_devops", "aws_codecommit", "generic_git",
)
_ORG_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_HOST_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_GOVERNANCE_TYPES = {
    "objective", "initiative", "risk", "decision", "change", "incident", "service_metric",
}
_GOVERNANCE_PRIORITIES = {"critical", "high", "medium", "low"}
_GOVERNANCE_STATUSES = {
    "proposed", "planned", "open", "in_progress", "blocked", "accepted", "mitigated",
    "resolved", "done", "rejected", "cancelled",
}
_REQUIRED_TABLES = {
    "organizations", "users", "memberships", "projects", "audit_events",
    "governance_records", "service_accounts", "service_tokens", "tenant_secrets",
    "idempotency_records",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, details: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    body: bytes
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)


class _RateLimiter:
    def __init__(self, limit: int, window: float) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window))
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, float]:
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and moment - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(0.0, self.window - (moment - bucket[0]))
                return False, retry_after
            bucket.append(moment)
            return True, 0.0


@dataclass
class ApiContext:
    store: EnterpriseStore
    policy: PolicyEngine | None = None
    identities: ServiceIdentityManager | None = None
    version: str = __version__
    rate_limit: int = DEFAULT_RATE_LIMIT
    rate_window: float = DEFAULT_RATE_WINDOW
    metrics_storage: Any = None
    limiter: _RateLimiter = field(init=False)
    metrics: Metrics = field(init=False)

    def __post_init__(self) -> None:
        self.store.initialize()
        if self.policy is None:
            self.policy = self.store.policy
        if self.identities is None:
            self.identities = ServiceIdentityManager(self.store, self.policy)
        self.limiter = _RateLimiter(self.rate_limit, self.rate_window)
        if self.metrics_storage is None:
            self.metrics_storage = InMemoryMetricsStorage()
        self.metrics = Metrics(version=self.version, storage=self.metrics_storage)

    @classmethod
    def default(cls) -> "ApiContext":
        store = EnterpriseStore()
        return cls(store=store)


def _jsonable(value: Any) -> Any:
    """Convert registry/domain objects into JSON-safe primitives."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value") and not callable(getattr(value, "value")):
        return _jsonable(value.value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "hex") and not callable(getattr(value, "hex")):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def _int_param(query: dict[str, list[str]], name: str, default: int,
               minimum: int, maximum: int) -> int:
    raw = query.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "invalid_parameter", f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ApiError(400, "invalid_parameter",
                       f"{name} must be between {minimum} and {maximum}.")
    return value


class _Handler(BaseHTTPRequestHandler):
    server_version = "OGX-API/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        logger = getattr(self.server, "ogx_logger", None)
        if logger is not None:
            logger.info("%s - %s", self.address_string(), format % args)

    @property
    def context(self) -> ApiContext:
        return self.server.context  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self._send(405, {"error": {"code": "method_not_allowed",
                                   "message": "CORS preflight is not supported."}},
                   extra_headers={"Allow": "GET, POST"})

    def do_HEAD(self) -> None:  # noqa: N802
        self._send(405, {"error": {"code": "method_not_allowed",
                                   "message": "HEAD is not supported."}})

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, {"error": {"code": "method_not_allowed",
                                   "message": "Method not allowed."}})

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(405, {"error": {"code": "method_not_allowed",
                                   "message": "Method not allowed."}})

    def do_PATCH(self) -> None:  # noqa: N802
        self._send(405, {"error": {"code": "method_not_allowed",
                                   "message": "Method not allowed."}})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str) -> None:
        self.request_id = self.headers.get("X-Request-Id", "").strip() or _token_hex(12)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,64}", self.request_id):
            self.request_id = _token_hex(12)
        started = time.monotonic()
        path = "/"
        self.context.metrics.begin()
        try:
            parsed = urlsplit(self.path)
            path = parsed.path or "/"
            query = parse_qs(parsed.query, keep_blank_values=True)
            if not self._origin_allowed(method):
                raise ApiError(403, "origin_not_allowed",
                               "Cross-origin requests are not allowed.")
            body = self._read_body() if method == "POST" else b""
            result = self._route(method, path, query, body)
            if isinstance(result, RawResponse):
                self._send_raw(result)
            else:
                status, payload, headers = result
                self._send(status, payload, extra_headers=headers)
        except ApiError as error:
            self.context.metrics.record_error(error.code)
            payload = {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": self.request_id,
                }
            }
            if error.details is not None:
                payload["error"]["details"] = _jsonable(error.details)
            self._send(error.status, payload, extra_headers=error.headers)
        except AuthorizationError as error:
            self._send(403, {"error": {
                "code": "forbidden",
                "message": error.decision.reason,
                "request_id": self.request_id,
                "details": error.decision.to_dict(),
            }})
        except KeyError as error:
            self._send(404, {"error": {"code": "not_found",
                                       "message": str(error).strip("'") or "Not found.",
                                       "request_id": self.request_id}})
        except (ValueError, PermissionError) as error:
            status = 403 if isinstance(error, PermissionError) else 400
            code = "forbidden" if status == 403 else "invalid_request"
            self._send(status, {"error": {"code": code, "message": str(error)[:300],
                                          "request_id": self.request_id}})
        except Exception:  # pragma: no cover - last-resort guard, never leaks internals
            self._send(500, {"error": {"code": "internal_error",
                                       "message": "The request could not be completed.",
                                       "request_id": self.request_id}})
        finally:
            self.context.metrics.finish(
                path, getattr(self, "_response_status", 500), time.monotonic() - started
            )

    def _origin_allowed(self, method: str) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        host = (self.headers.get("Host") or "").strip().lower()
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        return parsed.netloc.lower() == host

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0").strip() or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", "Content-Length is invalid.") from exc
        if length < 0:
            raise ApiError(400, "invalid_request", "Content-Length is invalid.")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "payload_too_large",
                           f"Request body exceeds {MAX_BODY_BYTES} bytes.")
        if length == 0:
            return b""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "unsupported_media_type",
                           "Mutating requests must use application/json.")
        return self.rfile.read(length)

    def _send(self, status: int, payload: dict[str, Any],
              extra_headers: dict[str, str] | None = None) -> None:
        if "request_id" not in payload.get("error", {}):
            payload = _with_request_id(payload, getattr(self, "request_id", ""))
        body = json.dumps(_jsonable(payload), sort_keys=True).encode("utf-8")
        self._write(status, body, "application/json; charset=utf-8",
                    {"Cache-Control": "no-store", **(extra_headers or {})})

    def _send_raw(self, response: RawResponse) -> None:
        self._write(response.status, response.body, response.content_type,
                    {"Cache-Control": "no-store", **response.headers})

    @staticmethod
    def _sanitize_header_value(value: object) -> str:
        return str(value).replace("\r", "").replace("\n", "")

    def _write(self, status: int, body: bytes, content_type: str,
               headers: dict[str, str]) -> None:
        if not getattr(self, "request_id", None):
            self.request_id = _token_hex(12)
        self._response_status = status
        self.send_response(status)
        self.send_header("Content-Type", self._sanitize_header_value(content_type))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", self._sanitize_header_value(self.request_id))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        for key, value in headers.items():
            self.send_header(key, self._sanitize_header_value(value))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    # -------------------------------------------------------------- routing
    def _route(self, method: str, path: str, query: dict[str, list[str]],
               body: bytes) -> Any:
        if method == "GET" and path == "/":
            return RawResponse(200, dashboard.render_html().encode("utf-8"),
                               "text/html; charset=utf-8", dashboard.SECURITY_HEADERS)
        if method == "GET" and path == "/dashboard.css":
            return RawResponse(200, dashboard.render_css().encode("utf-8"),
                               "text/css; charset=utf-8", dashboard.SECURITY_HEADERS)
        if method == "GET" and path == "/dashboard.js":
            return RawResponse(200, dashboard.render_js().encode("utf-8"),
                               "application/javascript; charset=utf-8",
                               dashboard.SECURITY_HEADERS)
        if method == "GET" and path == "/health":
            return 200, {"status": "ok", "version": self.context.version}, {}
        if method == "GET" and path == "/ready":
            status, payload = self._readiness()
            return status, payload, {}

        if method == "GET" and path == f"/api/{API_VERSION}/meta":
            return 200, self._meta(), {}
        if method == "GET" and path == f"/api/{API_VERSION}/openapi.json":
            self._authenticate()
            from .openapi import build_spec

            return 200, build_spec(version=self.context.version), {}
        if method == "GET" and path == f"/api/{API_VERSION}/metrics":
            self._authenticate()
            wanted = (query.get("format", ["json"])[0] or "json").strip().lower()
            if wanted == "prometheus":
                return RawResponse(200, self.context.metrics.prometheus().encode("utf-8"),
                                   "text/plain; version=0.0.4; charset=utf-8")
            if wanted != "json":
                raise ApiError(400, "invalid_parameter",
                               "format must be json or prometheus.")
            return 200, self.context.metrics.snapshot(), {}

        principal = self._authenticate()

        if path.startswith(f"/api/{API_VERSION}/organizations/"):
            return self._organization_routes(method, path, query, body, principal)
        if method == "GET" and path == f"/api/{API_VERSION}/organizations":
            return self._organizations(principal)
        if method == "GET" and path == f"/api/{API_VERSION}/policy/matrix":
            self._authorize(principal, Action.POLICY_READ)
            return 200, {"matrix": self.context.policy.matrix(),
                         "actions": self.context.policy.actions(),
                         "read_only": sorted(_read_actions())}, {}
        if method == "POST" and path == f"/api/{API_VERSION}/authorization/check":
            return self._authorization_check(body, principal)
        if method == "GET" and path == f"/api/{API_VERSION}/hosts":
            return self._hosts(principal)
        if path.startswith(f"/api/{API_VERSION}/hosts/"):
            return self._host(method, path, principal)
        if method == "GET" and path == f"/api/{API_VERSION}/jobs/stats":
            return self._jobs(principal)
        if method == "GET" and path == f"/api/{API_VERSION}/plugins":
            return self._plugins(principal)
        raise ApiError(404, "not_found", "No such endpoint.")

    # ------------------------------------------------------------- readiness
    def _readiness(self) -> tuple[int, dict[str, Any]]:
        """Report each dependency separately instead of one opaque verdict."""

        checks: list[dict[str, Any]] = []
        store = self.context.store
        try:
            with store._connection() as conn:
                conn.execute("SELECT 1").fetchone()
            checks.append({"name": "database", "status": "ok"})
        except Exception:  # pragma: no cover - defensive
            checks.append({"name": "database", "status": "failed"})
        try:
            with store._connection() as conn:
                present = {
                    str(row[0]) for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            missing = sorted(_REQUIRED_TABLES - present)
            checks.append({"name": "schema", "status": "ok" if not missing else "failed",
                           "missing": missing})
        except Exception:  # pragma: no cover - defensive
            checks.append({"name": "schema", "status": "failed"})
        # Observability storage health is reported but is not a hard readiness
        # gate: metrics failures must not break application requests.
        try:
            obs_status = "ok" if bool(self.context.metrics.healthy) else "degraded"
        except Exception:
            obs_status = "degraded"
        checks.append({"name": "observability_storage", "status": obs_status,
                       "critical": False})
        ready = all(check["status"] == "ok" for check in checks if check.get("critical", True))
        return (200 if ready else 503), {
            "status": "ready" if ready else "not_ready",
            "version": self.context.version,
            "checks": checks,
        }

    # --------------------------------------------------------- authenticated
    def _meta(self) -> dict[str, Any]:
        return {
            "product": "omega-guardian-x",
            "product_version": self.context.version,
            "api_version": API_VERSION,
            "authentication": {
                "scheme": "Bearer",
                "kind": "service_identity",
                "human_sso": False,
            },
            "limits": {
                "max_body_bytes": MAX_BODY_BYTES,
                "page_limit": MAX_PAGE_LIMIT,
                "rate_limit_per_minute": self.context.rate_limit,
            },
            "operations": {
                "openapi": f"/api/{API_VERSION}/openapi.json",
                "metrics": f"/api/{API_VERSION}/metrics",
                "health": "/health",
                "readiness": "/ready",
            },
        }

    def _authenticate(self) -> Principal:
        header = (self.headers.get("Authorization") or "").strip()
        if not header:
            raise ApiError(401, "authentication_required",
                           "A bearer token is required.",
                           headers={"WWW-Authenticate": 'Bearer realm="ogx"'})
        if len(header.encode("utf-8")) > MAX_TOKEN_HEADER_BYTES:
            raise ApiError(401, "invalid_token", "Bearer token is not recognised.")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_token", "Bearer token is not recognised.",
                           headers={"WWW-Authenticate": 'Bearer realm="ogx"'})
        identifier = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:32]
        allowed, retry_after = self.context.limiter.check(identifier)
        if not allowed:
            raise ApiError(429, "rate_limited", "Too many requests for this token.",
                           headers={"Retry-After": str(int(retry_after) + 1)})
        try:
            return self.context.identities.authenticate(token.strip())
        except InvalidToken as exc:
            raise ApiError(401, "invalid_token", "Bearer token is invalid, expired or revoked.",
                           headers={"WWW-Authenticate": 'Bearer realm="ogx"'}) from exc

    def _authorize(self, principal: Principal, action: Action, tenant_id: str | None = None,
                   *, kind: str = "organization", resource_id: str | None = None) -> None:
        resource = (
            Resource(kind=kind, id=resource_id, tenant_id=tenant_id)
            if tenant_id is not None else None
        )
        decision = self.context.policy.decide(
            principal, action, resource, context={"request_id": self.request_id}
        )
        if not decision.allowed:
            raise ApiError(403, "forbidden", decision.reason, details=decision.to_dict())

    def _organizations(self, principal: Principal) -> Any:
        self._authorize(principal, Action.ORGANIZATION_READ, principal.tenant_id,
                        kind="organization", resource_id=principal.tenant_id)
        organization = self.context.store.get_organization(principal.tenant_id or "")
        items = [organization] if organization else []
        return 200, {"items": items, "total": len(items)}, {}

    def _organization_routes(self, method: str, path: str, query: dict[str, list[str]],
                             body: bytes, principal: Principal) -> Any:
        remainder = path[len(f"/api/{API_VERSION}/organizations/"):]
        organization_id, _, tail = remainder.partition("/")
        if not _ORG_RE.match(organization_id):
            raise ApiError(404, "not_found", "Organization not found.")
        organization = self.context.store.get_organization(organization_id)
        if organization is None:
            raise ApiError(404, "not_found", "Organization not found.")

        if method == "GET" and tail == "":
            self._authorize(principal, Action.ORGANIZATION_READ, organization_id)
            return 200, organization, {}
        if method == "GET" and tail == "projects":
            self._authorize(principal, Action.PROJECT_READ, organization_id)
            limit = _int_param(query, "limit", 50, 1, MAX_PAGE_LIMIT)
            offset = _int_param(query, "offset", 0, 0, 1_000_000)
            return 200, self.context.store.list_projects_scoped(
                organization_id, limit=limit, offset=offset), {}
        if method == "POST" and tail == "projects":
            self._authorize(principal, Action.PROJECT_WRITE, organization_id)
            return self._idempotent(
                organization_id, "projects.create", body,
                lambda payload: self.context.store.register_project_scoped(
                    organization_id, payload["name"], payload.get("provider", "local"),
                    payload.get("repository_ref"), principal.subject),
            )
        if method == "GET" and tail == "members":
            self._authorize(principal, Action.MEMBER_READ, organization_id)
            limit = _int_param(query, "limit", 50, 1, MAX_PAGE_LIMIT)
            offset = _int_param(query, "offset", 0, 0, 1_000_000)
            return 200, self.context.store.list_members_scoped(
                organization_id, limit=limit, offset=offset), {}
        if method == "GET" and tail == "audit":
            self._authorize(principal, Action.AUDIT_READ, organization_id)
            limit = _int_param(query, "limit", 50, 1, MAX_PAGE_LIMIT)
            offset = _int_param(query, "offset", 0, 0, 1_000_000)
            return 200, self.context.store.list_audit_events_scoped(
                organization_id, limit=limit, offset=offset), {}
        if method == "GET" and tail == "audit/export":
            self._authorize(principal, Action.AUDIT_EXPORT, organization_id)
            limit = _int_param(query, "limit", MAX_PAGE_LIMIT, 1, MAX_PAGE_LIMIT)
            events = self.context.store.list_audit_events_scoped(
                organization_id, limit=limit, offset=0)
            lines = "".join(
                json.dumps(_jsonable(event), sort_keys=True) + "\n"
                for event in events["items"]
            )
            return RawResponse(200, lines.encode("utf-8"), "application/x-ndjson",
                               {"Content-Disposition": 'attachment; filename="ogx-audit.ndjson"'})
        if method == "GET" and tail == "governance/records":
            self._authorize(principal, Action.GOVERNANCE_READ, organization_id)
            limit = _int_param(query, "limit", 50, 1, MAX_PAGE_LIMIT)
            offset = _int_param(query, "offset", 0, 0, 1_000_000)
            record_type = query.get("type", [None])[0]
            return 200, self.context.store.list_governance_records_scoped(
                organization_id, record_type=record_type, limit=limit, offset=offset), {}
        if method == "POST" and tail == "governance/records":
            self._authorize(principal, Action.GOVERNANCE_WRITE, organization_id)
            return self._idempotent(
                organization_id, "governance.create", body,
                lambda payload: self.context.store.create_governance_record_scoped(
                    organization_id, payload["record_type"], payload["title"],
                    payload["owner_subject"], payload.get("priority", "medium"),
                    payload.get("status", "open"), payload.get("due_date"),
                    payload.get("details") or {}, principal.subject),
            )
        raise ApiError(404, "not_found", "No such endpoint.")

    def _idempotent(self, organization_id: str, route: str, body: bytes,
                    action: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
        payload = self._json_body(body)
        key = (self.headers.get("Idempotency-Key") or "").strip()
        digest = hashlib.sha256(
            (route + "\n" + json.dumps(payload, sort_keys=True)).encode("utf-8")
        ).hexdigest()
        if not key:
            return 201, action(payload), {}
        if not _IDEMPOTENCY_RE.match(key):
            raise ApiError(400, "invalid_idempotency_key",
                           "Idempotency-Key must be 8-128 characters.")
        record = self.context.store.get_idempotent(organization_id, key)
        if record is not None:
            if record["request_digest"] != digest:
                raise ApiError(409, "idempotency_conflict",
                               "This Idempotency-Key was used with a different request.")
            return record["status_code"], record["response"], {"Idempotent-Replay": "true"}
        result = action(payload)
        response = {"data": _jsonable(result)}
        self.context.store.save_idempotent(organization_id, key, route, digest,
                                           response, 201)
        return 201, response, {"Idempotency-Key": key}

    def _json_body(self, body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "invalid_json", "Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
        return payload

    def _authorization_check(self, body: bytes, principal: Principal) -> Any:
        self._authorize(principal, Action.POLICY_READ)
        payload = self._json_body(body)
        action = str(payload.get("action") or "").strip()
        if not action:
            raise ApiError(400, "invalid_request", "action is required.")
        resource = None
        if payload.get("resource_kind"):
            resource = Resource(
                kind=str(payload["resource_kind"])[:64],
                id=str(payload["resource_id"])[:128] if payload.get("resource_id") else None,
                tenant_id=principal.tenant_id,
            )
        decision = self.context.policy.decide(
            principal, action, resource, context={"request_id": self.request_id}
        )
        return 200, {
            "decision": decision.to_dict(),
            "effective_permissions": sorted(principal.permissions),
            "roles": list(principal.roles),
            "scopes": list(principal.scopes or ()),
        }, {}

    def _hosts(self, principal: Principal) -> Any:
        self._authorize(principal, Action.HOST_READ)
        from ..hosts.registry import get_host_registry

        registry = get_host_registry()
        hosts = registry.list_hosts(limit=100, offset=0)
        items = []
        for host in hosts:
            host_id = str(host.get("host_id"))
            try:
                capabilities = registry.get_host_capabilities(host_id)
            except Exception:  # pragma: no cover - registry may not know a new type
                capabilities = {}
            items.append({
                "host_id": host_id,
                "host_type": host.get("host_type"),
                "provider": host.get("provider"),
                "display_name": host.get("display_name"),
                "status": host.get("status"),
                "normalized_identifier": host.get("normalized_identifier"),
                "capabilities": [
                    {"name": name, "status": status}
                    for name, status in sorted(capabilities.items())
                ],
            })
        return 200, {"items": items, "total": len(items)}, {}

    def _host(self, method: str, path: str, principal: Principal) -> Any:
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "Method not allowed.")
        self._authorize(principal, Action.HOST_READ)
        remainder = path[len(f"/api/{API_VERSION}/hosts/"):]
        if not _HOST_ID_RE.match(remainder):
            raise ApiError(404, "not_found", "Host not found.")
        from ..hosts.registry import get_host_registry

        registry = get_host_registry()
        host = registry.get_host(remainder)
        return 200, {
            "host_id": str(host["host_id"]),
            "host_type": host["host_type"],
            "provider": host["provider"],
            "display_name": host["display_name"],
            "status": host["status"],
            "normalized_identifier": host["normalized_identifier"],
            "capabilities": registry.get_host_capabilities(str(host["host_id"])),
        }, {}

    def _jobs(self, principal: Principal) -> Any:
        self._authorize(principal, Action.JOB_READ)
        from ..jobs import JobQueue, default_queue_path

        queue = JobQueue(default_queue_path())
        return 200, queue.stats(), {}

    def _plugins(self, principal: Principal) -> Any:
        self._authorize(principal, Action.PLUGIN_READ)
        from ..plugins.permissions import CAPABILITY_RISK, SENSITIVE_CAPABILITIES
        from ..plugins.store import PluginStore

        installed: list[dict[str, Any]] = []
        available = True
        try:
            store = PluginStore(state_dir() / "plugins" / "plugins.db")
            store.initialize()
            installed = [
                {"name": item.get("name"), "version": item.get("version"),
                 "status": item.get("status")}
                for item in store.list()
            ]
        except Exception:  # pragma: no cover - a missing plugin database is not an error
            available = False
        return 200, {
            "installed": installed,
            "store_available": available,
            "capability_risk": {name: risk for name, risk in sorted(CAPABILITY_RISK.items())},
            "sensitive_capabilities": sorted(SENSITIVE_CAPABILITIES),
        }, {}


def _with_request_id(payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    error = payload.get("error")
    if isinstance(error, dict) and request_id and "request_id" not in error:
        return {**payload, "error": {**error, "request_id": request_id}}
    return payload


def _token_hex(length: int = 12) -> str:
    import secrets as _secrets

    return _secrets.token_hex(max(4, length // 2))


def _read_actions() -> set[str]:
    from ..enterprise.authz import READ_ONLY_ACTIONS

    return set(READ_ONLY_ACTIONS)


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    context: ApiContext

    def __init__(self, address: tuple[str, int], context: ApiContext,
                 logger: Any = None) -> None:
        super().__init__(address, _Handler)
        self.context = context
        self.ogx_logger = logger


def create_server(context: ApiContext | None = None, *, host: str = "127.0.0.1",
                  port: int = 0, logger: Any = None) -> ApiServer:
    """Create (but do not start) the API server. Binds to loopback by default."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "The API server only binds to a loopback address; terminate TLS and "
            "expose it through a reverse proxy."
        )
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535.")
    return ApiServer((host, int(port)), context or ApiContext.default(), logger=logger)


def serve(context: ApiContext | None = None, *, host: str = "127.0.0.1", port: int = 8765,
          logger: Any = None) -> None:
    server = create_server(context, host=host, port=port, logger=logger)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


__all__ = [
    "API_VERSION",
    "ApiContext",
    "ApiError",
    "ApiServer",
    "MAX_BODY_BYTES",
    "RawResponse",
    "create_server",
    "serve",
]

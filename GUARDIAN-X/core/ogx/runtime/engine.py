"""The guard engine: request hygiene, IP policy, rate limiting, access control and attack signatures.

`Shield.inspect(request)` is framework-neutral; the WSGI/ASGI middleware and the reverse proxy only
translate their native request objects into `Request` and apply the returned `Decision`.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
import uuid
from http import HTTPStatus
from typing import Callable

from ogx.runtime.access import check_access, ip_in
from ogx.runtime.config import RuntimeConfig
from ogx.runtime.ratelimit import RateLimiter
from ogx.runtime.request import REQUEST_ID_RE, Decision, Match, Request, normalize_path
from ogx.runtime.rules import build_views, run_signatures

_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")      # tab is allowed; CR/LF/NUL and other controls are not
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DIGITS = re.compile(r"^\d{1,12}$")
_LOG = logging.getLogger("omega.guard")
logging.getLogger("omega").addHandler(logging.NullHandler())   # library etiquette: silent until observe.configure()

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "geolocation=(), camera=(), microphone=()"),
)
Sink = Callable[[Decision, Request], None]


def log_sink(decision: Decision, request: Request) -> None:
    """Default event sink: one structured record per request that fired a rule. Never logs bodies/headers/queries."""
    blocked = decision.action == "block"
    would_block = not blocked and any(m.action == "block" for m in decision.matches)
    path = _CONTROL.sub("?", request.path.split("?", 1)[0])[:200]
    fields = {"request_id": decision.request_id, "client_ip": decision.client_ip, "method": request.method[:16],
              "path": path, "query_len": len(request.query), "action": decision.action, "status": decision.status,
              "rule_id": decision.rule_id or (decision.matches[0].rule_id if decision.matches else ""),
              "rules": [m.rule_id for m in decision.matches], "severity": decision.severity, "would_block": would_block}
    _LOG.log(logging.WARNING if (blocked or would_block) else logging.INFO,
             "guard %s %s", "block" if blocked else ("monitor" if would_block else "notice"), fields["rule_id"],
             extra={"fields": fields})


class Shield:
    def __init__(self, config: RuntimeConfig | None = None, *, clock: Callable[[], float] = time.monotonic,
                 sink: Sink | None = None):
        self.config = config or RuntimeConfig()
        self._limiter = RateLimiter(self.config.max_tracked_clients, clock)
        self._sink: Sink = sink if sink is not None else log_sink

    # ------------------------------------------------------------------ identity
    def _request_id(self, request: Request) -> str:
        supplied = request.header("X-Request-ID")
        return supplied if supplied and REQUEST_ID_RE.match(supplied) else uuid.uuid4().hex[:16]

    def client_ip(self, request: Request) -> str:
        """Direct peer, unless it is a trusted proxy: then the right-most untrusted X-Forwarded-For hop."""
        peer, trusted = request.client_ip, self.config.trusted_proxies
        if not trusted or not ip_in(peer, trusted):
            return peer
        chain = [part.strip() for value in request.headers_named("X-Forwarded-For") for part in value.split(",") if part.strip()]
        for hop in reversed(chain):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                return peer                      # garbage in the chain: do not trust it
            if not ip_in(hop, trusted):
                return hop
        return chain[0] if chain else peer

    # ------------------------------------------------------------------ layers
    def _hygiene(self, request: Request) -> list[Match]:
        cfg, out = self.config, []

        def add(rule_id, name, status, reason, severity="medium", action="block", headers=None):
            out.append(Match(rule_id, name, severity, action, status, reason, "protocol", headers or []))

        method = request.method.upper()
        if method not in cfg.allowed_methods:
            add("OGX-RT-0100", "method-not-allowed", 405, f"method {method[:16]!r} is not allowed",
                headers=[("Allow", ", ".join(cfg.allowed_methods))])
        if len(request.path) + (len(request.query) + 1 if request.query else 0) > cfg.max_url_length:
            add("OGX-RT-0110", "url-too-long", 414, "request URL exceeds the configured limit")
        if len(request.headers) > cfg.max_header_count or sum(len(k) + len(v) + 4 for k, v in request.headers) > cfg.max_header_bytes:
            add("OGX-RT-0120", "headers-too-large", 431, "request headers exceed the configured limits")
        if (_CONTROL.search(request.path) or _CONTROL.search(request.query)
                or any(_CONTROL.search(k) or _CONTROL.search(v) for k, v in request.headers)):
            add("OGX-RT-0160", "control-characters", 400, "control characters in request line or headers", "high")
        elif "%00" in request.path.lower() or "%00" in request.query.lower():
            add("OGX-RT-0160", "null-byte", 400, "encoded NUL byte in request", "high")
        if _BAD_PERCENT.search(request.path) or _BAD_PERCENT.search(request.query):
            add("OGX-RT-0170", "malformed-percent-encoding", 400, "malformed percent-encoding",
                action="block" if cfg.signature_profile == "strict" else "log")
        lengths = {value.strip() for value in request.headers_named("Content-Length")}
        te = request.headers_named("Transfer-Encoding")
        declared = request.declared_length
        if lengths:
            if len(lengths) > 1 or not all(_DIGITS.match(v) for v in lengths):
                add("OGX-RT-0140", "invalid-content-length", 400, "invalid or conflicting Content-Length", "high")
            elif declared is None:
                declared = int(next(iter(lengths)))
        if lengths and te:
            add("OGX-RT-0140", "request-smuggling-shape", 400, "both Content-Length and Transfer-Encoding present", "high")
        if max(declared or 0, len(request.body)) > cfg.max_body_bytes:
            add("OGX-RT-0130", "body-too-large", 413, "request body exceeds the configured limit")
        host = (request.header("Host") or "").rsplit(":", 1)[0].strip("[]").lower() if cfg.allowed_hosts else ""
        if cfg.allowed_hosts and host not in cfg.allowed_hosts:
            add("OGX-RT-0150", "host-not-allowed", 400, "Host header is not in allowed_hosts")
        return out

    def _client_policy(self, request: Request, ip: str) -> list[Match]:
        cfg, out = self.config, []
        if cfg.block_ips and ip_in(ip, cfg.block_ips):
            out.append(Match("OGX-RT-0010", "ip-blocked", "high", "block", 403, "client address is blocked", "client"))
        elif cfg.allow_ips and not ip_in(ip, cfg.allow_ips):
            out.append(Match("OGX-RT-0011", "ip-not-allowed", "high", "block", 403, "client address is not in allow_ips", "client"))
        agent = (request.header("User-Agent") or "").lower()
        if agent and any(bad in agent for bad in cfg.block_user_agents):
            out.append(Match("OGX-RT-1090", "scanner-user-agent", "medium", "block", 403, "known scanner user agent", "client"))
        return out

    def _throttle(self, ip: str, norm_path: str) -> list[Match]:
        cfg = self.config
        if not cfg.ratelimit_enabled:
            return []
        checks = [(ip, cfg.requests_per_minute, cfg.burst)]
        lowered = norm_path.lower()
        for route in cfg.route_limits:
            prefix = route.prefix.lower().rstrip("/") or "/"
            if prefix == "/" or lowered == prefix or lowered.startswith(prefix + "/"):
                checks.append((f"{ip}|{prefix}", route.requests_per_minute, route.burst))
        for key, rpm, burst in checks:
            allowed, retry = self._limiter.allow(key, rpm, burst)
            if not allowed:
                return [Match("OGX-RT-0020", "rate-limited", "medium", "block", 429, "rate limit exceeded", "ratelimit",
                              [("Retry-After", str(retry))])]
        return []

    def _signatures(self, request: Request, norm_path: str) -> list[Match]:
        cfg = self.config
        if cfg.signature_profile == "off":
            return []
        lowered = norm_path.lower()
        if any(lowered.startswith(p.lower()) for p in cfg.skip_signature_paths):
            return []
        views = build_views(request, norm_path, cfg.inspect_body_bytes)
        return run_signatures(views, cfg.signature_profile, set(cfg.disabled_rules))

    # ------------------------------------------------------------------ decision
    def inspect(self, request: Request) -> Decision:
        cfg = self.config
        decision = Decision(request_id=self._request_id(request), client_ip=self.client_ip(request))
        matches, enforce = decision.matches, cfg.mode == "enforce"

        def stop() -> bool:
            return enforce and any(m.action == "block" for m in matches)

        matches.extend(self._hygiene(request))
        norm = normalize_path(request.path)
        if not stop():
            matches.extend(self._client_policy(request, decision.client_ip))
        if not stop():
            matches.extend(self._throttle(decision.client_ip, norm))
        if not stop():
            found = check_access(cfg, request, norm, decision.client_ip)
            if found:
                matches.append(found)
        if not stop():
            matches.extend(self._signatures(request, norm))
        first = next((m for m in matches if m.action == "block"), None)
        if first and enforce:
            decision.action, decision.status = "block", first.status
            decision.rule_id, decision.reason, decision.severity = first.rule_id, first.reason, first.severity
            decision.headers = list(first.headers)
        if matches:
            try:
                self._sink(decision, request)
            except Exception:  # an event sink must never break request handling
                _LOG.exception("guard event sink failed")
        return decision

    # ------------------------------------------------------------------ responses
    def harden(self, headers: list[tuple[str, str]], scheme: str = "http", request_id: str | None = None) -> list[tuple[str, str]]:
        """Add missing security headers; never overrides what the application already set."""
        present = {name.lower() for name, _ in headers}
        extra: list[tuple[str, str]] = []
        if self.config.security_headers:
            extra.extend((n, v) for n, v in SECURITY_HEADERS if n.lower() not in present)
            if scheme == "https" and "strict-transport-security" not in present:
                extra.append(("Strict-Transport-Security", "max-age=15552000"))
        if request_id and "x-request-id" not in present:
            extra.append(("X-Request-ID", request_id))
        return headers + extra

    def block_response(self, decision: Decision, scheme: str = "http") -> tuple[int, list[tuple[str, str]], bytes]:
        payload = {"error": HTTPStatus(decision.status).phrase.lower(), "status": decision.status, "request_id": decision.request_id}
        if self.config.expose_rule_id:
            payload["rule"] = decision.rule_id
        body = json.dumps(payload).encode("utf-8")
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                   *decision.headers]
        return decision.status, self.harden(headers, scheme, decision.request_id), body

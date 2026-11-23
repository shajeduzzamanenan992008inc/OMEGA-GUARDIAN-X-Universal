"""Coarse access control: per-route API-key + role rules and per-route IP allow lists."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Iterable

from ogx.runtime.config import AccessRule, ApiKey, IPNetwork, RuntimeConfig
from ogx.runtime.request import Match, Request


def ip_in(address: str, networks: Iterable[IPNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in networks if ip.version == net.version)


def extract_api_key(request: Request) -> str | None:
    key = request.header("X-API-Key")
    if key and key.strip():
        return key.strip()
    auth = request.header("Authorization") or ""
    if auth[:7].lower() == "bearer " and auth[7:].strip():
        return auth[7:].strip()
    return None


def authenticate(keys: Iterable[ApiKey], presented: str) -> ApiKey | None:
    """Constant-time comparison against every stored SHA-256 digest (no early exit)."""
    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    found: ApiKey | None = None
    for key in keys:
        if hmac.compare_digest(digest, key.sha256) and found is None:
            found = key
    return found


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _rule_for(rules: Iterable[AccessRule], method: str, norm_path: str) -> AccessRule | None:
    lowered = norm_path.lower()  # fail closed on case-insensitive back ends
    for rule in rules:
        prefix = rule.prefix.lower().rstrip("/") or "/"
        in_scope = prefix == "/" or lowered == prefix or lowered.startswith(prefix + "/")
        if in_scope and ("*" in rule.methods or method in rule.methods):
            return rule
    return None


def check_access(config: RuntimeConfig, request: Request, norm_path: str, client_ip: str) -> Match | None:
    if not config.access_enabled:
        return None
    rule = _rule_for(config.access_rules, request.method.upper(), norm_path)
    if rule is None:
        return None
    if rule.ip_allow and not ip_in(client_ip, rule.ip_allow):
        return Match("OGX-RT-0032", "route-ip-denied", "high", "block", 403, "client address not allowed for this route", "access")
    if rule.require == "none":
        return None
    presented = extract_api_key(request)
    if presented is None:
        return Match("OGX-RT-0030", "credentials-missing", "medium", "block", 401, "API key required", "access",
                     [("WWW-Authenticate", 'Bearer realm="omega-shield"')])
    key = authenticate(config.api_keys, presented)
    if key is None:
        return Match("OGX-RT-0030", "credentials-invalid", "medium", "block", 401, "API key rejected", "access",
                     [("WWW-Authenticate", 'Bearer realm="omega-shield"')])
    if rule.roles and key.role not in rule.roles:
        return Match("OGX-RT-0031", "role-denied", "high", "block", 403, f"role {key.role!r} not allowed for this route", "access")
    return None

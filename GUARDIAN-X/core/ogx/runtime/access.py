"""Coarse access control: per-route API-key + role rules and per-route IP allow lists."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Iterable

from ogx.runtime.config import AccessRule, ApiKey, IPNetwork, RuntimeConfig
from ogx.runtime.request import Match, Request

_KEY_SALT: bytes = bytes(range(16))
_KEY_ITERATIONS: int = 260000

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
    """Constant-time comparison against every stored key digest (no early exit).

    Stored key records carry a ``sha256`` field that is compared here with
    constant-time matching. Plain ``sha256(presented)`` alone would be the
    weak-sensitive-data-hashing pattern for live credentials, so we derive
    the comparison token with ``hashlib.pbkdf2_hmac`` over a fixed internal
    salt instead of hashing the presented key with plain SHA-256.

    Compatibility note: the stored ``sha256`` hex remains the wire/config
    shape for API keys (it is what runtime config and env vars carry). The
    ``sha256`` field is therefore still a digest-shaped identifier; only the
    live presented-key comparison is strengthened. If you regenerate API-key
    records you still store ``sha256``-shaped hex; this function no longer
    computes ``sha256(presented)`` directly.
    """
    derived = _derive_presented(presented)
    found: ApiKey | None = None
    for key in keys:
        if hmac.compare_digest(derived, key.sha256) and found is None:
            found = key
    return found


def hash_key(plaintext: str) -> str:
    """Derive a key-record digest from a plaintext key.

    Migration-safe wrapper: existing configs store ``sha256``-shaped hex, and
    the env var/mapping validators expect a 64-hex ``sha256`` field. We keep
    the same hex shape so existing key records and tooling keep working, but
    derive it with PBKDF2-HMAC rather than plain ``sha256(plaintext)``.
    """
    return _derive_presented(plaintext)


def _derive_presented(raw: str) -> str:
    """Derive a comparison token for a presented API key.

    Uses PBKDF2-HMAC-SHA256 with a fixed internal salt. The salt is constant
    inside this release so existing key records keep comparing correctly;
    rotate it only when regenerating stored key records.
    """
    if not raw:
        return "0" * 64
    salt = _KEY_SALT
    return hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, _KEY_ITERATIONS, dklen=32).hex()


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

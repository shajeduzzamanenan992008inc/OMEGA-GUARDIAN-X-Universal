"""Runtime guard configuration: secure defaults, strict validation, TOML loading."""
from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ogx.kernel.paths import shield_home

CONFIG_ENV = "OMEGA_GUARD_CONFIG"
KEYS_ENV = "OMEGA_GUARD_KEYS"            # name:role:sha256hex[,name:role:sha256hex...]
DEFAULT_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
DEFAULT_BAD_AGENTS = ("sqlmap", "nikto", "nmap", "masscan", "acunetix", "nessus", "havij", "dirbuster")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(slots=True)
class RouteLimit:
    prefix: str
    requests_per_minute: int
    burst: int


@dataclass(slots=True)
class AccessRule:
    prefix: str
    methods: tuple[str, ...] = ("*",)
    require: str = "api_key"                  # none | api_key
    roles: tuple[str, ...] = ()
    ip_allow: tuple[IPNetwork, ...] = ()


@dataclass(slots=True)
class ApiKey:
    name: str
    sha256: str
    role: str = "user"


@dataclass(slots=True)
class RuntimeConfig:
    mode: str = "enforce"                     # enforce | monitor (log only, never blocks)
    allowed_methods: tuple[str, ...] = DEFAULT_METHODS
    max_url_length: int = 4096
    max_header_count: int = 100
    max_header_bytes: int = 16384
    max_body_bytes: int = 1_048_576
    inspect_body_bytes: int = 65_536
    allowed_hosts: tuple[str, ...] = ()
    trusted_proxies: tuple[IPNetwork, ...] = ()
    block_ips: tuple[IPNetwork, ...] = ()
    allow_ips: tuple[IPNetwork, ...] = ()
    block_user_agents: tuple[str, ...] = DEFAULT_BAD_AGENTS
    signature_profile: str = "balanced"       # off | balanced | strict
    disabled_rules: tuple[str, ...] = ()
    skip_signature_paths: tuple[str, ...] = ()
    security_headers: bool = True
    expose_rule_id: bool = False
    ratelimit_enabled: bool = True
    requests_per_minute: int = 300
    burst: int = 60
    max_tracked_clients: int = 10_000
    route_limits: tuple[RouteLimit, ...] = ()
    access_enabled: bool = False
    api_keys: tuple[ApiKey, ...] = ()
    access_rules: tuple[AccessRule, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "signature_profile": self.signature_profile,
            "methods": list(self.allowed_methods), "max_body_bytes": self.max_body_bytes,
            "ratelimit": {"enabled": self.ratelimit_enabled, "requests_per_minute": self.requests_per_minute,
                          "burst": self.burst, "route_limits": len(self.route_limits)},
            "access": {"enabled": self.access_enabled, "keys": len(self.api_keys), "rules": len(self.access_rules)},
            "ip_policy": {"block": len(self.block_ips), "allow_only": len(self.allow_ips), "trusted_proxies": len(self.trusted_proxies)},
            "security_headers": self.security_headers,
        }

    def warnings(self) -> list[str]:
        notes = []
        if self.mode == "monitor":
            notes.append("mode=monitor: nothing is blocked; events are only logged.")
        if self.signature_profile == "off":
            notes.append("signature_profile=off: attack-signature checks are disabled.")
        if self.access_enabled and not self.api_keys:
            notes.append("access is enabled but no API keys are configured: protected routes will reject every request.")
        if self.access_enabled and not self.access_rules:
            notes.append("access is enabled but no rules are defined: nothing is protected.")
        if not self.ratelimit_enabled:
            notes.append("rate limiting is disabled.")
        if not self.trusted_proxies:
            notes.append("trusted_proxies is empty: X-Forwarded-For is ignored (correct unless a proxy sits in front).")
        return notes


def _int(table: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be an integer between {low} and {high}.")
    return value


def _bool(table: dict[str, Any], key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false.")
    return value


def _strings(table: dict[str, Any], key: str, default: Iterable[str]) -> tuple[str, ...]:
    value = table.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{key} must be an array of strings.")
    return tuple(value)


def _networks(values: Iterable[str], label: str) -> tuple[IPNetwork, ...]:
    out = []
    for item in values:
        try:
            out.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError as exc:
            raise ValueError(f"{label}: invalid IP or CIDR {item!r}.") from exc
    return tuple(out)


def _prefix(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 200:
        raise ValueError(f"{label}: prefix must be a string starting with '/'.")
    return value


def _env_keys() -> list[ApiKey]:
    raw = os.environ.get(KEYS_ENV, "").strip()
    keys = []
    for item in filter(None, (part.strip() for part in raw.split(","))):
        pieces = item.split(":")
        if len(pieces) != 3 or not _NAME.match(pieces[0]) or not _NAME.match(pieces[1]) or not _HEX64.match(pieces[2].lower()):
            raise ValueError(f"{KEYS_ENV} entries must look like name:role:sha256hex.")
        keys.append(ApiKey(pieces[0], pieces[2].lower(), pieces[1]))
    return keys


def from_mapping(data: dict[str, Any]) -> RuntimeConfig:
    fw = data.get("firewall", {})
    rl = data.get("ratelimit", {})
    ac = data.get("access", {})
    for name, table in (("firewall", fw), ("ratelimit", rl), ("access", ac)):
        if not isinstance(table, dict):
            raise ValueError(f"[{name}] must be a TOML table.")
    mode = fw.get("mode", "enforce")
    if mode not in {"enforce", "monitor"}:
        raise ValueError("firewall.mode must be 'enforce' or 'monitor'.")
    profile = fw.get("signature_profile", "balanced")
    if profile not in {"off", "balanced", "strict"}:
        raise ValueError("firewall.signature_profile must be 'off', 'balanced' or 'strict'.")
    methods = tuple(m.upper() for m in _strings(fw, "allowed_methods", DEFAULT_METHODS))
    if not methods or not all(re.fullmatch(r"[A-Z]{3,10}", m) for m in methods):
        raise ValueError("firewall.allowed_methods must list HTTP method names.")
    skip = tuple(_prefix(p, "skip_signature_paths") for p in _strings(fw, "skip_signature_paths", ()))
    routes = []
    for item in rl.get("routes", []):
        routes.append(RouteLimit(_prefix(item.get("prefix"), "ratelimit.routes"),
                                 _int(item, "requests_per_minute", 60, 1, 100_000), _int(item, "burst", 10, 1, 100_000)))
    keys = []
    for item in ac.get("keys", []):
        digest = str(item.get("sha256", "")).lower()
        name, role = str(item.get("name", "")), str(item.get("role", "user"))
        if not _NAME.match(name) or not _NAME.match(role) or not _HEX64.match(digest):
            raise ValueError("access.keys entries need name, role and a 64-hex sha256 (never a plaintext key).")
        keys.append(ApiKey(name, digest, role))
    keys.extend(_env_keys())
    rules = []
    for item in ac.get("rules", []):
        require = item.get("require", "api_key")
        if require not in {"none", "api_key"}:
            raise ValueError("access.rules.require must be 'none' or 'api_key'.")
        rules.append(AccessRule(_prefix(item.get("prefix"), "access.rules"),
                                tuple(m.upper() for m in _strings(item, "methods", ("*",))), require,
                                _strings(item, "roles", ()), _networks(_strings(item, "ip_allow", ()), "access.rules.ip_allow")))
    return RuntimeConfig(
        mode=mode, allowed_methods=methods,
        max_url_length=_int(fw, "max_url_length", 4096, 64, 65536),
        max_header_count=_int(fw, "max_header_count", 100, 5, 1000),
        max_header_bytes=_int(fw, "max_header_bytes", 16384, 512, 1_048_576),
        max_body_bytes=_int(fw, "max_body_bytes", 1_048_576, 0, 1_073_741_824),
        inspect_body_bytes=_int(fw, "inspect_body_bytes", 65_536, 0, 4_194_304),
        allowed_hosts=tuple(h.lower() for h in _strings(fw, "allowed_hosts", ())),
        trusted_proxies=_networks(_strings(fw, "trusted_proxies", ()), "trusted_proxies"),
        block_ips=_networks(_strings(fw, "block_ips", ()), "block_ips"),
        allow_ips=_networks(_strings(fw, "allow_ips", ()), "allow_ips"),
        block_user_agents=tuple(a.lower() for a in _strings(fw, "block_user_agents", DEFAULT_BAD_AGENTS)),
        signature_profile=profile, disabled_rules=_strings(fw, "disabled_rules", ()), skip_signature_paths=skip,
        security_headers=_bool(fw, "security_headers", True), expose_rule_id=_bool(fw, "expose_rule_id", False),
        ratelimit_enabled=_bool(rl, "enabled", True),
        requests_per_minute=_int(rl, "requests_per_minute", 300, 1, 1_000_000), burst=_int(rl, "burst", 60, 1, 1_000_000),
        max_tracked_clients=_int(rl, "max_tracked_clients", 10_000, 100, 1_000_000), route_limits=tuple(routes),
        access_enabled=_bool(ac, "enabled", False), api_keys=tuple(keys), access_rules=tuple(rules),
    )


def default_config_path() -> Path | None:
    explicit = os.environ.get(CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = shield_home()
    if home is not None:
        candidate = home / "guard" / "runtime" / "runtime.toml"
        if candidate.is_file():
            return candidate
    return None


def load_runtime_config(path: str | Path | None = None) -> RuntimeConfig:
    """Load and validate runtime.toml; with no file the secure built-in defaults apply."""
    target = Path(path).expanduser() if path else default_config_path()
    if target is None:
        return from_mapping({})
    if not target.is_file() or target.stat().st_size > 262_144:
        raise ValueError(f"Runtime config must be a regular file under 256 KiB: {target}")
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Invalid runtime config {target}: {exc}") from exc
    return from_mapping(data)

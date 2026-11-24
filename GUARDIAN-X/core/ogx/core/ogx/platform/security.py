"""Authentication and endpoint security boundary.

Rules enforced here (and covered by tests):

* Credentials come only from the provider's own environment variables.
* A provider credential is never reused for another provider.
* Credentials are never placed in URLs.
* HTTPS is mandatory; HTTP is allowed only for explicitly permitted loopback.
* Custom endpoints are validated for SSRF: credentials in URL, non-HTTP
  schemes, private/link-local/metadata address literals are rejected.
* Secret values are redacted from arbitrary text and mapping payloads.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from .errors import InvalidEndpointError

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

#: Hosts that serve cloud instance credentials; never a valid provider endpoint.
METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "instance-data",
        "instance-data.ec2.internal",
        "169.254.169.254",
        "fd00:ec2::254",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(bearer|token|basic)\s+[A-Za-z0-9._\-+/=]{12,}"),
    re.compile(r"(?i)\b(?:authorization|private-token|x-api-key)\s*[:=]\s*\S+"),
)

_REDACTED = "[REDACTED]"


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove known secret values and common token shapes from ``text``."""

    if not text:
        return ""
    result = str(text)
    for secret in secrets:
        if secret and len(str(secret)) >= 4:
            result = result.replace(str(secret), _REDACTED)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


def redact_mapping(payload: Mapping[str, object] | None, secrets: Iterable[str] = ()) -> dict:
    """Recursively redact mapping values; never returns header/credential values."""

    safe_secrets = tuple(secrets)
    result: dict[str, object] = {}
    if not payload:
        return result
    for key, value in payload.items():
        lowered = str(key).lower()
        if lowered in {"authorization", "private-token", "x-api-key", "token", "password"}:
            result[str(key)] = _REDACTED
        elif isinstance(value, Mapping):
            result[str(key)] = redact_mapping(value, safe_secrets)
        elif isinstance(value, (list, tuple)):
            result[str(key)] = [
                redact(str(item), safe_secrets) if isinstance(item, str) else item
                for item in value
            ]
        elif isinstance(value, str):
            result[str(key)] = redact(value, safe_secrets)
        else:
            result[str(key)] = value
    return result


def _is_loopback(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_private_or_special(host: str) -> bool:
    normalized = host.strip("[]").lower()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_reserved,
            address.is_multicast,
            address.is_unspecified,
        )
    )


def validate_api_base(
    value: str,
    *,
    allow_loopback_http: bool = False,
    allow_private_network: bool = False,
) -> str:
    """Validate and normalize a provider API base URL.

    Raises :class:`InvalidEndpointError` rather than returning a usable URL for
    an unsafe endpoint.
    """

    if not isinstance(value, str) or not value.strip():
        raise InvalidEndpointError("Provider API base URL must be a non-empty string.")
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"https", "http"}:
        raise InvalidEndpointError(
            "Provider API base URL must use https (http is allowed only for loopback development)."
        )
    if not parsed.hostname:
        raise InvalidEndpointError("Provider API base URL must include a hostname.")
    if parsed.username or parsed.password:
        raise InvalidEndpointError("Provider API base URL must not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise InvalidEndpointError("Provider API base URL must not contain a query or fragment.")
    host = parsed.hostname.lower()
    if host in METADATA_HOSTS:
        raise InvalidEndpointError("Cloud metadata hosts are not valid provider endpoints.")
    loopback = _is_loopback(host)
    if parsed.scheme == "http" and not (loopback and allow_loopback_http):
        raise InvalidEndpointError(
            "Provider credentials may only be sent over HTTPS; HTTP requires an explicitly "
            "permitted loopback development endpoint."
        )
    if not allow_private_network and not loopback and _is_private_or_special(host):
        raise InvalidEndpointError(
            "Provider API base URL resolves to a private or special-purpose address; "
            "set OGX_ALLOW_PRIVATE_PROVIDER_ENDPOINTS=1 to opt in explicitly."
        )
    if loopback and not allow_loopback_http and parsed.scheme == "https":
        # HTTPS on loopback is fine and often self-signed; no extra restriction.
        pass
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return normalized


def validate_request_url(
    url: str,
    *,
    expected_host: str,
    allow_loopback_http: bool = False,
    allow_private_network: bool = False,
) -> str:
    """Validate a request URL and ensure it targets the configured provider host.

    Prevents a paginated ``next`` link (or a redirect) from sending the
    credential to an unrelated host.
    """

    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise InvalidEndpointError("Provider request URL must be an absolute http(s) URL.")
    if parsed.username or parsed.password:
        raise InvalidEndpointError("Provider request URLs must not contain embedded credentials.")
    if parsed.hostname.lower() != expected_host.lower():
        raise InvalidEndpointError(
            "Provider response pointed at a different host; refusing to send credentials there."
        )
    if not allow_private_network:
        validate_api_base(
            f"{parsed.scheme}://{parsed.netloc}",
            allow_loopback_http=allow_loopback_http,
            allow_private_network=False,
        )
    return url


@dataclass(frozen=True, slots=True)
class CredentialPolicy:
    """Per-provider credential source. Never shared across providers."""

    provider_id: str
    env_vars: tuple[str, ...]
    username_env: str | None = None

    def resolve(self, environ: Mapping[str, str] | None = None) -> str | None:
        env = environ if environ is not None else os.environ
        for name in self.env_vars:
            value = env.get(name)
            if value and value.strip():
                return value.strip()
        return None

    def source(self, environ: Mapping[str, str] | None = None) -> str | None:
        env = environ if environ is not None else os.environ
        for name in self.env_vars:
            if env.get(name, "").strip():
                return name
        return None

    def to_dict(self) -> dict[str, object]:
        # Environment variable *names* are safe; values never appear here.
        return {
            "provider_id": self.provider_id,
            "credential_env": list(self.env_vars),
            "username_env": self.username_env,
        }


def split_credential_pair(value: str) -> tuple[str, str] | None:
    """Split ``user:secret`` credential forms (Bitbucket app passwords)."""

    if not isinstance(value, str) or ":" not in value:
        return None
    username, _, secret = value.partition(":")
    if username and secret:
        return username, secret
    return None


def basic_auth_value(username: str, secret: str) -> str:
    import base64

    raw = f"{username}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")

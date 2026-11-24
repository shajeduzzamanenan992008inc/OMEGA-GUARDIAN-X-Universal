"""Normalized provider error taxonomy.

Provider-specific HTTP and API failures are translated here so callers never
need to inspect a vendor payload. Messages must already be redacted by the
transport; this module additionally bounds and sanitizes detail text.
"""

from __future__ import annotations

from typing import Any


def _redact(value: str) -> str:
    # Imported lazily: security imports this module for its error type.
    from .security import redact

    return redact(value)


class ProviderError(Exception):
    """Base class for every normalized provider failure."""

    kind = "provider_error"
    retryable = False
    http_status: int | None = None

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status: int | None = None,
        correlation_id: str | None = None,
        capability: str | None = None,
        detail: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        safe = _redact(str(message))[:600]
        super().__init__(safe)
        self.message = safe
        self.provider = provider
        self.status = status if status is not None else self.http_status
        self.correlation_id = correlation_id
        self.capability = capability
        self.detail = _redact(detail)[:800] if detail else None
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.kind,
            "message": self.message,
            "retryable": self.retryable,
        }
        for key in ("provider", "status", "correlation_id", "capability", "detail"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.retry_after is not None:
            payload["retry_after_seconds"] = self.retry_after
        return payload


class AuthenticationError(ProviderError):
    kind = "authentication_failed"
    http_status = 401


class PermissionDeniedError(ProviderError):
    kind = "permission_denied"
    http_status = 403


class NotFoundError(ProviderError):
    kind = "not_found"
    http_status = 404


class ConflictError(ProviderError):
    kind = "conflict"
    http_status = 409


class RateLimitError(ProviderError):
    kind = "rate_limited"
    retryable = True
    http_status = 429


class ServerError(ProviderError):
    kind = "provider_unavailable"
    retryable = True


class TransportError(ProviderError):
    kind = "connection_failed"
    retryable = True


class MalformedResponseError(ProviderError):
    kind = "malformed_response"


class ResponseTooLargeError(ProviderError):
    kind = "response_too_large"


class InvalidEndpointError(ProviderError):
    kind = "invalid_endpoint"


class CapabilityUnavailableError(ProviderError):
    kind = "capability_unavailable"


class UnsupportedOperationError(CapabilityUnavailableError):
    kind = "unsupported_operation"


class DuplicateEntityError(ProviderError):
    kind = "duplicate_entity"


class ApprovalRequiredError(ProviderError):
    kind = "approval_required"


def error_for_status(
    status: int,
    *,
    provider: str | None,
    correlation_id: str | None = None,
    detail: str | None = None,
    retry_after: float | None = None,
) -> ProviderError:
    """Map an HTTP status to the normalized taxonomy."""

    common = {
        "provider": provider,
        "status": status,
        "correlation_id": correlation_id,
        "detail": detail,
        "retry_after": retry_after,
    }
    if status == 401:
        return AuthenticationError(
            f"{provider or 'provider'} rejected the credential (HTTP 401).",
            **common,
        )
    if status == 403:
        return PermissionDeniedError(
            f"{provider or 'provider'} denied the operation (HTTP 403). "
            "The credential is valid but lacks the required scope/permission.",
            **common,
        )
    if status == 404:
        return NotFoundError(f"{provider or 'provider'} resource not found (HTTP 404).", **common)
    if status == 409:
        return ConflictError(f"{provider or 'provider'} reported a conflict (HTTP 409).", **common)
    if status == 429:
        return RateLimitError(
            f"{provider or 'provider'} rate limit exceeded (HTTP 429).", **common
        )
    if status in {400, 422}:
        return ProviderError(f"{provider or 'provider'} rejected the request (HTTP {status}).", **common)
    if 500 <= status <= 599:
        return ServerError(f"{provider or 'provider'} failed server-side (HTTP {status}).", **common)
    return ProviderError(f"{provider or 'provider'} returned HTTP {status}.", **common)

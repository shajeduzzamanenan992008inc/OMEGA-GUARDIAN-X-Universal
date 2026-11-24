"""Integration error taxonomy.

All contract violations, security issues, and normalization failures are
represented as typed exceptions with safe error messages.
"""

from __future__ import annotations

from typing import Any, Optional


class IntegrationError(Exception):
    """Base class for every integration contract error."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.capability = capability
        self.correlation_id = correlation_id
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.__class__.__name__,
            "message": self.message,
        }
        for key in ("provider", "capability", "correlation_id", "detail"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


class ContractViolationError(IntegrationError):
    """Raised when a provider violates the universal contract."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
        violated_contract: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail)
        self.violated_contract = violated_contract


class CapabilityMismatchError(ContractViolationError):
    """Raised when a declared capability doesn't match actual implementation."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
        expected: Optional[str] = None,
        actual: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violated_contract="capability_implementation")
        self.expected = expected
        self.actual = actual


class NormalizationError(IntegrationError):
    """Raised when provider response cannot be normalized to canonical models."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
        source_data: Optional[Any] = None,
        target_model: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail)
        self.source_data = source_data
        self.target_model = target_model


class SecurityViolationError(IntegrationError):
    """Raised when a security contract is violated."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
        violation_type: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail)
        self.violation_type = violation_type


class AuthenticationContractError(SecurityViolationError):
    """Raised when authentication contract is violated."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violation_type="authentication")


class AuthorizationContractError(SecurityViolationError):
    """Raised when authorization contract is violated."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violation_type="authorization")


class PaginationContractError(ContractViolationError):
    """Raised when pagination behavior violates contract."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violated_contract="pagination")


class MetadataContractError(ContractViolationError):
    """Raised when metadata contract is violated."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violated_contract="metadata")


class RateLimitContractError(ContractViolationError):
    """Raised when rate limit behavior violates contract."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violated_contract="rate_limit")


class UnsupportedCapabilityContractError(ContractViolationError):
    """Raised when an unsupported capability is incorrectly reported."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violated_contract="capability_support")


class ReadOnlyContractViolationError(SecurityViolationError):
    """Raised when a read-only operation attempts to write."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violation_type="read_only_violation")


class CredentialLeakageError(SecurityViolationError):
    """Raised when credentials leak in logs, errors, or responses."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
        correlation_id: Optional[str] = None,
        detail: Optional[str] = None,
        leaked_field: Optional[str] = None,
    ) -> None:
        super().__init__(message, provider=provider, capability=capability, correlation_id=correlation_id, detail=detail, violation_type="credential_leakage")
        self.leaked_field = leaked_field
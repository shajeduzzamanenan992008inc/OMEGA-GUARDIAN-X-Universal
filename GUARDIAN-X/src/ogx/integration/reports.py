"""Provider compatibility report generation.

Creates machine-readable compatibility reports with standardized verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CompatibilityVerdict(str, Enum):
    """Standardized compatibility verdicts."""

    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_LIMITATIONS = "compatible_with_limitations"
    INCOMPATIBLE = "incompatible"
    NOT_VERIFIED = "not_verified"


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Machine-readable compatibility report for a provider."""

    provider_id: str
    provider_version: str
    contract_version: str = "1.0.0"
    verdict: CompatibilityVerdict = CompatibilityVerdict.NOT_VERIFIED
    compatible: bool = False
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unsupported_capabilities: list[str] = field(default_factory=list)
    security_warnings: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    check_details: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    test_environment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ogx-provider-compatibility-report-1",
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "contract_version": self.contract_version,
            "verdict": self.verdict.value,
            "compatible": self.compatible,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warnings": self.warnings,
            "unsupported_capabilities": self.unsupported_capabilities,
            "security_warnings": self.security_warnings,
            "known_limitations": self.known_limitations,
            "check_details": self.check_details,
            "timestamp": self.timestamp,
            "test_environment": self.test_environment,
        }

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_verification_result(cls, result: Any) -> "CompatibilityReport":
        """Create report from VerificationResult."""
        verdict_map = {
            "compatible": CompatibilityVerdict.COMPATIBLE,
            "compatible_with_limitations": CompatibilityVerdict.COMPATIBLE_WITH_LIMITATIONS,
            "incompatible": CompatibilityVerdict.INCOMPATIBLE,
            "not_verified": CompatibilityVerdict.NOT_VERIFIED,
        }

        return cls(
            provider_id=result.provider_id,
            provider_version=result.provider_version,
            contract_version=result.contract_version,
            verdict=verdict_map.get(result.verdict, CompatibilityVerdict.NOT_VERIFIED),
            compatible=result.compatible,
            passed_checks=result.passed_checks,
            failed_checks=result.failed_checks,
            warnings=result.warnings,
            unsupported_capabilities=result.unsupported_capabilities,
            security_warnings=result.security_warnings,
            known_limitations=result.known_limitations,
            check_details=[
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "detail": c.detail,
                    "severity": c.severity,
                }
                for c in result.check_details
            ],
            timestamp=result.timestamp,
            test_environment=result.test_environment,
        )

    def save(self, path: str) -> None:
        """Save report to file."""
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "CompatibilityReport":
        """Load report from file."""
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            provider_id=data["provider_id"],
            provider_version=data["provider_version"],
            contract_version=data.get("contract_version", "1.0.0"),
            verdict=CompatibilityVerdict(data["verdict"]),
            compatible=data["compatible"],
            passed_checks=data["passed_checks"],
            failed_checks=data["failed_checks"],
            warnings=data["warnings"],
            unsupported_capabilities=data["unsupported_capabilities"],
            security_warnings=data["security_warnings"],
            known_limitations=data["known_limitations"],
            check_details=data["check_details"],
            timestamp=data["timestamp"],
            test_environment=data.get("test_environment", {}),
        )


@dataclass(frozen=True, slots=True)
class CapabilityMatrixReport:
    """Machine-readable capability matrix for multiple providers."""

    schema: str = "ogx-provider-capability-matrix-1"
    provider_count: int = 0
    authenticated_context: bool = False
    providers: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "provider_count": self.provider_count,
            "authenticated_context": self.authenticated_context,
            "providers": self.providers,
            "timestamp": self.timestamp,
        }

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_descriptors(cls, descriptors: list[Any], *, authenticated: bool = False) -> "CapabilityMatrixReport":
        """Create matrix from provider descriptors."""
        providers = [d.to_dict(authenticated=authenticated) for d in descriptors]
        return cls(
            provider_count=len(providers),
            authenticated_context=authenticated,
            providers=providers,
        )


@dataclass(frozen=True, slots=True)
class IntegrationAuditEvent:
    """Structured integration audit event."""

    event_type: str
    provider_id: str
    correlation_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: dict[str, Any] = field(default_factory=dict)

    # Event types
    PROVIDER_CONNECTED = "provider_connected"
    AUTHENTICATION_VERIFIED = "authentication_verified"
    AUTHENTICATION_FAILED = "authentication_failed"
    CAPABILITY_CHECKED = "capability_checked"
    REPOSITORY_DISCOVERED = "repository_discovered"
    REPOSITORY_INSPECTED = "repository_inspected"
    OPERATION_APPROVED = "operation_approved"
    OPERATION_REJECTED = "operation_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMIT_ENCOUNTERED = "rate_limit_encountered"
    SECURITY_VIOLATION = "security_violation"
    CONTRACT_VIOLATION = "contract_violation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "provider_id": self.provider_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "details": self.details,
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False)


class AuditLogger:
    """Safe audit event logger."""

    def __init__(self) -> None:
        self._events: list[IntegrationAuditEvent] = []

    def log(self, event: IntegrationAuditEvent) -> None:
        """Log an audit event."""
        # Ensure no secrets in details
        safe_details = self._sanitize_details(event.details)
        self._events.append(IntegrationAuditEvent(
            event_type=event.event_type,
            provider_id=event.provider_id,
            correlation_id=event.correlation_id,
            timestamp=event.timestamp,
            details=safe_details,
        ))

    def _sanitize_details(self, details: dict[str, Any]) -> dict[str, Any]:
        """Remove secrets from details."""
        from ogx.platform.security import redact_mapping
        return redact_mapping(details)

    def get_events(self, provider_id: Optional[str] = None, event_type: Optional[str] = None) -> list[IntegrationAuditEvent]:
        """Get filtered events."""
        events = self._events
        if provider_id:
            events = [e for e in events if e.provider_id == provider_id]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events

    def export(self, path: str) -> None:
        """Export events to file."""
        import json
        data = [e.to_dict() for e in self._events]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def generate_compatibility_report(
    provider_id: str,
    provider_version: str,
    verification_result: Any,
    contract_version: str = "1.0.0",
) -> CompatibilityReport:
    """Generate a compatibility report from verification result."""
    return CompatibilityReport.from_verification_result(verification_result)


def generate_capability_matrix(
    descriptors: list[Any],
    *,
    authenticated: bool = False,
) -> CapabilityMatrixReport:
    """Generate a capability matrix report."""
    return CapabilityMatrixReport.from_descriptors(descriptors, authenticated=authenticated)
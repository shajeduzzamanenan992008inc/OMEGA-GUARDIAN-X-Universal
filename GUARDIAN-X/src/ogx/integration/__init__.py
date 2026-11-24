"""Integration Contract Verification Engine.

This package provides the canonical contract validation subsystem that ensures
all provider adapters conform to the Universal Provider Contract.

Also re-exports legacy integration utilities for backward compatibility with
the CLI and other parts of the codebase.
"""

from __future__ import annotations

# Core verification engine
from .verifier import ContractVerifier, VerificationResult, verify_provider

# Normalized data models
from .models import (
    NormalizedRepository,
    NormalizedBranch,
    NormalizedCommit,
    NormalizedFile,
    NormalizedTreeEntry,
    NormalizedPullRequest,
    NormalizedIssue,
    NormalizedUser,
    NormalizedPermission,
    NormalizedPagination,
    NormalizedRateLimit,
    NormalizedHealthStatus,
    NormalizedOperationResult,
    NormalizedProviderError,
    IntegrationModel,
)

# Integration error taxonomy
from .errors import (
    IntegrationError,
    ContractViolationError,
    CapabilityMismatchError,
    NormalizationError,
    SecurityViolationError,
    AuthenticationContractError,
    AuthorizationContractError,
    PaginationContractError,
    MetadataContractError,
    UnsupportedCapabilityContractError,
    ReadOnlyContractViolationError,
    CredentialLeakageError,
)

# Reusable test suite
from .testing import ContractTestSuite, ProviderTestHarness

# Compatibility reports
from .reports import (
    CompatibilityReport,
    CompatibilityVerdict,
    CapabilityMatrixReport,
    IntegrationAuditEvent,
    AuditLogger,
    generate_compatibility_report,
    generate_capability_matrix,
)

# Security validator
from .security import SecurityValidator

# Legacy integration utilities (for backward compatibility with CLI and other imports)
from . import legacy_integration  # noqa: F401

# Make legacy utilities available at package level
apply_integration = legacy_integration.apply_integration
integration_status = legacy_integration.integration_status
plan_integration = legacy_integration.plan_integration
remove_integration = legacy_integration.remove_integration

# Make legacy utility functions also available
__all__ = [
    # Verification
    "ContractVerifier",
    "VerificationResult",
    "verify_provider",
    # Normalized models
    "NormalizedRepository",
    "NormalizedBranch",
    "NormalizedCommit",
    "NormalizedFile",
    "NormalizedTreeEntry",
    "NormalizedPullRequest",
    "NormalizedIssue",
    "NormalizedUser",
    "NormalizedPermission",
    "NormalizedPagination",
    "NormalizedRateLimit",
    "NormalizedHealthStatus",
    "NormalizedOperationResult",
    "NormalizedProviderError",
    # Errors
    "IntegrationError",
    "ContractViolationError",
    "CapabilityMismatchError",
    "NormalizationError",
    "SecurityViolationError",
    "AuthenticationContractError",
    "AuthorizationContractError",
    "PaginationContractError",
    "MetadataContractError",
    "UnsupportedCapabilityContractError",
    "ReadOnlyContractViolationError",
    "CredentialLeakageError",
    # Test suite
    "ContractTestSuite",
    "ProviderTestHarness",
    # Reports
    "CompatibilityReport",
    "CompatibilityVerdict",
    "CapabilityMatrixReport",
    "IntegrationAuditEvent",
    "AuditLogger",
    "generate_compatibility_report",
    "generate_capability_matrix",
    # Legacy exports
    "apply_integration",
    "integration_status",
    "plan_integration",
    "remove_integration",
    # Security
    "SecurityValidator",
]


# ---------------------------------------------------------------------------
# Compatibility bridge
# ---------------------------------------------------------------------------
# This package used to shadow a module of the same name. The project-folder
# workflow now lives in the unambiguous ``ogx.project_integration`` module; the
# package keeps re-exporting it for older imports without duplicating code.

_LEGACY_EXPORTS = frozenset(
    {
        "FOLDER",
        "MANIFEST",
        "MARKER",
        "apply_integration",
        "integration_status",
        "plan_integration",
        "remove_integration",
    }
)
def _load_legacy():
    from .. import project_integration

    return project_integration


def __getattr__(name: str):
    if name in _LEGACY_EXPORTS:
        return getattr(_load_legacy(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

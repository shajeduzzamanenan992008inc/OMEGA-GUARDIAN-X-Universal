"""Universal multi-platform provider engine.

The core exposes one normalized contract (:class:`ProviderAdapter`), one
registry, explicit capability negotiation, a shared reliability/security layer
and an approval-gated write path. Provider-specific API logic lives only in
``ogx.platform.adapters``; no adapter imports another adapter.
"""

from __future__ import annotations

from .base import CAPABILITY_METHODS, ProviderAdapter
from .capabilities import (
    UNAUTHENTICATED_CAPABILITIES,
    WRITE_CAPABILITIES,
    Capability,
    CapabilityState,
    ProviderDescriptor,
    ProviderKind,
    build_capability_matrix,
)
from .errors import (
    ApprovalRequiredError,
    AuthenticationError,
    CapabilityUnavailableError,
    DuplicateEntityError,
    InvalidEndpointError,
    MalformedResponseError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    RateLimitError,
    TransportError,
    UnsupportedOperationError,
)
from .identity import (
    assert_relocation_safe,
    deterministic_repository_id,
    link_repository_to_project,
    normalize_repository_url,
    project_for_repository,
)
from .models import (
    AuthStatus,
    Branch,
    Commit,
    FileContent,
    HealthStatus,
    Issue,
    OperationResult,
    PermissionSet,
    PullRequest,
    RateLimit,
    RepositoryMetadata,
    RepositoryRef,
    TreeEntry,
    WritePlan,
)
from .operations import (
    FORBIDDEN_OPERATIONS,
    append_audit,
    execute_pull_request,
    plan_pull_request,
    read_audit,
)
from .registry import UniversalProviderRegistry, get_provider_registry
from .security import CredentialPolicy, redact, redact_mapping, validate_api_base
from .transport import HttpTransport
from .webhooks import (
    SUPPORTED_PROVIDERS,
    DeliveryReplayGuard,
    VerifiedWebhook,
    WebhookVerificationError,
    WebhookVerifier,
)

__all__ = [
    "ProviderAdapter",
    "CAPABILITY_METHODS",
    "Capability",
    "CapabilityState",
    "ProviderDescriptor",
    "ProviderKind",
    "build_capability_matrix",
    "WRITE_CAPABILITIES",
    "UNAUTHENTICATED_CAPABILITIES",
    "ProviderError",
    "AuthenticationError",
    "PermissionDeniedError",
    "CapabilityUnavailableError",
    "UnsupportedOperationError",
    "ApprovalRequiredError",
    "DuplicateEntityError",
    "InvalidEndpointError",
    "MalformedResponseError",
    "NotFoundError",
    "RateLimitError",
    "TransportError",
    "AuthStatus",
    "Branch",
    "Commit",
    "FileContent",
    "HealthStatus",
    "Issue",
    "OperationResult",
    "PermissionSet",
    "PullRequest",
    "RateLimit",
    "RepositoryMetadata",
    "RepositoryRef",
    "TreeEntry",
    "WritePlan",
    "append_audit",
    "execute_pull_request",
    "plan_pull_request",
    "read_audit",
    "FORBIDDEN_OPERATIONS",
    "link_repository_to_project",
    "normalize_repository_url",
    "project_for_repository",
    "deterministic_repository_id",
    "assert_relocation_safe",
    "UniversalProviderRegistry",
    "get_provider_registry",
    "CredentialPolicy",
    "redact",
    "redact_mapping",
    "validate_api_base",
    "HttpTransport",
    "SUPPORTED_PROVIDERS",
    "DeliveryReplayGuard",
    "VerifiedWebhook",
    "WebhookVerificationError",
    "WebhookVerifier",
]

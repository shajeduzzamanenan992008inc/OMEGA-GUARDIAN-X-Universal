"""Universal provider capability vocabulary and negotiation states.

The capability matrix is *derived from adapter code*, never hand-maintained.
An operation is only reported as ``supported`` when the adapter actually
overrides the corresponding method (see :mod:`ogx.platform.base`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class ProviderKind(str, Enum):
    """Independent adapter families. The core never branches on provider id."""

    GIT_HOSTING = "git_hosting"
    CLOUD_WORKSPACE = "cloud_workspace"
    CICD = "cicd"
    PROJECT_MANAGEMENT = "project_management"
    PACKAGE_REGISTRY = "package_registry"
    CLOUD_INFRASTRUCTURE = "cloud_infrastructure"


class CapabilityState(str, Enum):
    """Explicit negotiation state for one provider capability.

    Blocking states are first-class: a missing capability is never reported as
    a successful operation.
    """

    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    REQUIRES_AUTH = "requires_auth"
    REQUIRES_PERMISSION = "requires_permission"
    REQUIRES_WORKSPACE = "requires_workspace"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"

    @property
    def is_available(self) -> bool:
        return self in {CapabilityState.SUPPORTED, CapabilityState.PARTIAL}

    @property
    def is_blocking(self) -> bool:
        return not self.is_available


class Capability(str, Enum):
    """Normalized provider operations shared by every adapter family."""

    # Identity / negotiation
    IDENTIFY = "identify"
    AUTH_DISCOVERY = "auth_discovery"
    HEALTH_CHECK = "health_check"
    RATE_LIMIT_READ = "rate_limit_read"
    PAGINATION = "pagination"
    API_VERSION = "api_version"
    PERMISSION_READ = "permission_read"

    # Git hosting: read
    REPOSITORY_DISCOVERY = "repository_discovery"
    REPOSITORY_METADATA = "repository_metadata"
    BRANCH_LIST = "branch_list"
    COMMIT_LIST = "commit_list"
    COMMIT_INSPECT = "commit_inspect"
    FILE_READ = "file_read"
    TREE_READ = "tree_read"
    PULL_REQUEST_LIST = "pull_request_list"
    ISSUE_LIST = "issue_list"

    # Git hosting: write (approval-gated)
    PULL_REQUEST_CREATE = "pull_request_create"
    ISSUE_CREATE = "issue_create"
    ISSUE_UPDATE = "issue_update"

    # Workspaces
    WORKSPACE_DISCOVERY = "workspace_discovery"
    WORKSPACE_INSPECT = "workspace_inspect"
    WORKSPACE_START = "workspace_start"
    WORKSPACE_STOP = "workspace_stop"

    # CI/CD
    PIPELINE_LIST = "pipeline_list"
    PIPELINE_INSPECT = "pipeline_inspect"
    PIPELINE_TRIGGER = "pipeline_trigger"

    # Project management
    WORK_ITEM_LIST = "work_item_list"
    WORK_ITEM_INSPECT = "work_item_inspect"
    WORK_ITEM_CREATE = "work_item_create"

    # Package registries
    PACKAGE_LOOKUP = "package_lookup"
    PACKAGE_VERSION_LIST = "package_version_list"
    PACKAGE_PUBLISH = "package_publish"

    # Cloud infrastructure
    CLOUD_RESOURCE_LIST = "cloud_resource_list"
    CLOUD_RESOURCE_INSPECT = "cloud_resource_inspect"


#: Capabilities that do not require a credential to be attempted.
UNAUTHENTICATED_CAPABILITIES: frozenset[Capability] = frozenset(
    {Capability.IDENTIFY, Capability.AUTH_DISCOVERY, Capability.API_VERSION}
)

#: Capabilities that change remote state and therefore require approval.
WRITE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.PULL_REQUEST_CREATE,
        Capability.ISSUE_CREATE,
        Capability.ISSUE_UPDATE,
        Capability.WORKSPACE_START,
        Capability.WORKSPACE_STOP,
        Capability.PIPELINE_TRIGGER,
        Capability.WORK_ITEM_CREATE,
        Capability.PACKAGE_PUBLISH,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Stable, serializable identity and capability declaration of a provider."""

    provider_id: str
    display_name: str
    kind: ProviderKind
    version: str
    api_version: str
    default_api_base: str
    credential_env: tuple[str, ...]
    self_hosted: bool
    capabilities: Mapping[Capability, CapabilityState]
    docs_url: str = ""
    implementation_status: str = "verified"
    limitations: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    pagination: bool = True

    def capability_state(
        self, capability: Capability, *, authenticated: bool, configured: bool = True
    ) -> CapabilityState:
        state = self.capabilities.get(capability, CapabilityState.UNSUPPORTED)
        if not configured:
            return CapabilityState.NOT_CONFIGURED
        if state.is_available and capability not in UNAUTHENTICATED_CAPABILITIES and not authenticated:
            return CapabilityState.REQUIRES_AUTH
        return state

    def is_write(self, capability: Capability) -> bool:
        return capability in WRITE_CAPABILITIES

    def to_dict(self, *, authenticated: bool = False, configured: bool = True) -> dict[str, Any]:
        states: dict[str, str] = {}
        supported: list[str] = []
        partial: list[str] = []
        unsupported: list[str] = []
        for capability in Capability:
            if capability not in self.capabilities:
                continue
            state = self.capability_state(
                capability, authenticated=authenticated, configured=configured
            )
            states[capability.value] = state.value
            if state is CapabilityState.SUPPORTED:
                supported.append(capability.value)
            elif state is CapabilityState.PARTIAL:
                partial.append(capability.value)
            elif state is CapabilityState.UNSUPPORTED:
                unsupported.append(capability.value)
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "version": self.version,
            "api_version": self.api_version,
            "default_api_base": self.default_api_base,
            "credential_env": list(self.credential_env),
            "self_hosted": self.self_hosted,
            "docs_url": self.docs_url,
            "implementation_status": self.implementation_status,
            "limitations": list(self.limitations),
            "required_permissions": list(self.required_permissions),
            "pagination": self.pagination,
            "supported_operations": supported,
            "partial_operations": partial,
            "unsupported_operations": unsupported,
            "write_operations": sorted(
                c.value for c in self.capabilities if c in WRITE_CAPABILITIES
            ),
            "capability_states": states,
        }


def build_capability_matrix(
    descriptors: Iterable[ProviderDescriptor], *, authenticated: bool = False
) -> dict[str, Any]:
    """Machine-readable matrix for ``ogx platform capabilities``."""

    entries = [d.to_dict(authenticated=authenticated) for d in descriptors]
    return {
        "schema": "ogx-provider-capability-matrix-1",
        "provider_count": len(entries),
        "authenticated_context": authenticated,
        "providers": entries,
    }


def merge_states(*states: CapabilityState) -> CapabilityState:
    """Return the most restrictive state (used when combining declarations)."""

    unique = list(dict.fromkeys(states))
    for candidate in (
        CapabilityState.FAILED,
        CapabilityState.UNAVAILABLE,
        CapabilityState.NOT_CONFIGURED,
        CapabilityState.REQUIRES_PERMISSION,
        CapabilityState.REQUIRES_WORKSPACE,
        CapabilityState.REQUIRES_AUTH,
        CapabilityState.UNSUPPORTED,
        CapabilityState.PARTIAL,
        CapabilityState.SUPPORTED,
    ):
        if candidate in unique:
            return candidate
    return CapabilityState.UNSUPPORTED

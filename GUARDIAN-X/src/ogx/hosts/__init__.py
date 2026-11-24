from __future__ import annotations

from .models import (
    HostRecord,
    HostType,
    HostStatus,
    HostCapability,
    ProviderType,
    WorkspaceProviderType,
    WorkspaceType,
    CapabilityStatus,
)
from .interfaces import (
    HostProvider,
    RepositoryProvider,
    WorkspaceProvider,
)
from .registry import HostRegistry, get_host_registry
from .capabilities import CAPABILITY_MATRIX, get_host_capabilities, get_supported_capabilities
from .adapters.local_filesystem import LocalFilesystemProvider
from .adapters.local_git import LocalGitProvider
from .adapters.github import GitHubProvider

__all__ = [
    "HostRecord",
    "HostType",
    "HostStatus",
    "HostCapability",
    "ProviderType",
    "WorkspaceProviderType",
    "WorkspaceType",
    "CapabilityStatus",
    "HostProvider",
    "RepositoryProvider",
    "WorkspaceProvider",
    "HostRegistry",
    "get_host_registry",
    "CAPABILITY_MATRIX",
    "get_host_capabilities",
    "get_supported_capabilities",
    "LocalFilesystemProvider",
    "LocalGitProvider",
    "GitHubProvider",
]
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional, AsyncIterator
from pathlib import Path
from uuid import UUID

from .models import (
    HostType,
    ProviderType,
    HostCapability,
    CapabilityStatus,
    RepositoryProviderInfo,
    WorkspaceProviderInfo,
    WorkspaceType,
    WorkspaceProviderType,
    HostRecord,
)


@dataclass(frozen=True, slots=True)
class RepositoryMetadata:
    """Metadata about a repository."""
    repository_id: str
    name: str
    full_name: str
    description: Optional[str]
    owner: str
    owner_type: str  # user, organization
    default_branch: str
    visibility: str  # public, private, internal
    archived: bool
    disabled: bool
    fork: bool
    language: Optional[str]
    languages: dict[str, int]
    stars: int
    forks: int
    watchers: int
    open_issues: int
    created_at: str
    updated_at: str
    pushed_at: Optional[str]
    clone_url: str
    ssh_url: str
    html_url: str
    size: int
    topics: List[str]
    license: Optional[str]
    permissions: dict
    metadata: dict


@dataclass(frozen=True, slots=True)
class BranchInfo:
    """Branch information."""
    name: str
    commit_sha: str
    protected: bool
    protected_by: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Commit information."""
    sha: str
    message: str
    author: str
    author_email: str
    date: str
    parents: List[str]
    tree_sha: str
    verification: Optional[dict] = None


@dataclass(frozen=True, slots=True)
class FileInfo:
    """File metadata."""
    path: str
    mode: str
    type: str  # blob, tree, commit
    sha: str
    size: int
    content: Optional[str] = None
    encoding: Optional[str] = None


@dataclass(frozen=True, slots=True)
class WorkspaceInfo:
    """Workspace metadata."""
    workspace_id: str
    name: str
    display_name: str
    provider_type: str
    workspace_type: str
    location: str
    status: str
    capabilities: List[str]
    owner: Optional[str] = None
    repository: Optional[str] = None
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    resources: dict = None  # cpu, memory, storage


@dataclass(frozen=True, slots=True)
class HostHealth:
    """Host health status."""
    host_id: str
    status: str  # healthy, degraded, down
    latency_ms: Optional[int] = None
    last_check: Optional[str] = None
    issues: List[str] = None
    details: dict = None


class RepositoryProvider(ABC):
    """Interface for repository providers."""

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Provider type identifier."""
        pass

    @property
    @abstractmethod
    def api_base(self) -> str:
        """API base URL."""
        pass

    @abstractmethod
    async def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        pass

    @abstractmethod
    async def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        pass

    @abstractmethod
    async def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        pass

    @abstractmethod
    async def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        pass

    @abstractmethod
    async def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        pass

    @abstractmethod
    async def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        pass

    @abstractmethod
    async def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        pass

    @abstractmethod
    async def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull/merge requests."""
        pass

    @abstractmethod
    async def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        pass

    @abstractmethod
    async def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        pass

    @abstractmethod
    async def get_provider_info(self) -> RepositoryProviderInfo:
        """Get provider information."""
        pass


class WorkspaceProvider(ABC):
    """Interface for workspace providers."""

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Provider type identifier."""
        pass

    @abstractmethod
    async def discover_workspaces(
        self,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover workspaces."""
        pass

    @abstractmethod
    async def inspect_workspace(self, workspace_id: str) -> dict:
        """Get workspace details."""
        pass

    @abstractmethod
    async def check_workspace_access(self, workspace_id: str) -> bool:
        """Check if workspace is accessible."""
        pass

    @abstractmethod
    async def get_workspace_capabilities(self, workspace_id: str) -> List[str]:
        """Get workspace capabilities."""
        pass

    @abstractmethod
    async def get_provider_info(self) -> WorkspaceProviderInfo:
        """Get provider information."""
        pass


class HostProvider(ABC):
    """Interface for host providers (both repository and workspace)."""

    @property
    @abstractmethod
    def host_type(self) -> str:
        """Host type identifier."""
        pass

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Provider type identifier."""
        pass

    @abstractmethod
    async def identify_host(self, host_identifier: str) -> Optional[dict]:
        """Identify and validate a host by its identifier."""
        pass

    @abstractmethod
    async def inspect_host(self, host_id: str) -> dict:
        """Inspect host details and health."""
        pass

    @abstractmethod
    async def get_capabilities(self, host_id: str) -> dict:
        """Get host capabilities."""
        pass

    @abstractmethod
    async def check_health(self, host_id: str) -> dict:
        """Check host health."""
        pass

    @abstractmethod
    async def list_resources(self, host_id: str, resource_type: str) -> List[dict]:
        """List resources (repositories, workspaces) on the host."""
        pass

    @abstractmethod
    async def get_provider_info(self) -> dict:
        """Get provider information."""
        pass

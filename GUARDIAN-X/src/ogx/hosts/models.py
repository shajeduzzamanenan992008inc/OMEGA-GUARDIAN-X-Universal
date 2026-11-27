from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID


class HostType(str, Enum):
    """Types of hosts that can host repositories or workspaces."""
    LOCAL_FILESYSTEM = "local_filesystem"
    LOCAL_GIT = "local_git"
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    GITEA = "gitea"
    FORGEJO = "forgejo"
    AZURE_DEVOPS = "azure_devops"
    AWS_CODECOMMIT = "aws_codecommit"
    GENERIC_GIT = "generic_git"
    REMOTE_WORKSPACE = "remote_workspace"
    REMOTE_SSH = "remote_ssh"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    CLOUD_WORKSPACE = "cloud_workspace"
    UNKNOWN = "unknown"


class ProviderType(str, Enum):
    """Repository hosting providers."""
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    GITEA = "gitea"
    FORGEJO = "forgejo"
    AZURE_DEVOPS = "azure_devops"
    AWS_CODECOMMIT = "aws_codecommit"
    GENERIC_GIT = "generic_git"
    LOCAL = "local"


class WorkspaceProviderType(str, Enum):
    """Workspace hosting providers."""
    LOCAL_FILESYSTEM = "local_filesystem"
    GITHUB_CODESPACES = "github_codespaces"
    GITPOD = "gitpod"
    AWS_CLOUD9 = "aws_cloud9"
    REMOTE_SSH = "remote_ssh"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    UNKNOWN = "unknown"


class WorkspaceType(str, Enum):
    """Types of workspaces."""
    LOCAL_FS = "local_fs"
    CODESPACE = "codespace"
    CONTAINER = "container"
    REMOTE_VM = "remote_vm"
    REMOTE_SSH = "remote_ssh"


class HostStatus(str, Enum):
    """Host availability status.

    ``REGISTERED`` means a record exists; it does not mean a live session.
    """
    REGISTERED = "registered"
    DISCONNECTED = "disconnected"
    FAILED = "failed"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class HostCapability(str, Enum):
    """Capabilities a host/provider may support."""
    REPOSITORY_METADATA_READ = "repository_metadata_read"
    REPOSITORY_STRUCTURE_READ = "repository_structure_read"
    BRANCH_READ = "branch_read"
    COMMIT_READ = "commit_read"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_CREATE = "workspace_create"
    WORKSPACE_DELETE = "workspace_delete"
    WORKSPACE_START = "workspace_start"
    WORKSPACE_STOP = "workspace_stop"
    WORKSPACE_EXEC = "workspace_exec"
    BRANCH_CREATE = "branch_create"
    BRANCH_DELETE = "branch_delete"
    COMMIT_CREATE = "commit_create"
    PULL_REQUEST_CREATE = "pull_request_create"
    PULL_REQUEST_MERGE = "pull_request_merge"
    PUSH = "push"
    FETCH = "fetch"
    CLONE = "clone"
    FILE_SYSTEM_ACCESS = "file_system_access"
    WORKSPACE_EXECUTE = "workspace_execute"
    TESTING = "testing"
    DEPLOYMENT = "deployment"
    REPOSITORY_WRITE = "repository_write"
    # Kubernetes-specific, read-only inspection capabilities (HOST-10).
    CLUSTER_INFO = "cluster_info"
    NAMESPACE_LIST = "namespace_list"
    NAMESPACE_METADATA = "namespace_metadata"
    POD_LIST = "pod_list"
    POD_METADATA = "pod_metadata"
    POD_LOGS = "pod_logs"
    DEPLOYMENT_LIST = "deployment_list"
    DEPLOYMENT_METADATA = "deployment_metadata"
    REPLICASET_LIST = "replicaset_list"
    REPLICASET_METADATA = "replicaset_metadata"
    STATEFULSET_LIST = "statefulset_list"
    STATEFULSET_METADATA = "statefulset_metadata"
    DAEMONSET_LIST = "daemonset_list"
    DAEMONSET_METADATA = "daemonset_metadata"
    JOB_LIST = "job_list"
    JOB_METADATA = "job_metadata"
    CRONJOB_LIST = "cronjob_list"
    CRONJOB_METADATA = "cronjob_metadata"
    SERVICE_LIST = "service_list"
    SERVICE_METADATA = "service_metadata"
    INGRESS_LIST = "ingress_list"
    INGRESS_METADATA = "ingress_metadata"
    NETWORKPOLICY_LIST = "networkpolicy_list"
    NETWORKPOLICY_METADATA = "networkpolicy_metadata"
    CONFIGMAP_METADATA = "configmap_metadata"
    SECRET_METADATA = "secret_metadata"
    PVC_LIST = "pvc_list"
    PVC_METADATA = "pvc_metadata"
    PV_METADATA = "pv_metadata"
    STORAGECLASS_LIST = "storageclass_list"
    STORAGECLASS_METADATA = "storageclass_metadata"


class CapabilityStatus(str, Enum):
    """Status of a capability."""
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    REQUIRES_AUTH = "requires_auth"
    REQUIRES_WORKSPACE = "requires_workspace"
    REQUIRES_PERMISSION = "requires_permission"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HostRecord:
    """Host registry record."""
    host_id: UUID
    host_type: HostType
    provider: ProviderType = ProviderType.LOCAL
    display_name: str = ""
    normalized_identifier: str = ""
    capabilities: List[HostCapability] = field(default_factory=list)
    status: HostStatus = HostStatus.UNKNOWN
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class RepositoryProviderInfo:
    """Information about a repository provider's capabilities."""
    provider_type: ProviderType
    api_base: str
    supported_operations: List[str] = field(default_factory=list)
    rate_limit: Optional[int] = None
    authentication_required: bool = True
    api_version: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceProviderInfo:
    """Information about a workspace provider's capabilities."""
    provider_type: WorkspaceProviderType
    supported_workspace_types: List[WorkspaceType] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    requires_authentication: bool = True
    max_workspaces: Optional[int] = None


@dataclass(frozen=True, slots=True)
class HostCapabilitySet:
    """Complete set of capabilities for a host."""
    host_id: UUID
    repository_capabilities: List[str] = field(default_factory=list)
    workspace_capabilities: List[str] = field(default_factory=list)
    filesystem_capabilities: List[str] = field(default_factory=list)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
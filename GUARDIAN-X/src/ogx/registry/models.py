from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


class ProjectLifecycleState(str, Enum):
    """Project lifecycle states."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class WorkspaceStatus(str, Enum):
    """Workspace status states."""
    ACTIVE = "active"
    INACCESSIBLE = "inaccessible"
    MISSING = "missing"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    """Project registry record."""
    project_id: UUID
    canonical_name: str
    description: str = ""
    identity_hash: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lifecycle_state: ProjectLifecycleState = ProjectLifecycleState.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepositoryRecord:
    """Repository registry record."""
    repository_id: UUID
    project_id: UUID
    provider: str
    repository_url: str
    normalized_repository_url: str
    repository_name: str = ""
    default_branch: str = ""
    visibility: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    """Workspace registry record."""
    workspace_id: UUID
    project_id: UUID
    repository_id: UUID | None = None
    workspace_provider: str = "local"
    workspace_type: str = "local_fs"
    location_reference: str = ""
    normalized_location: str = ""
    display_name: str = ""
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HostRecord:
    """Host registry record."""
    host_id: UUID
    host_type: str
    provider: str = ""
    host_identifier: str = ""
    status: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
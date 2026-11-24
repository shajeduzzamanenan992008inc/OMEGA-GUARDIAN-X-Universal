"""Normalized data models for universal provider integration.

Every provider adapter must return these shapes regardless of the vendor payload.
Fields that a provider cannot supply stay None/empty rather than being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


def _clean(value: Any) -> Any:
    """Recursively clean mapping/list values for serialization."""
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """Individual contract verification check result."""

    name: str
    passed: bool
    message: str = ""
    detail: str = ""
    severity: str = "info"  # "info", "warning", "error", "critical"


class IntegrationModel:
    """Base class for all integration models with serialization support."""

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntegrationModel":
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class NormalizedRepository(IntegrationModel):
    """Normalized repository metadata."""

    repository_id: str
    name: str
    full_name: str
    owner: str
    provider: str
    description: str = ""
    owner_type: str = "unknown"
    default_branch: str = ""
    visibility: str = "unknown"
    archived: bool = False
    disabled: bool = False
    fork: bool = False
    language: Optional[str] = None
    languages: Mapping[str, int] = field(default_factory=dict)
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pushed_at: Optional[str] = None
    clone_url: Optional[str] = None
    ssh_url: Optional[str] = None
    html_url: Optional[str] = None
    size_kb: int = 0
    topics: tuple[str, ...] = ()
    license: Optional[str] = None
    permissions: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}" if self.owner else self.name

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "repository_id": self.repository_id,
            "name": self.name,
            "full_name": self.full_name,
            "owner": self.owner,
            "provider": self.provider,
            "description": self.description,
            "owner_type": self.owner_type,
            "default_branch": self.default_branch,
            "visibility": self.visibility,
            "archived": self.archived,
            "disabled": self.disabled,
            "fork": self.fork,
            "language": self.language,
            "languages": dict(self.languages),
            "stars": self.stars,
            "forks": self.forks,
            "watchers": self.watchers,
            "open_issues": self.open_issues,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pushed_at": self.pushed_at,
            "clone_url": self.clone_url,
            "ssh_url": self.ssh_url,
            "html_url": self.html_url,
            "size_kb": self.size_kb,
            "topics": list(self.topics),
            "license": self.license,
            "permissions": dict(self.permissions),
            "extra": dict(self.extra),
        })


@dataclass(frozen=True, slots=True)
class NormalizedBranch(IntegrationModel):
    """Normalized branch metadata."""

    name: str
    commit_sha: str
    protected: bool = False
    protected_by: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "commit_sha": self.commit_sha,
            "protected": self.protected,
            "protected_by": self.protected_by,
        }


@dataclass(frozen=True, slots=True)
class NormalizedCommit(IntegrationModel):
    """Normalized commit metadata."""

    sha: str
    message: str
    author: str = ""
    author_email: str = ""
    authored_at: Optional[str] = None
    committer: str = ""
    parents: tuple[str, ...] = ()
    url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "message": self.message,
            "author": self.author,
            "author_email": self.author_email,
            "authored_at": self.authored_at,
            "committer": self.committer,
            "parents": list(self.parents),
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class NormalizedFile(IntegrationModel):
    """Normalized file content metadata."""

    path: str
    sha: str
    size: int
    content: str
    encoding: str = "utf-8"
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha": self.sha,
            "size": self.size,
            "content": self.content,
            "encoding": self.encoding,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class NormalizedTreeEntry(IntegrationModel):
    """Normalized tree entry metadata."""

    path: str
    type: str  # "file", "dir", "submodule", etc.
    sha: str = ""
    size: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "type": self.type, "sha": self.sha, "size": self.size}


@dataclass(frozen=True, slots=True)
class NormalizedPullRequest(IntegrationModel):
    """Normalized pull/merge request metadata."""

    pull_request_id: str
    number: Optional[int]
    title: str
    state: str  # "open", "closed", "merged", "draft"
    source_branch: str = ""
    target_branch: str = ""
    url: Optional[str] = None
    author: str = ""
    draft: bool = False
    created_at: Optional[str] = None
    merged: bool = False
    merged_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pull_request_id": self.pull_request_id,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "url": self.url,
            "author": self.author,
            "draft": self.draft,
            "created_at": self.created_at,
            "merged": self.merged,
            "merged_at": self.merged_at,
        }


@dataclass(frozen=True, slots=True)
class NormalizedIssue(IntegrationModel):
    """Normalized issue metadata."""

    issue_id: str
    number: Optional[int]
    title: str
    state: str  # "open", "closed"
    url: Optional[str] = None
    author: str = ""
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "author": self.author,
            "labels": list(self.labels),
            "assignees": list(self.assignees),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
        }


@dataclass(frozen=True, slots=True)
class NormalizedUser(IntegrationModel):
    """Normalized user reference."""

    user_id: str
    login: str
    name: Optional[str] = None
    email: Optional[str] = None
    type: str = "user"  # "user", "bot", "organization"
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "login": self.login,
            "name": self.name,
            "email": self.email,
            "type": self.type,
            "avatar_url": self.avatar_url,
            "html_url": self.html_url,
        }


class PermissionLevel(str, Enum):
    """Normalized permission levels."""

    UNKNOWN = "unknown"
    NONE = "none"
    READ = "read"
    TRIAGE = "triage"
    WRITE = "write"
    MAINTAIN = "maintain"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class NormalizedPermission(IntegrationModel):
    """Normalized permission metadata."""

    level: PermissionLevel = PermissionLevel.UNKNOWN
    admin: Optional[bool] = None
    maintain: Optional[bool] = None
    write: Optional[bool] = None
    triage: Optional[bool] = None
    read: Optional[bool] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "level": self.level.value,
            "admin": self.admin,
            "maintain": self.maintain,
            "write": self.write,
            "triage": self.triage,
            "read": self.read,
            "raw": dict(self.raw),
        })


@dataclass(frozen=True, slots=True)
class NormalizedPagination(IntegrationModel):
    """Normalized pagination metadata."""

    items: tuple[Any, ...]
    pages_fetched: int
    truncated: bool = False
    next_cursor: Optional[str] = None
    total_count: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_fetched": self.pages_fetched,
            "truncated": self.truncated,
            "next_cursor": self.next_cursor,
            "total_count": self.total_count,
            "item_count": len(self.items),
        }


@dataclass(frozen=True, slots=True)
class NormalizedRateLimit(IntegrationModel):
    """Normalized rate limit metadata."""

    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset_at: Optional[str] = None
    resource: str = ""
    observed_headers: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "resource": self.resource,
            "observed_headers": dict(self.observed_headers),
        }


@dataclass(frozen=True, slots=True)
class NormalizedHealthStatus(IntegrationModel):
    """Normalized health check metadata."""

    status: str  # "healthy", "degraded", "down"
    latency_ms: Optional[int] = None
    checked_at: Optional[str] = None
    detail: str = ""
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
            "detail": self.detail,
            "correlation_id": self.correlation_id,
        }


class OperationType(str, Enum):
    """Normalized operation types."""

    READ = "read"
    WRITE = "write"


class OperationStatus(str, Enum):
    """Normalized operation result status."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class NormalizedOperationResult(IntegrationModel):
    """Normalized operation result."""

    operation: str
    provider_id: str
    status: OperationStatus
    ok: bool
    correlation_id: Optional[str] = None
    result: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[Mapping[str, Any]] = None
    operation_type: OperationType = OperationType.READ
    destructive: bool = False
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "operation": self.operation,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "ok": self.ok,
            "correlation_id": self.correlation_id,
            "result": dict(self.result),
            "operation_type": self.operation_type.value,
            "destructive": self.destructive,
            "approval_required": self.approval_required,
        }
        if self.error is not None:
            payload["error"] = dict(self.error)
        return _clean(payload)


class ErrorCategory(str, Enum):
    """Normalized error categories."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_RESPONSE = "invalid_response"
    VALIDATION = "validation"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class NormalizedProviderError(IntegrationModel):
    """Normalized provider error."""

    category: ErrorCategory
    message: str
    provider_id: str
    capability: Optional[str] = None
    retryable: bool = False
    http_status: Optional[int] = None
    correlation_id: Optional[str] = None
    detail: Optional[str] = None
    retry_after_seconds: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
            "provider": self.provider_id,
        }
        for key in ("capability", "http_status", "correlation_id", "detail"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload
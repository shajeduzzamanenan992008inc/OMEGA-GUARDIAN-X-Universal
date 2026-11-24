"""Normalized provider data models.

Every adapter returns these shapes regardless of the vendor payload. Fields
that a provider cannot supply stay ``None``/empty rather than being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """Stable provider-scoped repository coordinate (never the project identity)."""

    provider_id: str
    owner: str
    name: str
    repository_id: str = ""
    url: str = ""

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}" if self.owner else self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "owner": self.owner,
            "name": self.name,
            "repository_id": self.repository_id,
            "url": self.url,
            "slug": self.slug,
        }


def split_repository_slug(value: str) -> tuple[str, str]:
    """Accept ``owner/name``, a host URL, or a provider numeric id."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("A repository identifier is required.")
    text = value.strip().rstrip("/")
    if "://" in text:
        from urllib.parse import urlsplit

        parsed = urlsplit(text)
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            raise ValueError("Repository URL must identify an owner and repository.")
        return parts[-2], parts[-1]
    if text.endswith(".git"):
        text = text[:-4]
    parts = [p for p in text.split("/") if p]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return "", parts[0]
    return "/".join(parts[:-1]), parts[-1]


@dataclass(frozen=True, slots=True)
class RepositoryMetadata:
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
    language: str | None = None
    languages: Mapping[str, int] = field(default_factory=dict)
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    pushed_at: str | None = None
    clone_url: str | None = None
    ssh_url: str | None = None
    html_url: str | None = None
    size_kb: int = 0
    topics: tuple[str, ...] = ()
    license: str | None = None
    permissions: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _clean(
            {
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
            }
        )


@dataclass(frozen=True, slots=True)
class Branch:
    name: str
    commit_sha: str
    protected: bool = False
    protected_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "commit_sha": self.commit_sha,
            "protected": self.protected,
            "protected_by": self.protected_by,
        }


@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    message: str
    author: str = ""
    author_email: str = ""
    authored_at: str | None = None
    committer: str = ""
    parents: tuple[str, ...] = ()
    url: str | None = None

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
class FileContent:
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
class TreeEntry:
    path: str
    type: str
    sha: str = ""
    size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "type": self.type, "sha": self.sha, "size": self.size}


@dataclass(frozen=True, slots=True)
class PullRequest:
    pull_request_id: str
    number: int | None
    title: str
    state: str
    source_branch: str = ""
    target_branch: str = ""
    url: str | None = None
    author: str = ""
    draft: bool = False
    created_at: str | None = None
    merged: bool = False

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
        }


@dataclass(frozen=True, slots=True)
class Issue:
    issue_id: str
    number: int | None
    title: str
    state: str
    url: str | None = None
    author: str = ""
    labels: tuple[str, ...] = ()
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "author": self.author,
            "labels": list(self.labels),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class PermissionSet:
    level: str = "unknown"
    admin: bool | None = None
    maintain: bool | None = None
    push: bool | None = None
    triage: bool | None = None
    pull: bool | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _clean(
            {
                "level": self.level,
                "admin": self.admin,
                "maintain": self.maintain,
                "push": self.push,
                "triage": self.triage,
                "pull": self.pull,
                "raw": dict(self.raw),
            }
        )


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int | None = None
    remaining: int | None = None
    reset_at: str | None = None
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
class HealthStatus:
    status: str
    latency_ms: int | None = None
    checked_at: str | None = None
    detail: str = ""
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
            "detail": self.detail,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class AuthStatus:
    provider_id: str
    configured: bool
    authenticated: bool
    source: str | None = None
    detail: str = ""
    required_env: tuple[str, ...] = ()
    permission_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "configured": self.configured,
            "authenticated": self.authenticated,
            "source": self.source,
            "detail": self.detail,
            "required_env": list(self.required_env),
            "permission_hint": self.permission_hint,
        }


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[Any, ...]
    pages_fetched: int
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class WritePlan:
    """Validated plan for a state-changing remote operation."""

    operation: str
    provider_id: str
    repository: str
    summary: str
    payload: Mapping[str, Any]
    required_permission: str
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "provider_id": self.provider_id,
            "repository": self.repository,
            "summary": self.summary,
            "payload": _clean(dict(self.payload)),
            "required_permission": self.required_permission,
            "destructive": self.destructive,
            "approval_required": True,
        }


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation: str
    provider_id: str
    ok: bool
    correlation_id: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "operation": self.operation,
            "provider_id": self.provider_id,
            "ok": self.ok,
            "correlation_id": self.correlation_id,
            "result": dict(self.result),
        }
        if self.error is not None:
            payload["error"] = dict(self.error)
        return _clean(payload)

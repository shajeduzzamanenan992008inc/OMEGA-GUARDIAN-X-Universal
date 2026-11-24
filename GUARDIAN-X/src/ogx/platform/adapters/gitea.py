"""Gitea / Forgejo adapters (API v1). Both are self-hosted by design."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from ..capabilities import Capability, ProviderKind
from ..models import (
    Branch,
    Commit,
    FileContent,
    HealthStatus,
    Issue,
    PermissionSet,
    PullRequest,
    RateLimit,
    RepositoryMetadata,
    TreeEntry,
)
from .rest import RestAdapter


class GiteaAdapter(RestAdapter):
    provider_id = "gitea"
    display_name = "Gitea"
    kind = ProviderKind.GIT_HOSTING
    version = "1.0.0"
    api_version = "v1"
    default_api_base = "https://gitea.com/api/v1"
    credential_env = ("OGX_GITEA_TOKEN",)
    self_hosted = True
    docs_url = "https://docs.gitea.com/api/1.20/"
    required_permissions = ("read:repository", "write:repository", "write:issue")
    limitations = (
        "The default endpoint is provided for convenience; production use normally requires an explicit self-hosted api_base.",
        "Protected-branch metadata is reported only when the instance exposes it on the branch payload.",
    )
    pagination_style = "offset"
    per_page_param = "limit"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._credential:
            headers["Authorization"] = f"token {self._credential}"
        return headers

    def _repo_path(self, repository: str) -> str:
        owner, name = self._owner_name(repository)
        if not owner:
            raise ValueError(f"{self.display_name} requires an owner/repository identifier.")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    def _normalize_repository(self, data: dict[str, Any]) -> RepositoryMetadata:
        owner = data.get("owner") or {}
        return RepositoryMetadata(
            repository_id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            full_name=str(data.get("full_name", "")),
            owner=str(owner.get("login") or owner.get("username") or ""),
            provider=self.provider_id,
            description=str(data.get("description") or ""),
            owner_type=str(owner.get("type") or "unknown"),
            default_branch=str(data.get("default_branch") or ""),
            visibility="private" if data.get("private") else "public",
            archived=bool(data.get("archived")),
            fork=bool(data.get("fork")),
            language=data.get("language"),
            stars=int(data.get("stars_count") or 0),
            forks=int(data.get("forks_count") or 0),
            watchers=int(data.get("watchers_count") or 0),
            open_issues=int(data.get("open_issues_count") or 0),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            clone_url=data.get("clone_url"),
            ssh_url=data.get("ssh_url"),
            html_url=data.get("html_url"),
            size_kb=int(data.get("size") or 0),
            topics=tuple(data.get("topics") or ()),
            permissions=dict(data.get("permissions") or {}),
        )

    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        path = f"/users/{quote(owner, safe='')}/repos" if owner else "/user/repos"
        items, _ = self._paginate(
            Capability.REPOSITORY_DISCOVERY,
            path,
            params={"limit": min(50, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else (payload.get("data") or []),
        )
        return tuple(self._normalize_repository(item) for item in items if isinstance(item, dict))

    def get_repository(self, repository: str) -> RepositoryMetadata:
        return self._normalize_repository(
            self._json(Capability.REPOSITORY_METADATA, "GET", self._repo_path(repository))
        )

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        items, _ = self._paginate(
            Capability.BRANCH_LIST,
            f"{self._repo_path(repository)}/branches",
            params={"limit": min(50, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else (payload.get("data") or []),
        )
        return tuple(
            Branch(
                name=str(item.get("name", "")),
                commit_sha=str((item.get("commit") or {}).get("id", "")),
                protected=bool(item.get("protected")),
            )
            for item in items
            if isinstance(item, dict)
        )

    def list_commits(
        self,
        repository: str,
        *,
        branch: str | None = None,
        path: str | None = None,
        limit: int = 50,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[Commit, ...]:
        params: dict[str, Any] = {"limit": min(50, self.per_page)}
        if branch:
            params["sha"] = branch
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        items, _ = self._paginate(
            Capability.COMMIT_LIST,
            f"{self._repo_path(repository)}/commits",
            params=params,
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else (payload.get("data") or []),
        )
        commits = []
        for item in items:
            if not isinstance(item, dict):
                continue
            detail = item.get("commit") or {}
            author = detail.get("author") or {}
            commits.append(
                Commit(
                    sha=str(item.get("sha", "")),
                    message=str(detail.get("message", "")),
                    author=str(author.get("name", "")),
                    author_email=str(author.get("email", "")),
                    authored_at=author.get("date"),
                    committer=str((detail.get("committer") or {}).get("name", "")),
                    parents=tuple(p.get("sha", "") for p in item.get("parents") or () if isinstance(p, dict)),
                    url=item.get("html_url"),
                )
            )
        return tuple(commits)

    def get_commit(self, repository: str, commit_sha: str) -> Commit:
        if not commit_sha or len(commit_sha) > 100:
            raise ValueError("A commit SHA is required.")
        data = self._json(
            Capability.COMMIT_INSPECT,
            "GET",
            f"{self._repo_path(repository)}/commits/{quote(commit_sha, safe='')}",
        )
        detail = data.get("commit") or {}
        author = detail.get("author") or {}
        return Commit(
            sha=str(data.get("sha", commit_sha)),
            message=str(detail.get("message", "")),
            author=str(author.get("name", "")),
            author_email=str(author.get("email", "")),
            authored_at=author.get("date"),
            parents=tuple(p.get("sha", "") for p in data.get("parents") or () if isinstance(p, dict)),
            url=data.get("html_url"),
        )

    def read_file(self, repository: str, path: str, ref: str | None = None) -> FileContent:
        if not path:
            raise ValueError("A file path is required.")
        params = {"ref": ref} if ref else {}
        data = self._json(
            Capability.FILE_READ,
            "GET",
            f"{self._repo_path(repository)}/contents/{quote(path, safe='/')}",
            params=params,
        )
        if isinstance(data, list):
            raise ValueError("The requested path is a directory; use tree inspection.")
        raw = data.get("content") or ""
        content = base64.b64decode(raw).decode("utf-8", "replace") if raw else ""
        return FileContent(
            path=str(data.get("path", path)),
            sha=str(data.get("sha", "")),
            size=int(data.get("size") or len(content)),
            content=content,
            truncated=not raw,
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        tree_ref = ref or self.get_repository(repository).default_branch or "main"
        data = self._json(
            Capability.TREE_READ,
            "GET",
            f"{self._repo_path(repository)}/git/trees/{quote(tree_ref, safe='')}",
            params={"recursive": "true" if recursive else "false", "per_page": 100},
        )
        prefix = path.strip("/")
        entries = []
        for item in data.get("tree") or ():
            if not isinstance(item, dict):
                continue
            item_path = str(item.get("path", ""))
            if prefix and item_path != prefix and not item_path.startswith(prefix + "/"):
                continue
            entries.append(
                TreeEntry(
                    path=item_path,
                    type=str(item.get("type", "blob")),
                    sha=str(item.get("sha", "")),
                    size=item.get("size"),
                )
            )
        return tuple(entries)

    def list_pull_requests(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[PullRequest, ...]:
        items, _ = self._paginate(
            Capability.PULL_REQUEST_LIST,
            f"{self._repo_path(repository)}/pulls",
            params={"state": state, "limit": min(50, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else (payload.get("data") or []),
        )
        return tuple(
            PullRequest(
                pull_request_id=str(item.get("id", "")),
                number=item.get("number"),
                title=str(item.get("title", "")),
                state=str(item.get("state", "")),
                source_branch=str((item.get("head") or {}).get("ref", "")),
                target_branch=str((item.get("base") or {}).get("ref", "")),
                url=item.get("html_url"),
                author=str((item.get("user") or {}).get("login", "")),
                draft=bool(item.get("draft")),
                created_at=item.get("created_at"),
                merged=bool(item.get("merged_at")),
            )
            for item in items
            if isinstance(item, dict)
        )

    def list_issues(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[Issue, ...]:
        items, _ = self._paginate(
            Capability.ISSUE_LIST,
            f"{self._repo_path(repository)}/issues",
            params={"state": state, "type": "issues", "limit": min(50, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else (payload.get("data") or []),
        )
        return tuple(
            Issue(
                issue_id=str(item.get("id", "")),
                number=item.get("number"),
                title=str(item.get("title", "")),
                state=str(item.get("state", "")),
                url=item.get("html_url"),
                author=str((item.get("user") or {}).get("login", "")),
                labels=tuple(str(label.get("name", "")) for label in item.get("labels") or () if isinstance(label, dict)),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def get_permissions(self, repository: str) -> PermissionSet:
        data = self._json(Capability.PERMISSION_READ, "GET", self._repo_path(repository))
        permissions = data.get("permissions") or {}
        level = "admin" if permissions.get("admin") else "write" if permissions.get("push") else "read" if permissions.get("pull") else "unknown"
        return PermissionSet(
            level=level,
            admin=permissions.get("admin"),
            push=permissions.get("push"),
            pull=permissions.get("pull"),
            raw=dict(permissions),
        )

    def rate_limit(self) -> RateLimit:
        response = self._send(Capability.RATE_LIMIT_READ, "GET", "/version")
        return RateLimit(
            limit=_int_or_none(response.header("x-ratelimit-limit")),
            remaining=_int_or_none(response.header("x-ratelimit-remaining")),
            reset_at=response.header("x-ratelimit-reset") or None,
            resource="api",
        )

    def health(self) -> HealthStatus:
        return self._timed_health(
            lambda: self._json(Capability.HEALTH_CHECK, "GET", "/version")
        )

    def create_pull_request(
        self, repository: str, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        data = self._json(
            Capability.PULL_REQUEST_CREATE,
            "POST",
            f"{self._repo_path(repository)}/pulls",
            body={"title": title, "body": body, "head": head, "base": base},
            write=True,
        )
        return PullRequest(
            pull_request_id=str(data.get("id", "")),
            number=data.get("number"),
            title=str(data.get("title", title)),
            state=str(data.get("state", "open")),
            source_branch=str((data.get("head") or {}).get("ref", head)),
            target_branch=str((data.get("base") or {}).get("ref", base)),
            url=data.get("html_url"),
            created_at=data.get("created_at"),
        )

    def create_issue(self, repository: str, *, title: str, body: str = "") -> Issue:
        data = self._json(
            Capability.ISSUE_CREATE,
            "POST",
            f"{self._repo_path(repository)}/issues",
            body={"title": title, "body": body},
            write=True,
        )
        return Issue(
            issue_id=str(data.get("id", "")),
            number=data.get("number"),
            title=str(data.get("title", title)),
            state=str(data.get("state", "open")),
            url=data.get("html_url"),
            created_at=data.get("created_at"),
        )

    def update_issue(
        self,
        repository: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> Issue:
        if not isinstance(number, int) or number <= 0:
            raise ValueError("A positive issue number is required.")
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            if state not in {"open", "closed"}:
                raise ValueError("Gitea issue state must be open or closed.")
            payload["state"] = state
        if not payload:
            raise ValueError("Provide at least one field to update.")
        data = self._json(
            Capability.ISSUE_UPDATE,
            "PATCH",
            f"{self._repo_path(repository)}/issues/{number}",
            body=payload,
            write=True,
        )
        return Issue(
            issue_id=str(data.get("id", number)),
            number=data.get("number", number),
            title=str(data.get("title", "")),
            state=str(data.get("state", "")),
            url=data.get("html_url"),
            updated_at=data.get("updated_at"),
        )


class ForgejoAdapter(GiteaAdapter):
    """Forgejo is API-compatible with Gitea for every operation implemented here."""

    provider_id = "forgejo"
    display_name = "Forgejo"
    version = "1.0.0"
    default_api_base = "https://codeberg.org/api/v1"
    credential_env = ("OGX_FORGEJO_TOKEN",)
    docs_url = "https://forgejo.org/docs/latest/user/api-usage/"
    limitations = (
        "Forgejo compatibility is validated against Forgejo's documented Gitea-compatible API v1 subset.",
        "Independent Forgejo instances require an explicit api_base; the default is Codeberg.",
    )


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

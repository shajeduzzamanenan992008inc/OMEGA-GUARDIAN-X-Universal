"""GitLab adapter (REST API v4). Supports gitlab.com and self-hosted instances."""

from __future__ import annotations

import time
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

_ACCESS_LEVELS = {
    0: "none",
    5: "minimal",
    10: "guest",
    20: "reporter",
    30: "developer",
    40: "maintainer",
    50: "owner",
}

_STATE_TO_GITLAB = {"open": "opened", "closed": "closed", "merged": "merged", "all": "all"}
_STATE_FROM_GITLAB = {"opened": "open", "locked": "open", "closed": "closed", "merged": "merged"}


class GitLabAdapter(RestAdapter):
    provider_id = "gitlab"
    display_name = "GitLab"
    kind = ProviderKind.GIT_HOSTING
    version = "1.0.0"
    api_version = "v4"
    default_api_base = "https://gitlab.com/api/v4"
    credential_env = ("OGX_GITLAB_TOKEN",)
    self_hosted = True
    docs_url = "https://docs.gitlab.com/ee/api/rest/"
    required_permissions = (
        "read_api (read operations)",
        "api (approval-gated writes: merge requests, issues)",
    )
    limitations = (
        "Rate-limit headers are only available on responses that include them; a lightweight /version probe is used.",
        "File reads use the raw endpoint, which does not return a blob SHA.",
    )
    pagination_style = "page"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._credential:
            headers["PRIVATE-TOKEN"] = self._credential
        return headers

    def _project(self, repository: str) -> str:
        owner, name = self._owner_name(repository)
        key = f"{owner}/{name}" if owner else name
        return quote(key, safe="")

    @staticmethod
    def _normalize_repository(data: dict[str, Any]) -> RepositoryMetadata:
        namespace = data.get("namespace") or {}
        license_info = data.get("license") or {}
        visibility = str(data.get("visibility") or "unknown")
        return RepositoryMetadata(
            repository_id=str(data.get("id", "")),
            name=str(data.get("name") or data.get("path") or ""),
            full_name=str(data.get("path_with_namespace") or ""),
            owner=str(namespace.get("full_path") or namespace.get("path") or ""),
            provider="gitlab",
            description=str(data.get("description") or ""),
            owner_type=str(namespace.get("kind") or "unknown"),
            default_branch=str(data.get("default_branch") or ""),
            visibility=visibility,
            archived=bool(data.get("archived")),
            fork=bool(data.get("forked_from_project")),
            language=None,
            stars=int(data.get("star_count") or 0),
            forks=int(data.get("forks_count") or 0),
            open_issues=int(data.get("open_issues_count") or 0),
            created_at=data.get("created_at"),
            updated_at=data.get("last_activity_at"),
            pushed_at=data.get("last_activity_at"),
            clone_url=data.get("http_url_to_repo"),
            ssh_url=data.get("ssh_url_to_repo"),
            html_url=data.get("web_url"),
            topics=tuple(data.get("topics") or data.get("tag_list") or ()),
            license=(license_info.get("key") or None) if isinstance(license_info, dict) else None,
        )

    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        params: dict[str, Any] = {"per_page": min(100, self.per_page), "order_by": "last_activity_at"}
        if owner:
            params["group_id"] = owner
            path = f"/groups/{quote(owner, safe='')}/projects"
        else:
            params["membership"] = "true"
            path = "/projects"
        items, _ = self._paginate(
            Capability.REPOSITORY_DISCOVERY,
            path,
            params=params,
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(self._normalize_repository(item) for item in items if isinstance(item, dict))

    def get_repository(self, repository: str) -> RepositoryMetadata:
        data = self._json(
            Capability.REPOSITORY_METADATA, "GET", f"/projects/{self._project(repository)}"
        )
        return self._normalize_repository(data)

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        items, _ = self._paginate(
            Capability.BRANCH_LIST,
            f"/projects/{self._project(repository)}/repository/branches",
            params={"per_page": min(100, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
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
        params: dict[str, Any] = {"per_page": min(100, self.per_page)}
        if branch:
            params["ref_name"] = branch
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        items, _ = self._paginate(
            Capability.COMMIT_LIST,
            f"/projects/{self._project(repository)}/repository/commits",
            params=params,
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(
            Commit(
                sha=str(item.get("id", "")),
                message=str(item.get("message") or item.get("title") or ""),
                author=str(item.get("author_name", "")),
                author_email=str(item.get("author_email", "")),
                authored_at=item.get("authored_date"),
                committer=str(item.get("committer_name", "")),
                parents=tuple(item.get("parent_ids") or ()),
                url=item.get("web_url"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def get_commit(self, repository: str, commit_sha: str) -> Commit:
        if not commit_sha or len(commit_sha) > 100:
            raise ValueError("A commit SHA is required.")
        data = self._json(
            Capability.COMMIT_INSPECT,
            "GET",
            f"/projects/{self._project(repository)}/repository/commits/{quote(commit_sha, safe='')}",
        )
        return Commit(
            sha=str(data.get("id", commit_sha)),
            message=str(data.get("message") or data.get("title") or ""),
            author=str(data.get("author_name", "")),
            author_email=str(data.get("author_email", "")),
            authored_at=data.get("authored_date"),
            committer=str(data.get("committer_name", "")),
            parents=tuple(data.get("parent_ids") or ()),
            url=data.get("web_url"),
        )

    def _resolve_ref(self, repository: str, ref: str | None) -> str:
        if ref:
            return ref
        return self.get_repository(repository).default_branch or "main"

    def read_file(self, repository: str, path: str, ref: str | None = None) -> FileContent:
        if not path:
            raise ValueError("A file path is required.")
        response = self._send(
            Capability.FILE_READ,
            "GET",
            f"/projects/{self._project(repository)}/repository/files/{quote(path, safe='')}/raw",
            params={"ref": self._resolve_ref(repository, ref)},
            headers={"Accept": "text/plain"},
        )
        content = response.text()
        return FileContent(
            path=path,
            sha="",
            size=len(response.body),
            content=content,
            encoding="utf-8",
            truncated=len(response.body) >= self.transport.max_response_bytes,
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        params: dict[str, Any] = {
            "ref": self._resolve_ref(repository, ref),
            "per_page": min(100, self.per_page),
            "recursive": "true" if recursive else "false",
        }
        if path:
            params["path"] = path
        items, _ = self._paginate(
            Capability.TREE_READ,
            f"/projects/{self._project(repository)}/repository/tree",
            params=params,
            limit=1000,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(
            TreeEntry(
                path=str(item.get("path", "")),
                type="tree" if item.get("type") == "tree" else "blob",
                sha=str(item.get("id", "")),
            )
            for item in items
            if isinstance(item, dict)
        )

    def list_pull_requests(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[PullRequest, ...]:
        items, _ = self._paginate(
            Capability.PULL_REQUEST_LIST,
            f"/projects/{self._project(repository)}/merge_requests",
            params={
                "state": _STATE_TO_GITLAB.get(state, state),
                "per_page": min(100, self.per_page),
            },
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(
            PullRequest(
                pull_request_id=str(item.get("id", item.get("iid", ""))),
                number=item.get("iid"),
                title=str(item.get("title", "")),
                state=_STATE_FROM_GITLAB.get(str(item.get("state")), str(item.get("state", ""))),
                source_branch=str(item.get("source_branch", "")),
                target_branch=str(item.get("target_branch", "")),
                url=item.get("web_url"),
                author=str((item.get("author") or {}).get("name", "")),
                draft=bool(item.get("draft") or item.get("work_in_progress")),
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
            f"/projects/{self._project(repository)}/issues",
            params={
                "state": _STATE_TO_GITLAB.get(state, state),
                "per_page": min(100, self.per_page),
            },
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(
            Issue(
                issue_id=str(item.get("id", item.get("iid", ""))),
                number=item.get("iid"),
                title=str(item.get("title", "")),
                state=_STATE_FROM_GITLAB.get(str(item.get("state")), str(item.get("state", ""))),
                url=item.get("web_url"),
                author=str((item.get("author") or {}).get("name", "")),
                labels=tuple(str(label) for label in item.get("labels") or ()),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def get_permissions(self, repository: str) -> PermissionSet:
        data = self._json(
            Capability.PERMISSION_READ, "GET", f"/projects/{self._project(repository)}"
        )
        permissions = data.get("permissions") or {}
        best = 0
        for key in ("project_access", "group_access"):
            access = permissions.get(key) or {}
            if isinstance(access, dict):
                best = max(best, int(access.get("access_level") or 0))
        level = _ACCESS_LEVELS.get(best, "unknown")
        return PermissionSet(
            level=level,
            admin=best >= 40,
            maintain=best >= 40,
            push=best >= 30,
            triage=best >= 20,
            pull=best >= 10,
            raw={"access_level": best},
        )

    def rate_limit(self) -> RateLimit:
        response = self._send(Capability.RATE_LIMIT_READ, "GET", "/version")
        return RateLimit(
            limit=_int_or_none(response.header("ratelimit-limit")),
            remaining=_int_or_none(response.header("ratelimit-remaining")),
            reset_at=_epoch_to_iso(response.header("ratelimit-reset")),
            resource="rest",
            observed_headers={
                key: response.header(key)
                for key in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset")
                if response.header(key)
            },
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
            f"/projects/{self._project(repository)}/merge_requests",
            body={
                "title": title,
                "description": body,
                "source_branch": head,
                "target_branch": base,
                "remove_source_branch": False,
            },
            write=True,
        )
        return PullRequest(
            pull_request_id=str(data.get("id", data.get("iid", ""))),
            number=data.get("iid"),
            title=str(data.get("title", title)),
            state=_STATE_FROM_GITLAB.get(str(data.get("state")), "open"),
            source_branch=str(data.get("source_branch", head)),
            target_branch=str(data.get("target_branch", base)),
            url=data.get("web_url"),
            author=str((data.get("author") or {}).get("name", "")),
            draft=bool(data.get("draft")),
            created_at=data.get("created_at"),
        )

    def create_issue(self, repository: str, *, title: str, body: str = "") -> Issue:
        data = self._json(
            Capability.ISSUE_CREATE,
            "POST",
            f"/projects/{self._project(repository)}/issues",
            body={"title": title, "description": body},
            write=True,
        )
        return Issue(
            issue_id=str(data.get("id", data.get("iid", ""))),
            number=data.get("iid"),
            title=str(data.get("title", title)),
            state=_STATE_FROM_GITLAB.get(str(data.get("state")), "open"),
            url=data.get("web_url"),
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
            payload["description"] = body
        if state is not None:
            if state not in {"open", "closed"}:
                raise ValueError("GitLab issue state must be open or closed.")
            payload["state_event"] = "reopen" if state == "open" else "close"
        if not payload:
            raise ValueError("Provide at least one field to update.")
        data = self._json(
            Capability.ISSUE_UPDATE,
            "PUT",
            f"/projects/{self._project(repository)}/issues/{number}",
            body=payload,
            write=True,
        )
        return Issue(
            issue_id=str(data.get("id", number)),
            number=data.get("iid", number),
            title=str(data.get("title", "")),
            state=_STATE_FROM_GITLAB.get(str(data.get("state")), ""),
            url=data.get("web_url"),
            updated_at=data.get("updated_at"),
        )


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch_to_iso(value: str) -> str | None:
    seconds = _int_or_none(value)
    if seconds is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))

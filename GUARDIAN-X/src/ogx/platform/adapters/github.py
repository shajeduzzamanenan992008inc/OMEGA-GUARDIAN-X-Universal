"""GitHub adapter (REST API v2022-11-28)."""

from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import quote

from ..base import ProviderAdapter
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
from .rest import RestAdapter, clamp


class GitHubAdapter(RestAdapter):
    provider_id = "github"
    display_name = "GitHub"
    kind = ProviderKind.GIT_HOSTING
    version = "1.0.0"
    api_version = "2022-11-28"
    default_api_base = "https://api.github.com"
    credential_env = ("OGX_GITHUB_TOKEN",)
    self_hosted = True  # GitHub Enterprise Server via explicit api_base
    docs_url = "https://docs.github.com/en/rest"
    required_permissions = (
        "repo (private repositories)",
        "public_repo (public repositories)",
        "read:org (organization discovery)",
        "pull_requests:write (approval-gated PR creation)",
    )
    limitations = (
        "The contents API returns an empty body above roughly 1 MiB; such files are marked truncated.",
        "Repository permissions are reported from the repository payload and may lag organization policy.",
    )
    pagination_style = "link"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
        }
        if self._credential:
            headers["Authorization"] = f"Bearer {self._credential}"
        return headers

    # ------------------------------------------------------------------ helpers
    def _repo_path(self, repository: str) -> str:
        owner, name = self._owner_name(repository)
        if not owner:
            raise ValueError("GitHub requires an owner/repository identifier.")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    @staticmethod
    def _normalize_repository(data: dict[str, Any]) -> RepositoryMetadata:
        owner = (data.get("owner") or {}).get("login", "")
        license_info = data.get("license") or {}
        return RepositoryMetadata(
            repository_id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            full_name=str(data.get("full_name", "")),
            owner=str(owner),
            provider="github",
            description=str(data.get("description") or ""),
            owner_type=str((data.get("owner") or {}).get("type", "unknown")),
            default_branch=str(data.get("default_branch") or ""),
            visibility=str(data.get("visibility") or ("private" if data.get("private") else "public")),
            archived=bool(data.get("archived")),
            disabled=bool(data.get("disabled")),
            fork=bool(data.get("fork")),
            language=data.get("language"),
            stars=int(data.get("stargazers_count") or 0),
            forks=int(data.get("forks_count") or 0),
            watchers=int(data.get("watchers_count") or 0),
            open_issues=int(data.get("open_issues_count") or 0),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            pushed_at=data.get("pushed_at"),
            clone_url=data.get("clone_url"),
            ssh_url=data.get("ssh_url"),
            html_url=data.get("html_url"),
            size_kb=int(data.get("size") or 0),
            topics=tuple(data.get("topics") or ()),
            license=(license_info.get("spdx_id") or None) if isinstance(license_info, dict) else None,
            permissions=dict(data.get("permissions") or {}),
        )

    # --------------------------------------------------------------- read ops
    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        if owner:
            path = f"/orgs/{quote(owner, safe='')}/repos"
            try:
                items, _ = self._paginate(
                    Capability.REPOSITORY_DISCOVERY,
                    path,
                    params={"per_page": min(100, self.per_page)},
                    limit=limit,
                    extract=lambda payload: payload if isinstance(payload, list) else [],
                )
            except Exception as exc:  # noqa: BLE001 - fall back to user namespace
                from ..errors import NotFoundError

                if not isinstance(exc, NotFoundError):
                    raise
                items, _ = self._paginate(
                    Capability.REPOSITORY_DISCOVERY,
                    f"/users/{quote(owner, safe='')}/repos",
                    params={"per_page": min(100, self.per_page)},
                    limit=limit,
                    extract=lambda payload: payload if isinstance(payload, list) else [],
                )
        else:
            items, _ = self._paginate(
                Capability.REPOSITORY_DISCOVERY,
                "/user/repos",
                params={
                    "per_page": min(100, self.per_page),
                    "affiliation": "owner,collaborator,organization_member",
                },
                limit=limit,
                extract=lambda payload: payload if isinstance(payload, list) else [],
            )
        return tuple(self._normalize_repository(item) for item in items if isinstance(item, dict))

    def get_repository(self, repository: str) -> RepositoryMetadata:
        data = self._json(Capability.REPOSITORY_METADATA, "GET", self._repo_path(repository))
        return self._normalize_repository(data)

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        items, _ = self._paginate(
            Capability.BRANCH_LIST,
            f"{self._repo_path(repository)}/branches",
            params={"per_page": min(100, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(
            Branch(
                name=str(item.get("name", "")),
                commit_sha=str((item.get("commit") or {}).get("sha", "")),
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
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        commits = []
        for item in items:
            if not isinstance(item, dict):
                continue
            commit = item.get("commit") or {}
            author = commit.get("author") or {}
            commits.append(
                Commit(
                    sha=str(item.get("sha", "")),
                    message=str(commit.get("message", "")),
                    author=str(author.get("name", "")),
                    author_email=str(author.get("email", "")),
                    authored_at=author.get("date"),
                    committer=str((commit.get("committer") or {}).get("name", "")),
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
        commit = data.get("commit") or {}
        author = commit.get("author") or {}
        return Commit(
            sha=str(data.get("sha", commit_sha)),
            message=str(commit.get("message", "")),
            author=str(author.get("name", "")),
            author_email=str(author.get("email", "")),
            authored_at=author.get("date"),
            committer=str((commit.get("committer") or {}).get("name", "")),
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
        encoding = str(data.get("encoding") or "")
        raw = data.get("content") or ""
        if encoding == "base64" and raw:
            content = base64.b64decode(raw).decode("utf-8", "replace")
            truncated = False
        else:
            content = ""
            truncated = True
        return FileContent(
            path=str(data.get("path", path)),
            sha=str(data.get("sha", "")),
            size=int(data.get("size") or len(content)),
            content=content,
            encoding="utf-8",
            truncated=truncated,
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        tree_ref = ref or self.get_repository(repository).default_branch or "HEAD"
        params: dict[str, Any] = {"recursive": "1"} if recursive else {}
        data = self._json(
            Capability.TREE_READ,
            "GET",
            f"{self._repo_path(repository)}/git/trees/{quote(tree_ref, safe='')}",
            params=params,
        )
        entries = []
        prefix = path.strip("/")
        for item in data.get("tree") or ():
            if not isinstance(item, dict):
                continue
            item_path = str(item.get("path", ""))
            if prefix and not item_path.startswith(prefix + "/") and item_path != prefix:
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
            params={"state": state, "per_page": min(100, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        return tuple(
            PullRequest(
                pull_request_id=str(item.get("id", item.get("number", ""))),
                number=item.get("number"),
                title=str(item.get("title", "")),
                state="closed" if item.get("state") == "closed" else str(item.get("state", "")),
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
            params={"state": state, "per_page": min(100, self.per_page)},
            limit=limit,
            extract=lambda payload: payload if isinstance(payload, list) else [],
        )
        issues = []
        for item in items:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            issues.append(
                Issue(
                    issue_id=str(item.get("id", item.get("number", ""))),
                    number=item.get("number"),
                    title=str(item.get("title", "")),
                    state=str(item.get("state", "")),
                    url=item.get("html_url"),
                    author=str((item.get("user") or {}).get("login", "")),
                    labels=tuple(
                        str(label.get("name", ""))
                        for label in item.get("labels") or ()
                        if isinstance(label, dict)
                    ),
                    created_at=item.get("created_at"),
                    updated_at=item.get("updated_at"),
                )
            )
        return tuple(issues)

    def get_permissions(self, repository: str) -> PermissionSet:
        data = self._json(Capability.PERMISSION_READ, "GET", self._repo_path(repository))
        permissions = data.get("permissions") or {}
        role = str(data.get("role_name") or "")
        level = "admin" if permissions.get("admin") else "write" if permissions.get("push") else "read" if permissions.get("pull") else (role or "unknown")
        return PermissionSet(
            level=level,
            admin=permissions.get("admin"),
            maintain=permissions.get("maintain"),
            push=permissions.get("push"),
            triage=permissions.get("triage"),
            pull=permissions.get("pull"),
            raw={"role_name": role} if role else {},
        )

    def rate_limit(self) -> RateLimit:
        data = self._json(Capability.RATE_LIMIT_READ, "GET", "/rate_limit")
        core = (data.get("resources") or {}).get("core") or {}
        reset = core.get("reset")
        reset_at = (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset)) if isinstance(reset, (int, float)) else None
        )
        return RateLimit(
            limit=core.get("limit"),
            remaining=core.get("remaining"),
            reset_at=reset_at,
            resource="core",
        )

    def health(self) -> HealthStatus:
        return self._timed_health(lambda: self._json(Capability.HEALTH_CHECK, "GET", "/rate_limit"))

    # -------------------------------------------------------------- write ops
    def create_pull_request(
        self, repository: str, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        data = self._json(
            Capability.PULL_REQUEST_CREATE,
            "POST",
            f"{self._repo_path(repository)}/pulls",
            body={"title": title, "body": body, "head": head, "base": base, "draft": True},
            write=True,
        )
        return PullRequest(
            pull_request_id=str(data.get("id", data.get("number", ""))),
            number=data.get("number"),
            title=str(data.get("title", title)),
            state=str(data.get("state", "open")),
            source_branch=str((data.get("head") or {}).get("ref", head)),
            target_branch=str((data.get("base") or {}).get("ref", base)),
            url=data.get("html_url"),
            author=str((data.get("user") or {}).get("login", "")),
            draft=bool(data.get("draft", True)),
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
            issue_id=str(data.get("id", data.get("number", ""))),
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
                raise ValueError("GitHub issue state must be open or closed.")
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

"""Bitbucket Cloud adapter (REST API 2.0)."""

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
    PullRequest,
    RateLimit,
    RepositoryMetadata,
    TreeEntry,
)
from ..security import basic_auth_value, split_credential_pair
from .rest import RestAdapter

_STATE_TO_BITBUCKET = {
    "open": "OPEN",
    "closed": "DECLINED",
    "merged": "MERGED",
    "declined": "DECLINED",
    "all": None,
}
_STATE_FROM_BITBUCKET = {
    "OPEN": "open",
    "MERGED": "merged",
    "DECLINED": "closed",
    "SUPERSEDED": "closed",
}


class BitbucketAdapter(RestAdapter):
    provider_id = "bitbucket"
    display_name = "Bitbucket Cloud"
    kind = ProviderKind.GIT_HOSTING
    version = "1.0.0"
    api_version = "2.0"
    default_api_base = "https://api.bitbucket.org/2.0"
    credential_env = ("OGX_BITBUCKET_TOKEN",)
    self_hosted = False
    docs_url = "https://developer.atlassian.com/cloud/bitbucket/rest/"
    required_permissions = (
        "repository:read",
        "pullrequest:write (approval-gated PR creation)",
        "issue:write (approval-gated issue operations)",
    )
    limitations = (
        "Bitbucket Cloud does not expose repository permission levels through the REST 2.0 repository payload; permission negotiation is unsupported.",
        "Bitbucket Server/Data Center uses a different API and is not covered by this adapter.",
    )
    pagination_style = "body_next"
    per_page_param = "pagelen"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._credential:
            pair = split_credential_pair(self._credential)
            if pair:
                headers["Authorization"] = basic_auth_value(*pair)
            else:
                headers["Authorization"] = f"Bearer {self._credential}"
        return headers

    def _repo_path(self, repository: str) -> str:
        owner, name = self._owner_name(repository)
        if not owner:
            raise ValueError("Bitbucket requires a workspace/repository identifier.")
        return f"/repositories/{quote(owner, safe='')}/{quote(name, safe='')}"

    @staticmethod
    def _normalize_repository(data: dict[str, Any]) -> RepositoryMetadata:
        links = data.get("links") or {}
        clone = links.get("clone") or []
        clone_url = next(
            (item.get("href") for item in clone if item.get("name") == "https"),
            None,
        )
        ssh_url = next(
            (item.get("href") for item in clone if item.get("name") == "ssh"),
            None,
        )
        mainbranch = data.get("mainbranch") or {}
        return RepositoryMetadata(
            repository_id=str(data.get("uuid") or data.get("id") or ""),
            name=str(data.get("name") or data.get("slug") or ""),
            full_name=str(data.get("full_name") or ""),
            owner=str((data.get("workspace") or {}).get("slug", "")),
            provider="bitbucket",
            description=str(data.get("description") or ""),
            owner_type="workspace",
            default_branch=str(mainbranch.get("name") or ""),
            visibility="private" if data.get("is_private") else "public",
            language=data.get("language"),
            created_at=data.get("created_on"),
            updated_at=data.get("updated_on"),
            clone_url=clone_url,
            ssh_url=ssh_url,
            html_url=(links.get("html") or {}).get("href"),
            size_kb=int(data.get("size") or 0),
        )

    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        if not owner:
            raise ValueError("Bitbucket discovery requires a workspace (--owner).")
        items, _ = self._paginate(
            Capability.REPOSITORY_DISCOVERY,
            f"/repositories/{quote(owner, safe='')}",
            params={"pagelen": min(100, self.per_page)},
            limit=limit,
            extract=lambda payload: (payload.get("values") or []) if isinstance(payload, dict) else [],
        )
        return tuple(self._normalize_repository(item) for item in items if isinstance(item, dict))

    def get_repository(self, repository: str) -> RepositoryMetadata:
        data = self._json(Capability.REPOSITORY_METADATA, "GET", self._repo_path(repository))
        return self._normalize_repository(data)

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        items, _ = self._paginate(
            Capability.BRANCH_LIST,
            f"{self._repo_path(repository)}/refs/branches",
            params={"pagelen": min(100, self.per_page)},
            limit=limit,
            extract=lambda payload: (payload.get("values") or []) if isinstance(payload, dict) else [],
        )
        return tuple(
            Branch(
                name=str(item.get("name", "")),
                commit_sha=str((item.get("target") or {}).get("hash", "")),
                protected=False,
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
        params: dict[str, Any] = {"pagelen": min(100, self.per_page)}
        if branch:
            params["include"] = branch
        if path:
            params["path"] = path
        items, _ = self._paginate(
            Capability.COMMIT_LIST,
            f"{self._repo_path(repository)}/commits",
            params=params,
            limit=limit,
            extract=lambda payload: (payload.get("values") or []) if isinstance(payload, dict) else [],
        )
        return tuple(
            Commit(
                sha=str(item.get("hash", "")),
                message=str(item.get("message", "")),
                author=str((item.get("author") or {}).get("user", {}).get("display_name") or (item.get("author") or {}).get("raw", "")),
                authored_at=item.get("date"),
                parents=tuple(p.get("hash", "") for p in item.get("parents") or () if isinstance(p, dict)),
                url=((item.get("links") or {}).get("html") or {}).get("href"),
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
            f"{self._repo_path(repository)}/commit/{quote(commit_sha, safe='')}",
        )
        return Commit(
            sha=str(data.get("hash", commit_sha)),
            message=str(data.get("message", "")),
            author=str((data.get("author") or {}).get("user", {}).get("display_name") or (data.get("author") or {}).get("raw", "")),
            authored_at=data.get("date"),
            parents=tuple(p.get("hash", "") for p in data.get("parents") or () if isinstance(p, dict)),
            url=((data.get("links") or {}).get("html") or {}).get("href"),
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
            f"{self._repo_path(repository)}/src/{quote(self._resolve_ref(repository, ref), safe='')}/{quote(path, safe='/')}",
            headers={"Accept": "text/plain"},
        )
        return FileContent(
            path=path,
            sha="",
            size=len(response.body),
            content=response.text(),
            truncated=len(response.body) >= self.transport.max_response_bytes,
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        suffix = f"/{quote(path.strip('/'), safe='/')}" if path.strip("/") else ""
        items, _ = self._paginate(
            Capability.TREE_READ,
            f"{self._repo_path(repository)}/src/{quote(self._resolve_ref(repository, ref), safe='')}{suffix}",
            params={"pagelen": min(100, self.per_page)},
            limit=1000,
            extract=lambda payload: (payload.get("values") or []) if isinstance(payload, dict) else [],
        )
        return tuple(
            TreeEntry(
                path=str(item.get("path", "")),
                type="tree" if item.get("type") == "commit_directory" else "blob",
                size=item.get("size"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def list_pull_requests(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[PullRequest, ...]:
        mapped = _STATE_TO_BITBUCKET.get(state, state.upper())
        params: dict[str, Any] = {"pagelen": min(100, self.per_page)}
        if mapped:
            params["state"] = mapped
        items, _ = self._paginate(
            Capability.PULL_REQUEST_LIST,
            f"{self._repo_path(repository)}/pullrequests",
            params=params,
            limit=limit,
            extract=lambda payload: (payload.get("values") or []) if isinstance(payload, dict) else [],
        )
        return tuple(
            PullRequest(
                pull_request_id=str(item.get("id", "")),
                number=item.get("id"),
                title=str(item.get("title", "")),
                state=_STATE_FROM_BITBUCKET.get(str(item.get("state")), str(item.get("state", "")).lower()),
                source_branch=str((item.get("source") or {}).get("branch", {}).get("name", "")),
                target_branch=str((item.get("destination") or {}).get("branch", {}).get("name", "")),
                url=((item.get("links") or {}).get("html") or {}).get("href"),
                author=str((item.get("author") or {}).get("display_name", "")),
                draft=bool(item.get("draft")),
                created_at=item.get("created_on"),
                merged=bool(item.get("merged_on")),
            )
            for item in items
            if isinstance(item, dict)
        )

    def list_issues(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[Issue, ...]:
        params: dict[str, Any] = {"pagelen": min(100, self.per_page)}
        if state in {"open", "new"}:
            params["q"] = 'state="new" OR state="open"'
        elif state in {"closed", "resolved"}:
            params["q"] = 'state="resolved" OR state="closed"'
        items, _ = self._paginate(
            Capability.ISSUE_LIST,
            f"{self._repo_path(repository)}/issues",
            params=params,
            limit=limit,
            extract=lambda payload: (payload.get("values") or []) if isinstance(payload, dict) else [],
        )
        return tuple(
            Issue(
                issue_id=str(item.get("id", "")),
                number=item.get("id"),
                title=str(item.get("title", "")),
                state=str(item.get("state", "")),
                url=((item.get("links") or {}).get("html") or {}).get("href"),
                author=str((item.get("reporter") or {}).get("display_name", "")),
                labels=tuple(
                    str(kind.get("name", "")) for kind in item.get("kind") or () if isinstance(kind, dict)
                ),
                created_at=item.get("created_on"),
                updated_at=item.get("updated_on"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def rate_limit(self) -> RateLimit:
        response = self._send(Capability.RATE_LIMIT_READ, "GET", "/user")
        reset = response.header("x-ratelimit-reset")
        reset_at = None
        try:
            reset_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(reset)))
        except (TypeError, ValueError):
            pass
        return RateLimit(
            limit=_int_or_none(response.header("x-ratelimit-limit")),
            remaining=_int_or_none(response.header("x-ratelimit-remaining")),
            reset_at=reset_at,
            resource="api",
        )

    def health(self) -> HealthStatus:
        return self._timed_health(
            lambda: self._json(Capability.HEALTH_CHECK, "GET", "/user")
        )

    def create_pull_request(
        self, repository: str, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        data = self._json(
            Capability.PULL_REQUEST_CREATE,
            "POST",
            f"{self._repo_path(repository)}/pullrequests",
            body={
                "title": title,
                "description": body,
                "source": {"branch": {"name": head}},
                "destination": {"branch": {"name": base}},
                "close_source_branch": False,
            },
            write=True,
        )
        return PullRequest(
            pull_request_id=str(data.get("id", "")),
            number=data.get("id"),
            title=str(data.get("title", title)),
            state="open",
            source_branch=str((data.get("source") or {}).get("branch", {}).get("name", head)),
            target_branch=str((data.get("destination") or {}).get("branch", {}).get("name", base)),
            url=((data.get("links") or {}).get("html") or {}).get("href"),
            draft=bool(data.get("draft")),
            created_at=data.get("created_on"),
        )

    def create_issue(self, repository: str, *, title: str, body: str = "") -> Issue:
        data = self._json(
            Capability.ISSUE_CREATE,
            "POST",
            f"{self._repo_path(repository)}/issues",
            body={"title": title, "content": {"raw": body}},
            write=True,
        )
        return Issue(
            issue_id=str(data.get("id", "")),
            number=data.get("id"),
            title=str(data.get("title", title)),
            state=str(data.get("state", "new")),
            url=((data.get("links") or {}).get("html") or {}).get("href"),
            created_at=data.get("created_on"),
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
            payload["content"] = {"raw": body}
        if state is not None:
            payload["state"] = state
        if not payload:
            raise ValueError("Provide at least one field to update.")
        data = self._json(
            Capability.ISSUE_UPDATE,
            "PUT",
            f"{self._repo_path(repository)}/issues/{number}",
            body=payload,
            write=True,
        )
        return Issue(
            issue_id=str(data.get("id", number)),
            number=data.get("id", number),
            title=str(data.get("title", "")),
            state=str(data.get("state", "")),
            url=((data.get("links") or {}).get("html") or {}).get("href"),
            updated_at=data.get("updated_on"),
        )


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

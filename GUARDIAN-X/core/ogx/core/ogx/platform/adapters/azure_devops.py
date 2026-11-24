"""Azure DevOps Repos adapter (REST API 7.1).

Requires ``OGX_AZURE_DEVOPS_ORG`` (organization) and a personal access token in
``OGX_AZURE_DEVOPS_TOKEN``. Repository identifiers use ``project/repository``.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from ..capabilities import Capability, ProviderKind
from ..models import (
    Branch,
    Commit,
    FileContent,
    HealthStatus,
    PullRequest,
    RepositoryMetadata,
    TreeEntry,
)
from ..security import basic_auth_value
from .rest import RestAdapter


class AzureDevOpsAdapter(RestAdapter):
    provider_id = "azure_devops"
    display_name = "Azure DevOps Repos"
    kind = ProviderKind.GIT_HOSTING
    version = "1.0.0"
    api_version = "7.1"
    default_api_base = "https://dev.azure.com"
    credential_env = ("OGX_AZURE_DEVOPS_TOKEN",)
    self_hosted = True  # Azure DevOps Server via explicit api_base
    docs_url = "https://learn.microsoft.com/en-us/rest/api/azure/devops/git/"
    required_permissions = (
        "Code (read) for repositories, branches, commits and items",
        "Code (write) for approval-gated pull request creation",
    )
    limitations = (
        "Issues and repository permission levels are not part of the Azure DevOps Repos contract; those capabilities are unsupported.",
        "Rate-limit headers are not consistently exposed, so rate-limit reporting is unsupported.",
        "The organization is read from OGX_AZURE_DEVOPS_ORG; project/repository is the identifier.",
    )
    pagination_style = "continuation"

    def __init__(self, **kwargs: Any) -> None:
        self.organization = (
            kwargs.pop("organization", None)
            or (kwargs.get("environ") or os.environ).get("OGX_AZURE_DEVOPS_ORG", "")
        )
        super().__init__(**kwargs)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._credential:
            headers["Authorization"] = basic_auth_value("", self._credential)
        return headers

    def _org(self, owner: str | None = None) -> str:
        value = (owner or self.organization or "").strip()
        if not value:
            raise ValueError("Azure DevOps requires an organization (OGX_AZURE_DEVOPS_ORG or --owner).")
        return quote(value, safe="")

    def _project_repo(self, repository: str) -> tuple[str, str]:
        owner, name = self._owner_name(repository)
        if owner and "/" in owner:
            project, _, repo = owner.rpartition("/")
            return project, repo or name
        if owner:
            return owner, name
        raise ValueError("Azure DevOps requires a project/repository identifier.")

    def _api(self, path: str) -> str:
        return f"{path}{'&' if '?' in path else '?'}api-version={self.api_version}"

    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        org = self._org(owner)
        items, _ = self._paginate(
            Capability.REPOSITORY_DISCOVERY,
            self._api(f"/{org}/_apis/git/repositories"),
            params={},
            limit=limit,
            extract=lambda payload: payload.get("value") or [] if isinstance(payload, dict) else [],
        )
        return tuple(self._normalize_repository(item) for item in items if isinstance(item, dict))

    @staticmethod
    def _normalize_repository(data: dict[str, Any]) -> RepositoryMetadata:
        project = data.get("project") or {}
        return RepositoryMetadata(
            repository_id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            full_name=f"{project.get('name', '')}/{data.get('name', '')}".strip("/"),
            owner=str(project.get("name", "")),
            provider="azure_devops",
            default_branch=str((data.get("defaultBranch") or "").replace("refs/heads/", "")),
            visibility="private" if data.get("isDisabled") is not None else "unknown",
            archived=bool(data.get("isDisabled")),
            disabled=bool(data.get("isDisabled")),
            clone_url=(data.get("remoteUrl") or None),
            ssh_url=(data.get("sshUrl") or None),
            html_url=(data.get("webUrl") or None),
            size_kb=int(data.get("size") or 0),
            extra={"project_id": project.get("id")},
        )

    def get_repository(self, repository: str) -> RepositoryMetadata:
        project, repo = self._project_repo(repository)
        org = self._org()
        data = self._json(
            Capability.REPOSITORY_METADATA,
            "GET",
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}"),
        )
        return self._normalize_repository(data)

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        project, repo = self._project_repo(repository)
        org = self._org()
        items, _ = self._paginate(
            Capability.BRANCH_LIST,
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/refs"),
            params={"filter": "heads/"},
            limit=limit,
            extract=lambda payload: payload.get("value") or [] if isinstance(payload, dict) else [],
        )
        return tuple(
            Branch(
                name=str(item.get("name", "")).replace("refs/heads/", ""),
                commit_sha=str(item.get("objectId", "")),
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
        project, repo = self._project_repo(repository)
        org = self._org()
        params: dict[str, Any] = {"searchCriteria.$top": min(100, limit)}
        if branch:
            params["searchCriteria.itemVersion.version"] = branch
            params["searchCriteria.itemVersion.versionType"] = "branch"
        if path:
            params["searchCriteria.itemPath"] = path
        items, _ = self._paginate(
            Capability.COMMIT_LIST,
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/commits"),
            params=params,
            limit=limit,
            extract=lambda payload: payload.get("value") or [] if isinstance(payload, dict) else [],
        )
        return tuple(
            Commit(
                sha=str(item.get("commitId", "")),
                message=str(item.get("comment", "")),
                author=str((item.get("author") or {}).get("name", "")),
                author_email=str((item.get("author") or {}).get("email", "")),
                authored_at=(item.get("author") or {}).get("date"),
                committer=str((item.get("committer") or {}).get("name", "")),
                parents=tuple(str(p) for p in item.get("parents") or ()),
                url=item.get("remoteUrl"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def get_commit(self, repository: str, commit_sha: str) -> Commit:
        if not commit_sha or len(commit_sha) > 100:
            raise ValueError("A commit SHA is required.")
        project, repo = self._project_repo(repository)
        org = self._org()
        data = self._json(
            Capability.COMMIT_INSPECT,
            "GET",
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/commits/{quote(commit_sha, safe='')}"),
        )
        author = data.get("author") or {}
        return Commit(
            sha=str(data.get("commitId", commit_sha)),
            message=str(data.get("comment", "")),
            author=str(author.get("name", "")),
            author_email=str(author.get("email", "")),
            authored_at=author.get("date"),
            committer=str((data.get("committer") or {}).get("name", "")),
            parents=tuple(str(p) for p in data.get("parents") or ()),
            url=data.get("remoteUrl"),
        )

    def read_file(self, repository: str, path: str, ref: str | None = None) -> FileContent:
        if not path:
            raise ValueError("A file path is required.")
        project, repo = self._project_repo(repository)
        org = self._org()
        params: dict[str, Any] = {"path": "/" + path.lstrip("/"), "includeContent": "true"}
        if ref:
            params["versionDescriptor.version"] = ref
            params["versionDescriptor.versionType"] = "branch"
        data = self._json(
            Capability.FILE_READ,
            "GET",
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/items"),
            params=params,
        )
        if data.get("isFolder"):
            raise ValueError("The requested path is a folder; use tree inspection.")
        content = data.get("content") or ""
        return FileContent(
            path=str(data.get("path", path)).lstrip("/"),
            sha=str(data.get("objectId", "")),
            size=int(data.get("size") or len(content.encode("utf-8"))),
            content=content,
            truncated=False,
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        project, repo = self._project_repo(repository)
        org = self._org()
        params: dict[str, Any] = {"recursionLevel": "Full" if recursive else "OneLevel"}
        if path:
            params["scopePath"] = "/" + path.strip("/")
        if ref:
            params["versionDescriptor.version"] = ref
            params["versionDescriptor.versionType"] = "branch"
        items, _ = self._paginate(
            Capability.TREE_READ,
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/items"),
            params=params,
            limit=1000,
            extract=lambda payload: payload.get("value") or [] if isinstance(payload, dict) else [],
        )
        return tuple(
            TreeEntry(
                path=str(item.get("path", "")).lstrip("/"),
                type="tree" if item.get("isFolder") else "blob",
                sha=str(item.get("objectId", "")),
                size=item.get("size"),
            )
            for item in items
            if isinstance(item, dict)
        )

    def list_pull_requests(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[PullRequest, ...]:
        project, repo = self._project_repo(repository)
        org = self._org()
        status = {"open": "active", "closed": "completed", "merged": "completed", "all": "all"}.get(state, "active")
        items, _ = self._paginate(
            Capability.PULL_REQUEST_LIST,
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/pullrequests"),
            params={"searchCriteria.status": status},
            limit=limit,
            extract=lambda payload: payload.get("value") or [] if isinstance(payload, dict) else [],
        )
        return tuple(
            PullRequest(
                pull_request_id=str(item.get("pullRequestId", "")),
                number=item.get("pullRequestId"),
                title=str(item.get("title", "")),
                state="open" if item.get("status") == "active" else "closed",
                source_branch=str(item.get("sourceRefName", "")).replace("refs/heads/", ""),
                target_branch=str(item.get("targetRefName", "")).replace("refs/heads/", ""),
                author=str((item.get("createdBy") or {}).get("displayName", "")),
                created_at=item.get("creationDate"),
                merged=item.get("status") == "completed",
            )
            for item in items
            if isinstance(item, dict)
        )

    def health(self) -> HealthStatus:
        org = self._org()
        return self._timed_health(
            lambda: self._json(Capability.HEALTH_CHECK, "GET", self._api(f"/{org}/_apis/connectionData"))
        )

    def create_pull_request(
        self, repository: str, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        project, repo = self._project_repo(repository)
        org = self._org()
        data = self._json(
            Capability.PULL_REQUEST_CREATE,
            "POST",
            self._api(f"/{org}/{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}/pullrequests"),
            body={
                "sourceRefName": f"refs/heads/{head}",
                "targetRefName": f"refs/heads/{base}",
                "title": title,
                "description": body,
            },
            write=True,
        )
        return PullRequest(
            pull_request_id=str(data.get("pullRequestId", "")),
            number=data.get("pullRequestId"),
            title=str(data.get("title", title)),
            state="open",
            source_branch=str(data.get("sourceRefName", head)).replace("refs/heads/", ""),
            target_branch=str(data.get("targetRefName", base)).replace("refs/heads/", ""),
            created_at=data.get("creationDate"),
        )

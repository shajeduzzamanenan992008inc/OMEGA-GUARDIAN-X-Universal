"""AWS CodeCommit adapter.

CodeCommit exposes a JSON 1.1 RPC API signed with AWS Signature Version 4.
Requests are POSTs but semantically read-only for every implemented operation.

Credentials come from ``OGX_AWS_ACCESS_KEY_ID`` / ``OGX_AWS_SECRET_ACCESS_KEY``
(or the standard ``AWS_*`` names). A session token is supported when present.

CodeCommit has no list-commits or issue API, so those capabilities are reported
as unsupported rather than emulated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from ..base import ProviderAdapter
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
from ..security import validate_api_base
from ..transport import HttpTransport, RequestFn

_SERVICE = "codecommit"
_TARGET_PREFIX = "CodeCommit_20150413"


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


class CodeCommitAdapter(ProviderAdapter):
    supports_pagination = False
    provider_id = "aws_codecommit"
    display_name = "AWS CodeCommit"
    kind = ProviderKind.GIT_HOSTING
    version = "1.0.0"
    api_version = "2015-04-13"
    default_api_base = "https://codecommit.us-east-1.amazonaws.com"
    credential_env = ("OGX_AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    self_hosted = False
    docs_url = "https://docs.aws.amazon.com/codecommit/latest/APIReference/"
    required_permissions = (
        "codecommit:GetRepository",
        "codecommit:ListRepositories",
        "codecommit:ListBranches",
        "codecommit:GetCommit",
        "codecommit:GetFile",
        "codecommit:GetFolder",
        "codecommit:ListPullRequests",
        "codecommit:CreatePullRequest (approval-gated)",
    )
    limitations = (
        "CodeCommit has no commit-listing, issue, permission or rate-limit API; those capabilities are unsupported.",
        "Commit history is not enumerated because the service exposes GetCommit only.",
        "Read-only operations are signed POSTs; the transport never retries state-changing calls.",
    )

    def __init__(
        self,
        *,
        region: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        session_token: str | None = None,
        api_base: str | None = None,
        transport: HttpTransport | None = None,
        environ: dict[str, str] | None = None,
        timeout: float = 15.0,
        request_fn: RequestFn | None = None,
        max_retries: int = 2,
        allow_loopback_http: bool | None = None,
        allow_private_network: bool | None = None,
        sleep=None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.region = (
            region
            or env.get("OGX_AWS_REGION")
            or env.get("AWS_REGION")
            or env.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        ).strip()
        self._access_key = (
            access_key
            or env.get("OGX_AWS_ACCESS_KEY_ID")
            or env.get("AWS_ACCESS_KEY_ID")
            or ""
        ).strip()
        self._secret_key = (
            secret_key
            or env.get("OGX_AWS_SECRET_ACCESS_KEY")
            or env.get("AWS_SECRET_ACCESS_KEY")
            or ""
        ).strip()
        self._session_token = (
            session_token
            or env.get("OGX_AWS_SESSION_TOKEN")
            or env.get("AWS_SESSION_TOKEN")
            or ""
        ).strip() or None
        if not api_base:
            api_base = f"https://codecommit.{self.region}.amazonaws.com"
        super().__init__(
            api_base=api_base,
            credential=self._access_key if (self._access_key and self._secret_key) else None,
            transport=transport,
            environ=env,
            timeout=timeout,
            request_fn=request_fn,
            max_retries=max_retries,
            allow_loopback_http=allow_loopback_http,
            allow_private_network=allow_private_network,
            sleep=sleep,
        )
        self.api_base = validate_api_base(
            api_base,
            allow_loopback_http=self.allow_loopback_http,
            allow_private_network=self.allow_private_network,
        )

    # ------------------------------------------------------------------ signing
    def _signed_headers(self, target: str, body: bytes) -> dict[str, str]:
        host = urlsplit(self.api_base).hostname or ""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        canonical_headers_map = {
            "content-type": "application/x-amz-json-1.1",
            "host": host,
            "x-amz-date": amz_date,
            "x-amz-target": target,
        }
        if self._session_token:
            canonical_headers_map["x-amz-security-token"] = self._session_token
        signed_headers = ";".join(sorted(canonical_headers_map))
        canonical_headers = "".join(
            f"{name}:{canonical_headers_map[name]}\n" for name in sorted(canonical_headers_map)
        )
        payload_hash = hashlib.sha256(body).hexdigest()
        canonical_request = "\n".join(
            ["POST", "/", "", canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/{self.region}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        key = f"AWS4{self._secret_key}".encode("utf-8")
        signing_key = _sign(_sign(_sign(_sign(key, date_stamp), self.region), _SERVICE), "aws4_request")
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Date": amz_date,
            "X-Amz-Target": target,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }
        if self._session_token:
            headers["X-Amz-Security-Token"] = self._session_token
        return headers

    def _call(self, capability: Capability, action: str, payload: dict[str, Any]) -> Any:
        self.require(capability)
        import json

        body = json.dumps(payload).encode("utf-8")
        headers = self._signed_headers(f"{_TARGET_PREFIX}.{action}", body)
        response = self._transport.send(
            "POST",
            self.api_base + "/",
            headers=headers,
            raw_body=body,
            write=False,
            expected_host=urlsplit(self.api_base).hostname,
            provider=self.provider_id,
        )
        return response.json(provider=self.provider_id)

    # -------------------------------------------------------------- read ops
    @staticmethod
    def _owner_name(repository: str) -> tuple[str, str]:
        """CodeCommit repositories are flat names inside one AWS account."""

        cleaned = str(repository or "").strip().strip("/")
        if not cleaned:
            raise ValueError("A CodeCommit repository name is required.")
        return "", cleaned.rsplit("/", 1)[-1]

    def get_repository(self, repository: str) -> RepositoryMetadata:
        _, name = self._owner_name(repository)
        data = self._call(Capability.REPOSITORY_METADATA, "GetRepository", {"repositoryName": name})
        meta = (data.get("repositoryMetadata") or {})
        return RepositoryMetadata(
            repository_id=str(meta.get("repositoryId", "")),
            name=str(meta.get("repositoryName", name)),
            full_name=str(meta.get("repositoryName", name)),
            owner=str(meta.get("accountId", "")),
            provider=self.provider_id,
            description=str(meta.get("repositoryDescription") or ""),
            default_branch=str(meta.get("defaultBranch") or "").replace("refs/heads/", ""),
            clone_url=meta.get("cloneUrlHttp"),
            ssh_url=meta.get("cloneUrlSsh"),
            created_at=_epoch(meta.get("creationDate")),
            updated_at=_epoch(meta.get("lastModifiedDate")),
            size_kb=int(meta.get("repositorySizeKb") or 0),
            extra={"arn": meta.get("Arn"), "account_id": meta.get("accountId")},
        )

    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        names: list[str] = []
        token: str | None = None
        pages = 0
        while len(names) < min(limit, 100) and pages < 10:
            payload: dict[str, Any] = {"maxResults": 100}
            if token:
                payload["nextToken"] = token
            data = self._call(Capability.REPOSITORY_DISCOVERY, "ListRepositories", payload)
            names.extend(str(item.get("repositoryName", "")) for item in data.get("repositories") or [])
            token = data.get("nextToken")
            pages += 1
            if not token:
                break
        names = [name for name in names if name][: min(limit, 50)]
        results = []
        for name in names:
            try:
                results.append(self.get_repository(name))
            except Exception:  # noqa: BLE001 - a single denied repo must not fail discovery
                continue
        return tuple(results)

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        _, name = self._owner_name(repository)
        data = self._call(
            Capability.BRANCH_LIST, "ListBranches", {"repositoryName": name}
        )
        return tuple(
            Branch(name=str(branch), commit_sha="")
            for branch in (data.get("branches") or [])[:limit]
        )

    def get_commit(self, repository: str, commit_sha: str) -> Commit:
        if not commit_sha or len(commit_sha) > 100:
            raise ValueError("A commit SHA is required.")
        _, name = self._owner_name(repository)
        data = self._call(
            Capability.COMMIT_INSPECT,
            "GetCommit",
            {"repositoryName": name, "commitId": commit_sha},
        )
        commit = data.get("commit") or {}
        author = commit.get("author") or {}
        return Commit(
            sha=str(commit.get("commitId", commit_sha)),
            message=str(commit.get("message", "")),
            author=str(author.get("name", "")),
            author_email=str(author.get("email", "")),
            authored_at=_epoch(author.get("date")),
            committer=str((commit.get("committer") or {}).get("name", "")),
            parents=tuple(str(p) for p in commit.get("parents") or ()),
        )

    def read_file(self, repository: str, path: str, ref: str | None = None) -> FileContent:
        if not path:
            raise ValueError("A file path is required.")
        _, name = self._owner_name(repository)
        payload: dict[str, Any] = {"repositoryName": name, "filePath": path.lstrip("/")}
        if ref:
            payload["commitSpecifier"] = ref
        data = self._call(Capability.FILE_READ, "GetFile", payload)
        encoded = data.get("fileContent") or ""
        raw = base64.b64decode(encoded) if encoded else b""
        return FileContent(
            path=path,
            sha=str(data.get("blobId", "")),
            size=len(raw),
            content=raw.decode("utf-8", "replace"),
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        _, name = self._owner_name(repository)
        entries: list[TreeEntry] = []
        queue = [path.strip("/")] if path.strip("/") else [""]
        depth = 0
        while queue and len(entries) < 1000 and depth < 8:
            current = queue.pop(0)
            payload: dict[str, Any] = {"repositoryName": name, "folderPath": current}
            if ref:
                payload["commitSpecifier"] = ref
            data = self._call(Capability.TREE_READ, "GetFolder", payload)
            for item in data.get("files") or ():
                entries.append(
                    TreeEntry(path=str(item.get("absolutePath", "")).lstrip("/"),
                              type="blob", sha=str(item.get("blobId", "")),
                              size=item.get("size"))
                )
            for item in data.get("subFolders") or ():
                sub = str(item.get("absolutePath", "")).lstrip("/")
                if recursive and sub:
                    queue.append(sub)
                else:
                    entries.append(
                        TreeEntry(path=sub, type="tree", sha=str(item.get("treeId", "")))
                    )
            depth += 1
            if not recursive:
                break
        return tuple(entries)

    def list_pull_requests(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[PullRequest, ...]:
        _, name = self._owner_name(repository)
        status = {"open": "OPEN", "closed": "CLOSED", "merged": "CLOSED", "all": None}.get(
            state, "OPEN"
        )
        payload: dict[str, Any] = {"repositoryName": name}
        if status:
            payload["pullRequestStatus"] = status
        data = self._call(Capability.PULL_REQUEST_LIST, "ListPullRequests", payload)
        results = []
        for pr_id in (data.get("pullRequestIds") or [])[: min(limit, 25)]:
            try:
                detail = self._call(
                    Capability.PULL_REQUEST_LIST,
                    "GetPullRequest",
                    {"pullRequestId": str(pr_id)},
                ).get("pullRequest") or {}
            except Exception:  # noqa: BLE001
                continue
            results.append(
                PullRequest(
                    pull_request_id=str(detail.get("pullRequestId", pr_id)),
                    number=int(detail.get("pullRequestId", 0) or 0) or None,
                    title=str(detail.get("title", "")),
                    state=str(detail.get("pullRequestStatus", "")).lower() or "unknown",
                    source_branch=str((detail.get("pullRequestTargets") or [{}])[0].get("sourceReference", "")).replace("refs/heads/", ""),
                    target_branch=str((detail.get("pullRequestTargets") or [{}])[0].get("destinationReference", "")).replace("refs/heads/", ""),
                    author=str(detail.get("authorArn", "")),
                    created_at=_epoch(detail.get("creationDate")),
                )
            )
        return tuple(results)

    def health(self) -> HealthStatus:
        return self._timed_health(
            lambda: self._call(Capability.HEALTH_CHECK, "ListRepositories", {"maxResults": 1})
        )

    def create_pull_request(
        self, repository: str, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        _, name = self._owner_name(repository)
        data = self._call(
            Capability.PULL_REQUEST_CREATE,
            "CreatePullRequest",
            {
                "repositoryName": name,
                "title": title,
                "description": body,
                "sourceReference": f"refs/heads/{head}",
                "targets": [{"repositoryName": name, "sourceReference": f"refs/heads/{head}",
                             "destinationReference": f"refs/heads/{base}"}],
            },
        )
        detail = data.get("pullRequest") or {}
        return PullRequest(
            pull_request_id=str(detail.get("pullRequestId", "")),
            number=int(detail.get("pullRequestId", 0) or 0) or None,
            title=str(detail.get("title", title)),
            state=str(detail.get("pullRequestStatus", "OPEN")).lower(),
            source_branch=head,
            target_branch=base,
            created_at=_epoch(detail.get("creationDate")),
        )


def _epoch(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return None

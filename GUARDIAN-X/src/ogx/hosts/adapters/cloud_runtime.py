"""Cloud workspace host adapter (HOST-11).

Provider selection: **GitHub Codespaces**, because it is the one cloud workspace
family whose API and auth model are documented and already share this project's
GitHub credential path. Nothing is invented: the adapter calls
``https://api.github.com`` endpoints documented for Codespaces and reports every
operation it does not implement as unsupported.

Implemented (read-only):

* ``GET /user/codespaces`` — the authenticated user's codespaces,
* ``GET /repos/{owner}/{repo}/codespaces`` — codespaces for one repository,
* ``GET /user/codespaces/{name}`` — one codespace's metadata.

Deliberately **not** implemented and not advertised: create/delete/start/stop,
exec, port forwarding, file read/write, repository writes. Those stay
``unsupported`` so the capability matrix cannot over-claim.

Safety properties: https-only with pinned host validation, bearer token read
from ``OGX_GITHUB_TOKEN`` (never from a URL, never logged, never returned),
bounded responses, bounded page size, redacted errors.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)
from ...platform.transport import HttpTransport

API_HOST = "api.github.com"
API_BASE = f"https://{API_HOST}"
TOKEN_ENV = "OGX_GITHUB_TOKEN"
MAX_PAGE_SIZE = 100
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")

_CAPABILITIES: Dict[str, str] = {
    "workspace_read": "supported",
    "workspace_create": "unsupported",
    "workspace_delete": "unsupported",
    "workspace_start": "unsupported",
    "workspace_stop": "unsupported",
    "workspace_exec": "unsupported",
    "workspace_execute": "unsupported",
    "repository_metadata_read": "unsupported",
    "repository_structure_read": "unsupported",
    "file_read": "unsupported",
    "file_write": "unsupported",
    "file_system_access": "unsupported",
    "repository_write": "unsupported",
    "branch_read": "unsupported",
    "commit_read": "unsupported",
    "branch_create": "unsupported",
    "commit_create": "unsupported",
    "push": "unsupported",
    "fetch": "unsupported",
    "clone": "unsupported",
    "pull_request_create": "unsupported",
    "pull_request_merge": "unsupported",
    "testing": "unsupported",
    "deployment": "unsupported",
}


class CloudWorkspaceError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "cloud_workspace_error") -> None:
        super().__init__(message, code=code)


class CloudCredentialMissing(CloudWorkspaceError):
    def __init__(self, message: str = f"{TOKEN_ENV} is required for a cloud workspace host") -> None:
        super().__init__(message, code="credential_required")


class CloudProviderUnsupported(CloudWorkspaceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="cloud_provider_unsupported")


class CloudResponseError(CloudWorkspaceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="cloud_response_invalid")


class CloudRequestError(CloudWorkspaceError):
    def __init__(self, message: str, *, code: str = "cloud_request_failed") -> None:
        super().__init__(message, code=code)


def _redact(value: str) -> str:
    """Remove anything that looks like a GitHub credential from a message."""

    text = (value or "").strip()
    text = re.sub(
        r"\b(gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,})\b",
        "[redacted-token]",
        text,
    )
    return text[:400]


def _normalize_codespace(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CloudResponseError("A codespace entry was not an object.")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CloudResponseError("A codespace entry is missing its name.")
    repository = raw.get("repository") if isinstance(raw.get("repository"), dict) else {}
    machine = raw.get("machine") if isinstance(raw.get("machine"), dict) else {}
    git_status = raw.get("git_status") if isinstance(raw.get("git_status"), dict) else {}
    return {
        "workspace_id": name.strip(),
        "name": name.strip(),
        "display_name": str(raw.get("display_name") or name)[:200],
        "state": str(raw.get("state") or "unknown")[:40],
        "workspace_type": "codespace",
        "provider_type": "github_codespaces",
        "location": str(raw.get("location") or "")[:80],
        "repository": str(repository.get("full_name") or "")[:200] or None,
        "machine": str(machine.get("name") or "")[:120] or None,
        "machine_display_name": str(machine.get("display_name") or "")[:120] or None,
        "operating_system": str(machine.get("operating_system") or "")[:40] or None,
        "web_url": str(raw.get("web_url") or "")[:300] or None,
        "created_at": str(raw.get("created_at") or "")[:40] or None,
        "last_used_at": str(raw.get("last_used_at") or "")[:40] or None,
        "branch": str(git_status.get("ref") or "")[:200] or None,
        "devcontainer_path": str(raw.get("devcontainer_path") or "")[:300] or None,
        # Never expose credentials or environment secrets.
        "capabilities": ["workspace_read"],
    }


class CloudWorkspaceHostAdapter(HostAdapter):
    """Read-only GitHub Codespaces inspection."""

    host_type = "cloud_workspace"

    def __init__(self, config, *, transport: Optional[HttpTransport] = None,
                 environ: Optional[dict[str, str]] = None) -> None:
        super().__init__(config)
        self._transport = transport
        self._environ = dict(environ) if environ is not None else None
        self._connected = False
        self._provider: str = ""
        self._repository: Optional[str] = None
        self._codespace: Optional[str] = None

    # ------------------------------------------------------------------ config
    def _env(self, name: str) -> str:
        source = self._environ if self._environ is not None else None
        if source is not None:
            return str(source.get(name) or "").strip()
        import os

        return str(os.environ.get(name) or "").strip()

    def _parse_config(self) -> None:
        metadata = dict(self.config.metadata or {})
        provider = str(metadata.get("provider") or metadata.get("cloud_provider") or "github_codespaces").strip()
        if provider not in {"github_codespaces", "github"}:
            raise CloudProviderUnsupported(
                f"Unsupported cloud workspace provider: {provider}. "
                "The implemented provider is github_codespaces."
            )
        self._provider = "github_codespaces"
        root = str(self.config.root or "").strip()
        repository = metadata.get("repository") or metadata.get("repository_full_name")
        codespace = metadata.get("codespace") or metadata.get("workspace")
        if root.startswith("host:"):
            # create_session() labels metadata-only hosts with an opaque root.
            root = "codespaces"
        if root and root not in {"codespaces", "github_codespaces", "/", "."}:
            if _REPOSITORY_RE.match(root):
                repository = repository or root
            elif root.lower() not in {"user", "me"}:
                raise CloudWorkspaceError(
                    "A cloud workspace root must be 'codespaces' or 'owner/repository'.",
                    code="invalid_config",
                )
        if repository is not None and not _REPOSITORY_RE.match(str(repository)):
            raise CloudWorkspaceError("repository must be 'owner/name'.", code="invalid_config")
        self._repository = str(repository) if repository else None
        if codespace is not None and not _NAME_RE.match(str(codespace)):
            raise CloudWorkspaceError("Invalid codespace name.", code="invalid_config")
        self._codespace = str(codespace) if codespace else None

    def _token(self) -> str:
        token = self._env(TOKEN_ENV)
        if not token:
            raise CloudCredentialMissing()
        return token

    def _client(self) -> HttpTransport:
        if self._transport is None:
            self._transport = HttpTransport(
                timeout=min(float(self.config.timeout_seconds), 30.0),
                max_response_bytes=min(self.config.max_file_bytes * 8, 2_000_000),
                max_retries=1,
                describe="github codespaces",
            )
        return self._transport

    # ---------------------------------------------------------------- requests
    def _get(self, path: str, *, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{API_BASE}{path}"
        try:
            response = self._client().send(
                "GET", url,
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params=params,
                write=False,
                expected_host=API_HOST,
                provider="github_codespaces",
            )
        except CloudWorkspaceError:
            raise
        except Exception as exc:  # transport errors already carry bounded detail
            raise CloudRequestError(
                f"GitHub Codespaces request failed: {_redact(type(exc).__name__)}"
            ) from exc

        if response.status in {401, 403}:
            raise CloudRequestError(
                "GitHub refused the credential or its scope for Codespaces.",
                code="cloud_permission_denied",
            )
        if response.status == 404:
            raise CloudRequestError("The requested codespace was not found.",
                                    code="cloud_not_found")
        if response.status == 429:
            raise CloudRequestError("GitHub rate limit reached for Codespaces.",
                                    code="cloud_rate_limited")
        if response.status >= 400:
            raise CloudRequestError(
                f"GitHub Codespaces returned HTTP {response.status}.", code="cloud_http_error"
            )
        try:
            payload = response.json(provider="github_codespaces")
        except Exception as exc:
            raise CloudResponseError("GitHub Codespaces returned a non-JSON response.") from exc
        return payload

    def _list_raw(self, limit: int) -> list[Any]:
        page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
        if self._repository:
            payload = self._get(
                f"/repos/{self._repository}/codespaces", params={"per_page": page_size}
            )
        else:
            payload = self._get("/user/codespaces", params={"per_page": page_size})
        if not isinstance(payload, dict):
            raise CloudResponseError("Unexpected Codespaces list response.")
        items = payload.get("codespaces")
        if not isinstance(items, list):
            raise CloudResponseError("Codespaces list response has no 'codespaces' array.")
        for item in items[:page_size]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise CloudResponseError("A codespace entry is malformed.")
        return items[:page_size]

    def _find(self, name: str) -> dict[str, Any]:
        if not _NAME_RE.match(name):
            raise CloudWorkspaceError("Invalid codespace name.", code="invalid_config")
        payload = self._get(f"/user/codespaces/{name}")
        return _normalize_codespace(payload)

    # ------------------------------------------------------------- host contract
    def validate(self) -> None:
        self._parse_config()
        self._token()

    def connect(self) -> None:
        self._parse_config()
        token = self._token()
        if not token:
            raise CloudCredentialMissing()
        # A bounded probe decides whether the session is real. A 403 here means
        # the token exists but lacks the codespace scope, which is a failure.
        self._list_raw(1)
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> HostHealth:
        if not self._connected:
            return HostHealth(
                state=HealthState.UNKNOWN,
                checked_at=datetime.now(timezone.utc),
                issues=("not connected",),
            )
        started = datetime.now(timezone.utc)
        try:
            self._list_raw(1)
        except CloudWorkspaceError as exc:
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=datetime.now(timezone.utc),
                issues=(_redact(str(exc)),),
            )
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return HostHealth(
            state=HealthState.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            details={"provider": self._provider, "scope": self._repository or "user"},
            latency_ms=latency,
        )

    def capabilities(self) -> Dict[str, str]:
        return dict(_CAPABILITIES)

    def metadata(self) -> dict:
        self._parse_config()
        return {
            "provider": self._provider,
            "api_base": API_BASE,
            "scope": self._repository or "user",
            "codespace": self._codespace,
            "credential_env": TOKEN_ENV,
            "capabilities": self.capabilities(),
        }

    # ------------------------------------------------------------ workspace ops
    def list_workspaces(self, limit: int = 50) -> dict[str, Any]:
        if not self._connected:
            raise CloudRequestError("Cloud workspace host is not connected.",
                                    code="not_connected")
        if not 1 <= int(limit) <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}.")
        items = [_normalize_codespace(raw) for raw in self._list_raw(int(limit))]
        if self._codespace:
            items = [item for item in items if item["name"] == self._codespace]
        return {
            "provider": self._provider,
            "scope": self._repository or "user",
            "items": items,
            "total": len(items),
        }

    def inspect_workspace(self, name: str) -> dict[str, Any]:
        if not self._connected:
            raise CloudRequestError("Cloud workspace host is not connected.",
                                    code="not_connected")
        return self._find(name)

    def describe(self) -> dict:
        return {
            "host_type": self.host_type,
            "root": self.config.root,
            "provider": self._provider or "github_codespaces",
            "capabilities": self.capabilities(),
        }


__all__ = [
    "API_BASE",
    "API_HOST",
    "CloudCredentialMissing",
    "CloudProviderUnsupported",
    "CloudRequestError",
    "CloudResponseError",
    "CloudWorkspaceError",
    "CloudWorkspaceHostAdapter",
    "MAX_PAGE_SIZE",
    "TOKEN_ENV",
]

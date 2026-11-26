"""GitHub host adapter over the canonical provider engine.

This is a host-session wrapper, not a second HTTP implementation: reads delegate
to :class:`ogx.platform.adapters.github.GitHubAdapter` (validated HTTPS base,
pinned DNS, bounded retries and bounded bodies). Writes exist only for the
pull-request operation the platform engine already supports, and they require a
validated plan plus explicit approval.

Honest limits:

* No branch creation, file update, push, workflow dispatch, deployment or merge
  is implemented here, and none is advertised.
* ``connect()`` probes the remote API; a session is never reported connected
  without a successful probe.
* The provider engine requires a credential for every repository capability,
  including public reads. Without ``OGX_GITHUB_TOKEN`` a session therefore
  refuses to connect with ``credential_required`` rather than pretending an
  unauthenticated read is supported.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)
from ...config import state_dir
from ...platform.capabilities import Capability, CapabilityState

_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9_.-]{1,100}$")
_READ_CAPABILITIES: dict[str, Capability] = {
    "repository_metadata_read": Capability.REPOSITORY_METADATA,
    "repository_structure_read": Capability.TREE_READ,
    "branch_read": Capability.BRANCH_LIST,
    "commit_read": Capability.COMMIT_LIST,
    "file_read": Capability.FILE_READ,
}
#: Declared but not implemented by the platform GitHub adapter.
_UNSUPPORTED = (
    "file_write",
    "branch_create",
    "branch_delete",
    "commit_create",
    "push",
    "pull_request_merge",
    "deployment",
    "testing",
    "workspace_exec",
    "file_system_access",
    "fetch",
    "clone",
)


class GitHubHostError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "github_host_error") -> None:
        super().__init__(message, code=code)


def _state_of(state: CapabilityState) -> str:
    if state is CapabilityState.SUPPORTED:
        return "supported"
    if state is CapabilityState.PARTIAL:
        return "partial"
    if state in {CapabilityState.REQUIRES_AUTH, CapabilityState.REQUIRES_PERMISSION}:
        return "requires_auth"
    if state is CapabilityState.REQUIRES_WORKSPACE:
        return "requires_workspace"
    if state is CapabilityState.NOT_CONFIGURED:
        return "not_configured"
    return "unsupported"


class GitHubHostAdapter(HostAdapter):
    """Read-capable GitHub host with one approval-gated write operation."""

    host_type = "github"

    def __init__(self, config, platform_adapter: Optional[Any] = None) -> None:
        super().__init__(config)
        self._adapter = platform_adapter
        self._repository = ""
        self._connected = False

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _repository_name(value: str) -> str:
        raw = str(value or "").strip().strip("/")
        if not _IDENTIFIER.fullmatch(raw) or ".." in raw:
            raise GitHubHostError(
                "A GitHub host identifier must be 'owner/repository'.", code="invalid_identifier"
            )
        return raw

    def _platform(self):
        if self._adapter is None:
            from ...platform.adapters.github import GitHubAdapter

            api_base = str(self.config.metadata.get("api_base") or "").strip() or None
            self._adapter = GitHubAdapter(api_base=api_base)
        return self._adapter

    def _require_connected(self) -> None:
        if not self._connected or not self._repository:
            raise GitHubHostError("GitHub host session is not connected.", code="not_connected")

    def _capability_states(self) -> dict[Capability, CapabilityState]:
        adapter = self._platform()
        try:
            return dict(adapter.capability_states())
        except Exception as exc:  # noqa: BLE001 - a broken adapter reports failure
            raise GitHubHostError(
                f"GitHub capability negotiation failed: {type(exc).__name__}",
                code="capability_failed",
            ) from exc

    # ---------------------------------------------------------------- lifecycle
    def validate(self) -> None:
        self._repository_name(self.config.root)
        self._platform()  # validates the API base, never contacts the network
        self._repository = self._repository_name(self.config.root)

    def connect(self) -> None:
        self.validate()
        adapter = self._platform()
        if not getattr(adapter, "credential", None):
            raise GitHubHostError(
                "GitHub host sessions require a credential in OGX_GITHUB_TOKEN;"
                " the provider engine refuses unauthenticated repository operations",
                code="credential_required",
            )
        try:
            health = adapter.health()
        except Exception as exc:  # noqa: BLE001 - convert to a stable host error
            raise GitHubHostError(
                f"GitHub API probe failed: {type(exc).__name__}", code="unreachable"
            ) from exc
        if str(getattr(health, "status", "")) != "healthy":
            raise GitHubHostError(
                "GitHub API probe did not report healthy; refusing to open the session.",
                code="unreachable",
            )
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._repository = ""

    def health(self) -> HostHealth:
        self.validate()
        try:
            probe = self._platform().health()
        except Exception as exc:  # noqa: BLE001 - health is an observation
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=datetime.now(timezone.utc),
                issues=(f"probe failed: {type(exc).__name__}",),
                details={"repository": self._repository or self.config.root},
            )
        status = str(getattr(probe, "status", "down"))
        return HostHealth(
            state=HealthState.HEALTHY if status == "healthy" else HealthState.DEGRADED,
            checked_at=datetime.now(timezone.utc),
            issues=() if status == "healthy" else (str(getattr(probe, "detail", "") or status),),
            details={
                "repository": self._repository or self.config.root,
                "latency_ms": getattr(probe, "latency_ms", None),
                "probe": status,
            },
        )

    def capabilities(self) -> dict[str, str]:
        states = self._capability_states()
        result: dict[str, str] = {}
        for name, capability in _READ_CAPABILITIES.items():
            result[name] = _state_of(states.get(capability, CapabilityState.UNSUPPORTED))
        result["pull_request_create"] = _state_of(
            states.get(Capability.PULL_REQUEST_CREATE, CapabilityState.UNSUPPORTED)
        )
        for name in _UNSUPPORTED:
            result[name] = "unsupported"
        result["workspace_read"] = "unsupported"
        result["workspace_create"] = "unsupported"
        result["workspace_delete"] = "unsupported"
        result["workspace_start"] = "unsupported"
        result["workspace_stop"] = "unsupported"
        return result

    def describe(self) -> dict:
        return {
            "host_type": self.host_type,
            "root": self.config.root,
            "repository": self._repository,
            "connected": self._connected,
            "capabilities": self.capabilities(),
        }

    # --------------------------------------------------------------- read ops
    def metadata(self) -> dict:
        self._require_connected()
        return self._platform().get_repository(self._repository).to_dict()

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> dict:
        self._require_connected()
        limit = int(max_bytes if max_bytes is not None else self.config.max_file_bytes)
        limit = max(1, min(limit, 4 * 1024 * 1024))
        file = self._platform().read_file(self._repository, path)
        content = str(file.content or "")
        truncated = bool(file.truncated) or len(content.encode("utf-8")) > limit
        if len(content.encode("utf-8")) > limit:
            content = content.encode("utf-8")[:limit].decode("utf-8", "replace")
        return {
            "path": file.path,
            "sha": file.sha,
            "size": file.size,
            "content": content,
            "truncated": truncated,
            "encoding": file.encoding,
        }

    def list_directory(self, path: str = "") -> dict:
        self._require_connected()
        entries = self._platform().read_tree(self._repository, path)
        return {
            "path": path,
            "entries": [entry.to_dict() for entry in entries][: self.config.max_directory_entries],
            "truncated": len(entries) > self.config.max_directory_entries,
        }

    def branches(self, limit: int = 100) -> list[dict]:
        self._require_connected()
        return [branch.to_dict() for branch in self._platform().list_branches(self._repository, limit=limit)]

    def commits(self, *, branch: str | None = None, limit: int = 50) -> list[dict]:
        self._require_connected()
        return [
            commit.to_dict()
            for commit in self._platform().list_commits(self._repository, branch=branch, limit=limit)
        ]

    def pull_requests(self, *, state: str = "open", limit: int = 50) -> list[dict]:
        self._require_connected()
        return [
            item.to_dict()
            for item in self._platform().list_pull_requests(self._repository, state=state, limit=limit)
        ]

    # -------------------------------------------------------------- write ops
    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        approved: bool,
        actor_subject: str | None = None,
    ) -> dict:
        """Create a draft pull request. Unapproved calls are refused."""

        self._require_connected()
        from ...platform.operations import execute_pull_request, plan_pull_request

        adapter = self._platform()
        plan = plan_pull_request(
            adapter,
            self._repository,
            title=title,
            body=body,
            head=head,
            base=base,
        )
        result = execute_pull_request(
            plan,
            adapter,
            approved=approved,
            actor_subject=actor_subject,
            audit_path=state_dir() / "platform" / "audit.jsonl",
        )
        return result.to_dict()

"""Provider-backed Git host adapters for GitLab, Bitbucket, Gitea, Forgejo,
Azure DevOps and AWS CodeCommit.

These adapters are host-session wrappers over the canonical provider engine in
:mod:`ogx.platform.adapters`; they add no second HTTP stack and no new
credential handling. Reads delegate to the provider adapter (validated HTTPS
base, pinned DNS, bounded retries and bounded bodies), and the only write is
the pull/merge-request creation the platform engine already supports, which
requires a validated plan plus explicit approval.

Honest limits:

* No branch creation, file update, push, merge, deployment or execution is
  implemented or advertised for these hosts.
* Capabilities are derived from the provider adapter's own introspection, so a
  missing operation is reported as ``unsupported`` instead of being emulated.
* ``connect()`` requires a credential and a successful remote probe.
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Optional

from ..lifecycle import HostLifecycleError
from .github_runtime import GitHubHostAdapter

#: host type -> (provider module, adapter class)
_PROVIDER_ADAPTERS: dict[str, tuple[str, str]] = {
    "gitlab": ("gitlab", "GitLabAdapter"),
    "bitbucket": ("bitbucket", "BitbucketAdapter"),
    "gitea": ("gitea", "GiteaAdapter"),
    "forgejo": ("gitea", "ForgejoAdapter"),
    "azure_devops": ("azure_devops", "AzureDevOpsAdapter"),
    "aws_codecommit": ("aws_codecommit", "CodeCommitAdapter"),
}

PROVIDER_HOST_TYPES: tuple[str, ...] = tuple(_PROVIDER_ADAPTERS)

_PROJECT_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9_.-]{1,100}$"
)
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class ProviderHostError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "provider_host_error") -> None:
        super().__init__(message, code=code)


def _adapter_class(host_type: str):
    module_name, class_name = _PROVIDER_ADAPTERS[host_type]
    module = importlib.import_module(f"ogx.platform.adapters.{module_name}")
    return getattr(module, class_name)


class ProviderGitHostAdapter(GitHubHostAdapter):
    """Approval-gated Git host session over a canonical provider adapter."""

    def __init__(
        self,
        config,
        *,
        provider_id: str,
        platform_adapter: Optional[Any] = None,
    ) -> None:
        normalized = str(provider_id or "").strip().lower()
        if normalized not in _PROVIDER_ADAPTERS:
            raise ProviderHostError(
                f"No provider host adapter exists for '{provider_id}'.",
                code="unsupported_host_type",
            )
        self.host_type = normalized
        self.provider_id = normalized
        super().__init__(config, platform_adapter=platform_adapter)

    # ------------------------------------------------------------------ helpers
    def _repository_name(self, value: str) -> str:
        raw = str(value or "").strip().strip("/")
        pattern = _REPOSITORY_NAME if self.provider_id == "aws_codecommit" else _PROJECT_REPOSITORY
        if not pattern.fullmatch(raw) or ".." in raw:
            expected = (
                "a CodeCommit repository name"
                if self.provider_id == "aws_codecommit"
                else "'project/repository' (or 'owner/repository')"
            )
            raise ProviderHostError(
                f"A {self.provider_id} host identifier must be {expected}.",
                code="invalid_identifier",
            )
        return raw

    def _platform(self):
        if self._adapter is None:
            metadata = dict(self.config.metadata or {})
            api_base = str(metadata.get("api_base") or "").strip() or None
            kwargs: dict[str, Any] = {}
            if api_base:
                kwargs["api_base"] = api_base
            if self.provider_id == "azure_devops" and metadata.get("organization"):
                kwargs["organization"] = str(metadata["organization"])
            if self.provider_id == "aws_codecommit" and metadata.get("region"):
                kwargs["region"] = str(metadata["region"])
            self._adapter = _adapter_class(self.provider_id)(**kwargs)
        return self._adapter

    def connect(self) -> None:
        self.validate()
        adapter = self._platform()
        if not getattr(adapter, "credential", None):
            required = ", ".join(getattr(adapter, "credential_env", ()) or ())
            raise ProviderHostError(
                f"{self.provider_id} host sessions require a credential"
                + (f" in {required}" if required else "")
                + "; the provider engine refuses unauthenticated repository operations",
                code="credential_required",
            )
        try:
            health = adapter.health()
        except Exception as exc:  # noqa: BLE001 - convert to a stable host error
            raise ProviderHostError(
                f"{self.provider_id} API probe failed: {type(exc).__name__}",
                code="unreachable",
            ) from exc
        if str(getattr(health, "status", "")) != "healthy":
            raise ProviderHostError(
                f"{self.provider_id} API probe did not report healthy;"
                " refusing to open the session.",
                code="unreachable",
            )
        self._connected = True

    def describe(self) -> dict:
        payload = super().describe()
        payload["provider_id"] = self.provider_id
        return payload

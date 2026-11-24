"""Normalized provider contract.

Read and write operations are separate methods. Adapters override only the
operations they genuinely implement; everything else raises
:class:`CapabilityUnavailableError`. :meth:`ProviderAdapter.descriptor`
computes the capability matrix by inspecting which methods are overridden, so a
capability can never be reported as supported without code behind it.
"""

from __future__ import annotations

import os
import time
from typing import Any, ClassVar

from .capabilities import (
    Capability,
    CapabilityState,
    ProviderDescriptor,
    ProviderKind,
    UNAUTHENTICATED_CAPABILITIES,
)
from .errors import CapabilityUnavailableError
from .models import (
    AuthStatus,
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
from .security import CredentialPolicy, validate_api_base
from .transport import HttpTransport, RequestFn

#: Capability -> adapter method that implements it.
CAPABILITY_METHODS: dict[Capability, str] = {
    Capability.IDENTIFY: "identify",
    Capability.AUTH_DISCOVERY: "auth_status",
    Capability.HEALTH_CHECK: "health",
    Capability.RATE_LIMIT_READ: "rate_limit",
    Capability.PERMISSION_READ: "get_permissions",
    Capability.REPOSITORY_DISCOVERY: "discover_repositories",
    Capability.REPOSITORY_METADATA: "get_repository",
    Capability.BRANCH_LIST: "list_branches",
    Capability.COMMIT_LIST: "list_commits",
    Capability.COMMIT_INSPECT: "get_commit",
    Capability.FILE_READ: "read_file",
    Capability.TREE_READ: "read_tree",
    Capability.PULL_REQUEST_LIST: "list_pull_requests",
    Capability.PULL_REQUEST_CREATE: "create_pull_request",
    Capability.ISSUE_LIST: "list_issues",
    Capability.ISSUE_CREATE: "create_issue",
    Capability.ISSUE_UPDATE: "update_issue",
    # Note: PAGINATION, API_VERSION are special - no method mapping
}

_descriptor_cache: dict[type, ProviderDescriptor] = {}


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in {"1", "true", "yes", "on"}


class ProviderAdapter:
    """Base class for every provider adapter.

    Adapters are independent: none may import another adapter.
    """

    provider_id: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base provider"
    kind: ClassVar[ProviderKind] = ProviderKind.GIT_HOSTING
    version: ClassVar[str] = "1.0.0"
    api_version: ClassVar[str] = "unknown"
    default_api_base: ClassVar[str] = ""
    credential_env: ClassVar[tuple[str, ...]] = ()
    self_hosted: ClassVar[bool] = False
    docs_url: ClassVar[str] = ""
    limitations: ClassVar[tuple[str, ...]] = ()
    required_permissions: ClassVar[tuple[str, ...]] = ()
    supports_pagination: ClassVar[bool] = False
    implementation_status: ClassVar[str] = "verified"

    def __init__(
        self,
        *,
        api_base: str | None = None,
        credential: str | None = None,
        transport: HttpTransport | None = None,
        environ: dict[str, str] | None = None,
        timeout: float = 15.0,
        request_fn: RequestFn | None = None,
        max_retries: int = 2,
        allow_loopback_http: bool | None = None,
        allow_private_network: bool | None = None,
        sleep=None,
    ) -> None:
        self.environ = environ if environ is not None else dict(os.environ)
        self.policy = CredentialPolicy(self.provider_id, self.credential_env)
        explicit = (credential or "").strip()
        resolved = explicit or self.policy.resolve(self.environ)
        self._credential: str | None = resolved or None
        self.credential_source = "explicit" if explicit else self.policy.source(self.environ)
        self.allow_loopback_http = (
            _truthy(self.environ.get("OGX_ALLOW_LOOPBACK_PROVIDER_HTTP"))
            if allow_loopback_http is None
            else allow_loopback_http
        )
        self.allow_private_network = (
            _truthy(self.environ.get("OGX_ALLOW_PRIVATE_PROVIDER_ENDPOINTS"))
            if allow_private_network is None
            else allow_private_network
        )
        candidate_base = api_base or self.default_api_base
        self.api_base = (
            validate_api_base(
                candidate_base,
                allow_loopback_http=self.allow_loopback_http,
                allow_private_network=self.allow_private_network,
            )
            if candidate_base
            else ""
        )
        self._transport = transport or HttpTransport(
            request_fn=request_fn,
            timeout=timeout,
            max_retries=max_retries,
            allow_loopback_http=self.allow_loopback_http,
            allow_private_network=self.allow_private_network,
            sleep=sleep,
            describe=self.provider_id,
        )

    # ---------------------------------------------------------------- identity
    @classmethod
    def descriptor(cls) -> ProviderDescriptor:
        cached = _descriptor_cache.get(cls)
        if cached is not None:
            return cached
        states: dict[Capability, CapabilityState] = {}
        for capability in Capability:
            if capability in {Capability.IDENTIFY, Capability.AUTH_DISCOVERY}:
                states[capability] = CapabilityState.SUPPORTED
                continue
            if capability is Capability.API_VERSION:
                states[capability] = (
                    CapabilityState.SUPPORTED if cls.api_version else CapabilityState.UNSUPPORTED
                )
                continue
            if capability is Capability.PAGINATION:
                states[capability] = (
                    CapabilityState.SUPPORTED
                    if cls.supports_pagination
                    else CapabilityState.UNSUPPORTED
                )
                continue
            if capability is Capability.HEALTH_CHECK:
                states[capability] = (
                    CapabilityState.SUPPORTED
                    if cls.health is not ProviderAdapter.health
                    else CapabilityState.UNSUPPORTED
                )
                continue
            method_name = CAPABILITY_METHODS.get(capability)
            if not method_name:
                states[capability] = CapabilityState.UNSUPPORTED
                continue
            implemented = getattr(cls, method_name) is not getattr(ProviderAdapter, method_name)
            states[capability] = (
                CapabilityState.SUPPORTED if implemented else CapabilityState.UNSUPPORTED
            )
        descriptor = ProviderDescriptor(
            provider_id=cls.provider_id,
            display_name=cls.display_name,
            kind=cls.kind,
            version=cls.version,
            api_version=cls.api_version,
            default_api_base=cls.default_api_base,
            credential_env=tuple(cls.credential_env),
            self_hosted=cls.self_hosted,
            capabilities=states,
            docs_url=cls.docs_url,
            implementation_status=cls.implementation_status,
            limitations=tuple(cls.limitations),
            required_permissions=tuple(cls.required_permissions),
            pagination=cls.supports_pagination,
        )
        _descriptor_cache[cls] = descriptor
        return descriptor

    def capability_states(self) -> dict[Capability, CapabilityState]:
        descriptor = self.descriptor()
        authenticated = self._credential is not None
        return {
            capability: descriptor.capability_state(
                capability, authenticated=authenticated, configured=True
            )
            for capability in descriptor.capabilities
        }

    def capability_state(self, capability: Capability) -> CapabilityState:
        return self.descriptor().capability_state(
            capability, authenticated=self._credential is not None, configured=True
        )

    def require(self, capability: Capability) -> None:
        """Raise unless ``capability`` is currently available."""

        state = self.capability_state(capability)
        if state.is_available:
            return
        raise CapabilityUnavailableError(
            f"{self.provider_id} cannot perform '{capability.value}': {state.value}.",
            provider=self.provider_id,
            capability=capability.value,
            detail=f"capability_state={state.value}",
        )

    def identify(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "version": self.version,
            "api_version": self.api_version,
            "api_base": self.api_base,
            "self_hosted": self.self_hosted,
            "implementation_status": self.implementation_status,
        }

    def auth_status(self) -> AuthStatus:
        configured = self._credential is not None
        return AuthStatus(
            provider_id=self.provider_id,
            configured=configured,
            authenticated=configured,
            source=self.credential_source,
            detail=(
                "credential present; not verified against the remote API"
                if configured
                else f"set one of {', '.join(self.credential_env) or 'no environment variables'}"
            ),
            required_env=tuple(self.credential_env),
            permission_hint="; ".join(self.required_permissions),
        )

    # ------------------------------------------------------------ read contract
    def health(self) -> HealthStatus:
        self.require(Capability.HEALTH_CHECK)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement health checks.",
            provider=self.provider_id,
            capability=Capability.HEALTH_CHECK.value,
        )

    def rate_limit(self) -> RateLimit:
        self.require(Capability.RATE_LIMIT_READ)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not expose rate-limit information.",
            provider=self.provider_id,
            capability=Capability.RATE_LIMIT_READ.value,
        )

    def get_permissions(self, repository: str) -> PermissionSet:
        self.require(Capability.PERMISSION_READ)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not expose repository permissions.",
            provider=self.provider_id,
            capability=Capability.PERMISSION_READ.value,
        )

    def discover_repositories(
        self, owner: str | None = None, limit: int = 100
    ) -> tuple[RepositoryMetadata, ...]:
        self.require(Capability.REPOSITORY_DISCOVERY)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement repository discovery.",
            provider=self.provider_id,
            capability=Capability.REPOSITORY_DISCOVERY.value,
        )

    def get_repository(self, repository: str) -> RepositoryMetadata:
        self.require(Capability.REPOSITORY_METADATA)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement repository metadata.",
            provider=self.provider_id,
            capability=Capability.REPOSITORY_METADATA.value,
        )

    def list_branches(self, repository: str, limit: int = 100) -> tuple[Branch, ...]:
        self.require(Capability.BRANCH_LIST)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement branch listing.",
            provider=self.provider_id,
            capability=Capability.BRANCH_LIST.value,
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
        self.require(Capability.COMMIT_LIST)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement commit listing.",
            provider=self.provider_id,
            capability=Capability.COMMIT_LIST.value,
        )

    def get_commit(self, repository: str, commit_sha: str) -> Commit:
        self.require(Capability.COMMIT_INSPECT)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement commit inspection.",
            provider=self.provider_id,
            capability=Capability.COMMIT_INSPECT.value,
        )

    def read_file(
        self, repository: str, path: str, ref: str | None = None
    ) -> FileContent:
        self.require(Capability.FILE_READ)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement file reading.",
            provider=self.provider_id,
            capability=Capability.FILE_READ.value,
        )

    def read_tree(
        self,
        repository: str,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
    ) -> tuple[TreeEntry, ...]:
        self.require(Capability.TREE_READ)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement tree reading.",
            provider=self.provider_id,
            capability=Capability.TREE_READ.value,
        )

    def list_pull_requests(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[PullRequest, ...]:
        self.require(Capability.PULL_REQUEST_LIST)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement pull/merge request listing.",
            provider=self.provider_id,
            capability=Capability.PULL_REQUEST_LIST.value,
        )

    def list_issues(
        self, repository: str, state: str = "open", limit: int = 50
    ) -> tuple[Issue, ...]:
        self.require(Capability.ISSUE_LIST)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement issue listing.",
            provider=self.provider_id,
            capability=Capability.ISSUE_LIST.value,
        )

    # ----------------------------------------------------------- write contract
    def create_pull_request(
        self, repository: str, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        self.require(Capability.PULL_REQUEST_CREATE)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement pull/merge request creation.",
            provider=self.provider_id,
            capability=Capability.PULL_REQUEST_CREATE.value,
        )

    def create_issue(self, repository: str, *, title: str, body: str = "") -> Issue:
        self.require(Capability.ISSUE_CREATE)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement issue creation.",
            provider=self.provider_id,
            capability=Capability.ISSUE_CREATE.value,
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
        self.require(Capability.ISSUE_UPDATE)
        raise CapabilityUnavailableError(
            f"{self.provider_id} does not implement issue updates.",
            provider=self.provider_id,
            capability=Capability.ISSUE_UPDATE.value,
        )

    # ------------------------------------------------------------------ helpers
    @property
    def transport(self) -> HttpTransport:
        return self._transport

    @property
    def credential(self) -> str | None:
        return self._credential

    def _timed_health(self, probe) -> HealthStatus:
        started = time.perf_counter()
        try:
            probe()
        except CapabilityUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - health must not raise
            return HealthStatus(
                status="down",
                latency_ms=int((time.perf_counter() - started) * 1000),
                detail=type(exc).__name__,
            )
        return HealthStatus(
            status="healthy",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

"""Universal provider registry and capability negotiation entry point."""

from __future__ import annotations

from typing import Any, Iterable

from .base import ProviderAdapter
from .capabilities import (
    CapabilityState,
    ProviderDescriptor,
    ProviderKind,
    build_capability_matrix,
)
from .errors import DuplicateEntityError, ProviderError
from .families import FAMILY_ADAPTERS

BUILTIN_ADAPTERS: tuple[type[ProviderAdapter], ...] = FAMILY_ADAPTERS


def _git_hosting_adapters() -> tuple[type[ProviderAdapter], ...]:
    from .adapters.aws_codecommit import CodeCommitAdapter
    from .adapters.azure_devops import AzureDevOpsAdapter
    from .adapters.bitbucket import BitbucketAdapter
    from .adapters.gitea import ForgejoAdapter, GiteaAdapter
    from .adapters.github import GitHubAdapter
    from .adapters.gitlab import GitLabAdapter
    from .adapters.sourcehut import SourceHutAdapter

    return (
        GitHubAdapter,
        GitLabAdapter,
        BitbucketAdapter,
        GiteaAdapter,
        ForgejoAdapter,
        AzureDevOpsAdapter,
        SourceHutAdapter,
        CodeCommitAdapter,
    )


def all_adapter_classes() -> tuple[type[ProviderAdapter], ...]:
    return _git_hosting_adapters() + BUILTIN_ADAPTERS


class UniversalProviderRegistry:
    """Registry of independent adapters. The core never branches on vendor ids."""

    def __init__(self, adapters: Iterable[type[ProviderAdapter]] | None = None) -> None:
        self._classes: dict[str, type[ProviderAdapter]] = {}
        for adapter in adapters if adapters is not None else all_adapter_classes():
            self.register(adapter)

    def register(self, adapter: type[ProviderAdapter], *, replace: bool = False) -> None:
        if not getattr(adapter, "provider_id", None):
            raise ValueError("An adapter must declare a provider_id.")
        existing = self._classes.get(adapter.provider_id)
        if existing is not None and existing is not adapter and not replace:
            raise DuplicateEntityError(
                f"Provider '{adapter.provider_id}' is already registered.",
                provider=adapter.provider_id,
                detail=f"{existing.__module__}.{existing.__name__}",
            )
        self._classes[adapter.provider_id] = adapter

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._classes))

    def classes(self) -> dict[str, type[ProviderAdapter]]:
        return dict(self._classes)

    def descriptor(self, provider_id: str) -> ProviderDescriptor:
        return self._lookup(provider_id).descriptor()

    def _lookup(self, provider_id: str) -> type[ProviderAdapter]:
        key = str(provider_id).strip().lower()
        aliases = {"bitbucket_cloud": "bitbucket", "azure": "azure_devops", "aws": "aws_codecommit"}
        key = aliases.get(key, key)
        adapter = self._classes.get(key)
        if adapter is None:
            raise ProviderError(
                f"Unknown provider '{provider_id}'.",
                provider=key,
                detail=f"known providers: {', '.join(self.provider_ids())}",
            )
        return adapter

    def get(self, provider_id: str, **kwargs: Any) -> ProviderAdapter:
        return self._lookup(provider_id)(**kwargs)

    def by_kind(self, kind: ProviderKind | str) -> tuple[ProviderDescriptor, ...]:
        target = ProviderKind(kind) if isinstance(kind, str) else kind
        return tuple(
            cls.descriptor() for cls in self._classes.values() if cls.kind is target
        )

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(cls.descriptor() for cls in sorted(self._classes.values(), key=lambda c: c.provider_id))

    def matrix(self, *, authenticated: bool = False) -> dict[str, Any]:
        return build_capability_matrix(self.descriptors(), authenticated=authenticated)

    def snapshot(self, *, environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Provider listing including live credential discovery (no network)."""

        rows: list[dict[str, Any]] = []
        for cls in sorted(self._classes.values(), key=lambda c: c.provider_id):
            descriptor = cls.descriptor()
            authent = False
            source = None
            try:
                instance = cls(environ=environ) if environ is not None else cls()
                authent = instance.credential is not None
                source = instance.credential_source
            except Exception:  # noqa: BLE001 - a bad endpoint must not hide the listing
                pass
            entry = descriptor.to_dict(authenticated=authent)
            entry["credential_present"] = authent
            entry["credential_source"] = source
            rows.append(entry)
        return rows

    def negotiate(
        self, provider_id: str, capability: str, **kwargs: Any
    ) -> CapabilityState:
        adapter = self.get(provider_id, **kwargs)
        from .capabilities import Capability

        try:
            target = Capability(capability)
        except ValueError as exc:
            raise ProviderError(
                f"Unknown capability '{capability}'.",
                provider=provider_id,
                detail=", ".join(c.value for c in Capability),
            ) from exc
        return adapter.capability_state(target)


_DEFAULT_REGISTRY: UniversalProviderRegistry | None = None


def get_provider_registry() -> UniversalProviderRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = UniversalProviderRegistry()
    return _DEFAULT_REGISTRY

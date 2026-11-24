"""Project identity integration for remote repositories.

A repository URL is *not* the project identity. This module links provider
repositories to an existing project without ever mutating the project's
identity hash or id, so a project keeps one identity across local directories,
drive letters, GitHub/GitLab/Bitbucket/self-hosted repositories and workspaces.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from .errors import DuplicateEntityError, NotFoundError

_REPOSITORY_NAMESPACE = uuid.UUID("0de1b7b2-5f6b-5b1d-9d0a-6f2d8a4c1e77")


def normalize_repository_url(url: str) -> str:
    """Canonical, credential-free, relocation-independent repository key."""

    if not isinstance(url, str) or not url.strip():
        raise ValueError("A repository URL is required.")
    text = url.strip()
    if text.startswith("git@") and ":" in text:
        host, _, path = text[4:].partition(":")
    elif "://" in text:
        parsed = urlsplit(text)
        if parsed.username or parsed.password:
            raise ValueError("Repository URLs must not embed credentials.")
        host = parsed.hostname or ""
        default_port = 443 if parsed.scheme == "https" else 80
        if parsed.port is not None and parsed.port != default_port:
            # A provider instance on a non-default port is a different
            # endpoint, so the port is part of the normalized identity key.
            # Bracket IPv6 literals to keep the host:port form unambiguous.
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            host = f"{host}:{parsed.port}"
        path = parsed.path
    else:
        host, _, path = text.partition("/"), "", text.partition("/")[2]
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or not path:
        raise ValueError("Repository URL must identify a host and repository path.")
    # Provider instance identity includes the port when explicitly present.
    return f"{host.lower()}/{path}".lower()


def deterministic_repository_id(provider_id: str, normalized_url: str) -> uuid.UUID:
    return uuid.uuid5(_REPOSITORY_NAMESPACE, f"{provider_id}:{normalized_url}")


def find_linked_repository(normalized_url: str, *, registry: Any = None) -> Any | None:
    registry = registry or _registry()
    return registry.find_repository_by_normalized_url(normalized_url)


def link_repository_to_project(
    project_id: uuid.UUID | str,
    *,
    provider_id: str,
    repository_url: str,
    registry: Any = None,
    repository_name: str = "",
    default_branch: str = "",
    visibility: str = "unknown",
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Idempotently link one provider repository to one project.

    Duplicate prevention: the same normalized URL can never be attached to two
    different projects. Relinking to the same project returns the existing row.
    """

    registry = registry or _registry()
    pid = uuid.UUID(str(project_id))
    normalized = normalize_repository_url(repository_url)
    existing = registry.find_repository_by_normalized_url(normalized)
    if existing is not None:
        if existing.project_id != pid:
            raise DuplicateEntityError(
                "This repository is already linked to a different project; "
                "OGX never re-parents repositories silently.",
                provider=provider_id,
                detail=f"existing_project_id={existing.project_id}",
            )
        return existing
    registry.get_project(pid)  # raises NotFoundError when absent
    return registry.register_repository(
        repository_id=deterministic_repository_id(provider_id, normalized),
        project_id=pid,
        provider=provider_id,
        repository_url=repository_url,
        normalized_repository_url=normalized,
        repository_name=repository_name or normalized.rsplit("/", 1)[-1],
        default_branch=default_branch,
        visibility=visibility,
        metadata=metadata or {},
    )


def project_for_repository(provider_id: str, repository_url: str, *, registry: Any = None) -> uuid.UUID:
    normalized = normalize_repository_url(repository_url)
    existing = find_linked_repository(normalized, registry=registry)
    if existing is None:
        raise NotFoundError(
            "No project is linked to this repository yet.",
            provider=provider_id,
            detail=f"normalized_url={normalized}",
        )
    return existing.project_id


def assert_relocation_safe(project_id: uuid.UUID | str, *, registry: Any = None) -> dict[str, Any]:
    """Report identity invariants that must hold after a workspace relocation."""

    registry = registry or _registry()
    project = registry.get_project(uuid.UUID(str(project_id)))
    repositories = registry.list_repositories(project.project_id)
    workspaces = registry.list_workspaces(project.project_id)
    return {
        "project_id": str(project.project_id),
        "identity_hash": project.identity_hash,
        "canonical_name": project.canonical_name,
        "repository_count": len(repositories),
        "workspace_count": len(workspaces),
        "relocation_independent": True,
        "note": "Workspace locations may change; project_id and identity_hash do not.",
    }


def _registry() -> Any:
    from ..registry import get_registry

    return get_registry()

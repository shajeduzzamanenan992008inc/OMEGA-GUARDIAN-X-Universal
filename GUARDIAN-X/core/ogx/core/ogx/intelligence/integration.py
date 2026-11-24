"""Read-only identity and registry integration.

Intelligence never creates or mutates a registry project.  It associates an
analysis with the strongest existing identity evidence: the immutable project
identity file, an existing registry workspace/project, an explicit project ID,
or the deterministic workspace fingerprint.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from ..pap import IdentityError, read_identity
from ..registry import RegistryError
from .models import ProjectIdentityInfo


def _location_candidates(root: Path) -> list[str]:
    resolved = root.resolve()
    candidates = [
        str(resolved),
        os.path.normcase(str(resolved)),
        str(resolved).replace("\\", "/"),
        os.path.normcase(str(resolved).replace("\\", "/")),
        root.expanduser().resolve().as_uri(),
    ]
    seen: list[str] = []
    for value in candidates:
        if value not in seen:
            seen.append(value)
    return seen


def _registry_project_for_location(registry, root: Path):
    for candidate in _location_candidates(root):
        try:
            workspace = registry.find_workspace_by_normalized_location(candidate)
        except (RegistryError, OSError, ValueError):
            continue
        if workspace is not None:
            try:
                return registry.get_project(workspace.project_id)
            except (RegistryError, OSError, ValueError):
                continue
    return None


def resolve_project_identity(root: Path, *,
                             explicit_project_id: str | None = None,
                             registry=None,
                             fallback_project_id: str = "") -> ProjectIdentityInfo:
    notes: list[str] = []
    identity = None
    try:
        identity = read_identity(root)
    except IdentityError as exc:
        notes.append(f"Project identity is unavailable: {type(exc).__name__}.")
    except (OSError, ValueError) as exc:
        notes.append(f"Project identity could not be read: {type(exc).__name__}.")

    registry_project = None
    if registry is not None:
        try:
            if identity is not None:
                try:
                    registry_project = registry.get_project(identity.project_id)
                except (RegistryError, OSError, ValueError):
                    registry_project = registry.find_project_by_identity(
                        identity_hash=identity.identity_hash)
            elif explicit_project_id:
                try:
                    registry_project = registry.get_project(explicit_project_id)
                except (RegistryError, OSError, ValueError):
                    registry_project = None
            else:
                registry_project = _registry_project_for_location(registry, root)
        except (RegistryError, OSError, ValueError) as exc:
            notes.append(f"Registry lookup failed: {type(exc).__name__}.")

    if identity is not None:
        source = "identity"
        project_id = identity.project_id
        if explicit_project_id and str(explicit_project_id) != str(identity.project_id):
            notes.append(
                "Explicit project ID differs from the immutable identity file; identity was preserved.")
    elif registry_project is not None:
        source = "registry"
        project_id = str(registry_project.project_id)
    elif explicit_project_id:
        source = "explicit"
        project_id = str(explicit_project_id)
    else:
        source = "fingerprint"
        project_id = fallback_project_id or "unknown"

    canonical_name = (getattr(identity, "project_name", "") or
                      getattr(registry_project, "canonical_name", "") or root.name)
    identity_hash = (getattr(identity, "identity_hash", "") or
                     getattr(registry_project, "identity_hash", "") or "")
    return ProjectIdentityInfo(
        source=source,
        project_id=project_id,
        canonical_name=canonical_name,
        identity_hash=identity_hash,
        registry_project_id=str(registry_project.project_id) if registry_project is not None else None,
        notes=notes,
    )


def resolve_registered_project(reference: str, registry=None) -> tuple[Path, str, str]:
    """Resolve a registry project ID or canonical name to a local workspace.

    Returns ``(root, project_id, canonical_name)``.  No network or clone is
    attempted; a project without an existing local workspace is an error.
    """
    if registry is None:
        from ..registry import get_registry
        registry = get_registry()
    project = None
    try:
        project_id = UUID(str(reference))
        project = registry.get_project(project_id)
    except (ValueError, RegistryError, OSError):
        try:
            project = registry.find_project_by_identity(canonical_name=str(reference))
        except (RegistryError, OSError, ValueError):
            project = None
    if project is None:
        raise ValueError(f"Unknown registry project: {reference}")
    try:
        workspaces = registry.list_workspaces(project.project_id)
    except (RegistryError, OSError, ValueError) as exc:
        raise ValueError(f"Could not list workspaces for project {reference}: {exc}") from exc
    for workspace in workspaces:
        location = (getattr(workspace, "location_reference", "") or "").strip()
        if not location:
            continue
        candidate = Path(location).expanduser()
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir():
            return resolved, str(project.project_id), project.canonical_name
    raise ValueError(
        f"Project {reference} has no accessible local workspace; "
        "intelligence analysis does not clone or access remote repositories.")

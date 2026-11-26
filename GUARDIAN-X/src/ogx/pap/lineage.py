"""Identity evidence explanation, explicit project fork and lineage records.

Two operations live here:

* :func:`explain_identity` — explains *why* GUARDIAN-X believes a directory is
  (or is not) a known project. It never mutates anything.
* :func:`fork_project` — creates a new project with a new ``project_id`` from a
  copy of an existing one, and records explicit parent lineage. A fork is the
  only supported way to derive one project identity from another; copying a
  directory never silently reuses an identity.

Lineage is stored in ``.guardian-x/lineage.json`` rather than inside
``identity.json`` because the identity schema is strict and versioned; adding
fields there would break schema ``1.0`` compatibility.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .identity import create_identity, read_identity
from .exceptions import IdentityCorruptedError, IdentityError, IdentityMissingError

LINEAGE_FILENAME = "lineage.json"
LINEAGE_SCHEMA_VERSION = "1.0"
MANIFEST_RELATIVE = Path("OMEGA GUARDIAN X") / "integration-manifest.json"

CLASS_VERIFIED = "verified"
CLASS_PROVISIONAL = "provisional"
CLASS_CONFLICT = "conflict"
CLASS_MISSING = "missing"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_manifest_project_id(root: Path) -> str | None:
    path = root / MANIFEST_RELATIVE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    value = data.get("project_id")
    return str(value) if value else None


def read_lineage(root: Path) -> dict[str, Any] | None:
    """Read ``.guardian-x/lineage.json`` if present."""

    path = Path(root) / ".guardian-x" / LINEAGE_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def explain_identity(root: Path | str, *, registry: Any = None) -> dict[str, Any]:
    """Explain the identity evidence available for a directory.

    Classifications:

    * ``verified`` — a valid identity file with a matching hash.
    * ``provisional`` — no identity file, but other evidence (registry or
      manifest) identifies the project.
    * ``conflict`` — evidence disagrees; nothing is merged.
    * ``missing`` — no usable evidence at all.
    """

    root_path = Path(root).expanduser().resolve()
    report: dict[str, Any] = {
        "root": str(root_path),
        "classification": CLASS_MISSING,
        "identity": None,
        "lineage": None,
        "evidence": [],
        "conflicts": [],
        "registry": None,
        "manifest_project_id": None,
        "recommendation": "",
    }

    identity = None
    try:
        identity = read_identity(root_path)
        report["identity"] = {
            "project_id": identity.project_id,
            "project_name": identity.project_name,
            "identity_hash": identity.identity_hash,
            "hash_valid": identity.verify_hash(),
            "created_at": identity.created_at,
        }
        report["evidence"].append(
            {
                "kind": "identity_file",
                "status": "valid",
                "detail": str(root_path / ".guardian-x" / "identity.json"),
            }
        )
    except IdentityMissingError:
        report["evidence"].append(
            {"kind": "identity_file", "status": "missing", "detail": "no identity.json"}
        )
    except IdentityCorruptedError as exc:
        report["evidence"].append(
            {"kind": "identity_file", "status": "corrupted", "detail": str(exc)}
        )
    except IdentityError as exc:
        report["evidence"].append(
            {"kind": "identity_file", "status": "invalid", "detail": str(exc)}
        )

    lineage = read_lineage(root_path)
    if lineage:
        report["lineage"] = lineage
        report["evidence"].append(
            {
                "kind": "lineage",
                "status": "present",
                "detail": f"parent={lineage.get('parent_project_id')}",
            }
        )

    manifest_project_id = _read_manifest_project_id(root_path)
    report["manifest_project_id"] = manifest_project_id
    if manifest_project_id:
        report["evidence"].append(
            {"kind": "integration_manifest", "status": "project_id_present", "detail": manifest_project_id}
        )

    if registry is None:
        try:
            from ..registry import get_registry

            registry = get_registry()
        except Exception:  # pragma: no cover - registry unavailable
            registry = None

    registry_project = None
    if registry is not None:
        try:
            if identity is not None:
                registry_project = registry.find_project_by_identity(
                    project_id=identity.project_id
                )
                if registry_project is None:
                    registry_project = registry.find_project_by_identity(
                        identity_hash=identity.identity_hash
                    )
            elif manifest_project_id:
                registry_project = registry.find_project_by_identity(
                    project_id=manifest_project_id
                )
            if registry_project is None:
                workspace = registry.find_workspace_by_normalized_location(str(root_path))
                if workspace is not None:
                    registry_project = registry.get_project(workspace.project_id)
        except Exception:  # pragma: no cover - defensive
            registry_project = None

    if registry_project is not None:
        report["registry"] = {
            "project_id": str(registry_project.project_id),
            "canonical_name": registry_project.canonical_name,
            "identity_hash": registry_project.identity_hash,
        }
        report["evidence"].append(
            {
                "kind": "registry",
                "status": "found",
                "detail": str(registry_project.project_id),
            }
        )

    # Conflict detection: explicit evidence disagreeing is never auto-resolved.
    if identity is not None and registry_project is not None:
        if str(registry_project.project_id) != identity.project_id:
            report["conflicts"].append(
                "registry project_id differs from identity file project_id"
            )
    if identity is not None and manifest_project_id and manifest_project_id != identity.project_id:
        report["conflicts"].append(
            "integration manifest project_id differs from identity file project_id"
        )
    if identity is None and registry_project is not None and manifest_project_id:
        if str(registry_project.project_id) != manifest_project_id:
            report["conflicts"].append(
                "registry project_id differs from integration manifest project_id"
            )
    if identity is not None and lineage and lineage.get("parent_project_id") == identity.project_id:
        report["conflicts"].append("lineage parent_project_id equals this project's project_id")

    if report["conflicts"]:
        report["classification"] = CLASS_CONFLICT
        report["recommendation"] = (
            "Conflicting identity evidence. Do not merge. Resolve by restoring the "
            "correct identity.json or by forking the project explicitly."
        )
    elif identity is not None:
        report["classification"] = CLASS_VERIFIED
        report["recommendation"] = "Identity is valid; no action required."
    elif registry_project is not None or manifest_project_id:
        report["classification"] = CLASS_PROVISIONAL
        report["recommendation"] = (
            "Provisional project. Create an identity file with an explicit project_id "
            "to adopt this project; GUARDIAN-X will not adopt it silently."
        )
    else:
        report["classification"] = CLASS_MISSING
        report["recommendation"] = (
            "No usable identity evidence. Run `ogx identity create` to start one, or "
            "`ogx identity recover` with registry/manifest evidence."
        )

    return report


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".lineage-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        tmp_path = Path(handle.name)
    try:
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def fork_project(
    source_root: Path | str,
    destination_root: Path | str,
    *,
    new_name: str | None = None,
    confirm: bool = False,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new project (new ``project_id``) from a copy of ``source_root``.

    The source is never modified and never deleted. The fork records its parent
    in ``.guardian-x/lineage.json`` so lineage is explicit evidence, not an
    implicit shared identity.
    """

    if not confirm:
        raise IdentityError(
            "Forking creates a new project identity; pass confirm=True to proceed.",
            details={"operation": "fork"},
        )

    source = Path(source_root).expanduser().resolve()
    destination = Path(destination_root).expanduser().resolve()

    parent = read_identity(source)
    if destination.exists():
        raise IdentityError(
            f"Fork destination already exists: {destination}",
            details={"destination": str(destination)},
        )
    if source == destination:
        raise IdentityError("Fork destination must differ from the source.")
    try:
        destination.relative_to(source)
        raise IdentityError("Fork destination must not be inside the source tree.")
    except ValueError:
        pass

    from ..relocation import copy_project_tree

    report = copy_project_tree(source, destination)

    forked = create_identity(
        destination,
        # Parentheses are not allowed by the identity project_name schema.
        project_name=new_name or f"{parent.project_name} fork",
        description=(
            description
            if description is not None
            else f"Fork of {parent.project_name} ({parent.project_id})"
        ),
        confirm=True,
    )

    lineage = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "relationship": "fork",
        "parent_project_id": parent.project_id,
        "parent_identity_hash": parent.identity_hash,
        "parent_project_name": parent.project_name,
        "forked_at": _now(),
        "forked_by": forked.created_by,
        "child_project_id": forked.project_id,
    }
    _write_json_atomic(destination / ".guardian-x" / LINEAGE_FILENAME, lineage)

    return {
        "source": str(source),
        "destination": str(destination),
        "parent_project_id": parent.project_id,
        "project_id": forked.project_id,
        "project_name": forked.project_name,
        "identity_hash": forked.identity_hash,
        "files_copied": report["files"],
        "warnings": report["warnings"],
        "lineage": lineage,
    }

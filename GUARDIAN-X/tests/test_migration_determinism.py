"""Tests for deterministic V1->V2 migration ids and the relocation verifier."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ogx.pap import create_identity, read_identity
from ogx.platform.identity import assert_relocation_safe
from ogx.registry import RegistryV2


def _write_v1(path: Path, projects: dict[str, str]) -> Path:
    path.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    return path


def _project_with_identity(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    create_identity(root, project_name=name)
    return root


def test_migration_ids_are_deterministic_across_databases(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    v1 = _write_v1(tmp_path / "projects.json", {"Alpha": str(project)})

    first = RegistryV2(tmp_path / "one.db")
    second = RegistryV2(tmp_path / "two.db")
    try:
        first_result = first.migrate_from_v1(v1)
        second_result = second.migrate_from_v1(v1)

        assert first_result["migrated"] == 1
        assert second_result["migrated"] == 1
        assert first_result["window"]["window_id"] == second_result["window"]["window_id"]

        first_project = first.list_projects()[0]
        second_project = second.list_projects()[0]
        assert first_project.project_id == second_project.project_id

        first_workspace = first.list_workspaces()[0]
        second_workspace = second.list_workspaces()[0]
        assert first_workspace.workspace_id == second_workspace.workspace_id
    finally:
        first.close()
        second.close()


def test_migration_is_idempotent_on_rerun(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    v1 = _write_v1(tmp_path / "projects.json", {"Alpha": str(project)})

    registry = RegistryV2(tmp_path / "registry.db")
    try:
        registry.migrate_from_v1(v1)
        registry.migrate_from_v1(v1)

        assert len(registry.list_projects()) == 1
        assert len(registry.list_workspaces()) == 1
    finally:
        registry.close()


def test_migration_without_identity_is_marked_provisional(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    v1 = _write_v1(tmp_path / "projects.json", {"Plain": str(plain)})

    registry = RegistryV2(tmp_path / "registry.db")
    try:
        result = registry.migrate_from_v1(v1)
        assert result["migrated"] == 1
        assert result["provisional_projects"] == ["Plain"]
        assert "id-fallback" in result["patch_kinds"]
        assert "workspace" in result["patch_kinds"]

        project = registry.list_projects()[0]
        assert project.identity_hash == ""
    finally:
        registry.close()


def test_migration_with_identity_records_identity_hash(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    identity = read_identity(project)
    v1 = _write_v1(tmp_path / "projects.json", {"Alpha": str(project)})

    registry = RegistryV2(tmp_path / "registry.db")
    try:
        result = registry.migrate_from_v1(v1)
        assert result["provisional_projects"] == []
        assert "id-fallback" not in result["patch_kinds"]
        assert registry.list_projects()[0].identity_hash == identity.identity_hash
    finally:
        registry.close()


def test_dry_run_writes_nothing(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    v1 = _write_v1(tmp_path / "projects.json", {"Alpha": str(project)})
    backup = v1.with_suffix(".migration-backup")

    registry = RegistryV2(tmp_path / "registry.db")
    try:
        preview = registry.migrate_from_v1(v1, dry_run=True)
        assert preview["dry_run"] is True
        assert preview["preview"]["projects_to_migrate"] == 1
        assert registry.list_projects() == []
        assert not backup.exists()

        again = registry.migrate_from_v1(v1, dry_run=True)
        assert again["preview"]["window"]["window_id"] == preview["preview"]["window"]["window_id"]
    finally:
        registry.close()


def test_window_id_changes_when_registry_content_changes(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    v1 = _write_v1(tmp_path / "projects.json", {"Alpha": str(project)})
    registry = RegistryV2(tmp_path / "registry.db")
    try:
        first = registry.migrate_from_v1(v1, dry_run=True)
        _write_v1(v1, {"Alpha": str(project), "Beta": str(project)})
        second = registry.migrate_from_v1(v1, dry_run=True)
        assert (
            first["preview"]["window"]["window_id"]
            != second["preview"]["window"]["window_id"]
        )
    finally:
        registry.close()


def test_migration_creates_backup_next_to_v1_registry(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    v1 = _write_v1(tmp_path / "projects.json", {"Alpha": str(project)})
    registry = RegistryV2(tmp_path / "registry.db")
    try:
        result = registry.migrate_from_v1(v1)
        assert result["backup_path"].endswith(".migration-backup")
        assert Path(result["backup_path"]).exists()
    finally:
        registry.close()


# --- Relocation verifier (G8) -------------------------------------------------


def test_relocation_verifier_reports_workspace_resolution(tmp_path):
    project = _project_with_identity(tmp_path / "alpha", "Alpha")
    identity = read_identity(project)

    registry = RegistryV2(tmp_path / "registry.db")
    try:
        project_id = uuid4()
        workspace_id = uuid4()
        registry.register_project(
            project_id=project_id,
            canonical_name="Alpha",
            identity_hash=identity.identity_hash,
        )
        registry.register_workspace(
            workspace_id=workspace_id,
            project_id=project_id,
            location_reference=str(project),
            normalized_location=str(project.resolve()),
        )

        report = assert_relocation_safe(project_id, registry=registry)
        assert report["relocation_independent"] is True
        assert report["workspace_paths_resolve"] is True
        assert report["workspaces"][0]["workspace_id"] == str(workspace_id)

        moved = tmp_path / "moved"
        project.rename(moved)
        stale = assert_relocation_safe(project_id, registry=registry)
        assert stale["workspace_paths_resolve"] is False

        registry.update_workspace_location(
            workspace_id, str(moved), str(moved.resolve())
        )
        refreshed = assert_relocation_safe(project_id, registry=registry)
        assert refreshed["workspace_paths_resolve"] is True
        assert refreshed["project_id"] == report["project_id"]
    finally:
        registry.close()

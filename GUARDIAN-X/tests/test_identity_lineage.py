"""Tests for identity evidence explanation, fork and lineage."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from ogx.pap import IdentityError, create_identity, read_identity
from ogx.pap.lineage import (
    CLASS_CONFLICT,
    CLASS_MISSING,
    CLASS_PROVISIONAL,
    CLASS_VERIFIED,
    LINEAGE_FILENAME,
    explain_identity,
    fork_project,
    read_lineage,
)
from ogx.registry import RegistryV2


def test_explain_verified_identity(tmp_path):
    create_identity(tmp_path, project_name="Explained")
    report = explain_identity(tmp_path)
    assert report["classification"] == CLASS_VERIFIED
    assert report["identity"]["hash_valid"] is True
    assert any(item["kind"] == "identity_file" and item["status"] == "valid" for item in report["evidence"])


def test_explain_missing_identity(tmp_path):
    report = explain_identity(tmp_path)
    assert report["classification"] == CLASS_MISSING
    assert report["identity"] is None
    assert "identity create" in report["recommendation"]


def test_explain_provisional_from_registry_workspace(tmp_path):
    registry = RegistryV2(tmp_path / "registry.db")
    project_id = uuid4()
    registry.register_project(project_id=project_id, canonical_name="Provisional")
    registry.register_workspace(
        workspace_id=uuid4(),
        project_id=project_id,
        location_reference=str(tmp_path),
        normalized_location=str(tmp_path.resolve()),
    )

    report = explain_identity(tmp_path, registry=registry)
    assert report["classification"] == CLASS_PROVISIONAL
    assert report["registry"]["project_id"] == str(project_id)
    registry.close()


def test_explain_conflict_between_identity_and_manifest(tmp_path):
    create_identity(tmp_path, project_name="Conflicted")
    manifest_dir = tmp_path / "OMEGA GUARDIAN X"
    manifest_dir.mkdir()
    (manifest_dir / "integration-manifest.json").write_text(
        json.dumps({"project_id": str(uuid4())}), encoding="utf-8"
    )

    report = explain_identity(tmp_path)
    assert report["classification"] == CLASS_CONFLICT
    assert report["conflicts"]
    assert "Do not merge" in report["recommendation"]


def test_explain_manifest_project_id_is_evidence(tmp_path):
    project_id = str(uuid4())
    manifest_dir = tmp_path / "OMEGA GUARDIAN X"
    manifest_dir.mkdir()
    (manifest_dir / "integration-manifest.json").write_text(
        json.dumps({"project_id": project_id}), encoding="utf-8"
    )
    report = explain_identity(tmp_path)
    assert report["manifest_project_id"] == project_id
    assert report["classification"] == CLASS_PROVISIONAL


def test_fork_creates_new_identity_and_lineage(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    parent = create_identity(source, project_name="Parent Project")
    (source / "file.txt").write_text("payload\n", encoding="utf-8")

    destination = tmp_path / "fork"
    result = fork_project(source, destination, new_name="Child Project", confirm=True)

    child = read_identity(destination)
    assert child.project_id != parent.project_id
    assert child.project_name == "Child Project"
    assert result["parent_project_id"] == parent.project_id
    assert result["project_id"] == child.project_id
    assert (destination / "file.txt").read_text(encoding="utf-8") == "payload\n"

    lineage = read_lineage(destination)
    assert lineage["relationship"] == "fork"
    assert lineage["parent_project_id"] == parent.project_id
    assert lineage["child_project_id"] == child.project_id
    assert (destination / ".guardian-x" / LINEAGE_FILENAME).exists()


def test_fork_does_not_modify_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    parent = create_identity(source, project_name="Stable Parent")
    (source / "file.txt").write_text("payload\n", encoding="utf-8")

    fork_project(source, tmp_path / "fork", confirm=True)

    assert read_identity(source).project_id == parent.project_id
    assert read_identity(source).identity_hash == parent.identity_hash
    assert read_lineage(source) is None


def test_fork_requires_confirmation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_identity(source, project_name="Parent")
    with pytest.raises(IdentityError):
        fork_project(source, tmp_path / "fork")
    assert not (tmp_path / "fork").exists()


def test_fork_refuses_existing_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_identity(source, project_name="Parent")
    destination = tmp_path / "fork"
    destination.mkdir()
    with pytest.raises(IdentityError):
        fork_project(source, destination, confirm=True)


def test_fork_refuses_destination_inside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_identity(source, project_name="Parent")
    with pytest.raises(IdentityError):
        fork_project(source, source / "inner", confirm=True)


def test_forked_project_explains_as_verified_with_lineage(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_identity(source, project_name="Parent")
    fork_project(source, tmp_path / "fork", confirm=True)

    report = explain_identity(tmp_path / "fork")
    assert report["classification"] == CLASS_VERIFIED
    assert report["lineage"]["relationship"] == "fork"

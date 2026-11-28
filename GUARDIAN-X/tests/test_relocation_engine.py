"""Tests for the relocation engine: plan, apply, journal, recovery, verify."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from ogx.pap import create_identity, read_identity
from ogx.registry import RegistryV2
from ogx.relocation import (
    RelocationError,
    RelocationRefused,
    SimulatedInterruption,
    apply_relocation,
    copy_project_tree,
    plan_relocation,
    recover_relocation,
    scan_tree,
    verify_relocation,
)


def make_project(root: Path, name: str = "proj") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    create_identity(root, project_name=name)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "README.md").write_text("# project\n", encoding="utf-8")
    return root


def test_scan_tree_reports_hashes(tmp_path):
    project = make_project(tmp_path / "p")
    entries, warnings = scan_tree(project)
    assert warnings == []
    paths = [entry["path"] for entry in entries]
    assert "src/app.py" in paths
    assert all(len(entry["sha256"]) == 64 for entry in entries)


def test_plan_refuses_existing_destination(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "d"
    destination.mkdir()
    with pytest.raises(RelocationRefused) as ctx:
        plan_relocation(project, destination)
    assert ctx.value.reason == "destination_exists"


def test_plan_refuses_destination_inside_source(tmp_path):
    project = make_project(tmp_path / "p")
    with pytest.raises(RelocationRefused) as ctx:
        plan_relocation(project, project / "nested")
    assert ctx.value.reason == "destination_inside_source"


def test_plan_refuses_without_identity(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RelocationRefused) as ctx:
        plan_relocation(plain, tmp_path / "dest")
    assert ctx.value.reason == "identity_invalid"


def test_plan_carries_identity_and_counts(tmp_path):
    project = make_project(tmp_path / "p")
    identity = read_identity(project)
    plan = plan_relocation(project, tmp_path / "dest")
    assert plan.project_id == identity.project_id
    assert plan.identity_hash == identity.identity_hash
    assert plan.file_count >= 3
    assert plan.operation == "move"


def test_apply_copy_retains_source_and_verifies(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="copy")
    result = apply_relocation(plan, journal_dir=tmp_path / "journal")

    assert result["state"] == "complete"
    assert result["source_deleted"] is False
    assert result["delete_reason"] == "source_retained_for_copy_or_clone"
    assert project.exists()
    assert (destination / "src" / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
    report = verify_relocation(project, destination)
    assert report["identity_preserved"] is True
    assert report["content_matches"] is True


def test_partial_copy_resumes_after_mid_copy_interruption(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    journal_dir = tmp_path / "journal"
    plan = plan_relocation(project, destination, operation="copy")

    with pytest.raises(SimulatedInterruption):
        apply_relocation(
            plan, journal_dir=journal_dir,
            fault_inject=_interrupt_at("copy", "started"),
        )
    assert not destination.exists()

    # Model a process that wrote two files and died: one complete, one truncated.
    (destination / "src").mkdir(parents=True)
    (destination / "src" / "app.py").write_bytes(b"pri")
    (destination / "README.md").write_bytes((project / "README.md").read_bytes())

    result = recover_relocation(journal_dir / f"{plan.plan_id}.json")
    assert result["state"] == "complete"
    assert result["resumed"] is True
    assert result["copy_report"]["reused"] == 1
    report = verify_relocation(project, destination)
    assert report["identity_preserved"] is True
    assert report["content_matches"] is True


def test_resume_rejects_unexpected_destination_files(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    journal_dir = tmp_path / "journal"
    plan = plan_relocation(project, destination, operation="copy")

    with pytest.raises(SimulatedInterruption):
        apply_relocation(
            plan, journal_dir=journal_dir,
            fault_inject=_interrupt_at("copy", "done"),
        )
    (destination / "injected.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(RelocationError) as ctx:
        recover_relocation(journal_dir / f"{plan.plan_id}.json")
    assert ctx.value.reason == "verify_failed"


def test_copy_still_refuses_a_non_empty_destination_by_default(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(RelocationRefused) as ctx:
        copy_project_tree(project, destination)
    assert ctx.value.reason == "destination_exists"


def test_apply_move_without_confirmation_retains_source(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="move")
    result = apply_relocation(plan, journal_dir=tmp_path / "journal")
    assert project.exists()
    assert result["source_deleted"] is False
    assert result["delete_reason"] == "explicit_confirmation_required"


def test_apply_move_with_confirmation_deletes_source(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="move")
    result = apply_relocation(
        plan, journal_dir=tmp_path / "journal", confirm_delete_source=True
    )
    assert result["source_deleted"] is True
    assert not project.exists()
    assert (destination / ".guardian-x" / "identity.json").exists()


def test_copy_cannot_delete_source(tmp_path):
    project = make_project(tmp_path / "p")
    plan = plan_relocation(project, tmp_path / "dest", operation="copy")
    with pytest.raises(RelocationRefused) as ctx:
        apply_relocation(plan, journal_dir=tmp_path / "j", confirm_delete_source=True)
    assert ctx.value.reason == "invalid_delete_request"


def test_journal_is_written_with_every_step(tmp_path):
    project = make_project(tmp_path / "p")
    journal_dir = tmp_path / "journal"
    plan = plan_relocation(project, tmp_path / "dest", operation="copy")
    apply_relocation(plan, journal_dir=journal_dir)

    journal_path = journal_dir / f"{plan.plan_id}.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["schema_version"] == "1.0"
    for step in ("preflight", "copy", "verify", "rebind_workspace", "delete_source", "finalize"):
        assert journal["steps"][step]["status"] == "done"
    assert journal["plan"]["project_id"] == plan.project_id


def _interrupt_at(step: str, status: str):
    def injector(current_step: str, current_status: str) -> None:
        if current_step == step and current_status == status:
            raise SimulatedInterruption(f"interrupted at {current_step}:{current_status}")

    return injector


@pytest.mark.parametrize(
    "step,status",
    [
        ("preflight", "started"),
        ("preflight", "done"),
        ("copy", "started"),
        ("copy", "done"),
        ("verify", "started"),
        ("verify", "done"),
    ],
)
def test_interruption_at_every_state_is_recoverable(tmp_path, step, status):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    journal_dir = tmp_path / "journal"
    plan = plan_relocation(project, destination, operation="copy")

    with pytest.raises(SimulatedInterruption):
        apply_relocation(
            plan,
            journal_dir=journal_dir,
            fault_inject=_interrupt_at(step, status),
        )

    journal_path = journal_dir / f"{plan.plan_id}.json"
    assert journal_path.exists(), "the journal must survive an interruption"

    result = recover_relocation(journal_path, registry=None)
    assert result["state"] == "complete"
    assert result["resumed"] is True
    report = verify_relocation(project, destination)
    assert report["identity_preserved"] is True
    assert report["content_matches"] is True


def test_recovery_is_idempotent_when_already_complete(tmp_path):
    project = make_project(tmp_path / "p")
    journal_dir = tmp_path / "journal"
    plan = plan_relocation(project, tmp_path / "dest", operation="copy")
    apply_relocation(plan, journal_dir=journal_dir)
    journal_path = journal_dir / f"{plan.plan_id}.json"

    first = recover_relocation(journal_path, registry=None)
    second = recover_relocation(journal_path, registry=None)
    assert first["state"] == "complete"
    assert second["state"] == "complete"


def test_real_failure_rolls_back_destination(tmp_path, monkeypatch):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    journal_dir = tmp_path / "journal"
    plan = plan_relocation(project, destination, operation="copy")

    def boom(source, target, *, entries=None, **kwargs):
        # Simulate a failure after the destination was partially created.
        target.mkdir(parents=True, exist_ok=True)
        (target / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr("ogx.relocation.copy_project_tree", boom)
    with pytest.raises(OSError):
        apply_relocation(plan, journal_dir=journal_dir)

    assert not destination.exists(), "an ordinary failure must roll the destination back"
    assert project.exists()
    journal = json.loads((journal_dir / f"{plan.plan_id}.json").read_text(encoding="utf-8"))
    assert journal["rollback"]["status"] == "done"
    assert journal["error"]["message"] == "disk full"


def test_relocation_rebinds_registered_workspace(tmp_path):
    project = make_project(tmp_path / "p")
    identity = read_identity(project)
    destination = tmp_path / "dest"

    registry = RegistryV2(tmp_path / "registry.db")
    project_id = uuid4()
    workspace_id = uuid4()
    registry.register_project(
        project_id=project_id,
        canonical_name="Rebind",
        identity_hash=identity.identity_hash,
    )
    registry.register_workspace(
        workspace_id=workspace_id,
        project_id=project_id,
        location_reference=str(project),
        normalized_location=str(project.resolve()),
    )

    plan = plan_relocation(project, destination, operation="copy")
    result = apply_relocation(plan, journal_dir=tmp_path / "journal", registry=registry)

    assert result["rebind"]["status"] == "done"
    workspace = registry.get_workspace(workspace_id)
    assert workspace.normalized_location == str(destination.resolve())
    registry.close()


def test_verify_detects_content_difference(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="copy")
    apply_relocation(plan, journal_dir=tmp_path / "journal")

    (destination / "README.md").write_text("changed\n", encoding="utf-8")
    report = verify_relocation(project, destination)
    assert report["identity_preserved"] is True
    assert report["content_matches"] is False
    assert any("content differs" in item for item in report["differences"])


def test_copy_skips_symlinks(tmp_path):
    project = make_project(tmp_path / "p")
    link = project / "link.txt"
    try:
        link.symlink_to(project / "README.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")

    destination = tmp_path / "dest"
    report = copy_project_tree(project, destination)
    assert not (destination / "link.txt").exists()
    assert any("symlink" in warning for warning in report["warnings"])


def test_recover_missing_journal_raises(tmp_path):
    with pytest.raises(RelocationError) as ctx:
        recover_relocation(tmp_path / "missing.json")
    assert ctx.value.reason == "journal_missing"

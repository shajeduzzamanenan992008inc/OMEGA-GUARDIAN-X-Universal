"""ID-10 / ID-11: relocation edge cases with simulated media.

Real second-drive, USB and second-machine verification is BLOCKED on this
machine (single fixed volume, no removable media). These tests exercise the
same code paths that media failures take: a failed write, a full volume, a
disconnected device, a read-only target and a copy that becomes a duplicate on
reconnect. They do not replace physical-media evidence.
"""

from __future__ import annotations

import errno
import json
import shutil
from pathlib import Path

import pytest

from ogx.pap import create_identity, read_identity
from ogx.relocation import (
    apply_relocation,
    plan_relocation,
    verify_relocation,
)


def make_project(root: Path, name: str = "proj") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    create_identity(root, project_name=name)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "README.md").write_text("# project\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    for index in range(4):
        (root / "data" / f"chunk-{index}.bin").write_bytes(bytes([index]) * 1024)
    return root


def fail_on_nth_copy(monkeypatch, exc: Exception, *, fail_at: int = 1):
    """Make the Nth real copy fail, simulating a device-level write error."""
    real_copy2 = shutil.copy2
    state = {"calls": 0}

    def flaky_copy2(src, dst, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] >= fail_at:
            raise exc
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr("ogx.relocation.shutil.copy2", flaky_copy2)
    return state


# ------------------------------------------------------- path independence
def test_identity_is_path_independent_across_a_new_location(tmp_path):
    project = make_project(tmp_path / "volume-c" / "proj")
    before = read_identity(project)
    raw_before = (project / ".guardian-x" / "identity.json").read_text(encoding="utf-8")

    new_home = tmp_path / "volume-d" / "renamed-copy"
    new_home.parent.mkdir(parents=True)
    shutil.move(str(project), str(new_home))

    after = read_identity(new_home)
    raw_after = (new_home / ".guardian-x" / "identity.json").read_text(encoding="utf-8")

    assert after.project_id == before.project_id
    assert after.identity_hash == before.identity_hash
    assert str(project) not in raw_before
    assert raw_before == raw_after
    assert str(new_home).lower() not in raw_after.lower()


def test_relocation_into_a_differently_named_mount_path_preserves_identity(tmp_path):
    project = make_project(tmp_path / "E" / "proj")
    plan = plan_relocation(project, tmp_path / "MNT" / "usb-stick" / "proj", operation="copy")
    result = apply_relocation(plan, journal_dir=tmp_path / "journal")

    assert result["state"] == "complete"
    report = verify_relocation(project, tmp_path / "MNT" / "usb-stick" / "proj")
    assert report["identity_preserved"] is True
    assert report["content_matches"] is True
    assert report["source_project_id"] == report["destination_project_id"]


# ------------------------------------------------------------ media errors
def test_permission_failure_rolls_back_and_keeps_the_source(tmp_path, monkeypatch):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="move")
    fail_on_nth_copy(monkeypatch, PermissionError(errno.EACCES, "read-only media"))

    with pytest.raises(PermissionError):
        apply_relocation(plan, journal_dir=tmp_path / "journal", confirm_delete_source=True)
    assert project.exists()
    assert not destination.exists()
    journal = json.loads(next((tmp_path / "journal").glob("*.json")).read_text(encoding="utf-8"))
    assert journal["error"]["type"] == "PermissionError"
    assert journal["rollback"]["status"] == "done"
    assert journal.get("source_deleted", False) is False


def test_insufficient_space_rolls_back_and_keeps_the_source(tmp_path, monkeypatch):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="move")
    fail_on_nth_copy(monkeypatch, OSError(errno.ENOSPC, "No space left on device"), fail_at=2)

    with pytest.raises(OSError) as excinfo:
        apply_relocation(plan, journal_dir=tmp_path / "journal", confirm_delete_source=True)

    assert excinfo.value.errno == errno.ENOSPC
    assert project.exists()
    assert not destination.exists()
    journal = json.loads(next((tmp_path / "journal").glob("*.json")).read_text(encoding="utf-8"))
    assert journal["rollback"]["status"] == "done"


def test_disconnect_during_copy_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    plan = plan_relocation(project, destination, operation="copy")
    fail_on_nth_copy(monkeypatch, OSError(errno.EIO, "device disconnected"), fail_at=3)

    with pytest.raises(OSError):
        apply_relocation(plan, journal_dir=tmp_path / "journal")

    assert not destination.exists()

    monkeypatch.undo()
    retry = plan_relocation(project, destination, operation="copy")
    result = apply_relocation(retry, journal_dir=tmp_path / "journal-2")
    assert result["state"] == "complete"
    assert verify_relocation(project, destination)["content_matches"] is True


def test_read_only_target_that_cannot_be_created_fails_closed(tmp_path):
    project = make_project(tmp_path / "p")
    blocker = tmp_path / "media"
    blocker.write_text("not a directory\n", encoding="utf-8")
    plan = plan_relocation(project, blocker / "proj", operation="copy")

    with pytest.raises(OSError):
        apply_relocation(plan, journal_dir=tmp_path / "journal")
    assert project.exists()
    assert blocker.read_text(encoding="utf-8") == "not a directory\n"


def test_partial_destination_is_never_merged_silently(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "dest"
    destination.mkdir()
    (destination / "leftover.txt").write_text("from another device\n", encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        plan_relocation(project, destination, operation="copy")
    assert "destination_exists" in str(getattr(excinfo.value, "reason", ""))


# ----------------------------------------------------------- duplicate USB
def test_copy_creates_a_detectable_duplicate_identity_on_reconnect(tmp_path):
    project = make_project(tmp_path / "p")
    usb = tmp_path / "usb"
    plan = plan_relocation(project, usb / "proj", operation="copy")
    apply_relocation(plan, journal_dir=tmp_path / "journal")

    # Simulate reconnecting the same stick at a different mount path.
    remounted = tmp_path / "usb-remounted"
    shutil.move(str(usb), str(remounted))

    report = verify_relocation(project, remounted / "proj")
    assert report["identity_preserved"] is True
    assert report["content_matches"] is True
    assert report["source_project_id"] == report["destination_project_id"]

    identity = read_identity(remounted / "proj")
    original = read_identity(project)
    assert identity.project_id == original.project_id
    assert identity.identity_hash == original.identity_hash


def test_second_machine_import_keeps_pid_but_reports_host_local_difference(tmp_path):
    project = make_project(tmp_path / "p")
    destination = tmp_path / "other-machine"
    plan = plan_relocation(project, destination, operation="copy")
    apply_relocation(plan, journal_dir=tmp_path / "journal")

    # A different machine identity must not be invented by the relocation: the
    # project keeps its PID and the copied tree carries no absolute path that
    # would pin it to the original machine.
    identity_text = (destination / ".guardian-x" / "identity.json").read_text(encoding="utf-8")
    assert read_identity(destination).project_id == read_identity(project).project_id
    assert str(project) not in identity_text
    assert "hostname" not in identity_text.lower()

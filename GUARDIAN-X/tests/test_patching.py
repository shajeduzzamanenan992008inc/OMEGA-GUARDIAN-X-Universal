"""Controlled patch engine: preconditions, backups, rollback and audit."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ogx.patching import (
    PatchApprovalRequired,
    PatchIntegrityError,
    PatchRefused,
    apply_patch,
    list_plans,
    load_plan,
    plan_patch,
    rollback_patch,
)
from ogx.platform.operations import read_audit


@pytest.fixture
def project(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("OGX_HOME", str(state))
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\nOTHER = 2\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    return root


def _plan(root, **kwargs):
    return plan_patch(
        root,
        [{"path": "app.py", "old": "VALUE = 1", "new": "VALUE = 42"}],
        **kwargs,
    )


def test_plan_writes_nothing_into_the_project(project):
    before = {path.name: path.read_bytes() for path in project.iterdir()}
    plan = _plan(project)
    after = {path.name: path.read_bytes() for path in project.iterdir()}
    assert before == after
    assert plan["state"] == "planned"
    assert plan["summary"]["files"] == 1
    stored = load_plan(plan["plan_id"])
    assert stored["schema"] == "ogx-patch-plan-1"
    assert stored["changes"][0]["before_sha256"]


def test_apply_and_rollback_restore_exact_bytes(project):
    original = (project / "app.py").read_bytes()
    plan = _plan(project, project_id="11111111-1111-4111-8111-111111111111")
    result = apply_patch(plan["plan_id"], approved=True, actor_subject="tester")
    assert result["applied"] == ["app.py"]
    assert (project / "app.py").read_text(encoding="utf-8") == "VALUE = 42\nOTHER = 2\n"

    rolled = rollback_patch(plan["plan_id"], approved=True, actor_subject="tester")
    assert rolled["restored"] == ["app.py"]
    assert (project / "app.py").read_bytes() == original
    assert load_plan(plan["plan_id"])["state"] == "rolled_back"


def test_apply_requires_explicit_approval(project):
    plan = _plan(project)
    with pytest.raises(PatchApprovalRequired):
        apply_patch(plan["plan_id"], approved=False)
    assert (project / "app.py").read_text(encoding="utf-8").startswith("VALUE = 1")


def test_stale_plan_is_refused(project):
    plan = _plan(project)
    (project / "app.py").write_text("VALUE = 1\nOTHER = 3\n", encoding="utf-8")
    with pytest.raises(PatchIntegrityError) as error:
        apply_patch(plan["plan_id"], approved=True)
    assert error.value.code == "stale_plan"
    assert "app.py" in error.value.details


def test_rollback_refuses_to_discard_later_edits(project):
    plan = _plan(project)
    apply_patch(plan["plan_id"], approved=True)
    (project / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
    with pytest.raises(PatchIntegrityError) as error:
        rollback_patch(plan["plan_id"], approved=True)
    assert error.value.code == "rollback_drift"
    assert (project / "app.py").read_text(encoding="utf-8") == "VALUE = 99\n"


def test_ambiguous_and_missing_matches_are_refused(project):
    with pytest.raises(PatchRefused) as duplicate:
        plan_patch(project, [{"path": "app.py", "old": "= ", "new": "=  "}])
    assert duplicate.value.code == "ambiguous_match"

    with pytest.raises(PatchRefused) as missing:
        plan_patch(project, [{"path": "app.py", "old": "NOT PRESENT", "new": "x"}])
    assert missing.value.code == "ambiguous_match"


def test_path_traversal_and_absolute_paths_are_refused(project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    for bad in ("../outside.txt", str(outside), "C:/Windows/win.ini", "/etc/passwd"):
        with pytest.raises(PatchRefused):
            plan_patch(project, [{"path": bad, "old": "secret", "new": "x"}])


def test_symlinked_target_is_refused(project):
    if os.name == "nt":
        pytest.skip("symlink creation requires privileges on Windows")
    (project / "link.txt").symlink_to(project / "notes.txt")
    with pytest.raises(PatchRefused) as error:
        plan_patch(project, [{"path": "link.txt", "old": "hello", "new": "bye"}])
    assert error.value.code == "symlink_rejected"


def test_symlink_check_runs_before_resolution(project, monkeypatch):
    """Regression: resolving first erased the symlink component (Linux CI)."""
    target = project / "app.py"
    real = Path.is_symlink

    def fake_is_symlink(self):
        return self == target or real(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    with pytest.raises(PatchRefused) as error:
        plan_patch(project, [{"path": "app.py", "old": "VALUE = 1", "new": "VALUE = 2"}])
    assert error.value.code == "symlink_rejected"


def test_expected_hash_precondition(project):
    with pytest.raises(PatchIntegrityError):
        plan_patch(
            project,
            [{"path": "app.py", "old": "VALUE = 1", "new": "VALUE = 2", "expected_sha256": "0" * 64}],
        )


def test_patch_state_must_live_outside_the_project(project, monkeypatch):
    with pytest.raises(PatchRefused) as error:
        plan_patch(
            project,
            [{"path": "app.py", "old": "VALUE = 1", "new": "VALUE = 2"}],
            state_home=project / ".state",
        )
    assert error.value.code == "state_inside_project"


def test_audit_records_are_hash_chained_and_redacted(project):
    plan = _plan(project)
    apply_patch(plan["plan_id"], approved=True, actor_subject="tester")
    events = read_audit(limit=100)
    names = [event.get("event") for event in events]
    assert "patch.plan.created" in names
    assert "patch.apply.completed" in names
    assert all("integrity" in event for event in events)
    serialized = json.dumps(events)
    assert "VALUE = 42" not in serialized


def test_list_plans_reports_state(project):
    plan = _plan(project)
    apply_patch(plan["plan_id"], approved=True)
    listing = list_plans()
    assert [item["plan_id"] for item in listing] == [plan["plan_id"]]
    assert listing[0]["state"] == "applied"

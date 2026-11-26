"""CLI wiring for patch and workflow commands."""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from ogx.cli import main


def run_cli(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("FLAG = False\n", encoding="utf-8")
    return root


def test_patch_cli_plan_apply_rollback(project, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"changes": [{"path": "app.py", "old": "FLAG = False", "new": "FLAG = True"}]}),
        encoding="utf-8",
    )
    code, out, _ = run_cli(["patch", "plan", str(project), "--spec", str(spec), "--json"])
    assert code == 0
    plan_id = json.loads(out)["plan_id"]
    assert (project / "app.py").read_text(encoding="utf-8") == "FLAG = False\n"

    code, _, err = run_cli(["patch", "apply", plan_id, "--json"])
    assert code == 2
    assert "approval" in err.lower()

    assert run_cli(["patch", "apply", plan_id, "--confirm", "--json"])[0] == 0
    assert (project / "app.py").read_text(encoding="utf-8") == "FLAG = True\n"

    assert run_cli(["patch", "rollback", plan_id, "--confirm", "--json"])[0] == 0
    assert (project / "app.py").read_text(encoding="utf-8") == "FLAG = False\n"

    code, out, _ = run_cli(["patch", "list", "--json"])
    assert code == 0
    assert json.loads(out)[0]["state"] == "rolled_back"


def test_workflow_cli_runs_and_reports_state(project):
    code, out, _ = run_cli([
        "workflow", "create", str(project), "--skip", "edit", "--skip", "test", "--json",
    ])
    assert code == 0
    workflow_id = json.loads(out)["workflow"]["workflow_id"]

    code, out, _ = run_cli(["workflow", "run", workflow_id, "--json"])
    assert code == 0
    workflow = json.loads(out)["workflow"]
    assert workflow["state"] == "succeeded"
    assert any(stage["name"] == "secure" for stage in workflow["stages"])

    code, out, _ = run_cli(["workflow", "status", workflow_id, "--json"])
    assert code == 0
    assert json.loads(out)["state"] == "succeeded"

    code, out, _ = run_cli(["workflow", "events", workflow_id, "--limit", "5", "--json"])
    assert code == 0
    assert len(json.loads(out)) == 5


def test_workflow_cli_reports_approval_gate(project):
    code, out, _ = run_cli(["workflow", "create", str(project), "--json"])
    assert code == 0
    workflow_id = json.loads(out)["workflow"]["workflow_id"]

    code, out, _ = run_cli(["workflow", "run", workflow_id, "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["workflow"]["state"] == "awaiting_approval"
    assert payload["workflow"]["current_stage"] == "edit"

    code, _, _ = run_cli([
        "workflow", "approve", workflow_id, "--stage", "edit", "--actor", "owner@example.com",
    ])
    assert code == 0

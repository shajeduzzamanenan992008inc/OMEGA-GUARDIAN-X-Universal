"""Durable workflow orchestration: gates, resume, retry, cancel, idempotency."""

from __future__ import annotations

import json

import pytest

from ogx.orchestrator import Orchestrator, StageFailed, WorkflowError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    return root


def _engine(tmp_path, **handlers):
    return Orchestrator(db_path=tmp_path / "state" / "workflows" / "orchestrator.db", handlers=handlers)


def test_full_default_workflow_with_gated_stages_skipped(project, tmp_path):
    engine = _engine(tmp_path)
    created = engine.create(project, skip_stages=("edit", "test"))
    workflow_id = created["workflow"]["workflow_id"]
    assert created["created"] is True

    before = sorted(path.name for path in project.iterdir())
    result = engine.run(workflow_id)
    workflow = result["workflow"]
    assert workflow["state"] == "succeeded"
    states = {stage["name"]: stage["state"] for stage in workflow["stages"]}
    assert states["inspect"] == "succeeded"
    assert states["secure"] == "succeeded"
    assert states["review"] == "succeeded"
    assert states["evolve"] == "succeeded"
    assert states["edit"] == "skipped"
    assert states["test"] == "skipped"
    assert sorted(path.name for path in project.iterdir()) == before

    inspect = next(stage for stage in workflow["stages"] if stage["name"] == "inspect")
    assert inspect["output"]["files_examined"] >= 2

    summary = workflow["stages"][-1]["output"]["summary_path"]
    assert json.loads(open(summary, encoding="utf-8").read())["workflow_id"] == workflow_id


def test_workflow_is_durable_across_engine_instances(project, tmp_path):
    engine = _engine(tmp_path)
    workflow_id = engine.create(project, skip_stages=("edit", "test"))["workflow"]["workflow_id"]
    engine.run(workflow_id)

    reopened = _engine(tmp_path)
    status = reopened.status(workflow_id)
    assert status["state"] == "succeeded"
    events = reopened.events(workflow_id)
    assert [event["event"] for event in events][:2] == ["created", "run.started"]
    assert any(event["event"] == "stage.succeeded" for event in events)


def test_edit_requires_recorded_approval(project, tmp_path):
    engine = _engine(tmp_path, edit=lambda ctx: {"applied": []}, test=lambda ctx: {"exit_code": 0})
    workflow_id = engine.create(project)["workflow"]["workflow_id"]

    first = engine.run(workflow_id)["workflow"]
    assert first["state"] == "awaiting_approval"
    assert first["current_stage"] == "edit"
    edit_stage = next(stage for stage in first["stages"] if stage["name"] == "edit")
    assert edit_stage["state"] == "awaiting_approval"

    # Running again without approval must not execute the gated stage.
    again = engine.run(workflow_id)["workflow"]
    assert again["state"] == "awaiting_approval"
    assert again["stages"][2]["attempts"] == 0

    engine.approve(workflow_id, "edit", actor="owner@example.com", note="reviewed")
    resumed = engine.run(workflow_id)["workflow"]
    assert resumed["current_stage"] == "test"
    assert resumed["state"] == "awaiting_approval"
    assert next(stage for stage in resumed["stages"] if stage["name"] == "edit")["state"] == "succeeded"

    engine.approve(workflow_id, "test", actor="owner@example.com")
    final = engine.run(workflow_id)["workflow"]
    assert final["state"] == "succeeded"
    assert all(stage["state"] in {"succeeded", "skipped"} for stage in final["stages"])


def test_missing_patch_plan_fails_honestly(project, tmp_path):
    engine = _engine(tmp_path)
    workflow_id = engine.create(project, skip_stages=("test",))["workflow"]["workflow_id"]
    engine.approve(workflow_id, "edit", actor="owner@example.com")
    result = engine.run(workflow_id)["workflow"]
    assert result["state"] == "failed"
    edit = next(stage for stage in result["stages"] if stage["name"] == "edit")
    assert edit["state"] == "failed"
    assert "no patch plan is attached" in edit["error"]


def test_retry_resets_only_a_failed_stage(project, tmp_path):
    attempts = {"count": 0}

    def flaky(ctx):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise StageFailed("plan", "transient failure", code="transient")
        return {"recovered": True}

    engine = _engine(tmp_path, plan=flaky, edit=lambda ctx: {}, test=lambda ctx: {})
    workflow_id = engine.create(project, skip_stages=("edit", "test"))["workflow"]["workflow_id"]
    failed = engine.run(workflow_id)["workflow"]
    assert failed["state"] == "failed"
    assert failed["current_stage"] == "plan"

    engine.retry(workflow_id)
    recovered = engine.run(workflow_id)["workflow"]
    assert recovered["state"] == "succeeded"
    assert attempts["count"] == 2

    plan_stage = next(stage for stage in recovered["stages"] if stage["name"] == "plan")
    assert plan_stage["attempts"] == 2


def test_cancel_is_terminal(project, tmp_path):
    engine = _engine(tmp_path)
    workflow_id = engine.create(project, skip_stages=("edit", "test"))["workflow"]["workflow_id"]
    cancelled = engine.cancel(workflow_id, actor="owner@example.com", reason="superseded")
    assert cancelled["state"] == "cancelled"
    assert not any(stage["state"] in {"pending", "running", "awaiting_approval"} for stage in cancelled["stages"])
    skipped = [stage for stage in cancelled["stages"] if stage["name"] in {"edit", "test"}]
    assert all(stage["state"] == "skipped" for stage in skipped)
    assert engine.run(workflow_id)["executed"] == []
    with pytest.raises(WorkflowError):
        engine.cancel(workflow_id, actor="owner@example.com")


def test_idempotency_key_returns_the_existing_workflow(project, tmp_path):
    engine = _engine(tmp_path)
    first = engine.create(project, idempotency_key="plan-42", skip_stages=("edit", "test"))
    second = engine.create(project, idempotency_key="plan-42", skip_stages=("edit", "test"))
    assert first["created"] is True
    assert second["created"] is False
    assert first["workflow"]["workflow_id"] == second["workflow"]["workflow_id"]


def test_unknown_stage_is_refused(project, tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(WorkflowError) as error:
        engine.create(project, stages=("inspect", "does-not-exist"))
    assert error.value.code == "unknown_stage"


def test_approval_requires_an_identifiable_actor(project, tmp_path):
    engine = _engine(tmp_path)
    workflow_id = engine.create(project)["workflow"]["workflow_id"]
    with pytest.raises(WorkflowError) as error:
        engine.approve(workflow_id, "edit", actor="  ")
    assert error.value.code == "missing_actor"


def test_corrupt_or_unknown_workflow_is_not_found(project, tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(WorkflowError) as error:
        engine.status("00000000-0000-4000-8000-000000000000")
    assert error.value.code == "workflow_not_found"

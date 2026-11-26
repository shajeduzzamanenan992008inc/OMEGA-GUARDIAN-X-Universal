"""CLI integration tests for host identity, identity explain/fork and relocate."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from ogx.cli import main
from ogx.hosts import lifecycle as lifecycle_module
from ogx.hosts import registry as host_registry_module
from ogx.pap import create_identity, read_identity
from ogx.registry import v2 as registry_module


@pytest.fixture
def cli_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    if registry_module._global_registry is not None:
        registry_module._global_registry.close()
    registry_module._global_registry = None
    host_registry_module._global_host_registry = None
    lifecycle_module.reset_session_manager()
    yield tmp_path
    manager = lifecycle_module.get_session_manager()
    manager.shutdown()
    for session in manager.list_sessions():
        try:
            manager.dispose(session.session_id)
        except Exception:
            pass
    lifecycle_module.reset_session_manager()
    if registry_module._global_registry is not None:
        registry_module._global_registry.close()
    registry_module._global_registry = None
    host_registry_module._global_host_registry = None


def _json(capsys):
    return json.loads(capsys.readouterr().out)


def _project(root):
    root.mkdir(parents=True, exist_ok=True)
    create_identity(root, project_name="Lifecycle")
    (root / "app.py").write_text("print('x')\n", encoding="utf-8")
    return root


def test_host_register_is_idempotent_for_same_root(cli_state, capsys):
    root = cli_state / "root"
    root.mkdir(exist_ok=True)
    args = [
        "host", "register", "--type", "local_filesystem", "--provider", "local",
        "--name", "Root", "--identifier", str(root), "--json",
    ]
    assert main(args) == 0
    first = _json(capsys)
    assert first["registration"] == "new"

    assert main(args) == 0
    second = _json(capsys)
    assert second["registration"] == "updated"
    assert second["host_id"] == first["host_id"]
    assert second["machine_host_id"] == first["machine_host_id"]

    assert main(["host", "list", "--json"]) == 0
    hosts = _json(capsys)["hosts"]
    assert len(hosts) == 1


def test_host_identity_command(cli_state, capsys):
    assert main(["host", "identity", "--json"]) == 0
    payload = _json(capsys)
    assert payload["host_id"]
    assert payload["binding_hash"].startswith("sha256:")
    assert payload["verification"]["state"] in {"verified", "weak"}

    # Stable across invocations.
    assert main(["host", "identity", "--json"]) == 0
    again = _json(capsys)
    assert again["host_id"] == payload["host_id"]


def test_identity_explain_verified(cli_state, capsys):
    project = _project(cli_state / "project")
    assert main(["identity", "explain", str(project), "--json"]) == 0
    report = _json(capsys)
    assert report["classification"] == "verified"


def test_identity_fork_command(cli_state, capsys):
    source = _project(cli_state / "source")
    parent = read_identity(source)
    destination = cli_state / "fork"

    assert main([
        "identity", "fork", str(source),
        "--destination", str(destination),
        "--name", "Child",
        "--confirm", "--json",
    ]) == 0
    result = _json(capsys)
    assert result["parent_project_id"] == parent.project_id
    assert result["project_id"] != parent.project_id
    assert read_identity(destination).project_name == "Child"


def test_identity_recover_uses_manifest_evidence(cli_state, capsys):
    project = cli_state / "recover-manifest"
    project.mkdir()
    manifest_project_id = str(uuid4())
    manifest_dir = project / "OMEGA GUARDIAN X"
    manifest_dir.mkdir()
    (manifest_dir / "integration-manifest.json").write_text(
        json.dumps({"project_id": manifest_project_id}), encoding="utf-8"
    )

    assert main(["identity", "recover", str(project), "--confirm"]) == 0
    output = capsys.readouterr().out
    assert "evidence: manifest" in output
    assert read_identity(project).project_id == manifest_project_id


def test_identity_recover_uses_registry_evidence(cli_state, capsys):
    project = cli_state / "recover-registry"
    project.mkdir()

    # Register the project and its workspace location directly, then recover
    # from that evidence alone: no identity file exists in the project.
    registry = registry_module.get_registry()
    project_id = uuid4()
    registry.register_project(project_id=project_id, canonical_name="Registered")
    registry.register_workspace(
        workspace_id=uuid4(),
        project_id=project_id,
        location_reference=str(project),
        normalized_location=str(project.resolve()),
    )

    assert main(["identity", "recover", str(project), "--confirm"]) == 0
    output = capsys.readouterr().out
    assert "evidence: registry" in output
    assert read_identity(project).project_id == str(project_id)


def test_relocate_plan_apply_verify(cli_state, capsys):
    source = _project(cli_state / "source")
    destination = cli_state / "destination"

    assert main(["relocate", "plan", str(source), str(destination), "--json"]) == 0
    plan = _json(capsys)
    assert plan["project_id"] == read_identity(source).project_id

    assert main([
        "relocate", "apply", str(source), str(destination), "--json", "--no-registry",
    ]) == 0
    applied = _json(capsys)
    assert applied["state"] == "complete"
    assert applied["source_deleted"] is False

    assert main(["relocate", "verify", str(source), str(destination), "--json"]) == 0
    report = _json(capsys)
    assert report["identity_preserved"] is True
    assert report["content_matches"] is True


def test_relocate_refuses_existing_destination(cli_state, capsys):
    source = _project(cli_state / "source")
    destination = cli_state / "existing"
    destination.mkdir()
    assert main(["relocate", "plan", str(source), str(destination)]) == 2
    assert "destination_exists" in capsys.readouterr().err

"""CLI lifecycle tests: register, connect, health, sessions, disconnect."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ogx.cli import main
from ogx.hosts import lifecycle as lifecycle_module
from ogx.hosts import registry as host_registry_module
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


def test_register_requires_root_for_local_hosts(cli_state):
    assert main(["host", "register", "--type", "local_filesystem", "--provider",
                 "local", "--name", "No Root"]) == 2


def test_register_connect_sessions_status_disconnect(cli_state, capsys):
    root = cli_state / "project"
    root.mkdir()
    (root / "app.py").write_text("print('x')\n", encoding="utf-8")

    assert main(["host", "register", "--type", "local_filesystem", "--provider",
                 "local", "--name", "Project", "--identifier", str(root), "--json"]) == 0
    host = _json(capsys)["host_id"]

    assert main(["host", "connect", host, "--json"]) == 0
    connected = _json(capsys)
    assert connected["state"] == "connected"
    assert connected["capabilities"]["file_read"] == "supported"
    assert connected["capabilities"]["file_write"] == "unsupported"

    assert main(["host", "sessions", "--json"]) == 0
    sessions = _json(capsys)
    assert sessions["process_local"] is True
    assert len(sessions["sessions"]) == 1

    assert main(["host", "status", host, "--json"]) == 0
    status = _json(capsys)
    assert status["status"] == "connected"
    assert status["live_session"]["host_id"] == host
    assert status["persisted_status"] != "connected"

    assert main(["host", "health", host, "--json"]) == 0
    health = _json(capsys)
    assert health["health"]["state"] == "healthy"

    assert main(["host", "disconnect", host, "--json"]) == 0
    assert _json(capsys)["disconnected"]

    assert main(["host", "sessions", "--json"]) == 0
    assert _json(capsys)["sessions"] == []


def test_health_probe_connects_without_live_session(cli_state, capsys):
    root = cli_state / "probe"
    root.mkdir()
    assert main(["host", "register", "--type", "local_git", "--provider",
                 "local", "--name", "Git", "--identifier", str(root), "--json"]) == 0
    host = _json(capsys)["host_id"]
    # Not a Git repository, so the probe must fail and report failure.
    assert main(["host", "health", host]) == 1
    assert "failed" in capsys.readouterr().err.lower()


def test_health_failure_returns_nonzero(cli_state, capsys):
    root = cli_state / "missing-root"
    root.mkdir()
    assert main(["host", "register", "--type", "local_filesystem", "--provider",
                 "local", "--name", "FS", "--identifier", str(root), "--json"]) == 0
    host = _json(capsys)["host_id"]
    root.rmdir()
    assert main(["host", "health", host]) == 1


def test_disconnect_without_live_session_is_idempotent(cli_state, capsys):
    root = cli_state / "idem"
    root.mkdir()
    assert main(["host", "register", "--type", "local_filesystem", "--provider",
                 "local", "--name", "FS", "--identifier", str(root), "--json"]) == 0
    host = _json(capsys)["host_id"]
    assert main(["host", "disconnect", host]) == 0
    assert "no live session" in capsys.readouterr().out.lower()


def test_unknown_host_returns_two(cli_state):
    assert main(["host", "connect", "00000000-0000-0000-0000-000000000001"]) == 2
    assert main(["host", "status", "00000000-0000-0000-0000-000000000001"]) == 2

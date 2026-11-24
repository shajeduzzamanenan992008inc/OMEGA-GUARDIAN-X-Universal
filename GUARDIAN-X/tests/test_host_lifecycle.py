"""Host lifecycle contract tests: states, sessions, persistence and capabilities."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ogx.hosts.lifecycle import (
    HealthState,
    HostLifecycleError,
    HostLifecycleState,
    HostSessionConfig,
    HostSessionManager,
    InvalidTransitionError,
    DuplicateSessionError,
    SessionNotFoundError,
    UnsupportedHostTypeError,
)
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2


@pytest.fixture
def manager(tmp_path):
    registry = RegistryV2(tmp_path / "registry.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    events: list[dict] = []
    manager = HostSessionManager(registry=hosts, audit_sink=events.append)
    yield manager, hosts, registry, events
    manager.shutdown()
    registry.close()


def _register(hosts, tmp_path, host_type="local_filesystem"):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    return hosts.register_host(
        host_id=uuid4(),
        host_type=host_type,
        provider="local",
        display_name="Lifecycle",
        normalized_identifier=str(root),
    )


def test_session_starts_configured_not_connected(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    session = manager.create_session(host.host_id)
    assert session.state is HostLifecycleState.CONFIGURED
    assert session.connected_at is None
    assert manager.effective_capabilities(session.session_id)["capabilities"]["file_read"] == "unavailable"
    assert hosts.get_host(host.host_id)["status"] == "registered"


def test_connect_then_disconnect_transitions_and_audit(manager, tmp_path):
    manager, hosts, _, events = manager
    host = _register(hosts, tmp_path)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    assert session.state is HostLifecycleState.CONNECTED
    assert session.last_health is HealthState.HEALTHY
    assert manager.effective_capabilities(session.session_id)["capabilities"]["file_read"] == "supported"

    manager.disconnect(session.session_id)
    assert session.state is HostLifecycleState.DISCONNECTED
    assert hosts.get_host(host.host_id)["status"] == "disconnected"
    # Idempotent disconnect.
    manager.disconnect(session.session_id)
    actions = [event["action"] for event in events]
    assert "host.session.connected" in actions
    assert "host.session.disconnected" in actions


def test_connected_state_is_never_persisted(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    assert hosts.get_host(host.host_id)["status"] != "connected"


def test_restart_does_not_restore_connected_session(tmp_path):
    db = tmp_path / "registry.db"
    registry = RegistryV2(db)
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    root = tmp_path / "root"
    root.mkdir()
    host = hosts.register_host(uuid4(), "local_filesystem", "local", "Root", str(root))
    manager = HostSessionManager(registry=hosts)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    registry.close()

    second_registry = RegistryV2(db)
    second_registry.initialize()
    second_hosts = HostRegistry(registry=second_registry)
    second_manager = HostSessionManager(registry=second_hosts)
    assert second_manager.list_sessions() == []
    assert second_hosts.get_host(host.host_id)["status"] == "registered"
    second_registry.close()


def test_missing_host_and_invalid_ids(manager):
    manager, _, _, _ = manager
    with pytest.raises(KeyError):
        manager.create_session(uuid4())
    with pytest.raises(SessionNotFoundError):
        manager.connect(uuid4())
    with pytest.raises(HostLifecycleError):
        manager.create_session("not-a-uuid")


def test_duplicate_active_session_rejected(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    manager.create_session(host.host_id)
    with pytest.raises(DuplicateSessionError):
        manager.create_session(host.host_id)


def test_invalid_transition_is_rejected(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    session = manager.create_session(host.host_id)
    with pytest.raises(InvalidTransitionError):
        manager.health(session.session_id)
    with pytest.raises(InvalidTransitionError):
        manager.dispose(session.session_id)


def test_unsupported_host_type_fails_closed(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = hosts.register_host(uuid4(), "github", "github", "GitHub", "https://github.com/acme")
    with pytest.raises(UnsupportedHostTypeError):
        manager.create_session(host.host_id)


def test_invalid_config_rejected(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    with pytest.raises(HostLifecycleError):
        manager.create_session(host.host_id, root="")
    with pytest.raises(HostLifecycleError):
        manager.create_session(host.host_id, max_file_bytes=0)


def test_missing_root_fails_connect_without_claiming_connected(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    missing = tmp_path / "does-not-exist"
    session = manager.create_session(host.host_id, root=str(missing))
    with pytest.raises(HostLifecycleError):
        manager.connect(session.session_id)
    assert session.state is HostLifecycleState.FAILED
    assert session.connected_at is None


def test_shutdown_closes_sessions(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    result = manager.shutdown()
    assert result["errors"] == []
    assert session.state is HostLifecycleState.CLOSED
    assert manager.list_sessions() != []
    manager.dispose(session.session_id)
    assert manager.list_sessions() == []


def test_dispose_requires_terminal_state(manager, tmp_path):
    manager, hosts, _, _ = manager
    host = _register(hosts, tmp_path)
    session = manager.create_session(host.host_id)
    with pytest.raises(InvalidTransitionError):
        manager.dispose(session.session_id)
    manager.disconnect  # not connected yet; terminal path is shutdown
    manager.shutdown()
    manager.dispose(session.session_id)


def test_session_config_rejects_invalid_limits():
    with pytest.raises(HostLifecycleError):
        HostSessionConfig(root="x", max_directory_entries=0)
    with pytest.raises(HostLifecycleError):
        HostSessionConfig(root="x", timeout_seconds=0)

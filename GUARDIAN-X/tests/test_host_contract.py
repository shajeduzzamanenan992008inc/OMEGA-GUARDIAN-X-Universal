"""Shared host contract tests for the registry and implemented local adapters."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ogx.hosts.adapters.local_filesystem import LocalFilesystemProvider
from ogx.hosts.adapters.local_git import LocalGitProvider
from ogx.hosts.capabilities import CAPABILITY_MATRIX
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2

CAPABILITY_STATES = {
    "supported",
    "partial",
    "unsupported",
    "requires_auth",
    "requires_permission",
    "requires_workspace",
    "not_configured",
    "unavailable",
    "failed",
}


@pytest.fixture
def host_registry(tmp_path):
    registry = RegistryV2(tmp_path / "registry.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    yield hosts, registry
    registry.close()


def _register(hosts, host_id=None, **kwargs):
    return hosts.register_host(
        host_id=host_id or uuid4(),
        host_type=kwargs.pop("host_type", "local_filesystem"),
        provider=kwargs.pop("provider", "local"),
        display_name=kwargs.pop("display_name", "Test Host"),
        normalized_identifier=kwargs.pop("normalized_identifier", "local://test"),
        **kwargs,
    )


def test_registration_and_retrieval(host_registry):
    hosts, _ = host_registry
    host_id = uuid4()
    record = _register(hosts, host_id)
    assert str(record.host_id) == str(host_id)

    fetched = hosts.get_host(host_id)
    assert fetched["host_id"] == str(host_id)
    assert fetched["host_type"] == "local_filesystem"
    assert fetched["status"] == "active"
    assert isinstance(fetched["capabilities"], list)


def test_duplicate_registration_updates_without_duplicating(host_registry):
    hosts, _ = host_registry
    host_id = uuid4()
    _register(hosts, host_id, display_name="First")
    _register(hosts, host_id, display_name="Second")
    assert len(hosts.list_hosts()) == 1


def test_missing_host_raises(host_registry):
    hosts, _ = host_registry
    with pytest.raises(KeyError):
        hosts.get_host(uuid4())


def test_invalid_host_id_fails_explicitly(host_registry):
    hosts, _ = host_registry
    with pytest.raises(ValueError):
        hosts.register_host("not-a-uuid", "local_filesystem")
    with pytest.raises(ValueError):
        hosts.get_host("not-a-uuid")


def test_unknown_host_type_is_not_silently_trusted(host_registry):
    hosts, _ = host_registry
    record = _register(hosts, host_type="not_a_real_host")
    assert record.host_type.value == "unknown"
    assert hosts.get_host(record.host_id)["host_type"] == "unknown"


def test_listing_filters_and_pagination(host_registry):
    hosts, _ = host_registry
    first = _register(hosts, display_name="A")
    _register(hosts, host_type="local_git", display_name="B")
    assert len(hosts.list_hosts()) == 2
    assert [h["host_id"] for h in hosts.list_hosts(host_type="local_git")] == [
        str(_host_id_of(hosts, "B"))
    ]
    assert len(hosts.list_hosts(limit=1)) == 1
    assert hosts.find_host_by_identifier(first.normalized_identifier) is not None


def _host_id_of(hosts, display_name):
    for host in hosts.list_hosts():
        if host["display_name"] == display_name:
            return host["host_id"]
    raise AssertionError(display_name)


def test_status_lifecycle_and_soft_delete(host_registry):
    hosts, _ = host_registry
    record = _register(hosts)
    hosts.update_host_status(record.host_id, "inactive")
    assert hosts.get_host(record.host_id)["status"] == "inactive"
    assert hosts.remove_host(record.host_id) is True
    assert hosts.get_host(record.host_id)["status"] == "inactive"
    hosts.update_host_last_seen(record.host_id)
    assert hosts.get_host(record.host_id)["status"] == "active"
    assert hosts.remove_host(uuid4()) is False


def test_registry_persistence_survives_reload(tmp_path):
    db_path = tmp_path / "registry.db"
    first = RegistryV2(db_path)
    first.initialize()
    hosts = HostRegistry(registry=first)
    record = _register(hosts)
    first.close()

    second = RegistryV2(db_path)
    second.initialize()
    reloaded = HostRegistry(registry=second)
    assert reloaded.get_host(record.host_id)["host_id"] == str(record.host_id)
    second.close()


def test_project_host_links(host_registry):
    hosts, registry = host_registry
    project_id = uuid4()
    registry.register_project(project_id=project_id, canonical_name="Linked")
    record = _register(hosts)

    hosts.link_project_host(project_id, record.host_id)
    linked = hosts.get_hosts_for_project(project_id)
    assert [host["host_id"] for host in linked] == [str(record.host_id)]

    hosts.unlink_project_host(project_id, record.host_id)
    assert hosts.get_hosts_for_project(project_id) == []


def test_capability_reporting_is_consistent(host_registry):
    hosts, _ = host_registry
    record = _register(hosts)
    all_caps = hosts.get_host_capabilities(record.host_id)
    supported = hosts.get_host_supported_capabilities(record.host_id)
    assert set(all_caps.values()) <= CAPABILITY_STATES
    assert set(supported) <= set(all_caps)
    assert all(all_caps[cap] == "supported" for cap in supported)
    for host_type in ("local_filesystem", "local_git", "unknown"):
        matrix = CAPABILITY_MATRIX.get_all_capabilities(host_type)
        assert isinstance(matrix, dict)


def test_local_filesystem_adapter_contract(tmp_path):
    provider = LocalFilesystemProvider()
    provider.initialize(str(uuid4()))
    assert provider.host_type == "local_filesystem"
    assert provider.provider_type == "local"
    info = provider.get_provider_info()
    assert info["host_type"] == "local_filesystem"
    assert isinstance(info["capabilities"], dict)
    assert provider.check_workspace_access(str(uuid4())) is False
    provider.add_discovery_root(str(tmp_path))
    assert provider.list_discovery_roots()[0]["path"] == str(tmp_path)


def test_local_git_adapter_contract():
    provider = LocalGitProvider()
    assert provider.host_type == "local_git"
    assert provider.provider_type == "local"
    info = provider.get_provider_info()
    assert info["host_type"] == "local_git"
    assert isinstance(info["git_available"], bool)
    assert provider.detect_provider("https://github.com/acme/demo.git") == "github"
    assert provider.discover_repositories("/path/that/does/not/exist") == []

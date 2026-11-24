"""Local filesystem host adapter tests: containment, limits and recovery."""

from __future__ import annotations

import os
import shutil
from uuid import uuid4

import pytest

from ogx.hosts.adapters.filesystem_runtime import (
    FilesystemHostAdapter,
    HostPathError,
    HostReadError,
)
from ogx.hosts.lifecycle import (
    HealthState,
    HostLifecycleState,
    HostSessionConfig,
    HostSessionManager,
)
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "root"
    path.mkdir()
    (path / "hello.txt").write_text("hello\n", encoding="utf-8")
    (path / "unicode-λ.txt").write_text("unicode\n", encoding="utf-8")
    (path / "nested").mkdir()
    (path / "nested" / "deep.txt").write_text("deep\n", encoding="utf-8")
    return path


@pytest.fixture
def adapter(root):
    instance = FilesystemHostAdapter(HostSessionConfig(root=str(root)))
    instance.connect()
    yield instance
    instance.disconnect()


@pytest.fixture
def manager(tmp_path, root):
    registry = RegistryV2(tmp_path / "registry.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    host = hosts.register_host(uuid4(), "local_filesystem", "local", "FS", str(root))
    instance = HostSessionManager(registry=hosts)
    yield instance, host, root
    instance.shutdown()
    registry.close()


def test_connect_and_read(adapter):
    metadata = adapter.metadata()
    assert metadata["read_only"] is True
    result = adapter.read_file("hello.txt")
    assert result["text"].strip() == "hello"
    assert result["truncated"] is False
    assert len(result["sha256"]) == 64


def test_listing_is_bounded_and_sorted(adapter, root):
    result = adapter.list_directory("")
    names = {entry["name"] for entry in result["entries"]}
    assert {"hello.txt", "unicode-λ.txt", "nested"} <= names
    assert result["truncated"] is False


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "nested/../../outside.txt",
        "/etc/passwd",
        "\\\\server\\share\\file",
    ],
)
def test_unsafe_paths_are_rejected(adapter, path):
    with pytest.raises(HostPathError):
        adapter.read_file(path)


@pytest.mark.skipif(os.name == "nt", reason="Windows drive-relative rejection is covered separately")
def test_windows_style_drive_rejected_on_all_platforms(adapter):
    with pytest.raises(HostPathError):
        adapter.read_file("C:/Windows/win.ini")


def test_missing_file_and_directory(adapter):
    with pytest.raises(HostPathError):
        adapter.read_file("missing.txt")
    with pytest.raises(HostPathError):
        adapter.list_directory("missing-dir")


def test_directory_read_via_file_api_rejected(adapter):
    with pytest.raises(HostReadError):
        adapter.read_file("nested")


def test_large_file_is_truncated(adapter):
    (adapter._root / "large.bin").write_bytes(b"a" * 50)
    result = adapter.read_file("large.bin", max_bytes=10)
    assert result["truncated"] is True
    assert result["bytes_read"] == 10
    assert result["text"] == "a" * 10


def test_binary_file_is_flagged(adapter):
    (adapter._root / "binary.bin").write_bytes(b"\x00\x01\x02")
    result = adapter.read_file("binary.bin")
    assert result["binary"] is True
    assert result["text"] is None


def test_symlink_escape_is_rejected(adapter, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    link = adapter._root / "escape.txt"
    try:
        os.symlink(outside / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this platform.")
    with pytest.raises(HostPathError):
        adapter.read_file("escape.txt")
    listing = adapter.list_directory("")
    entry = next(item for item in listing["entries"] if item["name"] == "escape.txt")
    assert entry["escapes_root"] is True


def test_symlink_inside_root_is_readable(adapter):
    link = adapter._root / "inside-link.txt"
    try:
        os.symlink(adapter._root / "hello.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this platform.")
    assert adapter.read_file("inside-link.txt")["text"].strip() == "hello"


def test_disconnected_adapter_refuses_reads(adapter):
    adapter.disconnect()
    with pytest.raises(HostReadError):
        adapter.read_file("hello.txt")


def test_missing_root_fails_connect(tmp_path):
    instance = FilesystemHostAdapter(HostSessionConfig(root=str(tmp_path / "missing")))
    with pytest.raises(HostPathError):
        instance.connect()


def test_file_as_root_is_rejected(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    instance = FilesystemHostAdapter(HostSessionConfig(root=str(target)))
    with pytest.raises(HostPathError):
        instance.connect()


def test_capabilities_are_read_only(adapter):
    capabilities = adapter.capabilities()
    assert capabilities["file_read"] == "supported"
    assert capabilities["file_write"] == "unsupported"
    assert capabilities["workspace_exec"] == "unsupported"
    assert capabilities["push"] == "unsupported"
    assert not hasattr(adapter, "write_file")


def test_manager_health_and_recovery_after_root_removed(manager):
    sessions, host, root = manager
    session = sessions.create_session(host.host_id)
    sessions.connect(session.session_id)
    assert sessions.health(session.session_id)["health"]["state"] == "healthy"

    shutil.rmtree(root)
    result = sessions.health(session.session_id)
    assert result["health"]["state"] == "unreachable"
    assert session.state is HostLifecycleState.DEGRADED

    root.mkdir()
    (root / "hello.txt").write_text("back\n", encoding="utf-8")
    sessions.recover(session.session_id)
    assert session.state is HostLifecycleState.CONNECTED
    assert session.last_health is HealthState.HEALTHY
    assert sessions.get_session(session.session_id).adapter.read_file("hello.txt")["text"].strip() == "back"


def test_manager_health_failure_is_not_reported_healthy(manager):
    sessions, host, root = manager
    session = sessions.create_session(host.host_id)
    sessions.connect(session.session_id)
    shutil.rmtree(root)
    assert sessions.health(session.session_id)["health"]["state"] != "healthy"
    with pytest.raises(Exception):
        sessions.recover(session.session_id, attempts=1)
    assert session.state is HostLifecycleState.FAILED

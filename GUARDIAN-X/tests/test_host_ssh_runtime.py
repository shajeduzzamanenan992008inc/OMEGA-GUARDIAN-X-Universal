"""Remote SSH host adapter tests (HOST-08): parsing, fail-closed, mocked transport."""
from __future__ import annotations

import subprocess
from unittest.mock import patch
from uuid import uuid4

import pytest

from ogx.hosts.adapters.ssh_runtime import (
    SshAuthenticationError,
    SshCommandError,
    SshHostAdapter,
    SshHostError,
    SshHostKeyError,
    SshTimeoutError,
)
from ogx.hosts.lifecycle import (
    HealthState,
    HostLifecycleState,
    HostSessionConfig,
    HostSessionManager,
)
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2


def _completed(stdout=b"", stderr=b"", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def adapter():
    cfg = HostSessionConfig(
        root="deploy@example.com:/srv/app",
        metadata={"known_hosts": "/tmp/known_hosts", "ssh_port": 2222},
        timeout_seconds=5.0,
    )
    a = SshHostAdapter(cfg)
    a.connect = lambda: None  # bypass for unit; we set connected manually
    a._connected = True
    a._parse_config()
    return a


def test_parse_scp_style(adapter):
    assert adapter._host == "example.com"
    assert adapter._user == "deploy"
    assert adapter._port == 2222
    assert adapter._remote_root == "/srv/app"
    assert adapter._known_hosts == "/tmp/known_hosts"


def test_strict_host_key_cannot_be_disabled(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    cfg = HostSessionConfig(
        root="h:/p",
        metadata={"ssh_host": "h", "strict_host_key_checking": False},
    )
    a = SshHostAdapter(cfg)
    with pytest.raises(SshHostError):
        a._parse_config()


def test_missing_host_raises():
    cfg = HostSessionConfig(root="x", metadata={})
    a = SshHostAdapter(cfg)
    with pytest.raises(SshHostError):
        a._parse_config()


def test_invalid_port_raises():
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h", "ssh_port": 0})
    a = SshHostAdapter(cfg)
    with pytest.raises(SshHostError):
        a._parse_config()


def test_identity_file_missing_raises(tmp_path):
    cfg = HostSessionConfig(
        root="h:/p",
        metadata={"ssh_host": "h", "identity_file": str(tmp_path / "nope")},
    )
    a = SshHostAdapter(cfg)
    with pytest.raises(SshHostError):
        a._parse_config()


def test_ssh_binary_missing(monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h"})
    a = SshHostAdapter(cfg)
    a._parse_config()
    with pytest.raises(SshHostError):
        a._ssh_binary()


def test_validate_success(monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/ssh")
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h"})
    a = SshHostAdapter(cfg)
    a._parse_config()
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(returncode=0))
    a.validate()  # should not raise


def test_validate_host_key_failure(monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/ssh")
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h"})
    a = SshHostAdapter(cfg)
    a._parse_config()
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: _completed(stderr=b"Host key verification failed.", returncode=255),
    )
    with pytest.raises(SshHostKeyError):
        a.validate()


def test_validate_auth_failure(monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/ssh")
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h"})
    a = SshHostAdapter(cfg)
    a._parse_config()
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: _completed(stderr=b"Permission denied (publickey).", returncode=255),
    )
    with pytest.raises(SshAuthenticationError):
        a.validate()


def test_validate_timeout(monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/ssh")
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h"})
    a = SshHostAdapter(cfg)
    a._parse_config()

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)
    monkeypatch.setattr(mod.subprocess, "run", raise_timeout)
    with pytest.raises(SshTimeoutError):
        a.validate()


def test_read_file_success(adapter, monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=b"hello\n", returncode=0))
    res = adapter.read_file("hello.txt")
    assert res["text"].strip() == "hello"
    assert res["truncated"] is False
    assert len(res["sha256"]) == 64
    assert res["remote_path"].endswith("hello.txt")


def test_read_file_traversal_rejected(adapter):
    with pytest.raises(SshHostError):
        adapter._remote_path("../etc/passwd")


def test_read_file_requires_connected(monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    cfg = HostSessionConfig(root="h:/p", metadata={"ssh_host": "h"})
    a = SshHostAdapter(cfg)
    a._parse_config()
    a._connected = False
    with pytest.raises(SshHostError):
        a.read_file("x")


def test_list_directory(adapter, monkeypatch):
    out = b"total 4\ndrwxr-xr-x 2 u g 4096 Jan 1 00:00 dir\n-rw-r--r-- 1 u g 10 Jan 1 00:00 file.txt\n"
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=out, returncode=0))
    res = adapter.list_directory("")
    names = {e["name"] for e in res["entries"]}
    assert "dir" in names and "file.txt" in names
    types = {e["name"]: e["type"] for e in res["entries"]}
    assert types["dir"] == "directory"
    assert types["file.txt"] == "file"


def test_capabilities_truthful(adapter):
    caps = adapter.capabilities()
    assert caps["file_read"] == "supported"
    assert caps["file_write"] == "requires_auth"
    assert caps.get("branch_read", "unsupported") == "unsupported"


def test_base_args_include_strict_host_key(adapter):
    args = adapter._base_args()
    joined = " ".join(args)
    assert "StrictHostKeyChecking=yes" in joined
    assert "BatchMode=yes" in joined
    assert "UserKnownHostsFile=/tmp/known_hosts" in joined


def test_health_unreachable(adapter, monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stderr=b"boom", returncode=255))
    h = adapter.health()
    assert h.state in (HealthState.DEGRADED, HealthState.UNREACHABLE)


def test_manager_integration(tmp_path, monkeypatch):
    import ogx.hosts.adapters.ssh_runtime as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/ssh")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(returncode=0))
    registry = RegistryV2(tmp_path / "reg.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    host = hosts.register_host(uuid4(), "remote_ssh", "local", "SSH", "deploy@example.com:/srv/app")
    manager = HostSessionManager(registry=hosts)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    assert session.state == HostLifecycleState.CONNECTED
    manager.shutdown()
    registry.close()

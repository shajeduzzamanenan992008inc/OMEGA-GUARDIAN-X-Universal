"""Docker host adapter tests (HOST-09): parsing, fail-closed, mocked transport."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from unittest.mock import patch
from uuid import uuid4

import pytest

from ogx.hosts.adapters.docker_runtime import (
    DockerCommandError,
    DockerDaemonError,
    DockerHostAdapter,
    DockerHostError,
    DockerNotFoundError,
    DockerPermissionError,
    DockerResponseError,
    DockerTimeoutError,
    DockerUnavailableError,
)
from ogx.hosts.lifecycle import (
    HealthState,
    HostLifecycleState,
    HostSessionConfig,
    HostSessionManager,
)
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2


@pytest.fixture(autouse=True)
def _docker_available(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")


def _completed(stdout=b"", stderr=b"", returncode=0):
    return subprocess.CompletedProcess(args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr)


def _tar_bytes(name="file.txt", content=b"hello\n", size=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        info = tarfile.TarInfo(name=name)
        info.size = len(content) if size is None else size
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture
def adapter():
    cfg = HostSessionConfig(
        root="mycontainer:/app",
        metadata={"container": "mycontainer"},
        timeout_seconds=5.0,
    )
    a = DockerHostAdapter(cfg)
    a._connected = True
    a._parse_config()
    return a


def test_parse_container_root():
    cfg = HostSessionConfig(root="abc123:/srv", metadata={})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    assert a._container == "abc123"
    assert a._base_path == "/srv"


def test_parse_metadata_container():
    cfg = HostSessionConfig(root="dockerhost", metadata={"container": "ctr1", "base_path": "/data"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    assert a._container == "ctr1"
    assert a._base_path == "/data"


def test_invalid_container_id_rejected():
    cfg = HostSessionConfig(root="bad name:/p", metadata={})
    a = DockerHostAdapter(cfg)
    with pytest.raises(DockerHostError):
        a._validate_container("bad name")


def test_invalid_image_rejected():
    a = DockerHostAdapter(HostSessionConfig(root="x", metadata={"container": "c"}))
    with pytest.raises(DockerHostError):
        a._validate_image("bad image with spaces")


def test_docker_binary_missing(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    with pytest.raises(DockerUnavailableError):
        a._docker_binary()


def test_validate_success(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: _completed(stdout=b'{"ServerVersion":"24.0.0"}\n')
    )
    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    a.validate()


def test_validate_daemon_unavailable(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: _completed(stderr=b"Cannot connect to the Docker daemon. Is the docker daemon running?", returncode=1),
    )
    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    with pytest.raises(DockerDaemonError):
        a.validate()


def test_validate_permission_denied(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: _completed(stderr=b"permission denied while trying to connect", returncode=1),
    )
    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    with pytest.raises(DockerPermissionError):
        a.validate()


def test_validate_timeout(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(mod.subprocess, "run", raise_timeout)
    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    with pytest.raises(DockerTimeoutError):
        a.validate()


def test_validate_malformed_json(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=b"not json\n", returncode=0))
    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    with pytest.raises(DockerResponseError):
        a.validate()


def test_connect_disconnect_health(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=b'{"ServerVersion":"24.0.0"}\n'))
    # adapter already connected via fixture; health should be healthy
    h = adapter.health()
    assert h.state == HealthState.HEALTHY
    adapter.disconnect()
    assert adapter._connected is False
    h2 = adapter.health()
    assert h2.state == HealthState.UNKNOWN


def test_capabilities_truthful(adapter):
    caps = adapter.capabilities()
    assert caps["file_read"] == "supported"
    assert caps["file_system_access"] == "supported"
    assert caps["file_write"] == "requires_auth"
    assert caps["branch_read"] == "unsupported"
    assert caps["deployment"] == "unsupported"


def test_list_containers(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    line = json.dumps({"ID": "abc", "Names": "web", "State": "running"})
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=(line + "\n").encode()))
    res = adapter.list_containers()
    assert isinstance(res, list)
    assert res[0]["Names"] == "web"


def test_list_images(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    line = json.dumps({"ID": "sha256:1", "Repository": "nginx", "Tag": "latest"})
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=(line + "\n").encode()))
    res = adapter.list_images()
    assert res[0]["Repository"] == "nginx"


def test_inspect_container_not_found(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: _completed(stderr=b"Error: No such container: nope", returncode=1)
    )
    with pytest.raises(DockerNotFoundError):
        adapter.inspect_container("nope")


def test_inspect_container_redacts_env(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    payload = json.dumps([{"Id": "c1", "Config": {"Env": ["SECRET=abc", "PATH=/usr/bin"], "Image": "img"}}])
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=payload.encode()))
    res = adapter.inspect_container("mycontainer")
    assert res["Config"]["Env"] == ["<redacted:2 entries>"]


def test_inspect_image(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    payload = json.dumps([{"Id": "sha256:img", "RepoTags": ["nginx:latest"]}])
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=payload.encode()))
    res = adapter.inspect_image("nginx:latest")
    assert res["Id"] == "sha256:img"


def test_server_info(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=b'{"ServerVersion":"24.0.0","SecurityOptions":["name=seccomp"]}\n'))
    info = adapter.server_info()
    assert info["ServerVersion"] == "24.0.0"
    assert info["SecurityOptions"] == ["<redacted>"]


def test_read_file_success(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    tar = _tar_bytes("file.txt", b"hello\n")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=tar, returncode=0))
    res = adapter.read_file("file.txt")
    assert res["text"].strip() == "hello"
    assert res["truncated"] is False
    assert len(res["sha256"]) == 64


def test_read_file_traversal_rejected(adapter):
    with pytest.raises(DockerHostError):
        adapter._normalize_remote_path("/../etc/passwd")
    with pytest.raises(DockerHostError):
        adapter._normalize_remote_path("../../etc/passwd")


def test_read_file_requires_connected(monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    cfg = HostSessionConfig(root="x", metadata={"container": "c"})
    a = DockerHostAdapter(cfg)
    a._parse_config()
    a._connected = False
    with pytest.raises(DockerHostError):
        a.read_file("x")


def test_read_file_not_found(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stderr=b"Error: No such file", returncode=1))
    with pytest.raises(DockerNotFoundError):
        adapter.read_file("missing.txt")


def test_list_directory(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    tar = _tar_bytes("dir/", b"")  # not regular; add a file too
    # build tar with dir entry and file
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        d = tarfile.TarInfo(name="subdir/")
        d.type = tarfile.DIRTYPE
        d.size = 0
        tf.addfile(d)
        f = tarfile.TarInfo(name="file.txt")
        f.size = 4
        tf.addfile(f, io.BytesIO(b"data"))
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=buf.getvalue(), returncode=0))
    res = adapter.list_directory("")
    names = {e["name"] for e in res["entries"]}
    assert "subdir" in names
    assert "file.txt" in names


def test_container_path_traversal_rejected(adapter):
    with pytest.raises(DockerHostError):
        adapter._normalize_remote_path("/../etc/passwd")
    with pytest.raises(DockerHostError):
        adapter._normalize_remote_path("../../etc/passwd")


def test_oversized_output_truncation(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    big = b"x" * (adapter._max_output_bytes + 100)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=big, returncode=0))
    # server_info parses JSON; use raw _run instead via list_containers? list_containers expects JSON lines; use _run directly
    res = adapter._run(["ps"])
    assert len(res.stdout) <= adapter._max_output_bytes


def test_health_unreachable(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: _completed(stderr=b"Cannot connect to the Docker daemon", returncode=1)
    )
    h = adapter.health()
    assert h.state in (HealthState.DEGRADED, HealthState.UNREACHABLE)


def test_manager_integration(tmp_path, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(stdout=b'{"ServerVersion":"24.0.0"}\n'))
    registry = RegistryV2(tmp_path / "reg.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    host = hosts.register_host(uuid4(), "docker", "local", "Docker", "mycontainer:/app")
    manager = HostSessionManager(registry=hosts)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    assert session.state == HostLifecycleState.CONNECTED
    manager.shutdown()
    registry.close()


def test_describe(adapter):
    d = adapter.describe()
    assert d["host_type"] == "docker"
    assert "capabilities" in d
    assert d["metadata"]["read_only"] is True


def test_no_shell_usage(adapter, monkeypatch):
    import ogx.hosts.adapters.docker_runtime as mod

    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return _completed(stdout=b'{"ServerVersion":"24.0.0"}\n')

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    adapter._docker_info()
    assert isinstance(captured["cmd"], list)
    assert captured["cmd"][0].endswith("docker")

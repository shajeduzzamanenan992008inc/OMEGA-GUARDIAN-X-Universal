"""SEC-12 / AI-05: container sandbox isolation, snapshot hygiene and cleanup.

These tests drive the real code path with a mocked Docker client. They prove
which flags OGX passes and how failures are handled; they do not prove that a
Docker daemon enforces them. Live verification is blocked on this machine
(no Docker binary or daemon).
"""

from __future__ import annotations

import subprocess

import pytest

from ogx import sandbox as sandbox_module
from ogx.sandbox import (
    ALLOWED_CPUS,
    ALLOWED_MEMORY,
    _copy_snapshot,
    run_in_sandbox,
    sandbox_plan,
    validate_image_ref,
)

IMAGE = "ghcr.io/example/ogx-sandbox@sha256:" + "a" * 64


class FakeDocker:
    def __init__(self, *, daemon_ok=True, image_present=True, run_result=None, run_exc=None):
        self.daemon_ok = daemon_ok
        self.image_present = image_present
        self.run_result = run_result
        self.run_exc = run_exc
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        assert kwargs.get("shell") in (None, False), "docker must never run through a shell"
        if args[1:3] == ["info", "--format"]:
            code = 0 if self.daemon_ok else 1
            return subprocess.CompletedProcess(args, code, "27.0.0" if code == 0 else "", "daemon down" if code else "")
        if args[1:3] == ["image", "inspect"]:
            code = 0 if self.image_present else 1
            return subprocess.CompletedProcess(args, code, "[]", "no such image" if code else "")
        if args[1:2] == ["rm"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1:2] == ["run"]:
            if self.run_exc is not None:
                raise self.run_exc
            if self.run_result is not None:
                return self.run_result
            return subprocess.CompletedProcess(args, 0, "ok", "")
        raise AssertionError(f"unexpected docker invocation: {args}")

    def run_call(self) -> list[str]:
        return next(call for call in self.calls if call[1:2] == ["run"])

    def rm_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[1:2] == ["rm"]]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "README.md").write_text("# project\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / ".env.production").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    (root / "secrets.json").write_text("{}\n", encoding="utf-8")
    (root / ".npmrc").write_text("//registry:_authToken=secret\n", encoding="utf-8")
    (root / "kubeconfig").write_text("users: []\n", encoding="utf-8")
    (root / "server.pem").write_text("cert\n", encoding="utf-8")
    (root / "client.key").write_text("key\n", encoding="utf-8")
    (root / "docker.sock").write_text("socket\n", encoding="utf-8")
    for directory in (".git", "node_modules", ".ssh", ".aws"):
        target = root / directory
        target.mkdir()
        (target / "secret.txt").write_text("do not copy\n", encoding="utf-8")
    return root


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))
    return fake


def _subprocess_proxy(fake):
    class Proxy:
        CompletedProcess = subprocess.CompletedProcess
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(args, **kwargs):
            return fake(args, **kwargs)

    return Proxy


# ------------------------------------------------------------------ image refs
@pytest.mark.parametrize(
    "image",
    [
        "ubuntu:latest",
        "ghcr.io/example/sandbox",
        "ghcr.io/example/sandbox@sha256:abc",
        "ghcr.io/example/sandbox@sha256:" + "A" * 64,
        "",
        " ghcr.io/example/sandbox@sha256:" + "a" * 64,
    ],
)
def test_mutable_or_malformed_image_references_are_rejected(image):
    with pytest.raises(ValueError):
        validate_image_ref(image)


def test_digest_pinned_image_reference_is_accepted():
    validate_image_ref(IMAGE)
    validate_image_ref("registry.example:5000/team/tool@sha256:" + "0123456789abcdef" * 4)


# --------------------------------------------------------------- fail-closed
def test_missing_docker_refuses_to_execute_project_code(monkeypatch, project):
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: None)
    with pytest.raises(RuntimeError) as excinfo:
        run_in_sandbox(project, IMAGE, ["python", "-m", "pytest"])
    assert "without the configured container sandbox" in str(excinfo.value)


def test_unreachable_daemon_fails_closed_before_any_container_starts(monkeypatch, project):
    fake = FakeDocker(daemon_ok=False)
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))
    with pytest.raises(RuntimeError) as excinfo:
        run_in_sandbox(project, IMAGE, ["python", "-m", "pytest"])
    assert "daemon is not reachable" in str(excinfo.value)
    assert fake.run_call.__self__ is fake  # sanity: same fake
    assert all(call[1:2] != ["run"] for call in fake.calls)


def test_missing_image_never_triggers_an_implicit_pull(monkeypatch, project):
    fake = FakeDocker(image_present=False)
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))
    with pytest.raises(RuntimeError) as excinfo:
        run_in_sandbox(project, IMAGE, ["python", "-m", "pytest"])
    assert "will not download images" in str(excinfo.value)
    assert all("pull" not in call for call in fake.calls)
    assert all(call[1:2] != ["run"] for call in fake.calls)


# ------------------------------------------------------------------- flags
def test_container_is_started_with_every_isolation_control(fake_docker, project):
    result = run_in_sandbox(project, IMAGE, ["python", "-m", "pytest", "-q"], timeout=120)

    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    args = fake_docker.run_call()
    joined = " ".join(args)

    assert args[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == args[args.index("--network"):args.index("--network") + 2]
    assert ["--ipc", "none"] == args[args.index("--ipc"):args.index("--ipc") + 2]
    assert "--read-only" in args
    assert ["--cap-drop", "ALL"] == args[args.index("--cap-drop"):args.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges:true"] == args[
        args.index("--security-opt"):args.index("--security-opt") + 2
    ]
    assert ["--pids-limit", "96"] == args[args.index("--pids-limit"):args.index("--pids-limit") + 2]
    assert ["--memory", "768m"] == args[args.index("--memory"):args.index("--memory") + 2]
    assert ["--memory-swap", "768m"] == args[
        args.index("--memory-swap"):args.index("--memory-swap") + 2
    ]
    assert ["--cpus", "1"] == args[args.index("--cpus"):args.index("--cpus") + 2]
    assert ["--user", "10001:10001"] == args[args.index("--user"):args.index("--user") + 2]
    entrypoint = args.index("--entrypoint")
    assert args[entrypoint + 1] == "python"
    assert args[entrypoint + 2] == IMAGE
    assert args[entrypoint + 3:] == ["-m", "pytest", "-q"]
    assert "noexec" in args[args.index("--tmpfs") + 1]
    assert "nosuid" in args[args.index("--tmpfs") + 1]
    assert "docker.sock" not in joined
    assert "--privileged" not in args
    assert "--volume" not in args and "-v" not in args


def test_environment_is_an_explicit_allowlist(fake_docker, project, monkeypatch):
    monkeypatch.setenv("OGX_GITHUB_TOKEN", "super-secret-token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret-key")
    run_in_sandbox(project, IMAGE, ["true"])
    args = fake_docker.run_call()
    env_values = [args[index + 1] for index, value in enumerate(args) if value == "--env"]
    assert env_values == ["HOME=/tmp", "TMPDIR=/tmp", "PYTHONDONTWRITEBYTECODE=1"]
    assert "super-secret-token" not in " ".join(args)
    assert "super-secret-key" not in " ".join(args)
    assert "--env-file" not in args


def test_snapshot_mount_is_the_disposable_copy_not_the_project(fake_docker, project):
    run_in_sandbox(project, IMAGE, ["true"])
    args = fake_docker.run_call()
    mount = args[args.index("--mount") + 1]
    assert "dst=/workspace" in mount
    assert "rw" in mount.split(",")[-1]
    assert str(project) not in mount
    assert "ogx-sandbox-" in mount


# ------------------------------------------------------------------ snapshot
def test_snapshot_excludes_credentials_vcs_and_dependencies(tmp_path, project):
    destination = tmp_path / "snapshot"
    destination.mkdir()
    summary = _copy_snapshot(project, destination)

    copied = {
        str(path.relative_to(destination)).replace("\\", "/")
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert copied == {"app.py", "README.md"}
    assert summary["files_copied"] == 2
    for forbidden in (
        ".env", ".env.production", "id_rsa", "secrets.json", ".npmrc",
        "kubeconfig", "server.pem", "client.key", "docker.sock",
    ):
        assert forbidden in summary["sensitive_excluded"]
    assert summary["entries_skipped"] >= 8


def test_snapshot_never_follows_symlinks(tmp_path, project):
    outside = tmp_path / "outside.txt"
    outside.write_text("host file\n", encoding="utf-8")
    link_file = project / "link.txt"
    link_dir = project / "linked-dir"
    try:
        link_file.symlink_to(outside)
        link_dir.symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")

    destination = tmp_path / "snapshot"
    destination.mkdir()
    summary = _copy_snapshot(project, destination)

    assert not (destination / "link.txt").exists()
    assert not (destination / "linked-dir").exists()
    assert summary["files_copied"] == 2


def test_snapshot_summary_is_reported_in_the_result(fake_docker, project):
    result = run_in_sandbox(project, IMAGE, ["true"])
    assert result["snapshot"]["files_copied"] == 2
    assert "sensitive_excluded" in result["snapshot"]


# ------------------------------------------------------------- cleanup paths
def test_timeout_force_removes_the_container(monkeypatch, project):
    fake = FakeDocker(run_exc=subprocess.TimeoutExpired(cmd="docker run", timeout=5))
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))

    result = run_in_sandbox(project, IMAGE, ["sleep", "600"], timeout=5)

    assert result["timed_out"] is True
    assert result["exit_code"] == 124
    assert result["cleanup"] == {"container_removed": True}
    name = result["container"]
    assert name.startswith("ogx-sandbox-")
    assert any(call == ["docker", "rm", "-f", name] for call in fake.rm_calls())


def test_cancellation_force_removes_the_container_and_propagates(monkeypatch, project):
    fake = FakeDocker(run_exc=KeyboardInterrupt())
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))

    with pytest.raises(KeyboardInterrupt):
        run_in_sandbox(project, IMAGE, ["sleep", "600"])
    assert any(call[1:2] == ["rm"] for call in fake.calls)


def test_cleanup_failure_does_not_mask_the_result(monkeypatch, project):
    class FailingRmDocker(FakeDocker):
        def __call__(self, args, **kwargs):
            if args[1:2] == ["rm"]:
                self.calls.append(list(args))
                raise subprocess.TimeoutExpired(cmd="docker rm", timeout=30)
            return super().__call__(args, **kwargs)

    fake = FailingRmDocker()
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))

    result = run_in_sandbox(project, IMAGE, ["true"])
    assert result["exit_code"] == 0
    assert result["cleanup"] == {"container_removed": True}


# ------------------------------------------------------------- input bounds
@pytest.mark.parametrize("timeout", [0, -1, 1801, 999999])
def test_timeout_bounds_are_enforced(fake_docker, project, timeout):
    with pytest.raises(ValueError):
        run_in_sandbox(project, IMAGE, ["true"], timeout=timeout)
    assert fake_docker.calls == []


@pytest.mark.parametrize("memory", ["", "10g", "768M", "0"])
def test_memory_allowlist_is_enforced(fake_docker, project, memory):
    with pytest.raises(ValueError):
        run_in_sandbox(project, IMAGE, ["true"], memory=memory)


@pytest.mark.parametrize("cpus", ["0", "8", "", "two"])
def test_cpu_allowlist_is_enforced(fake_docker, project, cpus):
    with pytest.raises(ValueError):
        run_in_sandbox(project, IMAGE, ["true"], cpus=cpus)


@pytest.mark.parametrize(
    "command",
    [[], [""], ["python\x00"], ["python", 3], "python -m pytest"],
)
def test_command_must_be_a_non_empty_argument_array(fake_docker, project, command):
    with pytest.raises(ValueError):
        run_in_sandbox(project, IMAGE, command)


def test_allowed_resource_values_are_the_documented_ones():
    assert ALLOWED_MEMORY == {"384m", "512m", "768m", "1g", "1536m", "2g"}
    assert ALLOWED_CPUS == {"0.5", "1", "1.5", "2"}


def test_output_is_bounded(monkeypatch, project):
    huge = "x" * 100000
    fake = FakeDocker(run_result=subprocess.CompletedProcess(["docker", "run"], 0, huge, huge))
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: "docker")
    monkeypatch.setattr(sandbox_module, "subprocess", _subprocess_proxy(fake))

    result = run_in_sandbox(project, IMAGE, ["true"])
    assert len(result["stdout"]) == sandbox_module.MAX_OUTPUT_CHARS
    assert len(result["stderr"]) == sandbox_module.MAX_OUTPUT_CHARS


# ------------------------------------------------------------------- plan
def test_plan_publishes_the_enforced_policy_and_limitations(monkeypatch):
    monkeypatch.setattr(sandbox_module, "docker_path", lambda: None)
    plan = sandbox_plan()
    assert plan["available"] is False
    policy = plan["default_policy"]
    assert policy["network"] == "none"
    assert policy["container_root"] == "read-only"
    assert policy["no_new_privileges"] is True
    assert policy["docker_socket"] == "never mounted"
    assert "not a formal VM" in " ".join(plan["limitations"])
    assert plan["snapshot_policy"]["symlinks"].startswith("never followed")

"""Local Git host adapter tests: repository scope, reads, safety and recovery."""

from __future__ import annotations

import shutil
import subprocess
from uuid import uuid4

import pytest

from ogx.hosts.adapters.git_runtime import (
    GitHostAdapter,
    GitHostError,
    GitUnavailableError,
)
from ogx.hosts.lifecycle import (
    HostLifecycleState,
    HostSessionConfig,
    HostSessionManager,
)
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, capture_output=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test User")
    (path / "readme.md").write_text("hello\n", encoding="utf-8")
    (path / "unicodé.txt").write_text("unicode\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "initial commit")
    return path


@pytest.fixture
def adapter(repo):
    instance = GitHostAdapter(HostSessionConfig(root=str(repo)))
    instance.connect()
    yield instance
    instance.disconnect()


def test_valid_repository_metadata(adapter):
    metadata = adapter.metadata()
    assert metadata["root"]
    assert len(metadata["head"]) == 40
    assert metadata["dirty"] is False
    assert metadata["remotes"] == {}
    assert metadata["git_version"].startswith("git version")


def test_read_file_and_unicode(adapter):
    assert adapter.read_file("readme.md")["text"].strip() == "hello"
    assert adapter.read_file("unicodé.txt")["text"].strip() == "unicode"


def test_branches_commits_and_tree(adapter):
    branches = adapter.list_branches()
    assert branches and any(item["current"] for item in branches)
    commits = adapter.list_commits(limit=5)
    assert commits[0]["subject"] == "initial commit"
    tree = adapter.list_tree(recursive=True)
    assert {"readme.md", "unicodé.txt"} <= {item["path"] for item in tree}


def test_remotes_are_sanitized(adapter, repo):
    _git(repo, "remote", "add", "origin", "https://user:secret-token@github.com/acme/repo.git")
    remotes = adapter.metadata()["remotes"]
    assert remotes["origin"]["provider"] == "github"
    assert "secret-token" not in remotes["origin"]["url"]
    assert "user@" not in remotes["origin"]["url"]


def test_detached_head(repo):
    _git(repo, "checkout", "-q", "--detach")
    instance = GitHostAdapter(HostSessionConfig(root=str(repo)))
    instance.connect()
    metadata = instance.metadata()
    assert metadata["detached"] is True
    assert metadata["branch"] is None
    assert metadata["head"]


def test_empty_repository_connects_but_is_degraded(tmp_path):
    path = tmp_path / "empty"
    path.mkdir()
    _git(path, "init", "-q")
    instance = GitHostAdapter(HostSessionConfig(root=str(path)))
    instance.connect()
    metadata = instance.metadata()
    assert metadata["head"] is None
    health = instance.health()
    assert health.state.value == "degraded"
    assert any("no commits" in issue for issue in health.issues)
    assert instance.list_commits() == []


def test_non_repository_is_rejected(tmp_path):
    path = tmp_path / "plain"
    path.mkdir()
    instance = GitHostAdapter(HostSessionConfig(root=str(path)))
    with pytest.raises(GitHostError):
        instance.connect()


def test_missing_repository_is_rejected(tmp_path):
    instance = GitHostAdapter(HostSessionConfig(root=str(tmp_path / "missing")))
    with pytest.raises(GitHostError):
        instance.connect()


def test_subdirectory_scope_mismatch(repo):
    sub = repo / "readme.md"
    instance = GitHostAdapter(HostSessionConfig(root=str(sub.parent)))
    # The parent here is the repository root, so instead use a real subdirectory.
    nested = repo / "nested"
    nested.mkdir()
    instance = GitHostAdapter(HostSessionConfig(root=str(nested)))
    with pytest.raises(GitHostError) as excinfo:
        instance.connect()
    assert excinfo.value.code == "repository_scope_mismatch"


@pytest.mark.parametrize("path", ["../readme.md", "/etc/passwd", "-p", "a:b", "a/../../b"])
def test_unsafe_repo_paths_rejected(adapter, path):
    with pytest.raises(GitHostError):
        adapter.read_file(path)


def test_missing_file_and_directory(adapter):
    with pytest.raises(GitHostError):
        adapter.read_file("missing.txt")
    with pytest.raises(GitHostError):
        adapter.list_tree(".")


def test_file_size_limit(adapter, repo):
    (repo / "large.txt").write_text("x" * 500, encoding="utf-8")
    _git(repo, "add", "large.txt")
    _git(repo, "commit", "-q", "-m", "large")
    with pytest.raises(GitHostError) as excinfo:
        adapter.read_file("large.txt", max_bytes=100)
    assert excinfo.value.code == "file_too_large"


def test_disconnected_adapter_refuses_reads(adapter):
    adapter.disconnect()
    with pytest.raises(GitHostError):
        adapter.read_file("readme.md")


def test_git_unavailable_fails_closed(monkeypatch, repo):
    monkeypatch.setattr("ogx.hosts.adapters.git_runtime.git_available", lambda: False)
    instance = GitHostAdapter(HostSessionConfig(root=str(repo)))
    with pytest.raises(GitUnavailableError):
        instance.validate()


def test_timeout_is_bounded(monkeypatch, adapter):
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 0.01)

    monkeypatch.setattr("ogx.hosts.adapters.git_runtime.run_git", _timeout)
    with pytest.raises(GitHostError) as excinfo:
        adapter.read_file("readme.md")
    assert excinfo.value.code == "git_timeout"


def test_capabilities_truthful(adapter):
    capabilities = adapter.capabilities()
    for capability in ("file_read", "branch_read", "commit_read", "repository_metadata_read"):
        assert capabilities[capability] == "supported"
    for capability in ("push", "fetch", "branch_delete", "commit_create", "file_write", "workspace_exec"):
        assert capabilities[capability] == "unsupported"


def test_manager_health_and_recovery(tmp_path, repo):
    registry = RegistryV2(tmp_path / "registry.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    host = hosts.register_host(uuid4(), "local_git", "local", "Git", str(repo))
    manager = HostSessionManager(registry=hosts)
    session = manager.create_session(host.host_id)
    manager.connect(session.session_id)
    assert manager.health(session.session_id)["health"]["state"] == "healthy"

    moved = repo.with_name("repo-moved")
    repo.rename(moved)
    assert manager.health(session.session_id)["health"]["state"] != "healthy"
    assert session.state is HostLifecycleState.DEGRADED

    moved.rename(repo)
    manager.recover(session.session_id)
    assert session.state is HostLifecycleState.CONNECTED
    manager.shutdown()
    registry.close()

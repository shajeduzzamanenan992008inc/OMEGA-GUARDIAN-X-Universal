"""GitHub host adapter: capability truth, bounded reads and approval-gated writes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ogx.hosts.adapters.github_runtime import GitHubHostAdapter, GitHubHostError
from ogx.hosts.lifecycle import HealthState, HostSessionConfig
from ogx.platform.capabilities import Capability, CapabilityState
from ogx.platform.models import FileContent, HealthStatus, PullRequest, TreeEntry


class FakePlatform:
    provider_id = "github"
    credential = "test-token"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.transport = SimpleNamespace(last_correlation_id="corr-1")
        self.capabilities = {
            Capability.REPOSITORY_METADATA: CapabilityState.SUPPORTED,
            Capability.TREE_READ: CapabilityState.SUPPORTED,
            Capability.BRANCH_LIST: CapabilityState.SUPPORTED,
            Capability.COMMIT_LIST: CapabilityState.SUPPORTED,
            Capability.FILE_READ: CapabilityState.SUPPORTED,
            Capability.PULL_REQUEST_CREATE: CapabilityState.SUPPORTED,
        }

    def capability_states(self):
        authenticated = {key: CapabilityState.SUPPORTED for key in self.capabilities}
        return authenticated

    def capability_state(self, capability):
        return self.capability_states().get(capability, CapabilityState.UNSUPPORTED)

    def health(self):
        self.calls.append("health")
        return HealthStatus(status="healthy", latency_ms=12)

    def get_repository(self, repository):
        self.calls.append(f"repo:{repository}")
        return SimpleNamespace(
            to_dict=lambda: {
                "repository_id": "1",
                "full_name": repository,
                "default_branch": "main",
                "visibility": "public",
                "provider": "github",
            }
        )

    def read_file(self, repository, path, ref=None):
        self.calls.append(f"read:{path}")
        return FileContent(
            path=path,
            sha="abc",
            size=11,
            content="print('ok')\n",
            encoding="utf-8",
            truncated=False,
        )

    def read_tree(self, repository, path="", ref=None, recursive=False):
        self.calls.append(f"tree:{path}")
        return (TreeEntry(path=f"{path}/a.py" if path else "a.py", type="blob", sha="abc", size=11),)

    def list_branches(self, repository, limit=100):
        self.calls.append("branches")
        return (SimpleNamespace(to_dict=lambda: {"name": "main", "commit_sha": "abc", "protected": True}),)

    def list_commits(self, repository, branch=None, limit=50):
        self.calls.append("commits")
        return (SimpleNamespace(to_dict=lambda: {"sha": "abc", "message": "init"}),)

    def list_pull_requests(self, repository, state="open", limit=50):
        self.calls.append("pulls")
        return (SimpleNamespace(to_dict=lambda: {"number": 1, "title": "PR"}),)

    def create_pull_request(self, repository, *, title, body, head, base):
        self.calls.append(f"create_pr:{head}->{base}")
        return PullRequest(
            pull_request_id="99",
            number=99,
            title=title,
            state="open",
            source_branch=head,
            target_branch=base,
            url="https://example.invalid/pr/99",
            draft=True,
        )


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    return tmp_path


def _adapter(tmp_path):
    fake = FakePlatform()
    adapter = GitHubHostAdapter(HostSessionConfig(root="acme/widget"), platform_adapter=fake)
    adapter.validate()
    adapter.connect()
    return adapter, fake


def test_connect_probes_the_remote(state):
    adapter, fake = _adapter(state)
    assert "health" in fake.calls
    assert adapter.describe()["connected"] is True


def test_connect_without_a_credential_fails_closed(state):
    fake = FakePlatform()
    fake.credential = None
    adapter = GitHubHostAdapter(HostSessionConfig(root="acme/widget"), platform_adapter=fake)
    adapter.validate()
    with pytest.raises(GitHubHostError) as error:
        adapter.connect()
    assert error.value.code == "credential_required"
    assert adapter.describe()["connected"] is False


def test_invalid_identifier_is_refused(state):
    adapter = GitHubHostAdapter(HostSessionConfig(root="not-a-repository"))
    with pytest.raises(GitHubHostError) as error:
        adapter.validate()
    assert error.value.code == "invalid_identifier"


def test_capabilities_are_truthful(state):
    adapter, _ = _adapter(state)
    capabilities = adapter.capabilities()
    assert capabilities["file_read"] == "supported"
    assert capabilities["repository_metadata_read"] == "supported"
    assert capabilities["pull_request_create"] == "supported"
    for unsupported in ("file_write", "push", "branch_create", "deployment", "testing"):
        assert capabilities[unsupported] == "unsupported"


def test_reads_are_bounded_and_delegated(state):
    adapter, fake = _adapter(state)
    metadata = adapter.metadata()
    assert metadata["full_name"] == "acme/widget"

    file = adapter.read_file("README.md", max_bytes=5)
    assert file["truncated"] is True
    assert len(file["content"].encode("utf-8")) <= 5

    listing = adapter.list_directory("")
    assert listing["entries"][0]["path"] == "a.py"
    assert adapter.branches()[0]["name"] == "main"
    assert adapter.commits()[0]["sha"] == "abc"
    assert adapter.pull_requests()[0]["number"] == 1


def test_unapproved_pull_request_is_refused(state):
    adapter, fake = _adapter(state)
    with pytest.raises(Exception) as error:
        adapter.create_pull_request(
            title="Test", body="body", head="feature/x", base="main", approved=False
        )
    assert type(error.value).__name__ == "ApprovalRequiredError"
    assert not any(call.startswith("create_pr:") for call in fake.calls)


def test_approved_pull_request_is_executed_and_audited(state):
    adapter, fake = _adapter(state)
    result = adapter.create_pull_request(
        title="Test",
        body="body",
        head="feature/x",
        base="main",
        approved=True,
        actor_subject="owner@example.com",
    )
    assert result["ok"] is True
    assert result["result"]["url"].endswith("/99")
    assert "create_pr:feature/x->main" in fake.calls

    audit = (state / "state" / "platform" / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.completed" in audit
    assert "owner@example.com" in audit


def test_protected_head_branch_is_refused(state):
    adapter, _ = _adapter(state)
    with pytest.raises(Exception) as error:
        adapter.create_pull_request(
            title="Test", body="body", head="main", base="develop", approved=True
        )
    assert type(error.value).__name__ == "PermissionDeniedError"


def test_disconnected_adapter_refuses_reads(state):
    adapter, _ = _adapter(state)
    adapter.disconnect()
    with pytest.raises(GitHubHostError) as error:
        adapter.metadata()
    assert error.value.code == "not_connected"


def test_health_failure_is_reported_not_raised(state):
    fake = FakePlatform()
    fake.health = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    adapter = GitHubHostAdapter(HostSessionConfig(root="acme/widget"), platform_adapter=fake)
    report = adapter.health()
    assert report.state is HealthState.UNREACHABLE
    assert "RuntimeError" in report.issues[0]

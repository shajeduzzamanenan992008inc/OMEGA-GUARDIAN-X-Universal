"""Provider-backed Git host adapters: truth, bounds, approvals and factory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ogx.hosts.adapters.provider_runtime import PROVIDER_HOST_TYPES, ProviderGitHostAdapter
from ogx.hosts.adapters.runtime_factory import create_host_adapter
from ogx.hosts.lifecycle import HostSessionConfig, UnsupportedHostTypeError
from ogx.platform.capabilities import Capability, CapabilityState
from ogx.platform.errors import ApprovalRequiredError
from ogx.platform.models import FileContent, HealthStatus, PullRequest, TreeEntry

PROVIDERS = sorted(PROVIDER_HOST_TYPES)


class FakePlatform:
    credential = "test-token"
    credential_env = ("OGX_TEST_TOKEN",)

    def __init__(self, *, provider_id="gitlab", pr_supported=True, credential="test-token") -> None:
        self.provider_id = provider_id
        self.credential = credential
        self.calls: list[str] = []
        self.transport = SimpleNamespace(last_correlation_id="corr-1")
        self._states = {
            Capability.REPOSITORY_METADATA: CapabilityState.SUPPORTED,
            Capability.TREE_READ: CapabilityState.SUPPORTED,
            Capability.BRANCH_LIST: CapabilityState.SUPPORTED,
            Capability.COMMIT_LIST: CapabilityState.SUPPORTED,
            Capability.FILE_READ: CapabilityState.SUPPORTED,
            Capability.PULL_REQUEST_CREATE: (
                CapabilityState.SUPPORTED if pr_supported else CapabilityState.UNSUPPORTED
            ),
        }

    def capability_states(self):
        return dict(self._states)

    def capability_state(self, capability):
        return self._states.get(capability, CapabilityState.UNSUPPORTED)

    def health(self):
        self.calls.append("health")
        return HealthStatus(status="healthy", latency_ms=7)

    def get_repository(self, repository):
        self.calls.append(f"repo:{repository}")
        return SimpleNamespace(
            to_dict=lambda: {
                "repository_id": "1",
                "full_name": repository,
                "provider": self.provider_id,
            }
        )

    def read_file(self, repository, path, ref=None):
        self.calls.append(f"read:{path}")
        return FileContent(
            path=path, sha="abc", size=3, content="ok\n", encoding="utf-8", truncated=False
        )

    def read_tree(self, repository, path="", ref=None, recursive=False):
        self.calls.append(f"tree:{path}")
        return (TreeEntry(path="a.py", type="blob", sha="abc", size=3),)

    def list_branches(self, repository, limit=100):
        self.calls.append("branches")
        return (SimpleNamespace(to_dict=lambda: {"name": "main"}),)

    def list_commits(self, repository, branch=None, limit=50):
        self.calls.append("commits")
        return (SimpleNamespace(to_dict=lambda: {"sha": "abc"}),)

    def list_pull_requests(self, repository, state="open", limit=50):
        self.calls.append("pulls")
        return (SimpleNamespace(to_dict=lambda: {"number": 1}),)

    def create_pull_request(self, repository, *, title, body, head, base):
        self.calls.append(f"create_pr:{head}->{base}")
        return PullRequest(
            pull_request_id="5",
            number=5,
            title=title,
            state="open",
            source_branch=head,
            target_branch=base,
            url="https://example.invalid/mr/5",
        )


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    return tmp_path


def _host(tmp_path, provider="gitlab", root="acme/widget", **fake_kwargs):
    fake = FakePlatform(provider_id=provider, **fake_kwargs)
    adapter = ProviderGitHostAdapter(
        HostSessionConfig(root=root), provider_id=provider, platform_adapter=fake
    )
    adapter.validate()
    adapter.connect()
    return adapter, fake


@pytest.mark.parametrize("provider", PROVIDERS)
def test_connect_probes_each_provider(state, provider):
    root = "widget" if provider == "aws_codecommit" else "acme/widget"
    adapter, fake = _host(state, provider=provider, root=root)
    assert fake.calls == ["health"]
    assert adapter.describe()["connected"] is True
    assert adapter.describe()["host_type"] == provider
    assert adapter.describe()["provider_id"] == provider


@pytest.mark.parametrize("provider", PROVIDERS)
def test_reads_delegate_to_the_provider_adapter(state, provider):
    root = "widget" if provider == "aws_codecommit" else "acme/widget"
    adapter, fake = _host(state, provider=provider, root=root)
    assert adapter.metadata()["full_name"] == root
    assert adapter.read_file("README.md")["content"] == "ok\n"
    assert adapter.list_directory("src")["entries"][0]["path"] == "a.py"
    assert adapter.branches()[0]["name"] == "main"
    assert adapter.commits()[0]["sha"] == "abc"
    assert adapter.pull_requests()[0]["number"] == 1


@pytest.mark.parametrize("provider", PROVIDERS)
def test_capabilities_are_truthful(state, provider):
    root = "widget" if provider == "aws_codecommit" else "acme/widget"
    adapter, _ = _host(state, provider=provider, root=root)
    capabilities = adapter.capabilities()
    assert capabilities["file_read"] == "supported"
    assert capabilities["pull_request_create"] == "supported"
    for unsupported in ("file_write", "push", "branch_create", "commit_create", "deployment"):
        assert capabilities[unsupported] == "unsupported"


def test_capabilities_do_not_advertise_an_unimplemented_write(state):
    adapter, _ = _host(state, provider="aws_codecommit", root="widget", pr_supported=False)
    assert adapter.capabilities()["pull_request_create"] == "unsupported"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_unapproved_pull_request_is_refused(state, provider):
    root = "widget" if provider == "aws_codecommit" else "acme/widget"
    adapter, fake = _host(state, provider=provider, root=root)
    with pytest.raises(ApprovalRequiredError):
        adapter.create_pull_request(
            title="Fix", body="Body", head="feature", base="main", approved=False
        )
    assert not any(call.startswith("create_pr") for call in fake.calls)


@pytest.mark.parametrize("provider", ["gitlab", "gitea"])
def test_approved_pull_request_executes_and_is_audited(state, provider):
    adapter, fake = _host(state, provider=provider)
    result = adapter.create_pull_request(
        title="Fix", body="Body", head="feature", base="main", approved=True
    )
    assert result["ok"] is True
    assert result["result"]["number"] == 5
    assert "create_pr:feature->main" in fake.calls
    audit = (state / "state" / "platform" / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.completed" in audit


@pytest.mark.parametrize("provider", PROVIDERS)
def test_connect_without_a_credential_fails_closed(state, provider):
    root = "widget" if provider == "aws_codecommit" else "acme/widget"
    fake = FakePlatform(provider_id=provider, credential=None)
    adapter = ProviderGitHostAdapter(
        HostSessionConfig(root=root), provider_id=provider, platform_adapter=fake
    )
    adapter.validate()
    with pytest.raises(Exception) as error:
        adapter.connect()
    assert getattr(error.value, "code", "") == "credential_required"
    assert adapter.describe()["connected"] is False


@pytest.mark.parametrize(
    "provider,root",
    [
        ("gitlab", "not-a-repository"),
        ("bitbucket", "../escape"),
        ("gitea", "owner/"),
        ("forgejo", "owner/repo/extra/trailing"),
        ("azure_devops", "justproject"),
        ("aws_codecommit", "owner/repo"),
    ],
)
def test_invalid_identifiers_are_refused(state, provider, root):
    adapter = ProviderGitHostAdapter(
        HostSessionConfig(root=root), provider_id=provider, platform_adapter=FakePlatform()
    )
    with pytest.raises(Exception) as error:
        adapter.validate()
    assert getattr(error.value, "code", "") == "invalid_identifier"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_factory_builds_a_host_for_each_provider(provider):
    root = "widget" if provider == "aws_codecommit" else "acme/widget"
    adapter = create_host_adapter(provider, HostSessionConfig(root=root))
    assert isinstance(adapter, ProviderGitHostAdapter)
    assert adapter.host_type == provider


def test_factory_still_rejects_unsupported_host_types():
    with pytest.raises(UnsupportedHostTypeError):
        create_host_adapter("generic_git", HostSessionConfig(root="acme/widget"))


def test_real_adapter_refuses_to_connect_without_env_credentials(monkeypatch, tmp_path):
    for name in (
        "OGX_GITLAB_TOKEN",
        "OGX_BITBUCKET_TOKEN",
        "OGX_GITEA_TOKEN",
        "OGX_FORGEJO_TOKEN",
        "OGX_AZURE_DEVOPS_TOKEN",
        "OGX_AWS_ACCESS_KEY_ID",
        "OGX_AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))

    adapter = create_host_adapter("gitlab", HostSessionConfig(root="acme/widget"))
    adapter.validate()  # validates the API base without contacting the network
    with pytest.raises(Exception) as error:
        adapter.connect()
    assert getattr(error.value, "code", "") == "credential_required"

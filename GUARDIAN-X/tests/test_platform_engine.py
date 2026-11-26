"""Deterministic, credential-free tests for the universal provider engine.

Every provider response is supplied by a local fake transport, so the suite
never performs network I/O and never needs real tokens.
"""

from __future__ import annotations

import base64
import json
import uuid

from urllib.parse import urlparse

import pytest

from ogx import registry as registry_module
from ogx.platform import (
    UNAUTHENTICATED_CAPABILITIES,
    Capability,
    CapabilityState,
    ProviderKind,
    get_provider_registry,
)
from ogx.platform.adapters import (
    AzureDevOpsAdapter,
    BitbucketAdapter,
    ForgejoAdapter,
    GiteaAdapter,
    GitHubAdapter,
    GitLabAdapter,
    CodeCommitAdapter,
    SourceHutAdapter,
)
from ogx.platform.errors import (
    AuthenticationError,
    CapabilityUnavailableError,
    InvalidEndpointError,
    MalformedResponseError,
    PermissionDeniedError,
    ResponseTooLargeError,
    TransportError,
)
from ogx.platform.identity import link_repository_to_project
from ogx.platform.operations import execute_pull_request, plan_pull_request
from ogx.platform.security import redact, validate_api_base
from ogx.platform.transport import HttpTransport, HttpResponse

DuplicateEntityError = getattr(registry_module, "DuplicateEntityError", Exception)


# --------------------------------------------------------------------- helpers
def response(status: int = 200, payload=None, headers=None, body: bytes | None = None) -> HttpResponse:
    if body is None:
        body = json.dumps({} if payload is None else payload).encode()
    return HttpResponse(status=status, headers=headers or {}, body=body, url="")


class Recorder:
    """Fake request function that records calls and replays canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def adapter(cls, responses, credential="test-token", **kwargs):
    recorder = Recorder(responses)
    instance = cls(
        request_fn=recorder,
        credential=credential,
        max_retries=0,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return instance, recorder


REPO = {
    "id": 42,
    "name": "demo",
    "full_name": "acme/demo",
    "html_url": "https://github.com/acme/demo",
    "clone_url": "https://github.com/acme/demo.git",
    "ssh_url": "git@github.com:acme/demo.git",
    "description": "Demo repo",
    "default_branch": "main",
    "private": False,
    "archived": False,
    "fork": False,
    "language": "Python",
    "stargazers_count": 7,
    "forks_count": 3,
    "open_issues_count": 2,
    "size": 128,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-02-01T00:00:00Z",
    "pushed_at": "2024-02-02T00:00:00Z",
    "owner": {"login": "acme", "id": 1},
}


# ------------------------------------------------------------------ capability
def test_registry_exposes_tier1_providers():
    registry = get_provider_registry()
    ids = set(registry.provider_ids())
    assert {"github", "gitlab", "bitbucket", "gitea", "forgejo"} <= ids
    assert {"azure_devops", "sourcehut", "aws_codecommit"} <= ids


def test_descriptor_only_providers_claim_no_domain_capabilities():
    registry = get_provider_registry()
    for descriptor in registry.descriptors():
        if descriptor.implementation_status != "descriptor_only":
            continue
        states = {
            descriptor.capability_state(cap, authenticated=False)
            for cap in descriptor.capabilities
            if cap not in UNAUTHENTICATED_CAPABILITIES
        }
        assert CapabilityState.SUPPORTED not in states, descriptor.provider_id


def test_supported_capabilities_are_backed_by_overrides():
    from ogx.platform.base import CAPABILITY_METHODS, ProviderAdapter

    registry = get_provider_registry()
    classes = registry.classes()
    for descriptor in registry.descriptors():
        if descriptor.implementation_status != "verified":
            continue
        cls = classes[descriptor.provider_id]
        for capability, method in CAPABILITY_METHODS.items():
            if capability in UNAUTHENTICATED_CAPABILITIES:
                continue
            if descriptor.capability_state(capability, authenticated=False) is CapabilityState.SUPPORTED:
                assert getattr(cls, method) is not getattr(ProviderAdapter, method), (
                    descriptor.provider_id,
                    capability.value,
                )


def test_authentication_is_required_without_a_credential():
    instance = GitHubAdapter(environ={}, credential=None)
    assert instance.credential is None
    assert instance.capability_state(Capability.REPOSITORY_METADATA) is CapabilityState.REQUIRES_AUTH
    # Descriptors that need no credential stay supported.
    assert instance.capability_state(Capability.IDENTIFY) is CapabilityState.SUPPORTED


def test_credential_is_never_reused_across_providers():
    instance = GitLabAdapter(environ={"OGX_GITHUB_TOKEN": "ghp_secret"}, credential=None)
    assert instance.credential is None
    assert instance.capability_state(Capability.REPOSITORY_METADATA) is CapabilityState.REQUIRES_AUTH


# ---------------------------------------------------------------------- github
def test_github_repository_normalization():
    instance, recorder = adapter(GitHubAdapter, [response(payload=REPO)])
    repo = instance.get_repository("acme/demo")
    assert repo.provider == "github"
    assert repo.full_name == "acme/demo"
    assert repo.default_branch == "main"
    assert repo.visibility == "public"
    assert repo.stars == 7
    assert recorder.calls[0]["url"].endswith("/repos/acme/demo")
    assert recorder.calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert repo.to_dict()["provider"] == "github"


def test_github_branch_and_commit_normalization():
    branches = [{"name": "main", "commit": {"sha": "abc123", "url": "u"}, "protected": True}]
    commit = {
        "sha": "abc123",
        "commit": {
            "message": "Initial",
            "author": {"name": "Ann", "email": "ann@example.com", "date": "2024-01-01T00:00:00Z"},
        },
        "author": {"login": "ann"},
        "parents": [{"sha": "root"}],
    }
    instance, _ = adapter(
        GitHubAdapter,
        [response(payload=branches), response(payload=commit), response(payload=[commit])],
    )
    items = instance.list_branches("acme/demo")
    assert items[0].name == "main" and items[0].protected is True
    assert items[0].commit_sha == "abc123"
    single = instance.get_commit("acme/demo", "abc123")
    assert single.sha == "abc123"
    assert single.author == "Ann"
    listed = instance.list_commits("acme/demo", limit=1)
    assert len(listed) == 1 and listed[0].parents == ("root",)


def test_github_file_and_tree_normalization():
    content = base64.b64encode(b"print('hi')\n").decode()
    file_payload = {
        "type": "file",
        "encoding": "base64",
        "size": 12,
        "name": "app.py",
        "path": "src/app.py",
        "content": content,
        "sha": "deadbeef",
        "html_url": "https://github.com/acme/demo/blob/main/src/app.py",
    }
    tree_payload = {
        "sha": "tree-sha",
        "tree": [{"path": "src/app.py", "mode": "100644", "type": "blob", "sha": "deadbeef", "size": 12}],
        "truncated": False,
    }
    instance, recorder = adapter(GitHubAdapter, [response(payload=file_payload), response(payload=tree_payload)])
    blob = instance.read_file("acme/demo", "src/app.py")
    assert blob.content == "print('hi')\n"
    assert blob.encoding == "utf-8"
    assert recorder.calls[0]["url"].endswith("/contents/src/app.py")
    tree = instance.read_tree("acme/demo", ref="main")
    assert tree[0].path == "src/app.py"
    assert tree[0].type == "blob"


def test_github_issues_filter_out_pull_requests():
    payload = [
        {"number": 1, "title": "Bug", "state": "open", "user": {"login": "ann"}, "labels": [{"name": "bug"}], "body": "b"},
        {"number": 2, "title": "PR", "state": "open", "pull_request": {"url": "x"}, "user": {"login": "bob"}},
    ]
    instance, _ = adapter(GitHubAdapter, [response(payload=payload)])
    issues = instance.list_issues("acme/demo")
    assert [issue.number for issue in issues] == [1]
    assert issues[0].labels == ("bug",)


def test_github_permissions_and_rate_limit():
    instance, _ = adapter(
        GitHubAdapter,
        [
            response(payload={"permissions": {"admin": False, "push": True, "pull": True}}),
            response(payload={"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 1700000000}}}),
        ],
    )
    perms = instance.get_permissions("acme/demo")
    assert perms.push is True and perms.admin is False
    limit = instance.rate_limit()
    assert limit.limit == 5000 and limit.remaining == 4999
    assert limit.reset_at is not None


def test_github_follows_link_header_pagination():
    page_two = [{"name": "dev", "commit": {"sha": "def"}}]
    headers = {"Link": '<https://api.github.com/repositories/42/branches?page=2>; rel="next"'}
    instance, recorder = adapter(GitHubAdapter, [response(payload=[{"name": "main"}], headers=headers), response(payload=page_two)])
    branches = instance.list_branches("acme/demo")
    assert [b.name for b in branches] == ["main", "dev"]
    assert recorder.calls[1]["url"].endswith("page=2")


def test_pagination_to_foreign_host_is_rejected():
    headers = {"Link": '<https://evil.example/steal>; rel="next"'}
    instance, _ = adapter(GitHubAdapter, [response(payload=[{"name": "main"}], headers=headers)])
    with pytest.raises(InvalidEndpointError):
        instance.list_branches("acme/demo")


# ---------------------------------------------------------------------- errors
def test_authentication_failure_is_normalized_and_redacted():
    instance, _ = adapter(GitHubAdapter, [response(status=401, payload={"message": "Bad credentials for ghp_test"})])
    with pytest.raises(AuthenticationError) as excinfo:
        instance.get_repository("acme/demo")
    assert "test-token" not in str(excinfo.value)
    assert excinfo.value.kind == "authentication_failed"
    assert excinfo.value.status == 401


def test_permission_failure_is_normalized():
    instance, _ = adapter(GitHubAdapter, [response(status=403, payload={"message": "Forbidden"})])
    with pytest.raises(PermissionDeniedError):
        instance.get_repository("acme/demo")


def test_malformed_json_is_normalized():
    instance, _ = adapter(GitHubAdapter, [response(body=b"<html>nope</html>")])
    with pytest.raises(MalformedResponseError):
        instance.get_repository("acme/demo")


def test_oversized_response_is_rejected():
    body = json.dumps({"padding": "x" * 4000}).encode()
    instance = GitHubAdapter(
        transport=HttpTransport(request_fn=Recorder([response(body=body)]), max_response_bytes=1024),
        credential="test-token",
    )
    with pytest.raises(ResponseTooLargeError):
        instance.get_repository("acme/demo")


def test_transport_timeout_is_normalized():
    instance, _ = adapter(GitHubAdapter, [TimeoutError("timed out")])
    with pytest.raises(TransportError):
        instance.get_repository("acme/demo")


def test_rate_limit_is_retried_with_backoff():
    slept: list[float] = []
    recorder = Recorder([
        response(status=429, payload={"message": "slow down"}, headers={"Retry-After": "2"}),
        response(payload=REPO),
    ])
    instance = GitHubAdapter(request_fn=recorder, credential="t", max_retries=2, sleep=slept.append)
    repo = instance.get_repository("acme/demo")
    assert repo.full_name == "acme/demo"
    assert slept and slept[0] >= 2.0


def test_unsafe_writes_are_never_retried():
    recorder = Recorder([
        response(status=500, payload={"message": "boom"}),
        response(payload={"number": 1}),
    ])
    instance = GitHubAdapter(request_fn=recorder, credential="t", max_retries=3, sleep=lambda _s: None)
    with pytest.raises(Exception):
        instance.create_pull_request("acme/demo", title="T", body="", head="h", base="b")
    assert len(recorder.calls) == 1


# --------------------------------------------------------------------- gitlab
def test_gitlab_repository_and_merge_request_normalization():
    project = {
        "id": 9,
        "name": "demo",
        "path_with_namespace": "acme/demo",
        "web_url": "https://gitlab.com/acme/demo",
        "http_url_to_repo": "https://gitlab.com/acme/demo.git",
        "ssh_url_to_repo": "git@gitlab.com:acme/demo.git",
        "description": "d",
        "default_branch": "main",
        "visibility": "internal",
        "archived": False,
        "star_count": 2,
        "forks_count": 1,
        "open_issues_count": 0,
    }
    mr = {
        "iid": 5,
        "title": "Feature",
        "state": "merged",
        "source_branch": "feat",
        "target_branch": "main",
        "web_url": "https://gitlab.com/acme/demo/-/merge_requests/5",
        "author": {"username": "ann"},
        "description": "body",
        "merged_at": "2024-03-01T00:00:00Z",
    }
    instance, recorder = adapter(GitLabAdapter, [response(payload=project), response(payload=[mr])])
    repo = instance.get_repository("acme/demo")
    assert repo.visibility == "internal"
    assert "%2F" in recorder.calls[0]["url"] or "acme%2Fdemo" in recorder.calls[0]["url"]
    pulls = instance.list_pull_requests("acme/demo")
    assert pulls[0].number == 5 and pulls[0].state == "merged"


def test_gitlab_uses_private_token_header():
    instance, recorder = adapter(GitLabAdapter, [response(payload=[])])
    instance.list_branches("acme/demo")
    assert recorder.calls[0]["headers"]["PRIVATE-TOKEN"] == "test-token"


# ------------------------------------------------------------------- bitbucket
def test_bitbucket_repository_normalization_and_basic_auth():
    payload = {
        "uuid": "{abc}",
        "name": "demo",
        "full_name": "acme/demo",
        "links": {"html": {"href": "https://bitbucket.org/acme/demo"}},
        "description": "d",
        "mainbranch": {"name": "main"},
        "is_private": True,
        "updated_on": "2024-01-01T00:00:00Z",
        "size": 10,
        "language": "python",
    }
    instance, recorder = adapter(BitbucketAdapter, [response(payload=payload)], credential="user:app-password")
    repo = instance.get_repository("acme/demo")
    assert repo.visibility == "private"
    assert repo.default_branch == "main"
    assert recorder.calls[0]["headers"]["Authorization"].startswith("Basic ")


# ----------------------------------------------------------------------- gitea
def test_gitea_and_forgejo_identity_and_auth_header():
    payload = {
        "id": 3,
        "name": "demo",
        "full_name": "acme/demo",
        "html_url": "https://gitea.com/acme/demo",
        "clone_url": "https://gitea.com/acme/demo.git",
        "ssh_url": "git@gitea.com:acme/demo.git",
        "description": "",
        "default_branch": "main",
        "private": False,
        "archived": False,
        "fork": False,
        "stars_count": 1,
        "forks_count": 0,
        "open_issues_count": 0,
    }
    gitea, recorder = adapter(GiteaAdapter, [response(payload=payload)])
    assert gitea.provider_id == "gitea"
    gitea.get_repository("acme/demo")
    assert recorder.calls[0]["headers"]["Authorization"].startswith("token ")

    forgejo = ForgejoAdapter(environ={}, credential="t")
    assert forgejo.provider_id == "forgejo"
    assert urlparse(forgejo.api_base).hostname == "codeberg.org"


def test_self_hosted_gitea_base_is_configurable():
    instance = GiteaAdapter(environ={}, credential="t", api_base="https://git.example.com/api/v1/")
    assert instance.api_base == "https://git.example.com/api/v1"


# ------------------------------------------------------- azure / codecommit / sr
def test_azure_devops_repository_normalization():
    payload = {
        "id": "guid",
        "name": "demo",
        "webUrl": "https://dev.azure.com/org/project/_git/demo",
        "sshUrl": "git@ssh.dev.azure.com:v3/org/project/demo",
        "remoteUrl": "https://org@dev.azure.com/org/project/_git/demo",
        "defaultBranch": "refs/heads/main",
        "isDisabled": False,
        "size": 99,
        "project": {"name": "project"},
    }
    instance, recorder = adapter(AzureDevOpsAdapter, [response(payload=payload)], organization="org")
    repo = instance.get_repository("project/demo")
    assert repo.default_branch == "main"
    assert repo.visibility == "private"
    assert recorder.calls[0]["headers"]["Authorization"].startswith("Basic ")


def test_aws_codecommit_sigv4_request_shape():
    payload = {
        "repositoryMetadata": {
            "repositoryName": "demo",
            "repositoryId": "id",
            "cloneUrlHttp": "https://git-codecommit.us-east-1.amazonaws.com/v1/repos/demo",
            "defaultBranch": "main",
            "repositoryDescription": "d",
        }
    }
    recorder = Recorder([response(payload=payload)])
    instance = CodeCommitAdapter(
        request_fn=recorder,
        access_key="AKIDEXAMPLE",
        secret_key="secret",
        region="us-east-1",
        environ={},
    )
    repo = instance.get_repository("demo")
    assert repo.name == "demo"
    headers = recorder.calls[0]["headers"]
    assert headers["X-Amz-Target"] == "CodeCommit_20150413.GetRepository"
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "Signature=" in headers["Authorization"]


def test_sourcehut_reports_unsupported_instead_of_faking():
    instance = SourceHutAdapter(environ={}, credential="t")
    assert instance.capability_state(Capability.REPOSITORY_METADATA) is CapabilityState.UNSUPPORTED
    with pytest.raises(CapabilityUnavailableError):
        instance.get_repository("~user/repo")


# ------------------------------------------------------------------- security
@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com",
        "https://user:pass@api.github.com",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal",
        "ftp://api.github.com",
        "https://10.0.0.5",
        "https://api.github.com/?token=abc",
        "api.github.com",
    ],
)
def test_insecure_api_bases_are_rejected(url):
    with pytest.raises(InvalidEndpointError):
        validate_api_base(url)


def test_loopback_http_requires_explicit_opt_in():
    with pytest.raises(InvalidEndpointError):
        validate_api_base("http://127.0.0.1:3000/api/v1")
    assert validate_api_base("http://127.0.0.1:3000/api/v1", allow_loopback_http=True).startswith("http://")


def test_private_network_requires_explicit_opt_in():
    with pytest.raises(InvalidEndpointError):
        validate_api_base("https://192.168.1.10/api")
    assert validate_api_base("https://192.168.1.10/api", allow_private_network=True).startswith("https://")


def test_secrets_are_redacted_from_error_bodies():
    text = "failed with ghp_abcdefghijklmnopqrstuvwxyz012345 and Authorization: Bearer abc.def"
    redacted = redact(text)
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in redacted
    assert "Bearer abc.def" not in redacted
    assert "redacted" in redacted.lower()


def test_errors_never_include_the_credential():
    instance, _ = adapter(GitHubAdapter, [response(status=403, payload={"message": "denied"})])
    with pytest.raises(PermissionDeniedError) as excinfo:
        instance.get_repository("acme/demo")
    assert "test-token" not in json.dumps(excinfo.value.to_dict())


# -------------------------------------------------------------------- identity
@pytest.fixture()
def registry(tmp_path):
    instance = registry_module.RegistryV2(tmp_path / "registry.db")
    instance.initialize()
    return instance


def test_link_repository_is_idempotent_and_preserves_project_identity(registry):
    project = registry.register_project(uuid.uuid4(), "demo")
    before = project.identity_hash
    first = link_repository_to_project(
        project.project_id,
        provider_id="github",
        repository_url="https://github.com/acme/demo.git",
        registry=registry,
    )
    second = link_repository_to_project(
        project.project_id,
        provider_id="github",
        repository_url="https://github.com/acme/demo",
        registry=registry,
    )
    assert first.repository_id == second.repository_id
    assert registry.get_project(project.project_id).identity_hash == before


def test_duplicate_repository_ownership_is_prevented(registry):
    owner = registry.register_project(uuid.uuid4(), "owner")
    other = registry.register_project(uuid.uuid4(), "other")
    link_repository_to_project(
        owner.project_id,
        provider_id="github",
        repository_url="https://github.com/acme/demo",
        registry=registry,
    )
    with pytest.raises(DuplicateEntityError):
        link_repository_to_project(
            other.project_id,
            provider_id="github",
            repository_url="https://github.com/acme/demo",
            registry=registry,
        )
    assert len(registry.list_repositories(project_id=other.project_id)) == 0


# ------------------------------------------------------------------ operations
def test_write_plan_rejects_protected_target():
    instance, _ = adapter(GitHubAdapter, [])
    with pytest.raises(Exception):
        plan_pull_request(instance, "acme/demo", title="T", body="B", head="main", base="main")


def test_pull_request_requires_explicit_approval():
    instance, recorder = adapter(GitHubAdapter, [response(payload={"number": 1})])
    plan = plan_pull_request(instance, "acme/demo", title="T", body="B", head="feat", base="main")
    with pytest.raises(Exception):
        execute_pull_request(plan, instance, approved=False)
    assert recorder.calls == []


def test_approved_pull_request_executes_and_audits(tmp_path):
    payload = {"number": 12, "html_url": "https://github.com/acme/demo/pull/12", "state": "open"}
    instance, recorder = adapter(GitHubAdapter, [response(status=201, payload=payload)])
    plan = plan_pull_request(instance, "acme/demo", title="T", body="B", head="feat", base="main")
    audit = tmp_path / "audit.jsonl"
    result = execute_pull_request(plan, instance, approved=True, actor_subject="tester", audit_path=audit)
    assert result.ok is True
    assert recorder.calls[0]["method"] == "POST"
    text = audit.read_text(encoding="utf-8")
    assert "platform.write.requested" in text and "platform.write.completed" in text


# ------------------------------------------------------------------------ cli
def test_cli_keeps_legacy_commands_and_adds_new_ones():
    from ogx.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["host", "list"]).func.__name__ == "cmd_host_list"
    assert parser.parse_args(["remote", "discover", "--provider", "github"]).func.__name__ == "cmd_remote_discover"
    assert parser.parse_args(["providers", "list"]).func.__name__ == "cmd_providers_list"
    assert parser.parse_args(["providers", "capabilities", "github"]).func.__name__ == "cmd_providers_capabilities"
    assert parser.parse_args(["platform", "status"]).func.__name__ == "cmd_platform"


def test_cli_providers_list_json(capsys):
    from ogx.cli import main

    assert main(["providers", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {row["provider_id"] for row in payload["providers"]}
    assert {"github", "gitlab", "bitbucket", "gitea", "forgejo"} <= ids
    assert payload["count"] == len(payload["providers"])


def test_cli_capability_matrix_is_machine_readable(capsys):
    from ogx.cli import main

    assert main(["platform", "capabilities"]) == 0
    matrix = json.loads(capsys.readouterr().out)
    providers = {row["provider_id"]: row for row in matrix["providers"]}
    assert "github" in providers
    github = providers["github"]
    assert github["implementation_status"] == "verified"
    # The unauthenticated matrix must never advertise credentialed work as ready.
    assert "identify" in github["supported_operations"]
    assert "repository_metadata" not in github["supported_operations"]


def test_legacy_hosts_package_reexports_the_canonical_adapters():
    from ogx.hosts import GitHubProvider

    assert GitHubProvider is GitHubAdapter

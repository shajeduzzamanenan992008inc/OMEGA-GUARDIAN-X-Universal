"""GitHub branch/file/commit/push writes: bounds, stale checks and approvals.

All provider traffic is supplied by a local recorder so nothing here performs
network I/O or needs a token.
"""

from __future__ import annotations

import base64
import json

import pytest

from ogx.hosts.adapters.github_runtime import GitHubHostAdapter
from ogx.hosts.lifecycle import HostSessionConfig
from ogx.platform.adapters import GitHubAdapter, GitLabAdapter
from ogx.platform.capabilities import Capability, CapabilityState
from ogx.platform.errors import (
    ApprovalRequiredError,
    CapabilityUnavailableError,
    ConflictError,
    PermissionDeniedError,
)
from ogx.platform.models import FileChange
from ogx.platform.operations import (
    execute_branch_create,
    execute_file_commit,
    execute_file_update,
    execute_ref_update,
    plan_branch_create,
    plan_file_commit,
    plan_file_update,
    plan_ref_update,
)
from ogx.platform.transport import HttpResponse
from tests._helpers import _commit_responses_one_blob

BASE = "a" * 40
OTHER = "b" * 40
TREE = "c" * 40
BLOB_OLD = "d" * 40
REPO = "acme/demo"


def response(status: int = 200, payload=None, body: bytes | None = None) -> HttpResponse:
    if body is None:
        body = json.dumps({} if payload is None else payload).encode()
    return HttpResponse(status=status, headers={}, body=body, url="")


def ref(sha: str) -> HttpResponse:
    return response(payload={"ref": "refs/heads/main", "object": {"sha": sha}})



def missing() -> HttpResponse:
    return response(status=404, payload={"message": "Not Found"})


def response(status: int = 200, payload=None, body: bytes | None = None) -> HttpResponse:
    if body is None:
        body = json.dumps({} if payload is None else payload).encode()
    return HttpResponse(status=status, headers={}, body=body, url="")


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def adapter(responses, *, credential="test-token", **kwargs):
    recorder = Recorder(responses)
    instance = GitHubAdapter(
        request_fn=recorder,
        credential=credential,
        max_retries=0,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return instance, recorder


def ref(sha: str) -> HttpResponse:
    return response(payload={"ref": "refs/heads/main", "object": {"sha": sha}})



def missing() -> HttpResponse:
    return response(status=404, payload={"message": "Not Found"})



def _commit_responses(existing=None, blob_shas=("1" * 40, "2" * 40), new_commit="9" * 40):



    tree_entries = [{"path": path, "type": "blob", "sha": sha} for path, sha in (existing or {}).items()]
    return [
        ref(BASE),
        response(payload={"sha": BASE, "tree": {"sha": TREE}}),
        response(payload={"sha": TREE, "tree": tree_entries, "truncated": False}),
        *[response(status=201, payload={"sha": sha}) for sha in blob_shas],
        response(status=201, payload={"sha": "8" * 40}),
        response(status=201, payload={"sha": new_commit}),
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]


def _write_update_responses(new_commit="7" * 40, blob_sha="6" * 40):
    return [
        missing(),
        response(status=201, payload={"commit": {"sha": new_commit}, "content": {"sha": blob_sha}}),
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]


def response(status: int = 200, payload=None, body: bytes | None = None) -> HttpResponse:
    if body is None:
        body = json.dumps({} if payload is None else payload).encode()
    return HttpResponse(status=status, headers={}, body=body, url="")




# --------------------------------------------------------------------- branches
def test_create_branch_from_base_branch():
    instance, recorder = adapter(
        [
            ref(BASE),
            missing(),
            response(status=201, payload={"ref": "refs/heads/feat/x", "object": {"sha": BASE}}),
        ]
    )
    branch = instance.create_branch(REPO, branch="feat/x", from_branch="main")
    assert branch.name == "feat/x" and branch.commit_sha == BASE
    assert [call["method"] for call in recorder.calls] == ["GET", "GET", "POST"]
    body = json.loads(recorder.calls[-1]["body"])
    assert body == {"ref": "refs/heads/feat/x", "sha": BASE}


@pytest.mark.parametrize(
    "name",
    ["", "..", "feat//x", "feat/../x", "-bad", "x.lock", "a" * 200, "feat x", "@{upstream}"],
)
def test_create_branch_rejects_invalid_names(name):
    instance, recorder = adapter([])
    with pytest.raises(ValueError):
        instance.create_branch(REPO, branch=name, from_branch="main")
    assert recorder.calls == []


def test_create_branch_reports_existing_branch():
    instance, _ = adapter([ref(BASE), ref(BASE)])
    with pytest.raises(ConflictError):
        instance.create_branch(REPO, branch="feat/x", from_branch="main")


def test_create_branch_requires_capability_without_credential():
    instance, recorder = adapter([], credential=None)
    with pytest.raises(CapabilityUnavailableError):
        instance.create_branch(REPO, branch="feat/x", from_branch="main")
    assert recorder.calls == []


# --------------------------------------------------------------------- file write
def test_write_file_creates_a_new_file():
    encoded = base64.b64encode(b"new\n").decode()
    instance, recorder = adapter(
        [
            missing(),
            response(status=201, payload={"commit": {"sha": "e" * 40}, "content": {"sha": "f" * 40}}),
        ]
    )
    result = instance.write_file(
        REPO, path="src/new.py", content="new\n", message="Add file", branch="feat/x"
    )
    assert result["commit_sha"] == "e" * 40
    body = json.loads(recorder.calls[-1]["body"])
    assert body["content"] == encoded
    assert body["branch"] == "feat/x"
    assert "sha" not in body


def test_write_file_refuses_unintended_overwrite():
    instance, recorder = adapter(
        [
            response(
                payload={
                    "path": "README.md",
                    "sha": BLOB_OLD,
                    "size": 3,
                    "encoding": "base64",
                    "content": base64.b64encode(b"old").decode(),
                }
            )
        ]
    )
    with pytest.raises(ConflictError):
        instance.write_file(
            REPO, path="README.md", content="new", message="M", branch="feat/x"
        )
    assert len(recorder.calls) == 1


def test_write_file_rejects_a_stale_blob_sha():
    instance, _ = adapter(
        [
            response(
                payload={
                    "path": "README.md",
                    "sha": BLOB_OLD,
                    "size": 3,
                    "encoding": "base64",
                    "content": base64.b64encode(b"old").decode(),
                }
            )
        ]
    )
    with pytest.raises(ConflictError):
        instance.write_file(
            REPO,
            path="README.md",
            content="new",
            message="M",
            branch="feat/x",
            expected_sha="1" * 40,
        )


@pytest.mark.parametrize(
    "path",
    ["../escape.txt", "/etc/passwd", "C:/windows/x", "a//b", "a/./b", "a/../b", ".git/config", "a\\b", "a\x00b"],
)
def test_write_paths_reject_traversal(path):
    instance, recorder = adapter([])
    with pytest.raises(ValueError):
        instance.write_file(REPO, path=path, content="x", message="M", branch="feat/x")
    assert recorder.calls == []


def test_write_file_bounds_content():
    instance, recorder = adapter([])
    with pytest.raises(ValueError):
        instance.write_file(
            REPO, path="big.txt", content="x" * (1024 * 1024 + 1), message="M", branch="feat/x"
        )
    assert recorder.calls == []


# ----------------------------------------------------------------- multi-file commit
def _commit_responses(existing=None, blob_shas=("1" * 40, "2" * 40), new_commit="9" * 40):
    tree_entries = [{"path": path, "type": "blob", "sha": sha} for path, sha in (existing or {}).items()]
    return [
        ref(BASE),
        response(payload={"sha": BASE, "tree": {"sha": TREE}}),
        response(payload={"sha": TREE, "tree": tree_entries, "truncated": False}),
        *[response(status=201, payload={"sha": sha}) for sha in blob_shas],
        response(status=201, payload={"sha": "8" * 40}),
        response(status=201, payload={"sha": new_commit}),
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]

def _commit_responses_one_blob(existing=None, blob_sha="1" * 40, new_commit="9" * 40):
    tree_entries = [{"path": path, "type": "blob", "sha": sha} for path, sha in (existing or {}).items()]
    return [
        ref(BASE),
        response(payload={"sha": BASE, "tree": {"sha": TREE}}),
        response(payload={"sha": TREE, "tree": tree_entries, "truncated": False}),
        response(status=201, payload={"sha": blob_sha}),
        response(status=201, payload={"sha": "8" * 40}),
        response(status=201, payload={"sha": new_commit}),
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]


def _write_update_responses(new_commit="7" * 40, blob_sha="6" * 40):
    return [
        missing(),
        response(status=201, payload={"commit": {"sha": new_commit}, "content": {"sha": blob_sha}}),
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]








def test_multi_file_commit_then_fast_forward(tmp_path):
    instance, recorder = adapter(_commit_responses(existing={"README.md": BLOB_OLD}))

    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="Update two files",
        files=[
            FileChange(path="README.md", content="new\n", expected_sha=BLOB_OLD),
            FileChange(path="src/new.py", content="print(1)\n"),
        ],
        expected_parent_sha=BASE,
    )
    result = execute_file_commit(
        plan, instance, approved=True, actor_subject="tester", audit_path=tmp_path / "audit.jsonl",
        expected_old_sha="9" * 40,
        blob_sha="2" * 40,
    )
    assert result.ok is True
    assert result.result["commit_sha"] == BASE
    assert result.result["ref"] == "refs/heads/feat/x"
    assert result.result["files"] == ["README.md", "src/new.py"]
    assert result.result["previous_sha"] == BASE
    patch_body = json.loads(recorder.calls[-1]["body"])
    assert patch_body == {"sha": BASE, "force": False}
    assert result.result["paths"] == ["README.md", "src/new.py"]






def test_commit_rejects_a_stale_parent():
    instance, recorder = adapter(_commit_responses(existing={"a.txt": BLOB_OLD}))


    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="M",
        files=[FileChange(path="a.txt", content="x")],
        expected_parent_sha=BASE,
    )
    with pytest.raises(ConflictError):
        execute_file_commit(plan, instance, approved=True, expected_old_sha=BASE, blob_sha="2" * 40)
    assert recorder.calls[-1]["method"] == "PATCH"
    assert recorder.calls[-1]["method"] == "PATCH"





    assert len(recorder.calls) == 7




def test_commit_refuses_overwriting_an_existing_path_without_sha():
    instance, _ = adapter(_commit_responses_one_blob(existing={"README.md": BLOB_OLD}))
    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="M",
        files=[FileChange(path="README.md", content="x", expected_sha="7" * 40)],
    )
    with pytest.raises(ConflictError):
        execute_file_commit(plan, instance, approved=True, expected_old_sha=BASE, blob_sha="1" * 40)



def test_commit_rejects_a_stale_blob_sha_in_the_tree():
    instance, _ = adapter(_commit_responses_one_blob(existing={"README.md": BLOB_OLD}))
    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="M",
        files=[FileChange(path="README.md", content="x", expected_sha="7" * 40)],
    )
    with pytest.raises(ConflictError):
        execute_file_commit(plan, instance, approved=True, expected_old_sha=BASE, blob_sha="1" * 40)



def test_plan_rejects_duplicate_paths_and_unbounded_plans():
    instance, _ = adapter([])
    with pytest.raises(ValueError):
        plan_file_commit(
            instance,
            REPO,
            branch="feat/x",
            message="M",
            files=[FileChange("a.txt", "1"), FileChange("a.txt", "2")],
        )
    with pytest.raises(ValueError):
        plan_file_commit(
            instance,
            REPO,
            branch="feat/x",
            message="M",
            files=[FileChange(f"f{i}.txt", "x") for i in range(21)],
        )


def test_commit_plan_requires_approval_and_touches_nothing():
    instance, recorder = adapter([])
    plan = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "x")]
    )
    with pytest.raises(ApprovalRequiredError):
        execute_file_commit(plan, instance, approved=False)
    assert recorder.calls == []


def test_approval_is_bound_to_the_exact_plan():
    instance, recorder = adapter([])
    first = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "one")]
    )
    second = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "two")]
    )
    assert first.fingerprint() != second.fingerprint()
    with pytest.raises(PermissionDeniedError):
        execute_file_commit(
            second,
            instance,
            approved=True,
            approved_fingerprint=first.fingerprint(),
        )
    assert recorder.calls == []


def test_branch_plan_refuses_protected_names_and_self():
    instance, _ = adapter([])
    with pytest.raises(PermissionDeniedError):
        plan_branch_create(instance, REPO, branch="main", from_branch="develop")
    with pytest.raises(ValueError):
        plan_branch_create(instance, REPO, branch="feat/x", from_branch="feat/x")


def test_branch_execution_audits_the_plan_fingerprint(tmp_path):
    instance, _ = adapter(
        [ref(BASE), missing(), response(status=201, payload={"object": {"sha": BASE}})]
    )
    plan = plan_branch_create(instance, REPO, branch="feat/x", from_branch="main")
    result = execute_branch_create(
        plan, instance, approved=True, actor_subject="owner", audit_path=tmp_path / "audit.jsonl",
        approved_fingerprint=plan.fingerprint(),
    )
    assert result.ok is True
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.requested" in text
    assert "platform.write.completed" in text
    assert plan.fingerprint() in text
    assert "owner" in text


# ------------------------------------------------------------------ ref updates
def test_update_reference_fast_forwards():
    instance, recorder = adapter([ref(BASE), response(payload={"ref": "refs/heads/feat/x", "object": {"sha": OTHER}})])
    result = instance.update_reference(REPO, branch="feat/x", sha=OTHER, expected_old_sha=BASE)
    assert result["sha"] == OTHER
    assert json.loads(recorder.calls[-1]["body"]) == {"sha": OTHER, "force": False}


def test_update_reference_detects_a_stale_ref():
    instance, recorder = adapter([ref(OTHER)])
    with pytest.raises(ConflictError):
        instance.update_reference(REPO, branch="feat/x", sha=OTHER, expected_old_sha=BASE)
    assert len(recorder.calls) == 1


def test_update_reference_never_forces():
    instance, recorder = adapter([])
    with pytest.raises(PermissionDeniedError):
        instance.update_reference(REPO, branch="feat/x", sha=OTHER, force=True)
    assert recorder.calls == []


def test_plan_ref_update_refuses_protected_branch():
    instance, _ = adapter([])
    with pytest.raises(PermissionDeniedError):
        plan_ref_update(instance, REPO, branch="main", sha=OTHER)


def test_ref_update_execution_is_approved_and_audited(tmp_path):
    instance, recorder = adapter(
        [ref(BASE), response(payload={"ref": "refs/heads/feat/x", "object": {"sha": OTHER}})]
    )
    plan = plan_ref_update(instance, REPO, branch="feat/x", sha=OTHER, expected_old_sha=BASE)
    with pytest.raises(ApprovalRequiredError):
        execute_ref_update(plan, instance, approved=False, audit_path=tmp_path / "a.jsonl")
    assert recorder.calls == []
    result = execute_ref_update(plan, instance, approved=True, audit_path=tmp_path / "a.jsonl")
    assert result.result["sha"] == OTHER


# ---------------------------------------------------------------- failures/audit
def test_write_file_execution_audits_and_never_retries(tmp_path):
    instance, recorder = adapter(_write_update_responses())
    plan = plan_file_update(instance, REPO, path="a.txt", content="x", message="M", branch="feat/x")
    result = execute_file_update(plan, instance, approved=True, audit_path=tmp_path / "audit.jsonl")
    assert result.ok is True
    assert result.result["commit_sha"] == "7" * 40
    assert recorder.calls[0]["method"] == "GET"
    assert recorder.calls[1]["method"] == "PUT"
    assert recorder.calls[2]["method"] == "PATCH"
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.requested" in text
    assert "platform.write.completed" in text
    assert "a.txt" in text
    assert "sk-live-do-not-log-this-value" not in text










def test_audit_never_contains_file_content(tmp_path):
    secret = "sk-live-do-not-log-this-value"
    instance, _ = adapter(_commit_responses(blob_shas=("1" * 40,), new_commit=BASE))

    instance, _ = adapter(_commit_responses(blob_shas=("1" * 40,), new_commit=BASE))
    plan = plan_file_commit(
        instance, REPO, branch="feat/x", message="Add config",
        files=[FileChange(path="config.txt", content=secret)],
        expected_parent_sha=BASE,
    )
    execute_file_commit(plan, instance, approved=True, audit_path=tmp_path / "audit.jsonl",
                        expected_old_sha=BASE)
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert secret not in text
    assert "config.txt" in text


def test_plan_display_exposes_a_fingerprint():
    instance, _ = adapter([])
    plan = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "x")]
    )
    payload = plan.to_dict()
    assert payload["plan_fingerprint"] == plan.fingerprint()
    assert payload["approval_required"] is True
    assert len(plan.fingerprint()) == 64


# -------------------------------------------------------------- provider truth
def test_gitlab_does_not_advertise_or_execute_github_writes():
    gitlab = GitLabAdapter(request_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")), credential="t")
    assert gitlab.capability_state(Capability.BRANCH_CREATE) is CapabilityState.UNSUPPORTED
    with pytest.raises(CapabilityUnavailableError):
        gitlab.create_branch(REPO, branch="feat/x", from_branch="main")


# ------------------------------------------------------------------ host layer
class WritePlatform:
    provider_id = "github"
    credential = "test-token"
    transport = type("T", (), {"last_correlation_id": "corr"})()

    def capability_states(self):
        return {
            Capability.REPOSITORY_METADATA: CapabilityState.SUPPORTED,
            Capability.TREE_READ: CapabilityState.SUPPORTED,
            Capability.BRANCH_LIST: CapabilityState.SUPPORTED,
            Capability.COMMIT_LIST: CapabilityState.SUPPORTED,
            Capability.FILE_READ: CapabilityState.SUPPORTED,
            Capability.BRANCH_CREATE: CapabilityState.SUPPORTED,
            Capability.FILE_WRITE: CapabilityState.SUPPORTED,
            Capability.COMMIT_CREATE: CapabilityState.SUPPORTED,
            Capability.REF_UPDATE: CapabilityState.SUPPORTED,
            Capability.PULL_REQUEST_CREATE: CapabilityState.SUPPORTED,
        }

    def capability_state(self, capability):
        return self.capability_states().get(capability, CapabilityState.UNSUPPORTED)

    def create_branch(self, repository, *, branch, from_branch=None, from_sha=None):
        from ogx.platform.models import Branch

        return Branch(name=branch, commit_sha=from_sha or BASE, protected=False)

    def create_commit(self, repository, *, branch, message, files, expected_parent=None):
        from ogx.platform.models import CommitResult

        return CommitResult(
            commit_sha="9" * 40,
            tree_sha="8" * 40,
            parent_sha=expected_parent or BASE,
            branch=branch,
            files=tuple(change.path for change in files),
        )

    def update_reference(self, repository, *, branch, sha, expected_old_sha=None, force=False):
        assert force is False
        return {"ref": f"refs/heads/{branch}", "sha": sha, "previous_sha": expected_old_sha}

    def write_file(self, repository, *, path, content, message, branch, expected_sha=None):
        return {
            "path": path,
            "branch": branch,
            "commit_sha": "7" * 40,
            "blob_sha": "6" * 40,
            "previous_blob_sha": expected_sha,
        }


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    instance = GitHubHostAdapter(
        HostSessionConfig(root=REPO), platform_adapter=WritePlatform()
    )
    instance.validate()
    instance._connected = True
    return instance


def test_host_advertises_all_write_capabilities(host):
    capabilities = host.capabilities()
    for name in ("branch_create", "file_write", "commit_create", "push"):
        assert capabilities[name] == "supported"
    for name in ("branch_delete", "pull_request_merge", "deployment"):
        assert capabilities[name] == "unsupported"


def test_host_commit_requires_approval(host):
    with pytest.raises(ApprovalRequiredError):
        host.commit_files(
            branch="feat/x", message="M", files=[{"path": "a.txt", "content": "x"}], approved=False
        )


def test_host_commit_executes_and_audits(host, tmp_path):
    instance = WritePlatform()
    host.platform_adapter = instance
    result = host.commit_files(
        branch="feat/x",
        message="M",
        files=[FileChange(path="a.txt", content="x")],
        approved=True,
        actor_subject="owner",
    )
    assert result["ok"] is True
    assert result["result"]["commit_sha"] == "9" * 40
    assert result.result["paths"] == ["a.txt"]


def test_host_branch_and_push_require_approval(host):
    with pytest.raises(ApprovalRequiredError):
        host.create_branch(branch="feat/x", from_branch="main", approved=False)
    with pytest.raises(ApprovalRequiredError):
        host.push(branch="feat/x", sha=OTHER, approved=False)
    with pytest.raises(ApprovalRequiredError):
        host.write_file(
            path="a.txt", content="x", message="M", branch="feat/x", approved=False
        )



def test_commit_refuses_overwriting_an_existing_path_without_sha():
    instance, _ = adapter(_commit_responses_one_blob(existing={"README.md": BLOB_OLD}))
    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="M",
        files=[FileChange(path="README.md", content="x")],
    )
    with pytest.raises(ConflictError):
        execute_file_commit(plan, instance, approved=True)


def test_commit_rejects_a_stale_blob_sha_in_the_tree():
    instance, _ = adapter(_commit_responses_one_blob(existing={"README.md": BLOB_OLD}))
    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="M",
        files=[FileChange(path="README.md", content="x", expected_sha="7" * 40)],
    )
    with pytest.raises(ConflictError):
        execute_file_commit(plan, instance, approved=True)


def test_plan_rejects_duplicate_paths_and_unbounded_plans():
    instance, _ = adapter([])
    with pytest.raises(ValueError):
        plan_file_commit(
            instance,
            REPO,
            branch="feat/x",
            message="M",
            files=[FileChange("a.txt", "1"), FileChange("a.txt", "2")],
        )
    with pytest.raises(ValueError):
        plan_file_commit(
            instance,
            REPO,
            branch="feat/x",
            message="M",
            files=[FileChange(f"f{i}.txt", "x") for i in range(21)],
        )


def test_commit_plan_requires_approval_and_touches_nothing():
    instance, recorder = adapter([])
    plan = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "x")]
    )
    with pytest.raises(ApprovalRequiredError):
        execute_file_commit(plan, instance, approved=False)
    assert recorder.calls == []


def test_approval_is_bound_to_the_exact_plan():
    instance, recorder = adapter([])
    first = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "one")]
    )
    second = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "two")]
    )
    assert first.fingerprint() != second.fingerprint()
    with pytest.raises(PermissionDeniedError):
        execute_file_commit(
            second,
            instance,
            approved=True,
            approved_fingerprint=first.fingerprint(),
        )
    assert recorder.calls == []


def test_branch_plan_refuses_protected_names_and_self():
    instance, _ = adapter([])
    with pytest.raises(PermissionDeniedError):
        plan_branch_create(instance, REPO, branch="main", from_branch="develop")
    with pytest.raises(ValueError):
        plan_branch_create(instance, REPO, branch="feat/x", from_branch="feat/x")


def test_branch_execution_audits_the_plan_fingerprint(tmp_path):
    instance, _ = adapter(
        [ref(BASE), missing(), response(status=201, payload={"object": {"sha": BASE}})]
    )
    plan = plan_branch_create(instance, REPO, branch="feat/x", from_branch="main")
    result = execute_branch_create(
        plan,
        instance,
        approved=True,
        actor_subject="owner",
        audit_path=tmp_path / "audit.jsonl",
        approved_fingerprint=plan.fingerprint(),
    )
    assert result.ok is True
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.requested" in text
    assert "platform.write.completed" in text
    assert plan.fingerprint() in text
    assert "owner" in text


# ------------------------------------------------------------------ ref updates
def test_update_reference_fast_forwards():
    instance, recorder = adapter([ref(BASE), response(payload={"ref": "refs/heads/feat/x", "object": {"sha": OTHER}})])
    result = instance.update_reference(REPO, branch="feat/x", sha=OTHER, expected_old_sha=BASE)
    assert result["sha"] == OTHER
    assert json.loads(recorder.calls[-1]["body"]) == {"sha": OTHER, "force": False}


def test_update_reference_detects_a_stale_ref():
    instance, recorder = adapter([ref(OTHER)])
    with pytest.raises(ConflictError):
        instance.update_reference(REPO, branch="feat/x", sha=OTHER, expected_old_sha=BASE)
    assert len(recorder.calls) == 1


def test_update_reference_never_forces():
    instance, recorder = adapter([])
    with pytest.raises(PermissionDeniedError):
        instance.update_reference(REPO, branch="feat/x", sha=OTHER, force=True)
    assert recorder.calls == []


def test_plan_ref_update_refuses_protected_branch():
    instance, _ = adapter([])
    with pytest.raises(PermissionDeniedError):
        plan_ref_update(instance, REPO, branch="main", sha=OTHER)


def test_ref_update_execution_is_approved_and_audited(tmp_path):
    instance, recorder = adapter(
        [ref(BASE), response(payload={"ref": "refs/heads/feat/x", "object": {"sha": OTHER}})]
    )
    plan = plan_ref_update(instance, REPO, branch="feat/x", sha=OTHER, expected_old_sha=BASE)
    with pytest.raises(ApprovalRequiredError):
        execute_ref_update(plan, instance, approved=False, audit_path=tmp_path / "a.jsonl")
    assert recorder.calls == []
    result = execute_ref_update(plan, instance, approved=True, audit_path=tmp_path / "a.jsonl")
    assert result.result["sha"] == OTHER


# ---------------------------------------------------------------- failures/audit
def test_write_file_execution_audits_and_never_retries(tmp_path):
    def raise_transport(*args, **kwargs):
        raise ConnectionError("transport failure")

    instance = GitHubAdapter(
        request_fn=raise_transport,
        credential="test-token",
        max_retries=0,
        sleep=lambda _seconds: None,
    )
    plan = plan_file_update(instance, REPO, path="a.txt", content="x", message="M", branch="feat/x")
    with pytest.raises(ConnectionError):
        execute_file_update(plan, instance, approved=True, audit_path=tmp_path / "audit.jsonl")
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.requested" in text
    assert "platform.write.failed" in text
    assert "a.txt" in text
    assert "sk-live-do-not-log-this-value" not in text


def test_audit_never_contains_file_content(tmp_path):
    secret = "sk-live-do-not-log-this-value"
    instance, _ = adapter(_commit_responses(blob_shas=("1" * 40,), new_commit=BASE))
    plan = plan_file_commit(
        instance,
        REPO,
        branch="feat/x",
        message="Add config",
        files=[FileChange(path="config.txt", content=secret)],
        expected_parent_sha=BASE,
    )
    execute_file_commit(plan, instance, approved=True, audit_path=tmp_path / "audit.jsonl", expected_old_sha=BASE)
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert secret not in text
    assert "config.txt" in text


def test_plan_display_exposes_a_fingerprint():
    instance, _ = adapter([])
    plan = plan_file_commit(
        instance, REPO, branch="feat/x", message="M", files=[FileChange("a.txt", "x")]
    )
    payload = plan.to_dict()
    assert payload["plan_fingerprint"] == plan.fingerprint()
    assert payload["approval_required"] is True
    assert len(plan.fingerprint()) == 64


# -------------------------------------------------------------- provider truth
def test_gitlab_does_not_advertise_or_execute_github_writes():
    gitlab = GitLabAdapter(request_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")), credential="t")
    assert gitlab.capability_state(Capability.BRANCH_CREATE) is CapabilityState.UNSUPPORTED
    with pytest.raises(CapabilityUnavailableError):
        gitlab.create_branch(REPO, branch="feat/x", from_branch="main")


# ------------------------------------------------------------------ host layer
class WritePlatform:
    provider_id = "github"
    credential = "test-token"
    transport = type("T", (), {"last_correlation_id": "corr"})()

    def capability_states(self):
        return {
            Capability.REPOSITORY_METADATA: CapabilityState.SUPPORTED,
            Capability.TREE_READ: CapabilityState.SUPPORTED,
            Capability.BRANCH_LIST: CapabilityState.SUPPORTED,
            Capability.COMMIT_LIST: CapabilityState.SUPPORTED,
            Capability.FILE_READ: CapabilityState.SUPPORTED,
            Capability.BRANCH_CREATE: CapabilityState.SUPPORTED,
            Capability.FILE_WRITE: CapabilityState.SUPPORTED,
            Capability.COMMIT_CREATE: CapabilityState.SUPPORTED,
            Capability.REF_UPDATE: CapabilityState.SUPPORTED,
            Capability.PULL_REQUEST_CREATE: CapabilityState.SUPPORTED,
        }

    def capability_state(self, capability):
        return self.capability_states().get(capability, CapabilityState.UNSUPPORTED)

    def create_branch(self, repository, *, branch, from_branch=None, from_sha=None):
        from ogx.platform.models import Branch

        return Branch(name=branch, commit_sha=from_sha or BASE, protected=False)

    def create_commit(self, repository, *, branch, message, files, expected_parent=None):
        from ogx.platform.models import CommitResult

        return CommitResult(
            commit_sha="9" * 40,
            tree_sha="8" * 40,
            parent_sha=expected_parent or BASE,
            branch=branch,
            files=tuple(change.path for change in files),
        )

    def update_reference(self, repository, *, branch, sha, expected_old_sha=None, force=False):
        assert force is False
        return {"ref": f"refs/heads/{branch}", "sha": sha, "previous_sha": expected_old_sha}

    def write_file(self, repository, *, path, content, message, branch, expected_sha=None):
        return {
            "path": path,
            "branch": branch,
            "commit_sha": "7" * 40,
            "blob_sha": "6" * 40,
            "previous_blob_sha": expected_sha,
        }


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "state"))
    instance = GitHubHostAdapter(
        HostSessionConfig(root=REPO), platform_adapter=WritePlatform()
    )
    instance.validate()
    instance._connected = True
    return instance


def test_host_advertises_all_write_capabilities(host):
    capabilities = host.capabilities()
    for name in ("branch_create", "file_write", "commit_create", "push"):
        assert capabilities[name] == "supported"
    for name in ("branch_delete", "pull_request_merge", "deployment"):
        assert capabilities[name] == "unsupported"


def test_host_commit_requires_approval(host):
    with pytest.raises(ApprovalRequiredError):
        host.commit_files(
            branch="feat/x", message="M", files=[{"path": "a.txt", "content": "x"}], approved=False
        )


def test_host_commit_executes_and_audits(host, tmp_path):
    result = host.commit_files(
        branch="feat/x",
        message="M",
        files=[FileChange(path="a.txt", content="x")],
        approved=True,
        actor_subject="owner",
    )
    assert result["ok"] is True
    assert result["result"]["commit_sha"] == "9" * 40
    audit = (tmp_path / "state" / "platform" / "audit.jsonl").read_text(encoding="utf-8")
    assert "platform.write.completed" in audit


def test_host_branch_and_push_require_approval(host):
    with pytest.raises(ApprovalRequiredError):
        host.create_branch(branch="feat/x", from_branch="main", approved=False)
    with pytest.raises(ApprovalRequiredError):
        host.push(branch="feat/x", sha=OTHER, approved=False)
    with pytest.raises(ApprovalRequiredError):
        host.write_file(
            path="a.txt", content="x", message="M", branch="feat/x", approved=False
        )

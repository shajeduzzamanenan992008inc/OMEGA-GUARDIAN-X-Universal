"""HOST-10 Kubernetes host adapter contract and security tests.

These tests drive the adapter through a mocked ``kubectl`` subprocess boundary.
They prove the *offline* contract: argument construction, TLS/context pinning,
namespace scoping, Secret redaction, bounded output, timeout/cancellation and
error mapping. Live-cluster verification stays blocked until an authorized
non-production cluster is available, and is deliberately NOT claimed here.

The previous revision of this module was machine-generated, could not even be
collected (a malformed selector regex aborted import) and contained mutually
contradictory assertions. It was replaced by this coherent suite covering the
same security surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import pytest

from ogx.hosts.adapters.kubernetes_runtime import (
    KubernetesAuthError,
    KubernetesConfigError,
    KubernetesContextError,
    KubernetesDaemonError,
    KubernetesHostAdapter,
    KubernetesHostError,
    KubernetesPermissionError,
    KubernetesResponseError,
    KubernetesTimeoutError,
    KubernetesUnavailableError,
    _KubectlClient,
    _metadata_only_configmap,
    _metadata_only_deployment,
    _metadata_only_secret,
    _redact,
    _validate_api_version,
    _validate_context,
    _validate_kubeconfig_path,
    _validate_label_selector,
    _validate_namespace,
    _validate_resource_name,
)
from ogx.hosts.capabilities import get_host_capabilities
from ogx.hosts.adapters.runtime_factory import create_host_adapter
from ogx.hosts.lifecycle import HealthState, HostLifecycleError, HostSessionConfig


# ---------------------------------------------------------------------------
# kubectl stub
# ---------------------------------------------------------------------------

Response = Tuple[int, str, str]


def ok(value: Any) -> Response:
    return (0, json.dumps(value), "")


def text(value: str) -> Response:
    return (0, value, "")


def fail(code: int = 1, stderr: str = "") -> Response:
    return (code, "", stderr or "kubectl error")


class KubectlStub:
    """Queued fake for ``subprocess.run`` plus a fake ``shutil.which``."""

    def __init__(self, *responses: Response, exists: bool = True) -> None:
        self.responses: List[Response] = list(responses)
        self.calls: List[List[str]] = []
        self.exists = exists

    def install(self, monkeypatch: pytest.MonkeyPatch, executable: str = "/usr/bin/kubectl") -> None:
        def fake_run(cmd, **kwargs):
            assert isinstance(cmd, list), "kubectl must be invoked with an argument list"
            assert "shell" not in kwargs, "kubectl must never run through a shell"
            self.calls.append(list(cmd))
            if not self.responses:
                raise AssertionError(f"unexpected kubectl invocation: {cmd}")
            code, out, err = self.responses.pop(0)
            return subprocess.CompletedProcess(
                cmd, code, out.encode("utf-8"), err.encode("utf-8")
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: executable if (self.exists and name == "kubectl") else None,
        )


def make_config(
    *,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    namespace: Optional[str] = None,
    allowlist: Optional[List[str]] = None,
    cluster_scope: bool = False,
    timeout: float = 10.0,
    max_file_bytes: int = 256 * 1024,
) -> HostSessionConfig:
    metadata: Dict[str, Any] = {}
    if kubeconfig is not None:
        metadata["kubeconfig"] = kubeconfig
    if context is not None:
        metadata["context"] = context
    if namespace is not None:
        metadata["namespace"] = namespace
    if allowlist is not None:
        metadata["namespace_allowlist"] = allowlist
    if cluster_scope:
        metadata["cluster_scope_allowed"] = True
    return HostSessionConfig(
        root=".",
        metadata=metadata,
        timeout_seconds=timeout,
        max_file_bytes=max_file_bytes,
    )


def connected_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *responses: Response,
    config: Optional[HostSessionConfig] = None,
    exists: bool = True,
) -> Tuple[KubernetesHostAdapter, KubectlStub]:
    """Build an adapter and connect it through the standard three-call handshake."""
    cfg = config or make_config(context="ctx", namespace="ns")
    stub = KubectlStub(
        ok({"serverVersion": {"gitVersion": "v1.28.0"}}),
        text("ctx\n"),
        ok([{"cluster": "https://10.0.0.1:6443"}]),
        *responses,
        exists=exists,
    )
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(cfg)
    adapter.connect()
    return adapter, stub


# ---------------------------------------------------------------------------
# Identifier and configuration validation
# ---------------------------------------------------------------------------


def test_validate_kubeconfig_accepts_relative_path() -> None:
    assert _validate_kubeconfig_path("shared/kubeconfig") == "shared/kubeconfig"
    assert _validate_kubeconfig_path(None) is None


@pytest.mark.parametrize("value", ["/etc/kubernetes/admin.conf", "C:/Users/me/.kube/config", "../../secret/config"])
def test_validate_kubeconfig_rejects_absolute_and_traversal(value: str) -> None:
    with pytest.raises(KubernetesConfigError, match="kubeconfig"):
        _validate_kubeconfig_path(value)


@pytest.mark.parametrize("value", ["", "-ctx", "bad ctx", "x" * 300])
def test_validate_context_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(KubernetesContextError):
        _validate_context(value)


def test_validate_context_accepts_pinned_context() -> None:
    assert _validate_context("gke_prod_1") == "gke_prod_1"


@pytest.mark.parametrize("value", ["", "-ns", "Bad_NS", "a" * 70, "ns/other", "ns;rm -rf /"])
def test_validate_namespace_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(KubernetesHostError):
        _validate_namespace(value)


@pytest.mark.parametrize("value", ["", "-pod", "pod name", "pod;id", "a" * 300])
def test_validate_resource_name_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(KubernetesHostError):
        _validate_resource_name(value)


@pytest.mark.parametrize(
    "value",
    ["app=nginx", "app==nginx", "app!=nginx", "app in (a, b)", "app notin (a)", "!app", "a=b,c in (d,e)"],
)
def test_validate_label_selector_accepts_valid_selectors(value: str) -> None:
    assert _validate_label_selector(value) == value


@pytest.mark.parametrize("value", ["-bad", "; rm -rf /", "app in (", "a=b,", "a" * 600])
def test_validate_label_selector_rejects_injection_and_malformed(value: str) -> None:
    with pytest.raises(KubernetesHostError):
        _validate_label_selector(value)


@pytest.mark.parametrize("value", ["v1", "apps/v1", "a" * 128])
def test_validate_api_version_accepts_valid(value: str) -> None:
    assert _validate_api_version(value) == value


@pytest.mark.parametrize("value", ["", "-bad", "too.long." + "x" * 140])
def test_validate_api_version_rejects_invalid(value: str) -> None:
    with pytest.raises(KubernetesHostError):
        _validate_api_version(value)


def test_config_accepts_relative_kubeconfig() -> None:
    adapter = KubernetesHostAdapter(make_config(kubeconfig="shared/kubeconfig"))
    adapter._parse_config()
    assert adapter._kubeconfig == "shared/kubeconfig"


def test_config_rejects_selected_namespace_outside_allowlist() -> None:
    adapter = KubernetesHostAdapter(make_config(namespace="other", allowlist=["ns-1"]))
    with pytest.raises(HostLifecycleError, match="allowlist"):
        adapter._parse_config()


def test_config_allowlist_must_be_a_list() -> None:
    config = HostSessionConfig(root=".", metadata={"namespace_allowlist": "ns-1"})
    with pytest.raises(HostLifecycleError, match="namespace_allowlist"):
        KubernetesHostAdapter(config)._parse_config()


def test_single_allowlist_namespace_becomes_default() -> None:
    adapter = KubernetesHostAdapter(make_config(allowlist=["ns-1"]))
    adapter._parse_config()
    assert adapter._effective_namespace() == "ns-1"


# ---------------------------------------------------------------------------
# Lifecycle: validate / connect / disconnect / health
# ---------------------------------------------------------------------------


def test_validate_success_keeps_session_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub(
        ok({"serverVersion": {"gitVersion": "v1.28.0"}}),
        text("ctx\n"),
        ok([{"cluster": "https://10.0.0.1:6443"}]),
    )
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(context="ctx"))
    adapter.validate()
    assert adapter._connected is False
    assert adapter._selected_context == "ctx"
    assert all(call[0] == "/usr/bin/kubectl" for call in stub.calls)


def test_validate_pins_context_and_rejects_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub(
        ok({"serverVersion": {"gitVersion": "v1.28.0"}}),
        text("other\n"),
    )
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(context="expected"))
    with pytest.raises(KubernetesContextError, match="Explicit context mismatch"):
        adapter.validate()


def test_validate_requires_kubectl(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub(exists=False)
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(context="ctx"))
    with pytest.raises(KubernetesUnavailableError):
        adapter.validate()


def test_validate_rejects_empty_version_response(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub(text(""))
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(context="ctx"))
    with pytest.raises(KubernetesResponseError, match="Empty kubectl version"):
        adapter.validate()


def test_validate_rejects_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub(text("not-json"))
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(context="ctx"))
    with pytest.raises(KubernetesResponseError, match="Malformed kubectl JSON"):
        adapter.validate()


def test_connect_and_disconnect_are_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(monkeypatch)
    assert adapter._connected is True
    adapter.disconnect()
    assert adapter._connected is False
    assert adapter._kubectl is None
    adapter.disconnect()  # idempotent


def test_health_healthy_reports_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(
        monkeypatch, ok([{"cluster": "https://10.0.0.1:6443"}])
    )
    health = adapter.health()
    assert health.state is HealthState.HEALTHY
    assert health.latency_ms is not None and health.latency_ms >= 0


def test_health_unreachable_when_daemon_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(monkeypatch, fail(1, "unable to connect to server"))
    health = adapter.health()
    assert health.state is HealthState.UNREACHABLE
    assert health.issues


def test_health_unknown_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub()
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(namespace="ns"))
    assert adapter.health().state is HealthState.UNKNOWN


def test_timeout_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout") or 0.05)

    monkeypatch.setattr(subprocess, "run", slow_run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/kubectl")
    adapter = KubernetesHostAdapter(make_config(context="ctx", timeout=0.05))
    with pytest.raises(KubernetesTimeoutError, match="timed out"):
        adapter.validate()


@pytest.mark.parametrize(
    "stderr,error",
    [
        ("error: You must be logged in to the server (Unauthorized)", KubernetesPermissionError),
        ("Error from server (Forbidden): rbac denied", KubernetesPermissionError),
        ("error: invalid credentials", KubernetesAuthError),
        ("unable to connect to the server: dial tcp: i/o timeout", KubernetesDaemonError),
    ],
)
def test_kubectl_stderr_is_mapped_to_typed_errors(
    monkeypatch: pytest.MonkeyPatch, stderr: str, error: type
) -> None:
    stub = KubectlStub(fail(1, stderr))
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config(context="ctx"))
    with pytest.raises(error):
        adapter.validate()


# ---------------------------------------------------------------------------
# Subprocess boundary / injection
# ---------------------------------------------------------------------------


def test_caller_supplied_flags_are_rejected() -> None:
    client = _KubectlClient(
        executable="/usr/bin/kubectl", timeout_seconds=5.0, max_output_bytes=4096
    )
    for injected in ("--server=http://evil", "--insecure-skip-tls-verify", "-n"):
        with pytest.raises(KubernetesHostError, match="Arbitrary kubectl flags"):
            client.run_json(args=["get", "pods", injected])


def test_nul_bytes_are_rejected() -> None:
    client = _KubectlClient(
        executable="/usr/bin/kubectl", timeout_seconds=5.0, max_output_bytes=4096
    )
    with pytest.raises(KubernetesHostError, match="NUL"):
        client.run_json(args=["get", "pods\x00"])


def test_internal_calls_are_argument_arrays_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, stub = connected_adapter(
        monkeypatch,
        ok([{"metadata": {"name": "p1"}, "spec": {"containers": []}}]),
        config=make_config(context="ctx", namespace="ns"),
    )
    adapter.pod_list()
    get_call = stub.calls[-1]
    assert get_call[:4] == ["/usr/bin/kubectl", "--context", "ctx", "-n"]
    assert "pods" in get_call


def test_labels_are_passed_as_arguments_not_interpolated(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, stub = connected_adapter(
        monkeypatch,
        ok([]),
        config=make_config(context="ctx", namespace="ns"),
    )
    adapter.pod_list(label_selector="app=nginx")
    assert "-l" in stub.calls[-1]
    assert "app=nginx" in stub.calls[-1]


# ---------------------------------------------------------------------------
# Namespace scoping and cluster-scope policy
# ---------------------------------------------------------------------------


def test_resource_ops_require_namespace_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = KubectlStub()
    stub.install(monkeypatch)
    adapter = KubernetesHostAdapter(make_config())
    adapter._parse_config()
    adapter._connected = True
    adapter._kubectl = _KubectlClient(
        executable="/usr/bin/kubectl", timeout_seconds=5.0, max_output_bytes=4096
    )
    with pytest.raises(KubernetesHostError, match="namespace-scoped by default"):
        adapter.pod_list()


def test_namespace_list_without_cluster_scope_returns_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = connected_adapter(
        monkeypatch,
        config=make_config(context="ctx", allowlist=["ns-a", "ns-b"]),
    )
    items = adapter.namespace_list(limit=1)
    assert items[0]["metadata"]["name"] == "ns-a"


def test_namespace_from_allowlist_is_enforced_on_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = connected_adapter(
        monkeypatch,
        config=make_config(context="ctx", namespace="allowed", allowlist=["allowed"]),
    )
    with pytest.raises(KubernetesHostError, match="allowlist"):
        adapter.namespace_metadata(name="other")


def test_cluster_scoped_listing_is_refused_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(
        monkeypatch, config=make_config(context="ctx", namespace="ns")
    )
    with pytest.raises(KubernetesHostError, match="cluster-scoped"):
        adapter.pv_list()
    with pytest.raises(KubernetesHostError, match="cluster-scoped"):
        adapter.storageclass_list()


def test_cluster_scoped_listing_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = connected_adapter(
        monkeypatch,
        ok({"items": []}),
        config=make_config(context="ctx", namespace="ns", cluster_scope=True),
    )
    assert adapter.storageclass_list() == []


# ---------------------------------------------------------------------------
# Metadata mapping and Secret protection
# ---------------------------------------------------------------------------


def test_pod_metadata_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {
        "metadata": {"name": "p1", "namespace": "ns", "labels": {"app": "x"}},
        "spec": {
            "nodeName": "node-1",
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:1.27",
                    "env": [{"name": "TOKEN", "value": "super-secret"}],
                    "command": ["/bin/sh", "-c", "curl evil"],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "podIP": "10.0.0.5",
            "hostIP": "10.0.0.1",
            "unknownField": "should-not-appear",
            "containerStatuses": [{"name": "app", "ready": True, "restartCount": 2}],
        },
    }
    adapter, _ = connected_adapter(
        monkeypatch, ok([item]), config=make_config(context="ctx", namespace="ns")
    )
    pods = adapter.pod_list()
    assert len(pods) == 1
    pod = pods[0]
    assert pod["name"] == "p1"
    assert pod["status"]["phase"] == "Running"
    assert "unknownField" not in pod["status"]
    assert pod["spec"]["containers"][0]["image"] == "nginx:1.27"
    assert pod["spec"]["containers"][0]["restarts"] == 2
    serialized = json.dumps(pod)
    assert "super-secret" not in serialized
    assert "curl evil" not in serialized


def test_deployment_metadata_reports_replicas_and_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {
        "metadata": {"name": "web", "namespace": "ns"},
        "spec": {
            "replicas": 3,
            "template": {"spec": {"containers": [{"image": "web:2.1"}]}},
        },
        "status": {"replicas": 3, "readyReplicas": 2, "availableReplicas": 2},
    }
    adapter, _ = connected_adapter(
        monkeypatch, ok([item]), config=make_config(context="ctx", namespace="ns")
    )
    deployments = adapter.deployment_list()
    assert deployments[0]["status"]["readyReplicas"] == 2
    assert deployments[0]["images"] == ["web:2.1"]


def test_secret_metadata_never_returns_values() -> None:
    mapped = _metadata_only_secret(
        {
            "metadata": {"name": "db", "namespace": "ns"},
            "type": "Opaque",
            "data": {"password": "cGFzc3dvcmQ="},
            "stringData": {"token": "plaintext-token"},
        }
    )
    assert mapped["name"] == "db"
    assert mapped["data"] == {"password": "<redacted>"}
    assert mapped["stringData"] == {"token": "<redacted>"}
    assert "cGFzc3dvcmQ=" not in json.dumps(mapped)
    assert "plaintext-token" not in json.dumps(mapped)


def test_secret_metadata_list_never_returns_values(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(
        monkeypatch,
        ok(
            [
                {
                    "metadata": {"name": "s1"},
                    "type": "Opaque",
                    "data": {"k": "c2VjcmV0"},
                }
            ]
        ),
        config=make_config(context="ctx", namespace="ns"),
    )
    items = adapter.secret_metadata_list()
    assert items[0]["data"] == {"k": "<redacted>"}
    assert "c2VjcmV0" not in json.dumps(items)


def test_configmap_redacts_secret_looking_keys() -> None:
    mapped = _metadata_only_configmap(
        {
            "metadata": {"name": "cfg"},
            "data": {"token": "abc123", "LOG_LEVEL": "info", "jwt": "eyJhbGciOi.abc.def"},
        }
    )
    assert mapped["data"]["token"] == "<redacted>"
    assert mapped["data"]["jwt"] == "<redacted>"
    assert mapped["data"]["LOG_LEVEL"] == "info"


def test_pod_log_is_bounded_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, stub = connected_adapter(
        monkeypatch,
        text("info: hello\nwarn: password=hunter2\ninfo: done\n"),
        config=make_config(context="ctx", namespace="ns"),
    )
    result = adapter.pod_log(name="pod-1", tail_lines=10, max_bytes=4096)
    assert result["pod"] == "pod-1"
    assert result["namespace"] == "ns"
    assert result["line_count"] == 3
    assert "hunter2" not in result["log"]
    assert "<redacted>" in result["log"]
    assert result["truncated"] is False
    assert "--tail" in stub.calls[-1]


@pytest.mark.parametrize("tail", [0, -1, 5001])
def test_pod_log_tail_lines_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tail: int
) -> None:
    adapter, _ = connected_adapter(
        monkeypatch, text(""), config=make_config(context="ctx", namespace="ns")
    )
    with pytest.raises(KubernetesHostError, match="tail_lines"):
        adapter.pod_log(name="pod-1", tail_lines=tail)


def test_redaction_masks_tokens_and_credentials() -> None:
    redacted = _redact("token=abcdef123 Authorization: Bearer abcdefghijklmnop")
    assert "abcdef123" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert len(redacted) <= 700


def test_resource_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(
        monkeypatch, ok([]), config=make_config(context="ctx", namespace="ns")
    )
    with pytest.raises(KubernetesHostError, match="Resource not found"):
        adapter.pod_metadata(name="missing")


# ---------------------------------------------------------------------------
# Output bounds
# ---------------------------------------------------------------------------


def test_oversized_output_is_bounded_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = [{"metadata": {"name": f"p{i}", "annotations": {"a": "x" * 200}}} for i in range(200)]
    adapter, _ = connected_adapter(
        monkeypatch,
        ok(huge),
        config=make_config(context="ctx", namespace="ns", max_file_bytes=64),
    )
    with pytest.raises(KubernetesResponseError):
        adapter.pod_list()


def test_empty_get_response_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = connected_adapter(
        monkeypatch, text(""), config=make_config(context="ctx", namespace="ns")
    )
    assert adapter.pod_list() == []


# ---------------------------------------------------------------------------
# Registration, capabilities and describe
# ---------------------------------------------------------------------------


def test_runtime_factory_registers_kubernetes() -> None:
    adapter = create_host_adapter("kubernetes", make_config(namespace="ns"))
    assert isinstance(adapter, KubernetesHostAdapter)


def test_capabilities_only_advertise_implemented_operations() -> None:
    adapter = KubernetesHostAdapter(make_config(namespace="ns"))
    caps = adapter.capabilities()
    assert caps["namespace_list"] == "supported"
    assert caps["pod_list"] == "supported"
    assert caps["pod_logs"] == "supported_limited"
    assert caps["secret_metadata"] == "supported_only_metadata"
    for unsupported in ("file_write", "workspace_exec", "workspace_execute", "push", "deployment"):
        assert caps[unsupported] == "unsupported"
    for never in ("kubectl_exec", "deployment_create", "secret_read", "port_forward"):
        assert caps.get(never, "unsupported") == "unsupported"


def test_capability_matrix_matches_adapter_claims() -> None:
    matrix = get_host_capabilities("kubernetes")
    adapter = KubernetesHostAdapter(make_config(namespace="ns"))
    for name, status in adapter.capabilities().items():
        assert matrix.get(name) == status


def test_describe_exposes_policy_not_secrets() -> None:
    adapter = KubernetesHostAdapter(make_config(context="ctx", kubeconfig="my.cfg"))
    described = adapter.describe()
    meta = described["metadata"]
    assert described["host_type"] == "kubernetes"
    assert meta["read_only"] is True
    assert meta["tls_verification_enforced"] is True
    assert meta["insecure_skip_tls_verify_denied"] is True
    assert meta["kubeconfig"] == "my.cfg"


def test_adapter_never_passes_insecure_tls_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, stub = connected_adapter(
        monkeypatch,
        ok([]),
        config=make_config(context="ctx", namespace="ns", kubeconfig="cfg"),
    )
    adapter.pod_list()
    for call in stub.calls:
        joined = " ".join(call)
        assert "--insecure-skip-tls-verify" not in joined
        assert call[0] == "/usr/bin/kubectl"
        if "--kubeconfig" in call:
            assert call[call.index("--kubeconfig") + 1] == "cfg"

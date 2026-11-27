"""
Kubernetes host adapter (HOST-10).

Drives the locally installed ``kubectl`` client through a safe, bounded
subprocess boundary and reads cluster/workload metadata. Conservative by
design: read-only by default, namespace-scoped by default, TLS verification
is never disabled, Secret values are never returned, and mutations are not
implemented in this adapter.

Live cluster verification is intentionally optional. Deterministic mocked
tests exercise the adapter without a real cluster.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import subprocess
import time
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)


# ---------------------------------------------------------------------------
# Identifier/validation helpers
# ---------------------------------------------------------------------------

_CONTEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./@:-]{0,253}$")
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?$")
_RESOURCE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,252}[a-z0-9])?$")
_LABEL_KEY_FRAGMENT = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_./-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?)*"
)
_LABEL_VALUE_FRAGMENT = r"(?:[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?)?"
_LABEL_REQUIREMENT = (
    rf"(?:{_LABEL_KEY_FRAGMENT}\s*(?:==?|!=)\s*{_LABEL_VALUE_FRAGMENT}"
    rf"|{_LABEL_KEY_FRAGMENT}\s+(?:in|notin)\s*\(\s*{_LABEL_VALUE_FRAGMENT}"
    rf"(?:\s*,\s*{_LABEL_VALUE_FRAGMENT})*\s*\)"
    rf"|!?{_LABEL_KEY_FRAGMENT})"
)
_LABEL_SELECTOR_RE = re.compile(
    rf"^{_LABEL_REQUIREMENT}(?:\s*,\s*{_LABEL_REQUIREMENT})*$"
)

_API_GROUP_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,127}[a-z0-9])?$")
_API_VERSION_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?/)?"
    r"(?:[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?)$"
)
_MAX_OUTPUT_BYTES_DEFAULT = 256 * 1024

_SENSITIVE_KEYS = frozenset({
    "token", "tokenValue", "token.value", "tokenSecret", "secret", "secretValue",
    "clientCertificateData", "clientKeyData", "clientCertificate", "clientKey",
    "password", "accessToken", "authorization", "cookies", "bearer",
})

_LOG_SENSITIVE_CONTAINS = frozenset({
    "eyJ", "token", "secret", "password", "credential", "authorization",
})


class KubernetesHostError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "kubernetes_error") -> None:
        super().__init__(message, code=code)


class KubernetesUnavailableError(KubernetesHostError):
    def __init__(self, message: str = "kubectl executable was not found on PATH") -> None:
        super().__init__(message, code="kubectl_unavailable")


class KubernetesDaemonError(KubernetesHostError):
    def __init__(self, message: str = "Kubernetes API server is not reachable") -> None:
        super().__init__(message, code="kubernetes_api_unreachable")


class KubernetesAuthError(KubernetesHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="kubernetes_auth_failed")


class KubernetesPermissionError(KubernetesHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="kubernetes_permission_denied")


class KubernetesConfigError(KubernetesHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="kubernetes_config_invalid")


class KubernetesContextError(KubernetesHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="kubernetes_context_invalid")


class KubernetesTimeoutError(KubernetesHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="kubernetes_timeout")


class KubernetesResponseError(KubernetesHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="kubernetes_response_invalid")


_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{8,}=*"),
    re.compile(r"(?i)\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)\b((?:token|secret|password|passwd|credential|authorization|api[_-]?key)[\w.-]*\s*[=:]\s*)"
        r"(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;]{4,})"
    ),
)


def _redact(text: str) -> str:
    """Truncate and mask credential-shaped fragments before surfacing text."""
    value = (text or "").strip()[:700]
    value = _SECRET_PATTERNS[0].sub(r"\1<redacted>", value)
    value = _SECRET_PATTERNS[1].sub("<redacted-jwt>", value)
    value = _SECRET_PATTERNS[2].sub(r"\1<redacted>", value)
    return value


def _redact_log_line(line: str) -> str:
    value = line
    value = _SECRET_PATTERNS[0].sub(r"\1<redacted>", value)
    value = _SECRET_PATTERNS[1].sub("<redacted-jwt>", value)
    value = _SECRET_PATTERNS[2].sub(r"\1<redacted>", value)
    return value


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json_value(v) for v in value]
    if not isinstance(value, str):
        return value
    lowered = value.lower()
    if any(k in lowered for k in ("eyj", "token", "secret", "password", "credential", "authorization")):
        return "<redacted>"
    return value


def _redact_secret_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not metadata:
        return None
    safe: Dict[str, Any] = {}
    for k, v in metadata.items():
        if k == "data" and isinstance(v, dict):
            safe[k] = {key: "<redacted>" for key in v}
        elif k == "stringData" and isinstance(v, dict):
            safe[k] = {key: "<redacted>" for key in v}
        elif k.lower() in _SENSITIVE_KEYS:
            safe[k] = "<redacted>"
        else:
            safe[k] = _redact_json_value(v)
    return safe


def _validate_kubeconfig_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KubernetesConfigError("kubeconfig must be a path string if provided")
    p = value.strip()
    if not p:
        return None
    candidate = p.replace("\\", "/")
    if candidate.startswith("/") or (
        len(candidate) > 1 and candidate[1] == ":"
    ):
        raise KubernetesConfigError("Only relative kubeconfig paths are accepted in this adapter")
    if ".." in candidate.split("/"):
        raise KubernetesConfigError("kubeconfig path traversal is not accepted")
    return candidate


def _validate_context(value: Any) -> str:
    v = str(value or "").strip()
    if not v or not _CONTEXT_RE.fullmatch(v) or v.startswith("-"):
        raise KubernetesContextError(f"Invalid kubeconfig context name: {value!r}")
    return v


def _validate_namespace(value: Any) -> str:
    v = str(value or "").strip()
    if not v or not _NAMESPACE_RE.fullmatch(v) or v.startswith("-"):
        raise KubernetesHostError(f"Invalid Kubernetes namespace: {value!r}")
    return v


def _validate_resource_name(value: Any) -> str:
    v = str(value or "").strip()
    if not v or not _RESOURCE_NAME_RE.fullmatch(v) or v.startswith("-"):
        raise KubernetesHostError(f"Invalid Kubernetes resource name: {value!r}")
    return v


def _validate_resource_type(value: Any) -> str:
    v = str(value or "").strip().lower()
    if not v or v.startswith("-"):
        raise KubernetesHostError(f"Invalid Kubernetes resource type: {value!r}")
    allowed = {
        "pods", "deployments", "replicasets", "statefulsets", "daemonsets",
        "jobs", "cronjobs", "services", "ingresses", "networkpolicies",
        "configmaps", "secrets", "persistentvolumeclaims", "persistentvolumes",
        "storageclasses", "namespaces",
    }
    if v not in allowed:
        raise KubernetesHostError(f"Unsupported Kubernetes resource type: {value!r}")
    return v


def _validate_api_group(value: Any) -> str:
    v = str(value or "").strip().lower()
    if not v or not _API_GROUP_RE.fullmatch(v):
        raise KubernetesHostError(f"Invalid Kubernetes API group: {value!r}")
    return v


def _validate_api_version(value: Any) -> str:
    v = str(value or "").strip().lower()
    if not v or len(v) > 128 or not _API_VERSION_RE.fullmatch(v):
        raise KubernetesHostError(f"Invalid Kubernetes API version: {value!r}")
    return v


def _validate_label_selector(value: Any) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if len(v) > 512 or not _LABEL_SELECTOR_RE.fullmatch(v) or v.startswith("-"):
        raise KubernetesHostError(f"Invalid Kubernetes label selector: {value!r}")
    return v


def _validate_output_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise KubernetesHostError(f"Invalid output limit: {value!r}") from exc
    if limit <= 0 or limit > _MAX_OUTPUT_BYTES_DEFAULT:
        raise KubernetesHostError(f"Output limit out of range: {value!r}")
    return limit


# ---------------------------------------------------------------------------
# Safe kubectl subprocess boundary
# ---------------------------------------------------------------------------


class _KubectlClient:
    """Bounded kubectl subprocess wrapper. Replaceable transport boundary."""

    def __init__(
        self,
        *,
        executable: str,
        timeout_seconds: float,
        max_output_bytes: int,
        kubeconfig: Optional[str] = None,
        context: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.kubeconfig = kubeconfig
        self.context = context
        self.namespace = namespace

    def _build_args(self, args: List[str]) -> List[str]:
        cmd = [self.executable]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        if self.context:
            cmd += ["--context", self.context]
        if self.namespace:
            cmd += ["-n", self.namespace]
        cmd += args
        return cmd

    @staticmethod
    def _validate_caller_args(args: List[str]) -> None:
        """Reject caller-supplied flags (untrusted boundary).

        Internal command builders use :meth:`_run_json`/:meth:`_run_text`,
        which are the only places allowed to add kubectl flags. Callers that
        go through the public :meth:`run_json`/``run_text`` surface can never
        inject ``--server``, ``--insecure-skip-tls-verify``, ``-n`` or any
        other flag.
        """
        if not isinstance(args, (list, tuple)) or not args:
            raise KubernetesHostError("kubectl arguments must be a non-empty list")
        for a in args:
            if not isinstance(a, str) or "\x00" in a:
                raise KubernetesHostError("kubectl arguments must be strings without NUL bytes")
            if a.startswith("-"):
                raise KubernetesHostError(f"Arbitrary kubectl flags are not permitted: {a!r}")

    def run_json(
        self,
        *,
        args: List[str],
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        self._validate_caller_args(args)
        return self._run_json(args=args, timeout=timeout)

    def run_text(
        self,
        *,
        args: List[str],
        timeout: Optional[float] = None,
        max_bytes: Optional[int] = None,
    ) -> str:
        self._validate_caller_args(args)
        return self._run_text(args=args, timeout=timeout, max_bytes=max_bytes)

    def _run_json(
        self,
        *,
        args: List[str],
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        completed = self._execute(args=args, timeout=timeout)
        stdout = completed.stdout or b""
        if len(stdout) > self.max_output_bytes:
            stdout = stdout[: self.max_output_bytes]
        text = stdout.decode("utf-8", "replace")
        if not text.strip():
            return []
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise KubernetesResponseError(f"Malformed kubectl JSON output: {_redact(text)}") from exc
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            # `kubectl get ... -o json` returns a List object; unwrap it so the
            # metadata mappers receive individual resources.
            items = payload.get("items")
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
            return [payload]
        raise KubernetesResponseError(f"Unexpected kubectl response shape: {type(payload)!r}")

    def _run_text(
        self,
        *,
        args: List[str],
        timeout: Optional[float] = None,
        max_bytes: Optional[int] = None,
    ) -> str:
        if max_bytes is None:
            max_bytes = self.max_output_bytes
        completed = self._execute(args=args, timeout=timeout)
        stdout = completed.stdout or b""
        if len(stdout) > max_bytes:
            stdout = stdout[:max_bytes]
        return stdout.decode("utf-8", "replace")

    def _execute(self, *, args: List[str], timeout: Optional[float] = None):
        if not isinstance(args, (list, tuple)) or not args:
            raise KubernetesHostError("kubectl arguments must be a non-empty list")
        for a in args:
            if not isinstance(a, str) or "\x00" in a:
                raise KubernetesHostError("kubectl arguments must be strings without NUL bytes")
        effective = timeout if timeout is not None else self.timeout_seconds
        if effective <= 0:
            effective = self.timeout_seconds
        cmd = self._build_args([str(a) for a in args])
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=effective,
            )
        except subprocess.TimeoutExpired as exc:
            raise KubernetesTimeoutError(
                f"kubectl command timed out after {effective}s"
            ) from exc
        except FileNotFoundError as exc:
            raise KubernetesUnavailableError() from exc
        except OSError as exc:
            raise KubernetesHostError(
                f"kubectl invocation failed: {_redact(str(exc))}", code="kubectl_io"
            ) from exc

        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        lowered = stderr.lower()
        if "unable to connect" in lowered or "could not find" in lowered or (
            "server" in lowered and "unavailable" in lowered
        ):
            raise KubernetesDaemonError(_redact(stderr or "Kubernetes API server is not reachable"))
        if "unauthorized" in lowered or ("forbidden" in lowered and "rbac" in lowered):
            raise KubernetesPermissionError(_redact(stderr or "Kubernetes RBAC authorization failed"))
        if "authentication" in lowered or "invalid credentials" in lowered:
            raise KubernetesAuthError(_redact(stderr or "Kubernetes authentication failed"))
        if completed.returncode != 0:
            raise KubernetesHostError(
                _redact(stderr.strip() or f"kubectl exited with status {completed.returncode}"),
                code="kubectl_failed",
            )
        return completed

    def version_json(self) -> Dict[str, Any]:
        args = ["version", "--output=json", "--request-timeout=5s"]
        out = self._run_json(args=args, timeout=10.0)
        if not out:
            raise KubernetesResponseError("Empty kubectl version response")
        merged: Dict[str, Any] = {}
        for item in out:
            merged.update({k: v for k, v in item.items() if isinstance(k, str)})
        return merged

    def current_context(self) -> str:
        out = self._run_text(args=["config", "current-context"], timeout=10.0).strip()
        if not out:
            raise KubernetesConfigError("No current kubectl context")
        return out

    def contexts(self) -> List[Dict[str, Any]]:
        return self._run_json(args=["config", "get-contexts", "--output=json"], timeout=10.0)

    def cluster_info_json(self) -> Dict[str, Any]:
        info = self._run_json(args=["cluster-info", "--output=json"], timeout=15.0)
        if not info:
            raise KubernetesResponseError("Empty cluster-info response")
        return info[0]

    def namespaces(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        args = ["get", "namespaces", "--output=json"]
        items = self._run_json(args=args, timeout=15.0)
        return items[: limit] if limit else items

    def resources(
        self,
        *,
        kind: str,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        args = ["get", kind, "--output=json"]
        if namespace:
            args += ["-n", namespace]
        if label_selector:
            args += ["-l", label_selector]
        if field_selector:
            args += ["--field-selector", field_selector]
        items = self._run_json(args=args, timeout=15.0)
        return items[: limit] if limit else items

    def resource(self, *, kind: str, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        args = ["get", kind, name, "--output=json"]
        if namespace:
            args += ["-n", namespace]
        items = self._run_json(args=args, timeout=15.0)
        if len(items) > 1:
            return items[0]
        if not items:
            raise KubernetesHostError(f"Resource not found: {kind}/{name}")
        return items[0]


# ---------------------------------------------------------------------------
# Redacted/metadata mappers
# ---------------------------------------------------------------------------


def _redact_cluster_info(info: Dict[str, Any]) -> Dict[str, Any]:
    safe = dict(info)
    for key in ("server", "certificateAuthorityData", "serviceAccount", "kubectl-streaming-location"):
        if key in safe:
            safe[key] = "<redacted>"
    return safe


def _metadata_only_namespace(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    labels = meta.get("labels") or {}
    annotations = meta.get("annotations") or {}
    if len(json.dumps(labels)) > 4096:
        labels = {"<labels_truncated>": True}
    if len(json.dumps(annotations)) > 4096:
        annotations = {"<annotations_truncated>": True}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": labels,
            "annotations": annotations,
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "phase": status.get("phase"),
        },
    }


def _bounded_status(item: Dict[str, Any], allowed_keys: frozenset) -> Dict[str, Any]:
    status = item.get("status") or {}
    return {k: v for k, v in status.items() if k in allowed_keys}


def _metadata_only_pod(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    labels = meta.get("labels") or {}
    annotations = meta.get("annotations") or {}
    if len(json.dumps(labels)) > 4096:
        labels = {"<labels_truncated>": True}
    if len(json.dumps(annotations)) > 4096:
        annotations = {"<annotations_truncated>": True}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": labels,
            "annotations": annotations,
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": _bounded_status(item, frozenset({
            "phase", "conditions", "ready", "containerStatuses",
            "startedAt", "finishedAt", "podIP", "hostIP",
            "qosClass", "reason", "message",
        })),
        "spec": {
            "containers": _bounded_containers(item),
            "nodeName": spec.get("nodeName"),
            "restartPolicy": spec.get("restartPolicy"),
        },
        "kind": item.get("kind") or "Pod",
    }


def _bounded_containers(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    spec = item.get("spec") or {}
    containers = spec.get("containers") or []
    out: List[Dict[str, Any]] = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        resources = c.get("resources") or {}
        limits = resources.get("limits") or {}
        requests = resources.get("requests") or {}
        out.append({
            "name": c.get("name"),
            "image": str(c.get("image")) if c.get("image") else None,
            "ready": None,
            "restarts": None,
            "limits": limits,
            "requests": requests,
            "state": None,
        })
    status_list = (item.get("status") or {}).get("containerStatuses") or []
    by_name: Dict[str, Dict[str, Any]] = {}
    for s in status_list:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if not name:
            continue
        state = (s.get("state") or {}).get("running") or (s.get("state") or {}).get("terminated") or (s.get("state") or {}).get("waiting")
        by_name[name] = {
            "ready": bool((s.get("ready") or [False])[0] if isinstance(s.get("ready"), list) else s.get("ready")),
            "restarts": s.get("restartCount"),
            "state": state,
        }
    for entry in out:
        name = entry.get("name")
        if name and name in by_name:
            entry["ready"] = by_name[name].get("ready")
            entry["restarts"] = by_name[name].get("restarts")
            entry["state"] = by_name[name].get("state")
    return out


def _metadata_only_deployment(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "replicas": status.get("replicas"),
            "readyReplicas": status.get("readyReplicas"),
            "availableReplicas": status.get("availableReplicas"),
            "unavailableReplicas": status.get("unavailableReplicas"),
            "updatedReplicas": status.get("updatedReplicas"),
            "conditions": _bounded_conditions(status),
        },
        "spec": {"replicas": spec.get("replicas")},
        "images": _deployment_images(item),
    }


def _bounded_labels(meta: Dict[str, Any]) -> Dict[str, Any]:
    labels = meta.get("labels") or {}
    if len(json.dumps(labels)) > 4096:
        return {"<labels_truncated>": True}
    return labels


def _bounded_annotations(meta: Dict[str, Any]) -> Dict[str, Any]:
    annotations = meta.get("annotations") or {}
    if len(json.dumps(annotations)) > 4096:
        return {"<annotations_truncated>": True}
    return annotations


def _bounded_conditions(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    conditions = status.get("conditions") or []
    out: List[Dict[str, Any]] = []
    for c in conditions:
        if not isinstance(c, dict):
            continue
        out.append({k: c[k] for k in ("type", "status", "reason", "message", "lastTransitionTime") if k in c})
    return out


def _deployment_images(item: Dict[str, Any]) -> List[str]:
    spec = item.get("spec") or {}
    template = (spec.get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    return [str(c.get("image")) for c in containers if isinstance(c, dict) and c.get("image")]


def _metadata_only_replicaset(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "replicas": status.get("replicas"),
            "readyReplicas": status.get("readyReplicas"),
            "availableReplicas": status.get("availableReplicas"),
            "fullyLabeledReplicas": status.get("fullyLabeledReplicas"),
        },
        "images": _replicaset_images(item),
    }


def _replicaset_images(item: Dict[str, Any]) -> List[str]:
    spec = item.get("spec") or {}
    template = (spec.get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    return [str(c.get("image")) for c in containers if isinstance(c, dict) and c.get("image")]


def _metadata_only_statefulset(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "replicas": status.get("replicas"),
            "readyReplicas": status.get("readyReplicas"),
            "currentReplicas": status.get("currentReplicas"),
            "updatedReplicas": status.get("updatedReplicas"),
        },
        "images": _statefulset_images(item),
    }


def _statefulset_images(item: Dict[str, Any]) -> List[str]:
    spec = item.get("spec") or {}
    template = (spec.get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    return [str(c.get("image")) for c in containers if isinstance(c, dict) and c.get("image")]


def _metadata_only_daemonset(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "currentNumberScheduled": status.get("currentNumberScheduled"),
            "numberAvailable": status.get("numberAvailable"),
            "numberReady": status.get("numberReady"),
            "desiredNumberScheduled": status.get("desiredNumberScheduled"),
        },
        "images": _daemonset_images(item),
    }


def _daemonset_images(item: Dict[str, Any]) -> List[str]:
    spec = item.get("spec") or {}
    template = (spec.get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    return [str(c.get("image")) for c in containers if isinstance(c, dict) and c.get("image")]


def _metadata_only_job(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "active": status.get("active"),
            "succeeded": status.get("succeeded"),
            "failed": status.get("failed"),
            "completionTime": status.get("completionTime"),
            "startTime": status.get("startTime"),
        },
        "spec": {"backoffLimit": spec.get("backoffLimit")},
        "images": _job_images(item),
    }


def _job_images(item: Dict[str, Any]) -> List[str]:
    spec = item.get("spec") or {}
    template = (spec.get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    return [str(c.get("image")) for c in containers if isinstance(c, dict) and c.get("image")]


def _metadata_only_cronjob(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    schedule = (spec.get("schedule") or "").strip()
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "status": {
            "active": status.get("active"),
            "lastScheduleTime": status.get("lastScheduleTime"),
        },
        "spec": {"schedule": schedule},
        "images": _cronjob_images(item),
    }


def _cronjob_images(item: Dict[str, Any]) -> List[str]:
    spec = item.get("spec") or {}
    job_template = (spec.get("jobTemplate") or {}).get("spec") or {}
    template = (job_template.get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    return [str(c.get("image")) for c in containers if isinstance(c, dict) and c.get("image")]


def _metadata_only_service(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "spec": {
            "type": spec.get("type"),
            "clusterIP": spec.get("clusterIP"),
            "ports": _bounded_ports(spec.get("ports") or []),
        },
        "status": {"loadBalancer": status.get("loadBalancer")},
    }


def _bounded_ports(ports: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in ports:
        if not isinstance(p, dict):
            continue
        entry: Dict[str, Any] = {}
        if "name" in p:
            entry["name"] = p["name"]
        if "port" in p:
            entry["port"] = p["port"]
        if "targetPort" in p:
            entry["targetPort"] = p["targetPort"]
        if "protocol" in p:
            entry["protocol"] = p["protocol"]
        if entry:
            out.append(entry)
    return out


def _metadata_only_ingress(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    rules = (spec.get("rules") or [])
    if len(json.dumps(rules)) > 4096:
        rules = [{"<rules_truncated>": True}]
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "spec": {
            "tls": _bounded_tls(spec.get("tls") or []),
            "rules": rules,
        },
        "status": {"loadBalancer": status.get("loadBalancer")},
    }


def _bounded_tls(tls: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in tls:
        if not isinstance(t, dict):
            continue
        entry: Dict[str, Any] = {}
        if "hosts" in t:
            entry["hosts"] = t["hosts"]
        if "secretName" in t:
            entry["secretName"] = t["secretName"]
        if entry:
            out.append(entry)
    return out


def _metadata_only_networkpolicy(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
    }


def _metadata_only_configmap(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    data = item.get("data") or {}
    redacted_data: Dict[str, Any] = {}
    for k, v in data.items():
        if _looks_like_secret(k) or _looks_like_secret(str(v)):
            redacted_data[k] = "<redacted>"
        else:
            redacted_data[k] = str(v)
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "data": redacted_data,
    }


def _looks_like_secret(value: str) -> bool:
    lowered = value.lower()
    return any(k in lowered for k in ("eyj", "token", "secret", "password", "credential", "authorization"))


def _metadata_only_secret(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    safe = _redact_secret_metadata(item)
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "data": safe.get("data"),
        "stringData": safe.get("stringData"),
        "type": item.get("type"),
    }


def _metadata_only_pvc(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "spec": {
            "accessModes": spec.get("accessModes"),
            "resources": spec.get("resources"),
            "storageClassName": spec.get("storageClassName"),
        },
        "status": {
            "phase": status.get("phase"),
            "accessModes": status.get("accessModes"),
            "capacity": status.get("capacity"),
        },
    }


def _metadata_only_pv(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "labels": _bounded_labels(meta),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
        "spec": {
            "capacity": spec.get("capacity"),
            "accessModes": spec.get("accessModes"),
            "persistentVolumeReclaimPolicy": spec.get("persistentVolumeReclaimPolicy"),
            "storageClassName": spec.get("storageClassName"),
            "hostPath": spec.get("hostPath"),
            "nfs": _bounded_nfs(spec.get("nfs") or {}),
            "cinder": _bounded_cinder(spec.get("cinder") or {}),
        },
        "status": {"phase": status.get("phase"), "capacity": status.get("capacity")},
    }


def _bounded_nfs(value: Dict[str, Any]) -> Dict[str, Any]:
    return {k: value[k] for k in ("server", "path") if k in value}


def _bounded_cinder(value: Dict[str, Any]) -> Dict[str, Any]:
    return {k: value[k] for k in ("volumeID", "fsType") if k in value}


def _metadata_only_storageclass(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") or {}
    return {
        "name": meta.get("name"),
        "metadata": {
            "name": meta.get("name"),
            "annotations": _bounded_annotations(meta),
            "resourceVersion": meta.get("resourceVersion"),
            "uid": meta.get("uid"),
        },
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class KubernetesHostAdapter(HostAdapter):
    host_type = "kubernetes"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._connected = False
        self._kubectl: Optional[_KubectlClient] = None
        self._selected_context: Optional[str] = None
        self._cluster_context_identity: Optional[str] = None
        self._kubectl_executable: Optional[str] = None
        self._namespace_allowlist: Tuple[str, ...] = ()
        self._cluster_scope_allowed = False
        self._default_namespace: Optional[str] = None
        self._last_health_latency_ms: Optional[int] = None
        self._cluster_info_cache: Dict[str, Any] = {}
        self._kubeconfig: Optional[str] = None
        self._config_parsed = False

    # ------------------------------------------------------------------ config

    def _parse_config(self) -> None:
        metadata = dict(self.config.metadata or {})
        kubeconfig_raw = metadata.get("kubeconfig")
        kubeconfig = _validate_kubeconfig_path(kubeconfig_raw)
        context = metadata.get("context") or metadata.get("kubernetes_context")
        context = _validate_context(context) if context else None
        namespace = metadata.get("namespace") or metadata.get("kubernetes_namespace")
        namespace = _validate_namespace(namespace) if namespace else None
        allowlist_raw = metadata.get("namespace_allowlist")
        allowlist: Tuple[str, ...] = ()
        if allowlist_raw:
            if not isinstance(allowlist_raw, (list, tuple)):
                raise KubernetesConfigError("namespace_allowlist must be a list")
            allowlist = tuple(_validate_namespace(n) for n in allowlist_raw)
        cluster_scope_raw = metadata.get("cluster_scope_allowed")
        cluster_scope_allowed = bool(cluster_scope_raw) if cluster_scope_raw is not None else False
        default_namespace_raw = metadata.get("default_namespace")
        default_namespace = _validate_namespace(default_namespace_raw) if default_namespace_raw else None

        # The kubectl binary is resolved lazily at connect/validate time so that
        # configuration parsing stays offline-testable; the adapter still fails
        # closed with KubernetesUnavailableError before any cluster operation.
        self._kubeconfig = kubeconfig
        self._selected_context = context
        self._default_namespace = namespace
        self._namespace_allowlist = allowlist
        self._cluster_scope_allowed = cluster_scope_allowed
        self._cluster_context_identity = None
        self._config_parsed = True

        if allowlist and namespace and namespace not in allowlist:
            raise KubernetesHostError(
                f"Selected namespace is not in the configured allowlist: {namespace!r}"
            )

    def _ensure_kubectl(self) -> str:
        if self._kubectl_executable:
            return self._kubectl_executable
        binary = shutil.which("kubectl")
        if not binary:
            raise KubernetesUnavailableError()
        self._kubectl_executable = binary
        return binary

    def _build_client(self, *, context: Optional[str] = None, namespace: Optional[str] = None) -> _KubectlClient:
        return _KubectlClient(
            executable=self._ensure_kubectl(),
            timeout_seconds=self.config.timeout_seconds,
            max_output_bytes=min(
                getattr(self.config, "max_file_bytes", _MAX_OUTPUT_BYTES_DEFAULT) or _MAX_OUTPUT_BYTES_DEFAULT,
                _MAX_OUTPUT_BYTES_DEFAULT,
            ),
            kubeconfig=self._kubeconfig,
            context=context or self._selected_context,
            namespace=namespace or self._effective_namespace(),
        )

    # ------------------------------------------------------------------ lifecycle

    def validate(self) -> None:
        self._parse_config()
        self._ensure_kubectl()
        client = self._build_client()
        self._kubectl = client
        version = client.version_json()
        server_version = version.get("serverVersion", {})
        if not server_version:
            raise KubernetesDaemonError("Kubernetes API server could not be identified")
        if self._selected_context:
            current = client.current_context()
            if current != self._selected_context:
                raise KubernetesContextError(
                    f"Explicit context mismatch: requested {self._selected_context!r}, "
                    f"current {current!r}"
                )
        cluster_info = client.cluster_info_json()
        self._cluster_info_cache = cluster_info
        self._cluster_context_identity = self._identity_from(server_version, cluster_info)

    @staticmethod
    def _identity_from(server_version: Dict[str, Any], cluster_info: Dict[str, Any]) -> str:
        """Stable cluster identity for one pinned context.

        The server version is informational only (older/newer kubectl clients
        may omit it), so identity is derived from the cluster endpoint set that
        ``cluster-info`` reports, plus the pinned context name when present.
        """
        # ``server_version`` is accepted for call-site symmetry but deliberately
        # excluded: a client upgrade must not masquerade as a cluster switch.
        del server_version
        contents = json.dumps(cluster_info or {}, sort_keys=True, default=str)
        return hashlib.sha256(contents.encode("utf-8")).hexdigest()[:32]

    def _context_identity(self, client: _KubectlClient) -> str:
        version = client.version_json()
        server = version.get("serverVersion", {})
        cluster_info = client.cluster_info_json()
        return self._identity_from(server, cluster_info)

    def connect(self) -> None:
        self.validate()
        client = self._kubectl
        if client is None:
            client = self._build_client()
            self._kubectl = client
        self._selected_context = client.context if client.context else client.current_context()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._kubectl = None
        self._selected_context = None
        self._cluster_context_identity = None
        self._cluster_info_cache = {}

    def health(self) -> HostHealth:
        if not self._connected:
            return HostHealth(
                state=HealthState.UNKNOWN,
                checked_at=datetime.datetime.now(timezone.utc),
                issues=("not connected",),
            )
        if self._kubectl is None:
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=datetime.datetime.now(timezone.utc),
                issues=("session lost",),
            )
        start = time.monotonic()
        try:
            cluster_info = self._kubectl.cluster_info_json()
            self._cluster_info_cache = cluster_info
            if self._cluster_context_identity:
                cached_version = {
                    "gitVersion": (cluster_info.get("serverVersion", {}) or {}).get("gitVersion", "")
                }
                current_identity = self._identity_from(cached_version, cluster_info)
                if current_identity != self._cluster_context_identity:
                    return HostHealth(
                        state=HealthState.DEGRADED,
                        checked_at=datetime.datetime.now(timezone.utc),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        issues=("cluster identity changed during session",),
                    )
        except KubernetesHostError as exc:
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=datetime.datetime.now(timezone.utc),
                latency_ms=int((time.monotonic() - start) * 1000),
                issues=(str(exc),),
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        self._last_health_latency_ms = latency_ms
        return HostHealth(
            state=HealthState.HEALTHY,
            checked_at=datetime.datetime.now(timezone.utc),
            latency_ms=latency_ms,
        )

    def capabilities(self) -> Dict[str, str]:
        return {
            "repository_metadata_read": "unsupported",
            "repository_structure_read": "unsupported",
            "branch_read": "unsupported",
            "commit_read": "unsupported",
            "file_read": "unsupported",
            "file_write": "unsupported",
            "workspace_read": "supported",
            "workspace_create": "unsupported",
            "workspace_delete": "unsupported",
            "workspace_start": "unsupported",
            "workspace_stop": "unsupported",
            "workspace_exec": "unsupported",
            "workspace_execute": "unsupported",
            "testing": "supported",
            "repository_write": "unsupported",
            "pull_request_create": "unsupported",
            "pull_request_merge": "unsupported",
            "push": "unsupported",
            "fetch": "unsupported",
            "clone": "unsupported",
            "file_system_access": "unsupported",
            "branch_create": "unsupported",
            "branch_delete": "unsupported",
            "commit_create": "unsupported",
            "deployment": "unsupported",
            "namespace_list": "supported",
            "namespace_metadata": "supported",
            "pod_list": "supported",
            "pod_metadata": "supported",
            "pod_logs": "supported_limited",
            "deployment_list": "supported",
            "deployment_metadata": "supported",
            "replicaset_list": "supported",
            "replicaset_metadata": "supported",
            "statefulset_list": "supported",
            "statefulset_metadata": "supported",
            "daemonset_list": "supported",
            "daemonset_metadata": "supported",
            "job_list": "supported",
            "job_metadata": "supported",
            "cronjob_list": "supported",
            "cronjob_metadata": "supported",
            "service_list": "supported",
            "service_metadata": "supported",
            "ingress_list": "supported",
            "ingress_metadata": "supported",
            "networkpolicy_list": "supported",
            "networkpolicy_metadata": "supported",
            "configmap_metadata": "supported",
            "secret_metadata": "supported_only_metadata",
            "pvc_list": "supported",
            "pvc_metadata": "supported",
            "pv_metadata": "supported",
            "storageclass_list": "supported",
            "storageclass_metadata": "supported",
            "cluster_info": "supported",
        }

    # ------------------------------------------------------------------ namespace scope

    def _effective_namespace(self) -> Optional[str]:
        if self._default_namespace:
            return self._default_namespace
        if self._namespace_allowlist:
            if len(self._namespace_allowlist) == 1:
                return self._namespace_allowlist[0]
            return None
        return None

    def _assert_namespace_scoped(self, namespace: Optional[str]) -> None:
        effective = namespace or self._effective_namespace()
        if effective is None and not self._cluster_scope_allowed:
            raise KubernetesHostError(
                "This adapter is namespace-scoped by default. Provide an explicit namespace "
                "or an approved namespace allowlist."
            )
        if effective and self._namespace_allowlist and effective not in self._namespace_allowlist:
            raise KubernetesHostError(
                f"Namespace is not permitted by the configured allowlist: {effective!r}"
            )

    # ------------------------------------------------------------------ describe

    def describe(self) -> dict:
        if not self._config_parsed:
            self._parse_config()
        return {
            "host_type": self.host_type,
            "root": self.config.root,
            "capabilities": self.capabilities(),
            "metadata": {
                "kubectl_executable": self._kubectl_executable,
                "kubeconfig": self._kubeconfig,
                "selected_context": self._selected_context,
                "namespace_allowlist": list(self._namespace_allowlist),
                "default_namespace": self._default_namespace,
                "cluster_scope_allowed": self._cluster_scope_allowed,
                "read_only": True,
                "tls_verification_enforced": True,
                "insecure_skip_tls_verify_denied": True,
            },
        }

    # ------------------------------------------------------------------ discovery

    def cluster_info(self) -> Dict[str, Any]:
        if not self._connected:
            raise KubernetesHostError("Adapter is not connected", code="not_connected")
        client = self._client_for_namespace()
        return _redact_cluster_info(client.cluster_info_json())

    def namespace_list(self, *, namespace: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if namespace is None and not self._cluster_scope_allowed:
            # Cluster-wide namespace listing is never implicit: only the
            # configured allowlist is enumerable without explicit cluster scope.
            return self._namespace_allowlist_list(limit=limit)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.namespaces(limit=limit)
        return [_metadata_only_namespace(item) for item in items]

    def _namespace_allowlist_list(self, *, limit: Optional[int]) -> List[Dict[str, Any]]:
        if not self._namespace_allowlist:
            return []
        allowed = [
            {"name": name, "metadata": {"name": name, "labels": {}, "annotations": {}}}
            for name in self._namespace_allowlist
        ]
        if limit is not None:
            allowed = allowed[:limit]
        return allowed

    def namespace_metadata(self, *, name: str) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(name)
        if self._namespace_allowlist and name not in self._namespace_allowlist:
            raise KubernetesHostError(f"Namespace is not permitted by the allowlist: {name!r}")
        client = self._client_for_namespace(namespace=name)
        item = client.resource(kind="namespaces", name=name)
        return _metadata_only_namespace(item)

    # ------------------------------------------------------------------ workloads

    def pod_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="pods",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_pod(item) for item in items]

    def pod_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_pod(client.resource(kind="pods", name=name, namespace=namespace or self._effective_namespace()))

    def deployment_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="deployments",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_deployment(item) for item in items]

    def deployment_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_deployment(client.resource(kind="deployments", name=name, namespace=namespace or self._effective_namespace()))

    def replicaset_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="replicasets",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_replicaset(item) for item in items]

    def replicaset_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_replicaset(client.resource(kind="replicasets", name=name, namespace=namespace or self._effective_namespace()))

    def statefulset_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="statefulsets",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_statefulset(item) for item in items]

    def statefulset_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_statefulset(client.resource(kind="statefulsets", name=name, namespace=namespace or self._effective_namespace()))

    def daemonset_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="daemonsets",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_daemonset(item) for item in items]

    def daemonset_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_daemonset(client.resource(kind="daemonsets", name=name, namespace=namespace or self._effective_namespace()))

    def job_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="jobs",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_job(item) for item in items]

    def job_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_job(client.resource(kind="jobs", name=name, namespace=namespace or self._effective_namespace()))

    def cronjob_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="cronjobs",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_cronjob(item) for item in items]

    def cronjob_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_cronjob(client.resource(kind="cronjobs", name=name, namespace=namespace or self._effective_namespace()))

    # ------------------------------------------------------------------ networking

    def service_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="services",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_service(item) for item in items]

    def service_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_service(client.resource(kind="services", name=name, namespace=namespace or self._effective_namespace()))

    def ingress_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="ingresses",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_ingress(item) for item in items]

    def ingress_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_ingress(client.resource(kind="ingresses", name=name, namespace=namespace or self._effective_namespace()))

    def networkpolicy_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="networkpolicies",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_networkpolicy(item) for item in items]

    def networkpolicy_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_networkpolicy(client.resource(kind="networkpolicies", name=name, namespace=namespace or self._effective_namespace()))

    # ------------------------------------------------------------------ config/secret metadata

    def configmap_metadata_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="configmaps",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_configmap(item) for item in items]

    def configmap_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_configmap(client.resource(kind="configmaps", name=name, namespace=namespace or self._effective_namespace()))

    def secret_metadata_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="secrets",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_secret(item) for item in items]

    def secret_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_secret(client.resource(kind="secrets", name=name, namespace=namespace or self._effective_namespace()))

    # ------------------------------------------------------------------ storage

    def pvc_list(
        self,
        *,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        label_selector = _validate_label_selector(label_selector)
        field_selector = _validate_label_selector(field_selector)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        items = client.resources(
            kind="persistentvolumeclaims",
            namespace=namespace or self._effective_namespace(),
            label_selector=label_selector,
            field_selector=field_selector,
            limit=limit,
        )
        return [_metadata_only_pvc(item) for item in items]

    def pvc_metadata(self, *, name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        _validate_resource_name(name)
        self._assert_namespace_scoped(namespace)
        client = self._client_for_namespace(namespace=namespace)
        return _metadata_only_pvc(client.resource(kind="persistentvolumeclaims", name=name, namespace=namespace or self._effective_namespace()))

    def pv_list(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if not self._cluster_scope_allowed:
            raise KubernetesHostError("PersistentVolume listing is cluster-scoped and not permitted")
        client = self._client_for_namespace()
        items = client.resources(kind="persistentvolumes", limit=limit)
        return [_metadata_only_pv(item) for item in items]

    def pv_metadata(self, *, name: str) -> Dict[str, Any]:
        _validate_resource_name(name)
        if not self._cluster_scope_allowed:
            raise KubernetesHostError("PersistentVolume metadata is cluster-scoped and not permitted")
        client = self._client_for_namespace()
        return _metadata_only_pv(client.resource(kind="persistentvolumes", name=name))

    def storageclass_list(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if not self._cluster_scope_allowed:
            raise KubernetesHostError("StorageClass listing is cluster-scoped and not permitted")
        client = self._client_for_namespace()
        items = client.resources(kind="storageclasses", limit=limit)
        return [_metadata_only_storageclass(item) for item in items]

    def storageclass_metadata(self, *, name: str) -> Dict[str, Any]:
        _validate_resource_name(name)
        if not self._cluster_scope_allowed:
            raise KubernetesHostError("StorageClass metadata is cluster-scoped and not permitted")
        client = self._client_for_namespace()
        return _metadata_only_storageclass(client.resource(kind="storageclasses", name=name))

    # ------------------------------------------------------------------ pod logs (bounded)

    def pod_log(
        self,
        *,
        name: str,
        namespace: Optional[str] = None,
        container: Optional[str] = None,
        tail_lines: Optional[int] = None,
        max_bytes: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        _validate_resource_name(name)
        if container is not None:
            _validate_resource_name(container)
        namespace = _validate_namespace(namespace) if namespace else self._default_namespace
        self._assert_namespace_scoped(namespace)
        if namespace is None:
            raise KubernetesHostError("pod log requires an explicit namespace for this adapter")
        effective_max = max_bytes if max_bytes is not None else self.config.max_file_bytes
        effective_max = _validate_output_limit(effective_max)
        tail = tail_lines if tail_lines is not None else 200
        tail = int(tail)
        if tail < 1 or tail > 5000:
            raise KubernetesHostError("pod log tail_lines must be between 1 and 5000")
        client = self._client_for_namespace(namespace=namespace)
        args = ["logs", name]
        if container:
            args += ["-c", container]
        args += ["--tail", str(tail), "--limit-bytes", str(effective_max)]
        raw = client._run_text(
            args=args,
            timeout=timeout or self.config.timeout_seconds,
            max_bytes=effective_max,
        )
        truncated = len(raw) >= effective_max and not raw.endswith("\n")
        raw_lines = raw.splitlines()[:tail]
        log_lines = [_redact_log_line(line) for line in raw_lines]
        log_text = "\n".join(log_lines)
        return {
            "pod": name,
            "namespace": namespace,
            "container": container,
            "lines": log_lines,
            "log": log_text,
            "line_count": len(log_lines),
            "bytes": len(log_text.encode("utf-8")),
            "tail_lines": tail,
            "truncated": truncated,
            "max_bytes": effective_max,
        }

    # ------------------------------------------------------------------ internal helpers

    def _client_for_namespace(self, *, namespace: Optional[str] = None) -> _KubectlClient:
        if self._kubectl is None:
            raise KubernetesHostError("Adapter is not connected", code="not_connected")
        effective = namespace or self._effective_namespace()
        if self._kubectl.context == self._selected_context and self._kubectl.namespace == effective:
            client = self._kubectl
        else:
            client = self._build_client(context=self._selected_context, namespace=effective)
        self._kubectl = client
        return client

    def set_kubectl_executable_override(self, path: str) -> None:
        if path:
            self._kubectl_executable = path

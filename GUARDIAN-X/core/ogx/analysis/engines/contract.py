"""Stable engine contract for the universal analysis layer.

Every engine exposes the same operations and returns a machine-readable
capability status.  Engines are passive by default: analysis must not execute
target-project code, install dependencies, or contact the network.

Status vocabulary (do not extend without a schema update):

* ``supported``       implemented and exercised by tests for the stated scope
* ``partial``         implemented with documented limits or heuristic output
* ``not_configured``  implementation exists but an external tool/SDK is absent
* ``unsupported``     no implementation for this operation
* ``failed``          the operation raised or could not complete
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ogx.kernel.models import Finding, ProjectProfile
from ogx.kernel.workspace import Workspace, WorkspaceError


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    NOT_CONFIGURED = "not_configured"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


CAPABILITY_NAMES = ("detect", "analyze", "dependencies", "build", "test", "diagnose")


@dataclass(slots=True)
class Capability:
    status: CapabilityStatus
    detail: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "detail": self.detail, "evidence": list(self.evidence)}


@dataclass(slots=True)
class EngineContext:
    workspace: Workspace
    profile: ProjectProfile
    project_id: str
    fingerprint: dict[str, Any]
    log_paths: tuple[Path, ...] = ()

    @property
    def root(self) -> Path:
        return self.workspace.root

    @property
    def config(self):
        return self.workspace.config

    def read_text(self, rel: str, *, max_bytes: int | None = None) -> str:
        return self.workspace.read_text(rel, max_bytes=max_bytes)


@dataclass(slots=True)
class EngineResult:
    engine_id: str
    engine_version: str
    display_name: str
    detected: bool
    capabilities: dict[str, Capability]
    findings: list[Finding] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    mode: str = "static-read-only"
    execution_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "display_name": self.display_name,
            "detected": self.detected,
            "capabilities": {name: capability.to_dict() for name, capability in self.capabilities.items()},
            "findings": [
                {
                    "rule_id": item.rule_id, "title": item.title, "severity": item.severity,
                    "category": item.category, "message": item.message, "path": item.path,
                    "line": item.line, "evidence": item.evidence,
                    "recommendation": item.recommendation, "confidence": item.confidence,
                }
                for item in self.findings
            ],
            "facts": self.facts,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "duration_ms": self.duration_ms,
            "mode": self.mode,
            "execution_performed": self.execution_performed,
        }


class BaseEngine:
    """Base class implementing contract enforcement and failure isolation."""

    engine_id = "base"
    engine_version = "1.0.0"
    display_name = "Base engine"
    language_keys: tuple[str, ...] = ()
    category = "generic"

    def detect(self, context: EngineContext) -> bool:
        raise NotImplementedError

    def capabilities(self, context: EngineContext) -> dict[str, Capability]:
        return {
            name: Capability(CapabilityStatus.UNSUPPORTED, "No operation is implemented by this engine.")
            for name in CAPABILITY_NAMES
        }

    def health(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "version": self.engine_version,
            "status": "ok",
            "mode": "static-read-only",
            "execution_performed": False,
            "network_access": False,
        }

    # -- operations -------------------------------------------------------
    def analyze(self, context: EngineContext) -> dict[str, Any]:
        return {"findings": [], "facts": {}, "warnings": []}

    def dependencies(self, context: EngineContext) -> dict[str, Any]:
        return {"components": [], "warnings": []}

    def build(self, context: EngineContext) -> dict[str, Any]:
        return {"discovered": False, "executed": False, "reason": "No build discovery is implemented."}

    def test(self, context: EngineContext) -> dict[str, Any]:
        return {"discovered": False, "executed": False, "reason": "No test discovery is implemented."}

    def diagnose(self, context: EngineContext) -> dict[str, Any]:
        return {"diagnostics": [], "reason": "No language-specific diagnostics are implemented."}

    # -- orchestration ----------------------------------------------------
    def evaluate(self, context: EngineContext) -> EngineResult:
        started = time.perf_counter()
        try:
            detected = bool(self.detect(context))
        except Exception as exc:  # pragma: no cover - defensive boundary
            result = EngineResult(
                engine_id=self.engine_id, engine_version=self.engine_version,
                display_name=self.display_name, detected=False,
                capabilities={name: Capability(CapabilityStatus.FAILED, "Detection failed.")
                              for name in CAPABILITY_NAMES},
                errors=[f"detect: {type(exc).__name__}"],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result

        capability_error: str | None = None
        try:
            capabilities = self.capabilities(context)
        except Exception as exc:  # pragma: no cover - defensive boundary
            capabilities = {name: Capability(CapabilityStatus.FAILED, "Capability negotiation failed.")
                            for name in CAPABILITY_NAMES}
            capabilities["detect"] = Capability(
                CapabilityStatus.SUPPORTED if detected else CapabilityStatus.UNSUPPORTED,
                "Detection completed; capability negotiation failed.",
            )
            capability_error = f"capabilities: {type(exc).__name__}"

        operations = {
            "analyze": self.analyze,
            "dependencies": self.dependencies,
            "build": self.build,
            "test": self.test,
            "diagnose": self.diagnose,
        }
        findings: list[Finding] = []
        try:
            signals = self._signals(context)
        except Exception:  # pragma: no cover - defensive boundary
            signals = {"unavailable": True}
        facts: dict[str, Any] = {"detection": {"detected": detected, "signals": signals}}
        warnings: list[str] = []
        errors: list[str] = []
        if capability_error:
            errors.append(capability_error)

        if detected:
            for name, operation in operations.items():
                capability = capabilities.get(name)
                if capability is None or capability.status in {CapabilityStatus.UNSUPPORTED}:
                    continue
                try:
                    payload = operation(context)
                except (WorkspaceError, OSError, ValueError, KeyError, TypeError) as exc:
                    capabilities[name] = Capability(
                        CapabilityStatus.FAILED,
                        f"{type(exc).__name__}: {str(exc)[:300]}",
                        capability.evidence,
                    )
                    errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:300]}")
                    continue
                except Exception as exc:  # pragma: no cover - defensive boundary
                    capabilities[name] = Capability(
                        CapabilityStatus.FAILED,
                        f"{type(exc).__name__}: operation raised unexpectedly",
                        capability.evidence,
                    )
                    errors.append(f"{name}: {type(exc).__name__}")
                    continue
                if not isinstance(payload, dict):
                    capabilities[name] = Capability(CapabilityStatus.FAILED, "Operation returned an invalid payload.")
                    errors.append(f"{name}: invalid payload type")
                    continue
                operation_findings = payload.pop("findings", [])
                if isinstance(operation_findings, list):
                    findings.extend(item for item in operation_findings if isinstance(item, Finding))
                facts[name] = payload
                operation_warnings = payload.get("warnings", [])
                if isinstance(operation_warnings, list):
                    warnings.extend(str(item) for item in operation_warnings)

        return EngineResult(
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            display_name=self.display_name,
            detected=detected,
            capabilities=capabilities,
            findings=findings,
            facts=facts,
            warnings=warnings,
            errors=errors,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _signals(self, context: EngineContext) -> dict[str, Any]:
        return {}

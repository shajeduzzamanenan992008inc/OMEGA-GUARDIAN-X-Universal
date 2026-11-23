"""Unified finding and evidence schema shared by every engine.

The legacy :class:`ogx.models.Finding` remains the internal scanner record for
backward compatibility.  :class:`UnifiedFinding` adds engine provenance,
stable IDs, verification state and explicit evidence classification without
changing existing report formats.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Finding, utc_now
from .rules import redact_evidence

VERIFICATION_STATES = ("observed", "inferred", "ai_suggested", "verified")
SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass(slots=True)
class UnifiedFinding:
    finding_id: str
    project_id: str
    engine_id: str
    engine_version: str
    rule_id: str
    category: str
    severity: str
    confidence: str
    title: str
    description: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    evidence: str | None = None
    component: str | None = None
    recommendation: str | None = None
    remediation_proposal: str | None = None
    verification_status: str = "inferred"
    observed_at: str = field(default_factory=utc_now)
    also_reported_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def finding_id_for(engine_id: str, rule_id: str, path: str | None, line: int | None,
                   title: str, description: str) -> str:
    material = "\x00".join((
        engine_id, rule_id, path or "", str(line or 0), title, description,
    ))
    return "ogx-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def unify(finding: Finding, *, project_id: str, engine_id: str, engine_version: str,
          verification_status: str = "inferred", column: int | None = None,
          component: str | None = None,
          remediation_proposal: str | None = None) -> UnifiedFinding:
    if verification_status not in VERIFICATION_STATES:
        raise ValueError(f"Unsupported verification state: {verification_status}")
    if finding.severity not in SEVERITIES:
        raise ValueError(f"Unsupported severity: {finding.severity}")
    description = redact_evidence(finding.message)
    evidence = redact_evidence(finding.evidence) if finding.evidence else None
    recommendation = redact_evidence(finding.recommendation) if finding.recommendation else None
    return UnifiedFinding(
        finding_id=finding_id_for(engine_id, finding.rule_id, finding.path, finding.line,
                                  finding.title, description),
        project_id=project_id,
        engine_id=engine_id,
        engine_version=engine_version,
        rule_id=finding.rule_id,
        category=finding.category,
        severity=finding.severity,
        confidence=finding.confidence,
        title=finding.title,
        description=description,
        path=finding.path,
        line=finding.line,
        column=column,
        evidence=evidence,
        component=component or _component_for(finding.path),
        recommendation=recommendation,
        remediation_proposal=remediation_proposal,
        verification_status=verification_status,
    )


def _component_for(path: str | None) -> str | None:
    if not path:
        return None
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if len(parts) > 1:
        return parts[0]
    return "repository-root"


def dedupe(findings: list[UnifiedFinding]) -> list[UnifiedFinding]:
    unique: dict[str, UnifiedFinding] = {}
    for finding in findings:
        existing = unique.get(finding.finding_id)
        if existing is None or _confidence_rank(finding.confidence) > _confidence_rank(existing.confidence):
            unique[finding.finding_id] = finding
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    return sorted(
        unique.values(),
        key=lambda item: (order.get(item.severity, 99), item.path or "", item.line or 0,
                          item.rule_id, item.finding_id),
    )


def _confidence_rank(value: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(value, 0)


def severity_summary(findings: list[UnifiedFinding]) -> dict[str, int]:
    summary = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        if finding.severity in summary:
            summary[finding.severity] += 1
    return summary

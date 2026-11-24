from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ProjectProfile:
    root: str
    name: str
    languages: list[str] = field(default_factory=list)
    ecosystems: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    ci_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Finding:
    rule_id: str
    title: str
    severity: str
    category: str
    message: str
    path: str | None = None
    line: int | None = None
    evidence: str | None = None
    recommendation: str | None = None
    confidence: str = "medium"


@dataclass(slots=True)
class ScanReport:
    tool: str
    version: str
    scan_id: str
    started_at: str
    finished_at: str
    project: ProjectProfile
    files_examined: int
    files_skipped: int
    findings: list[Finding]
    capabilities: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        result = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for finding in self.findings:
            key = finding.severity.lower()
            if key in result:
                result[key] += 1
        return result

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["summary"] = self.summary
        return data


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()

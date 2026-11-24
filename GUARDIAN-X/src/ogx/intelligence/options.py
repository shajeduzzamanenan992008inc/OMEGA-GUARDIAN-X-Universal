"""Analysis options, resource limits and shared analyzer state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import ScanConfig
from ..models import ProjectProfile
from ..workspace import Workspace, WorkspaceError
from .models import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisLimitation,
    AnalysisRecommendation,
    ArchitectureIndicator,
    BuildSystemProfile,
    Confidence,
    ConfigurationProfile,
    DependencyProfile,
    DocumentationProfile,
    EngineeringHealthProfile,
    EntryPoint,
    FrameworkProfile,
    LanguageProfile,
    Priority,
    ProjectClassification,
    ProjectStructure,
    TechnologyProfile,
    TestingProfile,
)

ANALYZER_NAMES: tuple[str, ...] = (
    "language",
    "dependencies",
    "structure",
    "entrypoints",
    "frameworks",
    "build",
    "testing",
    "documentation",
    "configuration",
    "architecture",
    "health",
)


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    """Bounded, local-only analysis options.

    ``excludes`` are directory names, matching ``Workspace`` semantics.  No
    option can enable project execution, package installation or network use.
    """

    excludes: tuple[str, ...] = ()
    max_files: int = 12_000
    max_file_bytes: int = 2_000_000
    max_manifest_bytes: int = 4_000_000
    max_depth: int = 6
    max_tree_entries: int = 400
    max_dependencies: int = 5_000
    max_entry_point_files: int = 300
    max_analyzed_files: int = 2_000
    deadline_seconds: float = 60.0
    analyzers: tuple[str, ...] = ()
    registry_lookup: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= 1_000_000:
            raise ValueError("max_files must be between 1 and 1,000,000")
        if not 1_024 <= self.max_file_bytes <= 100_000_000:
            raise ValueError("max_file_bytes must be between 1 KB and 100 MB")
        if not 1_024 <= self.max_manifest_bytes <= 100_000_000:
            raise ValueError("max_manifest_bytes must be between 1 KB and 100 MB")
        if not 0 <= self.max_depth <= 20:
            raise ValueError("max_depth must be between 0 and 20")
        if not 1 <= self.max_tree_entries <= 10_000:
            raise ValueError("max_tree_entries must be between 1 and 10,000")
        if not 1 <= self.max_dependencies <= 100_000:
            raise ValueError("max_dependencies must be between 1 and 100,000")
        if not 1 <= self.max_entry_point_files <= 10_000:
            raise ValueError("max_entry_point_files must be between 1 and 10,000")
        if not 1 <= self.max_analyzed_files <= 50_000:
            raise ValueError("max_analyzed_files must be between 1 and 50,000")
        if not 1 <= self.deadline_seconds <= 600:
            raise ValueError("deadline_seconds must be between 1 and 600")
        unknown = sorted(set(self.analyzers) - set(ANALYZER_NAMES))
        if unknown:
            raise ValueError(f"Unknown analyzer(s): {', '.join(unknown)}")
        if not all(isinstance(name, str) and name for name in self.excludes):
            raise ValueError("excludes must be directory names")

    def scan_config(self) -> ScanConfig:
        if not self.excludes:
            return ScanConfig(max_files=self.max_files, max_file_bytes=self.max_file_bytes)
        return ScanConfig(
            excludes=sorted(set(ScanConfig().excludes) | set(self.excludes)),
            max_files=self.max_files,
            max_file_bytes=self.max_file_bytes,
        )

    def selected_analyzers(self) -> tuple[str, ...]:
        return self.analyzers or ANALYZER_NAMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "excludes": list(self.excludes),
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_manifest_bytes": self.max_manifest_bytes,
            "max_depth": self.max_depth,
            "max_tree_entries": self.max_tree_entries,
            "max_dependencies": self.max_dependencies,
            "max_entry_point_files": self.max_entry_point_files,
            "max_analyzed_files": self.max_analyzed_files,
            "deadline_seconds": self.deadline_seconds,
            "analyzers": list(self.selected_analyzers()),
            "registry_lookup": self.registry_lookup,
        }


@dataclass(slots=True)
class AnalysisContext:
    """Shared state passed to analyzers in a fixed dependency order."""

    workspace: Workspace
    profile: ProjectProfile
    options: AnalysisOptions
    project_id: str
    registry_project_id: str | None = None
    started: float = field(default_factory=time.monotonic)
    languages: list[LanguageProfile] = field(default_factory=list)
    frameworks: list[FrameworkProfile] = field(default_factory=list)
    technologies: list[TechnologyProfile] = field(default_factory=list)
    dependencies: DependencyProfile = field(default_factory=DependencyProfile)
    structure: ProjectStructure = field(default_factory=ProjectStructure)
    entry_points: list[EntryPoint] = field(default_factory=list)
    architecture: list[ArchitectureIndicator] = field(default_factory=list)
    build_systems: list[BuildSystemProfile] = field(default_factory=list)
    testing: TestingProfile = field(default_factory=TestingProfile)
    documentation: DocumentationProfile = field(default_factory=DocumentationProfile)
    configuration: ConfigurationProfile = field(default_factory=ConfigurationProfile)
    health: EngineeringHealthProfile = field(default_factory=EngineeringHealthProfile)
    classification: ProjectClassification | None = None
    findings: list[AnalysisFinding] = field(default_factory=list)
    recommendations: list[AnalysisRecommendation] = field(default_factory=list)
    limitations: list[AnalysisLimitation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bytes_read: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.workspace.root

    @property
    def config(self) -> ScanConfig:
        return self.workspace.config

    def expired(self) -> bool:
        return (time.monotonic() - self.started) > self.options.deadline_seconds

    def read_text(self, rel: str, *, max_bytes: int | None = None) -> str | None:
        """Read a bounded text file, recording failures instead of raising."""
        entry = self.workspace.entry(rel)
        if entry is None:
            return None
        limit = max_bytes if max_bytes is not None else self.options.max_file_bytes
        if entry.size > limit:
            self.warnings.append(f"Skipped oversized file: {rel}")
            return None
        budget = self.options.max_analyzed_files * 65_536
        if self.bytes_read + entry.size > budget:
            self.warnings.append("Analysis read budget reached; remaining file reads were skipped.")
            return None
        try:
            text = self.workspace.read_text(rel, max_bytes=limit)
        except WorkspaceError:
            return None
        self.bytes_read += len(text.encode("utf-8", errors="replace"))
        return text

    def add_finding(self, finding: AnalysisFinding) -> None:
        if len(self.findings) < 500:
            self.findings.append(finding)

    def add_recommendation(self, recommendation: AnalysisRecommendation) -> None:
        key = (recommendation.category, recommendation.action)
        if any((item.category, item.action) == key for item in self.recommendations):
            return
        if len(self.recommendations) < 200:
            self.recommendations.append(recommendation)

    def add_limitation(self, area: str, detail: str) -> None:
        if any(item.area == area and item.detail == detail for item in self.limitations):
            return
        self.limitations.append(AnalysisLimitation(area, detail))

    def finding(self, finding_id: str, title: str, category: str,
                confidence: Confidence, method: str,
                evidence: list[AnalysisEvidence] | None = None,
                limitations: list[str] | None = None) -> None:
        normalized: list[AnalysisEvidence] = []
        for item in evidence or []:
            if isinstance(item, AnalysisEvidence):
                normalized.append(item)
            else:
                normalized.append(AnalysisEvidence(
                    kind=category, location="", detail=str(item)))
        self.add_finding(AnalysisFinding(
            finding_id=finding_id, title=title, category=category,
            confidence=confidence, method=method,
            evidence=normalized, limitations=limitations or [],
        ))

    def recommend(self, category: str, action: str, rationale: str,
                  priority: Priority = Priority.MEDIUM) -> None:
        self.add_recommendation(AnalysisRecommendation(category, action, rationale, priority))


Analyzer = Callable[[AnalysisContext], None]

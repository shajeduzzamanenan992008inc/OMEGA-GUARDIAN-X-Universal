"""Structured models for the Universal Project Intelligence Engine.

The models are plain dataclasses with deterministic JSON serialization.  Every
profile separates static observations (``evidence``) from heuristic
interpretation (``confidence``) so reports never present an inference as a
measured fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any

SCHEMA_VERSION = "ogx-intelligence-1"
ENGINE_VERSION = "1.0.0"
ANALYSIS_MODE = "static-read-only"


class Confidence(str, Enum):
    """Evidence strength. This is not a probability or a security verdict."""

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.UNKNOWN: 0,
    Confidence.POSSIBLE: 1,
    Confidence.LIKELY: 2,
    Confidence.CONFIRMED: 3,
}


def confidence_rank(value: Confidence) -> int:
    return CONFIDENCE_RANK.get(value, 0)


def strongest(*values: Confidence) -> Confidence:
    return max(values, key=confidence_rank, default=Confidence.UNKNOWN)


def at_least(value: Confidence, threshold: Confidence) -> bool:
    return confidence_rank(value) >= confidence_rank(threshold)


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HealthStatus(str, Enum):
    """Indicator status, not a quality grade."""

    ESTABLISHED = "established"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    return value


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(slots=True)
class AnalysisEvidence(_Serializable):
    kind: str
    location: str
    detail: str = ""


@dataclass(slots=True)
class AnalysisFinding(_Serializable):
    finding_id: str
    title: str
    category: str
    confidence: Confidence
    method: str
    evidence: list[AnalysisEvidence] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisRecommendation(_Serializable):
    category: str
    action: str
    rationale: str
    priority: Priority = Priority.MEDIUM


@dataclass(slots=True)
class AnalysisLimitation(_Serializable):
    area: str
    detail: str


@dataclass(slots=True)
class LanguageProfile(_Serializable):
    key: str
    name: str
    category: str
    file_count: int
    percentage: float
    role: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FrameworkProfile(_Serializable):
    name: str
    category: str
    ecosystem: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TechnologyProfile(_Serializable):
    name: str
    category: str
    ecosystem: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DependencyDeclaration(_Serializable):
    ecosystem: str
    name: str
    constraint: str
    scope: str
    source: str
    group: str = ""


@dataclass(slots=True)
class ManifestAnalysis(_Serializable):
    path: str
    ecosystem: str
    status: str
    declaration_count: int = 0
    error: str = ""


@dataclass(slots=True)
class PackageManagerProfile(_Serializable):
    name: str
    ecosystem: str
    manifest: str
    lockfile: str | None
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)

    @property
    def lockfile_present(self) -> bool:
        return bool(self.lockfile)


@dataclass(slots=True)
class DependencyProfile(_Serializable):
    manifests: list[ManifestAnalysis] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    declarations: list[DependencyDeclaration] = field(default_factory=list)
    package_managers: list[PackageManagerProfile] = field(default_factory=list)
    unsupported_manifests: list[str] = field(default_factory=list)
    resolved: bool = False
    transitive: bool = False
    network_access: bool = False
    truncated: bool = False
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BuildSystemProfile(_Serializable):
    name: str
    category: str
    manifest: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ci_workflows: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TreeEntry(_Serializable):
    path: str
    kind: str
    size: int = 0


@dataclass(slots=True)
class MonorepoPackage(_Serializable):
    path: str
    manifests: list[str] = field(default_factory=list)
    language_hint: str = ""


@dataclass(slots=True)
class ComponentInfo(_Serializable):
    path: str
    kind: str
    file_count: int = 0
    manifests: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectStructure(_Serializable):
    source_directories: list[str] = field(default_factory=list)
    test_directories: list[str] = field(default_factory=list)
    documentation_directories: list[str] = field(default_factory=list)
    script_directories: list[str] = field(default_factory=list)
    asset_directories: list[str] = field(default_factory=list)
    build_output_directories: list[str] = field(default_factory=list)
    ci_files: list[str] = field(default_factory=list)
    infrastructure_files: list[str] = field(default_factory=list)
    monorepo_packages: list[MonorepoPackage] = field(default_factory=list)
    workspace_indicators: list[str] = field(default_factory=list)
    components: list[ComponentInfo] = field(default_factory=list)
    tree: list[TreeEntry] = field(default_factory=list)
    tree_truncated: bool = False
    file_count: int = 0
    directory_count: int = 0
    total_bytes: int = 0
    generated_files: int = 0
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EntryPoint(_Serializable):
    path: str
    kind: str
    evidence: str
    confidence: Confidence


@dataclass(slots=True)
class ArchitectureIndicator(_Serializable):
    name: str
    verdict: Confidence
    summary: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectClassification(_Serializable):
    kind: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)
    secondary_kinds: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TestingProfile(_Serializable):
    test_file_count: int = 0
    test_directories: list[str] = field(default_factory=list)
    sample_test_files: list[str] = field(default_factory=list)
    frameworks: list[FrameworkProfile] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    coverage_configured: bool | None = None
    coverage_evidence: list[str] = field(default_factory=list)
    static_analysis: list[str] = field(default_factory=list)
    type_checking: list[str] = field(default_factory=list)
    ci_test_workflows: list[str] = field(default_factory=list)
    executed: bool = False
    confidence: Confidence = Confidence.UNKNOWN
    evidence: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentationIndicator(_Serializable):
    item: str
    present: bool
    evidence: str = ""


@dataclass(slots=True)
class DocumentationProfile(_Serializable):
    readme: str | None = None
    contributing: str | None = None
    changelog: str | None = None
    license: str | None = None
    security_policy: str | None = None
    code_of_conduct: str | None = None
    architecture_documentation: list[str] = field(default_factory=list)
    api_documentation: list[str] = field(default_factory=list)
    documentation_directories: list[str] = field(default_factory=list)
    indicators: list[DocumentationIndicator] = field(default_factory=list)
    confidence: Confidence = Confidence.UNKNOWN
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConfigurationFile(_Serializable):
    path: str
    category: str
    sensitive: bool = False
    reason: str = ""


@dataclass(slots=True)
class ConfigurationProfile(_Serializable):
    files: list[ConfigurationFile] = field(default_factory=list)
    categories: dict[str, list[str]] = field(default_factory=dict)
    sensitive_candidates: list[ConfigurationFile] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HealthDimension(_Serializable):
    name: str
    status: HealthStatus
    score: float | None
    rationale: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EngineeringHealthProfile(_Serializable):
    score: float | None = None
    available: bool = False
    dimensions: list[HealthDimension] = field(default_factory=list)
    scored_dimensions: int = 0
    unscored_dimensions: int = 0
    method: str = (
        "Equal-weight indicator coverage: established=1.0, partial=0.5, "
        "missing=0.0. not_applicable and unknown are excluded from the "
        "denominator. This measures observable engineering indicators, not "
        "code quality, security or team performance."
    )
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectIdentityInfo(_Serializable):
    source: str
    project_id: str
    canonical_name: str = ""
    identity_hash: str = ""
    registry_project_id: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectSummary(_Serializable):
    name: str
    root: str
    project_id: str
    identity: ProjectIdentityInfo
    fingerprint: dict[str, Any] = field(default_factory=dict)
    file_count: int = 0
    total_bytes: int = 0


@dataclass(slots=True)
class AnalysisMetadata(_Serializable):
    engine_version: str = ENGINE_VERSION
    schema_version: str = SCHEMA_VERSION
    generated_at: str = ""
    analysis_mode: str = ANALYSIS_MODE
    execution_performed: bool = False
    network_access: bool = False
    analysis_id: str = ""
    analyzers: list[str] = field(default_factory=list)
    duration_ms: int = 0
    cache_status: str = "recomputed"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectIntelligence(_Serializable):
    metadata: AnalysisMetadata
    project: ProjectSummary
    classification: ProjectClassification
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
    findings: list[AnalysisFinding] = field(default_factory=list)
    recommendations: list[AnalysisRecommendation] = field(default_factory=list)
    limitations: list[AnalysisLimitation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "languages": len(self.languages),
            "frameworks": len(self.frameworks),
            "technologies": len(self.technologies),
            "dependencies": len(self.dependencies.declarations),
            "entry_points": len(self.entry_points),
            "build_systems": len(self.build_systems),
            "architecture_indicators": len(self.architecture),
            "findings": len(self.findings),
            "recommendations": len(self.recommendations),
            "limitations": len(self.limitations),
            "health_score": self.health.score,
        }

    def to_dict(self) -> dict[str, Any]:
        data = _plain(self)
        data["summary"] = self.summary
        return data

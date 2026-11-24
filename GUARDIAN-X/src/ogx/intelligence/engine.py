"""Project intelligence engine orchestration."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..detector import detect_project
from ..workspace import Workspace, WorkspaceError
from .analyzers import (
    ANALYZER_ORDER,
    analyze_architecture,
    analyze_build_systems,
    analyze_configuration,
    analyze_dependencies,
    analyze_documentation,
    analyze_entry_points,
    analyze_frameworks,
    analyze_health,
    analyze_languages,
    analyze_structure,
    analyze_testing,
)
from .integration import resolve_project_identity
from .models import (
    ANALYSIS_MODE, ENGINE_VERSION, SCHEMA_VERSION, AnalysisLimitation,
    AnalysisMetadata, ProjectClassification, ProjectIntelligence, ProjectSummary,
    Confidence,
)
from .options import AnalysisContext, AnalysisOptions


class ProjectIntelligenceEngine:
    """Local, static-read-only project intelligence orchestration."""

    schema_version = SCHEMA_VERSION
    engine_version = ENGINE_VERSION

    def analyze(self, project_path: str | Path, options: AnalysisOptions | None = None,
                *, project_id: str | None = None) -> ProjectIntelligence:
        options = options or AnalysisOptions()
        root = Path(project_path).expanduser()
        workspace = Workspace.open(root, options.scan_config())
        if not workspace.files:
            workspace.warnings.append("No files were indexed; the project may be empty or fully excluded.")
        profile = detect_project(workspace.root, [workspace.root / entry.path for entry in workspace.files])
        fingerprint = workspace.fingerprint()

        registry = None
        registry_notes: list[str] = []
        if options.registry_lookup:
            try:
                from ..registry import get_registry
                registry = get_registry()
            except (OSError, ValueError, Exception) as exc:  # registry initialization is defensive
                registry_notes.append(f"Registry lookup unavailable: {type(exc).__name__}.")

        identity = resolve_project_identity(
            workspace.root, explicit_project_id=project_id, registry=registry,
            fallback_project_id=fingerprint["project_id"],
        )
        identity.notes.extend(registry_notes)

        started = datetime.now(timezone.utc)
        context = AnalysisContext(
            workspace=workspace,
            profile=profile,
            options=options,
            project_id=identity.project_id,
            registry_project_id=identity.registry_project_id,
        )
        context.warnings.extend(workspace.warnings)
        selected = options.selected_analyzers()
        ran: list[str] = []
        for name in ANALYZER_ORDER:
            if name not in selected:
                continue
            if context.expired():
                context.warnings.append(
                    f"Analysis deadline reached ({options.deadline_seconds:.0f}s); remaining analyzers were skipped.")
                context.add_limitation("analysis", "The time budget expired before every analyzer completed.")
                break
            try:
                self._run_analyzer(name, context)
                ran.append(name)
            except (WorkspaceError, OSError, ValueError, KeyError, TypeError) as exc:
                context.errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:300]}")
                context.add_limitation(name, f"Analyzer failed and was isolated: {type(exc).__name__}.")
            except Exception as exc:  # defensive analyzer boundary
                context.errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:300]}")
                context.add_limitation(name, "Analyzer failed unexpectedly and was isolated.")
            if context.expired():
                context.warnings.append(
                    f"Analysis deadline reached ({options.deadline_seconds:.0f}s); analysis may be partial.")

        digest = hashlib.sha256()
        digest.update(identity.project_id.encode("utf-8"))
        digest.update(fingerprint.get("tree_digest", "").encode("utf-8"))
        digest.update(SCHEMA_VERSION.encode("utf-8"))
        analysis_id = "ogx-intel-" + digest.hexdigest()[:20]

        classification = context.classification or ProjectClassification(
            kind="unknown project", confidence=Confidence.UNKNOWN,
            evidence=["Architecture classification was not run."],
        )
        for note in identity.notes:
            context.add_limitation("identity", note)
        context.add_limitation(
            "analysis",
            "Analysis is static and read-only: project code, builds, tests, package managers and installers were not executed.",
        )
        context.add_limitation(
            "analysis",
            "No network access was used; remote metadata and dependency resolution are outside scope.",
        )

        metadata = AnalysisMetadata(
            engine_version=ENGINE_VERSION, schema_version=SCHEMA_VERSION,
            generated_at=started.isoformat(timespec="seconds"),
            analysis_mode=ANALYSIS_MODE, execution_performed=False, network_access=False,
            analysis_id=analysis_id, analyzers=ran,
            duration_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            cache_status="recomputed", options=options.to_dict(),
        )
        return ProjectIntelligence(
            metadata=metadata,
            project=ProjectSummary(
                name=profile.name, root=str(workspace.root), project_id=identity.project_id,
                identity=identity, fingerprint=fingerprint,
                file_count=len(workspace), total_bytes=workspace.total_bytes,
            ),
            classification=classification,
            languages=context.languages,
            frameworks=context.frameworks,
            technologies=context.technologies,
            dependencies=context.dependencies,
            structure=context.structure,
            entry_points=context.entry_points,
            architecture=context.architecture,
            build_systems=context.build_systems,
            testing=context.testing,
            documentation=context.documentation,
            configuration=context.configuration,
            health=context.health,
            findings=context.findings,
            recommendations=context.recommendations,
            limitations=context.limitations,
            warnings=self._dedupe(context.warnings),
            errors=self._dedupe(context.errors),
        )

    @staticmethod
    def _run_analyzer(name: str, context: AnalysisContext) -> None:
        if name == "language":
            context.languages = analyze_languages(context)
        elif name == "dependencies":
            context.dependencies = analyze_dependencies(context)
        elif name == "structure":
            context.structure = analyze_structure(context)
        elif name == "entrypoints":
            context.entry_points = analyze_entry_points(context)
        elif name == "frameworks":
            context.frameworks, context.technologies = analyze_frameworks(context)
        elif name == "build":
            context.build_systems = analyze_build_systems(context)
        elif name == "testing":
            context.testing = analyze_testing(context)
        elif name == "documentation":
            context.documentation = analyze_documentation(context)
        elif name == "configuration":
            context.configuration = analyze_configuration(context)
        elif name == "architecture":
            context.architecture = analyze_architecture(context)
        elif name == "health":
            context.health = analyze_health(context)
        else:  # pragma: no cover - validated by AnalysisOptions
            raise ValueError(f"Unknown analyzer: {name}")

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

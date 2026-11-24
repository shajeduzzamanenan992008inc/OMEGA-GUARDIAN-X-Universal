"""Transparent engineering-indicator coverage. Not a quality or security score."""

from __future__ import annotations

from ..models import (
    AnalysisEvidence, Confidence, EngineeringHealthProfile, HealthDimension, HealthStatus,
    at_least,
)
from ..options import AnalysisContext

NUMERIC = {
    HealthStatus.ESTABLISHED: 1.0,
    HealthStatus.PARTIAL: 0.5,
    HealthStatus.MISSING: 0.0,
}


def analyze_health(context: AnalysisContext) -> EngineeringHealthProfile:
    dimensions: list[HealthDimension] = []
    has_code = any(item.role in {"primary", "secondary"} and item.file_count > 0
                   for item in context.languages)
    source_dirs = [path for path in context.structure.source_directories if path not in {".", ""}]
    manifests = context.dependencies.manifests
    parse_errors = [item for item in manifests if item.status == "parse_error"]
    unsupported = context.dependencies.unsupported_manifests
    managers = context.dependencies.package_managers
    tests = context.testing
    docs = context.documentation
    config_categories = context.configuration.categories
    build = context.build_systems
    ci_files = sorted(set(context.profile.ci_files) | set(context.testing.ci_test_workflows)
                      | set(context.metrics.get("ci_build_workflows", [])))
    architecture = [item for item in context.architecture if at_least(item.verdict, Confidence.LIKELY)]

    def add(name: str, status: HealthStatus, rationale: str, evidence: list[str]) -> None:
        dimensions.append(HealthDimension(
            name=name, status=status, score=NUMERIC.get(status), rationale=rationale,
            evidence=evidence,
        ))

    if not has_code:
        add("project organization", HealthStatus.NOT_APPLICABLE,
            "No primary source language was detected.", [])
    elif source_dirs:
        add("project organization", HealthStatus.ESTABLISHED,
            "Source code is organized under identifiable source roots.",
            [f"source root: {path}" for path in source_dirs[:5]])
    else:
        add("project organization", HealthStatus.PARTIAL,
            "Source code exists but no separate source root was identified.", [])

    if not has_code and not manifests:
        add("dependency management", HealthStatus.NOT_APPLICABLE,
            "No source or dependency manifest was detected.", [])
    elif manifests and not parse_errors:
        add("dependency management", HealthStatus.ESTABLISHED,
            "At least one dependency manifest was parsed and a package manager identified.",
            [f"manifest parsed: {item.path}" for item in manifests[:5]] +
            [f"package manager: {item.name}" for item in managers[:5]])
    elif manifests:
        add("dependency management", HealthStatus.PARTIAL,
            "Dependency manifests were found, but at least one could not be parsed.",
            [f"parse error: {item.path}" for item in parse_errors[:5]] +
            [f"unsupported: {path}" for path in unsupported[:5]])
    else:
        add("dependency management", HealthStatus.MISSING,
            "No supported dependency manifest was found for a project with source code.", [])

    if not has_code:
        add("testing", HealthStatus.NOT_APPLICABLE, "No source code was detected.", [])
    elif tests.test_file_count:
        add("testing", HealthStatus.ESTABLISHED,
            "Conventional test files were discovered.", [f"test files: {tests.test_file_count}"] +
            [f"framework: {item.name}" for item in tests.frameworks[:5]])
    elif tests.test_directories or tests.frameworks:
        add("testing", HealthStatus.PARTIAL,
            "Test structure or framework evidence exists but no test files were discovered.",
            [f"test directory: {path}" for path in tests.test_directories[:5]])
    else:
        add("testing", HealthStatus.MISSING, "No conventional test files or test configuration were found.", [])

    present_docs = [item for item in docs.indicators if item.present]
    if not context.workspace.files:
        add("documentation", HealthStatus.NOT_APPLICABLE,
            "No files were indexed, so documentation indicators cannot be assessed.", [])
    elif docs.readme and (docs.documentation_directories or docs.contributing):
        add("documentation", HealthStatus.ESTABLISHED,
            "A README and additional documentation structure were found.",
            [f"documentation indicator: {item.item}" for item in present_docs[:8]])
    elif docs.readme:
        add("documentation", HealthStatus.PARTIAL,
            "A README was found without a documentation directory or contribution guide.", [docs.readme])
    elif present_docs:
        add("documentation", HealthStatus.PARTIAL,
            "Some documentation files were found without a README.",
            [item.item for item in present_docs[:5]])
    else:
        add("documentation", HealthStatus.MISSING, "No conventional documentation file was found.", [])

    if not has_code:
        add("configuration consistency", HealthStatus.NOT_APPLICABLE, "No source code was detected.", [])
    elif len(config_categories) >= 3 and not parse_errors:
        add("configuration consistency", HealthStatus.ESTABLISHED,
            "Multiple configuration categories are present and dependency manifests parsed cleanly.",
            [f"category: {name}" for name in sorted(config_categories)[:8]])
    elif config_categories:
        add("configuration consistency", HealthStatus.PARTIAL,
            "Some configuration files were found, but coverage or parse cleanliness is incomplete.",
            [f"category: {name}" for name in sorted(config_categories)[:8]])
    else:
        add("configuration consistency", HealthStatus.MISSING,
            "No recognized configuration category was found.", [])

    if not has_code:
        add("build system", HealthStatus.NOT_APPLICABLE, "No source code was detected.", [])
    elif build:
        add("build system", HealthStatus.ESTABLISHED,
            "At least one build-system manifest was detected.",
            [f"build system: {item.name}" for item in build[:5]])
    else:
        add("build system", HealthStatus.MISSING,
            "Source code exists but no supported build-system manifest was detected.", [])

    if not has_code:
        add("ci/cd", HealthStatus.NOT_APPLICABLE, "No source code was detected.", [])
    elif ci_files:
        add("ci/cd", HealthStatus.ESTABLISHED if len(ci_files) >= 1 else HealthStatus.PARTIAL,
            "CI configuration was detected; workflows were not executed.", [f"CI file: {path}" for path in ci_files[:5]])
    else:
        add("ci/cd", HealthStatus.MISSING, "No conventional CI configuration was detected.", [])

    static_config = [item for item in tests.static_analysis if context.workspace.exists(item)]
    if not has_code:
        add("static analysis", HealthStatus.NOT_APPLICABLE, "No source code was detected.", [])
    elif static_config:
        add("static analysis", HealthStatus.ESTABLISHED,
            "Static-analysis configuration was detected.", [f"static analysis: {item}" for item in static_config[:6]])
    elif tests.static_analysis:
        add("static analysis", HealthStatus.PARTIAL,
            "A static-analysis tool is declared but no dedicated configuration file was detected.",
            [f"static analysis: {item}" for item in tests.static_analysis[:6]])
    else:
        add("static analysis", HealthStatus.MISSING, "No lint/static-analysis configuration was detected.", [])

    if not has_code:
        add("type checking", HealthStatus.NOT_APPLICABLE, "No source code was detected.", [])
    elif any(path.casefold().endswith(("mypy.ini", "pyrightconfig.json", "tsconfig.json", "jsconfig.json"))
             for path in tests.type_checking):
        add("type checking", HealthStatus.ESTABLISHED,
            "A dedicated type-checking configuration was detected.",
            [f"type checking: {item}" for item in tests.type_checking[:6]])
    elif tests.type_checking:
        add("type checking", HealthStatus.PARTIAL,
            "Compiler or type-checker evidence was found without a dedicated strict configuration.",
            [f"type checking: {item}" for item in tests.type_checking[:6]])
    else:
        add("type checking", HealthStatus.NOT_APPLICABLE,
            "No type-checking configuration was detected for the detected language set.", [])

    if not managers:
        add("lockfiles", HealthStatus.NOT_APPLICABLE, "No package manager was detected.", [])
    elif managers and all(item.lockfile_present for item in managers):
        add("lockfiles", HealthStatus.ESTABLISHED,
            "Every detected package manager has a recognized lockfile.", [f"lockfile: {item.lockfile}" for item in managers if item.lockfile][:8])
    elif any(item.lockfile_present for item in managers):
        add("lockfiles", HealthStatus.PARTIAL,
            "Some detected package managers have a lockfile and others do not.",
            [f"{item.name}: {item.lockfile or 'no lockfile'}" for item in managers[:8]])
    else:
        add("lockfiles", HealthStatus.PARTIAL,
            "No lockfile was detected; this is not always required for libraries.",
            [f"package manager: {item.name}" for item in managers[:8]])

    if not has_code:
        add("architecture clarity", HealthStatus.UNKNOWN,
            "No source code was detected, so architecture clarity cannot be assessed.", [])
    elif architecture and (docs.architecture_documentation or len(architecture) >= 2):
        add("architecture clarity", HealthStatus.ESTABLISHED,
            "Multiple architecture indicators or architecture documentation were found.",
            [f"indicator: {item.name}" for item in architecture[:6]] +
            [f"architecture documentation: {path}" for path in docs.architecture_documentation[:3]])
    elif architecture:
        add("architecture clarity", HealthStatus.PARTIAL,
            "A single architecture indicator was found; design intent is not documented.",
            [f"indicator: {item.name}" for item in architecture[:3]])
    else:
        add("architecture clarity", HealthStatus.UNKNOWN,
            "No reliable architecture indicator was established.", [])

    scored = [item for item in dimensions if item.score is not None]
    score = round((sum(item.score for item in scored) / len(scored)) * 100, 1) if scored else None
    profile = EngineeringHealthProfile(
        score=score, available=bool(scored), dimensions=dimensions,
        scored_dimensions=len(scored),
        unscored_dimensions=len(dimensions) - len(scored),
        notes=[
            "Equal-weight indicator coverage across observable engineering dimensions.",
            "not_applicable and unknown dimensions are excluded from the denominator.",
            "A missing indicator is not proof of poor quality, and a present indicator is not proof of correctness.",
            "This score is not a security assessment, certification or team-performance measure.",
        ],
    )
    context.health = profile

    for dimension in dimensions:
        if dimension.status is HealthStatus.MISSING:
            mapping = {
                "testing": ("testing", "Add deterministic tests for primary code paths",
                            "No conventional test infrastructure was detected.", "medium"),
                "documentation": ("documentation", "Add a README and core documentation",
                                  "No conventional documentation was detected.", "medium"),
                "dependency management": ("dependencies", "Add a supported dependency manifest",
                                          "No supported dependency manifest was detected for the source tree.", "medium"),
                "build system": ("build", "Add or document the build system",
                                 "No supported build-system manifest was detected.", "medium"),
                "ci/cd": ("ci", "Add a CI workflow for build and tests",
                          "No conventional CI configuration was detected.", "low"),
                "static analysis": ("quality", "Add lint/static-analysis configuration",
                                    "No lint or static-analysis configuration was detected.", "low"),
                "configuration consistency": ("configuration", "Consolidate project configuration",
                                              "Few or no recognized configuration categories were detected.", "low"),
            }
            if dimension.name in mapping:
                category, action, rationale, priority = mapping[dimension.name]
                context.recommend(category, action, rationale, priority)
        elif dimension.status is HealthStatus.PARTIAL and dimension.name == "lockfiles":
            context.recommend("dependencies", "Review lockfile coverage",
                              "At least one detected package manager has no recognized lockfile; this may be intentional for libraries.",
                              "low")
        elif dimension.status is HealthStatus.PARTIAL and dimension.name == "architecture clarity":
            context.recommend("architecture", "Document component boundaries and architecture",
                              "Architecture evidence is present but not documented or corroborated.",
                              "low")

    context.finding(
        "OGX-INTEL-HEALTH", "Engineering indicator coverage assessed", "health",
        Confidence.LIKELY, "Transparent equal-weight indicator coverage",
        evidence=[AnalysisEvidence(kind="dimension", location=item.name,
                                   detail=f"{item.status.value}: {item.rationale}")
                  for item in dimensions[:12]],
        limitations=["Indicator coverage is not a code-quality, security or performance measurement."],
    )
    context.add_limitation(
        "health",
        "The health score measures observable engineering indicators only; it is not a security or quality certification.",
    )
    return profile

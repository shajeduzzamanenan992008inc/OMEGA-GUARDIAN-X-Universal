"""Static architecture indicators with explicit evidence grades.

Directory names alone never produce a CONFIRMED finding.  A confirmed verdict
requires direct configuration, dependency or declared-entry-point evidence.
"""

from __future__ import annotations

from pathlib import Path

from ..detectors.project_type import classify_project
from ..models import (
    AnalysisEvidence, ArchitectureIndicator, Confidence, at_least,
)
from ..options import AnalysisContext
from .common import path_parts

WEB_FRAMEWORKS = {
    "Django", "Flask", "FastAPI", "Express", "Fastify", "NestJS", "Spring Boot",
    "Rails", "Laravel", "ASP.NET Core", "Symfony", "Streamlit", "Next.js",
    "SvelteKit", "Angular", "React", "Vue", "Svelte",
}
BACKEND_FRAMEWORKS = {
    "Django", "Flask", "FastAPI", "Express", "Fastify", "NestJS", "Spring Boot",
    "Rails", "Laravel", "ASP.NET Core", "Symfony", "Gin", "Echo", "Fiber",
    "Axum", "Actix Web",
}
FRONTEND_FRAMEWORKS = {"React", "Next.js", "Vue", "Angular", "Svelte", "SvelteKit"}
MOBILE_FRAMEWORKS = {"Android platform", "Flutter", "React Native"}
DESKTOP_FRAMEWORKS = {"Electron", "Tauri", "WPF", "WinForms"}
CLI_LIBRARIES = {"Click", "Typer", "Cobra", "Clap"}

LAYER_DIRS = {
    "controllers", "controller", "services", "service", "models", "model",
    "repositories", "repository", "domain", "usecases", "use_cases",
    "infrastructure", "adapters", "interfaces", "presentation", "application",
}
MVC_DIRS = {"controllers", "models", "views"}
MVVM_DIRS = {"viewmodels", "viewmodel", "views"}

ML_PACKAGES = {"torch", "tensorflow", "scikit-learn", "xgboost", "lightgbm",
               "transformers", "keras", "jax", "flax"}
DATA_PACKAGES = {"pandas", "numpy", "jupyter", "notebook", "ipykernel", "polars",
                 "matplotlib", "seaborn", "plotly"}

DIRECT_ENTRY_KINDS = {
    "python-script", "python-scripts", "python-gui-scripts", "python-poetry-script",
    "python-console-script", "javascript-bin", "rust-bin",
}


def _declared_names(context: AnalysisContext) -> set[str]:
    return {declaration.name.strip().casefold().replace("_", "-")
            for declaration in context.dependencies.declarations}


def _directory_names(context: AnalysisContext) -> set[str]:
    return {part.casefold() for entry in context.workspace.files
            for part in path_parts(entry.path)[:-1]}


def analyze_architecture(context: AnalysisContext) -> list[ArchitectureIndicator]:
    indicators: list[ArchitectureIndicator] = []
    directory_names = _directory_names(context)
    framework_names = {framework.name for framework in context.frameworks}
    technology_names = {technology.name for technology in context.technologies}
    declared = _declared_names(context)
    entry_kinds = {point.kind for point in context.entry_points}
    ci_build = set(context.metrics.get("ci_build_workflows", []))

    def add(name: str, verdict: Confidence, summary: str, evidence: list[str]) -> None:
        if verdict is Confidence.UNKNOWN:
            return
        indicators.append(ArchitectureIndicator(name=name, verdict=verdict,
                                                summary=summary, evidence=evidence))

    source_dirs = [path for path in context.structure.source_directories if path not in {".", ""}]
    packages = context.structure.monorepo_packages

    if context.structure.workspace_indicators:
        add("monorepo", Confidence.CONFIRMED,
            "An explicit workspace configuration groups multiple project packages.",
            context.structure.workspace_indicators[:5])
    elif len(packages) >= 2:
        add("monorepo", Confidence.LIKELY,
            "Multiple nested package manifests were found in distinct directories.",
            [f"package root: {package.path}" for package in packages[:5]])
    elif len(packages) == 1 and len(source_dirs) >= 2:
        add("monorepo", Confidence.POSSIBLE,
            "One nested package manifest and multiple source roots were found.",
            [f"package root: {packages[0].path}"])

    compose_images = int(context.metrics.get("compose_service_images", 0) or 0)
    service_roots: set[str] = set()
    for entry in context.workspace.files:
        name = Path(entry.path).name.casefold()
        if name not in {"dockerfile", "containerfile"}:
            continue
        parent = str(Path(entry.path).parent).replace("\\", "/")
        if parent not in {".", ""}:
            service_roots.add(parent)
    if compose_images >= 2 and len(service_roots) >= 1:
        add("microservices", Confidence.LIKELY,
            "Container image declarations and service-local container definitions suggest independently deployable services.",
            [f"declared container images: {compose_images}"] + [f"container root: {path}" for path in sorted(service_roots)[:5]])
    elif compose_images >= 2:
        add("microservices", Confidence.POSSIBLE,
            "Multiple container image declarations were found; deployment topology is not established.",
            [f"declared container images: {compose_images}"])
    elif len(service_roots) >= 2:
        add("microservices", Confidence.POSSIBLE,
            "Multiple directories contain container definitions; runtime topology is unknown.",
            [f"container root: {path}" for path in sorted(service_roots)[:5]])

    layers = sorted(LAYER_DIRS & directory_names)
    if len(layers) >= 3:
        verdict = Confidence.LIKELY if WEB_FRAMEWORKS & framework_names else Confidence.POSSIBLE
        add("layered architecture", verdict,
            "Conventional layer directories are present; the framework strengthens but does not prove the pattern.",
            [f"layer directory: {name}" for name in layers[:8]])
    if MVC_DIRS <= directory_names:
        verdict = Confidence.LIKELY if WEB_FRAMEWORKS & framework_names else Confidence.POSSIBLE
        add("MVC", verdict,
            "Controller, model and view directories were found together.",
            [f"directory: {name}" for name in sorted(MVC_DIRS)])
    if {"viewmodels", "viewmodel"} & directory_names and "views" in directory_names:
        verdict = (Confidence.LIKELY if MOBILE_FRAMEWORKS & framework_names or
                   DESKTOP_FRAMEWORKS & framework_names else Confidence.POSSIBLE)
        add("MVVM", verdict,
            "View-model and view directories were found together.",
            ["directory: viewmodels", "directory: views"])

    if context.structure.workspace_indicators or len(packages) >= 2:
        add("modular architecture", Confidence.LIKELY if not context.structure.workspace_indicators else Confidence.CONFIRMED,
            "Multiple package or module boundaries are declared.",
            [f"package: {package.path}" for package in packages[:6]] or context.structure.workspace_indicators[:3])
    elif len(source_dirs) >= 2:
        add("modular architecture", Confidence.POSSIBLE,
            "Multiple source roots are present; module boundaries are not otherwise declared.",
            [f"source root: {path}" for path in source_dirs[:6]])

    plugin_dirs = {"plugins", "plugin"} & directory_names
    plugin_dependency = "pluggy" in declared or "stevedore" in declared
    if plugin_dirs and plugin_dependency:
        add("plugin architecture", Confidence.LIKELY,
            "Plugin directories and a plugin framework dependency were found.",
            [f"directory: {name}" for name in sorted(plugin_dirs)] + ["dependency: pluggy/stevedore"])
    elif plugin_dirs:
        add("plugin architecture", Confidence.POSSIBLE,
            "A plugin directory was found without a registered plugin framework.",
            [f"directory: {name}" for name in sorted(plugin_dirs)])

    declared_cli = bool(entry_kinds & DIRECT_ENTRY_KINDS)
    cli_dependency = bool(CLI_LIBRARIES & technology_names)
    source_main = bool(entry_kinds & {"python-main-guard", "python-candidate", "go-main",
                                      "rust-main", "jvm-main", "kotlin-main",
                                      "native-main", "dotnet-main", "swift-main", "dart-main"})
    if declared_cli and cli_dependency:
        add("cli application", Confidence.CONFIRMED,
            "A declared executable entry point and a CLI framework dependency were found.",
            [f"entry point kind: {kind}" for kind in sorted(entry_kinds & DIRECT_ENTRY_KINDS)] +
            [f"CLI framework: {name}" for name in sorted(CLI_LIBRARIES & technology_names)])
    elif declared_cli:
        add("cli application", Confidence.CONFIRMED,
            "A package manifest declares an executable entry point.", [f"entry point kind: {kind}" for kind in sorted(entry_kinds & DIRECT_ENTRY_KINDS)])
    elif source_main and cli_dependency:
        add("cli application", Confidence.LIKELY,
            "A main function and a CLI framework dependency were found.",
            [f"entry point kind: {kind}" for kind in sorted(entry_kinds & {"python-main-guard", "go-main", "rust-main"})] +
            [f"CLI framework: {name}" for name in sorted(CLI_LIBRARIES & technology_names)])
    elif source_main:
        add("cli application", Confidence.POSSIBLE,
            "A language main entry was found without a declared executable interface.",
            [f"entry point kind: {kind}" for kind in sorted(entry_kinds & {"python-main-guard", "go-main", "rust-main"})])

    backend = BACKEND_FRAMEWORKS & framework_names
    frontend = FRONTEND_FRAMEWORKS & framework_names
    api_docs = context.documentation.api_documentation
    if backend and not frontend:
        add("api service", Confidence.CONFIRMED if api_docs else Confidence.LIKELY,
            "A backend/API framework is declared without a frontend framework.",
            [f"backend framework: {name}" for name in sorted(backend)] + [f"API description: {path}" for path in api_docs[:3]])
    if backend and frontend:
        add("web application", Confidence.CONFIRMED,
            "Backend and frontend framework declarations were both found.",
            [f"backend: {name}" for name in sorted(backend)] + [f"frontend: {name}" for name in sorted(frontend)])
    elif frontend:
        add("web application", Confidence.LIKELY,
            "A frontend framework is declared without a detected backend framework.",
            [f"frontend: {name}" for name in sorted(frontend)])

    mobile = MOBILE_FRAMEWORKS & framework_names
    if mobile:
        add("mobile application", Confidence.CONFIRMED,
            "A mobile platform or framework marker was found.",
            [f"mobile framework: {name}" for name in sorted(mobile)])
    desktop = DESKTOP_FRAMEWORKS & framework_names
    if desktop:
        add("desktop application", Confidence.CONFIRMED,
            "A desktop application framework or project marker was found.",
            [f"desktop framework: {name}" for name in sorted(desktop)])

    has_library_target = "rust-library" in entry_kinds or (
        "package.json" in {Path(entry.path).name.casefold() for entry in context.workspace.files}
        and "javascript-bin" not in entry_kinds
        and not (backend or frontend or mobile or desktop))
    python_package = any(Path(entry.path).name == "__init__.py" for entry in context.workspace.files)
    if (has_library_target or python_package) and not (backend or frontend or mobile or desktop or declared_cli):
        add("library", Confidence.LIKELY,
            "Package structure without an application or executable entry point suggests a reusable library.",
            ["library target or package initializer detected"])

    ml = sorted(ML_PACKAGES & declared)
    data = sorted(DATA_PACKAGES & declared)
    notebooks = [entry.path for entry in context.workspace.files
                 if Path(entry.path).suffix.casefold() == ".ipynb"]
    if notebooks:
        add("data science project", Confidence.LIKELY if not data else Confidence.CONFIRMED,
            "Jupyter notebooks were found in the indexed scope.",
            [f"notebook: {path}" for path in notebooks[:5]] + [f"data package: {name}" for name in data[:5]])
    elif data:
        add("data science project", Confidence.POSSIBLE,
            "Data-analysis packages are declared; project intent is not established.",
            [f"data package: {name}" for name in data[:8]])
    if ml:
        add("machine learning project", Confidence.CONFIRMED,
            "Machine-learning libraries are declared as dependencies.",
            [f"ML package: {name}" for name in ml[:8]])

    if context.structure.monorepo_packages and not any(
            item.name in {"web application", "api service", "mobile application", "desktop application", "cli application"}
            for item in indicators):
        # A monorepo without an identifiable application remains a monorepo.
        pass

    context.classification = classify_project(indicators)
    high_confidence = [item for item in indicators if at_least(item.verdict, Confidence.LIKELY)]
    for indicator in high_confidence[:20]:
        context.finding(
            "OGX-INTEL-ARCHITECTURE", f"Architecture indicator: {indicator.name}",
            "architecture", indicator.verdict,
            "Static directory, manifest and dependency correlation",
            evidence=[AnalysisEvidence(kind="architecture", location=indicator.name, detail=item)
                      for item in indicator.evidence[:8]],
            limitations=["Indicators are structural evidence, not a runtime topology or design review."],
        )
    if context.classification.confidence is Confidence.UNKNOWN:
        context.recommend(
            "architecture", "Document the intended architecture and component boundaries",
            "No reliable project-type or architecture evidence was established from the indexed scope.",
            "low")
    context.add_limitation(
        "architecture",
        "Architecture indicators are static heuristics; runtime topology, deployment and design intent are not verified.",
    )
    context.add_limitation(
        "architecture",
        "Directory-name patterns alone are capped below CONFIRMED and cannot establish a sophisticated architecture.",
    )
    return indicators

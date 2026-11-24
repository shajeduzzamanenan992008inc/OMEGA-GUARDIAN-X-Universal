"""Language intelligence from extensions and language-specific manifests."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ...engines.language_specs import SPECS, LanguageSpec
from ..models import AnalysisEvidence, Confidence, LanguageProfile
from ..options import AnalysisContext
from .common import CODE_CATEGORIES, is_counted_source, spec_extension_map

MANIFEST_LANGUAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "python": ("pyproject.toml", "setup.py", "setup.cfg", "pipfile"),
    "typescript": ("tsconfig.json", "jsconfig.json"),
    "java": ("pom.xml",),
    "kotlin": ("build.gradle.kts",),
    "csharp": ("directory.build.props",),
    "go": ("go.mod",),
    "rust": ("cargo.toml",),
    "ruby": ("gemfile",),
    "php": ("composer.json",),
    "swift": ("package.swift",),
    "dart": ("pubspec.yaml",),
    "scala": ("build.sbt",),
    "r": ("description", "renv.lock"),
    "julia": ("project.toml",),
}


def _manifest_marker(context: AnalysisContext, spec: LanguageSpec) -> list[str]:
    found: list[str] = []
    names = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}
    for marker in MANIFEST_LANGUAGE_MARKERS.get(spec.key, ()):
        if marker in names:
            found.append(names[marker])
        elif "*" in marker:
            found.extend(entry.path for entry in context.workspace.files
                         if Path(entry.path).match(marker))
    if spec.key == "python":
        found.extend(entry.path for entry in context.workspace.files
                     if Path(entry.path).name.casefold().startswith("requirements")
                     and Path(entry.path).suffix.casefold() == ".txt")
    if spec.key == "csharp":
        found.extend(entry.path for entry in context.workspace.files
                     if Path(entry.path).suffix.casefold() in {".csproj", ".sln", ".fsproj", ".vbproj"})
    if spec.key == "ruby":
        found.extend(entry.path for entry in context.workspace.files
                     if Path(entry.path).suffix.casefold() == ".gemspec")
    if spec.key == "lua":
        found.extend(entry.path for entry in context.workspace.files
                     if Path(entry.path).suffix.casefold() == ".rockspec")
    return sorted(set(found))


def analyze_languages(context: AnalysisContext) -> list[LanguageProfile]:
    extensions = spec_extension_map()
    counts: Counter[str] = Counter()
    excluded_generated = 0
    for entry in context.workspace.files:
        suffix = Path(entry.path).suffix.casefold()
        spec = extensions.get(suffix)
        if spec is None:
            continue
        if not is_counted_source(entry.path):
            excluded_generated += 1
            continue
        counts[spec.key] += 1

    profiles: list[LanguageProfile] = []
    total_counted = sum(counts.values())
    for spec in SPECS:
        count = counts.get(spec.key, 0)
        markers = _manifest_marker(context, spec)
        if not count and not markers:
            continue
        corroborated = bool(markers)
        if count >= 3:
            confidence = Confidence.CONFIRMED
        elif count == 2:
            confidence = Confidence.LIKELY
        elif count == 1:
            confidence = Confidence.CONFIRMED if corroborated else Confidence.POSSIBLE
        else:
            confidence = Confidence.LIKELY
        evidence: list[str] = []
        if count:
            evidence.append(f"{count} file(s) with extensions: {', '.join(spec.extensions)}")
        if markers:
            evidence.append("language manifest: " + ", ".join(markers[:5]))
        role = "incidental"
        if spec.category in CODE_CATEGORIES and count:
            role = "code"
        elif not count:
            role = "manifest-indicator"
        percentage = round((count / total_counted) * 100, 1) if total_counted else 0.0
        profiles.append(LanguageProfile(
            key=spec.key, name=spec.name, category=spec.category,
            file_count=count, percentage=percentage, role=role,
            confidence=confidence, evidence=evidence,
        ))

    code_profiles = sorted((item for item in profiles if item.role == "code"),
                           key=lambda item: (-item.file_count, item.name.casefold()))
    if code_profiles:
        code_profiles[0].role = "primary"
        for item in code_profiles[1:]:
            item.role = "secondary"
    profiles.sort(key=lambda item: (0 if item.role == "primary" else 1 if item.role == "secondary" else 2,
                                    -item.file_count, item.name.casefold()))

    if excluded_generated:
        context.warnings.append(
            f"Excluded {excluded_generated} generated or vendored file(s) from language counts.")
    if not profiles:
        context.add_limitation("languages",
                               "No supported source or manifest language indicator was detected.")
    if code_profiles:
        primary = code_profiles[0]
        primary_spec = next((spec for spec in SPECS if spec.key == primary.key), None)
        extensions = primary_spec.extensions if primary_spec else ()
        sample_files = [entry.path for entry in context.workspace.files
                        if Path(entry.path).suffix.casefold() in extensions
                        and is_counted_source(entry.path)][:5]
        language_evidence = [
            AnalysisEvidence(kind="source-file", location=path,
                             detail=f"{primary.name} extension match")
            for path in sample_files
        ]
        if primary_spec:
            language_evidence.extend(
                AnalysisEvidence(kind="manifest", location=path,
                                 detail="language manifest marker")
                for path in _manifest_marker(context, primary_spec)[:3])
        if not language_evidence:
            language_evidence.append(AnalysisEvidence(
                kind="language", location=primary.name,
                detail=f"{primary.file_count} extension-counted file(s)"))
        context.finding(
            "OGX-INTEL-LANGUAGE", f"Primary language: {primary.name}", "languages",
            primary.confidence, "Static extension and language-manifest inspection",
            evidence=language_evidence,
        )
        if len(code_profiles) > 1:
            context.finding(
                "OGX-INTEL-LANGUAGE-MIXED",
                "Mixed-language project: " + ", ".join(item.name for item in code_profiles[:6]),
                "languages", Confidence.CONFIRMED,
                "Static extension census after generated/vendored exclusion",
                evidence=[AnalysisEvidence(kind="language", location=item.name,
                                           detail=f"{item.file_count} file(s)")
                          for item in code_profiles[:8]],
            )
    context.add_limitation(
        "languages",
        "Language detection is extension and manifest based; it does not prove semantic language use.",
    )
    context.add_limitation(
        "languages",
        "Generated, minified and vendored paths are excluded with filename/directory heuristics, not content provenance.",
    )
    return profiles

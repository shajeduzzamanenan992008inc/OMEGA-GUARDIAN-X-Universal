"""Documentation presence intelligence. Presence is not a quality judgement."""

from __future__ import annotations

from pathlib import Path

from ..models import (
    AnalysisEvidence, Confidence, DocumentationIndicator, DocumentationProfile,
)
from ..options import AnalysisContext


def _first(exact: dict[str, str], prefixes: tuple[str, ...]) -> str | None:
    for name, rel in sorted(exact.items()):
        stem = Path(name).stem.casefold()
        for prefix in prefixes:
            if stem == prefix or stem.startswith(prefix + "-") or stem.startswith(prefix + "_"):
                return rel
    return None


def analyze_documentation(context: AnalysisContext) -> DocumentationProfile:
    exact = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}
    profile = DocumentationProfile()
    profile.readme = _first(exact, ("readme",))
    profile.contributing = _first(exact, ("contributing",))
    profile.changelog = _first(exact, ("changelog", "changes", "history", "releases"))
    profile.license = _first(exact, ("license", "licence", "copying"))
    profile.security_policy = _first(exact, ("security",))
    profile.code_of_conduct = _first(exact, ("code_of_conduct", "code-of-conduct"))
    profile.documentation_directories = sorted(
        {"/".join(entry.path.split("/")[:index + 1])
         for entry in context.workspace.files
         for index, part in enumerate(entry.path.split("/")[:-1])
         if part.casefold() in {"docs", "doc", "documentation"}})[:20]

    architecture: list[str] = []
    api: list[str] = []
    for entry in context.workspace.files:
        folded = entry.path.casefold()
        name = Path(entry.path).name.casefold()
        if name in {"architecture.md", "architecture.rst", "adr.md"} or "/adr/" in folded or folded.startswith("docs/architecture"):
            architecture.append(entry.path)
        if name in {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml"}:
            api.append(entry.path)
        elif ("/docs/api/" in folded or folded.startswith("docs/api/")) and Path(entry.path).suffix.casefold() in {".md", ".rst", ".html"}:
            api.append(entry.path)
    profile.architecture_documentation = sorted(set(architecture))[:20]
    profile.api_documentation = sorted(set(api))[:20]

    profile.indicators = [
        DocumentationIndicator("README", bool(profile.readme), profile.readme or ""),
        DocumentationIndicator("Documentation directory", bool(profile.documentation_directories),
                               ", ".join(profile.documentation_directories[:3])),
        DocumentationIndicator("CONTRIBUTING guide", bool(profile.contributing), profile.contributing or ""),
        DocumentationIndicator("CHANGELOG", bool(profile.changelog), profile.changelog or ""),
        DocumentationIndicator("License", bool(profile.license), profile.license or ""),
        DocumentationIndicator("Security policy", bool(profile.security_policy), profile.security_policy or ""),
        DocumentationIndicator("Architecture documentation", bool(profile.architecture_documentation),
                               ", ".join(profile.architecture_documentation[:3])),
        DocumentationIndicator("API documentation", bool(profile.api_documentation),
                               ", ".join(profile.api_documentation[:3])),
    ]
    present = sum(1 for item in profile.indicators if item.present)
    if profile.readme and profile.documentation_directories:
        profile.confidence = Confidence.CONFIRMED
    elif profile.readme or present >= 2:
        profile.confidence = Confidence.LIKELY
    elif present:
        profile.confidence = Confidence.POSSIBLE
    else:
        profile.confidence = Confidence.UNKNOWN

    documentation_evidence = [
        AnalysisEvidence(kind="documentation", location=item.evidence or ".",
                         detail=item.item)
        for item in profile.indicators if item.present
    ]
    if profile.readme:
        context.finding(
            "OGX-INTEL-DOCUMENTATION", "Project documentation detected", "documentation",
            profile.confidence, "Documentation filename and directory presence",
            evidence=documentation_evidence,
            limitations=["Documentation content, accuracy and freshness were not evaluated."],
        )
    else:
        context.finding(
            "OGX-INTEL-DOCUMENTATION-MISSING", "No README detected", "documentation",
            Confidence.LIKELY, "Documentation filename presence",
            evidence=[AnalysisEvidence(kind="search", location=".",
                                       detail="no conventional README was indexed")],
            limitations=["Documentation may exist under a non-standard name."],
        )
        context.recommend("documentation", "Add a README with setup, usage and support information",
                          "No conventional README file was detected in the indexed scope.", "medium")
    if not profile.license:
        context.recommend("documentation", "Add a license file",
                          "No conventional license file was detected; licensing status is unclear.",
                          "low")
    if not profile.contributing and context.languages:
        context.recommend("documentation", "Add contribution guidance",
                          "No conventional CONTRIBUTING file was detected.", "low")
    profile.limitations = [
        "Only documentation presence and location are assessed; content quality, accuracy and freshness are not judged.",
        "Documentation outside the indexed, bounded project scope is unknown.",
    ]
    for limitation in profile.limitations:
        context.add_limitation("documentation", limitation)
    return profile

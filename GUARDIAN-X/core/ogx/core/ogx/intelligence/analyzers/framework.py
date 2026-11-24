"""Framework and technology intelligence wrapper."""

from __future__ import annotations

from ..detectors.technology import detect_profiles
from ..models import AnalysisEvidence, FrameworkProfile, TechnologyProfile
from ..options import AnalysisContext


def analyze_frameworks(context: AnalysisContext) -> tuple[list[FrameworkProfile], list[TechnologyProfile]]:
    frameworks, technologies = detect_profiles(context)
    context.frameworks = frameworks
    context.technologies = technologies
    for framework in frameworks[:40]:
        evidence = [AnalysisEvidence(kind="framework", location=framework.name, detail=item)
                    for item in framework.evidence[:8]]
        if not evidence:
            evidence = [AnalysisEvidence(kind="framework", location=framework.name,
                                        detail=f"{framework.category} indicator")]
        context.finding(
            "OGX-INTEL-FRAMEWORK", f"Framework indicator: {framework.name}", "frameworks",
            framework.confidence, "Declared dependency, configuration file or explicit platform marker",
            evidence=evidence, limitations=list(framework.evidence[:6]),
        )
    if not frameworks:
        context.add_limitation("frameworks",
                               "No framework was identified from supported manifests or configuration markers.")
    context.add_limitation(
        "frameworks",
        "Framework detection uses declarations and configuration files; runtime presence and version behaviour are not verified.",
    )
    context.add_limitation(
        "technologies",
        "Database and infrastructure entries describe declared clients or configuration, not running services.",
    )
    return frameworks, technologies

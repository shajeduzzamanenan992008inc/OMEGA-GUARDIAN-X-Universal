"""Project classification from evidence-backed architecture indicators."""

from __future__ import annotations

from ..models import ArchitectureIndicator, Confidence, ProjectClassification, confidence_rank

KIND_PRIORITY: dict[str, int] = {
    "mobile application": 0,
    "desktop application": 1,
    "web application": 2,
    "api service": 3,
    "cli application": 4,
    "machine learning project": 5,
    "data science project": 6,
    "monorepo": 7,
    "library": 8,
    "unknown project": 9,
}


def classify_project(indicators: list[ArchitectureIndicator]) -> ProjectClassification:
    candidates = [item for item in indicators if item.verdict is not Confidence.UNKNOWN]
    if not candidates:
        return ProjectClassification(
            kind="unknown project", confidence=Confidence.UNKNOWN,
            evidence=["No reliable classification evidence was found."],
        )
    best = max(candidates, key=lambda item: (confidence_rank(item.verdict),
                                            -KIND_PRIORITY.get(item.name, 99)))
    secondary = [item.name for item in sorted(
        candidates, key=lambda item: (-confidence_rank(item.verdict), KIND_PRIORITY.get(item.name, 99)))
        if item.name != best.name]
    return ProjectClassification(
        kind=best.name,
        confidence=best.verdict,
        evidence=list(best.evidence),
        secondary_kinds=secondary,
    )

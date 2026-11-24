"""Deterministic JSON rendering for intelligence reports."""

from __future__ import annotations

import json
from typing import Any

from ..models import ProjectIntelligence


def report_dict(report: ProjectIntelligence | dict[str, Any]) -> dict[str, Any]:
    return report.to_dict() if isinstance(report, ProjectIntelligence) else report


def render_json(report: ProjectIntelligence | dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(report_dict(report), indent=indent, ensure_ascii=False) + "\n"

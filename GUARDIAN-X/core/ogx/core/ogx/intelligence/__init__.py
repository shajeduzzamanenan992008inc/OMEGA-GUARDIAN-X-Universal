"""Universal Project Intelligence & Engineering Analysis Engine.

The engine is local-only and static-read-only: it never imports or executes
project code, never installs dependencies, never runs builds, tests or package
managers, and never accesses the network.
"""

from __future__ import annotations

from .engine import ProjectIntelligenceEngine
from .models import ENGINE_VERSION, SCHEMA_VERSION
from .options import ANALYZER_NAMES, AnalysisOptions

__all__ = [
    "ProjectIntelligenceEngine",
    "AnalysisOptions",
    "ANALYZER_NAMES",
    "SCHEMA_VERSION",
    "ENGINE_VERSION",
]

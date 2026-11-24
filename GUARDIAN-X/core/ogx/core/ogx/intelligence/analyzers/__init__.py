"""Intelligence analyzer registry.

The order is dependency-aware: later analyzers consume profiles produced by
earlier ones.  Selecting a subset is supported; missing prerequisites degrade
to explicit unknowns rather than invented facts.
"""

from __future__ import annotations

from .architecture import analyze_architecture
from .build_system import analyze_build_systems
from .configuration import analyze_configuration
from .dependencies import analyze_dependencies
from .documentation import analyze_documentation
from .entrypoints import analyze_entry_points
from .framework import analyze_frameworks
from .health import analyze_health
from .language import analyze_languages
from .structure import analyze_structure
from .testing import analyze_testing

ANALYZER_ORDER: tuple[str, ...] = (
    "language", "dependencies", "structure", "entrypoints", "frameworks",
    "build", "testing", "documentation", "configuration", "architecture", "health",
)

__all__ = [
    "ANALYZER_ORDER",
    "analyze_languages",
    "analyze_dependencies",
    "analyze_structure",
    "analyze_entry_points",
    "analyze_frameworks",
    "analyze_build_systems",
    "analyze_testing",
    "analyze_documentation",
    "analyze_configuration",
    "analyze_architecture",
    "analyze_health",
]

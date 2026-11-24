"""Universal, capability-declared analysis engines.

The engine layer is additive to the original ``ogx`` scanners.  Every engine is
passive and read-only: importing this package never executes target code,
installs dependencies, invokes build tools, or contacts the network.
"""

from __future__ import annotations

from .contract import CAPABILITY_NAMES, BaseEngine, Capability, CapabilityStatus, EngineContext, EngineResult
from .registry import EngineRegistry, default_engines, language_engine_ids, profile_execution_plan

__all__ = [
    "BaseEngine",
    "Capability",
    "CapabilityStatus",
    "EngineContext",
    "EngineRegistry",
    "EngineResult",
    "CAPABILITY_NAMES",
    "default_engines",
    "language_engine_ids",
    "profile_execution_plan",
]

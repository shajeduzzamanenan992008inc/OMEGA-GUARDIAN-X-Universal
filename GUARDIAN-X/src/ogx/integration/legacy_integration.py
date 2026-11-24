"""Backward-compatible access to the tracked ``ogx/integration.py`` module.

The contract-verification engine lives in this package, which shadows the
historical ``ogx/integration.py`` module of the same name. The project-folder
integration workflow (``ogx integrate``) is still owned by that module, so it is
loaded explicitly by path and re-exported here instead of being reimplemented.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_NAME = "ogx._legacy_integration"
_MODULE_PATH = Path(__file__).resolve().parent.parent / "integration.py"


def _load() -> object:
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Legacy integration module is unavailable: {_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_legacy = _load()

FOLDER = _legacy.FOLDER
MANIFEST = _legacy.MANIFEST
MARKER = _legacy.MARKER
apply_integration = _legacy.apply_integration
integration_status = _legacy.integration_status
plan_integration = _legacy.plan_integration
remove_integration = _legacy.remove_integration

__all__ = [
    "FOLDER",
    "MANIFEST",
    "MARKER",
    "apply_integration",
    "integration_status",
    "plan_integration",
    "remove_integration",
]

"""Backward-compatible access to the project-folder integration module.

The project-folder workflow now lives in ``ogx.project_integration``; the old
``ogx/integration.py`` module collided with this package's name and made
imports ambiguous. Existing ``ogx.integration.<name>`` imports keep working
through these re-exports without duplicating any implementation.
"""
from __future__ import annotations

from ..project_integration import (
    FOLDER,
    MANIFEST,
    MARKER,
    apply_integration,
    integration_status,
    plan_integration,
    remove_integration,
)

__all__ = [
    "FOLDER",
    "MANIFEST",
    "MARKER",
    "apply_integration",
    "integration_status",
    "plan_integration",
    "remove_integration",
]

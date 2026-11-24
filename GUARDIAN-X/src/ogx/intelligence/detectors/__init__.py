"""Detector namespace for the intelligence engine."""

from __future__ import annotations

from .package_manager import detect_package_managers
from .project_type import classify_project
from .technology import detect_profiles

__all__ = ["detect_package_managers", "classify_project", "detect_profiles"]

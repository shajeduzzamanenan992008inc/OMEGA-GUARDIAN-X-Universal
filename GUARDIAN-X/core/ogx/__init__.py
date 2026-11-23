"""OMEGA GUARDIAN X (OGX): universal, local-first repository intelligence, protection and governance toolkit."""
from __future__ import annotations

import re
from pathlib import Path

_FALLBACK_VERSION = "0.8.0a1"  # kept equal to <shield>/VERSION by a unit test
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[a-z]+\d+)?$")


def _read_version() -> str:
    """Single source of truth is <shield>/VERSION (core/ogx/__init__.py -> parents[2]).

    A wheel install has no VERSION file next to it, so the constant above is the fallback.
    """
    try:
        text = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return _FALLBACK_VERSION
    return text if _VERSION_RE.fullmatch(text) else _FALLBACK_VERSION


__version__ = _read_version()

"""Shared test helpers."""
from __future__ import annotations

from pathlib import Path


def make_shield(root: Path, name: str = "omega-shield") -> Path:
    """Create a minimal fake shield folder (init.py + core/ogx) inside `root`."""
    folder = root / name
    (folder / "core" / "ogx").mkdir(parents=True, exist_ok=True)
    (folder / "init.py").write_text("# test shield\n", encoding="utf-8")
    return folder

#!/usr/bin/env python3
"""Build the lean drop-in zip (no tests, CI, release material or packaging) from this source tree.

    python _dev/tools/make_dist.py [output.zip]
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE_TOP = {"_dev", ".github", "pyproject.toml", ".git", "dist", "build"}
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / f"omega-shield-{VERSION}-dropin.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or rel.parts[0] in EXCLUDE_TOP or "__pycache__" in rel.parts or path.suffix == ".pyc":
                continue
            archive.write(path, Path("omega-shield") / rel)
            count += 1
    print(f"wrote {out} ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

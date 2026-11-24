#!/usr/bin/env python3
"""Build the lean drop-in zip (no tests, CI, release material or packaging) from this source tree.

    python _dev/tools/make_dist.py [output.zip]
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The drop-in contains init.py, core/, guard/, devkit/, link/, docs/ and the
# shield metadata only. Tests, CI, release material, the installable src tree
# and local secrets must never be shipped.
EXCLUDE_TOP = {
    "_dev", ".github", "pyproject.toml", ".git", "dist", "build", "release",
    "tests", "src", ".pytest_cache",
}
EXCLUDE_NAMES = {".env", "results.txt"}
EXCLUDE_SUFFIXES = {".pyc", ".whl", ".zip", ".db", ".sqlite", ".sqlite3"}
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / f"omega-shield-{VERSION}-dropin.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or rel.parts[0] in EXCLUDE_TOP:
                continue
            if "__pycache__" in rel.parts or path.suffix in EXCLUDE_SUFFIXES:
                continue
            if path.name in EXCLUDE_NAMES or (path.name.startswith(".env.") and path.name != ".env.example"):
                continue
            archive.write(path, Path("omega-shield") / rel)
            count += 1
    print(f"wrote {out} ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

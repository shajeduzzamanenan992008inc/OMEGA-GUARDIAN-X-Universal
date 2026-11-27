#!/usr/bin/env python3
"""Build the lean drop-in zip (no tests, CI, release material or packaging) from this source tree.

    python _dev/tools/make_dist.py [output.zip]
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The drop-in contains init.py, core/, guard/, devkit/, link/, docs/ and the
# shield metadata only. Tests, CI, release material, the installable src tree
# and local secrets must never be shipped. Directory names are excluded at any
# depth: a stray nested mirror such as GUARDIAN-X/.github/ is still repository
# metadata, not drop-in content. This mirrors the archive policy asserted in
# .github/workflows/ci.yml.
EXCLUDE_TOP_FILES = {"pyproject.toml"}
EXCLUDE_DIRS = {
    "_dev", ".github", ".git", "build", "dist", "release", "tests", "src",
    ".pytest_cache", "__pycache__",
}
EXCLUDE_NAMES = {".env", "results.txt"}
EXCLUDE_SUFFIXES = {".pyc", ".whl", ".zip", ".db", ".sqlite", ".sqlite3"}


def build_archive(root: Path, out: Path) -> int:
    """Write the drop-in zip for ``root`` to ``out`` and return the entry count."""
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if path.is_dir() or rel.parts[0] in EXCLUDE_TOP_FILES:
                continue
            if any(part in EXCLUDE_DIRS or part.endswith(".egg-info") for part in rel.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            if path.name in EXCLUDE_NAMES or (path.name.startswith(".env.") and path.name != ".env.example"):
                continue
            archive.write(path, Path("omega-shield") / rel)
            count += 1
    return count


def write_checksum(out: Path) -> tuple[str, Path]:
    """Write a sha256sum-compatible sidecar next to the artifact (REL-02)."""
    digest = hashlib.sha256()
    with open(out, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    sidecar = out.with_name(out.name + ".sha256")
    sidecar.write_text(f"{hexdigest}  {out.name}\n", encoding="utf-8", newline="\n")
    return hexdigest, sidecar


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / f"omega-shield-{version}-dropin.zip"
    count = build_archive(ROOT, out)
    digest, sidecar = write_checksum(out)
    print(f"wrote {out} ({count} files)")
    print(f"sha256 {digest}")
    print(f"wrote {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

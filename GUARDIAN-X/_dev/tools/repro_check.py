#!/usr/bin/env python3
"""Reproducible-build check (REL-05).

Builds the drop-in archive twice into separate temporary directories and
compares the SHA-256 of both outputs. A non-zero exit means the build is not
reproducible (or two runs over identical content diverged).

    python _dev/tools/repro_check.py [--root DIR] [--json]

The check is content-based: it normalises entry order, timestamps, permissions
and compression, so it does not depend on file mtimes, checkout path, locale or
host OS. It does not build or compare wheels; use `compare_artifacts` from
`ogx.provenance` (or `sha256sum`) for release artifacts produced elsewhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_dist import build_archive  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def compare_archives(first: Path, second: Path) -> dict[str, object]:
    return {
        "first": digest(first),
        "second": digest(second),
        "identical": digest(first) == digest(second),
        "first_size": first.stat().st_size,
        "second_size": second.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check drop-in build reproducibility.")
    parser.add_argument("--root", default=str(ROOT), help="source tree to archive")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / "init.py").is_file():
        print(f"ERROR: {root} does not look like the project root", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as temporary:
        first = Path(temporary) / "first.zip"
        second = Path(temporary) / "second.zip"
        count = build_archive(root, first)
        build_archive(root, second)
        report = {"root": str(root), "entries": count, **compare_archives(first, second)}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"entries: {report['entries']}")
        print(f"first:   {report['first']}")
        print(f"second:  {report['second']}")
        print("reproducible: yes" if report["identical"] else "reproducible: NO")
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

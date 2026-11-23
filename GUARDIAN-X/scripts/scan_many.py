#!/usr/bin/env python3
"""Scan several projects with ONE shield install (external mode, nothing is written into the projects).

    python omega-shield/scripts/scan_many.py D:\\apps\\a D:\\apps\\b --strict
Each project gets its own report/state folder (derived from its path), exactly as if the shield sat inside it.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parents[1] / "init.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("projects", nargs="+")
    parser.add_argument("--command", choices=("scan", "audit", "detect"), default="scan")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any project has high/critical findings")
    args = parser.parse_args()
    failures = 0
    for raw in args.projects:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            print(f"[skip] not a directory: {raw}", file=sys.stderr)
            failures += 1
            continue
        print(f"\n=== {root} ===", flush=True)
        command = [sys.executable, str(INIT), args.command] + (["--strict"] if args.strict and args.command != "detect" else [])
        code = subprocess.run(command, env={**os.environ, "OMEGA_HOST_ROOT": str(root)}).returncode
        failures += code != 0
    print(f"\nProjects with problems: {failures} of {len(args.projects)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

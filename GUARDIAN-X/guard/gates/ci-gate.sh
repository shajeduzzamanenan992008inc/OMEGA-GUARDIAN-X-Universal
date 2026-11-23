#!/bin/sh
# Portable CI gate: fails the pipeline when the shield finds high/critical issues in the host project.
# Works from any directory:   sh omega-shield/guard/gates/ci-gate.sh
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SHIELD="$(cd "$HERE/../.." && pwd)"
PY="$(command -v python3 || command -v python)" || { echo "[shield] python not found" >&2; exit 2; }
exec "$PY" "$SHIELD/init.py" scan --strict

# Contributing

## Development setup

Python 3.11+ is required. The runtime has no third-party dependencies.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
python -m compileall -q core _dev/tests
python -m unittest discover -s _dev/tests -v
```

## Engineering rules

- Treat every scanned repository as untrusted input.
- Do not execute target project code from a normal scan.
- Keep default scanning local, read-only, bounded, and deterministic.
- Never put secret values into findings, logs, fixtures, or reports.
- Give every rule a stable ID, severity rationale, confidence, safe evidence, and recommendation.
- Add tests for false positives, malformed input, symlink handling, path traversal, and resource limits.
- Never describe a heuristic as proof of exploitability or compliance.
- Any network or code-execution capability requires a separate threat review and explicit permission model.

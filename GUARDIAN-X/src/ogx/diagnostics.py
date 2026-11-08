from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PATTERNS = [
    ("python-exception", re.compile(r"(?m)^(?:[A-Za-z_][\w.]*Error|Traceback \(most recent call last\))"), "high"),
    ("javascript-error", re.compile(r"(?m)^(?:\w*Error:|UnhandledPromiseRejection)"), "high"),
    ("test-failure", re.compile(r"(?im)^(?:FAILED\b|FAIL:\s|AssertionError\b|Tests? failed)"), "medium"),
    ("timeout", re.compile(r"(?i)\b(?:timed?\s*out|timeout|deadline exceeded)\b"), "medium"),
    ("memory", re.compile(r"(?i)\b(?:out of memory|heap out of memory|memoryerror|oomkilled)\b"), "high"),
    ("database", re.compile(r"(?i)\b(?:deadlock detected|database is locked|connection refused|too many connections)\b"), "medium"),
]


def analyze_logs(paths: list[Path], max_bytes: int = 5_000_000) -> dict[str, Any]:
    findings = []
    processed = []
    skipped = []
    for path in paths:
        path = path.expanduser()
        if path.is_symlink():
            skipped.append(str(path)); continue
        path = path.resolve(strict=True)
        if not path.is_file():
            skipped.append(str(path)); continue
        if path.stat().st_size > max_bytes:
            skipped.append(str(path) + " (over size limit)"); continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped.append(str(path)); continue
        processed.append(str(path))
        for line_no, line in enumerate(text.splitlines(), 1):
            for rule_id, pattern, severity in PATTERNS:
                if pattern.search(line):
                    # Bound and redact likely inline credentials before reporting log excerpts.
                    safe = line[:240]
                    safe = re.sub(r"(?i)(token|password|secret|api[_-]?key)(\s*[=:]\s*)([^\s,;]+)", r"\1\2<redacted>", safe)
                    safe = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1<redacted>", safe)
                    safe = re.sub(r"(?i)(Authorization[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)", r"\1<redacted>", safe)
                    findings.append({"rule": rule_id, "severity": severity, "file": str(path), "line": line_no, "message": safe})
                    break
    return {"files_processed": processed, "files_skipped": skipped, "findings": findings,
            "limitations": ["Text-pattern diagnostics only; no causal proof or source-level runtime tracing.", "Logs may contain sensitive data; inspect and sanitize before sharing reports."]}

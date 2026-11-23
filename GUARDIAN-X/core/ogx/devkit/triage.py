from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ogx.analysis.architecture import analyze_architecture
from ogx.kernel.config import ScanConfig, load_config
from ogx.devkit.diagnostics import analyze_logs
from ogx.shield.scanner import scan_repository

TRACE_PATTERNS = (
    re.compile(r'''File\s+["']([^"']+)["'],\s*line\s+(\d+)'''),
    re.compile(r'''(?:^|[\s(])((?:[A-Za-z]:[\\/]|/|\.{0,2}[\\/])?[\w@ .-]+(?:[\\/][\w@ .-]+)*\.(?:py|js|jsx|ts|tsx|java|cs|go|rs|php|rb|swift|dart)):(\d+)(?::\d+)?''', re.I),
)


def _map_location(raw: str, root: Path, source_files: set[str]) -> str | None:
    value = raw.replace("\\", "/").strip()
    try:
        path = Path(value)
        if path.is_absolute():
            try:
                relative = path.resolve(strict=False).relative_to(root).as_posix()
            except ValueError:
                relative = value.lstrip("/")
        else:
            relative = value.removeprefix("./")
    except (OSError, ValueError):
        return None
    if relative in source_files:
        return relative
    matches = [item for item in source_files if item.casefold().endswith("/" + relative.casefold()) or relative.casefold().endswith("/" + item.casefold())]
    return matches[0] if len(matches) == 1 else None


def extract_trace_locations(log_paths: list[Path], root: Path, source_files: set[str], max_bytes: int = 5_000_000) -> list[dict[str, Any]]:
    found: set[tuple[str, int]] = set()
    for log in log_paths:
        path = log.expanduser()
        if path.is_symlink():
            continue
        path = path.resolve(strict=True)
        if not path.is_file() or path.stat().st_size > max_bytes:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            for pattern in TRACE_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                mapped = _map_location(match.group(1), root, source_files)
                try:
                    number = int(match.group(2))
                except ValueError:
                    number = 0
                if mapped and number > 0:
                    found.add((mapped, number))
                break
    return [{"file": file, "line": line} for file, line in sorted(found)]


def build_debug_report(root: Path, log_paths: list[Path], config: ScanConfig | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    config = config or load_config()
    scan = scan_repository(root, config)
    architecture = analyze_architecture(root, config)
    logs = analyze_logs(log_paths)
    locations = extract_trace_locations(log_paths, root, set(architecture["nodes"]))
    by_file: dict[str, list[dict[str, Any]]] = {}
    for finding in scan.findings:
        if finding.path:
            by_file.setdefault(finding.path.replace("\\", "/"), []).append({
                "rule_id": finding.rule_id, "severity": finding.severity,
                "title": finding.title, "line": finding.line,
                "confidence": finding.confidence,
            })
    correlations = []
    for location in locations:
        same_file = by_file.get(location["file"], [])
        nearby = [item for item in same_file if item["line"] is None or abs(item["line"] - location["line"]) <= 8]
        correlations.append({**location, "nearby_static_findings": nearby,
                             "correlation": "static finding near trace location" if nearby else "trace location only"})
    hypotheses = []
    if correlations:
        hypotheses.append({"type": "stack-trace-location", "confidence": "location-correlation-only",
                           "message": "Trace frames mapped to project source files; inspect those lines and nearby static findings."})
    for item in logs["findings"]:
        if item["rule"] in {"memory", "timeout", "database"}:
            hypotheses.append({"type": item["rule"], "confidence": "log-pattern-only",
                               "message": f"Log pattern suggests investigation of {item['rule']} behavior; no causal proof was established."})
    if not hypotheses:
        hypotheses.append({"type": "insufficient-evidence", "confidence": "low",
                           "message": "No supported traceback-to-source correlation or recognized diagnostic pattern was found."})
    recommendations = [
        "Reproduce the failure using a sanitized fixture and a disposable sandbox.",
        "Add a failing regression test before changing source code.",
        "Review the mapped source line, nearby static findings and upstream importers.",
        "Compare the failing revision with the last known good Git commit.",
        "Apply a reviewed patch only on a disposable workspace or dedicated feature branch.",
        "Re-run focused tests, the full available test suite and a second OGX scan.",
    ]
    return {"tool": "OMEGA GUARDIAN X", "project": scan.project.name, "root": str(root),
            "scan_id": scan.scan_id, "static_findings": [item.__dict__ if hasattr(item, "__dict__") else {
                "rule_id": item.rule_id, "title": item.title, "severity": item.severity,
                "path": item.path, "line": item.line, "confidence": item.confidence,
                "recommendation": item.recommendation} for item in scan.findings],
            "log_analysis": logs, "trace_locations": locations, "correlations": correlations,
            "architecture_summary": {"source_files": len(architecture["nodes"]), "import_edges": len(architecture["edges"]),
                                      "warnings": architecture["warnings"]},
            "root_cause_status": "hypotheses_only_not_proven", "root_cause_hypotheses": hypotheses,
            "recommended_next_steps": recommendations,
            "limitations": ["OGX does not execute the application or attach a live debugger in this workflow.",
                            "Log and traceback matching is heuristic; matching does not prove causation.",
                            "A root cause must be reproduced and verified with tests before it is considered confirmed."]}


def render_test_plan(debug_report: dict[str, Any]) -> str:
    lines = [f"# OGX Regression Test Plan — {debug_report['project']}", "",
             f"Scan ID: `{debug_report['scan_id']}`", "",
             "> Proposal only. OGX did not create test files or execute project code.", "",
             "## Reproduction and baseline", "",
             "1. Capture a minimal sanitized input that reproduces the failure.",
             "2. Record expected and actual behavior, environment and dependency versions.",
             "3. Add a failing test before implementing a fix.",
             "4. Run the test in the restricted sandbox with an approved, pre-pulled image.", "",
             "## Trace locations", ""]
    if debug_report["trace_locations"]:
        for item in debug_report["trace_locations"]:
            lines.append(f"- `{item['file']}:{item['line']}` — assert the failure condition and expected recovery behavior.")
    else:
        lines.append("- No source trace location was mapped; add an integration-level reproduction test from the supplied failure report.")
    lines += ["", "## Static findings", ""]
    if debug_report["static_findings"]:
        for item in debug_report["static_findings"]:
            lines.append(f"- [{item['severity'].upper()}] `{item.get('path') or 'project'}` — {item['title']}; add a regression assertion for the reported condition.")
    else:
        lines.append("- No enabled static findings; this is not proof that the project has no bugs.")
    lines += ["", "## Verification gates", "",
              "- Focused reproducer passes.", "- Existing unit and integration tests pass.",
              "- Regression suite passes repeatedly for flaky/concurrent paths.",
              "- Type checks, lint, build and dependency checks pass where available.",
              "- Second OGX scan introduces no new high-confidence regression finding.",
              "- Review the complete diff and rollback plan before any merge.", ""]
    return "\n".join(lines)

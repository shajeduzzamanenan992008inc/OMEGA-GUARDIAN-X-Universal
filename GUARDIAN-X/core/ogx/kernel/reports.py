from __future__ import annotations

import json
from pathlib import Path

from ogx.kernel.models import ScanReport


def write_json(report: ScanReport, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def render_markdown(report: ScanReport) -> str:
    project = report.project
    lines = [
        "# OMEGA GUARDIAN X — Scan Report", "",
        f"- **Scan ID:** `{report.scan_id}`",
        f"- **Project:** `{project.name}`",
        f"- **Root:** `{project.root}`",
        f"- **Started:** {report.started_at}",
        f"- **Finished:** {report.finished_at}",
        f"- **Files examined:** {report.files_examined}",
        f"- **Files skipped:** {report.files_skipped}", "",
        "## Detected technology", "",
        f"- **Languages:** {', '.join(project.languages) or 'Not detected'}",
        f"- **Ecosystems:** {', '.join(project.ecosystems) or 'Not detected'}",
        f"- **Framework indicators:** {', '.join(project.frameworks) or 'None detected'}",
        f"- **Manifests:** {', '.join(f'`{x}`' for x in project.manifests) or 'None'}",
        f"- **Lockfiles:** {', '.join(f'`{x}`' for x in project.lockfiles) or 'None'}", "",
        "## Summary", "",
        "| Severity | Count |", "|---|---:|",
    ]
    for severity, count in report.summary.items():
        lines.append(f"| {severity.title()} | {count} |")
    lines += ["", "## Findings", ""]
    if not report.findings:
        lines.append("No findings were produced by the enabled MVP rules. This is not a guarantee that the project is secure.")
    for index, finding in enumerate(report.findings, 1):
        location = f" — `{finding.path}`" if finding.path else ""
        if finding.line:
            location += f":{finding.line}"
        lines += [
            f"### {index}. [{finding.severity.upper()}] {finding.title}", "",
            f"- **Rule:** `{finding.rule_id}`",
            f"- **Category:** {finding.category}",
            f"- **Location:** {location or 'Repository-wide'}",
            f"- **Confidence:** {finding.confidence}",
            f"- **Message:** {finding.message}",
        ]
        if finding.evidence:
            safe_evidence = finding.evidence.replace("`", "\\`")
            lines.append(f"- **Evidence:** `{safe_evidence}`")
        if finding.recommendation:
            lines.append(f"- **Recommendation:** {finding.recommendation}")
        lines.append("")
    lines += ["## Capability limits", "", "| Capability | Status |", "|---|---|"]
    for key, value in report.capabilities.items():
        lines.append(f"| {key.replace('_', ' ').title()} | {value} |")
    if report.warnings:
        lines += ["", "## Warnings", ""] + [f"- {warning}" for warning in report.warnings]
    lines += ["", "> This MVP performs local static checks only. It does not resolve every ecosystem, query live CVE feeds, prove exploitability, run tests, or certify compliance.", ""]
    return "\n".join(lines)


def write_markdown(report: ScanReport, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(report), encoding="utf-8")
    return destination


def render_sarif(report: ScanReport) -> dict:
    """Render findings as SARIF 2.1.0 for supported code-scanning import pipelines."""
    levels = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
    rules: dict[str, dict] = {}
    results = []
    for finding in report.findings:
        rules.setdefault(finding.rule_id, {
            "id": finding.rule_id,
            "name": finding.title,
            "shortDescription": {"text": finding.title},
            "fullDescription": {"text": finding.message},
            "defaultConfiguration": {"level": levels.get(finding.severity, "warning")},
            "properties": {"category": finding.category},
        })
        result = {
            "ruleId": finding.rule_id,
            "level": levels.get(finding.severity, "warning"),
            "message": {"text": finding.message},
            "properties": {"severity": finding.severity, "confidence": finding.confidence,
                           "recommendation": finding.recommendation or "Review manually"},
        }
        if finding.path:
            location = {"physicalLocation": {"artifactLocation": {"uri": finding.path.replace("\\", "/")}}}
            if finding.line:
                location["physicalLocation"]["region"] = {"startLine": finding.line}
            result["locations"] = [location]
        results.append(result)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "OMEGA GUARDIAN X", "version": report.version, "rules": list(rules.values())}},
                  "invocations": [{"executionSuccessful": True, "properties": {"scanId": report.scan_id,
                                    "filesExamined": report.files_examined, "filesSkipped": report.files_skipped,
                                    "warnings": report.warnings}}],
                  "results": results}],
    }


def write_sarif(report: ScanReport, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(render_sarif(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination

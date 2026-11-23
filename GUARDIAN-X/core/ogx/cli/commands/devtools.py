"""Development commands: log diagnosis, debug correlation, test plans and fix plans."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ogx.devkit.diagnostics import analyze_logs
from ogx.devkit.triage import build_debug_report, render_test_plan
from ogx.kernel.config import load_config
from ogx.shield.scanner import scan_repository
from ogx.cli.common import _json_print, _prepare_report_dir, _resolve_project


def cmd_diagnose(args: argparse.Namespace) -> int:
    try:
        paths = [Path(item) for item in args.logs]
        result = analyze_logs(paths, max_bytes=args.max_bytes)
    except (OSError, ValueError) as exc:
        print(f"Log diagnostics failed: {exc}", file=sys.stderr); return 2
    _json_print(result)
    return 1 if any(f["severity"] == "high" for f in result["findings"]) else 0


def cmd_debug(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        report = build_debug_report(root, [Path(item) for item in args.logs], load_config())
        report_dir = _prepare_report_dir(root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path = report_dir / f"debug-triage-{stamp}.json"
        md_path = report_dir / f"debug-triage-{stamp}.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        lines = [f"# OGX Debug Triage — {report['project']}", "",
                 f"Scan ID: `{report['scan_id']}`", "",
                 f"Root-cause status: **{report['root_cause_status']}**", "",
                 "## Trace locations", ""]
        lines.extend([f"- `{x['file']}:{x['line']}` — {x['correlation']}" for x in report["correlations"]] or ["- No project source mapping found."])
        lines += ["", "## Root-cause hypotheses", ""]
        lines.extend([f"- **{x['type']}** ({x['confidence']}): {x['message']}" for x in report["root_cause_hypotheses"]])
        lines += ["", "## Recommended next steps", ""] + [f"{i}. {x}" for i, x in enumerate(report["recommended_next_steps"], 1)]
        lines += ["", "## Limitations", ""] + [f"- {x}" for x in report["limitations"]] + [""]
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Debug triage: {len(report['correlations'])} trace locations; {len(report['static_findings'])} static findings; {len(report['log_analysis']['findings'])} log patterns.")
        print(f"External reports: {json_path} | {md_path}")
        print("No project code was executed or modified; root cause remains a hypothesis until reproduced.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Debug triage failed: {exc}", file=sys.stderr); return 2


def cmd_test_plan(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        report = build_debug_report(root, [Path(item) for item in args.logs], load_config())
        report_dir = _prepare_report_dir(root)
        output = report_dir / f"regression-test-plan-{report['scan_id']}.md"
        output.write_text(render_test_plan(report), encoding="utf-8")
        print(f"Regression test plan: {output}")
        print("Proposal only; OGX did not create tests or edit the project.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Test plan failed: {exc}", file=sys.stderr); return 2


def cmd_fix_plan(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        report = scan_repository(root, load_config())
        lines = [f"# OGX Remediation Plan — {report.project.name}", "", f"Scan ID: `{report.scan_id}`", "",
                 "> Proposal only. No project files were changed and no fixes were applied.", "",
                 "| Severity | Rule | File | Line | Recommendation |", "|---|---|---|---:|---|"]
        for item in report.findings:
            recommendation = (item.recommendation or "Review manually").replace("|", "\\|")
            lines.append(f"| {item.severity} | {item.rule_id} | {item.path or ''} | {item.line or ''} | {recommendation} |")
        report_dir = _prepare_report_dir(root)
        output = report_dir / f"remediation-plan-{report.scan_id}.md"
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Proposed actions: {len(report.findings)}\nPlan: {output}\nNo project files were modified.")
    except (OSError, ValueError) as exc:
        print(f"Could not build remediation plan: {exc}", file=sys.stderr); return 2
    return 0



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
    diagnose = sub.add_parser("diagnose", help="Analyze existing log files without executing the project")
    diagnose.add_argument("logs", nargs="+", help="Log file paths")
    diagnose.add_argument("--max-bytes", type=int, default=5_000_000)
    diagnose.set_defaults(func=cmd_diagnose)

    debug = sub.add_parser("debug", help="Correlate logs, traces and static findings; does not execute the project")
    debug.add_argument("path", nargs="?")
    debug.add_argument("--project")
    debug.add_argument("--logs", nargs="+", required=True)
    debug.set_defaults(func=cmd_debug)

    test_plan = sub.add_parser("test-plan", help="Generate a regression-test plan from scans and optional logs")
    test_plan.add_argument("path", nargs="?")
    test_plan.add_argument("--project")
    test_plan.add_argument("--logs", nargs="*", default=[])
    test_plan.set_defaults(func=cmd_test_plan)

    fixplan = sub.add_parser("fix-plan", help="Generate evidence-backed remediation proposals; does not edit source")
    fixplan.add_argument("path", nargs="?")
    fixplan.add_argument("--project")
    fixplan.set_defaults(func=cmd_fix_plan)

"""Read-only analysis commands: detect, scan, engines, analyze, android, inventory, architecture, impact, validate, policy, doctor, compatibility."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ogx import __version__
from ogx.analysis.architecture import analyze_architecture, architecture_mermaid
from ogx.analysis.compatibility import compatibility_report
from ogx.analysis.detector import detect_project
from ogx.analysis.engines.registry import EngineRegistry
from ogx.analysis.impact import analyze_impact
from ogx.analysis.inventory import build_inventory
from ogx.analysis.pipeline import (
    analyze_android,
    analyze_project,
    inspect_engines,
    render_android_markdown,
    render_universal_markdown,
)
from ogx.analysis.toolchains import discover_toolchains
from ogx.kernel.config import load_config
from ogx.kernel.reports import write_json, write_markdown, write_sarif
from ogx.shield.policy import load_project_policy
from ogx.shield.scanner import scan_repository
from ogx.cli.common import (
    BANNER,
    _engine_ids,
    _json_print,
    _prepare_report_dir,
    _resolve_project,
    _root,
    _write_report_bundle,
)


def cmd_detect(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Error: directory not found: {root}", file=sys.stderr)
        return 2
    profile = detect_project(root)
    print(json.dumps({
        "name": profile.name, "root": profile.root, "languages": profile.languages,
        "ecosystems": profile.ecosystems, "frameworks": profile.frameworks,
        "manifests": profile.manifests, "lockfiles": profile.lockfiles,
        "ci_files": profile.ci_files,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Safe onboarding alias: detect and scan without project-local initialization."""
    print("Safe external mode: no files will be created in the target repository.")
    return cmd_scan(args)


def cmd_scan(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            raise ValueError(f"Project directory not found: {root}")
        config = load_config()
        report = scan_repository(root, config)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"scan-{stamp}-{report.scan_id}"
    outputs: list[Path] = []
    try:
        report_dir = _prepare_report_dir(root)
        if args.format in ("json", "both"):
            outputs.append(write_json(report, report_dir / f"{base}.json"))
        if args.format in ("markdown", "both"):
            outputs.append(write_markdown(report, report_dir / f"{base}.md"))
        if args.format == "sarif":
            outputs.append(write_sarif(report, report_dir / f"{base}.sarif"))
    except OSError as exc:
        print(f"Could not write external report: {exc}", file=sys.stderr)
        return 2
    summary = report.summary
    print(BANNER)
    print(f"Project: {report.project.name}")
    print(f"Languages: {', '.join(report.project.languages) or 'Unknown'}")
    print(f"Files examined: {report.files_examined}; skipped: {report.files_skipped}")
    print("Findings: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    for output in outputs:
        print(f"External report: {output}")
    print("Target repository: READ-ONLY; no project-local files created or changed.")
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    if args.strict and (summary["critical"] or summary["high"]):
        return 1
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    """Inspect the engine registry without analyzing or executing a project."""
    if args.engines_action == "list":
        registry = EngineRegistry()
        entries = [{
            "engine_id": engine.engine_id,
            "engine_version": engine.engine_version,
            "display_name": engine.display_name,
            "category": engine.category,
            "language_keys": list(engine.language_keys),
            "health": engine.health(),
        } for engine in registry.all()]
        result = {
            "schema": "ogx-engine-registry-1",
            "tool": "OMEGA GUARDIAN X",
            "engine_version": __version__,
            "count": len(entries),
            "engines": entries,
        }
        if args.json:
            _json_print(result)
        else:
            print(BANNER)
            print(f"Registered engines: {len(entries)} (static-read-only; no project code is executed)")
            print(f"{'ENGINE':24} {'CATEGORY':15} {'VERSION':10} DISPLAY NAME")
            for entry in entries:
                print(f"{entry['engine_id']:24} {entry['category']:15} "
                      f"{entry['engine_version']:10} {entry['display_name']}")
        return 0

    try:
        root = _resolve_project(args)
        if not root.is_dir():
            raise ValueError(f"Project directory not found: {root}")
        result = inspect_engines(root, engine_ids=_engine_ids(args.engines))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Engine inspection failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _json_print(result)
        return 0
    print(BANNER)
    print(f"Project: {result['project']['name']}")
    print(f"Files indexed: {result['project']['file_count']}; "
          f"detected engines: {len(result['detected_engines'])} of {len(result['engines'])}")
    print(f"{'ENGINE':24} {'DETECTED':9} {'ANALYZE':14} {'DEPS':14} {'BUILD':14} {'TEST':14}")
    for entry in result["engines"]:
        caps = entry["capabilities"]
        values = [caps.get(name, {}).get("status", "unsupported")
                  for name in ("analyze", "dependencies", "build", "test")]
        print(f"{entry['engine_id']:24} {str(entry['detected']):9} "
              + " ".join(f"{value:14}" for value in values))
    for warning in result["warnings"]:
        print(f"WARNING: {warning}")
    for error in result["errors"]:
        print(f"ERROR: {error}")
    print("No project code, build tool, package manager, device or network was executed or contacted.")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Run the universal static engine pipeline and write external reports."""
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            raise ValueError(f"Project directory not found: {root}")
        report = analyze_project(root, engine_ids=_engine_ids(args.engines))
        outputs = _write_report_bundle(root, "universal-analysis", report, args.format,
                                       render_universal_markdown)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Universal analysis failed: {exc}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(BANNER)
    print(f"Project: {report['project']['name']}")
    print(f"Detected engines: {len(report['detected_engines'])} of {len(report['engines'])}")
    print("Findings: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
    for output in outputs:
        print(f"External report: {output}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    for error in report["errors"]:
        print(f"ERROR: {error}")
    print("Target repository: READ-ONLY; no project-local files created or changed.")
    if args.strict and (summary["critical"] or summary["high"]):
        return 1
    return 0


def cmd_android(args: argparse.Namespace) -> int:
    """Run the dedicated Android engine without build/device execution."""
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            raise ValueError(f"Project directory not found: {root}")
        report = analyze_android(root)
        outputs = _write_report_bundle(root, "android-analysis", report, args.format,
                                       render_android_markdown)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Android analysis failed: {exc}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(BANNER)
    print(f"Project: {report['project']['name']}")
    print(f"Android detected: {report['detected']}")
    print("Findings: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
    for output in outputs:
        print(f"External report: {output}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    for error in report["errors"]:
        print(f"ERROR: {error}")
    print("No Gradle build, Android SDK tool, adb command, emulator, device or APK install was executed.")
    if args.strict and (summary["critical"] or summary["high"]):
        return 1
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        result = build_inventory(root)
    except (OSError, ValueError) as exc:
        print(f"Inventory failed: {exc}", file=sys.stderr); return 2
    if args.json:
        _json_print(result)
    else:
        print(f"Project: {root}\nFiles: {result['file_count']} | Skipped: {result['skipped']} | Size: {result['total_bytes']:,} bytes")
        print("Top extensions:")
        for ext, count in list(result["extensions"].items())[:25]: print(f"  {ext:18} {count}")
        for warning in result["warnings"]: print("WARNING: " + warning)
    return 0


def cmd_architecture(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        result = analyze_architecture(root)
    except (OSError, ValueError) as exc:
        print(f"Architecture analysis failed: {exc}", file=sys.stderr); return 2
    if args.format == "mermaid":
        print(architecture_mermaid(result), end="")
    else:
        _json_print(result)
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    try:
        result = analyze_impact(_resolve_project(args), args.file, load_config())
        _json_print(result)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Impact analysis failed: {exc}", file=sys.stderr); return 2


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        profile = detect_project(root)
        report = scan_repository(root, load_config())
        policy = load_project_policy(root)
        validation = {"project": profile.name, "root": str(root), "languages": profile.languages,
                      "file_scan": {"files_examined": report.files_examined, "files_skipped": report.files_skipped,
                                    "findings": report.summary, "warnings": report.warnings},
                      "policy": policy, "checks": {"python_syntax": "checked when Python files are scanned",
                      "json_manifests": "package manifests parsed by scanner", "runtime_tests": "not executed",
                      "build": "not executed", "security_certification": "not provided"}}
        _json_print(validation)
        return 1 if report.summary["critical"] or report.summary["high"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr); return 2


def cmd_compatibility(args: argparse.Namespace) -> int:
    try:
        _json_print(compatibility_report(_resolve_project(args)))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Compatibility inspection failed: {exc}", file=sys.stderr); return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    tools = discover_toolchains()
    available = sum(1 for item in tools if item["available"])
    print(f"OGX environment doctor: {available}/{len(tools)} optional tools detected")
    for item in tools:
        status = "FOUND" if item["available"] else "not found"
        print(f"{status:10} {item['name']:16} {item['purpose']}")
    print("Detection only: OGX does not install or execute these tools during doctor checks.")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    try:
        _json_print(load_project_policy(_root(args.path)))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Policy check failed: {exc}", file=sys.stderr); return 2



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
    for command, help_text, handler in (
        ("detect", "Detect project technologies without modifying the repository", cmd_detect),
        ("scan", "Read-only scan; reports are stored in external OGX data directory", cmd_scan),
        ("start", "Safe onboarding alias for detect + scan; no project-local writes", cmd_start),
    ):
        item = sub.add_parser(command, help=help_text)
        item.add_argument("path", nargs="?", default=None)
        item.add_argument("--project", help="Name of a registered project")
        if command in {"scan", "start"}:
            item.add_argument("--format", choices=("json", "markdown", "both", "sarif"), default="both")
            item.add_argument("--strict", action="store_true", help="Return 1 when high/critical findings exist")
        item.set_defaults(func=handler)

    engines = sub.add_parser("engines", help="List or inspect capability-declared analysis engines")
    engines_sub = engines.add_subparsers(dest="engines_action", required=True)
    engines_list = engines_sub.add_parser("list", help="List registered engines without scanning")
    engines_list.add_argument("--json", action="store_true", help="Print full registry JSON")
    engines_list.set_defaults(func=cmd_engines)
    engines_inspect = engines_sub.add_parser("inspect", help="Inspect detection and capabilities for a project")
    engines_inspect.add_argument("path", nargs="?", default=None)
    engines_inspect.add_argument("--project", help="Name of a registered project")
    engines_inspect.add_argument("--engines", nargs="+", metavar="ID",
                                 help="Engine IDs to inspect; accepts repeated or comma-separated values")
    engines_inspect.add_argument("--json", action="store_true", help="Print full inspection JSON")
    engines_inspect.set_defaults(func=cmd_engines)

    analyze = sub.add_parser("analyze", help="Run the universal static engine pipeline; writes external reports")
    analyze.add_argument("path", nargs="?", default=None)
    analyze.add_argument("--project", help="Name of a registered project")
    analyze.add_argument("--format", choices=("json", "markdown", "both"), default="both")
    analyze.add_argument("--engines", nargs="+", metavar="ID",
                         help="Limit to engine IDs; accepts repeated or comma-separated values")
    analyze.add_argument("--strict", action="store_true", help="Return 1 when high/critical findings exist")
    analyze.set_defaults(func=cmd_analyze)

    android = sub.add_parser("android", help="Run the dedicated Android static engine; no build/device execution")
    android.add_argument("path", nargs="?", default=None)
    android.add_argument("--project", help="Name of a registered project")
    android.add_argument("--format", choices=("json", "markdown", "both"), default="both")
    android.add_argument("--strict", action="store_true", help="Return 1 when high/critical findings exist")
    android.set_defaults(func=cmd_android)

    inventory = sub.add_parser("inventory", help="Build a bounded file and technology inventory")
    inventory.add_argument("path", nargs="?")
    inventory.add_argument("--project")
    inventory.add_argument("--json", action="store_true", help="Print full inventory JSON")
    inventory.set_defaults(func=cmd_inventory)

    architecture = sub.add_parser("architecture", help="Build a static source import/dependency graph")
    architecture.add_argument("path", nargs="?")
    architecture.add_argument("--project")
    architecture.add_argument("--format", choices=("json", "mermaid"), default="json")
    architecture.set_defaults(func=cmd_architecture)

    validate = sub.add_parser("validate", help="Run static project validation; never executes target code")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--project")
    validate.set_defaults(func=cmd_validate)

    impact = sub.add_parser("impact", help="Estimate static import dependents for a changed source file")
    impact.add_argument("path", nargs="?")
    impact.add_argument("--project")
    impact.add_argument("--file", required=True, help="Changed file inside the target project")
    impact.set_defaults(func=cmd_impact)

    policy = sub.add_parser("policy", help="Inspect target project OGX policy")
    policy.add_argument("path")
    policy.set_defaults(func=cmd_policy)

    doctor = sub.add_parser("doctor", help="Detect optional local toolchains and security analyzers")
    doctor.set_defaults(func=cmd_doctor)

    compatibility = sub.add_parser("compatibility", help="Report detected stack and installed toolchain capability")
    compatibility.add_argument("path", nargs="?")
    compatibility.add_argument("--project")
    compatibility.set_defaults(func=cmd_compatibility)

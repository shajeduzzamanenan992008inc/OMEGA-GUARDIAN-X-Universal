"""Protection commands: Docker sandbox and the project guardian."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from ogx.kernel.config import load_config
from ogx.shield.guardian import (
    apply_safe_fixes,
    build_evolution_report,
    build_guardian_audit,
    check_upstream,
    fetch_remote,
    plan_safe_fixes,
    render_guardian_markdown,
)
from ogx.shield.sandbox import run_in_sandbox, sandbox_plan
from ogx.cli.common import (
    _json_print,
    _prepare_report_dir,
    _print_approval_queued,
    _queue_email_approval,
    _requires_email_approval,
    _resolve_project,
    _root,
)


def cmd_sandbox(args: argparse.Namespace) -> int:
    try:
        if args.sandbox_action == "plan":
            _json_print(sandbox_plan())
            return 0
        if not args.confirm:
            raise PermissionError("Sandbox commands execute the supplied program in a disposable container; pass --confirm after reviewing it.")
        command = list(args.command or [])
        if command and command[0] == "--": command = command[1:]
        result = run_in_sandbox(_root(args.path), args.image, command, timeout=args.timeout, memory=args.memory, cpus=args.cpus)
        _json_print(result)
        return int(result["exit_code"] != 0)
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Sandbox operation failed: {exc}", file=sys.stderr); return 2


def cmd_guardian(args: argparse.Namespace) -> int:
    """Run universal Guardian audits, guarded repairs and Git upgrade workflows."""
    try:
        root = _resolve_project(args)
        action = args.guardian_action
        if action in {"audit", "maintain", "evolve"}:
            report = build_evolution_report(root, load_config()) if action == "evolve" else build_guardian_audit(root, load_config())
            report_dir = _prepare_report_dir(root)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            base = f"guardian-{action}-{stamp}"
            json_path = report_dir / f"{base}.json"
            md_path = report_dir / f"{base}.md"
            json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            md_path.write_text(render_guardian_markdown(report), encoding="utf-8")
            health = report["health"]
            print(f"OMEGA GUARDIAN X — {action.upper()}")
            print(f"Project: {report['project']['name']} | Languages: {', '.join(report['project']['languages']) or 'Unknown'}")
            print(f"Files: {health['files_examined']} examined / {health['files_skipped']} skipped")
            print("Findings: " + ", ".join(f"{key}={value}" for key, value in health["finding_counts"].items()))
            print("Recommended actions: " + str(len(report["recommendations"])))
            print(f"Reports: {json_path} | {md_path}")
            print("Project source was not executed or modified.")
            if action == "evolve":
                print("Evolution mode: evidence-based proposals only; no autonomous self-rewrite.")
            if getattr(args, "strict", False) and (health["finding_counts"]["critical"] or health["finding_counts"]["high"]):
                return 1
            return 0
        if action == "repair":
            if args.confirm and _requires_email_approval():
                queued = _queue_email_approval("guardian.repair", root,
                    "Apply only deterministic, allowlisted low-risk fixes shown by `ogx guardian repair-plan`.",
                    requester_subject=getattr(args, "requester_subject", None))
                _print_approval_queued(queued)
                return 0
            result = apply_safe_fixes(root, confirm=args.confirm)
            _json_print(result)
            return 1 if result.get("blocked") else 0
        if action == "repair-plan":
            _json_print({"root": str(root), "supported_safe_fixes": plan_safe_fixes(root),
                         "message": "No files were changed. Use `ogx guardian repair PATH --confirm` to apply only supported fixes."})
            return 0
        if action == "upgrade-check":
            result = check_upstream(root, online=args.online)
            _json_print(result)
            return 0 if not any(x.get("status") == "unreachable_or_unauthorized" for x in result.get("remotes", [])) else 1
        if action == "upgrade-fetch":
            if args.confirm and _requires_email_approval():
                queued = _queue_email_approval("guardian.upgrade_fetch", root,
                    f"Fetch references from Git remote '{args.remote}' only; do not merge, push or deploy.",
                    remote=args.remote, requester_subject=getattr(args, "requester_subject", None))
                _print_approval_queued(queued)
                return 0
            result = fetch_remote(root, remote=args.remote, confirm=args.confirm)
            _json_print(result)
            return 0
        raise ValueError(f"Unsupported Guardian action: {action}")
    except (OSError, RuntimeError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        print(f"Guardian operation failed: {exc}", file=sys.stderr)
        return 2



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
    sandbox = sub.add_parser("sandbox", help="Plan or run commands inside a disposable restricted Docker container")
    sandbox_sub = sandbox.add_subparsers(dest="sandbox_action", required=True)
    sandbox_plan_cmd = sandbox_sub.add_parser("plan", help="Inspect local sandbox capability and defaults")
    sandbox_plan_cmd.set_defaults(func=cmd_sandbox)
    sandbox_run = sandbox_sub.add_parser("run", help="Run a command against a disposable source snapshot")
    sandbox_run.add_argument("path")
    sandbox_run.add_argument("--image", required=True, help="Pre-pulled immutable image@sha256:digest")
    sandbox_run.add_argument("--timeout", type=int, default=300)
    sandbox_run.add_argument("--memory", choices=("384m", "512m", "768m", "1g", "1536m", "2g"), default="768m")
    sandbox_run.add_argument("--cpus", choices=("0.5", "1", "1.5", "2"), default="1")
    sandbox_run.add_argument("--confirm", action="store_true")
    sandbox_run.add_argument("command", nargs=argparse.REMAINDER, help="Command argv after --; shell syntax is not interpreted")
    sandbox_run.set_defaults(func=cmd_sandbox)

    guardian = sub.add_parser("guardian", help="Universal project guardian: audit, maintenance, safe repair and hosted-upgrade checks")
    guardian_sub = guardian.add_subparsers(dest="guardian_action", required=True)
    for action, help_text in (
        ("audit", "Run a combined stack, security, quality, architecture and maintenance audit"),
        ("maintain", "Build a prioritized maintenance report without changing the project"),
        ("evolve", "Run a self-improvement audit and produce a governed evolution proposal"),
    ):
        item = guardian_sub.add_parser(action, help=help_text)
        item.add_argument("path", nargs="?")
        item.add_argument("--project")
        item.add_argument("--strict", action="store_true", help="Return 1 when critical/high findings exist")
        item.set_defaults(func=cmd_guardian)
    repair_plan = guardian_sub.add_parser("repair-plan", help="Preview deterministic, low-risk fixes supported by OGX")
    repair_plan.add_argument("path", nargs="?")
    repair_plan.add_argument("--project")
    repair_plan.set_defaults(func=cmd_guardian)
    repair = guardian_sub.add_parser("repair", help="Apply only allowlisted safe fixes, with external backup")
    repair.add_argument("path", nargs="?")
    repair.add_argument("--project")
    repair.add_argument("--confirm", action="store_true", help="Queue an email approval before applying supported fixes")
    repair.add_argument("--requester-subject", help="External identity; defaults to OGX_ACTOR_SUBJECT")
    repair.set_defaults(func=cmd_guardian)
    upgrade_check = guardian_sub.add_parser("upgrade-check", help="Inspect local Git remotes; --online performs read-only remote lookup")
    upgrade_check.add_argument("path", nargs="?")
    upgrade_check.add_argument("--project")
    upgrade_check.add_argument("--online", action="store_true", help="Contact configured Git remotes to compare branch heads")
    upgrade_check.set_defaults(func=cmd_guardian)
    upgrade_fetch = guardian_sub.add_parser("upgrade-fetch", help="Fetch remote refs only; never merge or alter project files")
    upgrade_fetch.add_argument("path", nargs="?")
    upgrade_fetch.add_argument("--project")
    upgrade_fetch.add_argument("--remote", default="origin")
    upgrade_fetch.add_argument("--confirm", action="store_true", help="Queue an email approval before remote fetch")
    upgrade_fetch.add_argument("--requester-subject", help="External identity; defaults to OGX_ACTOR_SUBJECT")
    upgrade_fetch.set_defaults(func=cmd_guardian)

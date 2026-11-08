from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import load_config, state_dir
from .envfile import (
    EnvFileError, create_env_file, configured_settings, file_permission_warning,
    load_runtime_env, resolve_env_path, validate_runtime_config,
)
from .detector import detect_project
from .reports import write_json, write_markdown, write_sarif
from .scanner import scan_repository
from .integration import apply_integration, integration_status, plan_integration, remove_integration
from .inventory import build_inventory
from .architecture import analyze_architecture, architecture_mermaid
from .sbom import generate_sbom
from .diagnostics import analyze_logs
from .agents.registry import discover_agents
from .skills.registry import list_skills, validate_skill
from .policy import load_project_policy
from .connectors.git import inspect_repository, inspect_remote, clone_repository, _run as git_run
from .connectors.providers import ProviderClient
from .licenses import inventory_licenses
from .toolchains import discover_toolchains
from .compatibility import compatibility_report
from .sandbox import sandbox_plan, run_in_sandbox
from .enterprise.store import EnterpriseStore
from .enterprise.approvals import ApprovalManager
from .enterprise.governance import framework_document
from .osv import audit_components
from .triage import build_debug_report, render_test_plan
from .impact import analyze_impact
from .guardian import (
    apply_safe_fixes, build_evolution_report, build_guardian_audit, check_upstream,
    fetch_remote, plan_safe_fixes, render_guardian_markdown,
)

BANNER = "OMEGA GUARDIAN X — Universal Repository Intelligence"
GLOBAL_CONFIG = """# OMEGA GUARDIAN X global configuration
# Stored outside scanned repositories.
[scan]
exclude = [".git", "node_modules", ".venv", "venv", "dist", "build", "vendor"]
max_files = 12000
max_file_bytes = 2000000
"""


def _root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _project_registry_path() -> Path:
    return state_dir() / "projects.json"


def _load_projects() -> dict[str, str]:
    path = _project_registry_path()
    if path.is_symlink():
        raise ValueError("OGX project registry must not be a symlink.")
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError("Invalid OGX project registry.")
    return data


def _save_projects(projects: dict[str, str]) -> None:
    path = _project_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    temp.replace(path)


def _resolve_project(args: argparse.Namespace) -> Path:
    if getattr(args, "project", None):
        projects = _load_projects()
        if args.project not in projects:
            raise ValueError(f"Unknown registered project: {args.project}. Run: ogx projects list")
        return _root(projects[args.project])
    if getattr(args, "path", None):
        return _root(args.path)
    raise ValueError("Provide a repository path or --project NAME.")


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize OGX global state only; never create files in a target repository."""
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.toml"
    if config_path.is_symlink():
        print("Error: global OGX config must not be a symlink.", file=sys.stderr)
        return 2
    if config_path.exists() and not args.force:
        print(f"Global configuration already exists; left unchanged: {config_path}")
    else:
        config_path.write_text(GLOBAL_CONFIG, encoding="utf-8")
        print(f"Created global OGX configuration: {config_path}")
    print("No target repository was modified.")
    return 0



def cmd_env(args: argparse.Namespace) -> int:
    """Manage OGX secrets/config without exposing configured values."""
    try:
        if args.env_action == "init":
            target = create_env_file(Path(args.path) if args.path else None)
            print(f"Created private OGX environment template: {target}")
            print("Edit this file, then run: ogx env check")
            print("The file is not written into a scanned repository unless you explicitly pass --path.")
            return 0
        if args.env_action == "path":
            print(resolve_env_path())
            return 0
        if args.env_action == "check":
            target = resolve_env_path()
            if not target.exists():
                print(f"Environment file not found: {target}")
                print("Create it with: ogx env init")
            else:
                print(f"Environment file: {target}")
            warning = file_permission_warning(target)
            if warning:
                print(f"WARNING: {warning}")
            errors = validate_runtime_config()
            for error in errors:
                print(f"ERROR: {error}")
            if not errors:
                print("Configuration checks passed. Credential values were not displayed.")
            configured = [name for name, present in configured_settings() if present]
            print("Configured settings: " + (", ".join(configured) if configured else "none"))
            return 1 if errors or warning else 0
        raise ValueError(f"Unsupported env action: {args.env_action}")
    except (EnvFileError, OSError, ValueError) as exc:
        print(f"Environment configuration failed: {exc}", file=sys.stderr)
        return 2

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


def _external_report_dir(root: Path) -> Path:
    canonical = str(root.resolve()).casefold()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", root.name).strip(".-") or "project"
    return state_dir() / "projects" / f"{safe_name}-{digest}" / "reports"


def _prepare_report_dir(root: Path) -> Path:
    """Create OGX external report directories while refusing symlinked components."""
    target = _external_report_dir(root)
    base = state_dir()
    if base.resolve(strict=False).is_relative_to(root.resolve()):
        raise ValueError("OGX_HOME must resolve outside the target project so reports remain external.")
    directories = [base, base / "projects", target.parent, target]
    for directory in directories:
        if directory.is_symlink():
            raise OSError(f"OGX report directory must not be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError(f"OGX report directory became a symlink: {directory}")
    return target


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


def cmd_projects_add(args: argparse.Namespace) -> int:
    root = _root(args.path)
    if not root.is_dir():
        print(f"Error: directory not found: {root}", file=sys.stderr)
        return 2
    name = args.name or root.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        print("Error: project name must be 1-64 letters, digits, dot, underscore or hyphen.", file=sys.stderr)
        return 2
    try:
        projects = _load_projects()
        projects[name] = str(root)
        _save_projects(projects)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not register project: {exc}", file=sys.stderr)
        return 2
    print(f"Registered {name}: {root}")
    print("The target repository was not modified.")
    return 0


def cmd_projects_list(args: argparse.Namespace) -> int:
    try:
        projects = _load_projects()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not read project registry: {exc}", file=sys.stderr)
        return 2
    if not projects:
        print("No projects registered. Add one with: ogx projects add <path>")
    for name, path in sorted(projects.items()):
        print(f"{name}: {path}")
    return 0


def _requires_email_approval() -> bool:
    """Mutating commands are email-gated by default; explicit local break-glass is opt-in."""
    return os.environ.get("OGX_ALLOW_UNAPPROVED_WRITES", "0").strip().lower() not in {"1", "true", "yes"}


def _queue_action_approval(operation: str, payload: dict[str, str], summary: str,
                           requester_subject: str | None = None) -> dict[str, object]:
    actor = (requester_subject or os.environ.get("OGX_ACTOR_SUBJECT", "")).strip()
    if not actor:
        raise ValueError("Set OGX_ACTOR_SUBJECT to the requesting user's external identity before requesting a write.")
    return ApprovalManager().create(operation=operation, payload=payload,
                                    requester_subject=actor, summary=summary)


def _queue_email_approval(operation: str, root: Path, summary: str, remote: str | None = None,
                          requester_subject: str | None = None) -> dict[str, object]:
    payload: dict[str, str] = {"path": str(root.resolve())}
    if remote is not None:
        payload["remote"] = remote
    return _queue_action_approval(operation, payload, summary, requester_subject)


def _print_approval_queued(result: dict[str, object]) -> None:
    print("Email approval request sent; no project changes have been made.")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("After approval, the original requester runs: ogx governance execute " + str(result["request_id"]))


def cmd_integrate(args: argparse.Namespace) -> int:
    try:
        if args.integration_action == "plan":
            result = plan_integration(Path(args.path))
            print(f"OGX integration plan: {result['project']}\nTarget: {result['root']}")
            for item in result["files"]:
                print(f"  {item['status']:18} {item['path']}")
            print("\nNo files were changed. Run `ogx integrate apply PATH --confirm` after reviewing the plan.")
        elif args.integration_action == "apply":
            if not args.confirm:
                print("Refusing to write files without --confirm. Review `ogx integrate plan PATH` first.", file=sys.stderr)
                return 2
            if _requires_email_approval():
                result = _queue_email_approval("integration.apply", _root(args.path),
                    "Create only missing OGX-managed governance files in the selected project root.",
                    requester_subject=getattr(args, "requester_subject", None))
                _print_approval_queued(result)
                return 0
            result = apply_integration(Path(args.path))
            print(f"OGX project integration applied: {result['root']}")
            print(f"Created: {len(result['created'])}; preserved existing/conflicting files: {len(result['preserved'])}")
            print(f"Ownership manifest: {result['manifest']}")
            for item in result["created"]:
                print(f"  CREATED   {item}")
            for item in result["preserved"]:
                print(f"  PRESERVED {item}")
        elif args.integration_action == "status":
            print(json.dumps(integration_status(Path(args.path)), indent=2, ensure_ascii=False))
        elif args.integration_action == "remove":
            if not args.confirm:
                print("Refusing to remove files without --confirm.", file=sys.stderr)
                return 2
            if _requires_email_approval():
                result = _queue_email_approval("integration.remove", _root(args.path),
                    "Remove only unchanged OGX-managed integration files; preserve modified files.",
                    requester_subject=getattr(args, "requester_subject", None))
                _print_approval_queued(result)
                return 0
            result = remove_integration(Path(args.path))
            print(f"Removed unchanged OGX-managed files from: {result['root']}")
            print(f"Removed: {len(result['removed'])}; preserved modified/missing files: {len(result['preserved_modified_or_missing'])}")
            for item in result["preserved_modified_or_missing"]:
                print(f"  PRESERVED {item}")
        return 0
    except (ValueError, OSError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
        print(f"Integration failed: {exc}", file=sys.stderr)
        return 2


def _json_print(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


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


def cmd_sbom(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        result = generate_sbom(root)
        report_dir = _prepare_report_dir(root)
        if report_dir.is_symlink(): raise OSError("External report directory must not be a symlink.")
        output = Path(args.output).expanduser().resolve() if args.output else report_dir / f"sbom-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.cdx.json"
        if output.is_relative_to(root): raise ValueError("SBOM output must be outside the target repository to preserve read-only behavior.")
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and not args.overwrite: raise FileExistsError(f"Output exists: {output}; pass --overwrite to replace it.")
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"SBOM components: {len(result['components'])}; manifests: {len(result['ogx']['manifests'])}")
        print(f"External SBOM: {output}")
        for warning in result["ogx"]["warnings"]: print("WARNING: " + warning)
    except (OSError, ValueError) as exc:
        print(f"SBOM generation failed: {exc}", file=sys.stderr); return 2
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    _json_print(discover_agents())
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    try:
        if args.skills_action == "validate":
            result = validate_skill(Path(args.path))
            _json_print(result)
            return 0 if result["valid"] else 1
        results = list_skills([Path(p) for p in args.paths] if args.paths else None)
        _json_print(results)
        return 1 if any(not item.get("valid") for item in results) else 0
    except (OSError, ValueError) as exc:
        print(f"Skill operation failed: {exc}", file=sys.stderr); return 2


def cmd_diagnose(args: argparse.Namespace) -> int:
    try:
        paths = [Path(item) for item in args.logs]
        result = analyze_logs(paths, max_bytes=args.max_bytes)
    except (OSError, ValueError) as exc:
        print(f"Log diagnostics failed: {exc}", file=sys.stderr); return 2
    _json_print(result)
    return 1 if any(f["severity"] == "high" for f in result["findings"]) else 0


def cmd_policy(args: argparse.Namespace) -> int:
    try:
        _json_print(load_project_policy(_root(args.path)))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Policy check failed: {exc}", file=sys.stderr); return 2


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


def cmd_impact(args: argparse.Namespace) -> int:
    try:
        result = analyze_impact(_resolve_project(args), args.file, load_config())
        _json_print(result)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Impact analysis failed: {exc}", file=sys.stderr); return 2


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


def cmd_repo(args: argparse.Namespace) -> int:
    try:
        if args.repo_action == "inspect":
            _json_print(inspect_repository(_root(args.path)))
        elif args.repo_action == "remote":
            _json_print(inspect_remote(args.url))
        elif args.repo_action == "clone":
            if args.confirm and _requires_email_approval():
                queued = _queue_action_approval("repo.clone",
                    {"url": args.url, "destination": args.destination},
                    "Clone the specified Git repository into the exact selected destination.",
                    getattr(args, "requester_subject", None))
                _print_approval_queued(queued)
                return 0
            result = clone_repository(args.url, Path(args.destination), confirm=args.confirm)
            _json_print(result)
        elif args.repo_action == "branch-create":
            if not args.confirm: raise PermissionError("Creating a branch modifies .git; pass --confirm.")
            if _requires_email_approval():
                queued = _queue_action_approval("repo.branch_create",
                    {"path": str(_root(args.path)), "name": args.name},
                    f"Create local feature branch '{args.name}'.",
                    getattr(args, "requester_subject", None))
                _print_approval_queued(queued)
                return 0
            root = _root(args.path)
            if args.name in {"main", "master", "develop"} or args.name.startswith("-"):
                raise ValueError("Choose a new feature branch name; protected/default branch names are refused.")
            checked = git_run(["check-ref-format", "--branch", args.name], cwd=root)
            if checked.returncode != 0:
                raise ValueError("Invalid Git branch name.")
            current = inspect_repository(root)
            result = git_run(["switch", "-c", args.name], cwd=root)
            if result.returncode != 0: raise RuntimeError((result.stderr or result.stdout).strip())
            _json_print({"created_branch": args.name, "previous_branch": current["branch"], "root": str(root)})
        elif args.repo_action == "push":
            if not args.confirm: raise PermissionError("Pushing changes to a remote requires --confirm.")
            if _requires_email_approval():
                queued = _queue_action_approval("repo.push",
                    {"path": str(_root(args.path)), "branch": args.branch, "remote": args.remote},
                    f"Push feature branch '{args.branch}' to remote '{args.remote}'.",
                    getattr(args, "requester_subject", None))
                _print_approval_queued(queued)
                return 0
            if args.branch in {"main", "master"}: raise ValueError("OGX refuses direct pushes to main/master.")
            if args.remote.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", args.remote):
                raise ValueError("Invalid Git remote name.")
            root = _root(args.path)
            checked = git_run(["check-ref-format", "--branch", args.branch], cwd=root)
            if checked.returncode != 0: raise ValueError("Invalid Git branch name.")
            result = git_run(["push", "--set-upstream", "--", args.remote, args.branch], cwd=root, timeout=180)
            if result.returncode != 0: raise RuntimeError((result.stderr or result.stdout).strip())
            print(result.stderr or result.stdout or "Push completed.")
        return 0
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Repository operation failed: {exc}", file=sys.stderr); return 2


def cmd_platform(args: argparse.Namespace) -> int:
    try:
        if args.platform_action == "repo":
            client = ProviderClient(args.provider, args.url)
            data = client.get_repository()
            _json_print({k: data.get(k) for k in ("name", "full_name", "path_with_namespace", "slug", "html_url", "web_url", "default_branch", "private", "visibility", "description") if k in data})
        elif args.platform_action == "pr":
            if not args.confirm: raise PermissionError("Creating a Pull/Merge Request requires explicit --confirm.")
            if _requires_email_approval():
                queued = _queue_action_approval("platform.pr",
                    {"provider": args.provider, "url": args.url, "title": args.title,
                     "body": args.body, "head": args.head, "base": args.base},
                    f"Create a {args.provider} Pull/Merge Request from '{args.head}' to '{args.base}' with the supplied title/body.",
                    getattr(args, "requester_subject", None))
                _print_approval_queued(queued)
                return 0
            client = ProviderClient(args.provider, args.url)
            result = client.create_pull_request(args.title, args.body, args.head, args.base)
            _json_print({k: result.get(k) for k in ("number", "iid", "id", "html_url", "web_url", "state", "title") if k in result})
        return 0
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Platform operation failed: {exc}", file=sys.stderr); return 2


def cmd_osv_audit(args: argparse.Namespace) -> int:
    try:
        if not args.online or not args.confirm_network:
            raise PermissionError("This command sends exact dependency names/versions to OSV.dev. Pass both --online and --confirm-network to opt in.")
        root = _resolve_project(args)
        bom = generate_sbom(root, load_config())
        result = audit_components(bom.get("components", []), timeout=args.timeout, max_packages=args.max_packages)
        result["project"] = {"name": root.name, "root": str(root)}
        result["manifest_files"] = bom.get("ogx", {}).get("manifests", [])
        result["disclaimer"] = "Advisory candidates only; verify affected version ranges and whether each dependency is actually installed or reachable."
        report_dir = _prepare_report_dir(root)
        output = report_dir / f"osv-audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"OSV advisory candidates: {result['advisories_found']}; exact packages submitted: {result['packages_submitted']}; skipped: {len(result['packages_skipped'])}")
        print(f"External report: {output}")
        print(result["privacy"])
        for warning in result["warnings"]: print(f"WARNING: {warning}")
        return 1 if result["advisories_found"] else 0
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"OSV audit failed: {exc}", file=sys.stderr)
        return 2


def cmd_licenses(args: argparse.Namespace) -> int:
    try:
        result = inventory_licenses(_resolve_project(args))
        _json_print(result)
        return 0
    except (OSError, ValueError) as exc:
        print(f"License inventory failed: {exc}", file=sys.stderr); return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    tools = discover_toolchains()
    available = sum(1 for item in tools if item["available"])
    print(f"OGX environment doctor: {available}/{len(tools)} optional tools detected")
    for item in tools:
        status = "FOUND" if item["available"] else "not found"
        print(f"{status:10} {item['name']:16} {item['purpose']}")
    print("Detection only: OGX does not install or execute these tools during doctor checks.")
    return 0


def cmd_compatibility(args: argparse.Namespace) -> int:
    try:
        _json_print(compatibility_report(_resolve_project(args)))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Compatibility inspection failed: {exc}", file=sys.stderr); return 2


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


def cmd_enterprise(args: argparse.Namespace) -> int:
    try:
        store = EnterpriseStore()
        initialized_path = store.initialize()
        if args.enterprise_action == "init":
            print(f"Initialized local enterprise data model: {initialized_path}")
        elif args.enterprise_action == "org-create":
            store.initialize(); _json_print(store.create_organization(args.slug, args.name, args.owner_subject))
        elif args.enterprise_action == "project-add":
            store.initialize(); _json_print(store.register_project(args.org, args.name, args.provider, args.repository, args.actor_subject))
        elif args.enterprise_action == "projects":
            _json_print(store.list_projects(args.org, args.actor_subject))
        elif args.enterprise_action == "audit":
            _json_print(store.list_audit_events(args.org, args.actor_subject, args.limit))
        elif args.enterprise_action == "framework":
            _json_print(framework_document())
        elif args.enterprise_action == "overview":
            _json_print(store.governance_overview(args.org, args.actor_subject))
        elif args.enterprise_action == "record-add":
            details = json.loads(args.details) if args.details else {}
            if not isinstance(details, dict):
                raise ValueError("--details must be a JSON object.")
            _json_print(store.create_governance_record(args.org, args.type, args.title,
                args.owner_subject, args.priority, args.status, args.actor_subject,
                args.due_date, details))
        elif args.enterprise_action == "records":
            _json_print(store.list_governance_records(args.org, args.actor_subject,
                args.type, args.limit))
        elif args.enterprise_action == "record-update":
            details = json.loads(args.details) if args.details else None
            if details is not None and not isinstance(details, dict):
                raise ValueError("--details must be a JSON object.")
            _json_print(store.update_governance_record(args.org, args.record_id, args.actor_subject,
                status=args.status, priority=args.priority, owner_subject=args.owner_subject,
                due_date=args.due_date, details=details))
        elif args.enterprise_action == "member-add":
            _json_print(store.add_member(args.org, args.external_subject, args.role, args.actor_subject))
        elif args.enterprise_action == "members":
            _json_print(store.list_members(args.org, args.actor_subject))
        elif args.enterprise_action == "member-role-set":
            _json_print(store.set_member_role(args.org, args.external_subject, args.role, args.actor_subject))
        elif args.enterprise_action == "member-remove":
            _json_print(store.remove_member(args.org, args.external_subject, args.actor_subject))
        return 0
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Enterprise data operation failed: {exc}", file=sys.stderr); return 2

def cmd_governance(args: argparse.Namespace) -> int:
    manager = ApprovalManager()
    try:
        action = args.governance_action
        if action == "list":
            _json_print(manager.list_requests(args.limit))
            return 0
        if action in {"approve", "reject"}:
            subject = (args.approver_subject or os.environ.get("OGX_APPROVER_SUBJECT", "")).strip()
            if not subject:
                raise ValueError("Set OGX_APPROVER_SUBJECT or pass --approver-subject.")
            result = manager.decide(request_id=args.request_id, code=args.code,
                                    approver_subject=subject,
                                    decision="approved" if action == "approve" else "rejected")
            _json_print(result)
            return 0
        if action == "execute":
            actor = (args.requester_subject or os.environ.get("OGX_ACTOR_SUBJECT", "")).strip()
            if not actor:
                raise ValueError("Set OGX_ACTOR_SUBJECT or pass --requester-subject.")
            action_data = manager.claim_execution(request_id=args.request_id, requester_subject=actor)
            operation, payload = action_data["operation"], action_data["payload"]
            root = _root(payload["path"]) if "path" in payload else None
            try:
                if operation == "guardian.repair":
                    result = apply_safe_fixes(root, confirm=True)
                elif operation == "guardian.upgrade_fetch":
                    result = fetch_remote(root, remote=payload.get("remote", "origin"), confirm=True)
                elif operation == "integration.apply":
                    result = apply_integration(root)
                elif operation == "integration.remove":
                    result = remove_integration(root)
                elif operation == "repo.clone":
                    result = clone_repository(payload["url"], Path(payload["destination"]), confirm=True)
                elif operation == "repo.branch_create":
                    assert root is not None
                    name = payload["name"]
                    checked = git_run(["check-ref-format", "--branch", name], cwd=root)
                    if checked.returncode != 0:
                        raise ValueError("Approved branch name is no longer valid.")
                    current = inspect_repository(root)
                    switched = git_run(["switch", "-c", name], cwd=root)
                    if switched.returncode != 0:
                        raise RuntimeError((switched.stderr or switched.stdout).strip())
                    result = {"created_branch": name, "previous_branch": current["branch"], "root": str(root)}
                elif operation == "repo.push":
                    assert root is not None
                    branch, remote = payload["branch"], payload.get("remote", "origin")
                    checked = git_run(["check-ref-format", "--branch", branch], cwd=root)
                    if checked.returncode != 0:
                        raise ValueError("Approved branch name is no longer valid.")
                    pushed = git_run(["push", "--set-upstream", "--", remote, branch], cwd=root, timeout=180)
                    if pushed.returncode != 0:
                        raise RuntimeError((pushed.stderr or pushed.stdout).strip())
                    result = {"status": "pushed", "branch": branch, "remote": remote,
                              "output": (pushed.stderr or pushed.stdout or "Push completed.")[:1000]}
                elif operation == "platform.pr":
                    client = ProviderClient(payload["provider"], payload["url"])
                    pr = client.create_pull_request(payload["title"], payload["body"], payload["head"], payload["base"])
                    result = {k: pr.get(k) for k in ("number", "iid", "id", "html_url", "web_url", "state", "title") if k in pr}
                else:
                    raise ValueError("Approved operation is not in the execution allowlist.")
                manager.finish_execution(args.request_id, success=True,
                                         result_summary=json.dumps(result, ensure_ascii=False, default=str))
                _json_print({"request_id": args.request_id, "status": "executed", "result": result})
                return 0
            except Exception as exc:
                manager.finish_execution(args.request_id, success=False,
                                         result_summary=f"{type(exc).__name__}: {str(exc)[:1500]}")
                raise
        raise ValueError(f"Unsupported governance action: {action}")
    except (OSError, RuntimeError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        print(f"Governance operation failed: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ogx", description=BANNER)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize OGX global state outside target repositories")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    env = sub.add_parser("env", help="Create and validate private OGX environment settings")
    env_sub = env.add_subparsers(dest="env_action", required=True)
    env_init = env_sub.add_parser("init", help="Create a private .env template outside scanned projects")
    env_init.add_argument("--path", help="Optional explicit .env destination; defaults to OGX external state")
    env_init.set_defaults(func=cmd_env)
    env_check = env_sub.add_parser("check", help="Validate required mail and security settings without printing secrets")
    env_check.set_defaults(func=cmd_env)
    env_path = env_sub.add_parser("path", help="Print the selected .env file path")
    env_path.set_defaults(func=cmd_env)

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

    integrate = sub.add_parser("integrate", help="Install safe project-root AI governance and OGX control folder")
    integrate_sub = integrate.add_subparsers(dest="integration_action", required=True)
    for action, help_text in (("plan", "Preview root integration without writing"),
                              ("apply", "Create missing OGX-managed files in the target root"),
                              ("status", "Check OGX-managed integration files"),
                              ("remove", "Remove only unchanged OGX-managed files")):
        item = integrate_sub.add_parser(action, help=help_text)
        item.add_argument("path", help="Target repository root")
        if action in {"apply", "remove"}:
            item.add_argument("--confirm", action="store_true", help="Queue an email approval before file creation/removal")
            item.add_argument("--requester-subject", help="External identity; defaults to OGX_ACTOR_SUBJECT")
        item.set_defaults(func=cmd_integrate)

    projects = sub.add_parser("projects", help="Manage project shortcuts in OGX global storage")
    project_sub = projects.add_subparsers(dest="projects_command", required=True)
    add = project_sub.add_parser("add", help="Register a repository path without modifying it")
    add.add_argument("path")
    add.add_argument("--name")
    add.set_defaults(func=cmd_projects_add)
    listing = project_sub.add_parser("list", help="List registered repositories")
    listing.set_defaults(func=cmd_projects_list)

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

    sbom = sub.add_parser("sbom", help="Generate a best-effort CycloneDX 1.5 inventory")
    sbom.add_argument("path", nargs="?")
    sbom.add_argument("--project")
    sbom.add_argument("--output")
    sbom.add_argument("--overwrite", action="store_true")
    sbom.set_defaults(func=cmd_sbom)

    agents = sub.add_parser("agents", help="Discover compatible AI agent runtimes")
    agents.add_argument("action", choices=("list",), default="list", nargs="?")
    agents.set_defaults(func=cmd_agents)

    skills = sub.add_parser("skills", help="Inspect declarative skill packs")
    skills_sub = skills.add_subparsers(dest="skills_action", required=True)
    skills_list = skills_sub.add_parser("list", help="List and validate skill manifests")
    skills_list.add_argument("paths", nargs="*", help="Optional skill directories or skill.json files; defaults to official packs")
    skills_list.set_defaults(func=cmd_skills)
    skills_validate = skills_sub.add_parser("validate", help="Validate one skill manifest")
    skills_validate.add_argument("path")
    skills_validate.set_defaults(func=cmd_skills)

    diagnose = sub.add_parser("diagnose", help="Analyze existing log files without executing the project")
    diagnose.add_argument("logs", nargs="+", help="Log file paths")
    diagnose.add_argument("--max-bytes", type=int, default=5_000_000)
    diagnose.set_defaults(func=cmd_diagnose)

    validate = sub.add_parser("validate", help="Run static project validation; never executes target code")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--project")
    validate.set_defaults(func=cmd_validate)

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

    impact = sub.add_parser("impact", help="Estimate static import dependents for a changed source file")
    impact.add_argument("path", nargs="?")
    impact.add_argument("--project")
    impact.add_argument("--file", required=True, help="Changed file inside the target project")
    impact.set_defaults(func=cmd_impact)

    fixplan = sub.add_parser("fix-plan", help="Generate evidence-backed remediation proposals; does not edit source")
    fixplan.add_argument("path", nargs="?")
    fixplan.add_argument("--project")
    fixplan.set_defaults(func=cmd_fix_plan)

    policy = sub.add_parser("policy", help="Inspect target project OGX policy")
    policy.add_argument("path")
    policy.set_defaults(func=cmd_policy)

    repo = sub.add_parser("repo", help="Read-only Git inspection and explicitly approved repository operations")
    repo_sub = repo.add_subparsers(dest="repo_action", required=True)
    repo_inspect = repo_sub.add_parser("inspect")
    repo_inspect.add_argument("path")
    repo_inspect.set_defaults(func=cmd_repo)
    repo_remote = repo_sub.add_parser("remote")
    repo_remote.add_argument("url")
    repo_remote.set_defaults(func=cmd_repo)
    repo_clone = repo_sub.add_parser("clone")
    repo_clone.add_argument("url")
    repo_clone.add_argument("destination")
    repo_clone.add_argument("--confirm", action="store_true")
    repo_clone.add_argument("--requester-subject")
    repo_clone.set_defaults(func=cmd_repo)
    repo_branch = repo_sub.add_parser("branch-create")
    repo_branch.add_argument("path")
    repo_branch.add_argument("name")
    repo_branch.add_argument("--confirm", action="store_true")
    repo_branch.add_argument("--requester-subject")
    repo_branch.set_defaults(func=cmd_repo)
    repo_push = repo_sub.add_parser("push")
    repo_push.add_argument("path")
    repo_push.add_argument("branch")
    repo_push.add_argument("--remote", default="origin")
    repo_push.add_argument("--confirm", action="store_true")
    repo_push.add_argument("--requester-subject")
    repo_push.set_defaults(func=cmd_repo)

    platform = sub.add_parser("platform", help="Use supported Git hosting APIs with environment-provided credentials")
    platform_sub = platform.add_subparsers(dest="platform_action", required=True)
    platform_repo = platform_sub.add_parser("repo")
    platform_repo.add_argument("provider", choices=("github", "gitlab", "bitbucket", "gitea", "forgejo", "gitea-or-forgejo"))
    platform_repo.add_argument("url")
    platform_repo.set_defaults(func=cmd_platform)
    platform_pr = platform_sub.add_parser("pr")
    platform_pr.add_argument("provider", choices=("github", "gitlab", "bitbucket", "gitea", "forgejo", "gitea-or-forgejo"))
    platform_pr.add_argument("url")
    platform_pr.add_argument("--title", required=True)
    platform_pr.add_argument("--body", default="Created by OMEGA GUARDIAN X after explicit user approval.")
    platform_pr.add_argument("--head", required=True)
    platform_pr.add_argument("--base", required=True)
    platform_pr.add_argument("--confirm", action="store_true")
    platform_pr.add_argument("--requester-subject")
    platform_pr.set_defaults(func=cmd_platform)


    osv = sub.add_parser("osv-audit", help="Optional live OSV advisory lookup for exact manifest-declared dependencies")
    osv.add_argument("path", nargs="?")
    osv.add_argument("--project")
    osv.add_argument("--online", action="store_true", help="Explicitly enable external OSV.dev requests")
    osv.add_argument("--confirm-network", action="store_true", help="Confirm package names and exact versions will be sent to OSV.dev")
    osv.add_argument("--timeout", type=int, default=30)
    osv.add_argument("--max-packages", type=int, default=1000)
    osv.set_defaults(func=cmd_osv_audit)

    licenses = sub.add_parser("licenses", help="Inventory license files and SPDX headers; not legal advice")
    licenses.add_argument("path", nargs="?")
    licenses.add_argument("--project")
    licenses.set_defaults(func=cmd_licenses)

    doctor = sub.add_parser("doctor", help="Detect optional local toolchains and security analyzers")
    doctor.set_defaults(func=cmd_doctor)


    compatibility = sub.add_parser("compatibility", help="Report detected stack and installed toolchain capability")
    compatibility.add_argument("path", nargs="?")
    compatibility.add_argument("--project")
    compatibility.set_defaults(func=cmd_compatibility)

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

    enterprise = sub.add_parser("enterprise", help="Local tenant/organization data-model preview; not a SaaS server")
    enterprise_sub = enterprise.add_subparsers(dest="enterprise_action", required=True)
    enterprise_init = enterprise_sub.add_parser("init", help="Initialize local enterprise SQLite schema")
    enterprise_init.set_defaults(func=cmd_enterprise)
    enterprise_org = enterprise_sub.add_parser("org-create", help="Create a local organization and initial owner identity")
    enterprise_org.add_argument("--slug", required=True)
    enterprise_org.add_argument("--name", required=True)
    enterprise_org.add_argument("--owner-subject", required=True, help="External IdP subject; no password is stored")
    enterprise_org.set_defaults(func=cmd_enterprise)
    enterprise_project = enterprise_sub.add_parser("project-add", help="Register project within an organization")
    enterprise_project.add_argument("--org", required=True)
    enterprise_project.add_argument("--name", required=True)
    enterprise_project.add_argument("--provider", default="local")
    enterprise_project.add_argument("--repository")
    enterprise_project.add_argument("--actor-subject", required=True)
    enterprise_project.set_defaults(func=cmd_enterprise)
    enterprise_projects = enterprise_sub.add_parser("projects", help="List only projects visible to an organization member")
    enterprise_projects.add_argument("--org", required=True)
    enterprise_projects.add_argument("--actor-subject", required=True)
    enterprise_projects.set_defaults(func=cmd_enterprise)
    enterprise_audit = enterprise_sub.add_parser("audit", help="List organization audit events")
    enterprise_audit.add_argument("--org", required=True)
    enterprise_audit.add_argument("--actor-subject", required=True)
    enterprise_audit.add_argument("--limit", type=int, default=100)
    enterprise_audit.set_defaults(func=cmd_enterprise)
    enterprise_framework = enterprise_sub.add_parser("framework", help="Show the six-pillar enterprise operating model")
    enterprise_framework.set_defaults(func=cmd_enterprise)
    enterprise_overview = enterprise_sub.add_parser("overview", help="Show role-protected enterprise governance metrics")
    enterprise_overview.add_argument("--org", required=True)
    enterprise_overview.add_argument("--actor-subject", required=True)
    enterprise_overview.set_defaults(func=cmd_enterprise)
    enterprise_add = enterprise_sub.add_parser("record-add", help="Create an objective, initiative, risk, decision, change, incident or service metric")
    enterprise_add.add_argument("--org", required=True)
    enterprise_add.add_argument("--type", choices=("objective", "initiative", "risk", "decision", "change", "incident", "service_metric"), required=True)
    enterprise_add.add_argument("--title", required=True)
    enterprise_add.add_argument("--owner-subject", required=True)
    enterprise_add.add_argument("--priority", choices=("critical", "high", "medium", "low"), default="medium")
    enterprise_add.add_argument("--status", choices=("proposed", "planned", "open", "in_progress", "blocked", "accepted", "mitigated", "resolved", "done", "rejected", "cancelled"), default="proposed")
    enterprise_add.add_argument("--due-date")
    enterprise_add.add_argument("--details", default="{}", help="JSON object with structured governance details")
    enterprise_add.add_argument("--actor-subject", required=True)
    enterprise_add.set_defaults(func=cmd_enterprise)
    enterprise_records = enterprise_sub.add_parser("records", help="List governance records visible to an organization member")
    enterprise_records.add_argument("--org", required=True)
    enterprise_records.add_argument("--actor-subject", required=True)
    enterprise_records.add_argument("--type", choices=("objective", "initiative", "risk", "decision", "change", "incident", "service_metric"))
    enterprise_records.add_argument("--limit", type=int, default=100)
    enterprise_records.set_defaults(func=cmd_enterprise)
    enterprise_update = enterprise_sub.add_parser("record-update", help="Update an existing governance record with state-transition checks")
    enterprise_update.add_argument("--org", required=True)
    enterprise_update.add_argument("--record-id", required=True)
    enterprise_update.add_argument("--actor-subject", required=True)
    enterprise_update.add_argument("--status", choices=("proposed", "planned", "open", "in_progress", "blocked", "accepted", "mitigated", "resolved", "done", "rejected", "cancelled"))
    enterprise_update.add_argument("--priority", choices=("critical", "high", "medium", "low"))
    enterprise_update.add_argument("--owner-subject")
    enterprise_update.add_argument("--due-date")
    enterprise_update.add_argument("--details", help="JSON object to merge into the current details")
    enterprise_update.set_defaults(func=cmd_enterprise)
    enterprise_member_add = enterprise_sub.add_parser("member-add", help="Add a member with role-based privilege assignment")
    enterprise_member_add.add_argument("--org", required=True)
    enterprise_member_add.add_argument("--external-subject", required=True)
    enterprise_member_add.add_argument("--role", choices=("owner", "admin", "developer", "auditor", "viewer"), required=True)
    enterprise_member_add.add_argument("--actor-subject", required=True)
    enterprise_member_add.set_defaults(func=cmd_enterprise)
    enterprise_members = enterprise_sub.add_parser("members", help="List organization members and roles")
    enterprise_members.add_argument("--org", required=True)
    enterprise_members.add_argument("--actor-subject", required=True)
    enterprise_members.set_defaults(func=cmd_enterprise)
    enterprise_member_role = enterprise_sub.add_parser("member-role-set", help="Change a member role with owner/admin privilege guardrails")
    enterprise_member_role.add_argument("--org", required=True)
    enterprise_member_role.add_argument("--external-subject", required=True)
    enterprise_member_role.add_argument("--role", choices=("owner", "admin", "developer", "auditor", "viewer"), required=True)
    enterprise_member_role.add_argument("--actor-subject", required=True)
    enterprise_member_role.set_defaults(func=cmd_enterprise)
    enterprise_member_remove = enterprise_sub.add_parser("member-remove", help="Remove a member while protecting the last owner")
    enterprise_member_remove.add_argument("--org", required=True)
    enterprise_member_remove.add_argument("--external-subject", required=True)
    enterprise_member_remove.add_argument("--actor-subject", required=True)
    enterprise_member_remove.set_defaults(func=cmd_enterprise)

    governance = sub.add_parser("governance", help="Email-gated approvals and controlled execution")
    governance_sub = governance.add_subparsers(dest="governance_action", required=True)
    governance_list = governance_sub.add_parser("list", help="List recent write approval requests")
    governance_list.add_argument("--limit", type=int, default=100)
    governance_list.set_defaults(func=cmd_governance)
    for action, help_text in (("approve", "Approve a pending request using its one-time email code"),
                              ("reject", "Reject a pending request using its one-time email code")):
        item = governance_sub.add_parser(action, help=help_text)
        item.add_argument("request_id")
        item.add_argument("--code", required=True)
        item.add_argument("--approver-subject", help="External identity; defaults to OGX_APPROVER_SUBJECT")
        item.set_defaults(func=cmd_governance)
    governance_execute = governance_sub.add_parser("execute", help="Execute the exact single-use approved action")
    governance_execute.add_argument("request_id")
    governance_execute.add_argument("--requester-subject", help="External identity; defaults to OGX_ACTOR_SUBJECT")
    governance_execute.set_defaults(func=cmd_governance)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # `env init` and `env path` must remain usable even when an existing env file is broken.
    if not (args.command == "env" and args.env_action in {"init", "path"}):
        try:
            load_runtime_env()
        except EnvFileError as exc:
            print(f"Environment configuration failed: {exc}", file=sys.stderr)
            return 2
    return args.func(args)

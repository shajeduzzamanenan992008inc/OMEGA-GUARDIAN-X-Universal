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
from .config import load_config, state_dir, state_key_slug
from .envfile import (
    EnvFileError, create_env_file, configured_settings, file_permission_warning,
    load_runtime_env, resolve_env_path, validate_runtime_config,
)
from .detector import detect_project
from .reports import write_json, write_markdown, write_sarif
from .scanner import scan_repository
from .project_integration import (
    apply_integration,
    integration_status,
    plan_integration,
    remove_integration,
)
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
from .artifacts import ArtifactError, verify_sums, write_manifests
from .enterprise.store import EnterpriseStore
from .enterprise.authz import PolicyEngine, Principal, Resource
from .enterprise.secrets import SecretsVault
from .enterprise.backup import (
    create_backup, list_backups, prune_backups, recover_restore, restore_backup,
    verify_backup,
)
from .enterprise.backup_signing import HmacSigningProvider
from .enterprise.service_identity import ServiceIdentityManager
from .enterprise.approvals import ApprovalManager
from .enterprise.governance import framework_document
from .osv import audit_components
from .triage import build_debug_report, render_test_plan
from .impact import analyze_impact
from .guardian import (
    apply_safe_fixes, build_evolution_report, build_guardian_audit, check_upstream,
    fetch_remote, plan_safe_fixes, render_guardian_markdown,
)
from .engines.registry import EngineRegistry
from .pipeline import (
    analyze_android, analyze_project, inspect_engines,
    render_android_markdown, render_universal_markdown,
)
from .pap import (
    ProjectIdentity,
    create_identity,
    read_identity,
    validate_identity_file,
    recover_identity,
    update_identity_field,
    IdentityError,
    IdentityCorruptedError,
    IdentityMissingError,
    IdentityValidationError,
    IdentityImmutableError,
    # Manifest
    resolve_project_configuration,
    resolve_manifest_conflict,
    load_and_validate_manifest,
    discover_manifest,
    ManifestConflictResolution,
    ManifestLocation,
    TrustClassification,
    ActivationReadiness,
)
from .pap.host_identity import (
    load_or_create_host_identity,
    verify_host_identity,
)
from .pap.lineage import explain_identity, fork_project
from .relocation import (
    RelocationError,
    RelocationRefused,
    apply_relocation,
    plan_relocation,
    recover_relocation,
    verify_relocation,
)
from .patching import (
    PatchApprovalRequired,
    PatchError,
    PatchIntegrityError,
    PatchRefused,
    apply_patch as apply_source_patch,
    list_plans as list_source_patches,
    load_plan as load_source_patch,
    plan_patch as plan_source_patch,
    rollback_patch as rollback_source_patch,
)
from .orchestrator import Orchestrator, WorkflowError
from .registry import RegistryV2, get_registry, RegistryError, RegistryNotFoundError, MigrationError
from .discovery import (
    DiscoveryEngine, DiscoveryConfig, DiscoveryScope, DiscoveryRoot,
    DiscoveryStatus, discover_projects, discover_root, run_discovery_cycle,
)
from .hosts import (
    HostRegistry, get_host_registry,
    LocalFilesystemProvider, LocalGitProvider, GitHubProvider,
    HostType, ProviderType, HostCapability, CapabilityStatus,
)
from .hosts.lifecycle import ACTIVE_STATES, HostLifecycleError, get_session_manager
from .intelligence import AnalysisOptions, ProjectIntelligenceEngine
from .intelligence.integration import resolve_registered_project
from .intelligence.reports import render_markdown, render_terminal

from uuid import UUID, uuid4

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
    # The slug is case-folded by the same rule as the digest so a case-only
    # path difference yields one directory name on every filesystem.
    safe_name = state_key_slug(root.name)
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


def _scan_cache():
    """Open the external scan cache, or fall back to a full scan on any error."""

    from .cache import CacheError, ScanCache, default_cache_path

    try:
        return ScanCache(default_cache_path(), ruleset=f"{__version__}:scanner-v1")
    except (CacheError, OSError):
        return None


def cmd_scan(args: argparse.Namespace) -> int:
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            raise ValueError(f"Project directory not found: {root}")
        config = load_config()
        cache = _scan_cache() if getattr(args, "cache", False) else None
        report = scan_repository(root, config, cache=cache)
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


def _engine_ids(values: list[str] | None) -> list[str] | None:
    """Accept repeated values and comma-separated engine IDs."""
    if not values:
        return None
    selected: list[str] = []
    for value in values:
        selected.extend(part.strip() for part in value.split(",") if part.strip())
    return selected or None


def _write_report_bundle(root: Path, prefix: str, report: dict, report_format: str,
                         renderer) -> list[Path]:
    """Write reports to OGX external state only, refusing symlinked destinations."""
    report_dir = _prepare_report_dir(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"{prefix}-{stamp}"
    outputs: list[Path] = []
    if report_format in ("json", "both"):
        destination = report_dir / f"{base}.json"
        if destination.is_symlink():
            raise OSError(f"External report must not be a symlink: {destination}")
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        outputs.append(destination)
    if report_format in ("markdown", "both"):
        destination = report_dir / f"{base}.md"
        if destination.is_symlink():
            raise OSError(f"External report must not be a symlink: {destination}")
        destination.write_text(renderer(report), encoding="utf-8")
        outputs.append(destination)
    return outputs


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


def cmd_intelligence(args: argparse.Namespace) -> int:
    """Run the read-only universal project intelligence analysis."""
    try:
        root = None
        explicit_project_id = None
        if getattr(args, "path", None):
            root = _root(args.path)
        elif getattr(args, "project", None):
            projects = _load_projects()
            if args.project not in projects:
                raise ValueError(f"Unknown registered project: {args.project}. Run: ogx projects list")
            root = _root(projects[args.project])
        elif getattr(args, "target", None):
            candidate = Path(args.target).expanduser()
            if candidate.is_dir():
                root = candidate.resolve()
            else:
                root, explicit_project_id, _ = resolve_registered_project(args.target)
        else:
            raise ValueError(
                "Provide a project path, a registered project name or a registry project ID.")
        if not root.is_dir():
            raise ValueError(f"Project directory not found: {root}")
        options = AnalysisOptions(
            excludes=tuple(getattr(args, "exclude", None) or ()),
            max_files=args.max_files,
            max_depth=args.max_depth,
            registry_lookup=not args.no_registry,
        )
        report = ProjectIntelligenceEngine().analyze(
            root, options, project_id=explicit_project_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Intelligence analysis failed: {exc}", file=sys.stderr)
        return 2

    sections = None
    requested = [name for name, enabled in (
        ("languages", args.languages),
        ("dependencies", args.dependencies),
        ("architecture", args.architecture),
        ("health", args.health),
    ) if enabled]
    if requested:
        sections = requested

    report_format = "json" if args.json else args.format
    if report_format == "json":
        _json_print(report.to_dict())
        return 0
    if report_format == "markdown":
        print(render_markdown(report, sections))
        return 0
    if report_format == "both":
        try:
            report_dir = _prepare_report_dir(root)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            base = f"intelligence-{stamp}-{report.metadata.analysis_id}"
            json_path = report_dir / f"{base}.json"
            markdown_path = report_dir / f"{base}.md"
            for destination in (json_path, markdown_path):
                if destination.is_symlink():
                    raise OSError(f"External report must not be a symlink: {destination}")
            json_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            markdown_path.write_text(render_markdown(report), encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"Could not write external intelligence report: {exc}", file=sys.stderr)
            return 2
        print(render_terminal(report, sections))
        print(f"External JSON report: {json_path}")
        print(f"External Markdown report: {markdown_path}")
        return 0
    print(render_terminal(report, sections))
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


def cmd_registry_list(args: argparse.Namespace) -> int:
    """List all projects in Registry V2."""
    try:
        registry = get_registry()
        projects = registry.list_projects(limit=args.limit, offset=args.offset)
        
        if args.json:
            result = {
                "projects": [
                    {
                        "project_id": str(p.project_id),
                        "canonical_name": p.canonical_name,
                        "description": p.description,
                        "identity_hash": p.identity_hash,
                        "created_at": p.created_at.isoformat(),
                        "updated_at": p.updated_at.isoformat(),
                        "lifecycle_state": p.lifecycle_state.value,
                    }
                    for p in projects
                ],
                "count": len(projects),
                "limit": args.limit,
                "offset": args.offset,
            }
            _json_print(result)
        else:
            if not projects:
                print("No projects in registry.")
            for p in projects:
                print(f"{p.project_id}  {p.canonical_name}  [{p.lifecycle_state.value}]")
        return 0
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Registry list failed: {exc}", file=sys.stderr)
        return 2


def cmd_registry_show(args: argparse.Namespace) -> int:
    """Show detailed project information from Registry V2."""
    try:
        registry = get_registry()
        project_id = args.project_id
        
        # Try to parse as UUID, fallback to name lookup
        try:
            from uuid import UUID
            pid = UUID(project_id)
            project = registry.get_project(pid)
        except ValueError:
            # Try name lookup
            project = registry.find_project_by_identity(canonical_name=project_id)
            if not project:
                print(f"Project not found: {project_id}", file=sys.stderr)
                return 2
        
        # Get associated repositories and workspaces
        repos = registry.list_repositories(project.project_id)
        workspaces = registry.list_workspaces(project.project_id)
        
        if args.json:
            result = {
                "project": {
                    "project_id": str(project.project_id),
                    "canonical_name": project.canonical_name,
                    "description": project.description,
                    "identity_hash": project.identity_hash,
                    "created_at": project.created_at.isoformat(),
                    "updated_at": project.updated_at.isoformat(),
                    "lifecycle_state": project.lifecycle_state.value,
                    "metadata": project.metadata,
                },
                "repositories": [
                    {
                        "repository_id": str(r.repository_id),
                        "provider": r.provider,
                        "repository_url": r.repository_url,
                        "normalized_repository_url": r.normalized_repository_url,
                        "repository_name": r.repository_name,
                        "default_branch": r.default_branch,
                        "visibility": r.visibility,
                    }
                    for r in repos
                ],
                "workspaces": [
                    {
                        "workspace_id": str(w.workspace_id),
                        "repository_id": str(w.repository_id) if w.repository_id else None,
                        "workspace_provider": w.workspace_provider,
                        "workspace_type": w.workspace_type,
                        "location_reference": w.location_reference,
                        "normalized_location": w.normalized_location,
                        "display_name": w.display_name,
                        "status": w.status.value,
                        "last_seen_at": w.last_seen_at.isoformat(),
                    }
                    for w in workspaces
                ],
            }
            _json_print(result)
        else:
            print(f"Project: {project.canonical_name} ({project.project_id})")
            print(f"  Lifecycle: {project.lifecycle_state.value}")
            print(f"  Created: {project.created_at.isoformat()}")
            print(f"  Updated: {project.updated_at.isoformat()}")
            print(f"  Description: {project.description or '(none)'}")
            print(f"  Identity Hash: {project.identity_hash or '(none)'}")
            print(f"  Repositories: {len(repos)}")
            for r in repos:
                print(f"    {r.repository_id}  {r.provider}  {r.repository_name or r.repository_url}")
            print(f"  Workspaces: {len(workspaces)}")
            for w in workspaces:
                print(f"    {w.workspace_id}  [{w.status.value}]  {w.location_reference}")
        return 0
    except RegistryNotFoundError as exc:
        print(f"Project not found: {exc}", file=sys.stderr)
        return 2
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Registry show failed: {exc}", file=sys.stderr)
        return 2


def cmd_registry_status(args: argparse.Namespace) -> int:
    """Show registry status and statistics."""
    try:
        registry = get_registry()
        
        projects = registry.list_projects()
        total_repos = 0
        total_workspaces = 0
        
        for p in projects:
            total_repos += len(registry.list_repositories(p.project_id))
            total_workspaces += len(registry.list_workspaces(p.project_id))
        
        result = {
            "projects": len(projects),
            "repositories": total_repos,
            "workspaces": total_workspaces,
            "schema_version": registry.get_schema_version(),
            "database_path": str(registry.db_path),
        }
        
        if args.json:
            _json_print(result)
        else:
            print(f"Registry V2 Status:")
            print(f"  Database: {registry.db_path}")
            print(f"  Schema Version: {registry.get_schema_version()}")
            print(f"  Projects: {len(projects)}")
            print(f"  Repositories: {total_repos}")
            print(f"  Workspaces: {total_workspaces}")
        return 0
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Registry status failed: {exc}", file=sys.stderr)
        return 2


def cmd_registry_integrity(args: argparse.Namespace) -> int:
    """Check registry database integrity."""
    try:
        registry = get_registry()
        result = registry.check_integrity()
        
        if args.json:
            _json_print(result)
        else:
            print(f"Registry Integrity Check:")
            print(f"  Database: {registry.db_path}")
            print(f"  Schema Version: {result['schema_version']}")
            print(f"  Integrity OK: {result['integrity_ok']}")
            print(f"  Details: {result['integrity_details']}")
            print(f"  Foreign Key Violations: {result['foreign_key_violations']}")
            print(f"  Counts: {result['counts']}")
        return 0 if result['integrity_ok'] else 1
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Registry integrity check failed: {exc}", file=sys.stderr)
        return 2


def cmd_registry_migrate(args: argparse.Namespace) -> int:
    """Migrate from Registry V1 to V2."""
    try:
        registry = get_registry()
        
        # Find V1 registry
        from .config import state_dir
        v1_path = state_dir() / "projects.json"
        
        if not v1_path.exists():
            print(f"Registry V1 not found at {v1_path}", file=sys.stderr)
            return 2
        
        if not args.confirm and not args.dry_run:
            print("Migration requires --confirm or --dry-run", file=sys.stderr)
            return 2
        
        result = registry.migrate_from_v1(v1_path, dry_run=args.dry_run)
        
        if args.json:
            _json_print(result)
        else:
            if args.dry_run:
                print(f"Migration Preview (dry run):")
                print(f"  Projects to migrate: {result['preview']['projects_to_migrate']}")
                for p in result['preview']['projects']:
                    print(f"    {p['v1_name']} -> {p['project_id']}  (identity: {'yes' if p['identity_found'] else 'no'})")
            else:
                print(f"Migration completed:")
                print(f"  Migrated: {result['migrated']}/{result['total']}")
                if result['errors']:
                    print(f"  Errors: {len(result['errors'])}")
                    for e in result['errors']:
                        print(f"    {e['project']}: {e['error']}")
                if result.get('backup_path'):
                    print(f"  Backup saved: {result['backup_path']}")
        return 0
    except (RegistryError, MigrationError, OSError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 2


def cmd_registry_backup(args: argparse.Namespace) -> int:
    """Create a backup of the registry database."""
    try:
        registry = get_registry()
        
        output_path = None
        if args.output:
            output_path = Path(args.output)
        
        backup_path = registry.backup(output_path)
        
        if args.json:
            _json_print({"backup_path": str(backup_path)})
        else:
            print(f"Backup created: {backup_path}")
        return 0
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 2


def cmd_registry_recover(args: argparse.Namespace) -> int:
    """Recover registry from backup."""
    try:
        if not args.confirm:
            print("Recovery requires --confirm (will overwrite current registry)", file=sys.stderr)
            return 2
        
        backup_path = Path(args.backup_path)
        if not backup_path.exists():
            print(f"Backup file not found: {backup_path}", file=sys.stderr)
            return 2
        
        registry = get_registry()
        
        # Close current connections
        registry.close()
        
        # Replace database file
        import shutil
        shutil.copy2(backup_path, registry.db_path)
        
        # Reinitialize
        registry.initialize()
        
        print(f"Registry recovered from: {backup_path}")
        return 0
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Recovery failed: {exc}", file=sys.stderr)
        return 2


def cmd_pap_inspect(args: argparse.Namespace) -> int:
    """Inspect project PAP configuration."""
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            print(f"Error: directory not found: {root}", file=sys.stderr)
            return 2
        
        config = resolve_project_configuration(root)
        
        if args.json:
            result = {
                "manifest_data": config.manifest_data,
                "project_profile": {
                    "name": config.project_profile.name,
                    "root": config.project_profile.root,
                    "languages": config.project_profile.languages,
                    "ecosystems": config.project_profile.ecosystems,
                    "frameworks": config.project_profile.frameworks,
                } if config.project_profile else None,
                "identity": config.identity.to_dict() if config.identity else None,
                "effective_config": config.effective_config,
                "source_priority": config.source_priority,
                "trust_classification": config.trust_classification.value,
                "activation_readiness": config.activation_readiness.value,
                "validation_warnings": config.validation_warnings,
            }
            _json_print(result)
        else:
            print(f"Project: {config.project_profile.name if config.project_profile else 'Unknown'}")
            print(f"  Trust: {config.trust_classification.value}")
            print(f"  Readiness: {config.activation_readiness.value}")
            print(f"  Manifest: {'Yes' if config.manifest_data else 'No (auto-detected defaults)'}")
            if config.identity:
                print(f"  Identity: {config.identity.project_id}")
            for warning in config.validation_warnings:
                print(f"  WARNING: {warning}")
            print("\nEffective Configuration:")
            print(f"  Mode: {config.effective_config['guardian_x']['mode']}")
            print(f"  Execution Policy: {config.effective_config['policies']['execution']}")
            print(f"  Supported Hosts: {', '.join(config.effective_config['hosts']['supported'])}")
        
        return 0
    except (OSError, ValueError, IdentityValidationError) as exc:
        print(f"PAP inspect failed: {exc}", file=sys.stderr)
        return 2


def cmd_pap_validate(args: argparse.Namespace) -> int:
    """Validate project PAP manifest."""
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            print(f"Error: directory not found: {root}", file=sys.stderr)
            return 2
        
        discovery = discover_manifest(root)
        
        if not discovery.has_manifest:
            print("No manifest found.")
            if args.json:
                _json_print({"valid": False, "reason": "no_manifest"})
            else:
                print("No manifest file found (GUARDIAN-X.json or .guardian-x/manifest.json)")
            return 1
        
        if discovery.conflict:
            print("Manifest conflict detected!")
            if args.json:
                _json_print({"valid": False, "reason": "conflict", "locations": [l.value for l in discovery.conflicting_locations]})
            else:
                for loc in discovery.conflicting_locations:
                    print(f"  {loc.value}")
            return 1
        
        manifest_path = discovery.manifest_path
        result = load_and_validate_manifest(manifest_path)
        
        if args.json:
            output = {
                "valid": result.valid,
                "schema_version": result.schema_version,
                "errors": result.errors,
                "warnings": result.warnings,
                "defaults_applied": result.defaults_applied,
            }
            if result.data:
                output["data"] = result.data
            _json_print(output)
        else:
            if result.valid:
                print(f"Manifest VALID (schema v{result.schema_version})")
                if result.defaults_applied:
                    print("  Defaults applied: Yes")
            else:
                print(f"Manifest INVALID")
                for err in result.errors:
                    print(f"  ERROR: {err}")
            for warning in result.warnings:
                print(f"  WARNING: {warning}")
        
        return 0 if result.valid else 1
    except (OSError, ValueError, IdentityValidationError) as exc:
        print(f"PAP validate failed: {exc}", file=sys.stderr)
        return 2


def cmd_pap_status(args: argparse.Namespace) -> int:
    """Show project PAP activation status."""
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            print(f"Error: directory not found: {root}", file=sys.stderr)
            return 2
        
        config = resolve_project_configuration(root)
        
        if args.json:
            result = {
                "project_name": config.project_profile.name if config.project_profile else "Unknown",
                "activation_readiness": config.activation_readiness.value,
                "trust_classification": config.trust_classification.value,
                "has_manifest": config.manifest_data is not None and not config.validation_warnings,
                "validation_warnings": config.validation_warnings,
                "trust_classification": config.trust_classification.value,
                "activation_readiness": config.activation_readiness.value,
                "effective_mode": config.effective_config["guardian_x"]["mode"],
                "effective_execution_policy": config.effective_config["policies"]["execution"],
            }
            _json_print(result)
        else:
            print(f"Project: {config.project_profile.name if config.project_profile else 'Unknown'}")
            print(f"  Activation Readiness: {config.activation_readiness.value}")
            print(f"  Trust Classification: {config.trust_classification.value}")
            print(f"  Effective Mode: {config.effective_config['guardian_x']['mode']}")
            print(f"  Execution Policy: {config.effective_config['policies']['execution']}")
            for warning in config.validation_warnings:
                print(f"  WARNING: {warning}")
        
        return 0
    except (OSError, ValueError, IdentityValidationError) as exc:
        print(f"PAP status failed: {exc}", file=sys.stderr)
        return 2


def cmd_pap_schema(args: argparse.Namespace) -> int:
    """Print the GX-PAP manifest JSON schema."""
    try:
        from .pap.manifest_schema import MANIFEST_SCHEMA_V1
        _json_print(MANIFEST_SCHEMA_V1)
        return 0
    except (OSError, ValueError) as exc:
        print(f"PAP schema failed: {exc}", file=sys.stderr)
        return 2


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover projects in configured roots or specified paths."""
    try:
        config = DiscoveryConfig()
        
        if args.root:
            # Use specified root(s)
            roots = [Path(r) for r in args.root]
            projects = discover_root(roots[0]) if len(roots) == 1 else discover_projects(roots=roots)
        else:
            # Use configured roots
            if args.full:
                projects = discover_projects()
            else:
                # Show configured roots
                config_obj = DiscoveryConfig()
                if args.json:
                    _json_print({
                        "roots": [
                            {
                                "name": r.name,
                                "path": str(r.path),
                                "enabled": r.enabled,
                                "max_depth": r.max_depth,
                            }
                            for r in config_obj.get_enabled_roots()
                        ]
                    })
                else:
                    print("Discovery Roots:")
                    for r in config_obj.get_enabled_roots():
                        status = "enabled" if r.enabled else "disabled"
                        print(f"  {r.name}: {r.path} ({status}, max_depth={r.max_depth})")
                return 0
        
        if args.json:
            _json_print({
                "projects": [
                    {
                        "project_id": str(p.project_id),
                        "candidate_id": str(p.candidate.candidate_id),
                        "root_path": str(p.candidate.root_path),
                        "project_name": p.candidate.project_name,
                        "languages": p.candidate.languages,
                        "frameworks": p.candidate.frameworks,
                        "confidence": p.candidate.confidence,
                        "is_git_repo": p.candidate.is_git_repo,
                        "has_identity": p.candidate.has_identity,
                        "has_manifest": p.candidate.has_manifest,
                        "activation_status": p.activation_status.value,
                        "trust_classification": p.project_config.trust_classification.value,
                        "activation_readiness": p.project_config.activation_readiness.value,
                    }
                    for p in projects
                ]
            })
        else:
            print(f"Discovered {len(projects)} project(s):")
            for p in projects:
                status = p.activation_status.value
                print(f"  {p.candidate.project_name} ({p.candidate.root_path})")
                print(f"    ID: {p.project_id}")
                print(f"    Languages: {', '.join(p.candidate.languages) or 'none'}")
                print(f"    Frameworks: {', '.join(p.candidate.frameworks) or 'none'}")
                print(f"    Confidence: {p.candidate.confidence:.2f}")
                print(f"    Git: {'yes' if p.candidate.is_git_repo else 'no'}")
                print(f"    Identity: {'yes' if p.candidate.has_identity else 'no'}")
                print(f"    Manifest: {'yes' if p.candidate.has_manifest else 'no'}")
                print(f"    Status: {status}")
                print(f"    Trust: {p.project_config.trust_classification.value}")
                print(f"    Readiness: {p.project_config.activation_readiness.value}")
        return 0
    except (OSError, ValueError, Exception) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 2


def cmd_discover_root(args: argparse.Namespace) -> int:
    """Add or manage discovery roots."""
    try:
        config_obj = DiscoveryConfig()
        
        if args.discover_root_action == "add":
            root = config_obj.add_root(
                Path(args.path),
                name=args.name,
                max_depth=args.max_depth,
                exclusions=args.exclusions if args.exclusions else [],
            )
            print(f"Added discovery root: {root.name} -> {root.path}")
            return 0
        
        elif args.discover_root_action == "remove":
            if config_obj.remove_root(Path(args.path)):
                print(f"Removed discovery root: {args.path}")
            else:
                print(f"Root not found: {args.path}", file=sys.stderr)
                return 1
            return 0
        
        elif args.discover_root_action == "list":
            if args.json:
                _json_print({
                    "roots": [
                        {
                            "name": r.name,
                            "path": str(r.path),
                            "enabled": r.enabled,
                            "max_depth": r.max_depth,
                            "exclusions": r.exclusions,
                        }
                        for r in config_obj.roots
                    ]
                })
            else:
                print("Discovery Roots:")
                for r in config_obj.roots:
                    status = "enabled" if r.enabled else "disabled"
                    print(f"  {r.name}: {r.path} ({status}, max_depth={r.max_depth})")
            return 0
        
        return 0
    except (OSError, ValueError) as exc:
        print(f"Root management failed: {exc}", file=sys.stderr)
        return 2


def cmd_activate(args: argparse.Namespace) -> int:
    """Activate a project (run discovery and activation lifecycle)."""
    try:
        # For now, this is equivalent to discover + process
        # In future, this could run activation lifecycle steps
        if args.path:
            projects = discover_root(Path(args.path))
        else:
            projects = discover_projects()
        
        if args.json:
            _json_print({
                "activated": len(projects),
                "projects": [
                    {
                        "project_id": str(p.project_id),
                        "project_name": p.candidate.project_name,
                        "activation_status": p.activation_status.value,
                        "trust": p.project_config.trust_classification.value,
                        "readiness": p.project_config.activation_readiness.value,
                    }
                    for p in projects
                ]
            })
        else:
            print(f"Activated {len(projects)} project(s):")
            for p in projects:
                print(f"  {p.candidate.project_name} ({p.project_id}) - {p.activation_status.value}")
        return 0
    except (OSError, ValueError, Exception) as exc:
        print(f"Activation failed: {exc}", file=sys.stderr)
        return 2


# ==================== Host Commands ====================

def cmd_host_list(args: argparse.Namespace) -> int:
    """List all registered hosts."""
    try:
        host_registry = get_host_registry()
        host_type = args.type
        provider = args.provider
        status = None  # Not filtering by status for now
        
        hosts = host_registry.list_hosts(
            host_type=host_type,
            provider=provider,
            status=status,
            limit=100,
            offset=0,
        )
        
        if args.json:
            _json_print({"hosts": hosts})
        else:
            if not hosts:
                print("No hosts registered.")
            for h in hosts:
                caps = ", ".join(h.get("capabilities", [])[:5]) + ("..." if len(h.get("capabilities", [])) > 5 else "")
                print(f"  {h['host_id']}  {h['display_name']}  [{h['host_type']}]  [{h['provider']}]  {h['status']}")
                print(f"    Capabilities: {caps}")
        return 0
    except (OSError, ValueError, Exception) as exc:
        print(f"Host list failed: {exc}", file=sys.stderr)
        return 2


def cmd_host_show(args: argparse.Namespace) -> int:
    """Show detailed host information."""
    try:
        host_registry = get_host_registry()
        host = host_registry.get_host(args.host_id)
        
        if args.json:
            _json_print(host)
        else:
            print(f"Host: {host['display_name']} ({host['host_id']})")
            print(f"  Type: {host['host_type']}")
            print(f"  Provider: {host['provider']}")
            print(f"  Identifier: {host['normalized_identifier']}")
            print(f"  Status: {host['status']}")
            print(f"  Capabilities: {', '.join(host.get('capabilities', [])) or 'none'}")
            print(f"  Created: {host['created_at']}")
            print(f"  Updated: {host['updated_at']}")
        return 0
    except KeyError as exc:
        print(f"Host not found: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, Exception) as exc:
        print(f"Host show failed: {exc}", file=sys.stderr)
        return 2


def cmd_host_status(args: argparse.Namespace) -> int:
    """Show host status and health."""
    try:
        host_registry = get_host_registry()
        host = host_registry.get_host(args.host_id)
        
        capabilities = host_registry.get_host_capabilities(args.host_id)
        supported = host_registry.get_host_supported_capabilities(args.host_id)
        requires_auth = host_registry.get_host_requires_auth_capabilities(args.host_id)

        manager = get_session_manager()
        sessions = [s for s in manager.list_sessions(args.host_id) if s.state in ACTIVE_STATES]
        session = sessions[-1] if sessions else None
        live = session.to_dict() if session is not None else None
        effective = manager.effective_capabilities(session.session_id) if session is not None else None

        if args.json:
            _json_print({
                "host_id": host["host_id"],
                "display_name": host["display_name"],
                "host_type": host["host_type"],
                "provider": host["provider"],
                "persisted_status": host["status"],
                "status": session.state.value if session is not None else host["status"],
                "live_session": live,
                "effective_capabilities": effective,
                "capabilities": capabilities,
                "supported": supported,
                "requires_auth": requires_auth,
            })
        else:
            print(f"Host: {host['display_name']} ({host['host_id']})")
            print(f"  Type: {host['host_type']} (provider: {host['provider']})")
            print(f"  Persisted status: {host['status']}")
            print(f"  Live state: {session.state.value if session is not None else 'no live session'}")
            print(f"  Supported Capabilities: {', '.join(supported) if supported else 'none'}")
            print(f"  Requires Auth: {', '.join(requires_auth) if requires_auth else 'none'}")
        return 0
    except KeyError as exc:
        print(f"Host not found: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, Exception) as exc:
        print(f"Host status failed: {exc}", file=sys.stderr)
        return 2


def cmd_host_capabilities(args: argparse.Namespace) -> int:
    """Show host capabilities."""
    try:
        host_registry = get_host_registry()
        
        capabilities = host_registry.get_host_capabilities(args.host_id)
        supported = host_registry.get_host_supported_capabilities(args.host_id)
        requires_auth = host_registry.get_host_requires_auth_capabilities(args.host_id)
        
        if args.json:
            _json_print({
                "host_id": args.host_id,
                "capabilities": capabilities,
                "supported": supported,
                "requires_auth": requires_auth,
            })
        else:
            print(f"Capabilities for host {args.host_id}:")
            for cap, status in sorted(capabilities.items()):
                marker = "✓" if status == "supported" else ("!" if status == "requires_auth" else "✗")
                print(f"  {marker} {cap}: {status}")
        return 0
    except KeyError as exc:
        print(f"Host not found: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, Exception) as exc:
        print(f"Capabilities failed: {exc}", file=sys.stderr)
        return 2


def cmd_host_register(args: argparse.Namespace) -> int:
    """Register a new host."""
    try:
        host_registry = get_host_registry()
        
        host_type = HostType(args.type)
        provider = ProviderType(args.provider)

        if host_type in (HostType.LOCAL_FILESYSTEM, HostType.LOCAL_GIT) and not args.identifier:
            print(
                f"A {host_type.value} host requires --identifier pointing at its root directory.",
                file=sys.stderr,
            )
            return 2
        
        identifier = args.identifier or ""
        # Idempotent registration: the same (type, identifier) on this host
        # reuses its existing host id instead of creating a duplicate row.
        existing = host_registry.find_host_by_type_and_identifier(host_type.value, identifier)
        host_id = UUID(existing["host_id"]) if existing else uuid4()

        machine_identity = load_or_create_host_identity()
        host = host_registry.register_host(
            host_id=host_id,
            host_type=host_type.value,
            provider=provider.value,
            display_name=args.name,
            normalized_identifier=identifier,
            metadata={
                "machine_host_id": machine_identity.host_id,
                "machine_binding_kind": machine_identity.binding_kind,
            },
        )
        
        if args.json:
            _json_print({
                "host_id": str(host.host_id),
                "display_name": host.display_name,
                "host_type": host.host_type.value,
                "provider": host.provider.value,
                "normalized_identifier": host.normalized_identifier,
                "machine_host_id": machine_identity.host_id,
                "registration": "updated" if existing else "new",
            })
        else:
            verb = "Updated host" if existing else "Registered host"
            print(f"{verb}: {host.display_name} ({host.host_id})")
            print(f"  Type: {host.host_type.value}")
            print(f"  Provider: {host.provider.value}")
            print(f"  Machine host id: {machine_identity.host_id}")
        return 0
    except (OSError, ValueError, Exception) as exc:
        print(f"Host registration failed: {exc}", file=sys.stderr)
        return 2


def cmd_host_identity(args: argparse.Namespace) -> int:
    """Show and verify the machine-level host identity (host.json)."""
    try:
        identity = load_or_create_host_identity()
        report = verify_host_identity(identity)
        payload = {**identity.to_dict(), "verification": report}
        if args.json:
            _json_print(payload)
        else:
            print(f"Host identity: {identity.host_id}")
            print(f"  display_name: {identity.display_name}")
            print(f"  binding_kind: {identity.binding_kind}")
            print(f"  binding_hash: {identity.binding_hash}")
            print(f"  created_at: {identity.created_at}")
            print(f"  verification: {report['state']} (access: {report['access']})")
            print(f"  detail: {report['detail']}")
        return 0 if report["state"] in ("verified", "weak") else 1
    except (OSError, ValueError, IdentityError) as exc:
        print(f"Host identity failed: {exc}", file=sys.stderr)
        return 2


def cmd_relocate(args: argparse.Namespace) -> int:
    """Plan, apply, verify or recover a project relocation."""
    try:
        action = args.relocate_action
        if action == "plan":
            plan = plan_relocation(args.source, args.destination, operation=args.operation)
            if args.json:
                _json_print(plan.to_dict())
            else:
                print("Relocation plan:")
                print(f"  plan_id: {plan.plan_id}")
                print(f"  operation: {plan.operation}")
                print(f"  source: {plan.source}")
                print(f"  destination: {plan.destination}")
                print(f"  project_id: {plan.project_id}")
                print(f"  files: {plan.file_count} ({plan.total_bytes} bytes)")
                print("  source is retained unless apply is run with --delete-source (move only)")
                for warning in plan.warnings:
                    print(f"  WARNING: {warning}")
            return 0

        if action == "apply":
            plan = plan_relocation(args.source, args.destination, operation=args.operation)
            result = apply_relocation(
                plan,
                registry=None if args.no_registry else get_registry(),
                confirm_delete_source=bool(args.delete_source),
            )
            _json_print(result) if args.json else _print_relocation(result)
            return 0

        if action == "verify":
            report = verify_relocation(args.source, args.destination)
            _json_print(report) if args.json else _print_relocation_verify(report)
            return 0 if report["identity_preserved"] and report["content_matches"] else 1

        if action == "recover":
            result = recover_relocation(
                args.journal,
                registry=None if args.no_registry else get_registry(),
                confirm_delete_source=True if args.delete_source else None,
            )
            _json_print(result) if args.json else _print_relocation(result)
            return 0

        print(f"Unknown relocate action: {action}", file=sys.stderr)
        return 2
    except RelocationRefused as exc:
        print(f"Relocation refused ({exc.reason}): {exc}", file=sys.stderr)
        return 2
    except RelocationError as exc:
        print(f"Relocation failed ({exc.reason}): {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, IdentityError) as exc:
        print(f"Relocation failed: {exc}", file=sys.stderr)
        return 2


def _print_relocation(result: dict) -> None:
    plan = result.get("plan", {})
    print("Relocation complete:")
    print(f"  state: {result.get('state')}")
    print(f"  destination: {plan.get('destination')}")
    print(f"  project_id: {plan.get('project_id')}")
    print(f"  source_deleted: {result.get('source_deleted')} ({result.get('delete_reason')})")
    rebind = result.get("rebind") or {}
    print(f"  workspace_rebind: {rebind.get('status')}")
    if result.get("journal"):
        print(f"  journal: {result['journal']}")


def _print_relocation_verify(report: dict) -> None:
    print("Relocation verification:")
    print(f"  identity_preserved: {report['identity_preserved']}")
    print(f"  content_matches: {report['content_matches']}")
    print(f"  source_files: {report['source_files']}")
    print(f"  destination_files: {report['destination_files']}")
    for difference in report["differences"][:20]:
        print(f"  DIFF: {difference}")


def _host_error(exc: BaseException, action: str) -> int:
    if isinstance(exc, KeyError):
        print(f"Host not found: {exc}", file=sys.stderr)
        return 2
    if isinstance(exc, ValueError):
        print(f"Invalid host or session value: {exc}", file=sys.stderr)
        return 2
    print(f"Host {action} failed: {exc}", file=sys.stderr)
    return 1


def _new_host_session(host_id: str, root: str | None):
    manager = get_session_manager()
    session = manager.create_session(host_id, root=root)
    manager.connect(session.session_id)
    return manager, session


def cmd_host_connect(args: argparse.Namespace) -> int:
    """Connect a host in this process."""
    try:
        manager, session = _new_host_session(args.host_id, args.root)
        payload = session.to_dict()
        payload.update(manager.effective_capabilities(session.session_id))
        if args.json:
            _json_print(payload)
        else:
            print(f"Connected host session {session.session_id}")
            print(f"  Host: {session.host_id} [{session.host_type}]")
            print(f"  State: {session.state.value}")
            print(f"  Root: {session.config.root}")
            print("  Note: sessions are process-local; this one ends when the command exits.")
        return 0
    except (HostLifecycleError, KeyError, ValueError, OSError) as exc:
        return _host_error(exc, "connect")


def cmd_host_disconnect(args: argparse.Namespace) -> int:
    """Disconnect live sessions for a host (idempotent)."""
    try:
        manager = get_session_manager()
        sessions = manager.list_sessions(args.host_id)
        if not sessions:
            if args.json:
                _json_print({"host_id": args.host_id, "disconnected": [], "note": "no live session"})
            else:
                print(f"No live session for host {args.host_id} in this process.")
            return 0
        disconnected = []
        for session in sessions:
            manager.disconnect(session.session_id)
            disconnected.append(str(session.session_id))
        if args.json:
            _json_print({"host_id": args.host_id, "disconnected": disconnected})
        else:
            print(f"Disconnected {len(disconnected)} session(s) for host {args.host_id}.")
        return 0
    except (HostLifecycleError, KeyError, ValueError, OSError) as exc:
        return _host_error(exc, "disconnect")


def cmd_host_health(args: argparse.Namespace) -> int:
    """Run an adapter-specific health check, probing the host if needed."""
    try:
        manager = get_session_manager()
        sessions = [s for s in manager.list_sessions(args.host_id)
                    if s.state.value in {"connected", "degraded"}]
        created = None
        if sessions:
            session = sessions[-1]
        else:
            manager, session = _new_host_session(args.host_id, args.root)
            created = session
        result = manager.health(session.session_id)
        if created is not None:
            manager.disconnect(created.session_id)
        if args.json:
            _json_print(result)
        else:
            health = result["health"]
            print(f"Host health: {health['state']}")
            print(f"  Host: {args.host_id}")
            print(f"  Checked: {health['checked_at']}")
            for issue in health["issues"]:
                print(f"  Issue: {issue}")
        return 0 if result["health"]["state"] == "healthy" else 1
    except (HostLifecycleError, KeyError, ValueError, OSError) as exc:
        return _host_error(exc, "health check")


def cmd_host_recover(args: argparse.Namespace) -> int:
    """Recover a failed/degraded session, or establish one if absent."""
    try:
        manager = get_session_manager()
        sessions = manager.list_sessions(args.host_id)
        if sessions:
            session = sessions[-1]
            try:
                manager.connect(session.session_id)
            except HostLifecycleError:
                session = manager.recover(session.session_id, attempts=args.attempts)
        else:
            manager, session = _new_host_session(args.host_id, args.root)
        if args.json:
            _json_print(session.to_dict())
        else:
            print(f"Host recovered: {session.host_id} -> {session.state.value}")
        return 0
    except (HostLifecycleError, KeyError, ValueError, OSError) as exc:
        return _host_error(exc, "recover")


def cmd_host_sessions(args: argparse.Namespace) -> int:
    """List live sessions in this process."""
    try:
        manager = get_session_manager()
        sessions = [s for s in manager.list_sessions(args.host_id) if s.state in ACTIVE_STATES]
        payload = [session.to_dict() for session in sessions]
        if args.json:
            _json_print({"sessions": payload, "process_local": True})
        elif not payload:
            print("No live host sessions in this process.")
        else:
            for item in payload:
                print(f"  {item['session_id']}  {item['host_id']}  [{item['host_type']}]  {item['state']}")
        return 0
    except (HostLifecycleError, KeyError, ValueError, OSError) as exc:
        return _host_error(exc, "session listing")


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


def _is_uuid4(value: str) -> bool:
    """True when ``value`` is a UUID v4 string accepted by identity schema 1.0."""

    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError):
        return False
    return parsed.version == 4


def cmd_identity(args: argparse.Namespace) -> int:
    """Manage project identity (.guardian-x/identity.json)."""
    try:
        root = _resolve_project(args)
        if not root.is_dir():
            print(f"Error: directory not found: {root}", file=sys.stderr)
            return 2
        
        action = args.identity_action
        
        if action == "create":
            if not args.confirm:
                # Check if identity already exists
                identity_file = root / ".guardian-x" / "identity.json"
                if identity_file.exists():
                    print(f"Identity already exists at {identity_file}. Use --confirm to overwrite.", file=sys.stderr)
                    return 2
            identity = create_identity(
                root,
                project_name=args.name,
                description=args.description or "",
                created_by=args.created_by,
                project_id=args.project_id,
                confirm=args.confirm,
            )
            print(f"Created project identity:")
            print(f"  project_id: {identity.project_id}")
            print(f"  project_name: {identity.project_name}")
            print(f"  created_at: {identity.created_at}")
            print(f"  identity_hash: {identity.identity_hash}")
            print(f"  location: {root / '.guardian-x' / 'identity.json'}")
            return 0
        
        elif action == "show":
            try:
                identity = read_identity(root)
                if args.json:
                    _json_print(identity.to_dict())
                else:
                    print(f"Project Identity:")
                    print(f"  schema_version: {identity.schema_version}")
                    print(f"  project_id: {identity.project_id}")
                    print(f"  project_name: {identity.project_name}")
                    print(f"  description: {identity.description or '(none)'}")
                    print(f"  created_at: {identity.created_at}")
                    print(f"  created_by: {identity.created_by}")
                    print(f"  ogx_version: {identity.ogx_version}")
                    print(f"  identity_hash: {identity.identity_hash}")
                    print(f"  hash_valid: {identity.verify_hash()}")
                    print(f"  location: {root / '.guardian-x' / 'identity.json'}")
                return 0
            except IdentityMissingError as e:
                print(f"Identity not found: {e}", file=sys.stderr)
                return 2
            except IdentityCorruptedError as e:
                print(f"Identity corrupted: {e}", file=sys.stderr)
                if e.details:
                    print(f"  Details: {e.details}", file=sys.stderr)
                return 2
            except IdentityValidationError as e:
                print(f"Identity validation failed: {e}", file=sys.stderr)
                return 2
        
        elif action == "validate":
            result = validate_identity_file(root)
            if args.json:
                # Convert ProjectIdentity to dict if present
                if result["identity"]:
                    result = {**result, "identity": result["identity"].to_dict()}
                _json_print(result)
            else:
                if result["valid"]:
                    print(f"Identity is VALID")
                    ident = result["identity"]
                    print(f"  project_id: {ident.project_id}")
                    print(f"  project_name: {ident.project_name}")
                    print(f"  hash_valid: {ident.verify_hash()}")
                else:
                    print(f"Identity is INVALID")
                    for err in result["errors"]:
                        print(f"  ERROR: {err}")
                for warn in result["warnings"]:
                    print(f"  WARNING: {warn}")
            return 0 if result["valid"] else 1
        
        elif action == "explain":
            report = explain_identity(root)
            if args.json:
                _json_print(report)
            else:
                print(f"Identity evidence for {report['root']}:")
                print(f"  classification: {report['classification']}")
                for item in report["evidence"]:
                    print(f"  evidence: {item['kind']} [{item['status']}] {item['detail']}")
                for conflict in report["conflicts"]:
                    print(f"  CONFLICT: {conflict}")
                print(f"  recommendation: {report['recommendation']}")
            return 0 if report["classification"] != "conflict" else 1

        elif action == "fork":
            if not args.destination:
                print("Fork requires --destination.", file=sys.stderr)
                return 2
            if not args.confirm:
                print("Forking creates a new project identity; pass --confirm.", file=sys.stderr)
                return 2
            try:
                result = fork_project(
                    root,
                    args.destination,
                    new_name=args.name,
                    confirm=True,
                    description=args.description,
                )
            except IdentityError as e:
                print(f"Fork failed: {e}", file=sys.stderr)
                return 2
            if args.json:
                _json_print(result)
            else:
                print("Forked project:")
                print(f"  source: {result['source']}")
                print(f"  destination: {result['destination']}")
                print(f"  parent_project_id: {result['parent_project_id']}")
                print(f"  project_id: {result['project_id']}")
                print(f"  files_copied: {result['files_copied']}")
            for warning in result["warnings"]:
                print(f"  WARNING: {warning}", file=sys.stderr)
            return 0

        elif action == "recover":
            if not args.confirm:
                print("Recovery requires explicit --confirm. This may create a new identity if recovery evidence is insufficient.", file=sys.stderr)
                return 2
            try:
                # Use real evidence instead of guessing. Only UUID v4 ids can
                # become identity files; deterministic provisional ids must be
                # adopted explicitly.
                analysis = explain_identity(root)
                registry_identity = None
                manifest_project_id = None
                registry_info = analysis.get("registry")
                if registry_info and _is_uuid4(registry_info["project_id"]):
                    registry_identity = ProjectIdentity(
                        schema_version="1.0",
                        project_id=registry_info["project_id"],
                        project_name=registry_info["canonical_name"],
                        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        created_by="registry",
                        ogx_version="recovery",
                    )
                elif registry_info:
                    print(
                        "Registry evidence uses a provisional (non-v4) project id; "
                        "it cannot be adopted as a project identity without review.",
                        file=sys.stderr,
                    )
                candidate = analysis.get("manifest_project_id")
                if candidate and _is_uuid4(candidate):
                    manifest_project_id = candidate
                identity = recover_identity(
                    root,
                    registry_identity=registry_identity,
                    manifest_project_id=manifest_project_id,
                    confirm=True,
                )
                print(f"Recovered identity:")
                print(f"  project_id: {identity.project_id}")
                print(f"  project_name: {identity.project_name}")
                print(f"  created_at: {identity.created_at}")
                print(f"  identity_hash: {identity.identity_hash}")
                print(f"  evidence: {'registry' if registry_identity else ('manifest' if manifest_project_id else 'new identity (no strong evidence)')}")
                return 0
            except IdentityError as e:
                print(f"Recovery failed: {e}", file=sys.stderr)
                return 2
        
        elif action == "update":
            if not args.field or not args.value:
                print("Both --field and --value are required for update.", file=sys.stderr)
                return 2
            identity = update_identity_field(root, args.field, args.value, confirm=args.confirm)
            print(f"Updated {args.field}:")
            print(f"  project_id: {identity.project_id}")
            print(f"  project_name: {identity.project_name}")
            print(f"  description: {identity.description}")
            print(f"  identity_hash: {identity.identity_hash}")
            return 0
        
        else:
            print(f"Unknown identity action: {action}", file=sys.stderr)
            return 2
            
    except (ValueError, OSError, IdentityError) as exc:
        print(f"Identity operation failed: {exc}", file=sys.stderr)
        return 2


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
    from .platform.cli_commands import cmd_platform_engine

    return cmd_platform_engine(args)


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


def cmd_release(args: argparse.Namespace) -> int:
    """Generate or verify release artifact checksums (REL-02)."""
    try:
        action = args.release_action
        if action == "manifest":
            artifacts = [Path(item) for item in args.artifacts]
            sums_path = Path(args.sums) if args.sums else artifacts[0].parent / "SHA256SUMS"
            result = write_manifests(
                artifacts,
                version=args.version or __version__,
                sums_path=sums_path,
                manifest_path=Path(args.manifest) if args.manifest else None,
            )
            _json_print(result)
            return 0
        manifest = Path(args.manifest)
        text = manifest.read_text(encoding="utf-8")
        report = verify_sums(text, args.directory or manifest.parent)
        report["manifest"] = str(manifest)
        _json_print(report)
        return 0 if report["all_ok"] else 1
    except (OSError, ArtifactError) as exc:
        print(f"Release artifact operation failed: {exc}", file=sys.stderr)
        return 2


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


# ==================== Provider / Platform Commands ====================
# Handlers live in ogx.platform.cli_commands; re-exported for the argument parser.

from .platform.cli_commands import (  # noqa: E402
    cmd_provider_list,
    cmd_provider_show,
    cmd_provider_status,
    cmd_providers_capabilities,
    cmd_providers_inspect,
    cmd_providers_list,
    cmd_remote_activate,
    cmd_remote_discover,
    cmd_remote_inspect,
)


# ==================== Repository Commands ====================

def cmd_repository_list(args: argparse.Namespace) -> int:
    """List repositories."""
    try:
        from .registry import get_registry
        from uuid import UUID
        
        registry = get_registry()
        
        repos = []
        if args.project:
            try:
                pid = UUID(args.project)
                project = registry.get_project(pid)
                repos = registry.list_repositories(pid)
            except ValueError:
                project = registry.find_project_by_identity(canonical_name=args.project)
                if project:
                    repos = registry.list_repositories(project.project_id)
        elif args.host:
            try:
                hid = UUID(args.host)
                # We'd need to get repositories for this host from host registry
                from .hosts import get_host_registry
                host_registry = get_host_registry()
                host_repos = host_registry.get_repositories_for_host(hid) if hasattr(host_registry, 'get_repositories_for_host') else []
                for hr in host_repos:
                    try:
                        repos.append(registry.get_repository(hr["repository_id"]))
                    except Exception:
                        pass
            except ValueError:
                pass
        else:
            # List all
            projects = registry.list_projects()
            for p in projects:
                repos.extend(registry.list_repositories(p.project_id))

        if getattr(args, "provider", None):
            repos = [r for r in repos if r.provider == args.provider]

        if args.json:
            _json_print({
                "repositories": [
                    {
                        "repository_id": str(r.repository_id),
                        "project_id": str(r.project_id),
                        "provider": r.provider,
                        "repository_url": r.repository_url,
                        "repository_name": r.repository_name,
                        "default_branch": r.default_branch,
                        "visibility": r.visibility,
                    }
                    for r in repos
                ],
                "count": len(repos),
            })
        else:
            if not repos:
                print("No repositories found.")
            for r in repos:
                print(f"  {r.repository_id}  {r.provider}  {r.repository_name or r.repository_url}")
        return 0
    except (OSError, ValueError, Exception) as exc:
        print(f"Repository list failed: {exc}", file=sys.stderr)
        return 2


def cmd_repository_show(args: argparse.Namespace) -> int:
    """Show repository details."""
    try:
        from .registry import get_registry
        from uuid import UUID
        
        registry = get_registry()
        
        try:
            rid = UUID(args.repository_id)
            repo = registry.get_repository(rid)
        except ValueError:
            # Try to find by name - not directly supported, need to search
            print(f"Error: Repository ID must be a UUID", file=sys.stderr)
            return 2
        
        if args.json:
            _json_print({
                "repository_id": str(repo.repository_id),
                "project_id": str(repo.project_id),
                "provider": repo.provider,
                "repository_url": repo.repository_url,
                "normalized_repository_url": repo.normalized_repository_url,
                "repository_name": repo.repository_name,
                "default_branch": repo.default_branch,
                "visibility": repo.visibility,
                "metadata": repo.metadata,
                "created_at": repo.created_at.isoformat(),
                "updated_at": repo.updated_at.isoformat(),
            })
        else:
            print(f"Repository: {repo.repository_name or repo.repository_url}")
            print(f"  ID: {repo.repository_id}")
            print(f"  Project: {repo.project_id}")
            print(f"  Provider: {repo.provider}")
            print(f"  URL: {repo.repository_url}")
            print(f"  Normalized: {repo.normalized_repository_url}")
            print(f"  Default Branch: {repo.default_branch}")
            print(f"  Visibility: {repo.visibility}")
        return 0
    except Exception as exc:
        print(f"Repository show failed: {exc}", file=sys.stderr)
        return 2


# ==================== Workspace Commands ====================

def cmd_workspace_list(args: argparse.Namespace) -> int:
    """List workspaces."""
    try:
        from .registry import get_registry
        from uuid import UUID
        
        registry = get_registry()
        
        workspaces = []
        if args.project:
            try:
                pid = UUID(args.project)
                workspaces = registry.list_workspaces(pid)
            except ValueError:
                project = registry.find_project_by_identity(canonical_name=args.project)
                if project:
                    workspaces = registry.list_workspaces(project.project_id)
        elif args.host:
            # Get workspaces for host via host registry
            try:
                hid = UUID(args.host)
                from .hosts import get_host_registry
                host_registry = get_host_registry()
                host_workspaces = host_registry.get_workspaces_for_host(hid) if hasattr(host_registry, 'get_workspaces_for_host') else []
                for hw in host_workspaces:
                    try:
                        workspaces.append(registry.get_workspace(hw["workspace_id"]))
                    except Exception:
                        pass
            except ValueError:
                pass
        else:
            # List all
            projects = registry.list_projects()
            for p in projects:
                workspaces.extend(registry.list_workspaces(p.project_id))
        
        if args.json:
            _json_print({
                "workspaces": [
                    {
                        "workspace_id": str(w.workspace_id),
                        "project_id": str(w.project_id),
                        "repository_id": str(w.repository_id) if w.repository_id else None,
                        "workspace_provider": w.workspace_provider,
                        "workspace_type": w.workspace_type,
                        "location_reference": w.location_reference,
                        "normalized_location": w.normalized_location,
                        "display_name": w.display_name,
                        "status": w.status.value,
                    }
                    for w in workspaces
                ],
                "count": len(workspaces),
            })
        else:
            if not workspaces:
                print("No workspaces found.")
            for w in workspaces:
                print(f"  {w.workspace_id}  [{w.status.value}]  {w.location_reference}")
        return 0
    except (OSError, ValueError, Exception) as exc:
        print(f"Workspace list failed: {exc}", file=sys.stderr)
        return 2


def cmd_workspace_show(args: argparse.Namespace) -> int:
    """Show workspace details."""
    try:
        from .registry import get_registry
        from uuid import UUID
        
        registry = get_registry()
        
        try:
            wid = UUID(args.workspace_id)
            ws = registry.get_workspace(wid)
        except ValueError:
            print(f"Error: Workspace ID must be a UUID", file=sys.stderr)
            return 2
        
        if args.json:
            _json_print({
                "workspace_id": str(ws.workspace_id),
                "project_id": str(ws.project_id),
                "repository_id": str(ws.repository_id) if ws.repository_id else None,
                "workspace_provider": ws.workspace_provider,
                "workspace_type": ws.workspace_type,
                "location_reference": ws.location_reference,
                "normalized_location": ws.normalized_location,
                "display_name": ws.display_name,
                "status": ws.status.value,
                "last_seen_at": ws.last_seen_at.isoformat(),
                "metadata": ws.metadata,
                "created_at": ws.created_at.isoformat(),
                "updated_at": ws.updated_at.isoformat(),
            })
        else:
            print(f"Workspace: {ws.display_name or ws.location_reference}")
            print(f"  ID: {ws.workspace_id}")
            print(f"  Project: {ws.project_id}")
            print(f"  Repository: {ws.repository_id or 'None'}")
            print(f"  Provider: {ws.workspace_provider}")
            print(f"  Type: {ws.workspace_type}")
            print(f"  Location: {ws.location_reference}")
            print(f"  Normalized: {ws.normalized_location}")
            print(f"  Status: {ws.status.value}")
            print(f"  Last Seen: {ws.last_seen_at.isoformat()}")
        return 0
    except Exception as exc:
        print(f"Workspace show failed: {exc}", file=sys.stderr)
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
        elif args.enterprise_action == "backend":
            _json_print(store.health())
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
        elif args.enterprise_action == "service-account-create":
            scopes = [item.strip() for item in (args.scopes or "").split(",") if item.strip()]
            _json_print(ServiceIdentityManager(store).create_account(
                args.org, args.name, args.role, scopes, args.actor_subject))
        elif args.enterprise_action == "service-accounts":
            _json_print(ServiceIdentityManager(store).list_accounts(args.org, args.actor_subject))
        elif args.enterprise_action == "token-issue":
            issued = ServiceIdentityManager(store).issue_token(
                args.org, args.account, args.actor_subject, args.ttl)
            if args.json:
                _json_print(issued.to_dict())
            else:
                print(issued.token)
                print(
                    f"# token_id={issued.token_id} expires_at={issued.expires_at} "
                    f"scopes={','.join(issued.scopes)}",
                    file=sys.stderr,
                )
        elif args.enterprise_action == "tokens":
            _json_print(ServiceIdentityManager(store).list_tokens(args.org, args.actor_subject))
        elif args.enterprise_action == "token-revoke":
            _json_print(ServiceIdentityManager(store).revoke_token(
                args.org, args.token_id, args.actor_subject))
        elif args.enterprise_action == "token-rotate":
            rotated = ServiceIdentityManager(store).rotate_token(
                args.org, args.token_id, args.actor_subject, ttl_seconds=args.ttl)
            if args.json:
                _json_print(rotated)
            else:
                print(rotated["token"]["token"])
                print(
                    f"# token_id={rotated['token']['token_id']} "
                    f"previous_token_id={rotated['previous_token_id']} "
                    f"revoked_previous={rotated['revoked_previous']} "
                    f"expires_at={rotated['token']['expires_at']}",
                    file=sys.stderr,
                )
        elif args.enterprise_action == "secret-set":
            if not args.stdin:
                raise ValueError("Use --stdin so the secret never appears in shell history.")
            value = sys.stdin.read()
            if value.endswith("\n"):
                value = value[:-1]
            _json_print(SecretsVault(store).set(args.org, args.name, value, args.actor_subject))
        elif args.enterprise_action == "secret-get":
            value = SecretsVault(store).get(args.org, args.name, args.actor_subject)
            if args.json:
                _json_print({"organization_id": args.org, "name": args.name, "value": "[REDACTED]"})
            else:
                print(f"Secret '{args.name}' retrieved for organization '{args.org}' (value redacted).")
        elif args.enterprise_action == "secrets":
            _json_print(SecretsVault(store).names(args.org, args.actor_subject))
        elif args.enterprise_action == "kms-status":
            _json_print(SecretsVault(store).status(args.org, args.actor_subject))
        elif args.enterprise_action == "kms-rotate":
            _json_print(SecretsVault(store).rotate(args.org, args.actor_subject))
        elif args.enterprise_action == "backup":
            signer = HmacSigningProvider() if args.sign else None
            _json_print(create_backup(store, args.destination, encrypt=args.encrypt,
                                      signer=signer, label=args.label))
        elif args.enterprise_action == "backups":
            _json_print({"items": list_backups(args.destination)})
        elif args.enterprise_action == "backup-verify":
            signer = HmacSigningProvider() if args.check_signature else None
            _json_print(verify_backup(args.backup_path, signer=signer,
                                      require_signature=args.require_signature))
        elif args.enterprise_action == "restore":
            signer = HmacSigningProvider() if args.check_signature else None
            _json_print(restore_backup(args.backup_path, args.target,
                                       signer=signer,
                                       require_signature=args.require_signature,
                                       overwrite=args.overwrite))
        elif args.enterprise_action == "restore-recover":
            _json_print(recover_restore(args.target))
        elif args.enterprise_action == "backup-prune":
            _json_print(prune_backups(args.destination, args.keep))
        elif args.enterprise_action == "signing-keys":
            _json_print(HmacSigningProvider().describe())
        elif args.enterprise_action == "signing-key-rotate":
            key_id = HmacSigningProvider().rotate()
            _json_print({"rotated": True, "active_key_id": key_id})
        return 0
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Enterprise data operation failed: {exc}", file=sys.stderr); return 2

def cmd_authz(args: argparse.Namespace) -> int:
    """Show or evaluate the shared authorization policy (ENT-04 / UI-06)."""
    engine = PolicyEngine()
    if args.authz_action == "matrix":
        matrix = engine.matrix()
        if args.json:
            _json_print({"matrix": matrix, "actions": engine.actions()})
        else:
            print(f"{'action':<34}" + "".join(f"{role:<10}" for role in matrix))
            for action in engine.actions():
                cells = "".join(
                    ("allow" if action in matrix[role] else "-").ljust(10) for role in matrix
                )
                print(f"{action:<34}{cells}")
        return 0

    roles = tuple(item.strip() for item in (args.roles or "").split(",") if item.strip())
    if not roles:
        print("--roles is required for check/explain.", file=sys.stderr)
        return 2
    scopes = None
    if args.scopes is not None:
        scopes = tuple(item.strip() for item in args.scopes.split(",") if item.strip())
    try:
        principal = Principal(
            subject=args.subject or "cli-operator",
            kind="service" if scopes is not None else "user",
            roles=roles,
            tenant_id=args.tenant,
            scopes=scopes,
        )
    except ValueError as exc:
        print(f"Invalid principal: {exc}", file=sys.stderr)
        return 2
    resource = None
    if args.resource_kind or args.resource_id:
        resource = Resource(
            kind=args.resource_kind or "organization",
            id=args.resource_id,
            tenant_id=args.tenant,
        )
    decision = engine.decide(
        principal, args.action, resource, context={"reason": args.reason or ""}
    )
    payload = {
        "decision": decision.to_dict(),
        "effective_permissions": sorted(principal.permissions),
        "roles": list(principal.roles),
        "scopes": list(scopes) if scopes is not None else None,
    }
    if args.json or args.authz_action == "explain":
        _json_print(payload)
    else:
        verdict = "ALLOW" if decision.allowed else "DENY"
        print(f"{verdict} {decision.action} for {decision.subject} ({decision.reason})")
        print(f"  rule: {decision.rule}")
        print(f"  effective permissions: {len(payload['effective_permissions'])}")
    return 0 if decision.allowed else 1


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the authenticated API and dashboard on a loopback address."""
    from .api.server import ApiContext, serve as run_api

    store = EnterpriseStore()
    context = ApiContext(store=store, rate_limit=args.rate_limit)
    print(f"OGX API {context.version} listening on http://{args.host}:{args.port}/")
    print("Authentication: service-identity bearer token (no interactive SSO in this build).")
    print(
        "Issue a token with: ogx enterprise token-issue --org <org> "
        "--account <account> --actor-subject <owner>"
    )
    try:
        run_api(context, host=args.host, port=args.port)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


def cmd_host_workspaces(args: argparse.Namespace) -> int:
    """List cloud workspace instances through the connected host adapter."""
    manager = None
    session = None
    try:
        manager, session = _new_host_session(args.host_id, args.root)
        adapter = getattr(session, "adapter", None)
        lister = getattr(adapter, "list_workspaces", None)
        if lister is None:
            print("This host adapter does not expose cloud workspaces.", file=sys.stderr)
            return 2
        result = lister(args.limit)
        if args.json:
            _json_print(result)
        else:
            print(
                f"Workspaces ({result.get('provider')}, scope {result.get('scope')}): "
                f"{result.get('total', 0)}"
            )
            for item in result.get("items", []):
                print(
                    f"  {item['name']}  {item['state']}  "
                    f"{item.get('repository') or '-'}  {item.get('machine') or '-'}"
                )
        return 0
    except (HostLifecycleError, KeyError, ValueError, OSError) as exc:
        return _host_error(exc, "workspaces")
    finally:
        if manager is not None and session is not None:
            try:
                manager.disconnect(session.session_id)
            except Exception:
                pass


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


def _patch_error(exc: Exception) -> None:
    print(f"Patch operation failed: {exc}", file=sys.stderr)
    for detail in getattr(exc, "details", ()) or ():
        print(f"  {detail}", file=sys.stderr)


def cmd_patch(args: argparse.Namespace) -> int:
    """Plan, apply, inspect and roll back controlled source patches."""
    try:
        if args.patch_action == "plan":
            spec_path = Path(args.spec).expanduser()
            if spec_path.is_symlink() or not spec_path.is_file():
                print(f"Patch specification not found: {spec_path}", file=sys.stderr)
                return 2
            if spec_path.stat().st_size > 1_000_000:
                print("Patch specification exceeds 1 MB.", file=sys.stderr)
                return 2
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            changes = spec.get("changes") if isinstance(spec, dict) else spec
            if not isinstance(changes, list):
                print("Patch specification must contain a 'changes' array.", file=sys.stderr)
                return 2
            plan = plan_source_patch(
                _root(args.path),
                changes,
                project_id=args.project_id,
                actor_subject=args.actor_subject or os.environ.get("OGX_ACTOR_SUBJECT", ""),
            )
            if args.json:
                _json_print(plan)
            else:
                print(f"Patch plan: {plan['plan_id']}")
                print(f"  files: {plan['summary']['files']}")
                for path in plan['summary']['paths']:
                    print(f"  - {path}")
                print("No project file was modified.")
            return 0
        if args.patch_action == "apply":
            result = apply_source_patch(
                args.plan_id,
                approved=args.confirm,
                actor_subject=args.actor_subject,
            )
            if args.json:
                _json_print(result)
            else:
                print(f"Applied patch plan {result['plan_id']} to {len(result['applied'])} file(s).")
                print(f"  backup: {result['backup_dir']}")
                print("Rollback: ogx patch rollback " + result['plan_id'] + " --confirm")
            return 0
        if args.patch_action == "rollback":
            result = rollback_source_patch(
                args.plan_id,
                approved=args.confirm,
                actor_subject=args.actor_subject,
            )
            if args.json:
                _json_print(result)
            else:
                print(f"Rolled back {result['plan_id']}: restored {len(result['restored'])} file(s).")
            return 0
        if args.patch_action == "show":
            plan = load_source_patch(args.plan_id)
            _json_print(plan) if args.json else print(json.dumps(plan, indent=2))
            return 0
        if args.patch_action == "list":
            plans = list_source_patches(limit=args.limit)
            if args.json:
                _json_print(plans)
            else:
                if not plans:
                    print("No patch plans.")
                for item in plans:
                    print(f"  {item['plan_id']}  [{item['state']}]  {item['files']} file(s)  {item['project_root']}")
            return 0
        print("usage: ogx patch plan|apply|rollback|show|list", file=sys.stderr)
        return 2
    except PatchApprovalRequired as exc:
        _patch_error(exc)
        return 2
    except PatchIntegrityError as exc:
        _patch_error(exc)
        return 1
    except (PatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        _patch_error(exc)
        return 2


def cmd_workflow(args: argparse.Namespace) -> int:
    """Manage durable engineering workflows."""
    try:
        orchestrator = Orchestrator()
        action = args.workflow_action
        if action == "create":
            metadata: dict[str, object] = {}
            if args.patch_plan:
                metadata["patch_plan_id"] = args.patch_plan
            if args.changed_file:
                metadata["changed_file"] = args.changed_file
            if args.sandbox_image:
                metadata["sandbox_image"] = args.sandbox_image
            if args.test_command:
                metadata["test_command"] = list(args.test_command)
            if args.actor_subject:
                metadata["actor_subject"] = args.actor_subject
            result = orchestrator.create(
                _root(args.path),
                project_id=args.project_id,
                workflow_id=args.workflow_id,
                idempotency_key=args.idempotency_key,
                skip_stages=args.skip or (),
                metadata=metadata,
            )
            payload = result["workflow"]
            if args.json:
                _json_print(result)
            else:
                state = "created" if result["created"] else "already exists"
                print(f"Workflow {payload['workflow_id']} {state}.")
                print(f"  stages: {', '.join(stage['name'] for stage in payload['stages'])}")
                print("Run it with: ogx workflow run " + payload['workflow_id'])
            return 0
        if action == "run":
            actor = (args.actor or os.environ.get("OGX_ACTOR_SUBJECT", "") or "operator").strip()
            for stage in args.approve or ():
                orchestrator.approve(args.workflow_id, stage, actor=actor, note="approved on run")
            result = orchestrator.run(
                args.workflow_id,
                max_stages=args.max_stages,
                lease_seconds=args.lease_seconds,
            )
            if args.json:
                _json_print(result)
            else:
                workflow = result["workflow"]
                print(f"Workflow {workflow['workflow_id']}: {workflow['state']}")
                if result["executed"]:
                    print(f"  executed: {', '.join(result['executed'])}")
                for stage in workflow["stages"]:
                    print(f"  {stage['ordinal']}. {stage['name']:<8} {stage['state']}" + (f"  ({stage['error']})" if stage['error'] else ""))
                if workflow["state"] == "awaiting_approval":
                    print("Approve with: ogx workflow approve " + workflow['workflow_id'] + " --stage <stage> --actor <identity>")
            return 0
        if action == "status":
            payload = orchestrator.status(args.workflow_id, events=args.events)
            _json_print(payload) if args.json else print(json.dumps(payload, indent=2))
            return 0
        if action == "list":
            payload = orchestrator.list(limit=args.limit)
            if args.json:
                _json_print(payload)
            else:
                if not payload:
                    print("No workflows.")
                for item in payload:
                    print(f"  {item['workflow_id']}  [{item['state']}]  stage={item['current_stage']}  {item['project_root']}")
            return 0
        if action == "events":
            payload = orchestrator.events(args.workflow_id, limit=args.limit)
            _json_print(payload) if args.json else print(json.dumps(payload, indent=2))
            return 0
        if action == "approve":
            payload = orchestrator.approve(args.workflow_id, args.stage, actor=args.actor, note=args.note or "")
            _json_print(payload) if args.json else print(f"Stage '{args.stage}' approved for {args.workflow_id}.")
            return 0
        if action == "cancel":
            payload = orchestrator.cancel(args.workflow_id, actor=args.actor, reason=args.reason or "")
            _json_print(payload) if args.json else print(f"Workflow {args.workflow_id} cancelled.")
            return 0
        if action == "retry":
            payload = orchestrator.retry(args.workflow_id, stage=args.stage)
            _json_print(payload) if args.json else print(f"Workflow {args.workflow_id} reset to created; resume with 'workflow run'.")
            return 0
        print("usage: ogx workflow create|run|status|list|events|approve|cancel|retry", file=sys.stderr)
        return 2
    except WorkflowError as exc:
        print(f"Workflow operation failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Workflow operation failed: {exc}", file=sys.stderr)
        return 2


def _git_provider_choices() -> tuple[str, ...]:
    from .platform import ProviderKind, get_provider_registry

    return tuple(d.provider_id for d in get_provider_registry().by_kind(ProviderKind.GIT_HOSTING))


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
            item.add_argument("--cache", action="store_true",
                              help="Reuse per-file analysis for unchanged content (external scan cache)")
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

    intelligence = sub.add_parser(
        "intelligence", help="Universal static project intelligence and engineering analysis")
    intelligence_sub = intelligence.add_subparsers(dest="intelligence_action", required=True)
    intelligence_analyze = intelligence_sub.add_parser(
        "analyze", help="Analyze a project read-only; no code, build, test or package manager is executed")
    intelligence_analyze.add_argument(
        "target", nargs="?", default=None,
        help="Registry project ID or canonical name (ignored when --path/--project is used)")
    intelligence_analyze.add_argument("--path", help="Project root directory")
    intelligence_analyze.add_argument("--project", help="Name of a registered project")
    intelligence_analyze.add_argument(
        "--format", choices=("terminal", "json", "markdown", "both"), default="terminal",
        help="terminal output (default), stdout JSON/Markdown, or external JSON+Markdown artifacts")
    intelligence_analyze.add_argument("--json", action="store_true", help="Alias for --format json")
    intelligence_analyze.add_argument("--languages", action="store_true", help="Show only language findings (plus overview)")
    intelligence_analyze.add_argument("--dependencies", action="store_true", help="Show only dependency findings (plus overview)")
    intelligence_analyze.add_argument("--architecture", action="store_true", help="Show only architecture findings (plus overview)")
    intelligence_analyze.add_argument("--health", action="store_true", help="Show only engineering health (plus overview)")
    intelligence_analyze.add_argument("--exclude", action="append", metavar="DIR",
                                      default=[], help="Additional directory name to exclude")
    intelligence_analyze.add_argument("--max-files", type=int, default=12000,
                                      help="Maximum indexed files (default: 12000)")
    intelligence_analyze.add_argument("--max-depth", type=int, default=6,
                                      help="Maximum bounded tree depth (default: 6)")
    intelligence_analyze.add_argument("--no-registry", action="store_true",
                                      help="Do not consult Registry V2 for identity association")
    intelligence_analyze.set_defaults(func=cmd_intelligence)

    android = sub.add_parser("android", help="Run the dedicated Android static engine; no build/device execution")
    android.add_argument("path", nargs="?", default=None)
    android.add_argument("--project", help="Name of a registered project")
    android.add_argument("--format", choices=("json", "markdown", "both"), default="both")
    android.add_argument("--strict", action="store_true", help="Return 1 when high/critical findings exist")
    android.set_defaults(func=cmd_android)

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

    registry = sub.add_parser("registry", help="Manage Registry V2 (projects, repositories, workspaces, hosts)")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    
    reg_list = registry_sub.add_parser("list", help="List all projects in registry")
    reg_list.add_argument("--json", action="store_true", help="Print full registry JSON")
    reg_list.add_argument("--limit", type=int, default=100, help="Maximum number of results")
    reg_list.add_argument("--offset", type=int, default=0, help="Offset for pagination")
    reg_list.set_defaults(func=cmd_registry_list)
    
    reg_show = registry_sub.add_parser("show", help="Show project details from registry")
    reg_show.add_argument("project_id", help="Project UUID")
    reg_show.add_argument("--json", action="store_true", help="Print full project JSON")
    reg_show.set_defaults(func=cmd_registry_show)
    
    reg_status = registry_sub.add_parser("status", help="Show registry status and statistics")
    reg_status.add_argument("--json", action="store_true", help="Print status JSON")
    reg_status.set_defaults(func=cmd_registry_status)
    
    reg_integrity = registry_sub.add_parser("integrity", help="Check registry database integrity")
    reg_integrity.add_argument("--json", action="store_true", help="Print integrity check JSON")
    reg_integrity.set_defaults(func=cmd_registry_integrity)
    
    reg_migrate = registry_sub.add_parser("migrate", help="Migrate from Registry V1 to V2")
    reg_migrate.add_argument("--dry-run", action="store_true", help="Preview migration without applying")
    reg_migrate.add_argument("--confirm", action="store_true", help="Confirm migration")
    reg_migrate.add_argument("--json", action="store_true", help="Print migration result JSON")
    reg_migrate.set_defaults(func=cmd_registry_migrate)
    
    reg_backup = registry_sub.add_parser("backup", help="Create a backup of the registry database")
    reg_backup.add_argument("--output", help="Backup file path (default: OGX_HOME/registry/registry-backup-<timestamp>.db)")
    reg_backup.add_argument("--json", action="store_true", help="Print backup result JSON")
    reg_backup.set_defaults(func=cmd_registry_backup)
    
    reg_recover = registry_sub.add_parser("recover", help="Recover registry from backup")
    reg_recover.add_argument("backup_path", help="Path to backup file")
    reg_recover.add_argument("--confirm", action="store_true", help="Confirm recovery (overwrites current registry)")
    reg_recover.set_defaults(func=cmd_registry_recover)
    
    pap = sub.add_parser("pap", help="GX-PAP Project Activation Protocol management")
    pap_sub = pap.add_subparsers(dest="pap_command", required=True)
    
    pap_inspect = pap_sub.add_parser("inspect", help="Inspect project PAP configuration")
    pap_inspect.add_argument("path", nargs="?", default=".", help="Project root directory")
    pap_inspect.add_argument("--project", help="Name of a registered project")
    pap_inspect.add_argument("--json", action="store_true", help="Print full configuration JSON")
    pap_inspect.set_defaults(func=cmd_pap_inspect)
    
    pap_validate = pap_sub.add_parser("validate", help="Validate project PAP manifest")
    pap_validate.add_argument("path", nargs="?", default=".", help="Project root directory")
    pap_validate.add_argument("--project", help="Name of a registered project")
    pap_validate.add_argument("--json", action="store_true", help="Print validation result JSON")
    pap_validate.set_defaults(func=cmd_pap_validate)
    
    pap_status = pap_sub.add_parser("status", help="Show project PAP activation status")
    pap_status.add_argument("path", nargs="?", default=".", help="Project root directory")
    pap_status.add_argument("--project", help="Name of a registered project")
    pap_status.add_argument("--json", action="store_true", help="Print status JSON")
    pap_status.set_defaults(func=cmd_pap_status)
    
    pap_schema = pap_sub.add_parser("schema", help="Print the GX-PAP manifest JSON schema")
    pap_schema.set_defaults(func=cmd_pap_schema)
    
    identity = sub.add_parser("identity", help="Manage project identity (.guardian-x/identity.json)")
    identity_sub = identity.add_subparsers(dest="identity_action", required=True)
    
    id_create = identity_sub.add_parser("create", help="Create a new project identity")
    id_create.add_argument("path", nargs="?", default=".", help="Project root directory")
    id_create.add_argument("--project", help="Name of a registered project")
    id_create.add_argument("--name", help="Project name (defaults to directory name)")
    id_create.add_argument("--description", help="Project description")
    id_create.add_argument("--created-by", help="Actor identity (defaults to environment)")
    id_create.add_argument("--project-id", help="Explicit UUID v4 (for recovery)")
    id_create.add_argument("--confirm", action="store_true", help="Overwrite existing identity")
    id_create.set_defaults(func=cmd_identity)
    
    id_show = identity_sub.add_parser("show", help="Show project identity")
    id_show.add_argument("path", nargs="?", default=".", help="Project root directory")
    id_show.add_argument("--project", help="Name of a registered project")
    id_show.add_argument("--json", action="store_true", help="Print full identity JSON")
    id_show.set_defaults(func=cmd_identity)
    
    id_validate = identity_sub.add_parser("validate", help="Validate project identity")
    id_validate.add_argument("path", nargs="?", default=".", help="Project root directory")
    id_validate.add_argument("--project", help="Name of a registered project")
    id_validate.add_argument("--json", action="store_true", help="Print validation result JSON")
    id_validate.set_defaults(func=cmd_identity)
    
    id_recover = identity_sub.add_parser("recover", help="Recover corrupted/missing identity")
    id_recover.add_argument("path", nargs="?", default=".", help="Project root directory")
    id_recover.add_argument("--project", help="Name of a registered project")
    id_recover.add_argument("--confirm", action="store_true", help="Confirm recovery/new identity creation")
    id_recover.set_defaults(func=cmd_identity)
    
    id_update = identity_sub.add_parser("update", help="Update mutable identity field")
    id_update.add_argument("path", nargs="?", default=".", help="Project root directory")
    id_update.add_argument("--project", help="Name of a registered project")
    id_update.add_argument("--field", required=True, choices=["project_name", "description"], help="Field to update")
    id_update.add_argument("--value", required=True, help="New value")
    id_update.add_argument("--confirm", action="store_true", help="Confirm update")
    id_update.set_defaults(func=cmd_identity)

    id_explain = identity_sub.add_parser("explain", help="Explain identity evidence and conflicts")
    id_explain.add_argument("path", nargs="?", default=".", help="Project root directory")
    id_explain.add_argument("--project", help="Name of a registered project")
    id_explain.add_argument("--json", action="store_true", help="Print evidence JSON")
    id_explain.set_defaults(func=cmd_identity)

    id_fork = identity_sub.add_parser("fork", help="Fork a project into a new identity")
    id_fork.add_argument("path", nargs="?", default=".", help="Source project root")
    id_fork.add_argument("--project", help="Name of a registered project")
    id_fork.add_argument("--destination", required=True, help="New project directory")
    id_fork.add_argument("--name", help="Name for the forked project")
    id_fork.add_argument("--description", help="Description for the forked project")
    id_fork.add_argument("--confirm", action="store_true", help="Confirm creating a new identity")
    id_fork.add_argument("--json", action="store_true", help="Print fork result JSON")
    id_fork.set_defaults(func=cmd_identity)

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

    platform = sub.add_parser("platform", help="Universal multi-platform status, capabilities and approval-gated operations")
    platform_sub = platform.add_subparsers(dest="platform_action", required=True)
    platform_status = platform_sub.add_parser("status", help="Show the universal provider engine status")
    platform_status.add_argument("--json", action="store_true")
    platform_status.set_defaults(func=cmd_platform)
    platform_caps = platform_sub.add_parser("capabilities", help="Print the machine-readable capability matrix")
    platform_caps.set_defaults(func=cmd_platform)
    platform_audit = platform_sub.add_parser("audit", help="Verify the hash-chained platform audit log")
    platform_audit.add_argument("--json", action="store_true")
    platform_audit.set_defaults(func=cmd_platform)
    platform_repo = platform_sub.add_parser("repo")
    platform_repo.add_argument("provider", choices=_git_provider_choices())
    platform_repo.add_argument("url")
    platform_repo.set_defaults(func=cmd_platform)
    platform_pr = platform_sub.add_parser("pr")
    platform_pr.add_argument("provider", choices=_git_provider_choices())
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

    release = sub.add_parser("release", help="Generate or verify release artifact checksums")
    release_sub = release.add_subparsers(dest="release_action", required=True)
    rel_manifest = release_sub.add_parser("manifest", help="Write SHA256SUMS and a JSON manifest for built artifacts")
    rel_manifest.add_argument("artifacts", nargs="+", help="Artifact files to checksum")
    rel_manifest.add_argument("--sums", help="Output path for the sha256sum-compatible file (default: alongside the first artifact)")
    rel_manifest.add_argument("--manifest", help="Optional JSON manifest output path")
    rel_manifest.add_argument("--version", help="Release version recorded in the JSON manifest")
    rel_manifest.set_defaults(func=cmd_release)
    rel_verify = release_sub.add_parser("verify", help="Verify artifacts against a checksum manifest")
    rel_verify.add_argument("manifest", help="SHA256SUMS file to verify")
    rel_verify.add_argument("--directory", help="Directory holding the artifacts (default: the manifest's directory)")
    rel_verify.set_defaults(func=cmd_release)
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

    discover = sub.add_parser("discover", help="Automatically discover projects in configured roots")
    discover.add_argument("root", nargs="*", default=None, help="Specific root path(s) to scan")
    discover.add_argument("--full", action="store_true", help="Run full discovery across all configured roots")
    discover.add_argument("--json", action="store_true", help="Print full discovery results JSON")
    discover.set_defaults(func=cmd_discover)
    
    discover_root_cmd = sub.add_parser("discover-root", help="Manage discovery roots")
    discover_root_sub = discover_root_cmd.add_subparsers(dest="discover_root_action", required=True)
    dr_add = discover_root_sub.add_parser("add", help="Add a discovery root")
    dr_add.add_argument("path", help="Root path to add")
    dr_add.add_argument("--name", help="Root name (defaults to directory name)")
    dr_add.add_argument("--max-depth", type=int, default=4, help="Maximum scan depth")
    dr_add.add_argument("--exclusions", nargs="*", help="Additional exclusion patterns")
    dr_add.set_defaults(func=cmd_discover_root)
    dr_remove = discover_root_sub.add_parser("remove", help="Remove a discovery root")
    dr_remove.add_argument("path", help="Root path to remove")
    dr_remove.set_defaults(func=cmd_discover_root)
    dr_list = discover_root_sub.add_parser("list", help="List discovery roots")
    dr_list.add_argument("--json", action="store_true", help="Print roots JSON")
    dr_list.set_defaults(func=cmd_discover_root)
    
    activate = sub.add_parser("activate", help="Automatically activate discovered projects")
    activate.add_argument("path", nargs="?", default=None, help="Specific project path to activate")
    activate.add_argument("--json", action="store_true", help="Print activation results JSON")
    activate.set_defaults(func=cmd_activate)

    host = sub.add_parser("host", help="Manage hosts")
    host_sub = host.add_subparsers(dest="host_action", required=True)
    
    host_list = host_sub.add_parser("list", help="List all hosts")
    host_list.add_argument("--json", action="store_true", help="Print full host list JSON")
    host_list.add_argument("--type", help="Filter by host type")
    host_list.add_argument("--provider", help="Filter by provider")
    host_list.set_defaults(func=cmd_host_list)
    
    host_show = host_sub.add_parser("show", help="Show host details")
    host_show.add_argument("host_id", help="Host UUID")
    host_show.add_argument("--json", action="store_true", help="Print full host JSON")
    host_show.set_defaults(func=cmd_host_show)
    
    host_status = host_sub.add_parser("status", help="Show host status and health")
    host_status.add_argument("host_id", help="Host UUID")
    host_status.add_argument("--json", action="store_true", help="Print status JSON")
    host_status.set_defaults(func=cmd_host_status)
    
    host_capabilities = host_sub.add_parser("capabilities", help="Show host capabilities")
    host_capabilities.add_argument("host_id", help="Host UUID")
    host_capabilities.add_argument("--json", action="store_true", help="Print capabilities JSON")
    host_capabilities.set_defaults(func=cmd_host_capabilities)
    
    host_register = host_sub.add_parser("register", help="Register a new host")
    host_register.add_argument("--type", required=True, choices=[t.value for t in HostType], help="Host type")
    host_register.add_argument("--provider", required=True, choices=[p.value for p in ProviderType], help="Provider type")
    host_register.add_argument("--name", required=True, help="Display name")
    host_register.add_argument("--identifier", help="Normalized identifier/root path for local hosts")
    host_register.add_argument("--json", action="store_true", help="Print result JSON")
    host_register.set_defaults(func=cmd_host_register)

    host_identity = host_sub.add_parser("identity", help="Show and verify this machine's host identity")
    host_identity.add_argument("--json", action="store_true", help="Print host identity JSON")
    host_identity.set_defaults(func=cmd_host_identity)

    host_inspect = host_sub.add_parser("inspect", help="Inspect a host (alias of show)")
    host_inspect.add_argument("host_id", help="Host UUID")
    host_inspect.add_argument("--json", action="store_true", help="Print full host JSON")
    host_inspect.set_defaults(func=cmd_host_show)

    host_connect = host_sub.add_parser("connect", help="Connect a host for this process")
    host_connect.add_argument("host_id", help="Host UUID")
    host_connect.add_argument("--root", help="Override the configured root path")
    host_connect.add_argument("--json", action="store_true")
    host_connect.set_defaults(func=cmd_host_connect)

    host_disconnect = host_sub.add_parser("disconnect", help="Disconnect live sessions for a host")
    host_disconnect.add_argument("host_id", help="Host UUID")
    host_disconnect.add_argument("--json", action="store_true")
    host_disconnect.set_defaults(func=cmd_host_disconnect)

    host_health = host_sub.add_parser("health", help="Check adapter-specific host health")
    host_health.add_argument("host_id", help="Host UUID")
    host_health.add_argument("--root", help="Override the configured root path")
    host_health.add_argument("--json", action="store_true")
    host_health.set_defaults(func=cmd_host_health)

    host_recover = host_sub.add_parser("recover", help="Recover or establish a host session")
    host_recover.add_argument("host_id", help="Host UUID")
    host_recover.add_argument("--root", help="Override the configured root path")
    host_recover.add_argument("--attempts", type=int, default=2, help="Bounded recovery attempts (1-5)")
    host_recover.add_argument("--json", action="store_true")
    host_recover.set_defaults(func=cmd_host_recover)

    host_sessions = host_sub.add_parser("sessions", help="List live host sessions in this process")
    host_sessions.add_argument("--host", dest="host_id", help="Filter by host UUID")
    host_sessions.add_argument("--json", action="store_true")
    host_sessions.set_defaults(func=cmd_host_sessions)

    host_discover = host_sub.add_parser("discover", help="Discover repositories on a provider (read-only)")
    host_discover.add_argument("--provider", required=True, choices=_git_provider_choices())
    host_discover.add_argument("--owner", help="Organization/workspace/group scope")
    host_discover.add_argument("--limit", type=int, default=100)
    host_discover.add_argument("--json", action="store_true")
    host_discover.set_defaults(func=cmd_remote_discover)

    host_workspaces = host_sub.add_parser(
        "workspaces", help="List cloud workspace instances (read-only)")
    host_workspaces.add_argument("host_id")
    host_workspaces.add_argument("--root")
    host_workspaces.add_argument("--limit", type=int, default=50)
    host_workspaces.add_argument("--json", action="store_true")
    host_workspaces.set_defaults(func=cmd_host_workspaces)

    relocate = sub.add_parser("relocate", help="Plan, apply, verify or recover a project relocation")
    relocate_sub = relocate.add_subparsers(dest="relocate_action", required=True)

    def _relocate_paths(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("source", help="Current project root")
        parser.add_argument("destination", help="New project root (must not exist)")
        parser.add_argument(
            "--operation",
            choices=("move", "copy", "clone"),
            default="move",
            help="move, copy or clone (copy/clone always retain the source)",
        )
        parser.add_argument("--json", action="store_true", help="Print JSON")

    rel_plan = relocate_sub.add_parser("plan", help="Validate and preview a relocation")
    _relocate_paths(rel_plan)
    rel_plan.set_defaults(func=cmd_relocate)

    rel_apply = relocate_sub.add_parser("apply", help="Execute a relocation plan")
    _relocate_paths(rel_apply)
    rel_apply.add_argument(
        "--delete-source",
        action="store_true",
        help="Delete the source after a verified move (requires this explicit flag)",
    )
    rel_apply.add_argument("--no-registry", action="store_true", help="Do not rebind registry workspaces")
    rel_apply.set_defaults(func=cmd_relocate)

    rel_verify = relocate_sub.add_parser("verify", help="Verify identity and content across two trees")
    rel_verify.add_argument("source")
    rel_verify.add_argument("destination")
    rel_verify.add_argument("--json", action="store_true", help="Print JSON")
    rel_verify.set_defaults(func=cmd_relocate)

    rel_recover = relocate_sub.add_parser("recover", help="Resume an interrupted relocation from its journal")
    rel_recover.add_argument("journal", help="Path to the relocation journal JSON")
    rel_recover.add_argument("--delete-source", action="store_true", help="Confirm source deletion if pending")
    rel_recover.add_argument("--no-registry", action="store_true", help="Do not rebind registry workspaces")
    rel_recover.add_argument("--json", action="store_true", help="Print JSON")
    rel_recover.set_defaults(func=cmd_relocate)

    provider = sub.add_parser("provider", help="Manage providers")
    provider_sub = provider.add_subparsers(dest="provider_action", required=True)
    
    provider_list = provider_sub.add_parser("list", help="List all providers")
    provider_list.add_argument("--json", action="store_true", help="Print full provider list JSON")
    provider_list.add_argument("--kind", choices=["git_hosting", "cloud_workspace", "cicd", "project_management", "package_registry", "cloud_infrastructure"])
    provider_list.set_defaults(func=cmd_provider_list)
    
    provider_show = provider_sub.add_parser("show", help="Show provider details")
    provider_show.add_argument("provider_id", help="Provider UUID")
    provider_show.add_argument("--json", action="store_true", help="Print provider JSON")
    provider_show.set_defaults(func=cmd_provider_show)
    
    provider_status = provider_sub.add_parser("status", help="Show provider status")
    provider_status.add_argument("provider_id", help="Provider identifier")
    provider_status.add_argument("--json", action="store_true", help="Print status JSON")
    provider_status.add_argument("--check", action="store_true", help="Perform an authenticated health check (network)")
    provider_status.set_defaults(func=cmd_provider_status)

    provider_caps = provider_sub.add_parser("capabilities", help="Show provider capabilities")
    provider_caps.add_argument("provider_id")
    provider_caps.add_argument("--json", action="store_true")
    provider_caps.set_defaults(func=cmd_providers_capabilities)

    providers = sub.add_parser("providers", help="Universal provider registry, inspection and capability matrix")
    providers_sub = providers.add_subparsers(dest="providers_action", required=True)
    providers_list = providers_sub.add_parser("list", help="List every registered provider")
    providers_list.add_argument("--json", action="store_true")
    providers_list.add_argument("--kind", choices=["git_hosting", "cloud_workspace", "cicd", "project_management", "package_registry", "cloud_infrastructure"])
    providers_list.set_defaults(func=cmd_providers_list)
    providers_inspect = providers_sub.add_parser("inspect", help="Inspect one provider and its declared capabilities")
    providers_inspect.add_argument("provider_id")
    providers_inspect.add_argument("--json", action="store_true")
    providers_inspect.add_argument("--check", action="store_true", help="Perform an authenticated health check (network)")
    providers_inspect.set_defaults(func=cmd_providers_inspect)
    providers_caps = providers_sub.add_parser("capabilities", help="Show one provider's capabilities or the whole matrix")
    providers_caps.add_argument("provider_id", nargs="?")
    providers_caps.add_argument("--json", action="store_true")
    providers_caps.add_argument("--all", action="store_true")
    providers_caps.set_defaults(func=cmd_providers_capabilities)

    remote = sub.add_parser("remote", help="Manage remote repositories")
    remote_sub = remote.add_subparsers(dest="remote_action", required=True)
    
    remote_discover = remote_sub.add_parser("discover", help="Discover remote repositories")
    remote_discover.add_argument("--provider", required=True, choices=_git_provider_choices(), help="Provider to discover from")
    remote_discover.add_argument("--owner", help="Repository owner/org")
    remote_discover.add_argument("--limit", type=int, default=100, help="Maximum results")
    remote_discover.add_argument("--json", action="store_true", help="Print results JSON")
    remote_discover.set_defaults(func=cmd_remote_discover)
    
    remote_inspect = remote_sub.add_parser("inspect", help="Inspect a remote repository")
    remote_inspect.add_argument("url", help="Repository URL")
    remote_inspect.add_argument("--provider", choices=_git_provider_choices(), help="Provider type")
    remote_inspect.add_argument("--json", action="store_true", help="Print result JSON")
    remote_inspect.set_defaults(func=cmd_remote_inspect)
    
    remote_activate = remote_sub.add_parser("activate", help="Activate a remote repository")
    remote_activate.add_argument("url", help="Repository URL")
    remote_activate.add_argument("--provider", choices=_git_provider_choices(), help="Provider type")
    remote_activate.add_argument("--json", action="store_true", help="Print result JSON")
    remote_activate.set_defaults(func=cmd_remote_activate)

    repository = sub.add_parser("repository", help="Manage repositories")
    repo_sub = repository.add_subparsers(dest="repository_action", required=True)
    
    repo_list = repo_sub.add_parser("list", help="List repositories")
    repo_list.add_argument("--project", help="Filter by project")
    repo_list.add_argument("--host", help="Filter by host")
    repo_list.add_argument("--provider", help="Filter by provider id")
    repo_list.add_argument("--json", action="store_true", help="Print JSON")
    repo_list.set_defaults(func=cmd_repository_list)
    
    repo_show = repo_sub.add_parser("show", help="Show repository details")
    repo_show.add_argument("repository_id", help="Repository UUID")
    repo_show.add_argument("--json", action="store_true", help="Print JSON")
    repo_show.set_defaults(func=cmd_repository_show)

    workspace = sub.add_parser("workspace", help="Manage workspaces")
    ws_sub = workspace.add_subparsers(dest="workspace_action", required=True)
    
    ws_list = ws_sub.add_parser("list", help="List workspaces")
    ws_list.add_argument("--project", help="Filter by project")
    ws_list.add_argument("--host", help="Filter by host")
    ws_list.add_argument("--json", action="store_true", help="Print JSON")
    ws_list.set_defaults(func=cmd_workspace_list)
    
    ws_show = ws_sub.add_parser("show", help="Show workspace details")
    ws_show.add_argument("workspace_id", help="Workspace UUID")
    ws_show.add_argument("--json", action="store_true", help="Print JSON")
    ws_show.set_defaults(func=cmd_workspace_show)

    enterprise = sub.add_parser("enterprise", help="Local tenant/organization data-model preview; not a SaaS server")
    enterprise_sub = enterprise.add_subparsers(dest="enterprise_action", required=True)
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
    enterprise_backend = enterprise_sub.add_parser(
        "backend", help="Show the configured database backend, schema version and health")
    enterprise_backend.set_defaults(func=cmd_enterprise)
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

    enterprise_sa = enterprise_sub.add_parser(
        "service-account-create", help="Create a tenant-scoped service identity")
    enterprise_sa.add_argument("--org", required=True)
    enterprise_sa.add_argument("--name", required=True)
    enterprise_sa.add_argument("--role", choices=("admin", "developer", "auditor", "viewer"),
                               required=True)
    enterprise_sa.add_argument("--scopes", required=True,
                               help="Comma-separated actions this identity may use")
    enterprise_sa.add_argument("--actor-subject", required=True)
    enterprise_sa.set_defaults(func=cmd_enterprise)

    enterprise_sas = enterprise_sub.add_parser(
        "service-accounts", help="List service identities in an organization")
    enterprise_sas.add_argument("--org", required=True)
    enterprise_sas.add_argument("--actor-subject", required=True)
    enterprise_sas.set_defaults(func=cmd_enterprise)

    enterprise_token = enterprise_sub.add_parser(
        "token-issue", help="Issue a scoped bearer token (shown once)")
    enterprise_token.add_argument("--org", required=True)
    enterprise_token.add_argument("--account", required=True)
    enterprise_token.add_argument("--actor-subject", required=True)
    enterprise_token.add_argument("--ttl", type=int, default=3600)
    enterprise_token.add_argument("--json", action="store_true")
    enterprise_token.set_defaults(func=cmd_enterprise)

    enterprise_tokens = enterprise_sub.add_parser(
        "tokens", help="List token fingerprints (never the secrets)")
    enterprise_tokens.add_argument("--org", required=True)
    enterprise_tokens.add_argument("--actor-subject", required=True)
    enterprise_tokens.set_defaults(func=cmd_enterprise)

    enterprise_token_revoke = enterprise_sub.add_parser(
        "token-revoke", help="Revoke a service token immediately")
    enterprise_token_revoke.add_argument("--org", required=True)
    enterprise_token_revoke.add_argument("--token-id", required=True)
    enterprise_token_revoke.add_argument("--actor-subject", required=True)
    enterprise_token_revoke.set_defaults(func=cmd_enterprise)

    enterprise_token_rotate = enterprise_sub.add_parser(
        "token-rotate", help="Issue a replacement token and revoke the previous one")
    enterprise_token_rotate.add_argument("--org", required=True)
    enterprise_token_rotate.add_argument("--token-id", required=True)
    enterprise_token_rotate.add_argument("--actor-subject", required=True)
    enterprise_token_rotate.add_argument("--ttl", type=int, default=3600)
    enterprise_token_rotate.add_argument("--json", action="store_true")
    enterprise_token_rotate.set_defaults(func=cmd_enterprise)

    enterprise_secret_set = enterprise_sub.add_parser(
        "secret-set", help="Encrypt a tenant secret at rest (value read from stdin)")
    enterprise_secret_set.add_argument("--org", required=True)
    enterprise_secret_set.add_argument("--name", required=True)
    enterprise_secret_set.add_argument("--actor-subject", required=True)
    enterprise_secret_set.add_argument("--stdin", action="store_true", required=True,
                                       help="Read the secret value from standard input")
    enterprise_secret_set.set_defaults(func=cmd_enterprise)

    enterprise_secret_get = enterprise_sub.add_parser(
        "secret-get", help="Decrypt a tenant secret for an authorized actor")
    enterprise_secret_get.add_argument("--org", required=True)
    enterprise_secret_get.add_argument("--name", required=True)
    enterprise_secret_get.add_argument("--actor-subject", required=True)
    enterprise_secret_get.add_argument("--json", action="store_true")
    enterprise_secret_get.set_defaults(func=cmd_enterprise)

    enterprise_secrets = enterprise_sub.add_parser(
        "secrets", help="List tenant secret names and key ids (no values)")
    enterprise_secrets.add_argument("--org", required=True)
    enterprise_secrets.add_argument("--actor-subject", required=True)
    enterprise_secrets.set_defaults(func=cmd_enterprise)

    enterprise_kms_status = enterprise_sub.add_parser(
        "kms-status", help="Describe the key provider without exposing key material")
    enterprise_kms_status.add_argument("--org", required=True)
    enterprise_kms_status.add_argument("--actor-subject", required=True)
    enterprise_kms_status.set_defaults(func=cmd_enterprise)

    enterprise_kms_rotate = enterprise_sub.add_parser(
        "kms-rotate", help="Rotate the encryption key and re-encrypt tenant secrets")
    enterprise_kms_rotate.add_argument("--org", required=True)
    enterprise_kms_rotate.add_argument("--actor-subject", required=True)
    enterprise_kms_rotate.set_defaults(func=cmd_enterprise)

    enterprise_backup = enterprise_sub.add_parser(
        "backup", help="Create a consistent, verified database backup (ENT-10)")
    enterprise_backup.add_argument(
        "--destination", help="Backup directory (default: OGX_HOME/enterprise/backups)")
    enterprise_backup.add_argument(
        "--encrypt", action="store_true",
        help="Seal the snapshot with the local KMS keyring")
    enterprise_backup.add_argument("--label", help="Optional operator label")
    enterprise_backup.add_argument("--sign", action="store_true",
                                   help="Sign the manifest with the local signing key")
    enterprise_backup.set_defaults(func=cmd_enterprise)

    enterprise_backups = enterprise_sub.add_parser(
        "backups", help="List available backups (no plaintext is read)")
    enterprise_backups.add_argument("--destination")
    enterprise_backups.set_defaults(func=cmd_enterprise)

    enterprise_backup_verify = enterprise_sub.add_parser(
        "backup-verify", help="Re-hash a backup and prove it restores cleanly")
    enterprise_backup_verify.add_argument("--backup-path", required=True)
    enterprise_backup_verify.add_argument(
        "--check-signature", action="store_true",
        help="Verify manifest authenticity with the local signing key")
    enterprise_backup_verify.add_argument(
        "--require-signature", action="store_true",
        help="Fail when the manifest is unsigned")
    enterprise_backup_verify.set_defaults(func=cmd_enterprise)

    enterprise_restore = enterprise_sub.add_parser(
        "restore", help="Restore a verified backup into a database path (atomic)")
    enterprise_restore.add_argument("--backup-path", required=True)
    enterprise_restore.add_argument("--target", required=True)
    enterprise_restore.add_argument("--overwrite", action="store_true",
                                    help="Replace an existing database (kept as .pre-restore)")
    enterprise_restore.add_argument("--check-signature", action="store_true",
                                    help="Verify manifest authenticity before restoring")
    enterprise_restore.add_argument("--require-signature", action="store_true",
                                    help="Refuse an unsigned backup")
    enterprise_restore.set_defaults(func=cmd_enterprise)

    enterprise_recover = enterprise_sub.add_parser(
        "restore-recover", help="Roll back or clean up an interrupted restore")
    enterprise_recover.add_argument("--target", required=True)
    enterprise_recover.set_defaults(func=cmd_enterprise)

    enterprise_signing = enterprise_sub.add_parser(
        "signing-keys", help="Describe backup manifest signing keys (no secrets)")
    enterprise_signing.set_defaults(func=cmd_enterprise)

    enterprise_signing_rotate = enterprise_sub.add_parser(
        "signing-key-rotate", help="Rotate the active backup signing key")
    enterprise_signing_rotate.set_defaults(func=cmd_enterprise)

    enterprise_backup_prune = enterprise_sub.add_parser(
        "backup-prune", help="Delete old backups, keeping the newest N")
    enterprise_backup_prune.add_argument("--destination")
    enterprise_backup_prune.add_argument("--keep", type=int, default=5)
    enterprise_backup_prune.set_defaults(func=cmd_enterprise)

    authz = sub.add_parser("authz", help="Show and evaluate the shared authorization policy")
    authz_sub = authz.add_subparsers(dest="authz_action", required=True)
    authz_matrix = authz_sub.add_parser("matrix", help="Print the role/action permission matrix")
    authz_matrix.add_argument("--json", action="store_true")
    authz_matrix.set_defaults(func=cmd_authz)
    for authz_name, authz_help in (
        ("check", "Decide one action for a role or scoped service principal"),
        ("explain", "Explain a decision with the effective permission set"),
    ):
        authz_item = authz_sub.add_parser(authz_name, help=authz_help)
        authz_item.add_argument("--action", required=True)
        authz_item.add_argument("--roles", required=True, help="Comma-separated roles")
        authz_item.add_argument("--scopes",
                                help="Comma-separated scopes; makes the principal a service identity")
        authz_item.add_argument("--tenant", help="Tenant (organization id) binding")
        authz_item.add_argument("--resource-kind")
        authz_item.add_argument("--resource-id")
        authz_item.add_argument("--subject", help="Principal subject for the decision record")
        authz_item.add_argument("--reason", help="Required for break_glass")
        authz_item.add_argument("--json", action="store_true")
        authz_item.set_defaults(func=cmd_authz)

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

    patch_parser = sub.add_parser("patch", help="Plan, apply and roll back exact-text source patches")
    patch_sub = patch_parser.add_subparsers(dest="patch_action", required=True)
    patch_plan = patch_sub.add_parser("plan", help="Validate a JSON change set and store a patch plan")
    patch_plan.add_argument("path", help="Project root directory")
    patch_plan.add_argument("--spec", required=True, help="JSON file containing a 'changes' array")
    patch_plan.add_argument("--project-id", help="Optional project identity UUID recorded in the plan")
    patch_plan.add_argument("--actor-subject", help="Actor identity; defaults to OGX_ACTOR_SUBJECT")
    patch_plan.add_argument("--json", action="store_true")
    patch_plan.set_defaults(func=cmd_patch)
    patch_apply = patch_sub.add_parser("apply", help="Apply a planned patch after re-verifying every file hash")
    patch_apply.add_argument("plan_id")
    patch_apply.add_argument("--confirm", action="store_true", help="Explicitly approve the write")
    patch_apply.add_argument("--actor-subject", help="Actor identity; defaults to OGX_ACTOR_SUBJECT")
    patch_apply.add_argument("--json", action="store_true")
    patch_apply.set_defaults(func=cmd_patch)
    patch_rollback = patch_sub.add_parser("rollback", help="Restore originals from the external backup")
    patch_rollback.add_argument("plan_id")
    patch_rollback.add_argument("--confirm", action="store_true", help="Explicitly approve the rollback")
    patch_rollback.add_argument("--actor-subject", help="Actor identity; defaults to OGX_ACTOR_SUBJECT")
    patch_rollback.add_argument("--json", action="store_true")
    patch_rollback.set_defaults(func=cmd_patch)
    patch_show = patch_sub.add_parser("show", help="Print a stored patch plan")
    patch_show.add_argument("plan_id")
    patch_show.add_argument("--json", action="store_true")
    patch_show.set_defaults(func=cmd_patch)
    patch_list = patch_sub.add_parser("list", help="List stored patch plans")
    patch_list.add_argument("--limit", type=int, default=50)
    patch_list.add_argument("--json", action="store_true")
    patch_list.set_defaults(func=cmd_patch)

    workflow = sub.add_parser("workflow", help="Durable inspect/plan/edit/test/secure/review/evolve workflows")
    workflow_sub = workflow.add_subparsers(dest="workflow_action", required=True)
    wf_create = workflow_sub.add_parser("create", help="Create a durable workflow and its stage records")
    wf_create.add_argument("path", help="Project root directory")
    wf_create.add_argument("--project-id")
    wf_create.add_argument("--workflow-id", help="Explicit UUID; reusing it is idempotent")
    wf_create.add_argument("--idempotency-key", help="Reuse returns the existing workflow")
    wf_create.add_argument("--skip", action="append", default=[], metavar="STAGE",
                           help="Record a stage as intentionally skipped (repeatable)")
    wf_create.add_argument("--patch-plan", help="Patch plan id used by the edit stage")
    wf_create.add_argument("--changed-file", help="File used for change-impact analysis")
    wf_create.add_argument("--sandbox-image", help="Pinned sandbox image for the test stage")
    wf_create.add_argument("--test-command", nargs="+", metavar="ARG",
                           help="Sandboxed test command as an argument array")
    wf_create.add_argument("--actor-subject")
    wf_create.add_argument("--json", action="store_true")
    wf_create.set_defaults(func=cmd_workflow)
    wf_run = workflow_sub.add_parser("run", help="Resume execution until done, failed or awaiting approval")
    wf_run.add_argument("workflow_id")
    wf_run.add_argument("--approve", action="append", default=[], metavar="STAGE",
                        help="Record approval for a gated stage before running (repeatable)")
    wf_run.add_argument("--actor", help="Approver identity; defaults to OGX_ACTOR_SUBJECT")
    wf_run.add_argument("--max-stages", type=int, help="Execute at most this many stages in this call")
    wf_run.add_argument(
        "--lease-seconds",
        type=float,
        default=3600.0,
        help="Lease held by this run; an expired lease allows crash recovery (default 3600)",
    )
    wf_run.add_argument("--json", action="store_true")
    wf_run.set_defaults(func=cmd_workflow)
    wf_status = workflow_sub.add_parser("status", help="Show workflow, stage and event state")
    wf_status.add_argument("workflow_id")
    wf_status.add_argument("--events", type=int, default=20)
    wf_status.add_argument("--json", action="store_true")
    wf_status.set_defaults(func=cmd_workflow)
    wf_list = workflow_sub.add_parser("list", help="List workflows")
    wf_list.add_argument("--limit", type=int, default=50)
    wf_list.add_argument("--json", action="store_true")
    wf_list.set_defaults(func=cmd_workflow)
    wf_events = workflow_sub.add_parser("events", help="Show the workflow event history")
    wf_events.add_argument("workflow_id")
    wf_events.add_argument("--limit", type=int, default=100)
    wf_events.add_argument("--json", action="store_true")
    wf_events.set_defaults(func=cmd_workflow)
    wf_approve = workflow_sub.add_parser("approve", help="Record an approval for a gated stage")
    wf_approve.add_argument("workflow_id")
    wf_approve.add_argument("--stage", required=True)
    wf_approve.add_argument("--actor", required=True)
    wf_approve.add_argument("--note")
    wf_approve.add_argument("--json", action="store_true")
    wf_approve.set_defaults(func=cmd_workflow)
    wf_cancel = workflow_sub.add_parser("cancel", help="Cancel a workflow and its unfinished stages")
    wf_cancel.add_argument("workflow_id")
    wf_cancel.add_argument("--actor", required=True)
    wf_cancel.add_argument("--reason")
    wf_cancel.add_argument("--json", action="store_true")
    wf_cancel.set_defaults(func=cmd_workflow)
    wf_retry = workflow_sub.add_parser("retry", help="Reset a failed stage for another attempt")
    wf_retry.add_argument("workflow_id")
    wf_retry.add_argument("--stage")
    wf_retry.add_argument("--json", action="store_true")
    wf_retry.set_defaults(func=cmd_workflow)

    serve_parser = sub.add_parser(
        "serve", help="Run the authenticated REST API and dashboard (loopback only)")
    serve_parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--rate-limit", type=int, default=120)
    serve_parser.set_defaults(func=cmd_serve)

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

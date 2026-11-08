from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .architecture import analyze_architecture
from .compatibility import compatibility_report
from .config import ScanConfig, load_config, state_dir
from .detector import detect_project
from .inventory import build_inventory
from .scanner import scan_repository
from .connectors.git import detect_provider, remote_summary


GIT_TIMEOUT = 45
ENV_IGNORE_BLOCK = (
    "# OMEGA GUARDIAN X: keep local environment files out of version control\n"
    ".env\n.env.*\n!.env.example\n!.env.sample\n"
)


def _git(root: Path, args: list[str], *, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a fixed Git argument vector; never invokes a shell or project scripts."""
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("Git executable was not found on PATH.")
    return subprocess.run(
        [executable, *args], cwd=str(root), text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout, check=False, shell=False,
    )


def _project_root(root: Path) -> Path:
    resolved = root.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"Project path is not a directory: {resolved}")
    return resolved


def _project_signals(root: Path) -> dict[str, Any]:
    """Detect operational readiness signals without executing project files."""
    test_markers = ("tests", "test", "spec", "__tests__")
    has_tests = any((root / name).is_dir() for name in test_markers)
    if not has_tests:
        try:
            inspected = 0
            for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
                dirs[:] = [d for d in dirs if d not in {
                    ".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target", "coverage"
                } and not (Path(current) / d).is_symlink()]
                inspected += len(files)
                if any(
                    name.startswith(("test_", "tests_")) or name.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts"))
                    for name in files
                ):
                    has_tests = True
                    break
                if inspected >= 12000:
                    break
        except OSError:
            pass

    ci_candidates = [
        root / ".github" / "workflows", root / ".gitlab-ci.yml", root / "bitbucket-pipelines.yml",
        root / ".circleci" / "config.yml", root / "azure-pipelines.yml", root / "Jenkinsfile",
    ]
    ci_files: list[str] = []
    for candidate in ci_candidates:
        if candidate.is_dir():
            ci_files.extend(p.relative_to(root).as_posix() for p in candidate.glob("*.yml") if p.is_file())
            ci_files.extend(p.relative_to(root).as_posix() for p in candidate.glob("*.yaml") if p.is_file())
        elif candidate.is_file() and not candidate.is_symlink():
            ci_files.append(candidate.relative_to(root).as_posix())

    license_files = [name for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING") if (root / name).is_file()]
    dependency_automation = {
        "dependabot": (root / ".github" / "dependabot.yml").is_file() or (root / ".github" / "dependabot.yaml").is_file(),
        "renovate": any((root / name).is_file() for name in ("renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json")),
    }
    git_state = inspect_git_state(root)
    return {
        "tests_detected": has_tests,
        "ci_files": sorted(set(ci_files)),
        "ci_detected": bool(ci_files),
        "license_files": license_files,
        "dependency_automation": dependency_automation,
        "git": git_state,
    }


def inspect_git_state(root: Path) -> dict[str, Any]:
    """Return local repository metadata with remote credentials removed."""
    if not shutil.which("git"):
        return {"is_repository": False, "branch": None, "head": None, "dirty": False, "status": [], "remotes": [],
                "availability": "git-not-installed"}
    check = _git(root, ["rev-parse", "--is-inside-work-tree"])
    if check.returncode != 0 or check.stdout.strip() != "true":
        return {"is_repository": False, "branch": None, "head": None, "dirty": False, "status": [], "remotes": []}
    branch_result = _git(root, ["branch", "--show-current"])
    head_result = _git(root, ["rev-parse", "HEAD"])
    status_result = _git(root, ["status", "--short"])
    remote_result = _git(root, ["remote"])
    remotes: list[dict[str, str]] = []
    if remote_result.returncode == 0:
        for name in remote_result.stdout.splitlines():
            name = name.strip()
            if not name:
                continue
            url_result = _git(root, ["remote", "get-url", name])
            if url_result.returncode == 0:
                summary = remote_summary(url_result.stdout.strip())
                safe_url = summary["url"].split("?", 1)[0].split("#", 1)[0]
                remotes.append({"name": name, "provider": detect_provider(safe_url), "url": safe_url})
    status = status_result.stdout.splitlines() if status_result.returncode == 0 else []
    return {
        "is_repository": True,
        "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else "",
        "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "dirty": bool(status),
        "status": status[:200],
        "remotes": remotes,
    }


def _recommendations(scan: Any, signals: dict[str, Any], profile: Any) -> list[dict[str, str]]:
    priorities: list[dict[str, str]] = []
    summary = scan.summary
    if summary["critical"] or summary["high"]:
        priorities.append({"priority": "P0", "area": "security-and-correctness", "action": "Triage all critical/high findings; reproduce and add regression tests before patching."})
    if summary["medium"]:
        priorities.append({"priority": "P1", "area": "quality", "action": "Review medium findings and resolve confirmed issues in a dedicated branch."})
    if not signals["tests_detected"]:
        priorities.append({"priority": "P1", "area": "testing", "action": "Add a minimal automated test suite and at least one regression test for each confirmed defect."})
    if not signals["ci_detected"]:
        priorities.append({"priority": "P1", "area": "continuous-integration", "action": "Configure CI on the repository's host to run lint, tests, builds and security checks."})
    automation = signals["dependency_automation"]
    if not automation["dependabot"] and not automation["renovate"]:
        priorities.append({"priority": "P1", "area": "dependency-upgrades", "action": "Enable Renovate for GitHub/GitLab/Bitbucket/Gitea-compatible deployments or Dependabot on GitHub; require reviewed PRs."})
    if profile.manifests and not profile.lockfiles:
        priorities.append({"priority": "P2", "area": "reproducible-builds", "action": "Commit the package manager's supported lockfile where the ecosystem recommends one."})
    if not signals["license_files"]:
        priorities.append({"priority": "P2", "area": "project-governance", "action": "Choose and add a project license after reviewing ownership and distribution requirements."})
    if signals["git"].get("is_repository") and signals["git"].get("dirty"):
        priorities.append({"priority": "P2", "area": "change-control", "action": "Review the existing uncommitted changes before maintenance or repair; OGX will not discard them."})
    if not signals["git"].get("is_repository"):
        priorities.append({"priority": "P2", "area": "version-control", "action": "Initialize or connect a Git repository if history, rollback and hosted automation are required."})
    if not priorities:
        priorities.append({"priority": "P3", "area": "continuous-improvement", "action": "No enabled high-priority gaps were detected; maintain scheduled scans and reviewed dependency updates."})
    return priorities


def build_guardian_audit(root: Path, config: ScanConfig | None = None) -> dict[str, Any]:
    """Build one multi-signal maintenance assessment without executing project code."""
    root = _project_root(root)
    config = config or load_config()
    profile = detect_project(root)
    scan = scan_repository(root, config)
    inventory = build_inventory(root, config)
    architecture = analyze_architecture(root, config)
    compatibility = compatibility_report(root)
    signals = _project_signals(root)
    recommendations = _recommendations(scan, signals, profile)
    is_ogx = (root / "pyproject.toml").is_file() and (root / "src" / "ogx").is_dir()
    return {
        "tool": "OMEGA GUARDIAN X",
        "engine_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {"name": profile.name, "root": str(root), "languages": profile.languages,
                    "ecosystems": profile.ecosystems, "frameworks": profile.frameworks,
                    "manifests": profile.manifests, "lockfiles": profile.lockfiles},
        "health": {"files_examined": scan.files_examined, "files_skipped": scan.files_skipped,
                   "inventory_files": inventory["file_count"], "inventory_bytes": inventory["total_bytes"],
                   "source_nodes": len(architecture["nodes"]), "import_edges": len(architecture["edges"]),
                   "finding_counts": scan.summary},
        "quality_gates": {
            "static_scan": "completed",
            "tests": "detected_not_executed" if signals["tests_detected"] else "not_detected",
            "ci": "detected_not_executed" if signals["ci_detected"] else "not_detected",
            "dependency_updates": "configured" if any(signals["dependency_automation"].values()) else "not_configured",
            "git_history": "available" if signals["git"]["is_repository"] else "not_available",
            "runtime_build": "not_executed",
            "security_certification": "not_provided",
        },
        "signals": signals,
        "compatibility": compatibility,
        "findings": [
            {"rule_id": f.rule_id, "title": f.title, "severity": f.severity, "category": f.category,
             "message": f.message, "path": f.path, "line": f.line, "confidence": f.confidence,
             "recommendation": f.recommendation}
            for f in scan.findings
        ],
        "recommendations": recommendations,
        "capability_limits": [
            "Universal means generic repository inventory plus best-effort adapters; it does not mean complete semantic analysis of every language/framework.",
            "The scan is static and heuristic. It does not prove exploitability or root cause.",
            "Tests, builds, deployments and project-defined scripts were not executed.",
            "Dependency versions are not upgraded automatically by this audit; Renovate/Dependabot should submit reviewable PRs.",
        ],
        "self_evolution": {
            "is_ogx_repository": is_ogx,
            "mode": "self-audit-and-proposal" if is_ogx else "external-project-guardian",
            "automatic_engine_source_rewrite": False,
            "next_cycle": ["run test suite", "review findings", "implement scoped change", "run regression checks", "review diff", "release through signed/tagged workflow"],
        },
    }


def render_guardian_markdown(report: dict[str, Any]) -> str:
    project = report["project"]
    health = report["health"]
    lines = [
        f"# OMEGA GUARDIAN X — Guardian Report: {project['name']}", "",
        f"- **Root:** `{project['root']}`",
        f"- **Engine:** `{report['engine_version']}`",
        f"- **Languages:** {', '.join(project['languages']) or 'Unknown'}",
        f"- **Files scanned:** {health['files_examined']} (skipped: {health['files_skipped']})",
        f"- **Source graph:** {health['source_nodes']} files / {health['import_edges']} static import edges", "",
        "## Findings", "", "| Severity | Count |", "|---|---:|",
    ]
    lines.extend(f"| {key.title()} | {value} |" for key, value in health["finding_counts"].items())
    lines += ["", "## Quality gates", "", "| Gate | Result |", "|---|---|"]
    lines.extend(f"| {key.replace('_', ' ').title()} | {value} |" for key, value in report["quality_gates"].items())
    lines += ["", "## Recommended maintenance", "", "| Priority | Area | Action |", "|---|---|---|"]
    for item in report["recommendations"]:
        action = item["action"].replace("|", "\\|")
        lines.append(f"| {item['priority']} | {item['area']} | {action} |")
    lines += ["", "## Capability boundaries", ""]
    lines.extend(f"- {item}" for item in report["capability_limits"])
    lines += ["", "> No target source code was executed or changed during this audit.", ""]
    return "\n".join(lines)


def _backup_root(root: Path) -> Path:
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
    base = state_dir()
    if base.is_symlink():
        raise OSError(f"OGX state directory must not be a symlink: {base}")
    # A user override must never place backups inside the project being repaired.
    if base.resolve(strict=False).is_relative_to(root.resolve()):
        raise ValueError("OGX_HOME must resolve outside the target project before applying repairs.")
    backup_root = base / "backups" / digest
    for item in (base, base / "backups", backup_root):
        if item.is_symlink():
            raise OSError(f"OGX backup path must not be a symlink: {item}")
        item.mkdir(parents=True, exist_ok=True)
    return backup_root


def _tracked_env_files(root: Path) -> list[str]:
    result = _git(root, ["ls-files"])
    if result.returncode != 0:
        return []
    tracked = []
    for line in result.stdout.splitlines():
        basename = Path(line).name
        if basename == ".env" or basename.startswith(".env."):
            tracked.append(line)
    return tracked


def _find_environment_files(root: Path) -> list[str]:
    found: list[str] = []
    excluded = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "vendor", "dist", "build", "target", "coverage"}
    visited = 0
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in excluded and not (base / d).is_symlink()]
        for name in files:
            visited += 1
            if name == ".env" or name.startswith(".env."):
                candidate = base / name
                if candidate.is_file() and not candidate.is_symlink():
                    found.append(candidate.relative_to(root).as_posix())
            if visited >= 12000:
                return found
    return found


def plan_safe_fixes(root: Path) -> list[dict[str, str]]:
    """List only deterministic, low-risk fixes currently supported by the engine."""
    root = _project_root(root)
    env_files = _find_environment_files(root)
    if not env_files:
        return []
    ignore = root / ".gitignore"
    if ignore.is_symlink():
        return [{"id": "OGX-FIX-BLOCKED", "path": ".gitignore", "status": "blocked",
                 "reason": "Refusing to follow a symlinked .gitignore."}]
    if ignore.exists() and not ignore.is_file():
        return [{"id": "OGX-FIX-BLOCKED", "path": ".gitignore", "status": "blocked",
                 "reason": ".gitignore exists but is not a regular file."}]
    if _tracked_env_files(root):
        return [{"id": "OGX-FIX-BLOCKED", "path": ".gitignore", "status": "blocked",
                 "reason": "At least one environment file is already tracked by Git; ignoring it will not remove the secret from history."}]
    try:
        existing = ignore.read_bytes().decode("utf-8") if ignore.exists() else ""
    except UnicodeDecodeError:
        return [{"id": "OGX-FIX-BLOCKED", "path": ".gitignore", "status": "blocked",
                 "reason": ".gitignore is not valid UTF-8; refusing a lossy rewrite."}]
    patterns = {line.strip().lstrip("/") for line in existing.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "!"))}
    missing = [pattern for pattern in (".env", ".env.*") if pattern not in patterns]
    if not missing:
        return []
    return [{"id": "OGX-FIX-ENV-IGNORE", "path": ".gitignore", "status": "safe-fix-available",
             "reason": "Add ignore patterns for local environment files; existing file content will be preserved and backed up."}]


def apply_safe_fixes(root: Path, *, confirm: bool = False) -> dict[str, Any]:
    """Apply narrowly-scoped deterministic fixes with external backup and atomic replace."""
    root = _project_root(root)
    plans = plan_safe_fixes(root)
    if not confirm:
        return {"applied": [], "blocked": [x for x in plans if x["status"] == "blocked"],
                "planned": plans, "message": "Preview only. Pass --confirm to apply supported fixes."}
    if any(x["status"] == "blocked" for x in plans):
        return {"applied": [], "blocked": plans, "planned": [],
                "message": "No changes were made because a safety precondition failed."}
    if not plans:
        return {"applied": [], "blocked": [], "planned": [], "message": "No supported safe fixes are currently needed."}

    target = root / ".gitignore"
    if target.is_symlink():
        raise ValueError("Refusing to modify a symlinked .gitignore.")
    before = target.read_bytes() if target.exists() else None
    current_plan = plan_safe_fixes(root)
    if not current_plan or any(x["status"] == "blocked" for x in current_plan):
        raise RuntimeError("Project state changed while planning the fix; rerun the preview.")
    backup_dir = _backup_root(root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir.mkdir(mode=0o700)
    if before is None:
        (backup_dir / "ORIGINAL_FILE_DID_NOT_EXIST.txt").write_text(".gitignore did not exist before this fix.\n", encoding="utf-8")
        text = ""
    else:
        (backup_dir / "gitignore.before").write_bytes(before)
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(".gitignore is not valid UTF-8; refusing a lossy rewrite.") from exc
    addition = ENV_IGNORE_BLOCK
    if text and not text.endswith(("\n", "\r")):
        text += "\n"
    if ".env" in {line.strip() for line in text.splitlines()}:
        addition = addition.replace(".env\n", "")
    if ".env.*" in {line.strip() for line in text.splitlines()}:
        addition = addition.replace(".env.*\n", "")
    payload = (text + ("\n" if text and addition and not text.endswith("\n\n") else "") + addition).encode("utf-8")
    if before is not None and target.read_bytes() != before:
        raise RuntimeError(".gitignore changed after the backup snapshot; no change was applied.")
    temp_name = None
    try:
        if before is None:
            with target.open("xb") as stream:
                stream.write(payload)
        else:
            fd, temp_name = tempfile.mkstemp(prefix=".ogx-ignore-", suffix=".tmp", dir=root)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            try:
                os.chmod(temp_name, target.stat().st_mode & 0o777)
            except OSError:
                pass
            os.replace(temp_name, target)
            temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return {"applied": [{"id": "OGX-FIX-ENV-IGNORE", "path": ".gitignore", "backup": str(backup_dir),
                         "sha256": hashlib.sha256(payload).hexdigest()}],
            "blocked": [], "planned": [], "message": "Supported safe fix applied. Re-scan and review Git diff."}


def check_upstream(root: Path, *, online: bool = False) -> dict[str, Any]:
    """Inspect configured Git remotes; network access is opt-in and read-only."""
    root = _project_root(root)
    state = inspect_git_state(root)
    if not state["is_repository"]:
        return {"root": str(root), "is_repository": False, "online_checked": online, "remotes": [],
                "message": "This directory is not a Git worktree; remote upgrades cannot be inspected."}
    output: list[dict[str, Any]] = []
    for remote in state["remotes"]:
        item: dict[str, Any] = {"name": remote["name"], "provider": remote["provider"], "url": remote["url"],
                                "status": "not_checked", "local_head": state["head"], "remote_head": None}
        if online:
            branch = state["branch"]
            if branch:
                result = _git(root, ["ls-remote", "--heads", "--", remote["name"], f"refs/heads/{branch}"], timeout=60)
            else:
                result = _git(root, ["ls-remote", "--symref", "--", remote["name"], "HEAD"], timeout=60)
            if result.returncode != 0:
                item["status"] = "unreachable_or_unauthorized"
                item["detail"] = "Remote could not be inspected. Check network access and Git credential-helper configuration."
            else:
                remote_head = None
                for line in result.stdout.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{40,64}", parts[0]):
                        remote_head = parts[0].lower()
                        break
                item["remote_head"] = remote_head
                if remote_head is None:
                    item["status"] = "branch_not_found_or_empty"
                elif remote_head == state["head"]:
                    item["status"] = "up_to_date"
                else:
                    item["status"] = "remote_differs_review_required"
                    item["detail"] = "Remote branch and local HEAD differ; fetch and inspect ancestry before merging."
        output.append(item)
    return {"root": str(root), "is_repository": True, "branch": state["branch"], "dirty": state["dirty"],
            "online_checked": online, "remotes": output,
            "safety": "No branch, working tree, dependency manifest or project source was changed."}


def fetch_remote(root: Path, remote: str = "origin", *, confirm: bool = False) -> dict[str, Any]:
    """Fetch refs only; never merge, rebase, reset, checkout or modify working files."""
    root = _project_root(root)
    if not confirm:
        raise PermissionError("Fetching updates contacts a remote and writes Git metadata; pass --confirm after reviewing the remote.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", remote):
        raise ValueError("Invalid Git remote name.")
    state = inspect_git_state(root)
    if not state["is_repository"]:
        raise ValueError("Target directory is not a Git worktree.")
    if remote not in {item["name"] for item in state["remotes"]}:
        raise ValueError(f"Git remote '{remote}' is not configured.")
    before = _git(root, ["status", "--porcelain"])
    if before.returncode != 0:
        raise RuntimeError("Could not verify the working-tree state before fetch.")
    result = _git(root, ["fetch", "--no-tags", "--no-recurse-submodules", "--", remote], timeout=300)
    after = _git(root, ["status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("Git fetch failed. Check remote availability and credential configuration; no merge was attempted.")
    unchanged = before.stdout == after.stdout
    return {"remote": remote, "fetched": True, "working_tree_status_unchanged": unchanged,
            "working_tree_before": before.stdout.splitlines(), "working_tree_after": after.stdout.splitlines(),
            "message": "Remote-tracking refs fetched only. No merge, rebase, checkout, dependency install or project-code execution."}


def build_evolution_report(root: Path, config: ScanConfig | None = None) -> dict[str, Any]:
    """Generate a self-improvement/evolution proposal from an evidence-backed audit."""
    audit = build_guardian_audit(root, config)
    audit["evolution"] = {
        "cycle": "observe -> prioritize -> implement -> verify -> review -> release",
        "proposal_count": len(audit["recommendations"]),
        "proposals": audit["recommendations"],
        "change_policy": "No autonomous source rewrite. Apply each proposal in a reviewed branch with regression tests and a rollback point.",
        "self_update_policy": "The OGX engine may audit its own repository, but it must not overwrite its own source or update itself in-place.",
    }
    return audit

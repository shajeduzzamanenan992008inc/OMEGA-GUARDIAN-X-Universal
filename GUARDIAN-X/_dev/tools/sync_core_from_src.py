#!/usr/bin/env python3
"""
Deterministic synchronization of core/ogx from src/ogx.

This script generates the drop-in distribution (core/ogx) from the
canonical source (src/ogx) using a deterministic, repeatable process.

Usage:
    python _dev/tools/sync_core_from_src.py [--dry-run] [--version VERSION]

The script:
1. Reads version from single source of truth (VERSION file or src/ogx/__init__.py)
2. Copies src/ogx to core/ogx with transformations for drop-in distribution
3. Updates version in core/ogx/__init__.py to match canonical version
4. Removes modules not needed for drop-in distribution
5. Updates imports to use relative/absolute as appropriate for drop-in

This script is the SINGLE AUTHORITATIVE SYNC PROCESS.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import List, Set

# The script is at GUARDIAN-X/_dev/tools/sync_core_from_src.py
# So __file__.resolve().parents[2] = GUARDIAN-X directory (the repo root)
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_OGX = REPO_ROOT / "src" / "ogx"
CORE_OGX = REPO_ROOT / "core" / "ogx"
VERSION_FILE = REPO_ROOT / "VERSION"

# Directories to EXCLUDE from core/ogx (not needed for drop-in distribution)
EXCLUDED_DIRS: Set[str] = {
    "integration",        # New integration engine - not in drop-in
    "intelligence",       # Intelligence engine - not in drop-in
    "pap",                # Project Activation Protocol - not in drop-in
    "hosts",              # Host abstraction - not in drop-in
    "registry",           # Registry V2 - not in drop-in
    "platform",           # Provider engine - not in drop-in
    "hosts",              # Hosts - not in drop-in
    "skillpacks",         # Skill packs - not in drop-in (core has them in ecosystem)
}

# Files to EXCLUDE from core/ogx root (not needed for drop-in distribution)
EXCLUDED_ROOT_FILES: Set[str] = {
    "cli.py",             # CLI - not in drop-in (uses init.py instead)
    "architecture.py",    # Architecture - not in drop-in
    "compatibility.py",   # Compatibility - not in drop-in
    "config.py",          # Config - not in drop-in
    "detector.py",        # Detector - not in drop-in
    "diagnostics.py",     # Diagnostics - not in drop-in
    "discovery.py",       # Discovery - not in drop-in
    "findings.py",        # Findings - not in drop-in
    "guardian.py",        # Guardian - not in drop-in
    "impact.py",          # Impact - not in drop-in
    "integration.py",     # Integration - not in drop-in
    "inventory.py",       # Inventory - not in drop-in
    "osv.py",             # OSV audit - not in drop-in
    "pipeline.py",        # Pipeline - not in drop-in
    "policy.py",          # Policy - not in drop-in
    "reports.py",         # Reports - not in drop-in
    "rules.py",           # Rules - not in drop-in
    "sandbox.py",         # Sandbox - not in drop-in
    "scanner.py",         # Scanner - not in drop-in
    "toolchains.py",      # Toolchains - not in drop-in
    "triage.py",          # Triage - not in drop-in
    "workspace.py",       # Workspace - not in drop-in
}

# Directories to KEEP in core/ogx (needed for drop-in distribution)
# These are the EXISTING directories in core/ogx that should be PRESERVED
KEPT_DIRS: Set[str] = {
    "kernel",         # Core kernel (paths, config, findings, models, reports, redact, envfile)
    "analysis",       # Analysis engines (detector, engines, inventory, architecture, etc.)
    "shield",         # Shield (scanner, guardian, osv, licenses, sandbox, policy, sbom)
    "devkit",         # Devkit (diagnostics, triage, agents, runbooks)
    "governance",     # Governance (approvals, framework, mailer, store)
    "ecosystem",      # Ecosystem (agents, skills, integration, skillpacks)
    "runtime",        # Runtime (config, request, access, ratelimit, rules, engine, asgi, wsgi, proxy, observe)
    "connectors",     # Connectors (git, providers)
    "adapters",       # Adapters
    "engines",        # Engines (analysis engines)
    "skillpacks",     # Skill packs (for ecosystem)
    "skills",         # Skills (for ecosystem)
    "agents",         # Agents (for ecosystem)
    "enterprise",     # Enterprise (for governance)
}

# Files to KEEP in core/ogx root (needed for drop-in distribution)
KEPT_ROOT_FILES: Set[str] = {
    "__init__.py",
    "__main__.py",
    "architecture.py",    # Architecture - needed for analysis
    "compatibility.py",   # Compatibility - needed for analysis
    "config.py",          # Config - needed for kernel
    "detector.py",        # Detector - needed for analysis
    "diagnostics.py",     # Diagnostics - needed for devkit
    "discovery.py",       # Discovery - needed for integration
    "envfile.py",         # Envfile - needed for kernel
    "findings.py",        # Findings - needed for kernel
    "guardian.py",        # Guardian - needed for shield
    "impact.py",          # Impact - needed for analysis
    "integration.py",     # Integration - needed for ecosystem
    "inventory.py",       # Inventory - needed for analysis
    "licenses.py",        # Licenses - needed for shield
    "models.py",          # Models - needed for kernel
    "osv.py",             # OSV - needed for shield
    "pipeline.py",        # Pipeline - needed for analysis
    "policy.py",          # Policy - needed for shield
    "reports.py",         # Reports - needed for kernel
    "rules.py",           # Rules - needed for kernel
    "sandbox.py",         # Sandbox - needed for shield
    "sbom.py",            # SBOM - needed for shield
    "scanner.py",         # Scanner - needed for shield
    "toolchains.py",      # Toolchains - needed for analysis
    "triage.py",          # Triage - needed for devkit
    "workspace.py",       # Workspace - needed for devkit
    "osv.py",             # OSV - needed for shield
    "rules.py",           # Rules - needed for kernel
    "scanner.py",         # Scanner - needed for shield
    "sandbox.py",         # Sandbox - needed for shield
    "policy.py",          # Policy - needed for shield
    "reports.py",         # Reports - needed for kernel
    "licenses.py",        # Licenses - needed for shield
    "sbom.py",            # SBOM - needed for shield
    "triage.py",          # Triage - needed for devkit
    "diagnostics.py",     # Diagnostics - needed for devkit
    "impact.py",          # Impact - needed for analysis
    "workspace.py",       # Workspace - needed for devkit
}

# Directory mapping: ONLY map directories that EXIST in src/ogx
# The universal platform components that should be COPIED from src/ogx to core/ogx
PLATFORM_DIR_MAPPING = {
    "platform": "core/ogx/platform",      # Provider engine
    "hosts": "core/ogx/hosts",            # Host abstraction
    "registry": "core/ogx/registry",      # Registry V2
    "integration": "core/ogx/integration", # Integration engine
    "intelligence": "core/ogx/intelligence", # Intelligence engine
    "pap": "core/ogx/pap",                # Project Activation Protocol
    "hosts": "core/ogx/hosts",            # Hosts
    "skillpacks": "core/ogx/skillpacks",  # Skill packs
    "skills": "core/ogx/skills",          # Skills
    "agents": "core/ogx/agents",          # Agents
    "enterprise": "core/ogx/enterprise",  # Enterprise
    "engines": "core/ogx/engines",        # Engines
}

# Files to copy as-is from src root to core root
ROOT_FILES = {
    "__init__.py": "core/ogx/__init__.py",
    "__main__.py": "core/ogx/__main__.py",
}

# Files to copy as-is from src root to core root
ROOT_FILES = {
    "__init__.py": "core/ogx/__init__.py",
    "__main__.py": "core/ogx/__main__.py",
}

def read_version() -> str:
    """Read version from single source of truth."""
    if VERSION_FILE.exists():
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
        if re.match(r"^\d+\.\d+\.\d+(?:[a-z]+\d+)?$", text):
            return text
    # Fallback to src/ogx/__init__.py
    init_file = SRC_OGX / "__init__.py"
    if init_file.exists():
        content = init_file.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    return "0.0.0"

def write_version(version: str) -> None:
    """Write version to single source of truth."""
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    # Also update src/ogx/__init__.py
    init_file = SRC_OGX / "__init__.py"
    content = init_file.read_text(encoding="utf-8")
    content = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"',
        content
    )
    init_file.write_text(content, encoding="utf-8")

def copy_module(src: Path, dst: Path, dry_run: bool = False) -> None:
    """Copy a module directory with transformations."""
    if dry_run:
        print(f"[DRY RUN] Copy {src} -> {dst}")
        return
    
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"Copied {src} -> {dst}")

def transform_init_file(file_path: Path, version: str, dry_run: bool = False) -> None:
    """Transform __init__.py for drop-in distribution."""
    if not file_path.exists():
        return
    
    # Update version to read from VERSION file (drop-in style)
    new_content = f'''"""OMEGA GUARDIAN X (OGX): universal, local-first repository intelligence, protection and governance toolkit."""
from __future__ import annotations

import re
from pathlib import Path

_FALLBACK_VERSION = "{version}"  # kept equal to <shield>/VERSION by a unit test
_VERSION_RE = re.compile(r"^\\d+\\.\\d+\\.\\d+(?:[a-z]+\\d+)?$")


def _read_version() -> str:
    """Single source of truth is <shield>/VERSION (core/ogx/__init__.py -> parents[2]).

    A wheel install has no VERSION file next to it, so the constant above is the fallback.
    """
    try:
        text = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return _FALLBACK_VERSION
    return text if _VERSION_RE.fullmatch(text) else _FALLBACK_VERSION


__version__ = _read_version()
'''
    
    if dry_run:
        print(f"[DRY RUN] Would update {file_path} with version {version}")
        return
    
    file_path.write_text(new_content, encoding="utf-8")
    print(f"Updated {file_path} with version {version}")

def copy_root_files(version: str, dry_run: bool = False) -> None:
    """Copy root files with transformations."""
    for src_name, dst_name in ROOT_FILES.items():
        src = SRC_OGX / src_name
        dst = CORE_OGX / dst_name
        
        if not src.exists():
            print(f"Warning: Source not found: {src}")
            continue
        
        if dry_run:
            print(f"[DRY RUN] Copy {src_name} -> {dst_name}")
            continue
        
        if src_name == "__init__.py":
            # Transform __init__.py
            transform_init_file(dst, version, dry_run)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"Copied {src_name} -> {dst_name}")

def copy_dirs(dry_run: bool = False) -> None:
    """Copy directories from src to core."""
    # First, copy platform-specific directories that exist in src/ogx
    for src_name, dst_name in PLATFORM_DIR_MAPPING.items():
        src = SRC_OGX / src_name
        dst = CORE_OGX / dst_name
        
        if not src.exists():
            print(f"Warning: Source not found: {src}")
            continue
        
        if dry_run:
            print(f"[DRY RUN] Copy dir {src_name} -> {dst_name}")
            continue
        
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print(f"Copied dir {src_name} -> {dst_name}")
    
    # Then, copy any other directories that should be kept
    for src_name in KEPT_DIRS:
        if src_name in PLATFORM_DIR_MAPPING:
            continue  # Already handled
        
        src = SRC_OGX / src_name
        dst = CORE_OGX / src_name
        
        if not src.exists():
            # Not in src, skip
            continue
        
        if dry_run:
            print(f"[DRY RUN] Copy dir {src_name} -> {src_name}")
            continue
        
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print(f"Copied dir {src_name} -> {src_name}")

def copy_root_files(version: str, dry_run: bool = False) -> None:
    """Copy root files with transformations."""
    for src_name, dst_name in ROOT_FILES.items():
        src = SRC_OGX / src_name
        dst = CORE_OGX / dst_name
        
        if not src.exists():
            print(f"Warning: Source not found: {src}")
            continue
        
        if dry_run:
            print(f"[DRY RUN] Copy {src_name} -> {dst_name}")
            continue
        
        if src_name == "__init__.py":
            # Transform __init__.py
            transform_init_file(dst, version, dry_run)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"Copied {src_name} -> {dst_name}")

def copy_root_other_files(dry_run: bool = False) -> None:
    """Copy other root files (non-ROOT_FILES) that are in KEPT_ROOT_FILES."""
    for src_file in SRC_OGX.iterdir():
        if not src_file.is_file():
            continue
        if src_file.name in ROOT_FILES:
            continue  # Already handled
        if src_file.name not in KEPT_ROOT_FILES:
            if dry_run:
                print(f"[DRY RUN] Skip excluded root file: {src_file.name}")
            continue
        
        dst = CORE_OGX / src_file.name
        if dry_run:
            print(f"[DRY RUN] Copy root file {src_file.name} -> {src_file.name}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst)
        print(f"Copied root file {src_file.name}")

def remove_excluded(dry_run: bool = False) -> None:
    """Remove excluded directories and files from core/ogx."""
    for dir_name in EXCLUDED_DIRS:
        module_path = CORE_OGX / dir_name
        if module_path.exists():
            if dry_run:
                print(f"[DRY RUN] Remove dir {module_path}")
            else:
                shutil.rmtree(module_path)
                print(f"Removed dir {module_path}")
    
    for file_name in EXCLUDED_ROOT_FILES:
        file_path = CORE_OGX / file_name
        if file_path.exists():
            if dry_run:
                print(f"[DRY RUN] Remove file {file_path}")
            else:
                file_path.unlink()
                print(f"Removed file {file_path}")

def verify_core_structure() -> List[str]:
    """Verify core/ogx has expected structure."""
    issues = []
    
    # Check required directories exist
    for dir_name in KEPT_DIRS:
        dir_path = CORE_OGX / dir_name
        if not dir_path.exists():
            issues.append(f"Missing required directory: {dir_name}")
    
    # Check excluded directories don't exist
    for dir_name in EXCLUDED_DIRS:
        dir_path = CORE_OGX / dir_name
        if dir_path.exists():
            issues.append(f"Excluded directory still present: {dir_name}")
    
    # Check excluded root files don't exist
    for file_name in EXCLUDED_ROOT_FILES:
        file_path = CORE_OGX / file_name
        if file_path.exists():
            issues.append(f"Excluded root file still present: {file_name}")
    
    # Check kept root files exist
    for file_name in KEPT_ROOT_FILES:
        file_path = CORE_OGX / file_name
        if not file_path.exists():
            issues.append(f"Missing kept root file: {file_name}")
    
    # Check __init__.py exists
    init_file = CORE_OGX / "__init__.py"
    if not init_file.exists():
        issues.append("Missing core/ogx/__init__.py")
    
    # Check __main__.py exists
    main_file = CORE_OGX / "__main__.py"
    if not main_file.exists():
        issues.append("Missing core/ogx/__main__.py")
    
    return issues

def main() -> int:
    parser = argparse.ArgumentParser(description="Sync core/ogx from src/ogx")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--version", help="Explicit version to use (default: read from VERSION file or src/ogx/__init__.py)")
    args = parser.parse_args()
    
    # Read version
    if args.version:
        version = args.version
    else:
        version = read_version()
    
    print(f"=== Syncing core/ogx from src/ogx ===")
    print(f"Version: {version}")
    print(f"Dry run: {args.dry_run}")
    print()
    
    if args.dry_run:
        print("=== DRY RUN - No changes will be made ===")
        print()
    
    # Read current version
    current_version = read_version()
    print(f"Current version: {current_version}")
    print(f"Target version: {version}")
    print()
    
    # Update version if different
    if version != current_version:
        print(f"Updating version from {current_version} to {version}")
        write_version(version)
        print("Version updated in VERSION file and src/ogx/__init__.py")
        print()
    
    # Copy directories
    copy_dirs(args.dry_run)
    
    # Copy root files (ROOT_FILES)
    copy_root_files(version, args.dry_run)
    
    # Copy other root files
    copy_root_other_files(args.dry_run)
    
    # Remove excluded
    remove_excluded(args.dry_run)
    
    if not args.dry_run:
        # Verify structure
        issues = verify_core_structure()
        if issues:
            print("WARNING: Structure verification issues:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("✓ Core structure verified successfully")
    
    print()
    print("=== Sync complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
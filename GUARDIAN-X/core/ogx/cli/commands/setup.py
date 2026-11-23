"""Setup commands: global state, private env file and the project registry."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ogx.kernel.config import state_dir
from ogx.kernel.envfile import (
    EnvFileError,
    configured_settings,
    create_env_file,
    file_permission_warning,
    resolve_env_path,
    validate_runtime_config,
)
from ogx.cli.common import GLOBAL_CONFIG, _load_projects, _root, _save_projects


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



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
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

    projects = sub.add_parser("projects", help="Manage project shortcuts in OGX global storage")
    project_sub = projects.add_subparsers(dest="projects_command", required=True)
    add = project_sub.add_parser("add", help="Register a repository path without modifying it")
    add.add_argument("path")
    add.add_argument("--name")
    add.set_defaults(func=cmd_projects_add)
    listing = project_sub.add_parser("list", help="List registered repositories")
    listing.set_defaults(func=cmd_projects_list)

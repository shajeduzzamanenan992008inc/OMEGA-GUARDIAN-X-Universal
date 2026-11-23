"""Ecosystem commands: agent discovery, skill packs and host integration (link)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ogx.ecosystem.agents import discover_agents
from ogx.ecosystem.integration import (
    apply_integration,
    integration_status,
    plan_integration,
    remove_integration,
)
from ogx.ecosystem.skills import list_skills, validate_skill
from ogx.cli.common import (
    _json_print,
    _print_approval_queued,
    _queue_email_approval,
    _requires_email_approval,
    _root,
)
from ogx.kernel.paths import LINK_GROUPS, parse_link_groups


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


def cmd_integrate(args: argparse.Namespace) -> int:
    try:
        groups = parse_link_groups(getattr(args, "with_groups", None))
        if args.integration_action == "plan":
            result = plan_integration(Path(args.path), groups)
            print(f"Shield link plan: {result['project']}\nTarget: {result['root']}")
            print("Selected groups: " + (", ".join(result["groups"]) or "none (nothing is linked by default)"))
            for item in result["files"]:
                if item["status"] != "not-selected":
                    print(f"  {item['status']:18} {item['path']}")
            if not result["groups"]:
                print("\nAvailable groups (choose with --with; comma-separated or repeated; 'all' selects every group):")
                for name, description in result["available_groups"].items():
                    count = sum(1 for entry in result["files"] if entry["group"] == name)
                    print(f"  {name:8} {count:2} file(s)  {description}")
            print("\nNo files were changed. Run `ogx integrate apply PATH --with GROUP --confirm` after reviewing the plan.")
        elif args.integration_action == "apply":
            if not args.confirm:
                print("Refusing to write files without --confirm. Review `ogx integrate plan PATH --with GROUP` first.", file=sys.stderr)
                return 2
            if not groups:
                print("Select at least one link group with --with (" + ", ".join(LINK_GROUPS) + ", all). Nothing is linked by default.", file=sys.stderr)
                return 2
            if _requires_email_approval():
                result = _queue_email_approval("integration.apply", _root(args.path),
                    "Create only missing OGX-managed link files (" + ", ".join(groups) + ") in the selected project root.",
                    requester_subject=getattr(args, "requester_subject", None), groups=groups)
                _print_approval_queued(result)
                return 0
            result = apply_integration(Path(args.path), groups)
            print(f"Shield link applied: {result['root']} (groups: {', '.join(result['groups'])})")
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
                    "Remove only unchanged OGX-managed link files; preserve modified files.",
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
        print(f"Link failed: {exc}", file=sys.stderr)
        return 2


def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
    integrate = sub.add_parser("integrate", help="Opt-in host link: connect the shield to AI agents, editors and dependency bots (nothing is linked by default)")
    integrate_sub = integrate.add_subparsers(dest="integration_action", required=True)
    for action, help_text in (("plan", "Preview a link without writing; lists the available groups"),
                              ("apply", "Create missing OGX-managed files for the selected groups"),
                              ("status", "Check OGX-managed link files"),
                              ("remove", "Remove only unchanged OGX-managed link files")):
        item = integrate_sub.add_parser(action, help=help_text)
        item.add_argument("path", help="Target project root (must contain the shield folder)")
        if action in {"plan", "apply"}:
            item.add_argument("--with", dest="with_groups", nargs="+", metavar="GROUP",
                              help="Link groups: " + ", ".join(LINK_GROUPS) + ", all")
        if action in {"apply", "remove"}:
            item.add_argument("--confirm", action="store_true", help="Queue an email approval before file creation/removal")
            item.add_argument("--requester-subject", help="External identity; defaults to OGX_ACTOR_SUBJECT")
        item.set_defaults(func=cmd_integrate)

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

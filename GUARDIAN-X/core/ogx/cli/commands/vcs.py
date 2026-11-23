"""Git and hosting-platform commands (read-only by default; writes need approval)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ogx.connectors.git import _run as git_run, clone_repository, inspect_remote, inspect_repository
from ogx.connectors.providers import ProviderClient
from ogx.cli.common import (
    _json_print,
    _print_approval_queued,
    _queue_action_approval,
    _requires_email_approval,
    _root,
)


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



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
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

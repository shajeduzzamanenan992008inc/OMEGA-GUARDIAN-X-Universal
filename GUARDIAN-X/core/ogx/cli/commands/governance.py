"""Access-control commands: enterprise data model and email-gated approvals."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ogx.connectors.git import _run as git_run, clone_repository, inspect_repository
from ogx.connectors.providers import ProviderClient
from ogx.ecosystem.integration import apply_integration, remove_integration
from ogx.governance.approvals import ApprovalManager
from ogx.governance.framework import framework_document
from ogx.governance.store import EnterpriseStore
from ogx.shield.guardian import apply_safe_fixes, fetch_remote
from ogx.cli.common import _json_print, _root


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
                    result = apply_integration(root, groups=str(payload.get("groups", "")).split(","))
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



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
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

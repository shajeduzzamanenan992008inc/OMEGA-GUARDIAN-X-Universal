"""CLI handlers for ``ogx providers``, ``ogx platform``, ``ogx host`` and friends.

Kept out of ``ogx.cli`` so the universal engine stays independent of argparse
and so the command surface is unit-testable without the full CLI module.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .capabilities import Capability, CapabilityState, ProviderKind
from .errors import CapabilityUnavailableError, ProviderError
from .identity import link_repository_to_project
from .operations import execute_pull_request, plan_pull_request
from .registry import UniversalProviderRegistry, get_provider_registry


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _fail(exc: Exception) -> int:
    if isinstance(exc, ProviderError):
        print(f"Provider operation failed: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"  Detail: {exc.detail}", file=sys.stderr)
    else:
        print(f"Provider operation failed: {exc}", file=sys.stderr)
    return 2


def _registry() -> UniversalProviderRegistry:
    return get_provider_registry()


def _auth_state(registry: UniversalProviderRegistry, provider_id: str) -> tuple[bool, str | None]:
    try:
        adapter = registry.get(provider_id, environ=dict(os.environ))
    except Exception:  # noqa: BLE001 - listing must not fail on a bad endpoint
        return False, None
    return adapter.credential is not None, adapter.credential_source


# ---------------------------------------------------------------- providers
def cmd_provider_list(args) -> int:
    try:
        registry = _registry()
        rows = registry.snapshot(environ=dict(os.environ))
        kind = getattr(args, "kind", None)
        if kind:
            target = ProviderKind(kind)
            rows = [row for row in rows if row["kind"] == target.value]
        if getattr(args, "json", False):
            _print_json({"providers": rows, "count": len(rows)})
            return 0
        if not rows:
            print("No providers registered.")
        width = max((len(row["provider_id"]) for row in rows), default=0)
        for row in rows:
            mark = "auth" if row.get("credential_present") else "----"
            print(
                f"  {row['provider_id']:<{width}}  {row['kind']:<22} "
                f"{row['implementation_status']:<15} [{mark}] {row['display_name']}"
            )
        return 0
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def cmd_provider_show(args) -> int:
    """Inspect one provider by id (``ogx provider show`` / ``ogx providers inspect``)."""
    return cmd_providers_inspect(args)


def cmd_provider_status(args) -> int:
    return cmd_providers_inspect(args)


def cmd_providers_inspect(args) -> int:
    try:
        registry = _registry()
        provider_id = args.provider_id
        descriptor = registry.descriptor(provider_id)
        authenticated, source = _auth_state(registry, provider_id)
        payload = descriptor.to_dict(authenticated=authenticated)
        payload["credential_present"] = authenticated
        payload["credential_source"] = source
        payload["credential_env"] = list(descriptor.credential_env)
        if getattr(args, "check", False):
            try:
                adapter = registry.get(provider_id, environ=dict(os.environ))
                payload["health"] = adapter.health().to_dict()
            except ProviderError as exc:
                payload["health"] = {"status": "unavailable", "detail": exc.message}
        if getattr(args, "json", False):
            _print_json(payload)
            return 0
        print(f"Provider: {payload['display_name']} ({payload['provider_id']})")
        print(f"  Kind: {payload['kind']}")
        print(f"  Version: {payload['version']}  API: {payload['api_version']}")
        print(f"  Implementation: {payload['implementation_status']}")
        print(f"  API base: {payload['api_base'] if 'api_base' in payload else payload['default_api_base']}")
        print(f"  Self-hosted: {'yes' if payload['self_hosted'] else 'no'}")
        print(f"  Credential: {source or 'not configured'} ({', '.join(payload['credential_env']) or 'none'})")
        print(f"  Supported ({len(payload['supported_operations'])}): "
              f"{', '.join(payload['supported_operations']) or 'none'}")
        print(f"  Partial: {', '.join(payload['partial_operations']) or 'none'}")
        print(f"  Unsupported: {', '.join(payload['unsupported_operations']) or 'none'}")
        if payload["limitations"]:
            print("  Limitations:")
            for item in payload["limitations"]:
                print(f"    - {item}")
        if "health" in payload:
            print(f"  Health: {payload['health'].get('status')}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def cmd_providers_capabilities(args) -> int:
    try:
        registry = _registry()
        if getattr(args, "all", False) or not getattr(args, "provider_id", None):
            _print_json(registry.matrix(authenticated=False))
            return 0
        provider_id = args.provider_id
        descriptor = registry.descriptor(provider_id)
        authenticated, _ = _auth_state(registry, provider_id)
        states = {
            capability.value: descriptor.capability_state(
                capability, authenticated=authenticated
            ).value
            for capability in descriptor.capabilities
        }
        if getattr(args, "json", False):
            _print_json({"provider_id": provider_id, "capabilities": states})
            return 0
        print(f"Capabilities for {provider_id}:")
        for name, state in sorted(states.items()):
            marker = "OK " if state == CapabilityState.SUPPORTED.value else (
                "~  " if state == CapabilityState.PARTIAL.value else "x  "
            )
            print(f"  {marker}{name}: {state}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def cmd_providers_list(args) -> int:
    return cmd_provider_list(args)


# ------------------------------------------------------------------ remotes
def _provider_id(args) -> str:
    if getattr(args, "provider", None):
        return str(args.provider)
    from ..connectors.git import detect_provider

    detected = detect_provider(args.url)
    return {"gitea-or-forgejo": "gitea", "generic-git": "github"}.get(detected, detected)


def cmd_remote_discover(args) -> int:
    try:
        registry = _registry()
        provider_id = args.provider
        adapter = registry.get(provider_id, environ=dict(os.environ))
        repositories = adapter.discover_repositories(owner=getattr(args, "owner", None), limit=args.limit)
        rows = [repo.to_dict() for repo in repositories]
        if getattr(args, "json", False):
            _print_json({"provider_id": adapter.provider_id, "repositories": rows, "count": len(rows)})
            return 0
        print(f"Discovered {len(rows)} {adapter.provider_id} repositories:")
        for row in rows:
            print(f"  {row['full_name'] or row['name']}  [{row['visibility']}]  {row['html_url'] or ''}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def cmd_remote_inspect(args) -> int:
    try:
        registry = _registry()
        provider_id = _provider_id(args)
        adapter = registry.get(provider_id, environ=dict(os.environ))
        repo = adapter.get_repository(args.url)
        payload = repo.to_dict()
        if getattr(args, "json", False):
            _print_json(payload)
            return 0
        print(f"Repository: {payload['full_name'] or payload['name']}  [{payload['provider']}]")
        print(f"  URL: {payload['html_url'] or ''}")
        print(f"  Default branch: {payload['default_branch'] or 'unknown'}")
        print(f"  Visibility: {payload['visibility']}")
        print(f"  Description: {payload['description'] or 'none'}")
        print(f"  Stars/Forks/Issues: {payload['stars']}/{payload['forks']}/{payload['open_issues']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def cmd_remote_activate(args) -> int:
    """Link a remote repository to a project without changing project identity."""
    try:
        from ..registry import get_registry

        registry_v2 = get_registry()
        provider_registry = _registry()
        provider_id = _provider_id(args)
        adapter = provider_registry.get(provider_id, environ=dict(os.environ))
        repo = adapter.get_repository(args.url)

        canonical = repo.full_name or repo.name or args.url
        project = registry_v2.find_project_by_identity(canonical_name=canonical)
        if project is None:
            from uuid import uuid4

            project = registry_v2.register_project(
                project_id=uuid4(),
                canonical_name=canonical,
                description=repo.description,
                identity_hash=f"remote:{provider_id}:{repo.repository_id}",
            )
        repository = link_repository_to_project(
            project.project_id,
            provider_id=provider_id,
            repository_url=repo.html_url or args.url,
            registry=registry_v2,
            repository_name=repo.name,
            default_branch=repo.default_branch,
            visibility=repo.visibility,
            metadata={"remote_repository_id": repo.repository_id},
        )
        payload = {
            "project_id": str(project.project_id),
            "repository_id": str(repository.repository_id),
            "provider_id": provider_id,
            "repository_url": repository.repository_url,
            "status": "activated",
        }
        if getattr(args, "json", False):
            _print_json(payload)
        else:
            print(f"Activated {canonical}:")
            print(f"  Project: {project.project_id}")
            print(f"  Repository: {repository.repository_id} [{provider_id}]")
            print("  Project identity was not modified.")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


# ----------------------------------------------------------------- platform
def cmd_platform_engine(args) -> int:
    try:
        registry = _registry()
        action = args.platform_action
        if action == "status":
            rows = registry.snapshot(environ=dict(os.environ))
            payload = {
                "provider_count": len(rows),
                "authenticated": sum(1 for row in rows if row.get("credential_present")),
                "verified": sum(1 for row in rows if row["implementation_status"] == "verified"),
                "descriptor_only": sum(
                    1 for row in rows if row["implementation_status"] == "descriptor_only"
                ),
                "providers": rows,
            }
            if getattr(args, "json", False):
                _print_json(payload)
                return 0
            print("OMEGA GUARDIAN X platform status")
            print(f"  Providers: {payload['provider_count']} "
                  f"(verified {payload['verified']}, descriptor-only {payload['descriptor_only']})")
            print(f"  Credentials present: {payload['authenticated']}")
            for row in rows:
                print(f"    {row['provider_id']:<18} {row['kind']:<22} {row['implementation_status']}")
            print("  Writes: approval-gated; destructive operations are not implemented.")
            return 0

        if action == "capabilities":
            _print_json(registry.matrix(authenticated=False))
            return 0

        if action == "repo":
            adapter = registry.get(args.provider, environ=dict(os.environ))
            _print_json(adapter.get_repository(args.url).to_dict())
            return 0

        if action == "pr":
            if not args.confirm:
                raise PermissionError("Creating a Pull/Merge Request requires explicit --confirm.")
            adapter = registry.get(args.provider, environ=dict(os.environ))
            plan = plan_pull_request(
                adapter,
                args.url,
                title=args.title,
                body=args.body,
                head=args.head,
                base=args.base,
            )
            from ..cli import _queue_action_approval, _requires_email_approval

            if _requires_email_approval():
                queued = _queue_action_approval(
                    "platform.pr",
                    {
                        "provider": adapter.provider_id,
                        "url": args.url,
                        "title": plan.payload["title"],
                        "body": plan.payload["body"],
                        "head": plan.payload["head"],
                        "base": plan.payload["base"],
                    },
                    plan.summary,
                    getattr(args, "requester_subject", None),
                )
                _print_json(queued)
                return 0
            result = execute_pull_request(
                plan,
                adapter,
                approved=True,
                actor_subject=getattr(args, "requester_subject", None),
            )
            _print_json(result.to_dict())
            return 0

        raise ValueError(f"Unsupported platform action: {action}")
    except ProviderError as exc:
        return _fail(exc)
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Platform operation failed: {exc}", file=sys.stderr)
        return 2

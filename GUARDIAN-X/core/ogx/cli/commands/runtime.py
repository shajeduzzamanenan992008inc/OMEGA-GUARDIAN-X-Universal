"""Runtime commands (new in the shield redesign): request guard and observability.

`ogx guard check|test|keygen|proxy` and `ogx observe logs|errors|snapshot`.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from ogx.runtime import observe as obs
from ogx.runtime.access import hash_key
from ogx.runtime.config import default_config_path, load_runtime_config
from ogx.runtime.engine import Shield
from ogx.runtime.proxy import run_proxy
from ogx.runtime.request import Request

_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")


def _header(value: str) -> tuple[str, str]:
    name, sep, content = value.partition(":")
    if not sep or not name.strip():
        raise ValueError(f"Header must look like 'Name: value': {value!r}")
    return name.strip(), content.strip()


def cmd_guard(args: argparse.Namespace) -> int:
    try:
        if args.guard_action == "keygen":
            if not _NAME.match(args.name) or not _NAME.match(args.role):
                raise ValueError("name and role may only use letters, digits, '.', '_' and '-' (max 64).")
            key = secrets.token_urlsafe(32)
            digest = hash_key(key)
            print(f"API key (shown once, store it in your secret manager):\n  {key}\n")
            print("Add this to guard/runtime/runtime.toml (only the hash is stored):\n"
                  f'[[access.keys]]\nname = "{args.name}"\nrole = "{args.role}"\nsha256 = "{digest}"\n')
            print(f"...or export it as an environment variable:\n  OMEGA_GUARD_KEYS={args.name}:{args.role}:{digest}")
            return 0
        config = load_runtime_config(args.config)
        if args.guard_action == "check":
            source = args.config or default_config_path()
            print(f"Runtime config: {source if source else 'built-in secure defaults (no runtime.toml found)'}")
            print(json.dumps(config.summary(), indent=2))
            warnings = config.warnings()
            for note in warnings:
                print(f"WARNING: {note}")
            return 1 if (warnings and args.strict) else 0
        if args.guard_action == "test":
            target = args.path
            path, _, query = target.partition("?")
            headers = [_header(h) for h in args.header]
            if not any(k.lower() == "host" for k, _ in headers):
                headers.insert(0, ("Host", "localhost"))
            body = Path(args.body_file).read_bytes() if args.body_file else (args.body or "").encode("utf-8")
            request = Request(args.method.upper(), path or "/", query, headers, body, args.ip, "http", len(body) or None)
            decision = Shield(config, sink=lambda d, r: None).inspect(request)
            print(json.dumps(decision.as_dict(), indent=2))
            return 0 if decision.allowed else 1
        if args.guard_action == "proxy":
            obs.configure(console=not args.quiet)
            return run_proxy(Shield(config), args.upstream, args.listen, timeout=args.timeout, preserve_host=args.preserve_host)
    except (ValueError, OSError) as exc:
        print(f"Guard failed: {exc}", file=sys.stderr)
        return 2
    return 2


def cmd_observe(args: argparse.Namespace) -> int:
    try:
        if args.observe_action == "logs":
            events = obs.read_events(args.file, args.lines, args.level)
            if not events:
                print(f"No log events found in {args.file or obs.default_log_path()}.")
                return 0
            for event in events:
                if args.json:
                    print(json.dumps(event, ensure_ascii=False))
                    continue
                extra = f" {json.dumps(event['fields'], ensure_ascii=False)}" if event.get("fields") else ""
                print(f"{event.get('ts', '')} {event.get('level', ''):<7} {event.get('logger', '')}: {event.get('message', '')}{extra}")
            return 0
        if args.observe_action == "snapshot":
            print(json.dumps(obs.snapshot(), indent=2, ensure_ascii=False))
            return 0
        if args.observe_action == "errors":
            tracker = obs.ErrorTracker(args.db)
            if args.error_action == "list":
                rows = tracker.groups(args.limit)
                if not rows:
                    print("No tracked errors.")
                for row in rows:
                    print(f"{row['fingerprint']}  x{row['count']:<4} last {_stamp(row['last_seen'])}  {row['type']}: {row['message'][:100]}")
                return 0
            if args.error_action == "show":
                detail = tracker.group(args.fingerprint)
                if detail is None:
                    print("Unknown fingerprint.", file=sys.stderr)
                    return 1
                detail["first_seen"], detail["last_seen"] = _stamp(detail["first_seen"]), _stamp(detail["last_seen"])
                for item in detail["recent"]:
                    item["ts"] = _stamp(item["ts"])
                print(json.dumps(detail, indent=2, ensure_ascii=False))
                return 0
            if args.error_action == "clear":
                if not args.confirm:
                    print("Refusing to delete tracked errors without --confirm.", file=sys.stderr)
                    return 2
                print(f"Cleared {tracker.clear()} error group(s).")
                return 0
    except (ValueError, OSError) as exc:
        print(f"Observe failed: {exc}", file=sys.stderr)
        return 2
    return 2


def register(sub: argparse._SubParsersAction) -> None:
    """Attach `guard` and `observe` to the root parser."""
    guard = sub.add_parser("guard", help="Runtime request guard: firewall, validation, rate limit, access control (new)")
    gsub = guard.add_subparsers(dest="guard_action", required=True)
    check = gsub.add_parser("check", help="Validate runtime.toml and print the effective settings")
    check.add_argument("--config", help="Path to runtime.toml (default: <shield>/guard/runtime/runtime.toml or built-ins)")
    check.add_argument("--strict", action="store_true", help="Exit 1 when the configuration has warnings")
    test = gsub.add_parser("test", help="Run a synthetic request through the guard (exit 0 = allowed, 1 = blocked)")
    test.add_argument("path", help="Request target, e.g. '/search?q=1' (quote it in your shell)")
    test.add_argument("--config")
    test.add_argument("--method", default="GET")
    test.add_argument("--header", action="append", default=[], metavar="'Name: value'")
    test.add_argument("--body")
    test.add_argument("--body-file")
    test.add_argument("--ip", default="203.0.113.9")
    keygen = gsub.add_parser("keygen", help="Create an API key; only its SHA-256 goes into the configuration")
    keygen.add_argument("--name", required=True)
    keygen.add_argument("--role", default="user")
    proxy = gsub.add_parser("proxy", help="EXPERIMENTAL reverse proxy that guards an application written in any language")
    proxy.add_argument("--upstream", required=True, help="e.g. http://127.0.0.1:3000")
    proxy.add_argument("--listen", default="127.0.0.1:8080", help="HOST:PORT (default 127.0.0.1:8080)")
    proxy.add_argument("--config")
    proxy.add_argument("--timeout", type=float, default=30.0)
    proxy.add_argument("--preserve-host", action="store_true", help="Forward the client's Host header instead of the upstream's")
    proxy.add_argument("--quiet", action="store_true", help="No console events (file log only)")
    for action in gsub.choices.values():
        action.set_defaults(func=cmd_guard)

    observe = sub.add_parser("observe", help="Central logs, error tracking and debug snapshot (new)")
    osub = observe.add_subparsers(dest="observe_action", required=True)
    logs = osub.add_parser("logs", help="Show recent structured log events")
    logs.add_argument("--lines", type=int, default=30)
    logs.add_argument("--level", default="DEBUG")
    logs.add_argument("--file")
    logs.add_argument("--json", action="store_true")
    osub.add_parser("snapshot", help="Print a redacted runtime/debug snapshot")
    errors = osub.add_parser("errors", help="Tracked, de-duplicated application errors")
    errors.add_argument("--db", help="Error database path (default: <state>/errors.db)")
    esub = errors.add_subparsers(dest="error_action", required=True)
    elist = esub.add_parser("list")
    elist.add_argument("--limit", type=int, default=30)
    eshow = esub.add_parser("show")
    eshow.add_argument("fingerprint")
    eclear = esub.add_parser("clear")
    eclear.add_argument("--confirm", action="store_true")
    for action in osub.choices.values():
        action.set_defaults(func=cmd_observe)

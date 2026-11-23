#!/usr/bin/env python3
"""OMEGA Shield - the single entry point (stdlib only, Python 3.11+).

Drop the whole `omega-shield/` folder into the root of ANY project, then run:

    python omega-shield/init.py status

Zero-conflict contract
  * location comes from __file__, never from the current directory or PYTHONPATH
  * nothing is written inside the host unless you pass --confirm (hooks / link)
  * reports, logs and error data live OUTSIDE the host, in a per-host state folder
  * sys.path is changed only inside this process (CLI) or temporarily (embedding)

Embedding in a Python host (no permanent sys.path change):

    import runpy
    shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()
    app = shield.ShieldMiddleware(app)                    # WSGI;  shield.ShieldASGI(app) for ASGI
"""
from __future__ import annotations

import hashlib
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True                       # never write .pyc into the host tree
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

SHIELD_DIR = Path(__file__).resolve().parent         # <host>/omega-shield (found via __file__, not cwd)
CORE_DIR = SHIELD_DIR / "core"                       # vendored runtime: core/ogx
HOST_ROOT = Path(os.environ.get("OMEGA_HOST_ROOT") or SHIELD_DIR.parent).resolve()
MARKER = "omega-shield-managed-hook"
HOOKS = ("pre-commit", "pre-push")

USAGE = """usage: python omega-shield/init.py <command> [options]

protect
  scan [--strict]            read-only security scan of the host (exit 1 on high/critical with --strict)
  audit                      consolidated health + security + maintenance report
  hooks install|remove       git commit gate [--hook pre-commit|pre-push] (dry-run unless --confirm)
  guard check|test|keygen    runtime request guard: validate config, try a request, create an API key
  proxy --upstream URL       EXPERIMENTAL guard proxy in front of an app in ANY language (default 127.0.0.1:8080)
develop
  detect | doctor            detected stack / optional local toolchains
  logs | errors | snapshot   central logs, tracked errors, redacted debug snapshot
connect
  link plan|apply|status|remove [--with copilot,agents,editors,deps|all] [--confirm]   opt-in host files
other
  status                     paths, versions, gate state (read-only)
  ogx <args...>              pass-through to the full core CLI (e.g. ogx --help)
"""

ALIASES = {
    "scan": lambda rest: ["scan", str(HOST_ROOT), *rest],
    "audit": lambda rest: ["guardian", "audit", str(HOST_ROOT), *rest],
    "detect": lambda rest: ["detect", str(HOST_ROOT), *rest],
    "doctor": lambda rest: ["doctor", *rest],
    "guard": lambda rest: ["guard", *rest],
    "proxy": lambda rest: ["guard", "proxy", *rest],
    "logs": lambda rest: ["observe", "logs", *rest],
    "errors": lambda rest: ["observe", "errors", *rest],
    "snapshot": lambda rest: ["observe", "snapshot", *rest],
}


def state_home() -> Path:
    """Per-host state OUTSIDE the host tree (the core refuses an OGX_HOME inside the target)."""
    if os.environ.get("OGX_HOME"):
        return Path(os.environ["OGX_HOME"]).expanduser().absolute()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    digest = hashlib.sha256(str(HOST_ROOT).casefold().encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", HOST_ROOT.name).strip(".-") or "host"
    return base / "omega-shield" / f"{slug}-{digest}"


def _prepare_env(*, force: bool) -> Path:
    """Export the few variables the core understands. `force=False` (embedding) never overrides the host's own."""
    home = state_home()
    if home.resolve(strict=False).is_relative_to(HOST_ROOT):
        sys.exit("OGX_HOME must be outside the host project (reports must stay external).")
    home.mkdir(parents=True, exist_ok=True)
    values = {"OGX_HOME": str(home), "OMEGA_SHIELD_HOME": str(SHIELD_DIR), "OMEGA_SHIELD_DIRNAME": SHIELD_DIR.name,
              "OMEGA_HOST_ROOT": str(HOST_ROOT)}
    for key, value in values.items():
        if force and key != "OGX_HOME":
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    return home


def load_runtime():
    """Import `ogx.runtime` (guard + observability) without leaving sys.path modified. Returns the module."""
    _prepare_env(force=False)
    core = str(CORE_DIR)
    added = core not in sys.path
    if added:
        sys.path.insert(0, core)
    try:
        runtime = importlib.import_module("ogx.runtime")
        if not Path(runtime.__file__).resolve().is_relative_to(CORE_DIR):
            raise RuntimeError(f"another 'ogx' package shadows the shield runtime: {runtime.__file__}")
    finally:
        if added:
            sys.path.remove(core)
    return runtime


# ----------------------------------------------------------------------------- git hooks
def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=HOST_ROOT, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _hook_path(name: str) -> tuple[Path, str] | None:
    """(hook file, shield path relative to the git top-level); honours core.hooksPath and worktrees."""
    top, hooks = _git("rev-parse", "--show-toplevel"), _git("rev-parse", "--git-path", "hooks")
    if not top or not hooks:
        return None
    hooks_dir = Path(hooks) if Path(hooks).is_absolute() else (HOST_ROOT / hooks)
    try:
        rel = SHIELD_DIR.relative_to(Path(top).resolve()).as_posix()
    except ValueError:
        return None
    return hooks_dir.resolve() / name, rel


HOOK = """#!/bin/sh
# {marker}: installed by omega-shield. Remove: python {rel}/init.py hooks remove --hook {name} --confirm
ROOT="$(git rev-parse --show-toplevel)" || exit 1
for PY in python3 python py; do command -v "$PY" >/dev/null 2>&1 && break; done
command -v "$PY" >/dev/null 2>&1 || {{ echo "[shield] python not found - gate skipped" >&2; exit 0; }}
"$PY" "$ROOT/{rel}/init.py" scan --strict || {{ echo "[shield] {name} blocked: high/critical finding. Details: python {rel}/init.py scan" >&2; exit 1; }}
"""


def _is_ours(path: Path) -> bool:
    return path.is_file() and MARKER in path.read_text(encoding="utf-8", errors="replace")


def cmd_hooks(args: list[str]) -> int:
    action = args[0] if args else ""
    confirm = "--confirm" in args
    name = "pre-commit"
    if "--hook" in args:
        index = args.index("--hook")
        name = args[index + 1] if index + 1 < len(args) else ""
    found = _hook_path(name) if name in HOOKS else None
    if action not in {"install", "remove"} or not found:
        print("usage: hooks install|remove [--hook pre-commit|pre-push] [--confirm]   (needs a git repository containing this folder)")
        return 2
    path, rel = found
    mine = _is_ours(path)
    if action == "install":
        if path.exists() and not mine:
            print(f"REFUSED: {path} already exists and is not managed by omega-shield. Chain it manually:\n"
                  f"  python {rel}/init.py scan --strict   # add this line to your existing hook")
            return 2
        if mine:
            print(f"already installed: {path}")
            return 0
        if not confirm:
            print(f"DRY-RUN: would create {path}\nRe-run with --confirm to apply.")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as handle:      # exclusive create: never overwrites
            handle.write(HOOK.format(marker=MARKER, rel=rel, name=name))
        path.chmod(0o755)
        print(f"installed: {path}")
        return 0
    if not mine:
        print("nothing to remove (no omega-shield hook found).")
        return 0
    if not confirm:
        print(f"DRY-RUN: would delete {path}\nRe-run with --confirm to apply.")
        return 0
    path.unlink()
    print(f"removed: {path}")
    return 0


def cmd_status(home: Path) -> int:
    from ogx import __version__
    gates = []
    for name in HOOKS:
        found = _hook_path(name)
        gates.append(f"{name}={'no git repo' if not found else ('INSTALLED' if _is_ours(found[0]) else 'not installed')}")
    guard_cfg = SHIELD_DIR / "guard" / "runtime" / "runtime.toml"
    print(f"shield dir : {SHIELD_DIR}\nhost root  : {HOST_ROOT}\nstate home : {home}  (outside host)\n"
          f"core       : ogx {__version__} (python {sys.version.split()[0]})\ngates      : {', '.join(gates)}\n"
          f"guard cfg  : {guard_cfg if guard_cfg.is_file() else 'built-in defaults'}\n"
          f"link       : {'linked (see: link status)' if (SHIELD_DIR / 'link-manifest.json').is_file() else 'nothing linked'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if sys.version_info < (3, 11):
        sys.exit("OMEGA Shield needs Python 3.11+ (tomllib).")
    if not (CORE_DIR / "ogx").is_dir():
        sys.exit(f"core runtime missing: {CORE_DIR / 'ogx'}")
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    if cmd in {"help", "-h", "--help"}:
        print(USAGE)
        return 0
    home = _prepare_env(force=True)
    sys.path.insert(0, str(CORE_DIR))                # process-local only; the host app is never touched
    if cmd == "status":
        return cmd_status(home)
    if cmd == "hooks":
        return cmd_hooks(rest)
    if cmd == "link":
        if not rest:
            print("usage: link plan|apply|status|remove [--with copilot,agents,editors,deps|all] [--confirm]")
            return 2
        core_args = ["integrate", rest[0], str(HOST_ROOT), *rest[1:]]
    elif cmd in ALIASES:
        core_args = ALIASES[cmd](rest)
    elif cmd == "ogx":
        core_args = rest
    else:
        print(USAGE)
        return 2
    from ogx.cli import main as core_main
    return core_main(core_args)


if __name__ == "__main__":
    raise SystemExit(main())

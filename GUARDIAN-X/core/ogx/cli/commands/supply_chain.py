"""Supply-chain commands: SBOM, license inventory and optional OSV advisory lookup."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ogx.kernel.config import load_config
from ogx.shield.licenses import inventory_licenses
from ogx.shield.osv import audit_components
from ogx.shield.sbom import generate_sbom
from ogx.cli.common import _json_print, _prepare_report_dir, _resolve_project


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


def cmd_licenses(args: argparse.Namespace) -> int:
    try:
        result = inventory_licenses(_resolve_project(args))
        _json_print(result)
        return 0
    except (OSError, ValueError) as exc:
        print(f"License inventory failed: {exc}", file=sys.stderr); return 2


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



def register(sub: argparse._SubParsersAction) -> None:
    """Attach this group's subcommands to the root parser."""
    sbom = sub.add_parser("sbom", help="Generate a best-effort CycloneDX 1.5 inventory")
    sbom.add_argument("path", nargs="?")
    sbom.add_argument("--project")
    sbom.add_argument("--output")
    sbom.add_argument("--overwrite", action="store_true")
    sbom.set_defaults(func=cmd_sbom)

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

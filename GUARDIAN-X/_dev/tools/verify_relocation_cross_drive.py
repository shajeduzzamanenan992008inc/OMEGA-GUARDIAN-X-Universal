"""Real cross-volume relocation verification (ID-11/ID-12/REL evidence).

Creates a project on one volume and relocates it to another, using paths with
spaces, a unicode filename, an empty file and a binary blob. It exercises:

1. copy across volumes (source retained, every digest verified);
2. a mid-copy interruption with a partially written destination, resumed from
   the journal;
3. workspace registry rebinding from the old path to the new one;
4. a confirmed move that deletes the source only after verification.

Nothing outside the two unique temporary roots is touched. The JSON evidence
contains only local paths, sizes and digests, never credentials.

Usage:

    python _dev/tools/verify_relocation_cross_drive.py \
        --destination-root D:\\FTP \
        --evidence docs/evidence/relocation-cross-drive-2026-11-28.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ogx.pap import create_identity, read_identity  # noqa: E402
from ogx.registry import RegistryV2  # noqa: E402
from ogx.relocation import (  # noqa: E402
    SimulatedInterruption,
    apply_relocation,
    plan_relocation,
    recover_relocation,
    scan_tree,
    verify_relocation,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_project(root: Path, *, size: int = 1024 * 1024) -> dict[str, object]:
    root.mkdir(parents=True)
    create_identity(root, project_name="Cross Volume Project")
    files = {
        "README.md": b"# Cross Volume Project\n",
        "src/app.py": b"print('cross-volume')\n",
        "docs/read me.txt": b"spaces in the path\n",
        "unicode/\u65e5\u672c\u8a9e.txt": "\u30af\u30ed\u30b9\u30dc\u30ea\u30e5\u30fc\u30e0\n".encode("utf-8"),
        "empty.txt": b"",
    }
    for relative, payload in files.items():
        target = root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    blob = root / "data" / "blob.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    seed = hashlib.sha256(str(root).encode("utf-8")).digest()
    blob.write_bytes((seed * ((size // len(seed)) + 1))[:size])
    entries, warnings = scan_tree(root)
    return {
        "project": str(root),
        "drive": Path(root).drive,
        "file_count": len(entries),
        "bytes": sum(entry["size"] for entry in entries),
        "warnings": warnings,
        "identity": read_identity(root).project_id,
    }


def _manifest(root: Path) -> dict[str, str]:
    entries, _ = scan_tree(root)
    return {entry["path"]: entry["sha256"] for entry in entries}


def _fault_at(step: str, status: str):
    def injector(current_step: str, current_status: str) -> None:
        if current_step == step and current_status == status:
            raise SimulatedInterruption(f"interrupted at {current_step}:{current_status}")

    return injector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination-root", default="D:\\FTP")
    parser.add_argument("--source-root", default=os.environ.get("TEMP", tempfile.gettempdir()))
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the temporary trees")
    parser.add_argument("--size", type=int, default=1024 * 1024, help="binary blob bytes")
    args = parser.parse_args(argv)

    source_root = Path(args.source_root)
    destination_root = Path(args.destination_root)
    for name, path in (("source", source_root), ("destination", destination_root)):
        if not path.exists():
            print(f"{name} root does not exist: {path}")
            return 2
        if not os.access(path, os.W_OK):
            print(f"{name} root is not writable: {path}")
            return 2

    token = uuid.uuid4().hex[:10]
    base = f"ogx relocation {token}"
    source_parent = Path(tempfile.mkdtemp(prefix="ogx-reloc-src-", dir=source_root))
    destination_parent = destination_root / base
    source = source_parent / f"source project {token}"
    result: dict[str, object] = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": sys.platform,
        "python": sys.version.split()[0],
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "source_drive": Path(source).drive,
        "destination_drive": destination_root.drive,
        "paths_contain_spaces": True,
        "checks": {},
        "ok": False,
    }
    started = time.monotonic()
    registry: RegistryV2 | None = None
    try:
        if Path(source).drive.lower() == destination_root.drive.lower():
            print("source and destination are on the same volume; cross-volume evidence requires two")
            return 2
        result["project"] = _make_project(source, size=args.size)
        manifest = _manifest(source)
        identity = read_identity(source)

        # ---------------------------------------------------------- 1. copy
        copy_destination = destination_parent / f"copied project {token}"
        copy_plan = plan_relocation(source, copy_destination, operation="copy")
        copy_result = apply_relocation(copy_plan, journal_dir=destination_parent / "journal")
        copy_report = verify_relocation(source, copy_destination)
        copy_manifest = _manifest(copy_destination)
        result["checks"]["copy"] = {
            "state": copy_result["state"],
            "source_retained": source.exists(),
            "source_deleted": copy_result["source_deleted"],
            "files": copy_report["destination_files"],
            "bytes": copy_result["copy_report"]["bytes"],
            "digests_match": copy_manifest == manifest,
            "identity_preserved": copy_report["identity_preserved"],
            "content_matches": copy_report["content_matches"],
        }

        # ------------------------------------------- 2. interrupted mid-copy
        resume_destination = destination_parent / f"resumed project {token}"
        resume_plan = plan_relocation(source, resume_destination, operation="copy")
        journal_dir = destination_parent / "journal"
        interrupted = False
        try:
            apply_relocation(
                resume_plan,
                journal_dir=journal_dir,
                fault_inject=_fault_at("copy", "started"),
            )
        except SimulatedInterruption:
            interrupted = True
        # A real crash can leave a partial tree. Model one complete file and one
        # truncated file, then resume from the persisted journal.
        (resume_destination / "src").mkdir(parents=True, exist_ok=True)
        (resume_destination / "README.md").write_bytes((source / "README.md").read_bytes())
        (resume_destination / "src" / "app.py").write_bytes(b"pri")
        resumed = recover_relocation(journal_dir / f"{resume_plan.plan_id}.json")
        resume_report = verify_relocation(source, resume_destination)
        result["checks"]["resume"] = {
            "interrupted": interrupted,
            "state": resumed["state"],
            "resumed": resumed["resumed"],
            "reused_files": resumed["copy_report"]["reused"],
            "content_matches": resume_report["content_matches"],
            "identity_preserved": resume_report["identity_preserved"],
        }

        # ------------------------------------------- 3. registry rebind
        registry = RegistryV2(source_parent / "registry.db")
        project_uuid = uuid.uuid4()
        workspace_uuid = uuid.uuid4()
        registry.register_project(
            project_id=project_uuid,
            canonical_name="Cross Volume Project",
            identity_hash=identity.identity_hash,
        )
        registry.register_workspace(
            workspace_id=workspace_uuid,
            project_id=project_uuid,
            location_reference=str(source),
            normalized_location=str(source.resolve()),
        )
        rebind_destination = destination_parent / f"rebound project {token}"
        rebind_plan = plan_relocation(source, rebind_destination, operation="copy")
        rebind_result = apply_relocation(
            rebind_plan, journal_dir=journal_dir, registry=registry
        )
        workspace = registry.get_workspace(workspace_uuid)
        result["checks"]["registry_rebind"] = {
            "status": rebind_result["rebind"]["status"],
            "old_location": str(source.resolve()),
            "new_location": workspace.normalized_location,
            "updated": workspace.normalized_location == str(rebind_destination.resolve()),
        }

        # ---------------------------------------------------------- 4. move
        move_destination = destination_parent / f"moved project {token}"
        move_plan = plan_relocation(source, move_destination, operation="move")
        move_result = apply_relocation(
            move_plan,
            journal_dir=journal_dir,
            confirm_delete_source=True,
        )
        moved_manifest = _manifest(move_destination)
        result["checks"]["move"] = {
            "state": move_result["state"],
            "source_deleted": move_result["source_deleted"],
            "delete_reason": move_result["delete_reason"],
            "source_exists": source.exists(),
            "files": len(moved_manifest),
            "digests_match": moved_manifest == manifest,
            "identity_preserved": read_identity(move_destination).project_id == identity.project_id,
        }

        result["ok"] = all(
            (
                result["checks"]["copy"]["state"] == "complete",
                result["checks"]["copy"]["digests_match"],
                result["checks"]["copy"]["identity_preserved"],
                result["checks"]["copy"]["source_retained"],
                result["checks"]["resume"]["interrupted"],
                result["checks"]["resume"]["content_matches"],
                result["checks"]["resume"]["identity_preserved"],
                result["checks"]["registry_rebind"]["updated"],
                result["checks"]["move"]["state"] == "complete",
                result["checks"]["move"]["source_deleted"],
                result["checks"]["move"]["digests_match"],
                result["checks"]["move"]["identity_preserved"],
            )
        )
    except Exception as exc:  # noqa: BLE001 - evidence tool reports the class
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        if registry is not None:
            registry.close()
        if not args.keep:
            shutil.rmtree(destination_parent, ignore_errors=True)
            shutil.rmtree(source_parent, ignore_errors=True)
            result["cleaned_up"] = True
        else:
            result["cleaned_up"] = False

    serialized = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    print(serialized)
    if args.evidence:
        path = Path(args.evidence)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + "\n", encoding="utf-8")
        print(f"evidence written to {path}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

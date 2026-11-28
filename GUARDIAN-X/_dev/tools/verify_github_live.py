"""Live, read-only verification of the GitHub host adapter (HOST-14/ID-13/PROV-05).

The token is read from ``OGX_GITHUB_TOKEN`` only. It is never printed, never
written to the evidence file, and the script fails if the serialized output
contains it. Every call is a read: repository metadata, the root tree, branches,
commits, pull requests and one bounded file read. No write method is invoked.

Usage (PowerShell):

    $env:OGX_GITHUB_TOKEN = (gh auth token)
    python _dev/tools/verify_github_live.py --repository owner/name \
        --evidence docs/evidence/github-live-2026-11-28.json

Exit codes: 0 verified, 1 verification failed, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ogx.hosts.adapters.github_runtime import (  # noqa: E402
    GitHubHostAdapter,
    GitHubHostError,
)
from ogx.hosts.lifecycle import HostSessionConfig  # noqa: E402


def _short(value: object, limit: int = 80) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return text[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--file", default="README.md", help="bounded read target")
    parser.add_argument("--evidence", default=None, help="write a JSON evidence file")
    args = parser.parse_args(argv)

    token = os.environ.get("OGX_GITHUB_TOKEN", "").strip()
    if not token:
        print("OGX_GITHUB_TOKEN is not set; run: $env:OGX_GITHUB_TOKEN = (gh auth token)")
        return 2

    # Keep all state (audit paths, caches) outside the repository.
    os.environ.setdefault("OGX_HOME", tempfile.mkdtemp(prefix="ogx-github-live-"))

    adapter = GitHubHostAdapter(HostSessionConfig(root=args.repository))
    result: dict[str, object] = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository": args.repository,
        "read_only": True,
        "python": sys.version.split()[0],
    }
    started = time.monotonic()
    try:
        adapter.validate()
        capabilities = adapter.capabilities()
        result["capabilities"] = capabilities
        result["health"] = adapter.health().to_dict()
        adapter.connect()
        result["connected"] = True

        metadata = adapter.metadata()
        result["metadata"] = {
            key: metadata.get(key)
            for key in ("full_name", "default_branch", "visibility", "provider")
            if key in metadata
        }

        tree = adapter.list_directory("")
        result["tree"] = {
            "entries": len(tree["entries"]),
            "truncated": bool(tree["truncated"]),
            "sample": [_short(entry.get("path"), 120) for entry in tree["entries"][:5]],
        }

        branches = adapter.branches(limit=5)
        result["branches"] = {
            "count": len(branches),
            "names": [_short(branch.get("name")) for branch in branches],
        }

        commits = adapter.commits(limit=5)
        result["commits"] = {
            "count": len(commits),
            "shas": [_short(commit.get("sha"), 12) for commit in commits],
        }

        pulls = adapter.pull_requests(state="open", limit=5)
        result["pull_requests"] = {"open": len(pulls)}

        file = adapter.read_file(args.file, max_bytes=4096)
        result["file"] = {
            "path": file["path"],
            "sha": _short(file["sha"], 12),
            "size": file["size"],
            "returned_bytes": len(str(file["content"]).encode("utf-8")),
            "truncated": bool(file["truncated"]),
        }

        write_claims = {
            name: state
            for name, state in capabilities.items()
            if name in {"branch_create", "file_write", "commit_create", "push",
                        "pull_request_create"}
        }
        result["write_claims"] = write_claims
        # Capability truth (provider rule 11): a "supported" claim must map to a
        # callable host method. The live probe never invokes a write.
        methods = {
            "repository_metadata_read": "metadata",
            "repository_structure_read": "list_directory",
            "branch_read": "branches",
            "commit_read": "commits",
            "file_read": "read_file",
            "branch_create": "create_branch",
            "file_write": "write_file",
            "commit_create": "commit_files",
            "push": "push",
            "pull_request_create": "create_pull_request",
        }
        unimplemented = sorted(
            name for name, state in capabilities.items()
            if state == "supported" and name in methods
            and not callable(getattr(adapter, methods[name], None))
        )
        result["unsupported_claims"] = unimplemented
        if unimplemented:
            raise GitHubHostError(
                "host adapter advertised capabilities without an implementation: "
                + ", ".join(unimplemented),
                code="capability_truth_violation",
            )
        adapter.disconnect()
        result["connected_after_disconnect"] = adapter.describe()["connected"]
        result["ok"] = True
    except GitHubHostError as exc:
        result["ok"] = False
        result["error"] = f"{exc.code}: {_short(exc)}"
    except Exception as exc:  # noqa: BLE001 - evidence tool reports the class only
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {_short(exc)}"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)

    serialized = json.dumps(result, indent=2, sort_keys=True)
    if token in serialized:
        print("refusing to print evidence: the credential appeared in the payload")
        return 1
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

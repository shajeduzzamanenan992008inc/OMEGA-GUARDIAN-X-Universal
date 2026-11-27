"""Release artifact checksums (REL-02).

Deterministic, reproducible manifests: the JSON manifest carries no timestamp so
the same inputs always hash to the same manifest bytes, and every entry is named
and sized exactly as shipped.

Verification is hostile-input safe: names must be bare relative names with no
separator, no ``..`` and no drive prefix, so a manifest can never be used to
read outside the directory being verified.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

SUMS_SCHEMA_VERSION = "1.0"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")


class ArtifactError(Exception):
    """A manifest or artifact violated a safety or integrity rule."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise ArtifactError(f"Artifact exceeds the {MAX_ARTIFACT_BYTES} byte limit: {path.name}")
            digest.update(chunk)
    return digest.hexdigest()


def _validate_name(name: str) -> None:
    if not _SAFE_NAME.match(name):
        raise ArtifactError(
            f"Unsafe artifact name {name!r}: use a bare file name with no path separator."
        )
    if name in {".", ".."} or name.lower() in {"sha256sums", "sha256sums.txt"}:
        raise ArtifactError(f"Reserved artifact name: {name!r}")


def describe_artifacts(paths: Iterable[Path | str]) -> list[dict[str, Any]]:
    """Describe each artifact by name, size and SHA-256, sorted by name."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in paths:
        path = Path(item)
        if path.is_symlink():
            raise ArtifactError(f"Refusing to checksum a symlink: {path}")
        if not path.is_file():
            raise ArtifactError(f"Not a regular file: {path}")
        _validate_name(path.name)
        if path.name in seen:
            raise ArtifactError(f"Duplicate artifact name: {path.name}")
        seen.add(path.name)
        size = path.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"Artifact exceeds the {MAX_ARTIFACT_BYTES} byte limit: {path.name}")
        entries.append({"name": path.name, "size": size, "sha256": sha256_file(path)})
    if not entries:
        raise ArtifactError("No artifacts were supplied.")
    entries.sort(key=lambda entry: entry["name"])
    return entries


def render_sums(entries: list[dict[str, Any]]) -> str:
    """Render ``sha256sum``-compatible text (two spaces, LF endings)."""
    return "".join(f"{entry['sha256']}  {entry['name']}\n" for entry in entries)


def render_manifest(entries: list[dict[str, Any]], *, version: str) -> str:
    """Render the JSON manifest. No timestamp: identical inputs give identical bytes."""
    payload = {
        "schema_version": SUMS_SCHEMA_VERSION,
        "version": version,
        "algorithm": "sha256",
        "artifacts": entries,
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_manifests(
    paths: Iterable[Path | str],
    *,
    version: str,
    sums_path: Path | str,
    manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    entries = describe_artifacts(paths)
    sums_target = Path(sums_path)
    sums_target.parent.mkdir(parents=True, exist_ok=True)
    sums_text = render_sums(entries)
    sums_target.write_text(sums_text, encoding="utf-8", newline="\n")
    result: dict[str, Any] = {
        "version": version,
        "artifacts": entries,
        "sums": str(sums_target),
        "sums_sha256": hashlib.sha256(sums_text.encode("utf-8")).hexdigest(),
    }
    if manifest_path is not None:
        manifest_target = Path(manifest_path)
        manifest_target.parent.mkdir(parents=True, exist_ok=True)
        manifest_text = render_manifest(entries, version=version)
        manifest_target.write_text(manifest_text, encoding="utf-8", newline="\n")
        result["manifest"] = str(manifest_target)
        result["manifest_sha256"] = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    return result


def parse_sums(text: str) -> list[dict[str, Any]]:
    """Parse ``sha256sum`` text; reject anything that is not a safe, exact entry."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip("\r")
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match:
            raise ArtifactError(f"Malformed checksum line {number}: expected '<sha256>  <name>'.")
        digest, name = match.group(1).lower(), match.group(2)
        if not _HEX64.match(digest):
            raise ArtifactError(f"Malformed digest on line {number}.")
        _validate_name(name)
        if name in seen:
            raise ArtifactError(f"Duplicate artifact name on line {number}: {name}")
        seen.add(name)
        entries.append({"name": name, "sha256": digest})
    if not entries:
        raise ArtifactError("Checksum manifest is empty.")
    return entries


def verify_sums(text: str, base_dir: Path | str) -> dict[str, Any]:
    """Verify every manifest entry against ``base_dir`` without following links."""
    base = Path(base_dir).resolve()
    entries = parse_sums(text)
    report: dict[str, Any] = {
        "checked": 0,
        "ok": [],
        "mismatched": [],
        "missing": [],
        "unsafe": [],
        "all_ok": False,
    }
    for entry in entries:
        target = (base / entry["name"]).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            report["unsafe"].append(entry["name"])
            continue
        if target.is_symlink():
            report["unsafe"].append(entry["name"])
            continue
        if not target.is_file():
            report["missing"].append(entry["name"])
            continue
        actual = sha256_file(target)
        report["checked"] += 1
        if actual == entry["sha256"]:
            report["ok"].append(entry["name"])
        else:
            report["mismatched"].append(
                {"name": entry["name"], "expected": entry["sha256"], "actual": actual}
            )
    report["all_ok"] = (
        report["checked"] > 0
        and not report["mismatched"]
        and not report["missing"]
        and not report["unsafe"]
    )
    return report

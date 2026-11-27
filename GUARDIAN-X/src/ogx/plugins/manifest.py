"""Strict plugin manifest validation and deterministic artifact digests.

A plugin manifest is untrusted input. Validation therefore:

* rejects unknown keys (no silent capability smuggling),
* restricts the name/publisher/entrypoint grammars,
* refuses absolute paths, traversal, backslashes, drive prefixes, control
  characters and duplicate file entries,
* requires every declared file to carry a SHA-256 digest and size,
* computes a canonical artifact digest over the complete manifest body, so a
  signature binds the exact set of files, permissions and compatibility range.

The manifest never grants permissions by itself; see :mod:`ogx.plugins.permissions`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
MAX_FILES = 512
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_PUBLISHER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)

#: Capabilities a plugin may request. Anything not listed is rejected.
KNOWN_CAPABILITIES = frozenset(
    {
        "fs.read.package",
        "fs.write.package",
        "fs.read.workspace",
        "fs.write.workspace",
        "process.spawn",
        "network.outbound",
        "env.read.allowlisted",
        "artifact.emit",
    }
)

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "version",
        "publisher",
        "description",
        "entrypoint",
        "runtime",
        "ogx_requires",
        "permissions",
        "dependencies",
        "files",
    }
)


class ManifestError(ValueError):
    """Raised when a plugin manifest is invalid or unsafe."""


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class Version:
    """A small semantic version with prerelease ordering."""

    major: int
    minor: int
    patch: int
    prerelease: str = ""

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return f"{core}-{self.prerelease}" if self.prerelease else core

    def key(self) -> tuple[int, int, int, int, str]:
        # A release sorts after any prerelease of the same core version.
        return (self.major, self.minor, self.patch, 1 if not self.prerelease else 0, self.prerelease)

    def __lt__(self, other: "Version") -> bool:
        return self.key() < other.key()

    def __le__(self, other: "Version") -> bool:
        return self.key() <= other.key()


_VERSION_RE = re.compile(
    r"^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})"
    r"(?:-([0-9A-Za-z.-]{1,32}))?$"
)


def parse_version(value: str) -> Version:
    """Parse ``major.minor.patch[-prerelease]`` without accepting ranges."""

    if not isinstance(value, str):
        raise ManifestError("Version must be a string.")
    match = _VERSION_RE.match(value.strip())
    if not match:
        raise ManifestError(f"Invalid semantic version: {value!r}.")
    major, minor, patch, prerelease = match.groups()
    return Version(int(major), int(minor), int(patch), prerelease or "")


def _compare(left: Version, right: Version) -> int:
    left_key, right_key = left.key(), right.key()
    return (left_key > right_key) - (left_key < right_key)


def satisfies(version: Version, specifier: str) -> bool:
    """Return True when ``version`` satisfies a comma-separated specifier set."""

    if not isinstance(specifier, str) or not specifier.strip():
        raise ManifestError("Version specifier must not be empty.")
    for raw in specifier.split(","):
        clause = raw.strip()
        if not clause:
            raise ManifestError("Version specifier contains an empty clause.")
        match = re.match(r"^(>=|<=|==|!=|~=|>|<)\s*(.+)$", clause)
        if not match:
            raise ManifestError(f"Unsupported version specifier clause: {clause!r}.")
        operator, raw_version = match.groups()
        target = parse_version(raw_version)
        comparison = _compare(version, target)
        if operator == ">=" and comparison < 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator == "==" and comparison != 0:
            return False
        if operator == "!=" and comparison == 0:
            return False
        if operator == "~=":
            upper = Version(target.major, target.minor + 1, 0)
            if comparison < 0 or version.key() >= upper.key():
                return False
    return True


def _validate_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty relative path.")
    if len(value) > 240:
        raise ManifestError(f"{field} is too long.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ManifestError(f"{field} contains control characters.")
    if value.startswith(("/", "\\")) or value.startswith("~"):
        raise ManifestError(f"{field} must be relative.")
    if "\\" in value:
        raise ManifestError(f"{field} must use forward slashes.")
    if re.match(r"^[A-Za-z]:", value):
        raise ManifestError(f"{field} must not contain a drive prefix.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ManifestError(f"{field} contains an empty, '.' or '..' segment.")
    for part in parts:
        if part.split(".")[0].lower() in _RESERVED_NAMES:
            raise ManifestError(f"{field} contains a reserved device name.")
        if part.endswith((" ", ".")):
            raise ManifestError(f"{field} contains a trailing space or dot.")
    return value


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class Dependency:
    name: str
    specifier: str


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: Version
    publisher: str
    description: str
    entrypoint: str
    runtime: str
    ogx_requires: str
    permissions: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    files: tuple[FileEntry, ...]
    schema_version: int = SCHEMA_VERSION

    def body(self) -> dict[str, Any]:
        """Return the canonical signing body (stable ordering, no digest)."""

        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": str(self.version),
            "publisher": self.publisher,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "runtime": self.runtime,
            "ogx_requires": self.ogx_requires,
            "permissions": sorted(set(self.permissions)),
            "dependencies": [
                {"name": dep.name, "specifier": dep.specifier}
                for dep in sorted(self.dependencies, key=lambda d: d.name)
            ],
            "files": [
                {"path": entry.path, "sha256": entry.sha256, "size": entry.size}
                for entry in sorted(self.files, key=lambda f: f.path)
            ],
        }

    def artifact_digest(self) -> str:
        return sha256_hex(_canonical_json(self.body()))

    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files)

    def file_entry(self, path: str) -> FileEntry | None:
        for entry in self.files:
            if entry.path == path:
                return entry
        return None


def _require_str(mapping: Mapping[str, Any], key: str, *, max_len: int = 200) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"Manifest field {key!r} must be a non-empty string.")
    if len(value) > max_len:
        raise ManifestError(f"Manifest field {key!r} is too long.")
    return value.strip()


def parse_manifest(raw: Mapping[str, Any] | str | bytes) -> PluginManifest:
    """Validate an untrusted manifest mapping and return a typed object."""

    if isinstance(raw, (str, bytes)):
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(data) > MAX_MANIFEST_BYTES:
            raise ManifestError("Plugin manifest exceeds the size limit.")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError("Plugin manifest is not valid UTF-8 JSON.") from exc
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        raise ManifestError("Plugin manifest must be a JSON object.")

    if not isinstance(payload, dict):
        raise ManifestError("Plugin manifest must be a JSON object.")
    unknown = set(payload) - _MANIFEST_KEYS
    if unknown:
        raise ManifestError(f"Unknown manifest field(s): {', '.join(sorted(unknown))}.")
    missing = _MANIFEST_KEYS - set(payload)
    if missing:
        raise ManifestError(f"Missing manifest field(s): {', '.join(sorted(missing))}.")

    schema_version = payload["schema_version"]
    if schema_version != SCHEMA_VERSION:
        raise ManifestError(f"Unsupported manifest schema version: {schema_version!r}.")

    name = _require_str(payload, "name", max_len=64)
    if not _NAME_RE.match(name):
        raise ManifestError("Plugin name must be lowercase letters, digits, '.', '_' or '-'.")
    publisher = _require_str(payload, "publisher", max_len=64)
    if not _PUBLISHER_RE.match(publisher):
        raise ManifestError("Publisher must be lowercase letters, digits, '.', '_' or '-'.")

    version = parse_version(_require_str(payload, "version", max_len=64))
    description = _require_str(payload, "description", max_len=500)
    entrypoint = _validate_relative_path(payload["entrypoint"], "entrypoint")

    runtime = payload["runtime"]
    if runtime not in {"python"}:
        raise ManifestError("Only the 'python' plugin runtime is supported.")

    ogx_requires = _require_str(payload, "ogx_requires", max_len=120)
    # Validate the specifier syntax now, before any install decision.
    satisfies(version, ogx_requires)

    permissions = payload["permissions"]
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise ManifestError("permissions must be a list of strings.")
    if len(permissions) > len(KNOWN_CAPABILITIES):
        raise ManifestError("Too many permission entries.")
    for permission in permissions:
        if permission not in KNOWN_CAPABILITIES:
            raise ManifestError(f"Unknown capability: {permission!r}.")

    raw_dependencies = payload["dependencies"]
    if not isinstance(raw_dependencies, list):
        raise ManifestError("dependencies must be a list.")
    dependencies: list[Dependency] = []
    seen_dependencies: set[str] = set()
    for item in raw_dependencies:
        if not isinstance(item, Mapping) or set(item) != {"name", "specifier"}:
            raise ManifestError("Each dependency must have exactly 'name' and 'specifier'.")
        dep_name = item["name"]
        if not isinstance(dep_name, str) or not _NAME_RE.match(dep_name):
            raise ManifestError("Dependency name is invalid.")
        if dep_name in seen_dependencies:
            raise ManifestError(f"Duplicate dependency: {dep_name}.")
        seen_dependencies.add(dep_name)
        dep_spec = item["specifier"]
        if not isinstance(dep_spec, str):
            raise ManifestError("Dependency specifier must be a string.")
        satisfies(version, dep_spec)  # syntax validation only
        dependencies.append(Dependency(dep_name, dep_spec))

    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise ManifestError("files must be a non-empty list.")
    if len(raw_files) > MAX_FILES:
        raise ManifestError("Manifest declares too many files.")
    files: list[FileEntry] = []
    seen_paths: set[str] = set()
    total = 0
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "size"}:
            raise ManifestError("Each file entry must have exactly 'path', 'sha256' and 'size'.")
        path = _validate_relative_path(item["path"], "file path")
        if path in seen_paths:
            raise ManifestError(f"Duplicate file entry: {path}.")
        seen_paths.add(path)
        digest = item["sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise ManifestError(f"Invalid SHA-256 digest for {path}.")
        size = item["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError(f"Invalid size for {path}.")
        if size > MAX_FILE_BYTES:
            raise ManifestError(f"File {path} exceeds the per-file size limit.")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ManifestError("Declared plugin files exceed the total size limit.")
        files.append(FileEntry(path, digest, size))

    if entrypoint not in seen_paths:
        raise ManifestError("entrypoint must be one of the declared files.")

    return PluginManifest(
        name=name,
        version=version,
        publisher=publisher,
        description=description,
        entrypoint=entrypoint,
        runtime=runtime,
        ogx_requires=ogx_requires,
        permissions=tuple(sorted(set(permissions))),
        dependencies=tuple(dependencies),
        files=tuple(files),
    )


def verify_files(manifest: PluginManifest, contents: Mapping[str, bytes]) -> None:
    """Verify every extracted file matches the manifest digest and size."""

    declared = {entry.path for entry in manifest.files}
    undeclared = set(contents) - declared
    if undeclared:
        raise ManifestError(
            f"Package contains undeclared file(s): {', '.join(sorted(undeclared))}."
        )
    for entry in manifest.files:
        if entry.path not in contents:
            raise ManifestError(f"Package is missing declared file: {entry.path}.")
        data = contents[entry.path]
        if len(data) != entry.size:
            raise ManifestError(f"Size mismatch for {entry.path}.")
        if sha256_hex(data) != entry.sha256:
            raise ManifestError(f"Digest mismatch for {entry.path}.")


def dependency_names(manifest: PluginManifest) -> Iterable[str]:
    return (dep.name for dep in manifest.dependencies)

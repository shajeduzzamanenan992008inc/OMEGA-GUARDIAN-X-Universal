"""Bounded, read-only project workspace for engine analysis.

Design constraints:

* The workspace root is resolved once and every read is confined to it.
* Symlinks are never followed during indexing or reading.
* File count, file size and total sampled bytes are bounded.
* Structured parsers reject DTD/entity declarations before parsing XML.
* Nothing in this module executes project code, package managers or builds.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ogx.kernel.config import ScanConfig, load_config

MAX_XML_BYTES = 2_000_000
MAX_STRUCTURED_BYTES = 4_000_000
FINGERPRINT_MANIFEST_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties",
    "androidmanifest.xml", "composer.json", "composer.lock", "gemfile", "gemfile.lock",
    "package.swift", "cmakelists.txt", "pubspec.yaml", "pubspec.lock", "mix.exs",
    "build.sbt", "stack.yaml", "cabal.project", "chart.yaml", "dockerfile",
}


class WorkspaceError(ValueError):
    """A safe, user-facing workspace access error."""


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    size: int


@dataclass(slots=True)
class Workspace:
    root: Path
    config: ScanConfig
    files: list[FileEntry] = field(default_factory=list)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    _by_path: dict[str, FileEntry] = field(default_factory=dict, repr=False)

    @classmethod
    def open(cls, root: Path | str, config: ScanConfig | None = None) -> "Workspace":
        resolved = Path(root).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise WorkspaceError(f"Project path is not a directory: {resolved}")
        workspace = cls(root=resolved, config=config or load_config())
        workspace._index()
        return workspace

    # -- indexing ---------------------------------------------------------
    def _index(self) -> None:
        excludes = set(self.config.excludes)
        self.files = []
        self.skipped = 0
        self.warnings = []
        for current, dirs, names in os.walk(self.root, topdown=True, followlinks=False):
            base = Path(current)
            kept: list[str] = []
            for directory in dirs:
                candidate = base / directory
                if directory in excludes or candidate.is_symlink():
                    self.skipped += 1
                else:
                    kept.append(directory)
            dirs[:] = kept
            for name in names:
                candidate = base / name
                try:
                    if candidate.is_symlink() or not candidate.is_file():
                        self.skipped += 1
                        continue
                    size = candidate.stat().st_size
                except OSError:
                    self.skipped += 1
                    continue
                if len(self.files) >= self.config.max_files:
                    self.warnings.append(f"File limit reached ({self.config.max_files}); engine analysis is partial.")
                    self.files.sort(key=lambda entry: entry.path)
                    self._by_path = {entry.path: entry for entry in self.files}
                    return
                entry = FileEntry(candidate.relative_to(self.root).as_posix(), size)
                self.files.append(entry)
        self.files.sort(key=lambda entry: entry.path)
        self._by_path = {entry.path: entry for entry in self.files}

    # -- queries ----------------------------------------------------------
    def __len__(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files)

    @property
    def names(self) -> set[str]:
        return {Path(entry.path).name for entry in self.files}

    @property
    def dirs(self) -> set[str]:
        return {Path(entry.path).parent.as_posix() for entry in self.files}

    def exists(self, rel: str) -> bool:
        return self._normalize(rel) in self._by_path

    def entry(self, rel: str) -> FileEntry | None:
        return self._by_path.get(self._normalize(rel))

    def with_suffixes(self, suffixes: Iterable[str]) -> list[FileEntry]:
        wanted = {suffix.lower() for suffix in suffixes}
        return [entry for entry in self.files if Path(entry.path).suffix.lower() in wanted]

    def with_names(self, names: Iterable[str]) -> list[FileEntry]:
        wanted = {name.casefold() for name in names}
        return [entry for entry in self.files if Path(entry.path).name.casefold() in wanted]

    def glob(self, pattern: str, *, limit: int = 5000) -> list[FileEntry]:
        normalized = pattern.replace("\\", "/")
        return [entry for entry in self.files if fnmatch.fnmatchcase(entry.path, normalized)][:limit]

    def count_suffix(self, suffix: str) -> int:
        suffix = suffix.lower()
        return sum(1 for entry in self.files if Path(entry.path).suffix.lower() == suffix)

    # -- safe reads -------------------------------------------------------
    def _normalize(self, rel: str) -> str:
        rel = rel.replace("\\", "/")
        if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise WorkspaceError("Workspace reads require a relative path.")
        parts = PurePathParts(rel)
        if any(part in {"..", ""} for part in parts):
            raise WorkspaceError("Workspace reads must not traverse outside the project root.")
        return "/".join(parts)

    def _safe_path(self, rel: str) -> tuple[str, Path]:
        normalized = self._normalize(rel)
        candidate = self.root / normalized
        if candidate.is_symlink():
            raise WorkspaceError(f"Refusing to follow a symlink: {normalized}")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError(f"File is unavailable: {normalized}") from exc
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"Path escapes the project root: {normalized}")
        if not resolved.is_file():
            raise WorkspaceError(f"Not a regular file: {normalized}")
        return normalized, resolved

    def read_bytes(self, rel: str, *, max_bytes: int | None = None) -> bytes:
        normalized, path = self._safe_path(rel)
        limit = max_bytes if max_bytes is not None else self.config.max_file_bytes
        try:
            if path.stat().st_size > limit:
                raise WorkspaceError(f"File exceeds the read limit: {normalized}")
            data = path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"Could not read file: {normalized}") from exc
        if len(data) > limit:
            raise WorkspaceError(f"File exceeds the read limit: {normalized}")
        return data

    def read_text(self, rel: str, *, max_bytes: int | None = None) -> str:
        normalized, _ = self._safe_path(rel)
        data = self.read_bytes(normalized, max_bytes=max_bytes)
        if b"\x00" in data[:8192]:
            raise WorkspaceError(f"Binary content is not read as text: {normalized}")
        return data.decode("utf-8-sig", errors="replace")

    def json(self, rel: str) -> Any:
        text = self.read_text(rel, max_bytes=MAX_STRUCTURED_BYTES)
        return json.loads(text)

    def jsonc(self, rel: str) -> Any:
        text = self.read_text(rel, max_bytes=MAX_STRUCTURED_BYTES)
        return json.loads(strip_jsonc(text))

    def toml(self, rel: str) -> Any:
        text = self.read_text(rel, max_bytes=MAX_STRUCTURED_BYTES)
        return tomllib.loads(text)

    def xml(self, rel: str) -> ET.Element:
        text = self.read_text(rel, max_bytes=MAX_XML_BYTES)
        if re.search(r"<!(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            raise WorkspaceError(f"XML DTD/entity declarations are not accepted: {rel}")
        try:
            return ET.fromstring(text)
        except ET.ParseError as exc:
            line = exc.position[0] if getattr(exc, "position", None) else None
            raise WorkspaceError(f"Invalid XML in {rel}" + (f" at line {line}" if line else "")) from exc

    def readable_path(self, rel: str) -> Path:
        """Return a resolved, boundary-checked path for streaming readers."""
        _, path = self._safe_path(rel)
        return path

    # -- hashes -----------------------------------------------------------
    def sha256(self, rel: str) -> str:
        digest = hashlib.sha256()
        _, path = self._safe_path(rel)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def fingerprint(self) -> dict[str, Any]:
        tree = hashlib.sha256()
        for entry in self.files:
            tree.update(entry.path.encode("utf-8"))
            tree.update(b"\x00")
            tree.update(str(entry.size).encode("ascii"))
            tree.update(b"\x00")
        sampled = 0
        content = hashlib.sha256()
        for entry in self.files:
            if sampled >= 128:
                break
            if Path(entry.path).name.casefold() not in FINGERPRINT_MANIFEST_NAMES:
                continue
            try:
                data = self.read_bytes(entry.path, max_bytes=1_000_000)
            except WorkspaceError:
                continue
            content.update(entry.path.encode("utf-8"))
            content.update(b"\x00")
            content.update(hashlib.sha256(data).digest())
            sampled += 1
        material = f"{os.path.normcase(str(self.root))}\x00{tree.hexdigest()}\x00{content.hexdigest()}"
        project_id = "ogx-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return {
            "project_id": project_id,
            "root": str(self.root),
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "tree_digest": tree.hexdigest(),
            "manifest_sample_count": sampled,
            "manifest_digest": content.hexdigest(),
        }


def PurePathParts(rel: str) -> list[str]:
    """Split a POSIX relative path without resolving it on the filesystem."""
    return [part for part in rel.split("/") if part not in {".", ""}]


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments plus trailing commas outside strings.

    This is a bounded lexical helper for JSON-with-comments files such as
    tsconfig.json.  It is not a general JavaScript parser.
    """
    chars: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            chars.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            chars.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "/":
                while i < len(text) and text[i] not in "\r\n":
                    chars.append(" ")
                    i += 1
                continue
            if nxt == "*":
                chars.append("  ")
                i += 2
                while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                    chars.append("\n" if text[i] in "\r\n" else " ")
                    i += 1
                if i + 1 < len(text):
                    chars.append("  ")
                    i += 2
                continue
        chars.append(char)
        i += 1
    output = "".join(chars)

    # Second pass: strip trailing commas before } or ] outside strings.
    result: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(output):
        char = output[i]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            i += 1
            continue
        if char == ",":
            lookahead = i + 1
            while lookahead < len(output) and output[lookahead] in " \t\r\n":
                lookahead += 1
            if lookahead < len(output) and output[lookahead] in "}]":
                result.append(" ")
                i += 1
                continue
        result.append(char)
        i += 1
    return "".join(result)

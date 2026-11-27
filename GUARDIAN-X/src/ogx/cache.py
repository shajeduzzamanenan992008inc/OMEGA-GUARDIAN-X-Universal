"""Content-addressed incremental scan cache with bounded storage (PRF-04).

Correctness rules:

* keys are the SHA-256 of ``ruleset + relative path + file content``. A file
  edit, a renamed file, a rule change or a tool-version change is a miss, so a
  stale finding can never be served from an old analysis.
* file mtimes are **not** part of the key and are never trusted as evidence of
  unchanged content.
* storage is bounded by both entry count and byte size; the least recently used
  entries are evicted first.
* the cache is advisory: corruption or an I/O failure raises
  :class:`CacheCorrupt` and callers fall back to a full scan instead of
  producing a partial result.

Files whose analysis depends on more than their own content (``.gitignore``
depends on whether a root ``.env`` exists) are never cached by the scanner.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .models import Finding

DEFAULT_MAX_ENTRIES = 5_000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_cache (
    key TEXT PRIMARY KEY,
    ruleset TEXT NOT NULL,
    findings TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    created REAL NOT NULL,
    last_used REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_cache_lru ON scan_cache (last_used);
"""


class CacheError(RuntimeError):
    """Base class for cache failures."""


class CacheCorrupt(CacheError):
    """The cache exists but cannot be trusted; callers must scan fully."""


class ScanCache:
    def __init__(
        self,
        db_path: Path | str,
        *,
        ruleset: str,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        clock=time.time,
    ) -> None:
        if not ruleset:
            raise CacheError("A cache needs a ruleset fingerprint.")
        if max_entries < 1:
            raise CacheError("max_entries must be at least 1.")
        if max_bytes < 1_024:
            raise CacheError("max_bytes must be at least 1 KiB.")
        self.path = Path(db_path)
        self.ruleset = ruleset
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._clock = clock
        self.hits = 0
        self.misses = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise CacheError("Refusing a symlinked cache file.")
        try:
            with self._session() as connection:
                connection.executescript(_SCHEMA)
                found = connection.execute(
                    "SELECT ruleset FROM scan_cache LIMIT 1"
                ).fetchone()
                if found is not None and str(found["ruleset"]) != self.ruleset:
                    # A different tool/ruleset generation: drop everything.
                    connection.execute("DELETE FROM scan_cache")
        except sqlite3.DatabaseError as exc:
            raise CacheCorrupt(f"Scan cache is unreadable: {exc}") from exc

    @contextmanager
    def _session(self):
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()

    def key_for(self, relative_path: str, content: bytes) -> str:
        digest = hashlib.sha256()
        digest.update(self.ruleset.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(relative_path).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content)
        return digest.hexdigest()

    def get(self, key: str) -> list[Finding] | None:
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT findings FROM scan_cache WHERE key = ? AND ruleset = ?",
                    (key, self.ruleset),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    self.misses += 1
                    return None
                connection.execute(
                    "UPDATE scan_cache SET last_used = ? WHERE key = ?", (self._clock(), key)
                )
                connection.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            raise CacheCorrupt(f"Scan cache read failed: {exc}") from exc
        try:
            payload = json.loads(row["findings"])
            results = [Finding(**item) for item in payload]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            # A corrupt row is worse than a miss: evict it and scan again.
            self.delete(key)
            raise CacheCorrupt("Cached findings were malformed; entry evicted.") from exc
        self.hits += 1
        return results

    def put(self, key: str, findings: Iterable[Finding]) -> int:
        payload = [dataclasses.asdict(finding) for finding in findings]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
        moment = self._clock()
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR REPLACE INTO scan_cache"
                    " (key, ruleset, findings, bytes, created, last_used)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (key, self.ruleset, encoded, size, moment, moment),
                )
                connection.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            raise CacheCorrupt(f"Scan cache write failed: {exc}") from exc
        self._evict()
        return size

    def _evict(self) -> int:
        removed = 0
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                entries, total = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM scan_cache"
                ).fetchone()
                while int(entries) > self.max_entries or int(total) > self.max_bytes:
                    row = connection.execute(
                        "SELECT key, bytes FROM scan_cache ORDER BY last_used, created LIMIT 1"
                    ).fetchone()
                    if row is None:
                        break
                    connection.execute("DELETE FROM scan_cache WHERE key = ?", (row["key"],))
                    entries = int(entries) - 1
                    total = int(total) - int(row["bytes"])
                    removed += 1
                connection.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            raise CacheCorrupt(f"Scan cache eviction failed: {exc}") from exc
        return removed

    def delete(self, key: str) -> None:
        try:
            with self._session() as connection:
                connection.execute("DELETE FROM scan_cache WHERE key = ?", (key,))
        except sqlite3.DatabaseError:
            return

    def clear(self) -> int:
        try:
            with self._session() as connection:
                cursor = connection.execute("DELETE FROM scan_cache")
                return int(cursor.rowcount or 0)
        except sqlite3.DatabaseError as exc:
            raise CacheCorrupt(f"Scan cache clear failed: {exc}") from exc

    def stats(self) -> dict[str, Any]:
        try:
            with self._session() as connection:
                entries, total = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM scan_cache"
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise CacheCorrupt(f"Scan cache stats failed: {exc}") from exc
        lookups = self.hits + self.misses
        return {
            "entries": int(entries),
            "bytes": int(total),
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / lookups, 4) if lookups else None,
            "ruleset": self.ruleset,
        }


def default_cache_path(state_home: Path | str | None = None) -> Path:
    home = (
        Path(state_home)
        if state_home is not None
        else Path(os.environ.get("OGX_HOME", Path.home() / ".ogx"))
    )
    return home / "cache" / "scan-cache.sqlite3"

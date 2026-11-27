"""Durable plugin lifecycle store with enforced state transitions.

States and allowed transitions::

    (new) ----> installed ----> validated ----> enabled <--> disabled
                   |               |               |
                   +---------------+---------------+
                                   |
                                revoked ----> uninstalled
    disabled ---------------------------------------> uninstalled

A plugin may only be enabled after its signature/trust record was verified
(``validated``) and every declared dependency is installed, trusted and
version-compatible. State changes are recorded in an append-only event table
with actor, timestamp and a JSON detail payload (never secret values).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .manifest import ManifestError, PluginManifest, parse_version, satisfies
from .permissions import Grant, PermissionDenied

STATES = ("installed", "validated", "enabled", "disabled", "revoked", "uninstalled")

_ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "": frozenset({"installed"}),
    "installed": frozenset({"validated", "uninstalled"}),
    "validated": frozenset({"enabled", "uninstalled", "revoked"}),
    "enabled": frozenset({"disabled", "revoked"}),
    "disabled": frozenset({"enabled", "revoked", "uninstalled"}),
    "revoked": frozenset({"uninstalled"}),
    "uninstalled": frozenset(),
}

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS plugins (
  name TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  publisher TEXT NOT NULL,
  manifest_digest TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('installed','validated','enabled','disabled','revoked','uninstalled')),
  installed_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plugin_grants (
  plugin_name TEXT PRIMARY KEY REFERENCES plugins(name) ON DELETE CASCADE,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plugin_dependencies (
  plugin_name TEXT NOT NULL REFERENCES plugins(name) ON DELETE CASCADE,
  dependency_name TEXT NOT NULL,
  specifier TEXT NOT NULL,
  PRIMARY KEY (plugin_name, dependency_name)
);
CREATE TABLE IF NOT EXISTS plugin_events (
  id TEXT PRIMARY KEY,
  plugin_name TEXT NOT NULL,
  event TEXT NOT NULL,
  actor TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_plugin_events_name_time ON plugin_events(plugin_name, occurred_at);
CREATE TABLE IF NOT EXISTS plugin_trust (
  plugin_name TEXT PRIMARY KEY REFERENCES plugins(name) ON DELETE CASCADE,
  publisher TEXT NOT NULL,
  key_id TEXT NOT NULL,
  digest TEXT NOT NULL,
  verified_at TEXT NOT NULL
);
"""


class LifecycleError(ValueError):
    """Raised for an invalid plugin lifecycle operation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PluginStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.is_symlink():
            raise LifecycleError("Plugin database path must not be a symlink.")
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> Path:
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
        return self.db_path

    def _event(
        self,
        connection: sqlite3.Connection,
        name: str,
        event: str,
        actor: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO plugin_events(id,plugin_name,event,actor,occurred_at,details_json)"
            " VALUES(?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                name,
                event,
                actor,
                _now(),
                json.dumps(dict(details or {}), sort_keys=True),
            ),
        )

    def events(self, name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event,actor,occurred_at,details_json FROM plugin_events"
                " WHERE plugin_name=? ORDER BY rowid DESC LIMIT ?",
                (name, max(1, min(limit, 500))),
            ).fetchall()
        return [
            {
                "event": row["event"],
                "actor": row["actor"],
                "occurred_at": row["occurred_at"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def get(self, name: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM plugins WHERE name=?", (name,)
            ).fetchone()
        return None if row is None else dict(row)

    def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT name,version,publisher,status,manifest_digest,updated_at FROM plugins"
        params: tuple[Any, ...] = ()
        if status is not None:
            if status not in STATES:
                raise LifecycleError("Unknown plugin status filter.")
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY name"
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _transition(
        self,
        name: str,
        target: str,
        *,
        actor: str,
        event: str,
        allow_from: Iterable[str] | None = None,
        require_state: str | None = None,
    ) -> dict[str, Any]:
        if not actor.strip():
            raise LifecycleError("A lifecycle change requires an actor identity.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM plugins WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                raise LifecycleError(f"Plugin {name!r} is not installed.")
            current = row["status"]
            if require_state is not None and current != require_state:
                raise LifecycleError(
                    f"Illegal plugin transition: expected {require_state!r}, got {current!r}."
                )
            if allow_from is not None and current not in set(allow_from):
                raise LifecycleError(
                    f"Illegal plugin transition from {current!r} to {target!r}."
                )
            if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                raise LifecycleError(
                    f"Illegal plugin transition {current!r} -> {target!r}."
                )
            connection.execute(
                "UPDATE plugins SET status=?, updated_at=? WHERE name=?",
                (target, _now(), name),
            )
            self._event(connection, name, event, actor, {"from": current, "to": target})
        record = self.get(name)
        assert record is not None
        return record

    def install(
        self,
        manifest: PluginManifest,
        *,
        actor: str,
        verified: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not actor.strip():
            raise LifecycleError("A plugin install requires an actor identity.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM plugins WHERE name=?", (manifest.name,)
            ).fetchone()
            if existing is not None and existing["status"] != "uninstalled":
                raise LifecycleError(f"Plugin {manifest.name!r} is already installed.")
            now = _now()
            connection.execute(
                "INSERT INTO plugins(name,version,publisher,manifest_digest,manifest_json,status,installed_at,updated_at)"
                " VALUES(?,?,?,?,?,'installed',?,?)"
                " ON CONFLICT(name) DO UPDATE SET version=excluded.version,"
                " publisher=excluded.publisher, manifest_digest=excluded.manifest_digest,"
                " manifest_json=excluded.manifest_json, status='installed',"
                " installed_at=excluded.installed_at, updated_at=excluded.updated_at",
                (
                    manifest.name,
                    str(manifest.version),
                    manifest.publisher,
                    manifest.artifact_digest(),
                    json.dumps(manifest.body(), sort_keys=True),
                    now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM plugin_dependencies WHERE plugin_name=?", (manifest.name,)
            )
            for dependency in manifest.dependencies:
                connection.execute(
                    "INSERT INTO plugin_dependencies(plugin_name,dependency_name,specifier) VALUES(?,?,?)",
                    (manifest.name, dependency.name, dependency.specifier),
                )
            if verified:
                connection.execute(
                    "INSERT INTO plugin_trust(plugin_name,publisher,key_id,digest,verified_at)"
                    " VALUES(?,?,?,?,?) ON CONFLICT(plugin_name) DO UPDATE SET"
                    " publisher=excluded.publisher, key_id=excluded.key_id,"
                    " digest=excluded.digest, verified_at=excluded.verified_at",
                    (
                        manifest.name,
                        str(verified.get("publisher", manifest.publisher)),
                        str(verified.get("key_id", "")),
                        str(verified.get("digest", manifest.artifact_digest())),
                        now,
                    ),
                )
            self._event(
                connection,
                manifest.name,
                "installed",
                actor,
                {"version": str(manifest.version), "publisher": manifest.publisher},
            )
        record = self.get(manifest.name)
        assert record is not None
        return record

    def validate(self, name: str, *, actor: str) -> dict[str, Any]:
        record = self.get(name)
        if record is None:
            raise LifecycleError(f"Plugin {name!r} is not installed.")
        with self._connection() as connection:
            trust = connection.execute(
                "SELECT key_id FROM plugin_trust WHERE plugin_name=?", (name,)
            ).fetchone()
        if trust is None:
            raise LifecycleError(
                "A plugin cannot be validated without a verified trust record."
            )
        return self._transition(
            name, "validated", actor=actor, event="validated", require_state="installed"
        )

    def enable(self, name: str, *, actor: str) -> dict[str, Any]:
        record = self.get(name)
        if record is None:
            raise LifecycleError(f"Plugin {name!r} is not installed.")
        self._check_dependencies(name)
        return self._transition(
            name,
            "enabled",
            actor=actor,
            event="enabled",
            allow_from={"validated", "disabled"},
        )

    def _check_dependencies(self, name: str) -> None:
        with self._connection() as connection:
            dependencies = connection.execute(
                "SELECT dependency_name, specifier FROM plugin_dependencies WHERE plugin_name=?",
                (name,),
            ).fetchall()
            for dependency in dependencies:
                dep_name = dependency["dependency_name"]
                row = connection.execute(
                    "SELECT version,status FROM plugins WHERE name=?", (dep_name,)
                ).fetchone()
                if row is None or row["status"] in {"revoked", "uninstalled"}:
                    raise LifecycleError(
                        f"Dependency {dep_name!r} is not installed or has been revoked."
                    )
                if row["status"] not in {"validated", "enabled", "disabled"}:
                    raise LifecycleError(
                        f"Dependency {dep_name!r} is not trusted yet (status {row['status']!r})."
                    )
                try:
                    compatible = satisfies(
                        parse_version(row["version"]), dependency["specifier"]
                    )
                except ManifestError as exc:
                    raise LifecycleError(
                        f"Dependency {dep_name!r} version cannot be checked."
                    ) from exc
                if not compatible:
                    raise LifecycleError(
                        f"Dependency {dep_name!r} {row['version']} does not satisfy"
                        f" {dependency['specifier']!r}."
                    )

    def disable(self, name: str, *, actor: str, reason: str = "") -> dict[str, Any]:
        return self._transition(
            name,
            "disabled",
            actor=actor,
            event="disabled",
            require_state="enabled",
        )

    def revoke(self, name: str, *, actor: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise LifecycleError("Plugin revocation requires a reason.")
        record = self._transition(
            name,
            "revoked",
            actor=actor,
            event="revoked",
            allow_from={"validated", "enabled", "disabled"},
        )
        with self._connection() as connection:
            self._event(connection, name, "revocation_reason", actor, {"reason": reason.strip()[:200]})
        return record

    def uninstall(self, name: str, *, actor: str) -> dict[str, Any]:
        return self._transition(
            name,
            "uninstalled",
            actor=actor,
            event="uninstalled",
            allow_from={"installed", "validated", "disabled", "revoked"},
        )

    def set_grant(self, grant: Grant, *, actor: str) -> None:
        if not actor.strip():
            raise LifecycleError("Setting a grant requires an actor identity.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM plugins WHERE name=?", (grant.plugin_name,)
            ).fetchone() is None:
                raise LifecycleError("Cannot grant capabilities to an unknown plugin.")
            connection.execute(
                "INSERT INTO plugin_grants(plugin_name,payload_json) VALUES(?,?)"
                " ON CONFLICT(plugin_name) DO UPDATE SET payload_json=excluded.payload_json",
                (grant.plugin_name, json.dumps(grant.to_dict(), sort_keys=True)),
            )
            self._event(
                connection,
                grant.plugin_name,
                "grant",
                actor,
                {"capabilities": sorted(grant.capabilities)},
            )

    def get_grant(self, name: str) -> Grant | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM plugin_grants WHERE plugin_name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        try:
            return Grant.from_dict(json.loads(row["payload_json"]))
        except (json.JSONDecodeError, PermissionDenied) as exc:
            raise LifecycleError("Stored plugin grant is unreadable.") from exc

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5, NAMESPACE_OID

from .models import (
    HostRecord,
    ProjectLifecycleState,
    ProjectRecord,
    RepositoryRecord,
    WorkspaceRecord,
    WorkspaceStatus,
)
from .exceptions import (
    MigrationError,
    RegistryCorruptedError,
    RegistryError,
    RegistryIntegrityError,
    RegistryNotFoundError,
)

# ---- P1: deterministic migration ids and patch taxonomy ----

class MigrationPatchKind(str, Enum):
    """Which P1 patch kind a migration call is performing.

    Used for observability and conflict reporting, not for changing how the
    registry stores rows. Canonical migration patch kinds (RT-52 slot 1).
    """

    ID_FALLBACK = "id-fallback"
    WORKSPACE = "workspace"
    REPOSITORY = "repository"


class MigrationWindow:
    """A deterministic, repeatable window into a V1 registry for P1 migration.

    Two calls with the same V1 registry path (resolved to the same identifier)
    produce the same deterministic window id, so the migration result is
    reproducible even when identity fallback ids are involved.
    """

    def __init__(self, v1_registry_path: Path):
        self.v1_registry_path = v1_registry_path
        self.window_id = _migration_id(v1_registry_path)
        self.identifier = _migration_identifier(v1_registry_path)

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "identifier": self.identifier,
            "v1_registry_path": str(self.v1_registry_path),
            "v1_registry_path_resolved": str(self.v1_registry_path.resolve()),
        }


def _stable_file_digest(path: Path) -> str:
    """Content digest of a file, used as the deterministic migration seed.

    Content is used instead of mtime so the seed is reproducible from the
    same evidence; a touched-but-unchanged registry keeps its window id.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _migration_seed(v1_registry_path: Path | str) -> str:
    """Deterministic seed for a V1 registry: resolved path + content digest."""
    path = Path(v1_registry_path)
    resolved = path.resolve()
    if resolved.is_file():
        return f"{resolved}:{_stable_file_digest(resolved)}"
    # Dry-run/preview against a not-yet-created path stays deterministic.
    return f"{resolved}:absent"


def _migration_id(v1_registry_path: Path | str) -> str:
    """Deterministic uuid5 window id for a V1 registry location (RT-52 slot 1)."""
    return _deterministic_uuid5(_migration_seed(v1_registry_path))


def _migration_identifier(v1_registry_path: Path | str) -> str:
    """Human-readable deterministic migration identifier for a V1 registry.

    Display prefix + first 12 hex chars of the deterministic window id so it
    is easy to read in logs without implying a full UUID shape.
    """
    return f"v1-to-v2-{str(uuid5(NAMESPACE_OID, _migration_seed(v1_registry_path)))[:12]}"


def _deterministic_uuid5(seed: str) -> str:
    """Deterministic uuid5 from a stable seed (RT-52 slot 1).

    UUIDv5 from the OID namespace produces version-5 ids (the 13th hex
    digit of the UUID is ``5``); the caller does not depend on a specific
    suffix.
    """
    return str(uuid5(NAMESPACE_OID, seed))


def _fallback_project_id(v1_name: str, v1_path: str) -> str:
    """Deterministic provisional project id for a V1 entry with no identity.json.

    Deterministic by V1 name + resolved normalized path (RT-52, slot 1 and 2).
    Same evidence -> same id across calls and machines that see the same
    resolved path; dry-run does not mutate the seed.
    """
    seed = f"v1-fallback:{v1_name}:{Path(v1_path).resolve()}"
    return _deterministic_uuid5(seed)


def _fallback_workspace_id(v1_name: str, v1_path: str, fallback_project_id: str) -> str:
    """Deterministic workspace id for a migrated V1 project."""
    seed = f"v1-ws:{v1_name}:{Path(v1_path).resolve()}:{fallback_project_id}"
    return _deterministic_uuid5(seed)


def _fallback_repository_id(v1_name: str, remote_url: str, fallback_project_id: str) -> str:
    """Deterministic repository id for a detected git remote during migration."""
    seed = f"v1-repo:{v1_name}:{remote_url}:{fallback_project_id}"
    return _deterministic_uuid5(seed)


SCHEMA_VERSION = 2
DB_FILENAME = "registry.db"
MIGRATION_BACKUP_SUFFIX = ".migration-backup"
MIGRATION_PREVIEW_SUFFIX = ".migration-preview"



class RegistryV2:
    """
    Universal Registry V2 - SQLite-backed project management database.
    
    Supports projects, repositories, workspaces, and hosts with
    relational integrity and transactional safety.
    """
    
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            # Default: OGX_HOME/registry/registry.db
            from ..config import state_dir
            self.db_path = state_dir() / "registry" / DB_FILENAME
        else:
            self.db_path = Path(db_path)
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._initialized = False
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return self._local.conn
    
    def _close_connection(self) -> None:
        """Close thread-local connection."""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None
    
    @contextmanager
    def transaction(self):
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    def initialize(self) -> None:
        """Initialize database schema."""
        if self._initialized:
            return
        
        conn = self._get_connection()
        with conn:
            self._create_schema(conn)
            self._ensure_host_columns(conn)
            self._ensure_schema_version(conn)
        self._initialized = True

    @staticmethod
    def _ensure_host_columns(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial hosts table (idempotent)."""

        columns = {row[1] for row in conn.execute("PRAGMA table_info(hosts)")}
        additions = {
            "display_name": "TEXT DEFAULT ''",
            "last_seen_at": "TEXT DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE hosts ADD COLUMN {name} {declaration}")
    
    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create database tables and indexes."""
        # Projects table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                description TEXT DEFAULT '',
                identity_hash TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL DEFAULT 'active',
                metadata TEXT DEFAULT '{}'
            )
        """)
        
        # Repositories table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                repository_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                repository_url TEXT NOT NULL,
                normalized_repository_url TEXT NOT NULL,
                repository_name TEXT DEFAULT '',
                default_branch TEXT DEFAULT '',
                visibility TEXT DEFAULT 'unknown',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
            )
        """)
        
        # Workspaces table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                repository_id TEXT,
                workspace_provider TEXT DEFAULT 'local',
                workspace_type TEXT DEFAULT 'local_fs',
                location_reference TEXT NOT NULL,
                normalized_location TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                FOREIGN KEY (repository_id) REFERENCES repositories(repository_id) ON DELETE SET NULL
            )
        """)
        
        # Hosts table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hosts (
                host_id TEXT PRIMARY KEY,
                host_type TEXT NOT NULL,
                provider TEXT DEFAULT '',
                host_identifier TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                last_seen_at TEXT DEFAULT '',
                status TEXT DEFAULT 'unknown',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Project-Host many-to-many relationship
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_hosts (
                project_id TEXT NOT NULL,
                host_id TEXT NOT NULL,
                PRIMARY KEY (project_id, host_id),
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                FOREIGN KEY (host_id) REFERENCES hosts(host_id) ON DELETE CASCADE
            )
        """)
        
        # Schema version table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        
        # Indexes for common queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(canonical_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_state ON projects(lifecycle_state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_project ON repositories(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_normalized_url ON repositories(normalized_repository_url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_project ON workspaces(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_repo ON workspaces(repository_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_normalized ON workspaces(normalized_location)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hosts_type ON hosts(host_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_project_hosts_host ON project_hosts(host_id)")
    
    def _ensure_schema_version(self, conn: sqlite3.Connection) -> None:
        """Ensure schema version is recorded."""
        existing = conn.execute("SELECT version FROM schema_version").fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat())
            )
        elif existing[0] != SCHEMA_VERSION:
            raise RegistryCorruptedError(
                f"Schema version mismatch: expected {SCHEMA_VERSION}, found {existing[0]}",
                path=str(self.db_path)
            )
    
    def get_schema_version(self) -> int:
        """Get current schema version."""
        self.initialize()
        conn = self._get_connection()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    
    # ==================== Project Operations ====================
    
    def register_project(
        self,
        project_id: UUID | str,
        canonical_name: str,
        description: str = "",
        identity_hash: str = "",
        lifecycle_state: ProjectLifecycleState = ProjectLifecycleState.ACTIVE,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectRecord:
        """Register a new project or update existing one."""
        self.initialize()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        now = datetime.now(timezone.utc).isoformat()
        
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (str(pid),)
            ).fetchone()
            
            if existing:
                # Update existing
                conn.execute("""
                    UPDATE projects SET
                        canonical_name = ?,
                        description = ?,
                        identity_hash = ?,
                        lifecycle_state = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE project_id = ?
                """, (
                    canonical_name,
                    description,
                    identity_hash,
                    lifecycle_state.value,
                    json.dumps(metadata or {}),
                    now,
                    str(pid)
                ))
            else:
                # Insert new
                conn.execute("""
                    INSERT INTO projects (
                        project_id, canonical_name, description, identity_hash,
                        created_at, updated_at, lifecycle_state, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(pid),
                    canonical_name,
                    description,
                    identity_hash,
                    now,
                    now,
                    lifecycle_state.value,
                    json.dumps(metadata or {})
                ))
        
        return self.get_project(pid)
    
    def get_project(self, project_id: UUID | str) -> ProjectRecord:
        """Get project by ID."""
        self.initialize()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        conn = self._get_connection()
        
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (str(pid),)
        ).fetchone()
        
        if not row:
            raise RegistryNotFoundError(
                f"Project not found: {pid}",
                entity_type="project",
                entity_id=str(pid)
            )
        
        return self._row_to_project(row)
    
    def find_project_by_identity(
        self,
        project_id: UUID | str | None = None,
        canonical_name: str | None = None,
        identity_hash: str | None = None,
    ) -> ProjectRecord | None:
        """Find project by identity evidence (any available)."""
        self.initialize()
        
        conn = self._get_connection()
        query = "SELECT * FROM projects WHERE 1=1"
        params = []
        
        if project_id:
            pid = UUID(project_id) if isinstance(project_id, str) else project_id
            query += " AND project_id = ?"
            params.append(str(pid))
        
        if canonical_name:
            query += " AND canonical_name = ?"
            params.append(canonical_name)
        
        if identity_hash:
            query += " AND identity_hash = ?"
            params.append(identity_hash)
        
        query += " ORDER BY updated_at DESC LIMIT 1"
        
        row = conn.execute(query, params).fetchone()
        return self._row_to_project(row) if row else None
    
    def list_projects(
        self,
        lifecycle_state: ProjectLifecycleState | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectRecord]:
        """List projects with optional filtering."""
        self.initialize()
        
        conn = self._get_connection()
        query = "SELECT * FROM projects WHERE 1=1"
        params = []
        
        if lifecycle_state:
            query += " AND lifecycle_state = ?"
            params.append(lifecycle_state.value)
        
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_project(row) for row in rows]
    
    def update_project(
        self,
        project_id: UUID | str,
        canonical_name: str | None = None,
        description: str | None = None,
        identity_hash: str | None = None,
        lifecycle_state: ProjectLifecycleState | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectRecord:
        """Update project fields."""
        self.initialize()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        
        # Get current
        current = self.get_project(pid)
        
        now = datetime.now(timezone.utc).isoformat()
        
        with self.transaction() as conn:
            conn.execute("""
                UPDATE projects SET
                    canonical_name = COALESCE(?, canonical_name),
                    description = COALESCE(?, description),
                    identity_hash = COALESCE(?, identity_hash),
                    lifecycle_state = COALESCE(?, lifecycle_state),
                    metadata = COALESCE(?, metadata),
                    updated_at = ?
                WHERE project_id = ?
            """, (
                canonical_name,
                description,
                identity_hash,
                lifecycle_state.value if lifecycle_state else None,
                json.dumps(metadata) if metadata is not None else None,
                now,
                str(pid)
            ))
        
        return self.get_project(pid)
    
    def archive_project(self, project_id: UUID | str) -> ProjectRecord:
        """Archive a project (mark as archived)."""
        return self.update_project(
            project_id,
            lifecycle_state=ProjectLifecycleState.ARCHIVED
        )
    
    def delete_project(self, project_id: UUID | str) -> bool:
        """Soft delete a project (mark as deleted)."""
        try:
            self.update_project(
                project_id,
                lifecycle_state=ProjectLifecycleState.DELETED
            )
            return True
        except RegistryNotFoundError:
            return False
    
    # ==================== Repository Operations ====================
    
    def register_repository(
        self,
        repository_id: UUID | str,
        project_id: UUID | str,
        provider: str,
        repository_url: str,
        normalized_repository_url: str,
        repository_name: str = "",
        default_branch: str = "",
        visibility: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> RepositoryRecord:
        """Register a repository for a project."""
        self.initialize()
        
        rid = UUID(repository_id) if isinstance(repository_id, str) else repository_id
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        now = datetime.now(timezone.utc).isoformat()
        
        # Verify project exists
        self.get_project(pid)
        
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT repository_id FROM repositories WHERE repository_id = ?",
                (str(rid),)
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE repositories SET
                        project_id = ?,
                        provider = ?,
                        repository_url = ?,
                        normalized_repository_url = ?,
                        repository_name = ?,
                        default_branch = ?,
                        visibility = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE repository_id = ?
                """, (
                    str(pid), provider, repository_url, normalized_repository_url,
                    repository_name, default_branch, visibility,
                    json.dumps(metadata or {}), now, str(rid)
                ))
            else:
                conn.execute("""
                    INSERT INTO repositories (
                        repository_id, project_id, provider, repository_url,
                        normalized_repository_url, repository_name, default_branch,
                        visibility, metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(rid), str(pid), provider, repository_url,
                    normalized_repository_url, repository_name, default_branch,
                    visibility, json.dumps(metadata or {}), now, now
                ))
        
        return self.get_repository(rid)
    
    def get_repository(self, repository_id: UUID | str) -> RepositoryRecord:
        """Get repository by ID."""
        self.initialize()
        
        rid = UUID(repository_id) if isinstance(repository_id, str) else repository_id
        conn = self._get_connection()
        
        row = conn.execute(
            "SELECT * FROM repositories WHERE repository_id = ?",
            (str(rid),)
        ).fetchone()
        
        if not row:
            raise RegistryNotFoundError(
                f"Repository not found: {rid}",
                entity_type="repository",
                entity_id=str(rid)
            )
        
        return self._row_to_repository(row)
    
    def find_repository_by_normalized_url(
        self,
        normalized_url: str,
    ) -> RepositoryRecord | None:
        """Find repository by normalized URL."""
        self.initialize()
        
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM repositories WHERE normalized_repository_url = ? ORDER BY updated_at DESC LIMIT 1",
            (normalized_url,)
        ).fetchone()
        
        return self._row_to_repository(row) if row else None
    
    def list_repositories(
        self,
        project_id: UUID | str | None = None,
    ) -> list[RepositoryRecord]:
        """List repositories, optionally filtered by project."""
        self.initialize()
        
        conn = self._get_connection()
        
        if project_id:
            pid = UUID(project_id) if isinstance(project_id, str) else project_id
            rows = conn.execute(
                "SELECT * FROM repositories WHERE project_id = ? ORDER BY updated_at DESC",
                (str(pid),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM repositories ORDER BY updated_at DESC"
            ).fetchall()
        
        return [self._row_to_repository(row) for row in rows]
    
    def get_repositories_for_project(self, project_id: UUID | str) -> list[RepositoryRecord]:
        """Get all repositories for a project."""
        return self.list_repositories(project_id)
    
    # ==================== Workspace Operations ====================
    
    def register_workspace(
        self,
        workspace_id: UUID | str,
        project_id: UUID | str,
        location_reference: str,
        normalized_location: str,
        repository_id: UUID | str | None = None,
        workspace_provider: str = "local",
        workspace_type: str = "local_fs",
        display_name: str = "",
        status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceRecord:
        """Register a workspace for a project."""
        self.initialize()
        
        wid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        rid = UUID(repository_id) if repository_id and isinstance(repository_id, str) else repository_id
        now = datetime.now(timezone.utc).isoformat()
        
        # Verify project exists
        self.get_project(pid)
        if rid:
            self.get_repository(rid)
        
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT workspace_id FROM workspaces WHERE workspace_id = ?",
                (str(wid),)
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE workspaces SET
                        project_id = ?,
                        repository_id = ?,
                        workspace_provider = ?,
                        workspace_type = ?,
                        location_reference = ?,
                        normalized_location = ?,
                        display_name = ?,
                        status = ?,
                        last_seen_at = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE workspace_id = ?
                """, (
                    str(pid),
                    str(rid) if rid else None,
                    workspace_provider,
                    workspace_type,
                    location_reference,
                    normalized_location,
                    display_name,
                    status.value,
                    now,
                    json.dumps(metadata or {}),
                    now,
                    str(wid)
                ))
            else:
                conn.execute("""
                    INSERT INTO workspaces (
                        workspace_id, project_id, repository_id,
                        workspace_provider, workspace_type, location_reference,
                        normalized_location, display_name, status,
                        last_seen_at, created_at, updated_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(wid), str(pid), str(rid) if rid else None,
                    workspace_provider, workspace_type, location_reference,
                    normalized_location, display_name, status.value,
                    now, now, now, json.dumps(metadata or {})
                ))
        
        return self.get_workspace(wid)
    
    def get_workspace(self, workspace_id: UUID | str) -> WorkspaceRecord:
        """Get workspace by ID."""
        self.initialize()
        
        wid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        conn = self._get_connection()
        
        row = conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (str(wid),)
        ).fetchone()
        
        if not row:
            raise RegistryNotFoundError(
                f"Workspace not found: {wid}",
                entity_type="workspace",
                entity_id=str(wid)
            )
        
        return self._row_to_workspace(row)
    
    def find_workspace_by_normalized_location(
        self,
        normalized_location: str,
    ) -> WorkspaceRecord | None:
        """Find workspace by normalized location."""
        self.initialize()
        
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM workspaces WHERE normalized_location = ? ORDER BY last_seen_at DESC LIMIT 1",
            (normalized_location,)
        ).fetchone()
        
        return self._row_to_workspace(row) if row else None
    
    def list_workspaces(
        self,
        project_id: UUID | str | None = None,
        status: WorkspaceStatus | None = None,
    ) -> list[WorkspaceRecord]:
        """List workspaces with optional filtering."""
        self.initialize()
        
        conn = self._get_connection()
        query = "SELECT * FROM workspaces WHERE 1=1"
        params = []
        
        if project_id:
            pid = UUID(project_id) if isinstance(project_id, str) else project_id
            query += " AND project_id = ?"
            params.append(str(pid))
        
        if status:
            query += " AND status = ?"
            params.append(status.value)
        
        query += " ORDER BY last_seen_at DESC"
        
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_workspace(row) for row in rows]
    
    def update_workspace_location(
        self,
        workspace_id: UUID | str,
        location_reference: str,
        normalized_location: str,
    ) -> WorkspaceRecord:
        """Update workspace location (for relocation)."""
        self.initialize()
        
        wid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        now = datetime.now(timezone.utc).isoformat()
        
        with self.transaction() as conn:
            conn.execute("""
                UPDATE workspaces SET
                    location_reference = ?,
                    normalized_location = ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE workspace_id = ?
            """, (location_reference, normalized_location, now, now, str(wid)))
        
        return self.get_workspace(wid)
    
    def update_workspace_status(
        self,
        workspace_id: UUID | str,
        status: WorkspaceStatus,
    ) -> WorkspaceRecord:
        """Update workspace status."""
        self.initialize()
        
        wid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        now = datetime.now(timezone.utc).isoformat()
        
        with self.transaction() as conn:
            conn.execute("""
                UPDATE workspaces SET
                    status = ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE workspace_id = ?
            """, (status.value, now, now, str(wid)))
        
        return self.get_workspace(wid)
    
    def update_workspace_last_seen(self, workspace_id: UUID | str) -> WorkspaceRecord:
        """Update workspace last_seen_at to now."""
        return self.update_workspace_status(workspace_id, WorkspaceStatus.ACTIVE)
    
    def remove_workspace(self, workspace_id: UUID | str) -> bool:
        """Remove a workspace (soft delete by marking as archived)."""
        try:
            self.update_workspace_status(workspace_id, WorkspaceStatus.ARCHIVED)
            return True
        except RegistryNotFoundError:
            return False
    
    # ==================== Host Operations ====================
    
    def register_host(
        self,
        host_id: UUID | str,
        host_type: str,
        provider: str = "",
        host_identifier: str = "",
        status: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> HostRecord:
        """Register a host."""
        self.initialize()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        now = datetime.now(timezone.utc).isoformat()
        
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT host_id FROM hosts WHERE host_id = ?",
                (str(hid),)
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE hosts SET
                        host_type = ?,
                        provider = ?,
                        host_identifier = ?,
                        status = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE host_id = ?
                """, (host_type, provider, host_identifier, status, json.dumps(metadata or {}), now, str(hid)))
            else:
                conn.execute("""
                    INSERT INTO hosts (
                        host_id, host_type, provider, host_identifier,
                        status, metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (str(hid), host_type, provider, host_identifier, status, json.dumps(metadata or {}), now, now))
        
        return self.get_host(hid)
    
    def get_host(self, host_id: UUID | str) -> HostRecord:
        """Get host by ID."""
        self.initialize()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        conn = self._get_connection()
        
        row = conn.execute(
            "SELECT * FROM hosts WHERE host_id = ?",
            (str(hid),)
        ).fetchone()
        
        if not row:
            raise RegistryNotFoundError(
                f"Host not found: {hid}",
                entity_type="host",
                entity_id=str(hid)
            )
        
        return self._row_to_host(row)
    
    def link_project_host(self, project_id: UUID | str, host_id: UUID | str) -> None:
        """Link project to host (many-to-many)."""
        self.initialize()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        
        self.get_project(pid)
        self.get_host(hid)
        
        with self.transaction() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO project_hosts (project_id, host_id)
                VALUES (?, ?)
            """, (str(pid), str(hid)))
    
    def get_hosts_for_project(self, project_id: UUID | str) -> list[HostRecord]:
        """Get all hosts linked to a project."""
        self.initialize()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        conn = self._get_connection()
        
        rows = conn.execute("""
            SELECT h.* FROM hosts h
            JOIN project_hosts ph ON h.host_id = ph.host_id
            WHERE ph.project_id = ?
        """, (str(pid),)).fetchall()
        
        return [self._row_to_host(row) for row in rows]
    
    # ==================== Migration Operations ====================
    
    def migrate_from_v1(self, v1_registry_path: Path, dry_run: bool = False) -> dict[str, Any]:
        """Migrate from Registry V1 (projects.json) to Registry V2.

        Deterministic migration ids are derived from the V1 registry location
        and each entry's (name, resolved path) so repeated migrations of the
        same, unmodified V1 registry produce the same ids (RT-52 slot 1-2).
        v1_registry_path must be provided by the caller.
        """
        resolved = Path(v1_registry_path).resolve()
        if not resolved.exists():
            raise MigrationError(
                f"Registry V1 not found: {v1_registry_path}",
                stage="validate"
            )

        with open(resolved, "r", encoding="utf-8") as handle:
            v1_data = json.load(handle)

        if not isinstance(v1_data, dict):
            raise MigrationError(
                "Registry V1 must be a dictionary",
                stage="validate"
            )

        window = MigrationWindow(resolved)
        preview: dict[str, Any] = {
            "projects_to_migrate": len(v1_data),
            "projects": [],
            "warnings": [],
            "window": window.summary,
        }

        from ..pap import read_identity, IdentityMissingError, IdentityCorruptedError

        for name, path_str in v1_data.items():
            project_path = Path(path_str)
            project_info: dict[str, Any] = {
                "v1_name": name,
                "v1_path": path_str,
                "project_id": None,
                "identity_found": False,
                "identity_hash": None,
                "git_remote": None,
            }

            try:
                identity = read_identity(project_path)
                project_info["project_id"] = str(identity.project_id)
                project_info["identity_found"] = True
                project_info["identity_hash"] = identity.identity_hash
            except (IdentityMissingError, IdentityCorruptedError):
                project_info["project_id"] = _fallback_project_id(name, path_str)
                project_info["provisional"] = True

            from ..connectors.git import inspect_repository
            try:
                git_info = inspect_repository(project_path)
                if git_info.get("remotes"):
                    for _remote_name, remote_info in git_info["remotes"].items():
                        project_info["git_remote"] = remote_info.get("url")
                        break
            except Exception:
                pass

            preview["projects"].append(project_info)

        if dry_run:
            return {"dry_run": True, "preview": preview}

        return self._apply_migration(v1_data, preview)

    def _apply_migration(
        self,
        v1_data: dict[str, str],
        preview: dict[str, Any],
        *,
        patch_kinds: list[MigrationPatchKind] | None = None,
    ) -> dict[str, Any]:
        """Apply the V1->V2 migration to the database.

        Deterministic ids are derived from the V1 entry inputs, so repeated
        migrations of the same V1 registry reuse the same project/workspace/
        repository ids and are idempotent by registration upsert.

        backup_path is derived from the resolved V1 path carried in the
        preview window (not from a ``Path("")`` default).
        """
        resolved = Path(preview["window"]["v1_registry_path_resolved"])
        patch_kinds: set[MigrationPatchKind] = set(patch_kinds or [])
        if any(info.get("provisional") for info in preview["projects"]):
            patch_kinds.add(MigrationPatchKind.ID_FALLBACK)

        backup_path: Path | None = None
        if resolved.exists():
            backup_path = resolved.with_suffix(MIGRATION_BACKUP_SUFFIX)
            shutil.copy2(resolved, backup_path)

        migrated = 0
        errors: list[dict[str, Any]] = []

        for project_info in preview["projects"]:
            try:
                project_id = UUID(project_info["project_id"])

                self.register_project(
                    project_id=project_id,
                    canonical_name=project_info["v1_name"],
                    identity_hash=project_info.get("identity_hash") or "",
                    lifecycle_state=ProjectLifecycleState.ACTIVE,
                )

                location = project_info["v1_path"]
                normalized = str(Path(location).resolve())
                workspace_id = UUID(
                    project_info.get("workspace_id")
                    or _fallback_workspace_id(
                        project_info["v1_name"], location, project_info["project_id"]
                    )
                )

                self.register_workspace(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    location_reference=location,
                    normalized_location=normalized,
                    display_name=project_info["v1_name"],
                )
                patch_kinds.add(MigrationPatchKind.WORKSPACE)

                git_remote = project_info.get("git_remote")
                if git_remote:
                    from ..connectors.git import detect_provider

                    provider = detect_provider(git_remote)
                    normalized_url = self._normalize_git_url(git_remote)
                    repo_id = UUID(
                        _fallback_repository_id(
                            project_info["v1_name"], git_remote, project_info["project_id"]
                        )
                    )

                    self.register_repository(
                        repository_id=repo_id,
                        project_id=project_info["project_id"],
                        provider=provider,
                        repository_url=git_remote,
                        normalized_repository_url=normalized_url,
                    )
                    self._update_workspace_repo(workspace_id, repo_id)
                    patch_kinds.add(MigrationPatchKind.REPOSITORY)

                migrated += 1
            except Exception as exc:
                errors.append({
                    "project": project_info["v1_name"],
                    "error": str(exc),
                })

        return {
            "dry_run": False,
            "migrated": migrated,
            "total": len(v1_data),
            "errors": errors,
            "backup_path": str(backup_path) if backup_path else None,
            "patch_kinds": sorted(k.value for k in patch_kinds),
            "provisional_projects": sorted(
                info["v1_name"] for info in preview["projects"] if info.get("provisional")
            ),
            "window": preview.get("window"),
        }
    
    def _normalize_git_url(self, url: str) -> str:
        """Normalize Git URL for comparison."""
        from urllib.parse import urlparse, urlunsplit
        
        try:
            parsed = urlparse(url if "://" in url else "https://" + url)
            host = parsed.hostname or ""
            if parsed.port:
                host += f":{parsed.port}"
            clean = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            return clean.lower().rstrip(".git")
        except Exception:
            return url.lower().rstrip(".git")
    
    def _update_workspace_repo(self, workspace_id: UUID, repository_id: UUID) -> None:
        """Update workspace with repository reference."""
        self.initialize()
        
        conn = self._get_connection()
        conn.execute(
            "UPDATE workspaces SET repository_id = ?, updated_at = ? WHERE workspace_id = ?",
            (str(repository_id), datetime.now(timezone.utc).isoformat(), str(workspace_id))
        )
        conn.commit()
    
    # ==================== Backup and Recovery ====================
    
    def backup(self, backup_path: Path | None = None) -> Path:
        """Create a backup of the registry database."""
        if backup_path is None:
            backup_path = self.db_path.parent / f"registry-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
        
        # Use SQLite backup API for consistent backup
        src_conn = self._get_connection()
        dst_conn = sqlite3.connect(backup_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
        
        return backup_path
    
    def check_integrity(self) -> dict[str, Any]:
        """Check database integrity."""
        self.initialize()
        
        conn = self._get_connection()
        
        # Run integrity check
        integrity_result = conn.execute("PRAGMA integrity_check").fetchone()
        
        # Check foreign key constraints
        fk_result = conn.execute("PRAGMA foreign_key_check").fetchall()
        
        # Count records
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        repo_count = conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
        workspace_count = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        host_count = conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0]
        
        return {
            "integrity_ok": integrity_result[0] == "ok",
            "integrity_details": integrity_result[0],
            "foreign_key_violations": len(fk_result),
            "fk_details": fk_result,
            "counts": {
                "projects": project_count,
                "repositories": repo_count,
                "workspaces": workspace_count,
                "hosts": host_count,
            },
            "schema_version": self.get_schema_version(),
        }
    
    def vacuum(self) -> None:
        """Optimize database."""
        self.initialize()
        conn = self._get_connection()
        conn.execute("VACUUM")
        conn.commit()
    
    # ==================== Helper Methods ====================
    
    def _row_to_project(self, row: sqlite3.Row) -> ProjectRecord:
        """Convert database row to ProjectRecord."""
        return ProjectRecord(
            project_id=UUID(row["project_id"]),
            canonical_name=row["canonical_name"],
            description=row["description"],
            identity_hash=row["identity_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            lifecycle_state=ProjectLifecycleState(row["lifecycle_state"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
    
    def _row_to_repository(self, row: sqlite3.Row) -> RepositoryRecord:
        """Convert database row to RepositoryRecord."""
        return RepositoryRecord(
            repository_id=UUID(row["repository_id"]),
            project_id=UUID(row["project_id"]),
            provider=row["provider"],
            repository_url=row["repository_url"],
            normalized_repository_url=row["normalized_repository_url"],
            repository_name=row["repository_name"],
            default_branch=row["default_branch"],
            visibility=row["visibility"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    
    def _row_to_workspace(self, row: sqlite3.Row) -> WorkspaceRecord:
        """Convert database row to WorkspaceRecord."""
        return WorkspaceRecord(
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            repository_id=UUID(row["repository_id"]) if row["repository_id"] else None,
            workspace_provider=row["workspace_provider"],
            workspace_type=row["workspace_type"],
            location_reference=row["location_reference"],
            normalized_location=row["normalized_location"],
            display_name=row["display_name"],
            status=WorkspaceStatus(row["status"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
    
    def _row_to_host(self, row: sqlite3.Row) -> HostRecord:
        """Convert database row to HostRecord."""
        return HostRecord(
            host_id=UUID(row["host_id"]),
            host_type=row["host_type"],
            provider=row["provider"],
            host_identifier=row["host_identifier"],
            status=row["status"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    
    def close(self) -> None:
        """Close database connections."""
        self._close_connection()


# Global registry instance for convenience
_global_registry: RegistryV2 | None = None


def get_registry() -> RegistryV2:
    """Get or create global registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = RegistryV2()
        _global_registry.initialize()
    return _global_registry
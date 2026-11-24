from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from .models import (
    HostRecord,
    HostType,
    ProviderType,
    HostStatus,
    HostCapability,
    HostCapabilitySet,
)
from .capabilities import CAPABILITY_MATRIX, get_host_capabilities

from ..registry import get_registry, RegistryV2
from ..registry.models import HostRecord as RegistryHostRecord


class HostRegistry:
    """
    Universal Host Registry - manages host records and provider relationships.
    
    Integrates with Registry V2 for persistence.
    """

    def __init__(self, registry: Optional[RegistryV2] = None):
        self._registry = registry or get_registry()
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}

    # ==================== Host Operations ====================

    def register_host(
        self,
        host_id: UUID | str,
        host_type: str,
        provider: str = "local",
        display_name: str = "",
        normalized_identifier: str = "",
        metadata: Optional[dict] = None,
    ) -> HostRecord:
        """Register or update a host."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        now = datetime.now(timezone.utc).isoformat()
        
        # Determine host type and capabilities
        try:
            host_type = HostType(host_type)
        except ValueError:
            host_type = HostType.UNKNOWN
        
        try:
            provider_type = ProviderType(provider)
        except ValueError:
            provider_type = ProviderType.LOCAL
        
        # Get default capabilities for host type
        caps_dict = CAPABILITY_MATRIX.get_all_capabilities(host_type.value if isinstance(host_type, HostType) else host_type)
        capabilities = [HostCapability(cap) for cap, status in caps_dict.items() if status == "supported"]
        
        host = HostRecord(
            host_id=hid,
            host_type=host_type,
            provider=provider_type,
            display_name=display_name or host_type.value,
            normalized_identifier=normalized_identifier,
            capabilities=capabilities,
            status=HostStatus.ACTIVE,
            metadata=metadata or {},
        )
        
        # Persist to registry
        with self._registry.transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO hosts (
                    host_id, host_type, provider, host_identifier, status,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(host.host_id),
                host.host_type.value,
                host.provider.value,
                host.normalized_identifier,
                host.status.value,
                json.dumps(host.metadata),
                host.created_at.isoformat(),
                host.updated_at.isoformat(),
            ))
        
        return host

    def get_host(self, host_id: UUID | str) -> dict:
        """Get host by ID."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        conn = self._registry._get_connection()
        
        row = conn.execute(
            "SELECT * FROM hosts WHERE host_id = ?",
            (str(hid),)
        ).fetchone()
        
        if not row:
            raise KeyError(f"Host not found: {hid}")
        
        return self._row_to_host(row)

    def find_host_by_identifier(self, identifier: str) -> Optional[dict]:
        """Find host by normalized identifier."""
        self._ensure_initialized()
        
        conn = self._registry._get_connection()
        row = conn.execute(
            "SELECT * FROM hosts WHERE host_identifier = ? ORDER BY updated_at DESC LIMIT 1",
            (identifier,)
        ).fetchone()
        
        return self._row_to_host(row) if row else None

    def find_hosts_by_type(self, host_type: str) -> List[dict]:
        """Find all hosts of a given type."""
        self._ensure_initialized()
        
        conn = self._registry._get_connection()
        rows = conn.execute(
            "SELECT * FROM hosts WHERE host_type = ? ORDER BY updated_at DESC",
            (host_type,)
        ).fetchall()
        
        return [self._row_to_host(row) for row in rows]

    def find_hosts_by_provider(self, provider: str) -> List[dict]:
        """Find all hosts for a given provider."""
        self._ensure_initialized()
        
        conn = self._registry._get_connection()
        rows = conn.execute(
            "SELECT * FROM hosts WHERE provider = ? ORDER BY updated_at DESC",
            (provider,)
        ).fetchall()
        
        return [self._row_to_host(row) for row in rows]

    def list_hosts(
        self,
        host_type: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict]:
        """List hosts with optional filtering."""
        self._ensure_initialized()
        
        conn = self._registry._get_connection()
        query = "SELECT * FROM hosts WHERE 1=1"
        params = []
        
        if host_type:
            query += " AND host_type = ?"
            params.append(host_type)
        
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_host(row) for row in rows]

    def update_host(
        self,
        host_id: UUID | str,
        host_type: Optional[str] = None,
        provider: Optional[str] = None,
        display_name: Optional[str] = None,
        normalized_identifier: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Update host fields."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        now = datetime.now(timezone.utc).isoformat()
        
        # Get current
        current = self.get_host(hid)
        
        with self._registry.transaction() as conn:
            conn.execute("""
                UPDATE hosts SET
                    host_type = COALESCE(?, host_type),
                    provider = COALESCE(?, provider),
                    display_name = COALESCE(?, display_name),
                    host_identifier = COALESCE(?, host_identifier),
                    status = COALESCE(?, status),
                    metadata = COALESCE(?, metadata),
                    updated_at = ?
                WHERE host_id = ?
            """, (
                host_type,
                provider,
                display_name,
                normalized_identifier,
                status,
                json.dumps(metadata) if metadata is not None else None,
                now,
                str(host_id)
            ))
        
        return self.get_host(hid)

    def update_host_status(self, host_id: UUID | str, status: str) -> dict:
        """Update host status."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        now = datetime.now(timezone.utc).isoformat()
        
        with self._registry.transaction() as conn:
            conn.execute("""
                UPDATE hosts SET
                    status = ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE host_id = ?
            """, (status, now, now, str(host_id)))
        
        return self.get_host(hid)

    def update_host_last_seen(self, host_id: UUID | str) -> dict:
        """Update host last_seen_at to now."""
        return self.update_host_status(host_id, "active")

    def remove_host(self, host_id: UUID | str) -> bool:
        """Remove a host (soft delete by marking inactive)."""
        try:
            self.update_host_status(host_id, "inactive")
            return True
        except KeyError:
            return False

    def get_host_capabilities(self, host_id: UUID | str) -> Dict[str, str]:
        """Get capabilities for a host."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        host = self.get_host(hid)
        
        host_type = host.get("host_type", "unknown")
        return CAPABILITY_MATRIX.get_all_capabilities(host_type)

    def get_host_supported_capabilities(self, host_id: UUID | str) -> List[str]:
        """Get list of supported capabilities for a host."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        host = self.get_host(hid)
        
        host_type = host.get("host_type", "unknown")
        return CAPABILITY_MATRIX.get_supported_capabilities(host.get("host_type", "unknown"))

    def get_host_requires_auth_capabilities(self, host_id: UUID | str) -> List[str]:
        """Get capabilities requiring authentication for a host."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        host = self.get_host(hid)
        
        host_type = host.get("host_type", "unknown")
        return CAPABILITY_MATRIX.get_requires_auth_capabilities(host_type)

    # ==================== Project-Host Relationships ====================

    def link_project_host(self, project_id: UUID | str, host_id: UUID | str) -> None:
        """Link project to host (many-to-many)."""
        self._ensure_initialized()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        
        # Verify both exist
        from ..registry import get_registry
        registry = get_registry()
        registry.get_project(pid)
        self.get_host(hid)
        
        with self._registry.transaction() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO project_hosts (project_id, host_id)
                VALUES (?, ?)
            """, (str(pid), str(hid)))

    def unlink_project_host(self, project_id: UUID | str, host_id: UUID | str) -> None:
        """Unlink project from host."""
        self._ensure_initialized()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        
        with self._registry.transaction() as conn:
            conn.execute("""
                DELETE FROM project_hosts WHERE project_id = ? AND host_id = ?
            """, (str(pid), str(hid)))

    def get_hosts_for_project(self, project_id: UUID | str) -> List[dict]:
        """Get all hosts linked to a project."""
        self._ensure_initialized()
        
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        conn = self._registry._get_connection()
        
        rows = conn.execute("""
            SELECT h.* FROM hosts h
            JOIN project_hosts ph ON h.host_id = ph.host_id
            WHERE ph.project_id = ?
        """, (str(pid),)).fetchall()
        
        return [self._row_to_host(row) for row in rows]

    def get_projects_for_host(self, host_id: UUID | str) -> List[dict]:
        """Get all projects linked to a host."""
        self._ensure_initialized()
        
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        conn = self._registry._get_connection()
        
        from ..registry import get_registry
        registry = get_registry()
        
        rows = conn.execute("""
            SELECT p.* FROM projects p
            JOIN project_hosts ph ON p.project_id = ph.project_id
            WHERE ph.host_id = ?
        """, (str(hid),)).fetchall()
        
        projects = []
        for row in rows:
            try:
                projects.append(registry._row_to_project(row))
            except Exception:
                pass
        
        return projects

    # ==================== Repository-Host Relationships ====================

    def link_repository_host(
        self,
        repository_id: UUID | str,
        host_id: UUID | str,
        is_primary: bool = False,
    ) -> None:
        """Link repository to its host."""
        self._ensure_initialized()
        
        rid = UUID(repository_id) if isinstance(repository_id, str) else repository_id
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        
        with self._registry.transaction() as conn:
            # Check if table exists, add column if needed
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repository_hosts (
                    repository_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    is_primary BOOLEAN DEFAULT 0,
                    PRIMARY KEY (repository_id, host_id),
                    FOREIGN KEY (repository_id) REFERENCES repositories(repository_id) ON DELETE CASCADE,
                    FOREIGN KEY (host_id) REFERENCES hosts(host_id) ON DELETE CASCADE
                )
            """)
            
            conn.execute("""
                INSERT OR REPLACE INTO repository_hosts (repository_id, host_id, is_primary)
                VALUES (?, ?, ?)
            """, (str(rid), str(hid), is_primary))

    def get_hosts_for_repository(self, repository_id: UUID | str) -> List[dict]:
        """Get hosts for a repository."""
        self._ensure_initialized()
        
        rid = UUID(repository_id) if isinstance(repository_id, str) else repository_id
        conn = self._registry._get_connection()
        
        rows = conn.execute("""
            SELECT h.* FROM hosts h
            JOIN repository_hosts rh ON h.host_id = rh.host_id
            WHERE rh.repository_id = ?
        """, (str(rid),)).fetchall()
        
        return [self._row_to_host(row) for row in rows]

    # ==================== Workspace-Host Relationships ====================

    def link_workspace_host(
        self,
        workspace_id: UUID | str,
        host_id: UUID | str,
    ) -> None:
        """Link workspace to its host."""
        self._ensure_initialized()
        
        wid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        hid = UUID(host_id) if isinstance(host_id, str) else host_id
        
        with self._registry.transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workspace_hosts (
                    workspace_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, host_id),
                    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
                    FOREIGN KEY (host_id) REFERENCES hosts(host_id) ON DELETE CASCADE
                )
            """)
            
            conn.execute("""
                INSERT OR IGNORE INTO workspace_hosts (workspace_id, host_id)
                VALUES (?, ?)
            """, (str(wid), str(hid)))

    def get_hosts_for_workspace(self, workspace_id: UUID | str) -> List[dict]:
        """Get hosts for a workspace."""
        self._ensure_initialized()
        
        wid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        conn = self._registry._get_connection()
        
        rows = conn.execute("""
            SELECT h.* FROM hosts h
            JOIN workspace_hosts wh ON h.host_id = wh.host_id
            WHERE wh.workspace_id = ?
        """, (str(wid),)).fetchall()
        
        return [self._row_to_host(row) for row in rows]

    # ==================== Host Discovery & Auto-Registration ====================

    def auto_register_local_host(self) -> dict:
        """Auto-register the local filesystem host."""
        # Check if already registered
        existing = self.find_host_by_identifier("local://filesystem")
        if existing:
            return existing
        
        host_id = uuid4()
        return self.register_host(
            host_id=host_id,
            host_type=HostType.LOCAL_FILESYSTEM.value,
            provider=ProviderType.LOCAL.value,
            display_name="Local Filesystem",
            normalized_identifier="local://filesystem",
            metadata={
                "os": "auto",
                "auto_registered": True,
            },
        )

    def auto_register_local_git_host(self) -> dict:
        """Auto-register the local Git host."""
        existing = self.find_host_by_identifier("local://git")
        if existing:
            return existing
        
        host_id = uuid4()
        return self.register_host(
            host_id=host_id,
            host_type=HostType.LOCAL_GIT.value,
            provider=ProviderType.LOCAL.value,
            display_name="Local Git",
            normalized_identifier="local://git",
            metadata={
                "auto_registered": True,
            },
        )

    def auto_register_github_host(self, api_base: str = "https://api.github.com") -> dict:
        """Auto-register GitHub as a host."""
        existing = self.find_host_by_identifier("github.com")
        if existing:
            return existing
        
        host_id = uuid4()
        return self.register_host(
            host_id=host_id,
            host_type=HostType.GITHUB.value,
            provider=ProviderType.GITHUB.value,
            display_name="GitHub",
            normalized_identifier="github.com",
            metadata={
                "api_base": api_base,
                "auto_registered": True,
            },
        )

    def auto_register_from_git_remote(self, remote_url: str) -> Optional[dict]:
        """Auto-register host based on Git remote URL."""
        from ..connectors.git import detect_provider
        
        try:
            provider = detect_provider(remote_url)
            if provider in ["github", "gitlab", "bitbucket", "gitea", "forgejo"]:
                host_id = uuid4()
                host_type_map = {
                    "github": HostType.GITHUB,
                    "gitlab": HostType.GITLAB,
                    "bitbucket": HostType.BITBUCKET,
                    "gitea": HostType.GITEA,
                    "forgejo": HostType.FORGEJO,
                }
                provider_map = {
                    "github": ProviderType.GITHUB,
                    "gitlab": ProviderType.GITLAB,
                    "bitbucket": ProviderType.BITBUCKET,
                    "gitea": ProviderType.GITEA,
                    "forgejo": ProviderType.FORGEJO,
                }
                
                return self.register_host(
                    host_id=uuid4(),
                    host_type=host_type_map[provider].value,
                    provider=provider_map[provider].value,
                    display_name=provider.capitalize(),
                    normalized_identifier=remote_url.lower(),
                    metadata={
                        "remote_url": remote_url,
                        "auto_registered": True,
                    },
                )
        except Exception:
            pass
        
        return None

    # ==================== Internal Helpers ====================

    def _ensure_initialized(self):
        """Ensure registry is initialized."""
        self._registry.initialize()

    def _row_to_host(self, row: Any) -> dict:
        """Convert database row to host dict."""
        if not row:
            return None
        
        try:
            host_type = HostType(row["host_type"])
        except ValueError:
            host_type = HostType.UNKNOWN
        
        try:
            provider = ProviderType(row["provider"])
        except ValueError:
            provider = ProviderType.LOCAL
        
        try:
            status = HostStatus(row["status"])
        except ValueError:
            status = HostStatus.UNKNOWN
        
        caps = CAPABILITY_MATRIX.get_all_capabilities(row["host_type"])
        supported = [cap for cap, status in caps.items() if status == "supported"]
        
        return {
            "host_id": row["host_id"],
            "host_type": row["host_type"],
            "provider": row["provider"],
            "display_name": row["display_name"],
            "normalized_identifier": row["host_identifier"],
            "status": row["status"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "capabilities": supported,
        }

    def close(self) -> None:
        """Close database connections."""
        self._registry.close()


# Global host registry instance
_global_host_registry: Optional[HostRegistry] = None
_lock = threading.Lock()


def get_host_registry() -> HostRegistry:
    """Get or create global host registry instance."""
    global _global_host_registry
    with _lock:
        if _global_host_registry is None:
            _global_host_registry = HostRegistry()
            _global_host_registry._ensure_initialized()
        return _global_host_registry


def get_host_capabilities(host_type: str) -> Dict[str, str]:
    """Get capabilities for a host type."""
    return CAPABILITY_MATRIX.get_all_capabilities(host_type)


def get_supported_capabilities(host_type: str) -> List[str]:
    """Get supported capabilities for a host type."""
    return CAPABILITY_MATRIX.get_supported_capabilities(host_type)
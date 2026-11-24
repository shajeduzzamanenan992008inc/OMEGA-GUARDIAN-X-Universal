from __future__ import annotations

from typing import Dict, List, Optional, Any
from pathlib import Path
from uuid import UUID

from ..interfaces import WorkspaceProvider
from ..models import (
    HostType,
    ProviderType,
    WorkspaceProviderType,
    WorkspaceType,
)
from ..capabilities import CAPABILITY_MATRIX, get_supported_capabilities, get_requires_auth_capabilities
from ...discovery import discover_root, DiscoveryScope
from ...registry import get_registry
from ...pap import resolve_project_configuration
from ...connectors.git import inspect_repository


class LocalFilesystemProvider:
    """
    Local filesystem provider for project discovery and workspace management.
    
    Reuses the existing Discovery Engine for project detection.
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._host_id: Optional[str] = None
        self._discovery_config = None
    
    @property
    def host_type(self) -> str:
        return "local_filesystem"
    
    @property
    def provider_type(self) -> str:
        return "local"
    
    def initialize(self, host_id: str) -> None:
        """Initialize the provider with a host ID."""
        self._host_id = host_id
        
        # Create discovery config
        from ...discovery import DiscoveryConfig
        self._discovery_config = DiscoveryConfig()
        # Clear auto-discovered roots, we'll add our own
        self._discovery_config.roots = []
    
    def add_discovery_root(self, path: str, name: str = None, max_depth: int = 4) -> str:
        """Add a discovery root for this provider."""
        from ...discovery import DiscoveryConfig, DiscoveryRoot
        root = DiscoveryRoot(
            path=path,
            name=name or Path(path).name,
            enabled=True,
            max_depth=max_depth,
        )
        self._discovery_config.roots.append(root)
        return root.path
    
    def list_discovery_roots(self) -> list:
        """List all configured discovery roots."""
        return [
            {
                "name": r.name,
                "path": str(r.path),
                "enabled": r.enabled,
                "max_depth": r.max_depth,
            }
            for r in self._discovery_config.get_enabled_roots()
        ]
    
    def discover_projects(self, scope: str = "full", roots: list = None) -> list:
        """
        Discover projects in the configured roots.
        
        Args:
            scope: "full", "incremental", or "single_root"
            roots: Optional list of paths to scan (overrides configured roots)
        
        Returns:
            List of discovered projects with metadata
        """
        if isinstance(scope, str):
            scope = DiscoveryScope(scope)
        
        if roots:
            # Discover specific roots
            projects = []
            for root in roots:
                projects.extend(discover_root(Path(root)))
            return [self._format_discovered_project(p) for p in projects]
        else:
            # Full discovery
            from ...discovery import discover_projects
            projects = discover_projects()
            return [self._format_discovered_project(p) for p in projects]
    
    def discover_root(self, path: str) -> list:
        """Discover projects in a single root directory."""
        projects = discover_root(Path(path))
        return [self._format_discovered_project(p) for p in projects]
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("local_filesystem")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("local_filesystem")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("local_filesystem")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "local_filesystem",
            "host_type": "local_filesystem",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
        }
    
    def _format_discovered_project(self, project) -> dict:
        """Format a DiscoveredProject for API response."""
        return {
            "project_id": str(project.project_id),
            "candidate_id": str(project.candidate.candidate_id),
            "root_path": str(project.candidate.root_path),
            "project_name": project.candidate.project_name,
            "languages": project.candidate.languages,
            "frameworks": project.candidate.frameworks,
            "confidence": project.candidate.confidence,
            "is_git_repo": project.candidate.is_git_repo,
            "has_identity": project.candidate.has_identity,
            "has_manifest": project.candidate.has_manifest,
            "activation_status": project.activation_status.value,
            "trust_classification": project.project_config.trust_classification.value,
            "activation_readiness": project.project_config.activation_readiness.value,
        }
    
    def discover_workspaces(self, project_id: str = None) -> List[dict]:
        """Discover workspaces on this host."""
        # For local filesystem, workspaces are the same as discovered projects
        # Each project root is a workspace
        from ...registry import get_registry
        registry = get_registry()
        
        workspaces = []
        if self._host_id:
            # Get workspaces associated with this host
            workspaces = registry.list_workspaces()
            return [
                {
                    "workspace_id": str(w.workspace_id),
                    "project_id": str(w.project_id),
                    "repository_id": str(w.repository_id) if w.repository_id else None,
                    "workspace_provider": w.workspace_provider,
                    "workspace_type": w.workspace_type,
                    "location_reference": w.location_reference,
                    "normalized_location": w.normalized_location,
                    "display_name": w.display_name,
                    "status": w.status.value,
                    "last_seen_at": w.last_seen_at.isoformat(),
                }
                for w in workspaces
            ]
        return []
    
    def get_workspace_info(self, workspace_id: str) -> dict:
        """Get workspace details."""
        from ...registry import get_registry
        registry = get_registry()
        ws = registry.get_workspace(workspace_id)
        
        return {
            "workspace_id": str(ws.workspace_id),
            "project_id": str(ws.project_id),
            "repository_id": str(ws.repository_id) if ws.repository_id else None,
            "workspace_provider": ws.workspace_provider,
            "workspace_type": ws.workspace_type,
            "location_reference": ws.location_reference,
            "normalized_location": ws.normalized_location,
            "display_name": ws.display_name,
            "status": ws.status.value,
            "last_seen_at": ws.last_seen_at.isoformat(),
            "created_at": ws.created_at.isoformat(),
            "updated_at": ws.updated_at.isoformat(),
        }
    
    def inspect_workspace(self, workspace_id: str) -> dict:
        """Inspect workspace details."""
        return self.get_workspace_info(workspace_id)
    
    def check_workspace_access(self, workspace_id: str) -> bool:
        """Check if workspace is accessible."""
        try:
            from ...registry import get_registry
            registry = get_registry()
            ws = registry.get_workspace(workspace_id)
            path = Path(ws.location_reference)
            return path.exists() and path.is_dir()
        except Exception:
            return False
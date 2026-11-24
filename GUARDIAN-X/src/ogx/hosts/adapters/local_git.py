from __future__ import annotations

from typing import Dict, List, Optional, Any
from pathlib import Path
from uuid import UUID

from ..interfaces import RepositoryProvider, WorkspaceProvider
from ..models import (
    HostType,
    ProviderType,
    WorkspaceProviderType,
    WorkspaceType,
)
from ..capabilities import CAPABILITY_MATRIX, get_supported_capabilities, get_requires_auth_capabilities
from ...connectors.git import (
    git_available,
    inspect_repository,
    detect_provider,
    remote_summary,
    clone_repository,
)
from ...registry import get_registry
from ...pap import resolve_project_configuration


class LocalGitProvider:
    """
    Local Git provider for Git repository inspection and management.
    
    Reuses existing Git connector for repository inspection.
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._host_id: Optional[str] = None
    
    @property
    def host_type(self) -> str:
        return "local_git"
    
    @property
    def provider_type(self) -> str:
        return "local"
    
    def initialize(self, host_id: str) -> None:
        """Initialize the provider with a host ID."""
        self._host_id = host_id
    
    def is_available(self) -> bool:
        """Check if Git is available."""
        from ...connectors.git import git_available
        return git_available()
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("local_git")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("local_git")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("local_git")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "local_git",
            "host_type": "local_git",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "git_available": self.is_available(),
        }
    
    def inspect_repository(self, path: str) -> dict:
        """Inspect a local Git repository."""
        from ...connectors.git import inspect_repository
        return inspect_repository(Path(path))
    
    def detect_provider(self, remote_url: str) -> str:
        """Detect provider from Git remote URL."""
        from ...connectors.git import detect_provider
        return detect_provider(remote_url)
    
    def list_remotes(self, path: str) -> List[dict]:
        """List Git remotes for a repository."""
        result = self.inspect_repository(path)
        remotes = result.get("remotes", {})
        return [
            {
                "name": name,
                "url": info["url"],
                "provider": info["provider"],
            }
            for name, info in remotes.items()
        ]
    
    def get_remote_summary(self, url: str) -> dict:
        """Get normalized summary of a Git remote URL."""
        from ...connectors.git import remote_summary
        return remote_summary(url)
    
    def clone_repository(self, url: str, destination: str, confirm: bool = False) -> dict:
        """Clone a repository."""
        from ...connectors.git import clone_repository
        return clone_repository(url, Path(destination), confirm=confirm)
    
    def get_remote_summary(self, url: str) -> dict:
        """Get normalized summary of a Git remote URL."""
        from ...connectors.git import remote_summary
        return remote_summary(url)
    
    def inspect_remote(self, url: str) -> dict:
        """Inspect a remote repository without cloning."""
        from ...connectors.git import inspect_remote
        return inspect_remote(url)
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("local_git")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("local_git")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("local_git")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "local_git",
            "host_type": "local_git",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "git_available": self.is_available(),
        }
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("local_git")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("local_git")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("local_git")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "local_git",
            "host_type": "local_git",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "git_available": self.is_available(),
        }
    
    def discover_repositories(self, path: str) -> List[dict]:
        """Discover Git repositories in a path."""
        from pathlib import Path
        repos = []
        path = Path(path)
        
        if not path.exists():
            return []
        
        if path.is_dir():
            # Check if it's a Git repo
            if (path / ".git").exists():
                repo_info = self.inspect_repository(str(path))
                repos.append({
                    "path": str(path),
                    "name": path.name,
                    "is_git_repo": True,
                    "branch": repo_info.get("branch", ""),
                    "remotes": repo_info.get("remotes", {}),
                })
            else:
                # Scan subdirectories
                try:
                    for child in path.iterdir():
                        if child.is_dir() and not child.is_symlink():
                            if (child / ".git").exists():
                                repo_info = self.inspect_repository(str(child))
                                repos.append({
                                    "path": str(child),
                                    "name": child.name,
                                    "is_git_repo": True,
                                    "branch": repo_info.get("branch", ""),
                                    "remotes": repo_info.get("remotes", {}),
                                })
                except (PermissionError, OSError):
                    pass
        
        return repos
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "local_git",
            "host_type": "local_git",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "git_available": self.is_available(),
        }
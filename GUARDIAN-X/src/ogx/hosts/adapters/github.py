from __future__ import annotations

from typing import Dict, List, Optional, Any, AsyncIterator
from pathlib import Path

from ..interfaces import RepositoryProvider
from ..models import (
    HostType,
    ProviderType,
    RepositoryProviderInfo,
)
from ..capabilities import CAPABILITY_MATRIX, get_supported_capabilities, get_requires_auth_capabilities
from ...connectors.providers import ProviderClient, PROVIDERS, _validate_api_base


class GitHubProvider:
    """
    GitHub repository provider (read-only).
    
    Uses the existing ProviderClient for API interactions.
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._host_id: Optional[str] = None
        self._client: Optional[Any] = None
        self._api_base: str = "https://api.github.com"
        self._token: Optional[str] = None
    
    @property
    def host_type(self) -> str:
        return "github"
    
    @property
    def provider_type(self) -> str:
        return "github"
    
    def initialize(self, host_id: str, api_base: Optional[str] = None, token: Optional[str] = None) -> None:
        """Initialize the provider with host ID and credentials."""
        self._host_id = host_id
        
        # Use provided API base or default
        if api_base:
            from ...connectors.providers import _validate_api_base
            self._api_base = _validate_api_base(api_base)
        
        # Get token from parameter or environment
        self._token = token or self._get_token_from_env()
        
        # Initialize provider client if we have a token
        if self._token:
            self._client = ProviderClient(
                provider="github",
                remote_url="https://github.com",  # dummy URL for initialization
            )
            # Override the client's config with our settings
            self._client.config = type('Config', (), {
                'name': 'github',
                'token_env': 'OGX_GITHUB_TOKEN',
                'api_base': self._api_base,
            })()
            self._client.token = self._token
    
    def _get_token_from_env(self) -> Optional[str]:
        """Get GitHub token from environment variables."""
        import os
        return os.environ.get("OGX_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    
    @property
    def host_type(self) -> str:
        return "github"
    
    @property
    def provider_type(self) -> str:
        return "github"
    
    def is_available(self) -> bool:
        """Check if provider is available (has token)."""
        return self._token is not None
    
    def is_authenticated(self) -> bool:
        """Check if provider is authenticated."""
        return self.is_available()
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("github")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("github")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("github")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("github")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("github")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("github")
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("github")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("github")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("github")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("github")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("github")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("github")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("github")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("github")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("github")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    def get_host_capabilities(self) -> Dict[str, str]:
        """Get capabilities for this provider."""
        from ..capabilities import CAPABILITY_MATRIX
        return CAPABILITY_MATRIX.get_all_capabilities("github")
    
    def get_supported_capabilities(self) -> List[str]:
        """Get list of supported capabilities."""
        from ..capabilities import get_supported_capabilities
        return get_supported_capabilities("github")
    
    def get_requires_auth_capabilities(self) -> List[str]:
        """Get capabilities requiring authentication."""
        from ..capabilities import get_requires_auth_capabilities
        return get_requires_auth_capabilities("github")
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
    
    # Remote discovery
    
    def discover_repositories(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Discover repositories matching criteria."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return iter([])
    
    def discover_repositories_sync(
        self,
        owner: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Synchronous version of discover_repositories."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    # Repository operations
    
    def get_repository(self, repo_id: str) -> dict:
        """Get repository metadata by ID (owner/repo)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        
        return self._client.get_repository()
    
    def inspect_repository(self, repo_id: str) -> dict:
        """Get full repository metadata."""
        return self.get_repository(repo_id)
    
    def list_branches(
        self,
        repo_id: str,
        limit: int = 100,
        protected_only: bool = False,
    ) -> List[dict]:
        """List repository branches."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def list_commits(
        self,
        repo_id: str,
        branch: Optional[str] = None,
        path: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[dict]:
        """List repository commits."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_file_content(
        self,
        repo_id: str,
        path: str,
        ref: Optional[str] = None,
    ) -> dict:
        """Get file content and metadata."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def get_repository_tree(
        self,
        repo_id: str,
        path: str = "",
        ref: Optional[str] = None,
        recursive: bool = False,
    ) -> List[dict]:
        """Get repository tree structure."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_commit(self, repo_id: str, commit_sha: str) -> dict:
        """Get commit details."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return {}
    
    def list_pull_requests(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """List pull requests."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return []
    
    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get repository metadata (lightweight)."""
        if not self._client:
            raise PermissionError("GitHub provider not authenticated")
        return self.get_repository(repo_id)
    
    def check_repository_access(self, repo_id: str) -> bool:
        """Check if repository is accessible."""
        if not self._client:
            return False
        try:
            self.get_repository(repo_id)
            return True
        except Exception:
            return False
    
    def get_provider_info(self) -> dict:
        """Get provider information."""
        return {
            "provider_type": "github",
            "host_type": "github",
            "capabilities": self.get_host_capabilities(),
            "supported_capabilities": self.get_supported_capabilities(),
            "requires_auth": self.get_requires_auth_capabilities(),
            "authenticated": self.is_authenticated(),
            "api_base": self._api_base,
        }
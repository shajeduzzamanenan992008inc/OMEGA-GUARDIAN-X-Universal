from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .config import ScanConfig, load_config, state_dir
from .detector import detect_project
from .pap import resolve_project_configuration, ProjectConfiguration
from .registry import get_registry, RegistryV2, WorkspaceRecord, ProjectRecord, RepositoryRecord, WorkspaceStatus, ProjectLifecycleState


class DiscoveryStatus(str, Enum):
    """Project discovery lifecycle states."""
    DISCOVERED = "discovered"
    CLASSIFIED = "classified"
    IDENTITY_RESOLVED = "identity_resolved"
    REGISTERED = "registered"
    CONFIGURATION_RESOLVED = "configuration_resolved"
    SECURITY_EVALUATED = "security_evaluated"
    ACTIVATION_READY = "activation_ready"
    ACTIVATED = "activated"
    MONITORING = "monitoring"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"
    DISCOVERY_FAILED = "discovery_failed"
    INVALID_PROJECT = "invalid_project"
    IDENTITY_CONFLICT = "identity_conflict"
    IDENTITY_CORRUPTED = "identity_corrupted"
    REGISTRATION_FAILED = "registration_failed"
    CONFIGURATION_INVALID = "configuration_invalid"
    SECURITY_RESTRICTED = "security_restricted"
    HOST_INACCESSIBLE = "host_inaccessible"
    ACTIVATION_FAILED = "activation_failed"
    RECOVERY_REQUIRED = "recovery_required"


class DiscoveryScope(str, Enum):
    """Scope of discovery operation."""
    FULL = "full"
    INCREMENTAL = "incremental"
    SINGLE_ROOT = "single_root"


@dataclass(frozen=True, slots=True)
class DiscoveryRoot:
    """A root directory for project discovery."""
    path: Path
    name: str
    enabled: bool = True
    max_depth: int = 4
    exclusions: list[str] = field(default_factory=list)
    
    def __post_init__(self):
        object.__setattr__(self, 'path', Path(self.path).expanduser().resolve())


@dataclass(frozen=True, slots=True)
class ProjectCandidate:
    """A candidate project discovered during scanning."""
    candidate_id: UUID = field(default_factory=uuid4)
    root_path: Path = field(default=None)
    project_name: str = ""
    languages: list[str] = field(default_factory=list)
    ecosystems: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    confidence: float = 0.0
    is_git_repo: bool = False
    git_remotes: list[dict[str, str]] = field(default_factory=list)
    has_identity: bool = False
    has_manifest: bool = False
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    detection_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiscoveredProject:
    """A fully processed project with identity and configuration."""
    project_id: UUID
    candidate: ProjectCandidate
    project_config: ProjectConfiguration
    registry_record: ProjectRecord | None = None
    workspace_record: WorkspaceRecord | None = None
    activation_status: DiscoveryStatus = DiscoveryStatus.DISCOVERED
    registered_at: datetime | None = None
    activated_at: datetime | None = None
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activation_warnings: list[str] = field(default_factory=list)


class DiscoveryError(Exception):
    """Base exception for discovery errors."""
    pass


class DiscoveryConfig:
    """Configuration for discovery operations."""
    
    DEFAULT_EXCLUSIONS = [
        ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "vendor",
        "dist", "build", "target", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".next", ".nuxt", "coverage", "site-packages", ".idea", ".vscode",
        ".DS_Store", "Thumbs.db", "*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.dylib",
        "*.exe", "*.bin", "*.o", "*.a", "*.lib", "*.obj", "*.class", "*.jar", "*.war",
        "*.ear", "*.apk", "*.ipa", "*.app", "*.framework", "*.dSYM", "*.xcarchive",
        "*.gradle", "*.gradle.kts", "gradlew", "gradlew.bat", "mvnw", "mvnw.cmd",
        "*.lock", "yarn.lock", "package-lock.json", "pnpm-lock.yaml", "bun.lock",
        "bun.lockb", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock",
        "poetry.lock", "Pipfile.lock", "uv.lock", "packages.lock.json",
        "Package.resolved", "*.log", "*.tmp", "*.temp", "*.cache", "*.bak", "*.swp",
        "*.swo", "*~", ".#*", "#*#", "*.orig", "*.rej", "*.patch", "*.diff",
    ]
    
    COMMON_DEVELOPMENT_ROOTS = [
        ("Projects", "~/Projects"),
        ("Development", "~/Development"),
        ("Workspace", "~/Workspace"),
        ("Repositories", "~/Repositories"),
        ("Source", "~/Source"),
        ("Code", "~/Code"),
        ("Documents/Projects", "~/Documents/Projects"),
        ("Documents/Development", "~/Documents/Development"),
        ("github", "~/github"),
        ("gitlab", "~/gitlab"),
        ("bitbucket", "~/bitbucket"),
    ]
    
    def __init__(self, config: ScanConfig | None = None):
        self.scan_config = config or load_config()
        self.roots: list[DiscoveryRoot] = []
        self.exclusions = self.scan_config.excludes + self.DEFAULT_EXCLUSIONS
        self.max_files = self.scan_config.max_files
        self.max_file_bytes = self.scan_config.max_file_bytes
        self.max_depth = 4
        self._load_roots_from_config()
    
    def _load_roots_from_config(self):
        """Load discovery roots from OGX_HOME config if available."""
        from .config import state_dir
        config_path = state_dir() / "discovery.toml"
        if config_path.exists():
            try:
                import tomllib
                raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
                roots_data = raw.get("roots", [])
                for root_data in roots_data:
                    self.roots.append(DiscoveryRoot(
                        path=root_data["path"],
                        name=root_data.get("name", Path(root_data["path"]).name),
                        enabled=root_data.get("enabled", True),
                        max_depth=root_data.get("max_depth", 4),
                        exclusions=root_data.get("exclusions", []),
                    ))
            except Exception:
                pass
        
        # Add common roots if no roots configured
        if not self.roots:
            self._add_common_roots()
    
    def _add_common_roots(self):
        """Add common development directories as roots."""
        for name, path in self.COMMON_DEVELOPMENT_ROOTS:
            expanded = Path(path).expanduser()
            if expanded.exists() and expanded.is_dir():
                self.roots.append(DiscoveryRoot(
                    path=expanded,
                    name=name,
                    enabled=True,
                    max_depth=4,
                    exclusions=[],
                ))
    
    def add_root(self, path: Path, name: str | None = None, max_depth: int = 4, 
                 exclusions: list[str] | None = None) -> DiscoveryRoot:
        """Add a discovery root and persist to config."""
        root = DiscoveryRoot(
            path=path,
            name=name or Path(path).name,
            enabled=True,
            max_depth=max_depth,
            exclusions=exclusions or [],
        )
        self.roots.append(root)
        self._save_roots_to_config()
        return root
    
    def remove_root(self, root_path: Path) -> bool:
        """Remove a discovery root."""
        for i, root in enumerate(self.roots):
            if root.path == root_path:
                self.roots.pop(i)
                self._save_roots_to_config()
                return True
        return False
    
    def _save_roots_to_config(self):
        """Save discovery roots to TOML config file."""
        from .config import state_dir
        config_path = state_dir() / "discovery.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        roots_data = []
        for root in self.roots:
            roots_data.append({
                "path": str(root.path),
                "name": root.name,
                "enabled": root.enabled,
                "max_depth": root.max_depth,
                "exclusions": root.exclusions,
            })
        
        import tomli_w
        config_path.write_text(tomli_w.dumps({"roots": roots_data}), encoding="utf-8")
    
    def get_enabled_roots(self) -> list[DiscoveryRoot]:
        """Get all enabled discovery roots."""
        return [r for r in self.roots if r.enabled]


class DiscoveryEngine:
    """Main engine for project discovery and processing."""
    
    def __init__(self, config: DiscoveryConfig | None = None):
        self.config = config or DiscoveryConfig()
        self.registry = get_registry()
        self._lock = threading.Lock()
        self._discovery_state: dict[str, DiscoveryStatus] = {}
    
    def discover_projects(self, scope: DiscoveryScope = DiscoveryScope.FULL, 
                         roots: list[Path] | None = None) -> list[DiscoveredProject]:
        """
        Main discovery entry point.
        
        Args:
            scope: Discovery scope (full, incremental, or single root)
            roots: Optional specific roots to scan (overrides config)
        
        Returns:
            List of discovered and processed projects
        """
        discovered_projects = []
        
        if roots:
            # Use provided roots
            discovery_roots = [
                DiscoveryRoot(path=r, name=r.name, enabled=True)
                for r in roots
            ]
        else:
            discovery_roots = self.config.get_enabled_roots()
        
        for root in discovery_roots:
            if not root.enabled:
                continue
            
            try:
                candidates = self._scan_root(root)
                for candidate in candidates:
                    project = self._process_candidate(candidate)
                    if project:
                        discovered_projects.append(project)
            except Exception as e:
                # Log error but continue with other roots
                pass
        
        return discovered_projects
    
    def _scan_root(self, root: DiscoveryRoot) -> list[ProjectCandidate]:
        """Scan a single root directory for project candidates."""
        candidates = []
        root_path = root.path.expanduser().resolve()
        
        if not root_path.exists() or not root_path.is_dir():
            return candidates
        
        # Walk the directory tree
        visited = set()
        stack = [(root_path, 0)]
        
        while stack:
            current_path, depth = stack.pop()
            
            # Check depth limit
            if depth > root.max_depth:
                continue
            
            # Check for cycles
            real_path = current_path.resolve()
            if real_path in visited:
                continue
            visited.add(real_path)
            
            # Skip symlinks
            if current_path.is_symlink():
                continue
            
            # Check exclusions
            if self._is_excluded(current_path, root.exclusions):
                continue
            
            try:
                # Check if this looks like a project root
                candidate = self._evaluate_candidate(current_path, root_path)
                if candidate and candidate.confidence > 0.3:
                    candidates.append(candidate)
                
                # Add subdirectories to stack
                if depth < root.max_depth:
                    try:
                        for child in current_path.iterdir():
                            if child.is_dir() and not child.is_symlink():
                                stack.append((child, depth + 1))
                    except (PermissionError, OSError):
                        pass
            except (PermissionError, OSError):
                pass
        
        return candidates
    
    def _is_excluded(self, path: Path, root_exclusions: list[str]) -> bool:
        """Check if a path should be excluded."""
        name = path.name
        full_path = str(path)
        
        # Check root-specific exclusions (both name and full path)
        for excl in root_exclusions:
            if self._match_pattern(name, excl):
                return True
            # Also check full path for patterns with path separators
            if '/' in excl or '\\' in excl:
                if self._match_pattern(full_path, excl):
                    return True
        
        # Check global exclusions
        for excl in self.config.exclusions:
            if self._match_pattern(name, excl):
                return True
            if '/' in excl or '\\' in excl:
                if self._match_pattern(full_path, excl):
                    return True
        
        # Skip hidden directories (starting with .) except .git
        if name.startswith('.') and name != '.git':
            return True
        
        return False
    
    def _match_pattern(self, name: str, pattern: str) -> bool:
        """Simple pattern matching for exclusions."""
        if '*' in pattern:
            import fnmatch
            return fnmatch.fnmatch(name, pattern)
        return name == pattern
    
    def _evaluate_candidate(self, path: Path, root_path: Path) -> ProjectCandidate | None:
        """Evaluate if a directory is a project candidate."""
        try:
            # Quick checks first
            if not path.is_dir():
                return None
            
            # Use detector to analyze
            profile = detect_project(path)
            
            # If no languages detected, not a project
            if not profile.languages:
                return None
            
            # Check for Git repo
            is_git_repo = (path / ".git").exists()
            git_remotes = []
            if is_git_repo:
                try:
                    from .connectors.git import inspect_repository
                    git_info = inspect_repository(path)
                    git_remotes = [
                        {"name": name, "url": info["url"], "provider": info["provider"]}
                        for name, info in git_info.get("remotes", {}).items()
                    ]
                except Exception:
                    pass
            
            # Check for identity
            has_identity = (path / ".guardian-x" / "identity.json").exists()
            
            # Check for manifest
            has_manifest = ((path / "GUARDIAN-X.json").exists() or 
                           (path / ".guardian-x" / "manifest.json").exists())
            
            # Calculate confidence based on signals
            confidence = 0.0
            if profile.manifests:
                confidence += 0.4
            if profile.lockfiles:
                confidence += 0.2
            if is_git_repo:
                confidence += 0.2
            if has_identity:
                confidence += 0.3
            if has_manifest:
                confidence += 0.1
            if profile.languages:
                confidence += 0.1
            
            # Boost confidence for known project types
            if any(f in ["Django", "Flask", "FastAPI", "React", "Vue", "Next.js", "Angular", 
                        "Spring", "Maven", "Gradle", "Cargo", "npm", "pnpm", "yarn", "pip", 
                        "Poetry", "Composer", "Bundler", "Android", "Flutter"] 
                   for f in profile.frameworks):
                confidence += 0.2
            
            candidate = ProjectCandidate(
                root_path=path,
                project_name=path.name,
                languages=profile.languages,
                ecosystems=profile.ecosystems,
                frameworks=profile.frameworks,
                manifests=profile.manifests,
                lockfiles=profile.lockfiles,
                confidence=min(confidence, 1.0),
                is_git_repo=is_git_repo,
                git_remotes=git_remotes,
                has_identity=has_identity,
                has_manifest=has_manifest,
                detection_metadata={
                    "manifests": profile.manifests,
                    "lockfiles": profile.lockfiles,
                    "ci_files": profile.ci_files,
                }
            )
            
            return candidate
            
        except Exception:
            return None
    
    def _process_candidate(self, candidate: ProjectCandidate) -> DiscoveredProject | None:
        """Process a project candidate through the full lifecycle."""
        try:
            # 1. Resolve identity
            identity_warnings = self._resolve_identity(candidate)
            
            # 2. Resolve configuration
            project_config = resolve_project_configuration(candidate.root_path)
            
            # 2.5 Check activation readiness
            if project_config.activation_readiness.value == "configuration_invalid":
                return None
            if project_config.activation_readiness.value == "security_restriction":
                return None
            
            # 3. Register or update in registry
            project_record = self._register_project(candidate, project_config)
            
            # 4. Register workspace
            workspace_record = self._register_workspace(candidate, project_record)
            
            # 5. Register repository if applicable
            if candidate.is_git_repo and candidate.git_remotes:
                self._register_repositories(candidate, project_record)
            
            # 6. Build discovered project
            discovered = DiscoveredProject(
                project_id=project_record.project_id,
                candidate=candidate,
                project_config=project_config,
                registry_record=project_record,
                workspace_record=workspace_record,
                activation_status=DiscoveryStatus.ACTIVATED,
                activated_at=datetime.now(timezone.utc),
                activation_warnings=project_config.validation_warnings,
            )
            
            return discovered
            
        except Exception:
            return None
    
    def _resolve_identity(self, candidate: ProjectCandidate) -> list[str]:
        """Resolve project identity, return warnings."""
        warnings = []
        
        if candidate.has_identity:
            # Try to read existing identity
            try:
                from .pap import read_identity
                identity = read_identity(candidate.root_path)
                # Identity found, verify it matches any registry record
            except Exception as e:
                warnings.append(f"Identity file error: {e}")
        else:
            # No identity file - may need to create one
            pass
        
        return warnings
    
    def _register_project(self, candidate: ProjectCandidate, 
                         project_config: ProjectConfiguration) -> ProjectRecord:
        """Register or update project in registry."""
        project_id = None
        
        # Try to find existing project by identity
        if candidate.has_identity:
            try:
                from .pap import read_identity
                identity = read_identity(candidate.root_path)
                project_id = identity.project_id
            except Exception:
                pass
        
        # Try to find by repository
        if not project_id and candidate.is_git_repo and candidate.git_remotes:
            for remote in candidate.git_remotes:
                normalized_url = self._normalize_git_url(remote["url"])
                repo = self.registry.find_repository_by_normalized_url(normalized_url)
                if repo:
                    project_id = repo.project_id
                    break
        
        # Try to find by workspace path
        if not project_id:
            workspace = self.registry.find_workspace_by_normalized_location(
                str(candidate.root_path.resolve())
            )
            if workspace:
                project_id = workspace.project_id
        
        if project_id:
            # Update existing project
            project = self.registry.get_project(project_id)
            project = self.registry.update_project(
                project_id=project_id,
                canonical_name=candidate.project_name,
                description=f"Auto-discovered: {', '.join(candidate.languages)} project",
                identity_hash=project.identity_hash,
                lifecycle_state=ProjectLifecycleState.ACTIVE,
            )
        else:
            # Create new project
            project_id = uuid4()
            project = self.registry.register_project(
                project_id=project_id,
                canonical_name=candidate.project_name,
                description=f"Auto-discovered: {', '.join(candidate.languages)} project",
                identity_hash="",
                lifecycle_state=ProjectLifecycleState.ACTIVE,
            )
        
        return project
    
    def _register_workspace(self, candidate: ProjectCandidate, 
                           project: ProjectRecord) -> WorkspaceRecord:
        """Register or update workspace for project."""
        normalized_location = str(candidate.root_path.resolve())
        existing_workspace = self.registry.find_workspace_by_normalized_location(normalized_location)
        
        if existing_workspace:
            # Update existing workspace
            workspace = self.registry.update_workspace_last_seen(existing_workspace.workspace_id)
        else:
            # Create new workspace
            workspace_id = uuid4()
            workspace = self.registry.register_workspace(
                workspace_id=workspace_id,
                project_id=project.project_id,
                location_reference=str(candidate.root_path),
                normalized_location=normalized_location,
                display_name=candidate.project_name,
                workspace_provider="local",
                workspace_type="local_fs",
                status=WorkspaceStatus.ACTIVE,
            )
        
        return workspace
    
    def _register_repositories(self, candidate: ProjectCandidate, 
                              project: ProjectRecord) -> list[RepositoryRecord]:
        """Register Git repositories for project."""
        repositories = []
        
        for remote in candidate.git_remotes:
            repo_id = uuid4()
            normalized_url = self._normalize_git_url(remote["url"])
            
            repo = self.registry.register_repository(
                repository_id=repo_id,
                project_id=project.project_id,
                provider=remote["provider"],
                repository_url=remote["url"],
                normalized_repository_url=normalized_url,
                repository_name=remote.get("name", ""),
            )
            repositories.append(repo)
        
        return repositories
    
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


# Convenience functions for programmatic use

def discover_projects(config: DiscoveryConfig | None = None, 
                     scope: DiscoveryScope = DiscoveryScope.FULL,
                     roots: list[Path] | None = None) -> list[DiscoveredProject]:
    """Discover projects using the default engine."""
    engine = DiscoveryEngine(config)
    return engine.discover_projects(scope, roots)


def discover_root(path: Path, config: DiscoveryConfig | None = None) -> list[DiscoveredProject]:
    """Discover projects in a single root directory."""
    config = config or DiscoveryConfig()
    engine = DiscoveryEngine(config)
    root = DiscoveryRoot(path=path, name=path.name, enabled=True)
    candidates = engine._scan_root(root)
    projects = []
    for candidate in candidates:
        project = engine._process_candidate(candidate)
        if project:
            projects.append(project)
    return projects


def run_discovery_cycle(config: DiscoveryConfig | None = None) -> list[DiscoveredProject]:
    """Run a complete discovery cycle."""
    engine = DiscoveryEngine(config)
    return engine.discover_projects(DiscoveryScope.FULL)
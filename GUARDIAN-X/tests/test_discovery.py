from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from ogx.discovery import (
    DiscoveryConfig,
    DiscoveryEngine,
    DiscoveryScope,
    DiscoveryRoot,
    DiscoveryStatus,
    ProjectCandidate,
    discover_projects,
    discover_root,
    run_discovery_cycle,
)


class DiscoveryTests(unittest.TestCase):
    """Tests for the Discovery Engine."""
    
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
    
    def tearDown(self):
        self.temp_dir.cleanup()
    
    # --- Configuration Tests ---
    
    def test_discovery_config_defaults(self):
        """Test default discovery configuration."""
        config = DiscoveryConfig()
        
        self.assertIsInstance(config.roots, list)
        self.assertIsInstance(config.exclusions, list)
        self.assertGreater(len(config.exclusions), 0)
    
    def test_discovery_config_add_root(self):
        """Test adding a discovery root."""
        config = DiscoveryConfig()
        initial_count = len(config.roots)
        
        root = config.add_root(Path("/test/path"), name="Test Root", max_depth=3)
        
        self.assertEqual(len(config.roots), initial_count + 1)
        self.assertEqual(root.name, "Test Root")
        self.assertEqual(root.max_depth, 3)
        self.assertTrue(root.enabled)
    
    def test_discovery_config_remove_root(self):
        """Test removing a discovery root."""
        config = DiscoveryConfig()
        # Clear any auto-discovered roots
        config.roots = []
        test_path = Path("/test/path")
        root = config.add_root(test_path, name="Test Root")
        
        # The stored path will be expanded
        stored_path = config.roots[0].path
        result = config.remove_root(stored_path)
        
        self.assertTrue(result)
        self.assertEqual(len(config.roots), 0)
    
    def test_discovery_config_remove_nonexistent(self):
        """Test removing a non-existent root."""
        config = DiscoveryConfig()
        
        result = config.remove_root(Path("/nonexistent/path"))
        
        self.assertFalse(result)
    
    def test_discovery_root_dataclass(self):
        """Test DiscoveryRoot dataclass."""
        root = DiscoveryRoot(
            path=Path("/test"),
            name="Test",
            enabled=True,
            max_depth=3,
            exclusions=["*.tmp"],
        )
        
        self.assertEqual(root.path, Path("/test").expanduser().resolve())
        self.assertEqual(root.name, "Test")
        self.assertEqual(root.max_depth, 3)
        self.assertEqual(root.exclusions, ["*.tmp"])
        self.assertTrue(root.enabled)
    
    # --- Engine Tests ---
    
    def test_discovery_engine_initialization(self):
        """Test DiscoveryEngine initialization."""
        config = DiscoveryConfig()
        engine = DiscoveryEngine(config)
        
        self.assertEqual(engine.config, config)
        self.assertIsNotNone(engine.registry)
    
    def test_discover_projects_without_roots(self):
        """Test discovering projects with no configured roots."""
        config = DiscoveryConfig()
        # Clear roots to test empty case
        config.roots = []
        
        engine = DiscoveryEngine(config)
        projects = engine.discover_projects(DiscoveryScope.FULL)
        
        self.assertEqual(projects, [])
    
    def test_discover_root_single_path(self):
        """Test discovering projects in a single root path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create a simple Python project
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            
            projects = discover_root(root)
            
            self.assertGreaterEqual(len(projects), 1)
            for p in projects:
                self.assertIsInstance(p.candidate.root_path, Path)
    
    def test_discover_projects_with_temp_root(self):
        """Test discovering projects with a temporary root."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            (root / "main.py").write_text("print('hello')", encoding="utf-8")
            
            config = DiscoveryConfig()
            config.roots = [DiscoveryRoot(path=root, name="Test", enabled=True)]
            
            engine = DiscoveryEngine(config)
            projects = engine.discover_projects(DiscoveryScope.FULL)
            
            self.assertGreaterEqual(len(projects), 1)
            for p in projects:
                self.assertIn("Python", p.candidate.languages)
    
    def test_discovery_scope_full(self):
        """Test FULL discovery scope."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            
            config = DiscoveryConfig()
            config.roots = [DiscoveryRoot(path=root, name="Test", enabled=True)]
            
            engine = DiscoveryEngine(config)
            projects = engine.discover_projects(DiscoveryScope.FULL)
            
            self.assertGreaterEqual(len(projects), 1)
    
    def test_discovery_scope_incremental(self):
        """Test INCREMENTAL discovery scope."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            
            config = DiscoveryConfig()
            config.roots = [DiscoveryRoot(path=root, name="Test", enabled=True)]
            
            engine = DiscoveryEngine(config)
            projects = engine.discover_projects(DiscoveryScope.INCREMENTAL)
            
            self.assertGreaterEqual(len(projects), 1)
    
    def test_discovery_scope_single_root(self):
        """Test SINGLE_ROOT discovery scope."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            
            config = DiscoveryConfig()
            config.roots = [DiscoveryRoot(path=root, name="Test", enabled=True)]
            
            engine = DiscoveryEngine(config)
            projects = engine.discover_projects(DiscoveryScope.SINGLE_ROOT)
            
            self.assertGreaterEqual(len(projects), 1)
    
    # --- Candidate Detection Tests ---
    
    def test_candidate_dataclass(self):
        """Test ProjectCandidate dataclass."""
        candidate = ProjectCandidate(
            root_path=Path("/test"),
            project_name="Test",
            languages=["Python"],
            frameworks=["Django"],
            confidence=0.8,
        )
        
        self.assertEqual(candidate.project_name, "Test")
        self.assertEqual(candidate.languages, ["Python"])
        self.assertEqual(candidate.frameworks, ["Django"])
        self.assertEqual(candidate.confidence, 0.8)
    
    def test_discovered_project_dataclass(self):
        """Test DiscoveredProject dataclass."""
        from ogx.discovery import DiscoveredProject
        from ogx.pap import ProjectConfiguration
        
        candidate = ProjectCandidate(
            root_path=Path("/test"),
            project_name="Test",
        )
        
        # This would require a full config, so just test instantiation
        from uuid import UUID
        discovered = DiscoveredProject(
            project_id=uuid4(),
            candidate=candidate,
            project_config=None,  # Would be ProjectConfiguration in real use
            activation_status=DiscoveryStatus.ACTIVATED,
        )
        
        self.assertEqual(discovered.candidate.project_name, "Test")
        self.assertEqual(discovered.activation_status, DiscoveryStatus.ACTIVATED)
    
    def test_discovery_status_enum(self):
        """Test DiscoveryStatus enum values."""
        statuses = [
            DiscoveryStatus.DISCOVERED,
            DiscoveryStatus.CLASSIFIED,
            DiscoveryStatus.IDENTITY_RESOLVED,
            DiscoveryStatus.REGISTERED,
            DiscoveryStatus.CONFIGURATION_RESOLVED,
            DiscoveryStatus.SECURITY_EVALUATED,
            DiscoveryStatus.ACTIVATION_READY,
            DiscoveryStatus.ACTIVATED,
            DiscoveryStatus.MONITORING,
            DiscoveryStatus.SUSPENDED,
            DiscoveryStatus.ARCHIVED,
            DiscoveryStatus.DISCOVERY_FAILED,
            DiscoveryStatus.INVALID_PROJECT,
            DiscoveryStatus.IDENTITY_CONFLICT,
            DiscoveryStatus.IDENTITY_CORRUPTED,
            DiscoveryStatus.REGISTRATION_FAILED,
            DiscoveryStatus.CONFIGURATION_INVALID,
            DiscoveryStatus.SECURITY_RESTRICTED,
            DiscoveryStatus.HOST_INACCESSIBLE,
            DiscoveryStatus.ACTIVATION_FAILED,
            DiscoveryStatus.RECOVERY_REQUIRED,
        ]
        
        for status in statuses:
            self.assertIsInstance(status.value, str)
    
    def test_discovery_scope_enum(self):
        """Test DiscoveryScope enum values."""
        scopes = [
            DiscoveryScope.FULL,
            DiscoveryScope.INCREMENTAL,
            DiscoveryScope.SINGLE_ROOT,
        ]
        
        for scope in scopes:
            self.assertIsInstance(scope.value, str)
    
    # --- Exclusion Tests ---
    
    def test_exclusion_patterns(self):
        """Test exclusion pattern matching."""
        config = DiscoveryConfig()
        engine = DiscoveryEngine(config)
        
        # Test standard exclusions
        test_paths = [
            Path("node_modules"),
            Path(".git"),
            Path("venv"),
            Path("dist"),
            Path("build"),
            Path("__pycache__"),
            Path("test.py"),
        ]
        
        for path in test_paths:
            excluded = engine._is_excluded(path, [])
            if path.name in ["test.py"]:
                self.assertFalse(excluded, f"{path} should not be excluded")
            else:
                self.assertTrue(excluded, f"{path} should be excluded")
    
    def test_custom_exclusions(self):
        """Test custom exclusion patterns."""
        config = DiscoveryConfig()
        engine = DiscoveryEngine(config)
        
        custom_exclusions = ["*.secret", "private/*"]
        
        self.assertTrue(engine._is_excluded(Path("config.secret"), custom_exclusions))
        self.assertTrue(engine._is_excluded(Path("private/keys"), custom_exclusions))
        self.assertFalse(engine._is_excluded(Path("public/config"), custom_exclusions))
    
    # --- Convenience Functions ---
    
    def test_run_discovery_cycle(self):
        """Test run_discovery_cycle convenience function."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            
            config = DiscoveryConfig()
            config.roots = [DiscoveryRoot(path=root, name="Test", enabled=True)]
            
            projects = run_discovery_cycle(config)
            
            self.assertGreaterEqual(len(projects), 1)
    
    def test_discover_root_function(self):
        """Test discover_root convenience function."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            
            projects = discover_root(root)
            
            self.assertGreaterEqual(len(projects), 1)


if __name__ == "__main__":
    unittest.main()
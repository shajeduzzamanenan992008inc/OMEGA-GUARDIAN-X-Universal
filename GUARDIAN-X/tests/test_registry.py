from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from ogx.registry import (
    RegistryV2,
    ProjectRecord,
    RepositoryRecord,
    WorkspaceRecord,
    HostRecord,
    ProjectLifecycleState,
    WorkspaceStatus,
    RegistryError,
    RegistryNotFoundError,
)


class RegistryV2Tests(unittest.TestCase):
    """Tests for Registry V2."""
    
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test-registry.db"
        self.registry = RegistryV2(self.db_path)
    
    def tearDown(self):
        self.registry.close()
        self.temp_dir.cleanup()
    
    # --- Project Tests ---
    
    def test_register_project(self):
        """Test registering a new project."""
        project_id = uuid4()
        project = self.registry.register_project(
            project_id=project_id,
            canonical_name="Test Project",
            description="A test project",
        )
        
        self.assertEqual(project.project_id, project_id)
        self.assertEqual(project.canonical_name, "Test Project")
        self.assertEqual(project.description, "A test project")
        self.assertEqual(project.lifecycle_state, ProjectLifecycleState.ACTIVE)
    
    def test_register_project_with_identity_hash(self):
        """Test registering project with identity hash."""
        project_id = uuid4()
        project = self.registry.register_project(
            project_id=project_id,
            canonical_name="Hash Project",
            identity_hash="sha256:abc123",
        )
        
        self.assertEqual(project.identity_hash, "sha256:abc123")
    
    def test_get_project(self):
        """Test getting a project by ID."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Get Test")
        
        project = self.registry.get_project(project_id)
        self.assertEqual(project.project_id, project_id)
        self.assertEqual(project.canonical_name, "Get Test")
    
    def test_get_nonexistent_project_raises(self):
        """Test getting non-existent project raises RegistryNotFoundError."""
        with self.assertRaises(RegistryNotFoundError):
            self.registry.get_project(uuid4())
    
    def test_update_project(self):
        """Test updating project fields."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Original")
        
        updated = self.registry.update_project(
            project_id=project_id,
            canonical_name="Updated",
            description="New description",
        )
        
        self.assertEqual(updated.canonical_name, "Updated")
        self.assertEqual(updated.description, "New description")
        
        # Verify persistence
        refreshed = self.registry.get_project(project_id)
        self.assertEqual(refreshed.canonical_name, "Updated")
    
    def test_archive_project(self):
        """Test archiving a project."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Archive Test")
        
        archived = self.registry.archive_project(project_id)
        self.assertEqual(archived.lifecycle_state, ProjectLifecycleState.ARCHIVED)
    
    def test_delete_project(self):
        """Test soft deleting a project."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Delete Test")
        
        result = self.registry.delete_project(project_id)
        self.assertTrue(result)
        
        deleted = self.registry.get_project(project_id)
        self.assertEqual(deleted.lifecycle_state, ProjectLifecycleState.DELETED)
    
    def test_list_projects(self):
        """Test listing projects."""
        ids = [uuid4() for _ in range(3)]
        for i, pid in enumerate(ids):
            self.registry.register_project(project_id=pid, canonical_name=f"Project {i}")
        
        projects = self.registry.list_projects()
        self.assertEqual(len(projects), 3)
    
    def test_list_projects_with_filter(self):
        """Test listing projects with lifecycle filter."""
        pid1 = uuid4()
        pid2 = uuid4()
        self.registry.register_project(project_id=pid1, canonical_name="Active")
        self.registry.register_project(project_id=pid2, canonical_name="Archived")
        self.registry.archive_project(pid2)
        
        active = self.registry.list_projects(lifecycle_state=ProjectLifecycleState.ACTIVE)
        archived = self.registry.list_projects(lifecycle_state=ProjectLifecycleState.ARCHIVED)
        
        self.assertEqual(len(active), 1)
        self.assertEqual(len(archived), 1)
    
    def test_find_project_by_identity(self):
        """Test finding project by identity evidence."""
        project_id = uuid4()
        self.registry.register_project(
            project_id=project_id,
            canonical_name="Identity Test",
            identity_hash="sha256:abc123",
        )
        
        # Find by project_id
        found = self.registry.find_project_by_identity(project_id=project_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.project_id, project_id)
        
        # Find by canonical_name
        found = self.registry.find_project_by_identity(canonical_name="Identity Test")
        self.assertIsNotNone(found)
        
        # Find by identity_hash
        found = self.registry.find_project_by_identity(identity_hash="sha256:abc123")
        self.assertIsNotNone(found)
    
    # --- Repository Tests ---
    
    def test_register_repository(self):
        """Test registering a repository."""
        project_id = uuid4()
        repo_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Repo Test")
        
        repo = self.registry.register_repository(
            repository_id=repo_id,
            project_id=project_id,
            provider="github",
            repository_url="https://github.com/org/repo",
            normalized_repository_url="github.com/org/repo",
            repository_name="repo",
        )
        
        self.assertEqual(repo.repository_id, repo_id)
        self.assertEqual(repo.provider, "github")
    
    def test_get_repository(self):
        """Test getting a repository."""
        project_id = uuid4()
        repo_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Repo Get")
        self.registry.register_repository(
            repository_id=repo_id,
            project_id=project_id,
            provider="gitlab",
            repository_url="https://gitlab.com/org/repo",
            normalized_repository_url="gitlab.com/org/repo",
        )
        
        repo = self.registry.get_repository(repo_id)
        self.assertEqual(repo.provider, "gitlab")
    
    def test_find_repository_by_normalized_url(self):
        """Test finding repository by normalized URL."""
        project_id = uuid4()
        repo_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Find Repo")
        self.registry.register_repository(
            repository_id=repo_id,
            project_id=project_id,
            provider="github",
            repository_url="https://github.com/org/repo.git",
            normalized_repository_url="github.com/org/repo",
        )
        
        found = self.registry.find_repository_by_normalized_url("github.com/org/repo")
        self.assertIsNotNone(found)
        self.assertEqual(found.repository_id, repo_id)
    
    def test_list_repositories(self):
        """Test listing repositories for a project."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="List Repos")
        
        for i in range(2):
            self.registry.register_repository(
                repository_id=uuid4(),
                project_id=project_id,
                provider="github",
                repository_url=f"https://github.com/org/repo{i}",
                normalized_repository_url=f"github.com/org/repo{i}",
            )
        
        repos = self.registry.list_repositories(project_id)
        self.assertEqual(len(repos), 2)
    
    # --- Workspace Tests ---
    
    def test_register_workspace(self):
        """Test registering a workspace."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="WS Test")
        
        ws = self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path/to/project",
            normalized_location="/normalized/path/to/project",
            display_name="My Workspace",
        )
        
        self.assertEqual(ws.workspace_id, ws_id)
        self.assertEqual(ws.display_name, "My Workspace")
        self.assertEqual(ws.status, WorkspaceStatus.ACTIVE)
    
    def test_get_workspace(self):
        """Test getting a workspace."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="WS Get")
        self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path",
            normalized_location="/norm/path",
        )
        
        ws = self.registry.get_workspace(ws_id)
        self.assertEqual(ws.workspace_id, ws_id)
    
    def test_find_workspace_by_normalized_location(self):
        """Test finding workspace by normalized location."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Find WS")
        self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path",
            normalized_location="/normalized/path",
        )
        
        found = self.registry.find_workspace_by_normalized_location("/normalized/path")
        self.assertIsNotNone(found)
        self.assertEqual(found.workspace_id, ws_id)
    
    def test_update_workspace_location(self):
        """Test updating workspace location (relocation)."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Relocate WS")
        self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/old/path",
            normalized_location="/old/path",
        )
        
        updated = self.registry.update_workspace_location(
            workspace_id=ws_id,
            location_reference="/new/path",
            normalized_location="/new/path",
        )
        
        self.assertEqual(updated.location_reference, "/new/path")
        self.assertEqual(updated.normalized_location, "/new/path")
    
    def test_update_workspace_status(self):
        """Test updating workspace status."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Status WS")
        self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path",
            normalized_location="/norm/path",
        )
        
        updated = self.registry.update_workspace_status(ws_id, WorkspaceStatus.INACCESSIBLE)
        self.assertEqual(updated.status, WorkspaceStatus.INACCESSIBLE)
    
    def test_list_workspaces(self):
        """Test listing workspaces."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="List WS")
        
        for i in range(2):
            self.registry.register_workspace(
                workspace_id=uuid4(),
                project_id=project_id,
                location_reference=f"/path{i}",
                normalized_location=f"/norm/path{i}",
            )
        
        workspaces = self.registry.list_workspaces(project_id)
        self.assertEqual(len(workspaces), 2)
    
    def test_remove_workspace(self):
        """Test removing a workspace (soft delete)."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Remove WS")
        self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path",
            normalized_location="/norm/path",
        )
        
        result = self.registry.remove_workspace(ws_id)
        self.assertTrue(result)
        
        removed = self.registry.get_workspace(ws_id)
        self.assertEqual(removed.status, WorkspaceStatus.ARCHIVED)
    
    # --- Host Tests ---
    
    def test_register_host(self):
        """Test registering a host."""
        host_id = uuid4()
        host = self.registry.register_host(
            host_id=host_id,
            host_type="local",
            host_identifier="localhost",
        )
        
        self.assertEqual(host.host_id, host_id)
        self.assertEqual(host.host_type, "local")
    
    def test_get_host(self):
        """Test getting a host."""
        host_id = uuid4()
        self.registry.register_host(host_id=host_id, host_type="github", host_identifier="github.com")
        
        host = self.registry.get_host(host_id)
        self.assertEqual(host.host_type, "github")
    
    def test_link_project_host(self):
        """Test linking project to host."""
        project_id = uuid4()
        host_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Link Test")
        self.registry.register_host(host_id=host_id, host_type="local")
        
        self.registry.link_project_host(project_id, host_id)
        
        hosts = self.registry.get_hosts_for_project(project_id)
        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0].host_id, host_id)
    
    # --- Integrity Tests ---
    
    def test_check_integrity(self):
        """Test database integrity check."""
        self.registry.register_project(
            project_id=uuid4(),
            canonical_name="Integrity Test"
        )
        
        result = self.registry.check_integrity()
        
        self.assertTrue(result["integrity_ok"])
        self.assertEqual(result["counts"]["projects"], 1)
    
    def test_backup(self):
        """Test database backup."""
        self.registry.register_project(
            project_id=uuid4(),
            canonical_name="Backup Test"
        )
        
        with tempfile.TemporaryDirectory() as tmp:
            backup_path = Path(tmp) / "backup.db"
            result_path = self.registry.backup(backup_path)
            
            self.assertTrue(result_path.exists())
            self.assertEqual(result_path, backup_path)
    
    # --- Duplicate Prevention ---
    
    def test_register_same_project_id_updates(self):
        """Test registering same project_id updates existing record."""
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Original")
        
        # Register again with different name - should update
        updated = self.registry.register_project(
            project_id=project_id,
            canonical_name="Updated",
        )
        
        self.assertEqual(updated.canonical_name, "Updated")
        
        # Verify only one project exists
        projects = self.registry.list_projects()
        self.assertEqual(len(projects), 1)
    
    def test_register_same_workspace_id_updates(self):
        """Test registering same workspace_id updates existing record."""
        project_id = uuid4()
        ws_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="WS Dup")
        self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path",
            normalized_location="/norm/path",
        )
        
        # Register again with different display_name - should update
        updated = self.registry.register_workspace(
            workspace_id=ws_id,
            project_id=project_id,
            location_reference="/path",
            normalized_location="/norm/path",
            display_name="Updated Display",
        )
        
        self.assertEqual(updated.display_name, "Updated Display")


if __name__ == "__main__":
    unittest.main()
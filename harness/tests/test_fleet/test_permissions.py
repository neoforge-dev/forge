"""
Tests for Permission Pre-loader (HRN-019)
==========================================

Test coverage for agent permission management system.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forge_harness.fleet.permissions import (
    DEFAULT_PERMISSIONS,
    PermissionPreloader,
    PermissionProfile,
    PermissionVerificationResult,
    create_permission_preloader,
)


@pytest.fixture
def temp_config_path(tmp_path: Path) -> Path:
    """Create temporary config directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return config_dir / "agent_permissions.json"


@pytest.fixture
def sample_config(temp_config_path: Path) -> Path:
    """Create a sample permission config file."""
    config = {
        "agents": {
            "backend-engineer": {
                "permissions": [
                    "file:read",
                    "file:write",
                    "git:commit",
                    "test:run",
                ],
                "domain_permissions": {
                    "production": ["deployment:prod"],
                    "testing": ["test:debug"],
                },
                "critical_permissions": ["file:read", "test:run"],
            },
            "custom-agent": {
                "permissions": ["api:read", "api:write"],
                "domain_permissions": {},
                "critical_permissions": ["api:read"],
            },
        }
    }

    with open(temp_config_path, "w") as f:
        json.dump(config, f, indent=2)

    return temp_config_path


class TestPermissionPreloader:
    """Test PermissionPreloader class."""

    def test_init_default(self):
        """Test initialization with defaults."""
        preloader = PermissionPreloader()

        assert preloader.config_path is None
        assert preloader.fail_on_missing_critical is True
        assert len(preloader.profiles) == 0
        assert len(preloader.applied_permissions) == 0

    def test_init_with_config(self, temp_config_path: Path):
        """Test initialization with config path."""
        preloader = PermissionPreloader(
            config_path=temp_config_path, fail_on_missing_critical=False
        )

        assert preloader.config_path == temp_config_path
        assert preloader.fail_on_missing_critical is False

    def test_load_permissions_default(self):
        """Test loading default permissions when no config file exists."""
        preloader = PermissionPreloader()
        profiles = preloader.load_permissions()

        # Should load default permissions for all agent types
        assert len(profiles) == len(DEFAULT_PERMISSIONS)
        assert "backend-engineer" in profiles
        assert "frontend-builder" in profiles
        assert "orchestrator" in profiles

        # Check backend-engineer default permissions
        backend_profile = profiles["backend-engineer"]
        assert "file:read" in backend_profile.permissions
        assert "file:write" in backend_profile.permissions
        assert "git:commit" in backend_profile.permissions

    def test_load_permissions_from_file(self, sample_config: Path):
        """Test loading permissions from config file."""
        preloader = PermissionPreloader(config_path=sample_config)
        profiles = preloader.load_permissions()

        # Should have custom agents from config
        assert "backend-engineer" in profiles
        assert "custom-agent" in profiles

        # Check custom agent
        custom_profile = profiles["custom-agent"]
        assert "api:read" in custom_profile.permissions
        assert "api:write" in custom_profile.permissions
        assert "api:read" in custom_profile.critical_permissions

    def test_apply_permissions_with_profile(self):
        """Test applying permissions from a loaded profile."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        success = preloader.apply_permissions("backend-engineer")

        assert success is True
        assert "backend-engineer" in preloader.applied_permissions
        applied = preloader.applied_permissions["backend-engineer"]
        assert "file:read" in applied
        assert "file:write" in applied

    def test_apply_permissions_with_explicit_list(self):
        """Test applying explicit permission list."""
        preloader = PermissionPreloader()

        permissions = ["file:read", "git:status"]
        success = preloader.apply_permissions("test-agent", permissions=permissions)

        assert success is True
        assert "test-agent" in preloader.applied_permissions
        applied = preloader.applied_permissions["test-agent"]
        assert set(applied) == set(permissions)

    def test_apply_permissions_with_domain(self, sample_config: Path):
        """Test applying permissions with domain-specific additions."""
        preloader = PermissionPreloader(config_path=sample_config)
        preloader.load_permissions()

        success = preloader.apply_permissions("backend-engineer", domain="production")

        assert success is True
        applied = preloader.applied_permissions["backend-engineer"]
        assert "deployment:prod" in applied  # Domain-specific permission

    def test_apply_permissions_unknown_agent_without_perms(self):
        """Test applying permissions to unknown agent without explicit perms raises error."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        with pytest.raises(ValueError, match="Unknown agent type"):
            preloader.apply_permissions("nonexistent-agent")

    def test_verify_permissions_success(self):
        """Test successful permission verification."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")

        verified = preloader.verify_permissions("forge:tech-001", "backend-engineer")

        assert verified is True
        assert len(preloader.verification_history) == 1

        result = preloader.verification_history[0]
        assert result.agent_id == "forge:tech-001"
        assert result.agent_type == "backend-engineer"
        assert result.verified is True
        assert len(result.critical_missing) == 0

    def test_verify_permissions_missing_critical(self):
        """Test verification fails when critical permissions missing."""
        preloader = PermissionPreloader(fail_on_missing_critical=True)
        preloader.load_permissions()

        # Apply only some permissions (missing critical ones)
        preloader.applied_permissions["backend-engineer"] = ["git:status"]

        with pytest.raises(RuntimeError, match="missing critical permissions"):
            preloader.verify_permissions("forge:tech-001", "backend-engineer")

    def test_verify_permissions_missing_critical_no_fail(self):
        """Test verification returns False but doesn't raise when fail_on_missing_critical=False."""
        preloader = PermissionPreloader(fail_on_missing_critical=False)
        preloader.load_permissions()

        # Apply only some permissions (missing critical ones)
        preloader.applied_permissions["backend-engineer"] = ["git:status"]

        verified = preloader.verify_permissions("forge:tech-001", "backend-engineer")

        assert verified is False
        result = preloader.verification_history[0]
        assert len(result.critical_missing) > 0

    def test_verify_permissions_auto_detect_agent_type(self):
        """Test auto-detection of agent type from agent ID."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")

        # Should auto-detect backend-engineer from "tech" in ID
        verified = preloader.verify_permissions("forge:tech-001")

        assert verified is True

    def test_verify_permissions_unknown_agent_type(self):
        """Test verification fails gracefully for unknown agent type."""
        preloader = PermissionPreloader(fail_on_missing_critical=False)

        verified = preloader.verify_permissions("unknown-agent-123", "unknown-type")

        assert verified is False
        result = preloader.verification_history[0]
        assert result.error_message is not None

    def test_get_permissions(self):
        """Test getting permissions for an agent type."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        permissions = preloader.get_permissions("backend-engineer")

        assert len(permissions) > 0
        assert "file:read" in permissions
        assert "file:write" in permissions

    def test_get_permissions_with_domain(self, sample_config: Path):
        """Test getting permissions with domain-specific additions."""
        preloader = PermissionPreloader(config_path=sample_config)
        preloader.load_permissions()

        permissions = preloader.get_permissions("backend-engineer", domain="production")

        assert "deployment:prod" in permissions

    def test_get_permissions_unknown_agent(self):
        """Test getting permissions for unknown agent returns empty list."""
        preloader = PermissionPreloader()

        permissions = preloader.get_permissions("nonexistent-agent")

        assert permissions == []

    def test_get_verification_history_no_filter(self):
        """Test getting all verification history."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")

        preloader.verify_permissions("agent-1", "backend-engineer")
        preloader.verify_permissions("agent-2", "backend-engineer")

        history = preloader.get_verification_history()

        assert len(history) == 2

    def test_get_verification_history_filter_by_agent_id(self):
        """Test filtering verification history by agent ID."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")

        preloader.verify_permissions("agent-1", "backend-engineer")
        preloader.verify_permissions("agent-2", "backend-engineer")

        history = preloader.get_verification_history(agent_id="agent-1")

        assert len(history) == 1
        assert history[0].agent_id == "agent-1"

    def test_get_verification_history_filter_by_agent_type(self):
        """Test filtering verification history by agent type."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")
        preloader.apply_permissions("frontend-builder")

        preloader.verify_permissions("agent-1", "backend-engineer")
        preloader.verify_permissions("agent-2", "frontend-builder")

        history = preloader.get_verification_history(agent_type="backend-engineer")

        assert len(history) == 1
        assert history[0].agent_type == "backend-engineer"

    def test_export_config(self, tmp_path: Path):
        """Test exporting permission config to JSON."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        output_path = tmp_path / "exported_config.json"
        preloader.export_config(output_path)

        assert output_path.exists()

        # Load and verify
        with open(output_path) as f:
            config = json.load(f)

        assert "agents" in config
        assert "backend-engineer" in config["agents"]
        assert "permissions" in config["agents"]["backend-engineer"]

    def test_reset_agent_permissions(self):
        """Test resetting agent permissions to defaults."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        # Modify permissions
        preloader.add_permission("backend-engineer", "custom:permission")

        # Reset to defaults
        success = preloader.reset_agent_permissions("backend-engineer")

        assert success is True
        profile = preloader.profiles["backend-engineer"]
        assert "custom:permission" not in profile.permissions

    def test_reset_agent_permissions_unknown_agent(self):
        """Test resetting unknown agent returns False."""
        preloader = PermissionPreloader()

        success = preloader.reset_agent_permissions("nonexistent-agent")

        assert success is False

    def test_add_permission(self):
        """Test adding a permission to an agent type."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        success = preloader.add_permission("backend-engineer", "new:permission")

        assert success is True
        profile = preloader.profiles["backend-engineer"]
        assert "new:permission" in profile.permissions

    def test_add_permission_critical(self):
        """Test adding a critical permission."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        success = preloader.add_permission("backend-engineer", "critical:permission", critical=True)

        assert success is True
        profile = preloader.profiles["backend-engineer"]
        assert "critical:permission" in profile.permissions
        assert "critical:permission" in profile.critical_permissions

    def test_add_permission_unknown_agent(self):
        """Test adding permission to unknown agent returns False."""
        preloader = PermissionPreloader()

        success = preloader.add_permission("nonexistent-agent", "permission")

        assert success is False

    def test_remove_permission(self):
        """Test removing a permission from an agent type."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        success = preloader.remove_permission("backend-engineer", "git:push")

        assert success is True
        profile = preloader.profiles["backend-engineer"]
        assert "git:push" not in profile.permissions

    def test_remove_permission_unknown_agent(self):
        """Test removing permission from unknown agent returns False."""
        preloader = PermissionPreloader()

        success = preloader.remove_permission("nonexistent-agent", "permission")

        assert success is False

    def test_detect_agent_type_backend(self):
        """Test agent type detection for backend engineer."""
        agent_type = PermissionPreloader._detect_agent_type("forge:tech-001")

        assert agent_type == "backend-engineer"

    def test_detect_agent_type_frontend(self):
        """Test agent type detection for frontend builder."""
        agent_type = PermissionPreloader._detect_agent_type("forge:frontend-001")

        assert agent_type == "frontend-builder"

    def test_detect_agent_type_debug(self):
        """Test agent type detection for debug detective."""
        agent_type = PermissionPreloader._detect_agent_type("forge:debug-001")

        assert agent_type == "debug-detective"

    def test_detect_agent_type_qa(self):
        """Test agent type detection for QA specialist."""
        agent_type = PermissionPreloader._detect_agent_type("forge:qa-001")

        assert agent_type == "qa-specialist"

    def test_detect_agent_type_content(self):
        """Test agent type detection for content creator."""
        agent_type = PermissionPreloader._detect_agent_type("forge:content-001")

        assert agent_type == "content-creator"

    def test_detect_agent_type_orchestrator(self):
        """Test agent type detection for orchestrator."""
        agent_type = PermissionPreloader._detect_agent_type("forge:orchestrator-001")

        assert agent_type == "orchestrator"

    def test_detect_agent_type_unknown(self):
        """Test agent type detection for unknown pattern."""
        agent_type = PermissionPreloader._detect_agent_type("unknown-agent-123")

        assert agent_type is None


class TestPermissionProfile:
    """Test PermissionProfile dataclass."""

    def test_create_profile(self):
        """Test creating a permission profile."""
        profile = PermissionProfile(
            agent_type="test-agent",
            permissions={"file:read", "file:write"},
            critical_permissions={"file:read"},
        )

        assert profile.agent_type == "test-agent"
        assert len(profile.permissions) == 2
        assert len(profile.critical_permissions) == 1
        assert profile.verified is False


class TestPermissionVerificationResult:
    """Test PermissionVerificationResult dataclass."""

    def test_create_result(self):
        """Test creating a verification result."""
        now = datetime.now(UTC)
        result = PermissionVerificationResult(
            agent_id="test-agent",
            agent_type="backend-engineer",
            verified=True,
            missing_permissions=[],
            critical_missing=[],
            timestamp=now,
        )

        assert result.agent_id == "test-agent"
        assert result.verified is True
        assert result.error_message is None


class TestCreatePermissionPreloader:
    """Test factory function."""

    def test_create_default(self):
        """Test creating preloader with defaults."""
        preloader = create_permission_preloader()

        assert isinstance(preloader, PermissionPreloader)
        assert len(preloader.profiles) > 0  # Should have loaded defaults

    def test_create_with_config(self, sample_config: Path):
        """Test creating preloader with custom config."""
        preloader = create_permission_preloader(config_path=sample_config)

        assert preloader.config_path == sample_config
        assert "custom-agent" in preloader.profiles


class TestIntegrationScenarios:
    """Test complete integration scenarios."""

    def test_full_permission_lifecycle(self, sample_config: Path):
        """Test complete permission lifecycle from load to verify."""
        # Create and configure
        preloader = create_permission_preloader(config_path=sample_config)

        # Apply permissions with domain
        preloader.apply_permissions("backend-engineer", domain="production")

        # Verify
        verified = preloader.verify_permissions("forge:tech-001", "backend-engineer")
        assert verified is True

        # Export
        export_path = sample_config.parent / "exported.json"
        preloader.export_config(export_path)
        assert export_path.exists()

    def test_multi_agent_verification(self):
        """Test verifying multiple agents in a fleet."""
        preloader = create_permission_preloader()

        # Apply permissions for different agent types
        preloader.apply_permissions("backend-engineer")
        preloader.apply_permissions("frontend-builder")
        preloader.apply_permissions("qa-specialist")

        # Verify each agent
        agents = [
            ("forge:tech-001", "backend-engineer"),
            ("forge:frontend-001", "frontend-builder"),
            ("forge:qa-001", "qa-specialist"),
        ]

        for agent_id, agent_type in agents:
            verified = preloader.verify_permissions(agent_id, agent_type)
            assert verified is True

        # Check history
        history = preloader.get_verification_history()
        assert len(history) == 3

    def test_permission_modification_workflow(self):
        """Test adding/removing permissions and re-verifying."""
        preloader = create_permission_preloader()

        # Initial setup
        preloader.apply_permissions("backend-engineer")
        assert preloader.verify_permissions("agent-1", "backend-engineer") is True

        # Add custom permission
        preloader.add_permission("backend-engineer", "custom:deploy")
        preloader.apply_permissions("backend-engineer")

        # Verify still works
        assert preloader.verify_permissions("agent-2", "backend-engineer") is True

        # Remove permission
        preloader.remove_permission("backend-engineer", "custom:deploy")
        preloader.apply_permissions("backend-engineer")

        # Verify still works
        assert preloader.verify_permissions("agent-3", "backend-engineer") is True

"""Tests for forge_harness.fleet.permissions module."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from forge_harness.fleet.permissions import (
    CRITICAL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    DOMAIN_PERMISSIONS,
    PermissionPreloader,
    PermissionProfile,
    PermissionVerificationResult,
    create_permission_preloader,
)


class TestPermissionProfile:
    """Tests for PermissionProfile dataclass."""

    def test_default_values(self):
        """Test PermissionProfile default values."""
        profile = PermissionProfile(agent_type="test-agent")
        assert profile.agent_type == "test-agent"
        assert profile.permissions == set()
        assert profile.domain_permissions == {}
        assert profile.critical_permissions == set()
        assert profile.last_applied is None
        assert profile.verified is False

    def test_with_permissions(self):
        """Test PermissionProfile with permissions."""
        profile = PermissionProfile(
            agent_type="backend-engineer",
            permissions={"file:read", "file:write"},
            critical_permissions={"file:read"},
        )
        assert len(profile.permissions) == 2
        assert "file:read" in profile.permissions
        assert "file:write" in profile.permissions
        assert "file:read" in profile.critical_permissions


class TestPermissionVerificationResult:
    """Tests for PermissionVerificationResult dataclass."""

    def test_basic_creation(self):
        """Test creating a verification result."""
        result = PermissionVerificationResult(
            agent_id="agent-001",
            agent_type="backend-engineer",
            verified=True,
            missing_permissions=[],
            critical_missing=[],
            timestamp=datetime.now(),
        )
        assert result.agent_id == "agent-001"
        assert result.verified is True

    def test_with_error_message(self):
        """Test verification result with error."""
        result = PermissionVerificationResult(
            agent_id="agent-001",
            agent_type="unknown",
            verified=False,
            missing_permissions=[],
            critical_missing=[],
            timestamp=datetime.now(),
            error_message="Unknown agent type",
        )
        assert result.error_message == "Unknown agent type"


class TestPermissionPreloader:
    """Tests for PermissionPreloader class."""

    def test_init_default(self):
        """Test default initialization."""
        preloader = PermissionPreloader()
        assert preloader.config_path is None
        assert preloader.fail_on_missing_critical is True
        assert preloader.profiles == {}
        assert preloader.applied_permissions == {}
        assert preloader.verification_history == []

    def test_init_custom_config(self):
        """Test initialization with custom config."""
        config_path = Path("/tmp/test_config.json")
        preloader = PermissionPreloader(config_path=config_path, fail_on_missing_critical=False)
        assert preloader.config_path == config_path
        assert preloader.fail_on_missing_critical is False

    def test_load_permissions_defaults(self):
        """Test loading default permissions."""
        preloader = PermissionPreloader()
        profiles = preloader.load_permissions()

        assert len(profiles) > 0
        assert "orchestrator" in profiles
        assert "backend-engineer" in profiles

        # Check default permissions are loaded
        profile = profiles["backend-engineer"]
        assert "file:read" in profile.permissions
        assert "file:write" in profile.permissions

    def test_load_permissions_from_file(self):
        """Test loading permissions from config file."""
        config_content = json.dumps(
            {
                "agents": {
                    "custom-agent": {
                        "permissions": ["file:read", "command:exec"],
                        "critical_permissions": ["file:read"],
                        "domain_permissions": {"production": ["deploy:prod"]},
                    }
                }
            }
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=config_content)):
                preloader = PermissionPreloader()
                profiles = preloader.load_permissions(Path("/tmp/config.json"))

        assert "custom-agent" in profiles
        assert "file:read" in profiles["custom-agent"].permissions

    def test_load_permissions_nonexistent_file(self):
        """Test loading from nonexistent file falls back to defaults."""
        with patch("pathlib.Path.exists", return_value=False):
            preloader = PermissionPreloader()
            profiles = preloader.load_permissions()

        assert len(profiles) > 0

    def test_apply_permissions_existing_agent(self):
        """Test applying permissions to existing agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.apply_permissions("backend-engineer", ["file:read", "git:commit"])

        assert result is True
        assert "backend-engineer" in preloader.applied_permissions

    def test_apply_permissions_with_domain(self):
        """Test applying permissions with domain."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.apply_permissions("backend-engineer", domain="production")

        assert result is True
        applied = preloader.applied_permissions["backend-engineer"]
        # Domain permissions should be added
        assert "deployment:stage" in applied or "deployment:prod" in applied

    def test_apply_permissions_new_agent(self):
        """Test applying permissions to new agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.apply_permissions("new-agent", ["file:read", "command:exec"])

        assert result is True
        assert "new-agent" in preloader.profiles
        assert "new-agent" in preloader.applied_permissions

    def test_apply_permissions_unknown_agent_no_permissions(self):
        """Test applying permissions to unknown agent without permissions raises."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        with pytest.raises(ValueError, match="Unknown agent type"):
            preloader.apply_permissions("unknown-agent")

    def test_verify_permissions_success(self):
        """Test successful permission verification."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")

        result = preloader.verify_permissions("forge:tech-001")

        assert result is True

    def test_verify_permissions_missing_critical_raises(self):
        """Test that missing critical permissions raise when configured."""
        preloader = PermissionPreloader(fail_on_missing_critical=True)
        preloader.load_permissions()
        # Don't apply permissions, so critical ones are missing

        with pytest.raises(RuntimeError, match="missing critical permissions"):
            preloader.verify_permissions("forge:tech-001")

    def test_verify_permissions_missing_critical_no_raise(self):
        """Test missing critical permissions don't raise when configured."""
        preloader = PermissionPreloader(fail_on_missing_critical=False)
        preloader.load_permissions()

        result = preloader.verify_permissions("forge:tech-001")

        assert result is False

    def test_verify_permissions_unknown_agent(self):
        """Test verifying permissions for unknown agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.verify_permissions("unknown-agent-id", agent_type="unknown-type")

        assert result is False
        assert len(preloader.verification_history) == 1
        assert preloader.verification_history[0].error_message is not None

    def test_get_permissions_existing_agent(self):
        """Test getting permissions for existing agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        perms = preloader.get_permissions("backend-engineer")

        assert len(perms) > 0
        assert "file:read" in perms

    def test_get_permissions_with_domain(self):
        """Test getting permissions with domain from profile."""
        preloader = PermissionPreloader()
        # Load from config with domain_permissions
        config_content = json.dumps(
            {
                "agents": {
                    "backend-engineer": {
                        "permissions": ["file:read"],
                        "critical_permissions": [],
                        "domain_permissions": {
                            "production": ["deployment:stage", "deployment:prod"]
                        },
                    }
                }
            }
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=config_content)):
                preloader.load_permissions(Path("/tmp/config.json"))

        perms = preloader.get_permissions("backend-engineer", domain="production")

        assert "deployment:stage" in perms
        assert "deployment:prod" in perms

    def test_get_permissions_unknown_agent(self):
        """Test getting permissions for unknown agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        perms = preloader.get_permissions("unknown-agent")

        assert perms == []

    def test_get_verification_history_all(self):
        """Test getting all verification history."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")
        preloader.verify_permissions("forge:tech-001")

        history = preloader.get_verification_history()

        assert len(history) == 1

    def test_get_verification_history_filter_by_agent_id(self):
        """Test filtering verification history by agent ID."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")
        preloader.verify_permissions("agent-001")
        preloader.verify_permissions("agent-002")

        history = preloader.get_verification_history(agent_id="agent-001")

        assert len(history) == 1
        assert history[0].agent_id == "agent-001"

    def test_get_verification_history_filter_by_agent_type(self):
        """Test filtering verification history by agent type."""
        preloader = PermissionPreloader()
        preloader.load_permissions()
        preloader.apply_permissions("backend-engineer")
        preloader.verify_permissions("agent-001", agent_type="backend-engineer")

        history = preloader.get_verification_history(agent_type="backend-engineer")

        assert len(history) == 1

    def test_export_config(self, tmp_path):
        """Test exporting permission config."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        output_path = tmp_path / "permissions.json"
        preloader.export_config(output_path)

        assert output_path.exists()

        with open(output_path) as f:
            config = json.load(f)

        assert "agents" in config
        assert "backend-engineer" in config["agents"]

    def test_reset_agent_permissions_success(self):
        """Test resetting agent permissions."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        # Add extra permission
        preloader.profiles["orchestrator"].permissions.add("extra:perm")

        result = preloader.reset_agent_permissions("orchestrator")

        assert result is True
        assert "extra:perm" not in preloader.profiles["orchestrator"].permissions

    def test_reset_agent_permissions_unknown(self):
        """Test resetting unknown agent returns False."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.reset_agent_permissions("unknown-agent")

        assert result is False

    def test_add_permission_success(self):
        """Test adding permission to agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.add_permission("backend-engineer", "new:permission")

        assert result is True
        assert "new:permission" in preloader.profiles["backend-engineer"].permissions

    def test_add_permission_as_critical(self):
        """Test adding permission as critical."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.add_permission("backend-engineer", "critical:perm", critical=True)

        assert result is True
        assert "critical:perm" in preloader.profiles["backend-engineer"].critical_permissions

    def test_add_permission_unknown_agent(self):
        """Test adding permission to unknown agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.add_permission("unknown-agent", "some:perm")

        assert result is False

    def test_remove_permission_success(self):
        """Test removing permission from agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        # Add permission first
        preloader.add_permission("backend-engineer", "temp:perm")

        result = preloader.remove_permission("backend-engineer", "temp:perm")

        assert result is True
        assert "temp:perm" not in preloader.profiles["backend-engineer"].permissions

    def test_remove_permission_also_removes_critical(self):
        """Test removing permission also removes from critical."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        # Add as critical
        preloader.add_permission("backend-engineer", "removable:perm", critical=True)

        # Now remove
        result = preloader.remove_permission("backend-engineer", "removable:perm")

        assert result is True
        assert "removable:perm" not in preloader.profiles["backend-engineer"].critical_permissions

    def test_remove_permission_unknown_agent(self):
        """Test removing permission from unknown agent."""
        preloader = PermissionPreloader()
        preloader.load_permissions()

        result = preloader.remove_permission("unknown-agent", "some:perm")

        assert result is False


class TestDetectAgentType:
    """Tests for _detect_agent_type method."""

    def test_detect_tech(self):
        """Test detecting backend-engineer from tech."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:tech-001")
        assert agent_type == "backend-engineer"

    def test_detect_frontend(self):
        """Test detecting frontend-builder."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:frontend-001")
        assert agent_type == "frontend-builder"

    def test_detect_ui(self):
        """Test detecting frontend from ui."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:ui-agent")
        assert agent_type == "frontend-builder"

    def test_detect_debug(self):
        """Test detecting debug-detective."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:debug-001")
        assert agent_type == "debug-detective"

    def test_detect_qa(self):
        """Test detecting qa-specialist."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:qa-001")
        assert agent_type == "qa-specialist"

    def test_detect_content(self):
        """Test detecting content-creator."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:content-001")
        assert agent_type == "content-creator"

    def test_detect_orchestrator(self):
        """Test detecting orchestrator."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:orchestrator")
        assert agent_type == "orchestrator"

    def test_detect_unknown(self):
        """Test detecting unknown agent returns None."""
        preloader = PermissionPreloader()
        agent_type = preloader._detect_agent_type("forge:unknown-001")
        assert agent_type is None


class TestConstants:
    """Tests for module constants."""

    def test_default_permissions_defined(self):
        """Test DEFAULT_PERMISSIONS is defined."""
        assert "orchestrator" in DEFAULT_PERMISSIONS
        assert "backend-engineer" in DEFAULT_PERMISSIONS
        assert len(DEFAULT_PERMISSIONS["backend-engineer"]) > 0

    def test_critical_permissions_defined(self):
        """Test CRITICAL_PERMISSIONS is defined."""
        assert "orchestrator" in CRITICAL_PERMISSIONS
        assert "backend-engineer" in CRITICAL_PERMISSIONS
        # Critical permissions should be subset of default
        for agent_type in CRITICAL_PERMISSIONS:
            default_perms = set(DEFAULT_PERMISSIONS.get(agent_type, []))
            critical_perms = set(CRITICAL_PERMISSIONS[agent_type])
            assert critical_perms.issubset(default_perms)

    def test_domain_permissions_defined(self):
        """Test DOMAIN_PERMISSIONS is defined."""
        assert "production" in DOMAIN_PERMISSIONS
        assert "development" in DOMAIN_PERMISSIONS
        assert "testing" in DOMAIN_PERMISSIONS


class TestCreatePermissionPreloader:
    """Tests for create_permission_preloader function."""

    def test_create_with_defaults(self):
        """Test creating preloader with defaults."""
        preloader = create_permission_preloader()

        assert isinstance(preloader, PermissionPreloader)
        assert len(preloader.profiles) > 0

    def test_create_with_custom_config(self):
        """Test creating preloader with custom config."""
        config_path = Path("/tmp/test.json")

        with patch.object(PermissionPreloader, "load_permissions") as mock_load:
            mock_load.return_value = {}
            preloader = create_permission_preloader(config_path=config_path)

        assert preloader.config_path == config_path

    def test_create_with_fail_on_missing_false(self):
        """Test creating preloader with fail_on_missing_critical=False."""
        preloader = create_permission_preloader(fail_on_missing_critical=False)

        assert preloader.fail_on_missing_critical is False

"""Tests for permission pre-loader module."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from forge_harness.iteration.permission_preloader import (
    PermissionLevel,
    PermissionPreloader,
    PermissionRule,
    PermissionSet,
    create_preloader,
)


class TestPermissionPreloader:
    """Test suite for PermissionPreloader."""

    @pytest.fixture
    def preloader(self):
        """Create a preloader with no config file."""
        return PermissionPreloader(config_path=Path("/tmp/nonexistent.json"))

    @pytest.fixture
    def temp_config(self, tmp_path):
        """Create a temporary config file."""
        config_file = tmp_path / ".forge/permissions.json"
        return config_file

    def test_default_rules_creation(self, preloader):
        """Test that default rules are created correctly."""
        rules = preloader._create_default_rules()

        assert len(rules) > 0
        assert any(r.pattern == "*.py" for r in rules)
        assert any(r.pattern == "*.md" for r in rules)
        assert any(r.pattern == ".env*" for r in rules)
        assert any(r.pattern == "**/node_modules/**" for r in rules)

        # Check that .env files are blocked
        env_rule = next(r for r in rules if r.pattern == ".env*")
        assert env_rule.level == PermissionLevel.NONE
        assert env_rule.reason == "Secrets protection"

    def test_load_config_missing_file(self, preloader):
        """Test loading config when file doesn't exist."""
        permissions = preloader.load_config()

        assert isinstance(permissions, PermissionSet)
        assert len(permissions.rules) > 0
        assert permissions.global_level == PermissionLevel.READ_ONLY
        assert len(permissions.allowed_tools) == 0
        assert len(permissions.blocked_paths) == 0

    def test_load_config_valid_file(self, temp_config):
        """Test loading config from valid file."""
        config_data = {
            "global_level": "write_local",
            "allowed_tools": ["pytest", "mypy"],
            "blocked_paths": ["/tmp/sensitive"],
            "rules": [
                {
                    "pattern": "*.ts",
                    "level": "write_all",
                    "commands": ["npm", "node"],
                    "reason": "TypeScript development",
                }
            ],
        }
        temp_config.write_text(json.dumps(config_data))

        preloader = PermissionPreloader(config_path=temp_config)
        permissions = preloader.load_config()

        assert permissions.global_level == PermissionLevel.WRITE_LOCAL
        assert permissions.allowed_tools == {"pytest", "mypy"}
        assert permissions.blocked_paths == ["/tmp/sensitive"]
        assert len(permissions.rules) == 1
        assert permissions.rules[0].pattern == "*.ts"
        assert permissions.rules[0].level == PermissionLevel.WRITE_ALL
        assert permissions.rules[0].commands == ["npm", "node"]

    def test_load_config_invalid_json(self, temp_config):
        """Test loading config with invalid JSON."""
        temp_config.write_text("{ invalid json }")

        preloader = PermissionPreloader(config_path=temp_config)
        permissions = preloader.load_config()

        # Should fall back to defaults
        assert isinstance(permissions, PermissionSet)
        assert len(permissions.rules) > 0

    def test_load_config_invalid_level(self, temp_config):
        """Test loading config with invalid permission level."""
        config_data = {"global_level": "invalid_level", "rules": []}
        temp_config.write_text(json.dumps(config_data))

        preloader = PermissionPreloader(config_path=temp_config)
        permissions = preloader.load_config()

        # Should fall back to defaults
        assert isinstance(permissions, PermissionSet)

    def test_preload_general_task(self, preloader):
        """Test preloading for general task."""
        result = preloader.preload(task_type="general")

        assert result.success
        assert isinstance(result.permissions, PermissionSet)
        assert isinstance(result.applied_at, datetime)
        assert len(result.warnings) == 0

    def test_preload_test_task(self, preloader):
        """Test preloading for test task."""
        result = preloader.preload(task_type="test")

        assert result.success
        assert "pytest" in result.permissions.allowed_tools
        assert "coverage" in result.permissions.allowed_tools
        assert "mypy" in result.permissions.allowed_tools

    def test_preload_deploy_task(self, preloader):
        """Test preloading for deploy task."""
        result = preloader.preload(task_type="deploy")

        assert result.success
        assert len(result.warnings) > 0
        assert any("manual approval" in w.lower() for w in result.warnings)

    def test_preload_dangerous_task(self, preloader):
        """Test preloading for dangerous task."""
        result = preloader.preload(task_type="dangerous")

        assert result.success
        assert result.permissions.global_level == PermissionLevel.DANGEROUS
        assert len(result.warnings) > 0
        assert any("dangerous mode" in w.lower() for w in result.warnings)

    def test_check_permission_blocked_path(self, preloader):
        """Test permission check for blocked path."""
        permissions = PermissionSet(blocked_paths=["/tmp/sensitive", "secrets/"])

        assert not preloader.check_permission(Path("/tmp/sensitive/data.txt"), "read", permissions)
        assert not preloader.check_permission(Path("secrets/api_key.txt"), "write", permissions)

    def test_check_permission_rule_match(self, preloader):
        """Test permission check with matching rule."""
        permissions = PermissionSet(
            rules=[
                PermissionRule(
                    pattern="*.py", level=PermissionLevel.WRITE_LOCAL, commands=["pytest"]
                )
            ]
        )

        # Should allow write for .py files
        assert preloader.check_permission(Path("test.py"), "write", permissions)

        # Should allow read for .py files
        assert preloader.check_permission(Path("test.py"), "read", permissions)

    def test_check_permission_none_level(self, preloader):
        """Test permission check with NONE level."""
        permissions = PermissionSet(
            rules=[PermissionRule(pattern=".env*", level=PermissionLevel.NONE)]
        )

        assert not preloader.check_permission(Path(".env"), "read", permissions)
        assert not preloader.check_permission(Path(".env.local"), "write", permissions)

    def test_check_permission_global_fallback(self, preloader):
        """Test permission check falls back to global level."""
        permissions = PermissionSet(global_level=PermissionLevel.READ_ONLY, rules=[])

        # Should allow read based on global level
        assert preloader.check_permission(Path("random.txt"), "read", permissions)

        # Should not allow write with READ_ONLY global level
        # (no matching rule with write permission)
        assert not preloader.check_permission(Path("random.txt"), "write", permissions)

    def test_check_permission_none_global(self, preloader):
        """Test permission check with NONE global level."""
        permissions = PermissionSet(global_level=PermissionLevel.NONE, rules=[])

        assert not preloader.check_permission(Path("any.txt"), "read", permissions)

    def test_generate_cli_flags_dangerous(self, preloader):
        """Test CLI flag generation for dangerous mode."""
        permissions = PermissionSet(global_level=PermissionLevel.DANGEROUS)

        flags = preloader.generate_cli_flags(permissions)

        assert "--dangerously-skip-permissions" in flags

    def test_generate_cli_flags_allowed_tools(self, preloader):
        """Test CLI flag generation for allowed tools."""
        permissions = PermissionSet(allowed_tools={"pytest", "mypy", "ruff"})

        flags = preloader.generate_cli_flags(permissions)

        assert "--allowedTools=pytest" in flags
        assert "--allowedTools=mypy" in flags
        assert "--allowedTools=ruff" in flags
        assert "--dangerously-skip-permissions" not in flags

    def test_generate_cli_flags_empty(self, preloader):
        """Test CLI flag generation with no special permissions."""
        permissions = PermissionSet()

        flags = preloader.generate_cli_flags(permissions)

        assert len(flags) == 0

    def test_create_preloader_factory(self):
        """Test factory function."""
        preloader = create_preloader()

        assert isinstance(preloader, PermissionPreloader)
        assert preloader.config_path == Path(".forge/permissions.json")

    def test_create_preloader_with_custom_path(self, temp_config):
        """Test factory function with custom path."""
        preloader = create_preloader(config_path=temp_config)

        assert isinstance(preloader, PermissionPreloader)
        assert preloader.config_path == temp_config

    def test_permission_rule_defaults(self):
        """Test PermissionRule default values."""
        rule = PermissionRule(pattern="*.js", level=PermissionLevel.WRITE_LOCAL)

        assert rule.commands == []
        assert rule.reason == ""

    def test_permission_set_defaults(self):
        """Test PermissionSet default values."""
        pset = PermissionSet()

        assert pset.rules == []
        assert pset.global_level == PermissionLevel.READ_ONLY
        assert pset.allowed_tools == set()
        assert pset.blocked_paths == []

    def test_preload_result_structure(self, preloader):
        """Test PreloadResult structure."""
        result = preloader.preload()

        assert hasattr(result, "success")
        assert hasattr(result, "permissions")
        assert hasattr(result, "warnings")
        assert hasattr(result, "applied_at")
        assert isinstance(result.applied_at, datetime)

    def test_check_permission_write_all(self, preloader):
        """Test permission check with WRITE_ALL level."""
        permissions = PermissionSet(
            rules=[PermissionRule(pattern="*.md", level=PermissionLevel.WRITE_ALL)]
        )

        assert preloader.check_permission(Path("README.md"), "write", permissions)

    def test_check_permission_read_only(self, preloader):
        """Test permission check with READ_ONLY level."""
        permissions = PermissionSet(
            rules=[PermissionRule(pattern="*.txt", level=PermissionLevel.READ_ONLY)]
        )

        assert preloader.check_permission(Path("file.txt"), "read", permissions)

        # READ_ONLY doesn't grant write permission
        assert not preloader.check_permission(Path("file.txt"), "write", permissions)

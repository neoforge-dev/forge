"""
Tests for forge_harness/config.py - Unified Configuration
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from forge_harness.config import ForgeSettings, clear_settings_cache, get_settings


class TestForgeSettingsDefaults:
    """Tests for ForgeSettings default values."""

    def test_default_values(self):
        """Test that defaults are sensible."""
        settings = ForgeSettings()

        # Core defaults
        assert settings.domain is None
        assert settings.project is None
        assert settings.dry_run is False

        # Path defaults
        assert settings.checkpoint_dir == Path(".forge/orchestration_checkpoints")
        assert settings.session_dir == Path(".forge/sessions")
        assert settings.approval_storage_dir == Path(".forge/approvals")
        assert settings.learning_store_dir == Path(".forge/learning")

        # Loop defaults
        assert settings.max_iterations == 100
        assert settings.max_failures_per_feature == 5
        assert settings.checkpoint_interval == 10
        assert settings.timeout_seconds == 3600
        assert settings.approval_timeout_hours == 24.0

        # Logging defaults
        assert settings.log_level == "INFO"
        assert settings.log_format == "text"

    def test_integration_tokens_not_in_repr(self):
        """Test that sensitive tokens are hidden from repr."""
        settings = ForgeSettings(
            notion_api_token="secret-token",
            github_token="gh-secret",
            slack_webhook_url="https://hooks.slack.com/secret",
        )

        repr_str = repr(settings)

        # Sensitive fields should be hidden
        assert "secret-token" not in repr_str
        assert "gh-secret" not in repr_str
        # Non-sensitive fields should be visible
        assert "max_iterations" in repr_str


class TestForgeSettingsValidation:
    """Tests for ForgeSettings validation."""

    def test_log_level_validation_valid(self):
        """Test valid log levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            settings = ForgeSettings(log_level=level)
            assert settings.log_level == level

    def test_log_level_validation_case_insensitive(self):
        """Test log level is case-insensitive."""
        settings = ForgeSettings(log_level="debug")
        assert settings.log_level == "DEBUG"

    def test_log_level_validation_invalid(self):
        """Test invalid log level raises error."""
        with pytest.raises(ValidationError) as exc_info:
            ForgeSettings(log_level="TRACE")

        assert "log_level" in str(exc_info.value)

    def test_log_format_validation_valid(self):
        """Test valid log formats."""
        for fmt in ["text", "json"]:
            settings = ForgeSettings(log_format=fmt)
            assert settings.log_format == fmt

    def test_log_format_validation_invalid(self):
        """Test invalid log format raises error."""
        with pytest.raises(ValidationError) as exc_info:
            ForgeSettings(log_format="xml")

        assert "log_format" in str(exc_info.value)

    def test_positive_int_validation(self):
        """Test positive integer validation."""
        with pytest.raises(ValidationError):
            ForgeSettings(max_iterations=0)

        with pytest.raises(ValidationError):
            ForgeSettings(max_failures_per_feature=-1)

    def test_timeout_validation_too_low(self):
        """Test timeout minimum validation."""
        with pytest.raises(ValidationError) as exc_info:
            ForgeSettings(timeout_seconds=10)

        assert "at least 30" in str(exc_info.value)

    def test_timeout_validation_too_high(self):
        """Test timeout maximum validation."""
        with pytest.raises(ValidationError) as exc_info:
            ForgeSettings(timeout_seconds=100000)

        assert "86400" in str(exc_info.value)


class TestForgeSettingsFromEnv:
    """Tests for loading settings from environment."""

    def test_from_env_with_overrides(self):
        """Test from_env with overrides."""
        settings = ForgeSettings.from_env(
            domain="test-domain",
            project="test-project",
            max_iterations=50,
        )

        assert settings.domain == "test-domain"
        assert settings.project == "test-project"
        assert settings.max_iterations == 50

    def test_from_env_reads_environment(self):
        """Test from_env reads environment variables."""
        env_vars = {
            "FORGE_DOMAIN": "env-domain",
            "FORGE_PROJECT": "env-project",
            "FORGE_DRY_RUN": "true",
            "FORGE_MAX_ITERATIONS": "200",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            settings = ForgeSettings()

            assert settings.domain == "env-domain"
            assert settings.project == "env-project"
            assert settings.dry_run is True
            assert settings.max_iterations == 200

    def test_from_env_override_takes_precedence(self):
        """Test that explicit overrides take precedence over env."""
        env_vars = {
            "FORGE_DOMAIN": "env-domain",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            settings = ForgeSettings.from_env(domain="override-domain")

            assert settings.domain == "override-domain"


class TestForgeSettingsBackwardCompat:
    """Tests for backward compatibility methods."""

    def test_to_harness_config(self):
        """Test conversion to HarnessConfig."""
        settings = ForgeSettings(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=Path("/tmp/checkpoints"),
            notion_api_token="notion-token",
            notion_data_source_id="data-source-123",
            github_token="gh-token",
            dry_run=True,
            meta_learning_enabled=True,
        )

        config = settings.to_harness_config()

        assert config.domain == "test-domain"
        assert config.project == "test-project"
        assert config.checkpoint_dir == Path("/tmp/checkpoints")
        assert config.notion_api_token == "notion-token"
        assert config.notion_database_id == "data-source-123"
        assert config.github_token == "gh-token"
        assert config.dry_run is True
        assert config.meta_learning_enabled is True

    def test_to_loop_config(self):
        """Test conversion to LoopConfig."""
        settings = ForgeSettings(
            domain="test-domain",
            project="test-project",
            max_iterations=50,
            max_failures_per_feature=3,
            test_command="pytest -v",
            timeout_seconds=1800,
            dry_run=True,
            approval_timeout_hours=12.0,
        )

        config = settings.to_loop_config()

        assert config.domain == "test-domain"
        assert config.project == "test-project"
        assert config.max_iterations == 50
        assert config.max_failures_per_feature == 3
        assert config.test_command == "pytest -v"
        assert config.timeout_seconds == 1800
        assert config.dry_run is True
        assert config.approval_timeout_hours == 12.0


class TestForgeSettingsUtilities:
    """Tests for utility properties and methods."""

    def test_effective_notion_database_id_prefers_data_source(self):
        """Test data_source_id takes precedence over database_id."""
        settings = ForgeSettings(
            notion_database_id="db-123",
            notion_data_source_id="ds-456",
        )

        assert settings.effective_notion_database_id == "ds-456"

    def test_effective_notion_database_id_falls_back(self):
        """Test fallback to database_id."""
        settings = ForgeSettings(
            notion_database_id="db-123",
        )

        assert settings.effective_notion_database_id == "db-123"

    def test_has_notion_true(self):
        """Test has_notion when configured."""
        settings = ForgeSettings(
            notion_api_token="token",
            notion_database_id="db-123",
        )

        assert settings.has_notion is True

    def test_has_notion_false_no_token(self):
        """Test has_notion when token missing."""
        settings = ForgeSettings(
            notion_database_id="db-123",
        )

        assert settings.has_notion is False

    def test_has_notion_false_no_db(self):
        """Test has_notion when database missing."""
        settings = ForgeSettings(
            notion_api_token="token",
        )

        assert settings.has_notion is False

    def test_has_github(self):
        """Test has_github property."""
        assert ForgeSettings().has_github is False
        assert ForgeSettings(github_token="token").has_github is True

    def test_has_slack(self):
        """Test has_slack property."""
        assert ForgeSettings().has_slack is False
        assert ForgeSettings(slack_webhook_url="https://...").has_slack is True

    def test_has_posthog(self):
        """Test has_posthog property."""
        assert ForgeSettings().has_posthog is False
        assert ForgeSettings(posthog_api_key="key").has_posthog is True

    def test_has_meta_learning(self):
        """Test has_meta_learning property.

        Meta-learning is enabled by default and uses local LearningStore.
        External bridges (Code Atlas, Tech Diligence) are optional.
        """
        # Default is True - meta-learning enabled by default
        assert ForgeSettings().has_meta_learning is True
        assert ForgeSettings(meta_learning_enabled=True).has_meta_learning is True
        assert ForgeSettings(meta_learning_enabled=False).has_meta_learning is False

    def test_has_external_bridges(self):
        """Test has_external_bridges property for external integration checks."""
        assert ForgeSettings().has_external_bridges is False
        assert ForgeSettings(code_atlas_url="http://...").has_external_bridges is True
        assert ForgeSettings(tech_diligence_url="http://...").has_external_bridges is True
        assert (
            ForgeSettings(
                code_atlas_url="http://...",
                tech_diligence_url="http://...",
            ).has_external_bridges
            is True
        )

    def test_get_project_path_from_project_dir(self):
        """Test get_project_path when project_dir is set."""
        settings = ForgeSettings(project_dir=Path("/explicit/path"))

        assert settings.get_project_path() == Path("/explicit/path")

    def test_get_project_path_from_forge_root(self):
        """Test get_project_path constructed from FORGE root."""
        settings = ForgeSettings(domain="domain-a", project="project-b")

        path = settings.get_project_path(forge_root=Path("/forge"))

        assert path == Path("/forge/domain-a/project-b")

    def test_get_project_path_none(self):
        """Test get_project_path returns None when not configured."""
        settings = ForgeSettings()

        assert settings.get_project_path() is None


class TestGetSettings:
    """Tests for get_settings singleton."""

    def test_get_settings_returns_singleton(self):
        """Test get_settings returns same instance."""
        clear_settings_cache()

        settings1 = get_settings()
        settings2 = get_settings()

        assert settings1 is settings2

    def test_clear_settings_cache(self):
        """Test clearing the settings cache."""
        clear_settings_cache()
        settings1 = get_settings()

        clear_settings_cache()
        settings2 = get_settings()

        # Different instances after cache clear
        assert settings1 is not settings2


class TestEnvironmentVariableMapping:
    """Tests for environment variable name mapping."""

    def test_notion_token_env_name(self):
        """Test NOTION_API_TOKEN mapping (no FORGE_ prefix)."""
        # The Notion token uses standard name without FORGE_ prefix
        env_vars = {"FORGE_NOTION_API_TOKEN": "token123"}

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            settings = ForgeSettings()
            assert settings.notion_api_token == "token123"

    def test_github_token_env_name(self):
        """Test FORGE_GITHUB_TOKEN mapping."""
        env_vars = {"FORGE_GITHUB_TOKEN": "gh-token"}

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            settings = ForgeSettings()
            assert settings.github_token == "gh-token"

    def test_webhook_settings(self):
        """Test webhook settings from environment."""
        env_vars = {
            "FORGE_WEBHOOK_TOKEN": "webhook-secret",
            "FORGE_WEBHOOK_REQUIRE_AUTH": "false",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            settings = ForgeSettings()
            assert settings.webhook_token == "webhook-secret"
            assert settings.webhook_require_auth is False


class TestLoopConfigFactoryMethods:
    """Tests for LoopConfig factory methods."""

    def test_loop_config_from_env(self):
        """Test LoopConfig.from_env() creates config from environment."""
        from forge_harness.ralph_loop import LoopConfig

        env_vars = {
            "FORGE_DOMAIN": "test-domain",
            "FORGE_PROJECT": "test-project",
            "FORGE_MAX_ITERATIONS": "50",
            "FORGE_DRY_RUN": "true",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            config = LoopConfig.from_env()

            assert config.domain == "test-domain"
            assert config.project == "test-project"
            assert config.max_iterations == 50
            assert config.dry_run is True

    def test_loop_config_from_env_with_overrides(self):
        """Test LoopConfig.from_env() accepts overrides."""
        from forge_harness.ralph_loop import LoopConfig

        env_vars = {
            "FORGE_DOMAIN": "env-domain",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            config = LoopConfig.from_env(domain="override-domain")

            assert config.domain == "override-domain"

    def test_loop_config_from_settings(self):
        """Test LoopConfig.from_settings() creates config from ForgeSettings."""
        from forge_harness.ralph_loop import LoopConfig

        settings = ForgeSettings(
            domain="settings-domain",
            project="settings-project",
            max_iterations=25,
            max_failures_per_feature=2,
            test_command="pytest -x",
            timeout_seconds=600,
            dry_run=True,
            approval_timeout_hours=6.0,
        )

        config = LoopConfig.from_settings(settings)

        assert config.domain == "settings-domain"
        assert config.project == "settings-project"
        assert config.max_iterations == 25
        assert config.max_failures_per_feature == 2
        assert config.test_command == "pytest -x"
        assert config.timeout_seconds == 600
        assert config.dry_run is True
        assert config.approval_timeout_hours == 6.0


class TestHarnessConfigFactoryMethods:
    """Tests for HarnessConfig factory methods."""

    def test_harness_config_from_env(self):
        """Test HarnessConfig.from_env() creates config from environment."""
        from forge_harness.harness_registry import HarnessConfig

        env_vars = {
            "FORGE_DOMAIN": "test-domain",
            "FORGE_PROJECT": "test-project",
            "FORGE_DRY_RUN": "true",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            config = HarnessConfig.from_env()

            assert config.domain == "test-domain"
            assert config.project == "test-project"
            assert config.dry_run is True

    def test_harness_config_from_env_with_overrides(self):
        """Test HarnessConfig.from_env() accepts overrides."""
        from forge_harness.harness_registry import HarnessConfig

        env_vars = {
            "FORGE_DOMAIN": "env-domain",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            clear_settings_cache()
            config = HarnessConfig.from_env(domain="override-domain")

            assert config.domain == "override-domain"

    def test_harness_config_from_settings(self):
        """Test HarnessConfig.from_settings() creates config from ForgeSettings."""
        from forge_harness.harness_registry import HarnessConfig

        settings = ForgeSettings(
            domain="settings-domain",
            project="settings-project",
            checkpoint_dir=Path("/tmp/checkpoints"),
            notion_api_token="notion-token",
            notion_data_source_id="data-source-123",
            github_token="gh-token",
            dry_run=True,
            meta_learning_enabled=True,
        )

        config = HarnessConfig.from_settings(settings)

        assert config.domain == "settings-domain"
        assert config.project == "settings-project"
        assert config.checkpoint_dir == Path("/tmp/checkpoints")
        assert config.notion_api_token == "notion-token"
        assert config.notion_database_id == "data-source-123"
        assert config.github_token == "gh-token"
        assert config.dry_run is True
        assert config.meta_learning_enabled is True

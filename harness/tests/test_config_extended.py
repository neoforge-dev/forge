"""Extended tests for forge_harness.config — env loading, defaults, validation, paths.

Coverage targets (0% → high%):
- ForgeSettings defaults
- Environment variable loading (FORGE_ prefix)
- Validators: log_level, log_format, positive ints, timeout
- Factory method: from_env()
- Property methods: has_notion, has_github, has_slack, has_posthog,
                    has_meta_learning, has_external_bridges,
                    effective_notion_database_id
- get_project_path() resolution
- to_harness_config() backward compat
- to_loop_config() backward compat
- get_settings() singleton / clear_settings_cache()
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from forge_harness.config import ForgeSettings, clear_settings_cache, get_settings

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure the lru_cache singleton is cleared before every test."""
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture()
def clean_env(monkeypatch):
    """Remove all FORGE_* env vars so tests start from a blank slate."""
    for key in list(os.environ.keys()):
        if key.startswith("FORGE_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


# ===========================================================================
# 1. Default values
# ===========================================================================


class TestDefaults:
    def test_domain_default_none(self, clean_env):
        s = ForgeSettings()
        assert s.domain is None

    def test_project_default_none(self, clean_env):
        s = ForgeSettings()
        assert s.project is None

    def test_project_dir_default_none(self, clean_env):
        s = ForgeSettings()
        assert s.project_dir is None

    def test_dry_run_default_false(self, clean_env):
        s = ForgeSettings()
        assert s.dry_run is False

    def test_checkpoint_dir_default(self, clean_env):
        s = ForgeSettings()
        assert s.checkpoint_dir == Path(".forge/orchestration_checkpoints")

    def test_session_dir_default(self, clean_env):
        s = ForgeSettings()
        assert s.session_dir == Path(".forge/sessions")

    def test_approval_storage_dir_default(self, clean_env):
        s = ForgeSettings()
        assert s.approval_storage_dir == Path(".forge/approvals")

    def test_learning_store_dir_default(self, clean_env):
        s = ForgeSettings()
        assert s.learning_store_dir == Path(".forge/learning")

    def test_ralph_checkpoint_dir_default(self, clean_env):
        s = ForgeSettings()
        assert s.ralph_checkpoint_dir == Path(".forge/ralph_checkpoints")

    def test_max_iterations_default(self, clean_env):
        s = ForgeSettings()
        assert s.max_iterations == 100

    def test_max_failures_per_feature_default(self, clean_env):
        s = ForgeSettings()
        assert s.max_failures_per_feature == 5

    def test_checkpoint_interval_default(self, clean_env):
        s = ForgeSettings()
        assert s.checkpoint_interval == 10

    def test_timeout_seconds_default(self, clean_env):
        s = ForgeSettings()
        assert s.timeout_seconds == 3600

    def test_approval_timeout_hours_default(self, clean_env):
        s = ForgeSettings()
        assert s.approval_timeout_hours == 24.0

    def test_log_level_default(self, clean_env):
        s = ForgeSettings()
        assert s.log_level == "INFO"

    def test_log_format_default(self, clean_env):
        s = ForgeSettings()
        assert s.log_format == "text"

    def test_meta_learning_enabled_default(self, clean_env):
        s = ForgeSettings()
        assert s.meta_learning_enabled is True

    def test_webhook_require_auth_default(self, clean_env):
        s = ForgeSettings()
        assert s.webhook_require_auth is True

    def test_llm_provider_default(self, clean_env):
        s = ForgeSettings()
        assert s.llm_provider == "claude"

    def test_all_tokens_default_none(self, clean_env):
        s = ForgeSettings()
        assert s.notion_api_token is None
        assert s.github_token is None
        assert s.slack_webhook_url is None
        assert s.posthog_api_key is None
        assert s.railway_token is None
        assert s.cloudflare_api_token is None
        assert s.webhook_token is None


# ===========================================================================
# 2. Environment variable loading
# ===========================================================================


class TestEnvVarLoading:
    def test_domain_from_env(self, clean_env):
        clean_env.setenv("FORGE_DOMAIN", "codeswiftr-com")
        s = ForgeSettings()
        assert s.domain == "codeswiftr-com"

    def test_project_from_env(self, clean_env):
        clean_env.setenv("FORGE_PROJECT", "interview-simulator")
        s = ForgeSettings()
        assert s.project == "interview-simulator"

    def test_dry_run_true_from_env(self, clean_env):
        clean_env.setenv("FORGE_DRY_RUN", "true")
        s = ForgeSettings()
        assert s.dry_run is True

    def test_dry_run_false_from_env(self, clean_env):
        clean_env.setenv("FORGE_DRY_RUN", "false")
        s = ForgeSettings()
        assert s.dry_run is False

    def test_max_iterations_from_env(self, clean_env):
        clean_env.setenv("FORGE_MAX_ITERATIONS", "50")
        s = ForgeSettings()
        assert s.max_iterations == 50

    def test_checkpoint_interval_from_env(self, clean_env):
        clean_env.setenv("FORGE_CHECKPOINT_INTERVAL", "5")
        s = ForgeSettings()
        assert s.checkpoint_interval == 5

    def test_timeout_seconds_from_env(self, clean_env):
        clean_env.setenv("FORGE_TIMEOUT_SECONDS", "1800")
        s = ForgeSettings()
        assert s.timeout_seconds == 1800

    def test_log_level_from_env(self, clean_env):
        clean_env.setenv("FORGE_LOG_LEVEL", "DEBUG")
        s = ForgeSettings()
        assert s.log_level == "DEBUG"

    def test_log_format_json_from_env(self, clean_env):
        clean_env.setenv("FORGE_LOG_FORMAT", "json")
        s = ForgeSettings()
        assert s.log_format == "json"

    def test_github_token_from_env(self, clean_env):
        clean_env.setenv("FORGE_GITHUB_TOKEN", "ghp_secret")
        s = ForgeSettings()
        assert s.github_token == "ghp_secret"

    def test_github_repo_from_env(self, clean_env):
        clean_env.setenv("FORGE_GITHUB_REPO", "owner/repo")
        s = ForgeSettings()
        assert s.github_repo == "owner/repo"

    def test_notion_api_token_from_env(self, clean_env):
        clean_env.setenv("FORGE_NOTION_API_TOKEN", "ntn_xxx")
        s = ForgeSettings()
        assert s.notion_api_token == "ntn_xxx"

    def test_notion_database_id_from_env(self, clean_env):
        clean_env.setenv("FORGE_NOTION_DATABASE_ID", "db-abc")
        s = ForgeSettings()
        assert s.notion_database_id == "db-abc"

    def test_notion_data_source_id_from_env(self, clean_env):
        clean_env.setenv("FORGE_NOTION_DATA_SOURCE_ID", "ds-xyz")
        s = ForgeSettings()
        assert s.notion_data_source_id == "ds-xyz"

    def test_slack_webhook_url_from_env(self, clean_env):
        clean_env.setenv("FORGE_SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        s = ForgeSettings()
        assert s.slack_webhook_url == "https://hooks.slack.com/test"

    def test_meta_learning_disabled_from_env(self, clean_env):
        clean_env.setenv("FORGE_META_LEARNING_ENABLED", "false")
        s = ForgeSettings()
        assert s.meta_learning_enabled is False

    def test_code_atlas_url_from_env(self, clean_env):
        clean_env.setenv("FORGE_CODE_ATLAS_URL", "http://localhost:7070")
        s = ForgeSettings()
        assert s.code_atlas_url == "http://localhost:7070"

    def test_tech_diligence_url_from_env(self, clean_env):
        clean_env.setenv("FORGE_TECH_DILIGENCE_URL", "http://localhost:7071")
        s = ForgeSettings()
        assert s.tech_diligence_url == "http://localhost:7071"

    def test_checkpoint_dir_from_env(self, clean_env, tmp_path):
        clean_env.setenv("FORGE_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
        s = ForgeSettings()
        assert s.checkpoint_dir == tmp_path / "checkpoints"

    def test_unknown_env_vars_ignored(self, clean_env):
        """extra='ignore' means unknown env vars don't raise."""
        clean_env.setenv("FORGE_TOTALLY_UNKNOWN_SETTING", "value")
        s = ForgeSettings()  # Must not raise
        assert s is not None


# ===========================================================================
# 3. Validators
# ===========================================================================


class TestValidators:
    # log_level
    def test_log_level_debug_valid(self, clean_env):
        s = ForgeSettings(log_level="debug")
        assert s.log_level == "DEBUG"

    def test_log_level_warning_valid(self, clean_env):
        s = ForgeSettings(log_level="WARNING")
        assert s.log_level == "WARNING"

    def test_log_level_error_valid(self, clean_env):
        s = ForgeSettings(log_level="error")
        assert s.log_level == "ERROR"

    def test_log_level_critical_valid(self, clean_env):
        s = ForgeSettings(log_level="CRITICAL")
        assert s.log_level == "CRITICAL"

    def test_log_level_case_insensitive(self, clean_env):
        s = ForgeSettings(log_level="iNfO")
        assert s.log_level == "INFO"

    def test_log_level_invalid_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(log_level="VERBOSE")

    def test_log_level_empty_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(log_level="")

    # log_format
    def test_log_format_text_valid(self, clean_env):
        s = ForgeSettings(log_format="text")
        assert s.log_format == "text"

    def test_log_format_json_valid(self, clean_env):
        s = ForgeSettings(log_format="json")
        assert s.log_format == "json"

    def test_log_format_case_insensitive(self, clean_env):
        s = ForgeSettings(log_format="JSON")
        assert s.log_format == "json"

    def test_log_format_invalid_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(log_format="xml")

    # positive int validators
    def test_max_iterations_positive(self, clean_env):
        s = ForgeSettings(max_iterations=1)
        assert s.max_iterations == 1

    def test_max_iterations_zero_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(max_iterations=0)

    def test_max_iterations_negative_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(max_iterations=-5)

    def test_max_failures_per_feature_zero_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(max_failures_per_feature=0)

    def test_checkpoint_interval_positive(self, clean_env):
        s = ForgeSettings(checkpoint_interval=1)
        assert s.checkpoint_interval == 1

    def test_checkpoint_interval_zero_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(checkpoint_interval=0)

    # timeout validator
    def test_timeout_minimum_boundary(self, clean_env):
        s = ForgeSettings(timeout_seconds=30)
        assert s.timeout_seconds == 30

    def test_timeout_below_minimum_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(timeout_seconds=29)

    def test_timeout_maximum_boundary(self, clean_env):
        s = ForgeSettings(timeout_seconds=86400)
        assert s.timeout_seconds == 86400

    def test_timeout_above_maximum_raises(self, clean_env):
        with pytest.raises(ValidationError):
            ForgeSettings(timeout_seconds=86401)

    def test_timeout_typical_value(self, clean_env):
        s = ForgeSettings(timeout_seconds=1800)
        assert s.timeout_seconds == 1800


# ===========================================================================
# 4. from_env factory method
# ===========================================================================


class TestFromEnvFactory:
    def test_from_env_no_overrides(self, clean_env):
        s = ForgeSettings.from_env()
        assert s.domain is None
        assert s.project is None

    def test_from_env_domain_override(self, clean_env):
        s = ForgeSettings.from_env(domain="brandfocus-ai")
        assert s.domain == "brandfocus-ai"

    def test_from_env_project_override(self, clean_env):
        s = ForgeSettings.from_env(project="voice-coach")
        assert s.project == "voice-coach"

    def test_from_env_both_overrides(self, clean_env):
        s = ForgeSettings.from_env(domain="brandfocus-ai", project="voice-coach")
        assert s.domain == "brandfocus-ai"
        assert s.project == "voice-coach"

    def test_from_env_kwargs_override(self, clean_env):
        s = ForgeSettings.from_env(dry_run=True, max_iterations=20)
        assert s.dry_run is True
        assert s.max_iterations == 20

    def test_from_env_env_var_plus_override(self, clean_env):
        clean_env.setenv("FORGE_DOMAIN", "env-domain")
        # Explicit override wins over env var
        s = ForgeSettings.from_env(domain="explicit-domain")
        assert s.domain == "explicit-domain"

    def test_from_env_reads_env_when_no_override(self, clean_env):
        clean_env.setenv("FORGE_DOMAIN", "from-env")
        s = ForgeSettings.from_env()
        assert s.domain == "from-env"


# ===========================================================================
# 5. Properties: integration presence checks
# ===========================================================================


class TestProperties:
    def test_has_notion_false_when_no_token(self, clean_env):
        s = ForgeSettings(notion_database_id="db-123")
        assert s.has_notion is False

    def test_has_notion_false_when_no_db_id(self, clean_env):
        s = ForgeSettings(notion_api_token="ntn_xxx")
        assert s.has_notion is False

    def test_has_notion_true_with_token_and_database_id(self, clean_env):
        s = ForgeSettings(notion_api_token="ntn_xxx", notion_database_id="db-123")
        assert s.has_notion is True

    def test_has_notion_true_with_token_and_data_source_id(self, clean_env):
        s = ForgeSettings(notion_api_token="ntn_xxx", notion_data_source_id="ds-abc")
        assert s.has_notion is True

    def test_has_github_false_by_default(self, clean_env):
        s = ForgeSettings()
        assert s.has_github is False

    def test_has_github_true_with_token(self, clean_env):
        s = ForgeSettings(github_token="ghp_test")
        assert s.has_github is True

    def test_has_slack_false_by_default(self, clean_env):
        s = ForgeSettings()
        assert s.has_slack is False

    def test_has_slack_true_with_url(self, clean_env):
        s = ForgeSettings(slack_webhook_url="https://hooks.slack.com/abc")
        assert s.has_slack is True

    def test_has_posthog_false_by_default(self, clean_env):
        s = ForgeSettings()
        assert s.has_posthog is False

    def test_has_posthog_true_with_key(self, clean_env):
        s = ForgeSettings(posthog_api_key="phc_secret")
        assert s.has_posthog is True

    def test_has_meta_learning_true_by_default(self, clean_env):
        s = ForgeSettings()
        assert s.has_meta_learning is True

    def test_has_meta_learning_false_when_disabled(self, clean_env):
        s = ForgeSettings(meta_learning_enabled=False)
        assert s.has_meta_learning is False

    def test_has_external_bridges_false_by_default(self, clean_env):
        s = ForgeSettings()
        assert s.has_external_bridges is False

    def test_has_external_bridges_true_with_code_atlas(self, clean_env):
        s = ForgeSettings(code_atlas_url="http://localhost:7070")
        assert s.has_external_bridges is True

    def test_has_external_bridges_true_with_tech_diligence(self, clean_env):
        s = ForgeSettings(tech_diligence_url="http://localhost:7071")
        assert s.has_external_bridges is True

    def test_effective_notion_database_id_uses_data_source_when_both_set(self, clean_env):
        s = ForgeSettings(notion_data_source_id="ds-primary", notion_database_id="db-fallback")
        assert s.effective_notion_database_id == "ds-primary"

    def test_effective_notion_database_id_falls_back_to_database_id(self, clean_env):
        s = ForgeSettings(notion_database_id="db-only")
        assert s.effective_notion_database_id == "db-only"

    def test_effective_notion_database_id_none_when_neither_set(self, clean_env):
        s = ForgeSettings()
        assert s.effective_notion_database_id is None


# ===========================================================================
# 6. get_project_path() resolution
# ===========================================================================


class TestGetProjectPath:
    def test_returns_none_when_nothing_configured(self, clean_env):
        s = ForgeSettings()
        assert s.get_project_path() is None

    def test_returns_project_dir_when_set(self, clean_env, tmp_path):
        project_path = tmp_path / "my-project"
        s = ForgeSettings(project_dir=project_path)
        assert s.get_project_path() == project_path

    def test_project_dir_takes_precedence_over_forge_root(self, clean_env, tmp_path):
        project_path = tmp_path / "explicit-dir"
        forge_root = tmp_path / "forge"
        s = ForgeSettings(project_dir=project_path, domain="dom", project="proj")
        result = s.get_project_path(forge_root=forge_root)
        assert result == project_path

    def test_constructs_path_from_forge_root_domain_project(self, clean_env, tmp_path):
        s = ForgeSettings(domain="codeswiftr-com", project="interview-simulator")
        result = s.get_project_path(forge_root=tmp_path)
        assert result == tmp_path / "codeswiftr-com" / "interview-simulator"

    def test_returns_none_when_forge_root_provided_but_domain_missing(self, clean_env, tmp_path):
        s = ForgeSettings(project="interview-simulator")
        result = s.get_project_path(forge_root=tmp_path)
        assert result is None

    def test_returns_none_when_forge_root_provided_but_project_missing(self, clean_env, tmp_path):
        s = ForgeSettings(domain="codeswiftr-com")
        result = s.get_project_path(forge_root=tmp_path)
        assert result is None

    def test_returns_none_without_forge_root_and_no_project_dir(self, clean_env):
        s = ForgeSettings(domain="codeswiftr-com", project="interview-simulator")
        assert s.get_project_path() is None


# ===========================================================================
# 7. Backward compatibility: to_harness_config()
# ===========================================================================


class TestToHarnessConfig:
    def test_to_harness_config_returns_harness_config(self, clean_env):
        from forge_harness.harness_registry import HarnessConfig

        s = ForgeSettings()
        hc = s.to_harness_config()
        assert isinstance(hc, HarnessConfig)

    def test_to_harness_config_domain_propagated(self, clean_env):
        s = ForgeSettings(domain="codeswiftr-com")
        hc = s.to_harness_config()
        assert hc.domain == "codeswiftr-com"

    def test_to_harness_config_project_propagated(self, clean_env):
        s = ForgeSettings(project="voice-coach")
        hc = s.to_harness_config()
        assert hc.project == "voice-coach"

    def test_to_harness_config_dry_run_propagated(self, clean_env):
        s = ForgeSettings(dry_run=True)
        hc = s.to_harness_config()
        assert hc.dry_run is True

    def test_to_harness_config_tokens_propagated(self, clean_env):
        s = ForgeSettings(
            github_token="ghp_test",
            slack_webhook_url="https://hooks.slack.com/x",
            posthog_api_key="phc_key",
        )
        hc = s.to_harness_config()
        assert hc.github_token == "ghp_test"
        assert hc.slack_webhook_url == "https://hooks.slack.com/x"
        assert hc.posthog_api_key == "phc_key"

    def test_to_harness_config_data_source_id_takes_precedence(self, clean_env):
        s = ForgeSettings(
            notion_data_source_id="ds-primary",
            notion_database_id="db-fallback",
        )
        hc = s.to_harness_config()
        assert hc.notion_database_id == "ds-primary"

    def test_to_harness_config_meta_learning_propagated(self, clean_env):
        s = ForgeSettings(meta_learning_enabled=False)
        hc = s.to_harness_config()
        assert hc.meta_learning_enabled is False


# ===========================================================================
# 8. Backward compatibility: to_loop_config()
# ===========================================================================


class TestToLoopConfig:
    def test_to_loop_config_returns_loop_config(self, clean_env):
        from forge_harness.ralph_loop import LoopConfig

        s = ForgeSettings()
        lc = s.to_loop_config()
        assert isinstance(lc, LoopConfig)

    def test_to_loop_config_domain_propagated(self, clean_env):
        s = ForgeSettings(domain="brandfocus-ai")
        lc = s.to_loop_config()
        assert lc.domain == "brandfocus-ai"

    def test_to_loop_config_max_iterations_propagated(self, clean_env):
        s = ForgeSettings(max_iterations=42)
        lc = s.to_loop_config()
        assert lc.max_iterations == 42

    def test_to_loop_config_timeout_propagated(self, clean_env):
        s = ForgeSettings(timeout_seconds=900)
        lc = s.to_loop_config()
        assert lc.timeout_seconds == 900

    def test_to_loop_config_test_command_propagated(self, clean_env):
        s = ForgeSettings(test_command="pytest -q")
        lc = s.to_loop_config()
        assert lc.test_command == "pytest -q"

    def test_to_loop_config_lint_command_propagated(self, clean_env):
        s = ForgeSettings(lint_command="ruff check .")
        lc = s.to_loop_config()
        assert lc.lint_command == "ruff check ."

    def test_to_loop_config_dry_run_propagated(self, clean_env):
        s = ForgeSettings(dry_run=True)
        lc = s.to_loop_config()
        assert lc.dry_run is True

    def test_to_loop_config_approval_timeout_propagated(self, clean_env):
        s = ForgeSettings(approval_timeout_hours=48.0)
        lc = s.to_loop_config()
        assert lc.approval_timeout_hours == 48.0


# ===========================================================================
# 9. Singleton: get_settings() and clear_settings_cache()
# ===========================================================================


class TestSingleton:
    def test_get_settings_returns_forge_settings(self, clean_env):
        s = get_settings()
        assert isinstance(s, ForgeSettings)

    def test_get_settings_cached(self, clean_env):
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_clear_cache_allows_new_instance(self, clean_env):
        s1 = get_settings()
        clear_settings_cache()
        s2 = get_settings()
        # After clearing, a fresh instance is created.  They may be equal
        # but must not be the same object.
        assert s1 is not s2

    def test_get_settings_reflects_env_after_cache_clear(self, clean_env):
        s1 = get_settings()
        assert s1.domain is None

        clear_settings_cache()
        clean_env.setenv("FORGE_DOMAIN", "new-domain")
        s2 = get_settings()
        assert s2.domain == "new-domain"

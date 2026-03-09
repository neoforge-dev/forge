"""
Tests for HarnessRegistry - Dependency Injection Layer
=======================================================

Covers:
- HarnessConfig dataclass (defaults, env loading, from_settings)
- HarnessRegistry core operations (get, register, has, list, is_initialized)
- Lazy loading and caching behavior
- Circular dependency detection
- Custom factory registration
- All built-in harness factory methods
- Mock harness classes (MockDecisionEngine, MockContentHarness, MockPosthogTracker)
- create_harness_registry factory function
- Meta-learning registry integration
- get_all_harnesses / get_for_orchestration
- Error paths and edge cases
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.harness_registry import (
    HarnessConfig,
    HarnessRegistry,
    MockContentHarness,
    MockDecisionEngine,
    MockPosthogTracker,
    create_harness_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KNOWN_BUILTIN_HARNESSES = [
    "content",
    "notification",
    "session",
    "preflight",
    "deployment",
    "workflow",
    "approval_queue",
    "human_gate",
    "posthog",
    "learning_store",
    "code_atlas",
    "tech_diligence",
    "decision_engine",
    "feedback_loop_manager",
    "failure_pattern_db",
    "simple_history",
]


def make_config(**kwargs) -> HarnessConfig:
    """Helper to build a minimal HarnessConfig."""
    defaults = dict(domain="test-domain", project="test-project")
    defaults.update(kwargs)
    return HarnessConfig(**defaults)


def make_registry(**kwargs) -> HarnessRegistry:
    """Helper to build a HarnessRegistry with a minimal config."""
    return HarnessRegistry(make_config(**kwargs))


# ===========================================================================
# HarnessConfig Tests
# ===========================================================================


class TestHarnessConfigDefaults:
    """Tests for HarnessConfig default values."""

    def test_default_domain_is_none(self):
        config = HarnessConfig()
        assert config.domain is None

    def test_default_project_is_none(self):
        config = HarnessConfig()
        assert config.project is None

    def test_default_dry_run_is_false(self):
        config = HarnessConfig()
        assert config.dry_run is False

    def test_default_checkpoint_dir(self):
        config = HarnessConfig()
        assert config.checkpoint_dir == Path(".forge/orchestration_checkpoints")

    def test_default_session_dir(self):
        config = HarnessConfig()
        assert config.session_dir == Path(".forge/sessions")

    def test_default_approval_storage_dir(self):
        config = HarnessConfig()
        assert config.approval_storage_dir == Path(".forge/approvals")

    def test_default_learning_store_dir(self):
        config = HarnessConfig()
        assert config.learning_store_dir == Path(".forge/learning")

    def test_default_notion_api_token_is_none(self):
        config = HarnessConfig()
        assert config.notion_api_token is None

    def test_default_notion_database_id_is_none(self):
        config = HarnessConfig()
        assert config.notion_database_id is None

    def test_default_slack_webhook_url_is_none(self):
        config = HarnessConfig()
        assert config.slack_webhook_url is None

    def test_default_github_token_is_none(self):
        config = HarnessConfig()
        assert config.github_token is None

    def test_default_github_repo_is_none(self):
        config = HarnessConfig()
        assert config.github_repo is None

    def test_default_posthog_api_key_is_none(self):
        config = HarnessConfig()
        assert config.posthog_api_key is None

    def test_default_meta_learning_enabled_is_false(self):
        config = HarnessConfig()
        assert config.meta_learning_enabled is False

    def test_default_code_atlas_url_is_none(self):
        config = HarnessConfig()
        assert config.code_atlas_url is None

    def test_default_tech_diligence_url_is_none(self):
        config = HarnessConfig()
        assert config.tech_diligence_url is None


class TestHarnessConfigExplicitValues:
    """Tests for HarnessConfig with explicit values."""

    def test_explicit_domain(self):
        config = HarnessConfig(domain="my-domain")
        assert config.domain == "my-domain"

    def test_explicit_project(self):
        config = HarnessConfig(project="my-project")
        assert config.project == "my-project"

    def test_explicit_dry_run(self):
        config = HarnessConfig(dry_run=True)
        assert config.dry_run is True

    def test_explicit_slack_webhook(self):
        url = "https://hooks.slack.com/test"
        config = HarnessConfig(slack_webhook_url=url)
        assert config.slack_webhook_url == url

    def test_explicit_github_token(self):
        config = HarnessConfig(github_token="ghp_test123")
        assert config.github_token == "ghp_test123"

    def test_explicit_github_repo(self):
        config = HarnessConfig(github_repo="owner/repo")
        assert config.github_repo == "owner/repo"

    def test_explicit_checkpoint_dir(self, tmp_path):
        config = HarnessConfig(checkpoint_dir=tmp_path / "ckpts")
        assert config.checkpoint_dir == tmp_path / "ckpts"

    def test_explicit_meta_learning_enabled(self):
        config = HarnessConfig(meta_learning_enabled=True)
        assert config.meta_learning_enabled is True

    def test_explicit_code_atlas_url(self):
        config = HarnessConfig(code_atlas_url="http://atlas:8001")
        assert config.code_atlas_url == "http://atlas:8001"

    def test_explicit_tech_diligence_url(self):
        config = HarnessConfig(tech_diligence_url="http://diligence:8002")
        assert config.tech_diligence_url == "http://diligence:8002"

    def test_all_optional_tokens(self):
        config = HarnessConfig(
            notion_api_token="ntn_test",
            notion_database_id="db123",
            slack_webhook_url="https://hooks.slack.com/x",
            github_token="ghp_x",
            github_repo="owner/repo",
            posthog_api_key="phc_x",
        )
        assert config.notion_api_token == "ntn_test"
        assert config.notion_database_id == "db123"
        assert config.slack_webhook_url == "https://hooks.slack.com/x"
        assert config.github_token == "ghp_x"
        assert config.github_repo == "owner/repo"
        assert config.posthog_api_key == "phc_x"


class TestHarnessConfigFromEnv:
    """Tests for HarnessConfig.from_env()."""

    def test_from_env_empty_env(self):
        with patch.dict(os.environ, {}, clear=True):
            config = HarnessConfig.from_env()
        assert config.domain is None
        assert config.project is None

    def test_from_env_reads_domain(self):
        with patch.dict(os.environ, {"FORGE_DOMAIN": "env-domain"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.domain == "env-domain"

    def test_from_env_reads_project(self):
        with patch.dict(os.environ, {"FORGE_PROJECT": "env-project"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.project == "env-project"

    def test_from_env_reads_dry_run_true(self):
        with patch.dict(os.environ, {"FORGE_DRY_RUN": "true"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.dry_run is True

    def test_from_env_reads_dry_run_false(self):
        with patch.dict(os.environ, {"FORGE_DRY_RUN": "false"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.dry_run is False

    def test_from_env_domain_override_beats_env(self):
        with patch.dict(os.environ, {"FORGE_DOMAIN": "env-domain"}, clear=True):
            config = HarnessConfig.from_env(domain="override-domain")
        assert config.domain == "override-domain"

    def test_from_env_project_override_beats_env(self):
        with patch.dict(os.environ, {"FORGE_PROJECT": "env-project"}, clear=True):
            config = HarnessConfig.from_env(project="override-project")
        assert config.project == "override-project"

    def test_from_env_reads_notion_api_token(self):
        with patch.dict(os.environ, {"FORGE_NOTION_API_TOKEN": "ntn_test"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.notion_api_token == "ntn_test"

    def test_from_env_reads_slack_webhook(self):
        with patch.dict(
            os.environ,
            {"FORGE_SLACK_WEBHOOK_URL": "https://hooks.slack.com/env"},
            clear=True,
        ):
            config = HarnessConfig.from_env()
        assert config.slack_webhook_url == "https://hooks.slack.com/env"

    def test_from_env_reads_github_token(self):
        with patch.dict(os.environ, {"FORGE_GITHUB_TOKEN": "ghp_test"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.github_token == "ghp_test"

    def test_from_env_reads_github_repo(self):
        with patch.dict(os.environ, {"FORGE_GITHUB_REPO": "owner/repo"}, clear=True):
            config = HarnessConfig.from_env()
        assert config.github_repo == "owner/repo"

    def test_from_env_dry_run_true_variations(self):
        for value in ["true", "True", "TRUE", "1", "yes"]:
            with patch.dict(os.environ, {"FORGE_DRY_RUN": value}, clear=True):
                config = HarnessConfig.from_env()
                assert config.dry_run is True, f"Failed for value={value!r}"

    def test_from_env_dry_run_false_variations(self):
        for value in ["false", "False", "0", "no"]:
            with patch.dict(os.environ, {"FORGE_DRY_RUN": value}, clear=True):
                config = HarnessConfig.from_env()
                assert config.dry_run is False, f"Failed for value={value!r}"

    def test_from_env_meta_learning_url_fields(self):
        env = {
            "FORGE_META_LEARNING_ENABLED": "true",
            "FORGE_CODE_ATLAS_URL": "http://atlas:8001",
            "FORGE_TECH_DILIGENCE_URL": "http://diligence:8002",
        }
        with patch.dict(os.environ, env, clear=True):
            config = HarnessConfig.from_env()
        assert config.meta_learning_enabled is True
        assert config.code_atlas_url == "http://atlas:8001"
        assert config.tech_diligence_url == "http://diligence:8002"


class TestHarnessConfigFromSettings:
    """Tests for HarnessConfig.from_settings()."""

    def test_from_settings_delegates_to_settings(self):
        from forge_harness.config import ForgeSettings

        settings = ForgeSettings(domain="s-domain", project="s-project")
        config = HarnessConfig.from_settings(settings)

        assert config.domain == "s-domain"
        assert config.project == "s-project"

    def test_from_settings_dry_run(self):
        from forge_harness.config import ForgeSettings

        settings = ForgeSettings(dry_run=True)
        config = HarnessConfig.from_settings(settings)

        assert config.dry_run is True


# ===========================================================================
# HarnessRegistry Core Operation Tests
# ===========================================================================


class TestHarnessRegistryCore:
    """Tests for the HarnessRegistry class core behavior."""

    @pytest.fixture
    def config(self):
        return make_config()

    @pytest.fixture
    def registry(self, config):
        return HarnessRegistry(config)

    # --- Registration and discovery ---

    def test_list_available_returns_sorted(self, registry):
        available = registry.list_available()
        assert available == sorted(available)

    def test_list_available_contains_all_builtins(self, registry):
        available = registry.list_available()
        for name in KNOWN_BUILTIN_HARNESSES:
            assert name in available, f"Expected {name!r} in available"

    def test_has_returns_true_for_registered(self, registry):
        assert registry.has("content") is True
        assert registry.has("session") is True
        assert registry.has("notification") is True

    def test_has_returns_false_for_unknown(self, registry):
        assert registry.has("nonexistent") is False
        assert registry.has("notion") is False
        assert registry.has("") is False

    def test_is_initialized_false_before_access(self, registry):
        assert registry.is_initialized("content") is False
        assert registry.is_initialized("session") is False

    def test_is_initialized_true_after_access(self, registry):
        registry.get("session")
        assert registry.is_initialized("session") is True

    def test_is_initialized_other_not_affected(self, registry):
        registry.get("session")
        assert registry.is_initialized("content") is False

    # --- get() behavior ---

    def test_get_unknown_raises_key_error(self, registry):
        with pytest.raises(KeyError) as exc_info:
            registry.get("nonexistent")
        assert "nonexistent" in str(exc_info.value)

    def test_get_key_error_lists_available(self, registry):
        with pytest.raises(KeyError) as exc_info:
            registry.get("nonexistent")
        assert "Available:" in str(exc_info.value)

    def test_get_returns_same_instance_on_repeated_calls(self, registry):
        instance_a = registry.get("session")
        instance_b = registry.get("session")
        assert instance_a is instance_b

    def test_get_caches_instance_for_lazy_access(self, registry):
        assert not registry.is_initialized("session")
        registry.get("session")
        assert registry.is_initialized("session")

    # --- register() behavior ---

    def test_register_adds_new_factory(self, registry):
        sentinel = object()
        registry.register("my_custom", lambda c, r: sentinel)
        assert registry.has("my_custom")

    def test_register_factory_returns_correct_instance(self, registry):
        sentinel = object()
        registry.register("my_custom", lambda c, r: sentinel)
        assert registry.get("my_custom") is sentinel

    def test_register_clears_cached_instance(self, registry):
        """Re-registering a harness clears the old cached instance."""
        first = registry.get("session")
        new_sentinel = object()
        registry.register("session", lambda c, r: new_sentinel)
        second = registry.get("session")
        assert second is new_sentinel
        assert second is not first

    def test_register_accepts_callable(self, registry):
        called_with = {}

        def factory(config, reg):
            called_with["config"] = config
            called_with["reg"] = reg
            return MagicMock()

        registry.register("tracked", factory)
        registry.get("tracked")

        assert called_with["config"] is registry.config
        assert called_with["reg"] is registry

    # --- Circular dependency ---

    def test_circular_dependency_raises_runtime_error(self):
        def factory_a(config, reg):
            return reg.get("b")

        def factory_b(config, reg):
            return reg.get("a")

        reg = HarnessRegistry(HarnessConfig(), {"a": factory_a, "b": factory_b})
        with pytest.raises(RuntimeError) as exc_info:
            reg.get("a")
        assert "Circular dependency" in str(exc_info.value)
        assert "b" in str(exc_info.value) or "a" in str(exc_info.value)

    def test_circular_dependency_cleans_initializing_set(self):
        """After circular detection, the _initializing set must be clean."""
        def factory_a(config, reg):
            return reg.get("b")

        def factory_b(config, reg):
            return reg.get("a")

        reg = HarnessRegistry(HarnessConfig(), {"a": factory_a, "b": factory_b})
        with pytest.raises(RuntimeError):
            reg.get("a")
        # After error, a should NOT still be in _initializing
        assert "a" not in reg._initializing

    # --- Custom factories via constructor ---

    def test_custom_factories_merged_with_defaults(self):
        sentinel = object()
        reg = HarnessRegistry(HarnessConfig(), {"my_harness": lambda c, r: sentinel})
        assert reg.has("my_harness")
        assert reg.has("content")  # built-ins still present

    def test_custom_factory_can_be_overridden_via_register(self):
        """Use register() to override a built-in - this always works."""
        reg = HarnessRegistry(HarnessConfig())
        sentinel = object()
        reg.register("session", lambda c, r: sentinel)
        assert reg.get("session") is sentinel

    def test_custom_factory_for_new_name_via_constructor(self):
        """A new (non-builtin) name via constructor is preserved."""
        sentinel = object()
        reg = HarnessRegistry(HarnessConfig(), {"my_new_harness": lambda c, r: sentinel})
        assert reg.get("my_new_harness") is sentinel

    # --- Factory error propagation ---

    def test_factory_exception_propagates_from_get(self, registry):
        registry.register("broken", lambda c, r: (_ for _ in ()).throw(ValueError("boom")))
        with pytest.raises(Exception):
            registry.get("broken")

    def test_factory_exception_leaves_harness_uninitialized(self, registry):
        registry.register("broken", lambda c, r: (_ for _ in ()).throw(ValueError("boom")))
        try:
            registry.get("broken")
        except Exception:
            pass
        assert not registry.is_initialized("broken")


# ===========================================================================
# Built-in Harness Factory Tests
# ===========================================================================


class TestBuiltinHarnessFactories:
    """Tests for each built-in harness factory method."""

    @pytest.fixture
    def tmp_registry(self, tmp_path):
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=tmp_path / "checkpoints",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
            learning_store_dir=tmp_path / "learning",
        )
        return HarnessRegistry(config)

    def test_session_manager_created(self, tmp_registry):
        session = tmp_registry.get("session")
        assert session is not None
        assert hasattr(session, "create_session")
        assert hasattr(session, "save_state")

    def test_notification_harness_created(self, tmp_registry):
        notification = tmp_registry.get("notification")
        assert notification is not None
        assert hasattr(notification, "notify")

    def test_preflight_checker_created(self, tmp_registry):
        preflight = tmp_registry.get("preflight")
        assert preflight is not None

    def test_posthog_tracker_is_mock(self, tmp_registry):
        posthog = tmp_registry.get("posthog")
        assert isinstance(posthog, MockPosthogTracker)

    def test_content_harness_mock_without_domain(self, tmp_path):
        config = HarnessConfig(
            domain=None,
            project=None,
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        content = reg.get("content")
        assert isinstance(content, MockContentHarness)

    def test_content_harness_mock_without_project(self, tmp_path):
        config = HarnessConfig(
            domain="my-domain",
            project=None,
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        content = reg.get("content")
        assert isinstance(content, MockContentHarness)

    def test_workflow_harness_created(self, tmp_registry):
        workflow = tmp_registry.get("workflow")
        assert workflow is not None

    def test_approval_queue_created(self, tmp_registry):
        from forge_harness.approval_queue import ApprovalQueueHarness

        aq = tmp_registry.get("approval_queue")
        assert isinstance(aq, ApprovalQueueHarness)

    def test_human_gate_harness_wired(self, tmp_registry):
        from forge_harness.human_gate_harness import HumanGateHarness

        hg = tmp_registry.get("human_gate")
        assert isinstance(hg, HumanGateHarness)
        assert hg.approval_queue is not None
        assert hg.notification_harness is not None

    def test_human_gate_shares_approval_queue_instance(self, tmp_registry):
        """human_gate and approval_queue should share the same instance."""
        aq = tmp_registry.get("approval_queue")
        hg = tmp_registry.get("human_gate")
        assert hg.approval_queue is aq

    def test_human_gate_shares_notification_instance(self, tmp_registry):
        """human_gate and notification should share the same instance."""
        notification = tmp_registry.get("notification")
        hg = tmp_registry.get("human_gate")
        assert hg.notification_harness is notification

    def test_learning_store_created(self, tmp_registry):
        from forge_harness.meta_learning import LearningStore

        store = tmp_registry.get("learning_store")
        assert isinstance(store, LearningStore)

    def test_code_atlas_none_when_no_url(self, tmp_registry):
        atlas = tmp_registry.get("code_atlas")
        assert atlas is None

    def test_tech_diligence_none_when_no_url(self, tmp_registry):
        diligence = tmp_registry.get("tech_diligence")
        assert diligence is None

    def test_code_atlas_created_with_url(self, tmp_path):
        from forge_harness.meta_learning import CodeAtlasBridge

        config = HarnessConfig(
            domain="test",
            project="test",
            code_atlas_url="http://atlas:8001",
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        atlas = reg.get("code_atlas")
        assert isinstance(atlas, CodeAtlasBridge)

    def test_tech_diligence_created_with_url(self, tmp_path):
        from forge_harness.meta_learning import TechDiligenceBridge

        config = HarnessConfig(
            domain="test",
            project="test",
            tech_diligence_url="http://diligence:8002",
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        diligence = reg.get("tech_diligence")
        assert isinstance(diligence, TechDiligenceBridge)

    def test_decision_engine_mock_when_disabled(self, tmp_registry):
        engine = tmp_registry.get("decision_engine")
        assert isinstance(engine, MockDecisionEngine)

    def test_decision_engine_real_when_enabled(self, tmp_path):
        from forge_harness.meta_learning import DecisionEngine

        config = HarnessConfig(
            domain="test",
            project="test",
            meta_learning_enabled=True,
            learning_store_dir=tmp_path / "learning",
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        engine = reg.get("decision_engine")
        assert isinstance(engine, DecisionEngine)

    def test_decision_engine_wired_with_learning_store(self, tmp_path):
        config = HarnessConfig(
            domain="test",
            project="test",
            meta_learning_enabled=True,
            learning_store_dir=tmp_path / "learning",
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        engine = reg.get("decision_engine")
        store = reg.get("learning_store")
        assert engine.learning_store is store

    def test_simple_history_created(self, tmp_registry):
        from forge_harness.simple_history import SimpleHistory

        sh = tmp_registry.get("simple_history")
        assert isinstance(sh, SimpleHistory)

    def test_failure_pattern_db_created(self, tmp_registry):
        from forge_harness.failure_patterns import EnhancedFailurePatternDB

        db = tmp_registry.get("failure_pattern_db")
        assert isinstance(db, EnhancedFailurePatternDB)

    def test_feedback_loop_manager_created(self, tmp_registry):
        from forge_harness.meta_learning import FeedbackLoopManager

        manager = tmp_registry.get("feedback_loop_manager")
        assert isinstance(manager, FeedbackLoopManager)

    def test_deployment_harness_dry_run_false(self, tmp_path):
        config = HarnessConfig(
            domain="test",
            project="test",
            dry_run=False,
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        deployment = reg.get("deployment")
        assert deployment is not None

    def test_deployment_harness_dry_run_true(self, tmp_path):
        config = HarnessConfig(
            domain="test",
            project="test",
            dry_run=True,
            session_dir=tmp_path / "sessions",
        )
        reg = HarnessRegistry(config)
        deployment = reg.get("deployment")
        assert deployment is not None


# ===========================================================================
# get_all_harnesses / get_for_orchestration Tests
# ===========================================================================


class TestBatchHarnessRetrieval:
    """Tests for get_all_harnesses() and get_for_orchestration()."""

    @pytest.fixture
    def registry(self, tmp_path):
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=tmp_path / "checkpoints",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
            learning_store_dir=tmp_path / "learning",
        )
        return HarnessRegistry(config)

    def test_get_all_harnesses_returns_dict(self, registry):
        harnesses = registry.get_all_harnesses()
        assert isinstance(harnesses, dict)

    def test_get_all_harnesses_contains_content(self, registry):
        harnesses = registry.get_all_harnesses()
        assert "content" in harnesses

    def test_get_all_harnesses_contains_session(self, registry):
        harnesses = registry.get_all_harnesses()
        assert "session" in harnesses

    def test_get_all_harnesses_contains_notification(self, registry):
        harnesses = registry.get_all_harnesses()
        assert "notification" in harnesses

    def test_get_all_harnesses_count_is_reasonable(self, registry):
        harnesses = registry.get_all_harnesses()
        assert len(harnesses) >= len(KNOWN_BUILTIN_HARNESSES) - 2  # some may fail gracefully

    def test_get_all_harnesses_tolerates_factory_failures(self, registry):
        """Factories that throw should be skipped, not crash get_all_harnesses."""
        registry.register("bad_harness", lambda c, r: (_ for _ in ()).throw(RuntimeError("fail")))
        harnesses = registry.get_all_harnesses()
        # bad_harness should be absent but others should be present
        assert "bad_harness" not in harnesses
        assert "session" in harnesses

    def test_get_for_orchestration_returns_dict(self, registry):
        harnesses = registry.get_for_orchestration()
        assert isinstance(harnesses, dict)

    def test_get_for_orchestration_includes_session(self, registry):
        harnesses = registry.get_for_orchestration()
        assert "session" in harnesses

    def test_get_for_orchestration_includes_notification(self, registry):
        harnesses = registry.get_for_orchestration()
        assert "notification" in harnesses

    def test_get_for_orchestration_includes_human_gate(self, registry):
        harnesses = registry.get_for_orchestration()
        assert "human_gate" in harnesses

    def test_get_for_orchestration_skips_missing(self, registry):
        """Harnesses that are not registered (e.g. notion) are silently skipped."""
        harnesses = registry.get_for_orchestration()
        # "notion" and "analytics" are not registered in current registry
        # The method should not raise; missing ones are just absent
        assert isinstance(harnesses, dict)


# ===========================================================================
# Mock Harness Class Tests
# ===========================================================================


class TestMockDecisionEngine:
    """Tests for MockDecisionEngine."""

    @pytest.mark.asyncio
    async def test_get_recommendation_returns_proceed(self):
        from forge_harness.meta_learning.schemas import DecisionAction, DecisionContext

        engine = MockDecisionEngine()
        ctx = DecisionContext(domain="d", project="p", file_paths=[], tags=[])
        rec = await engine.get_recommendation(ctx)
        assert rec.action == DecisionAction.PROCEED

    @pytest.mark.asyncio
    async def test_get_recommendation_reasoning_mentions_disabled(self):
        from forge_harness.meta_learning.schemas import DecisionContext

        engine = MockDecisionEngine()
        ctx = DecisionContext(domain="d", project="p", file_paths=[], tags=[])
        rec = await engine.get_recommendation(ctx)
        assert "disabled" in rec.reasoning.lower() or "default" in rec.reasoning.lower()

    @pytest.mark.asyncio
    async def test_record_outcome_is_noop(self):
        engine = MockDecisionEngine()
        await engine.record_outcome("decision-123", success=True)
        # Should not raise

    def test_get_statistics_returns_dict(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        assert isinstance(stats, dict)

    def test_get_statistics_meta_learning_disabled(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        assert stats["meta_learning_enabled"] is False

    def test_get_statistics_bridges_false(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        assert stats["has_atlas_bridge"] is False
        assert stats["has_diligence_bridge"] is False

    def test_get_statistics_cached_decisions_zero(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        assert stats["cached_decisions"] == 0


class TestMockContentHarness:
    """Tests for MockContentHarness."""

    @pytest.mark.asyncio
    async def test_generate_content_library_returns_dict(self):
        harness = MockContentHarness()
        result = await harness.generate_content_library()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_generate_content_library_returns_items_list(self):
        harness = MockContentHarness()
        result = await harness.generate_content_library()
        assert "items" in result
        assert isinstance(result["items"], list)

    @pytest.mark.asyncio
    async def test_generate_content_library_returns_empty(self):
        harness = MockContentHarness()
        result = await harness.generate_content_library()
        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_generate_brief_returns_dict(self):
        harness = MockContentHarness()
        result = await harness.generate_brief()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_generate_brief_accepts_kwargs(self):
        harness = MockContentHarness()
        result = await harness.generate_brief(topic="test", audience="devs")
        assert isinstance(result, dict)


class TestMockPosthogTracker:
    """Tests for MockPosthogTracker."""

    @pytest.mark.asyncio
    async def test_track_does_not_raise(self):
        tracker = MockPosthogTracker()
        await tracker.track("test_event", {"key": "value"})

    @pytest.mark.asyncio
    async def test_track_with_none_properties(self):
        tracker = MockPosthogTracker()
        await tracker.track("event", None)

    @pytest.mark.asyncio
    async def test_track_batch_does_not_raise(self):
        tracker = MockPosthogTracker()
        await tracker.track_batch(["event1", "event2"], count=2)

    @pytest.mark.asyncio
    async def test_track_batch_empty_list(self):
        tracker = MockPosthogTracker()
        await tracker.track_batch([], count=0)


# ===========================================================================
# create_harness_registry Factory Function Tests
# ===========================================================================


class TestCreateHarnessRegistry:
    """Tests for the create_harness_registry factory function."""

    def test_returns_registry_instance(self):
        registry = create_harness_registry(domain="d", project="p")
        assert isinstance(registry, HarnessRegistry)

    def test_domain_passed_through(self):
        registry = create_harness_registry(domain="my-domain", project="my-project")
        assert registry.config.domain == "my-domain"

    def test_project_passed_through(self):
        registry = create_harness_registry(domain="d", project="my-project")
        assert registry.config.project == "my-project"

    def test_pre_built_config_used_directly(self):
        config = HarnessConfig(domain="config-domain", project="config-project")
        registry = create_harness_registry(config=config)
        assert registry.config is config

    def test_pre_built_config_ignores_domain_project_args(self):
        config = HarnessConfig(domain="config-domain")
        registry = create_harness_registry(domain="arg-domain", config=config)
        # When config is passed, domain/project args are ignored
        assert registry.config is config

    def test_kwargs_applied_to_config(self, tmp_path):
        registry = create_harness_registry(
            domain="d",
            project="p",
            dry_run=True,
            checkpoint_dir=tmp_path / "ckpts",
        )
        assert registry.config.dry_run is True
        assert registry.config.checkpoint_dir == tmp_path / "ckpts"

    def test_unknown_kwargs_ignored(self):
        # Should not raise for kwargs that do not map to HarnessConfig attrs
        registry = create_harness_registry(
            domain="d",
            project="p",
            totally_unknown_key="value",
        )
        assert isinstance(registry, HarnessRegistry)

    def test_from_env_fallback(self):
        with patch.dict(os.environ, {"FORGE_DOMAIN": "env-domain"}, clear=True):
            registry = create_harness_registry()
        assert registry.config.domain == "env-domain"

    def test_no_args_creates_valid_registry(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = create_harness_registry()
        assert isinstance(registry, HarnessRegistry)


# ===========================================================================
# OrchestrationHarness Integration Tests
# ===========================================================================


class TestOrchestrationIntegration:
    """Tests for create_orchestration_harness_from_registry."""

    def test_factory_function_exists(self):
        from forge_harness.orchestration_harness import (
            create_orchestration_harness_from_registry,
        )

        assert callable(create_orchestration_harness_from_registry)

    def test_creates_orchestration_harness(self, tmp_path):
        from forge_harness.orchestration_harness import (
            OrchestrationHarness,
            create_orchestration_harness_from_registry,
        )

        orchestrator = create_orchestration_harness_from_registry(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=tmp_path / "checkpoints",
        )
        assert isinstance(orchestrator, OrchestrationHarness)

    def test_orchestrator_has_session(self, tmp_path):
        from forge_harness.orchestration_harness import (
            create_orchestration_harness_from_registry,
        )

        orchestrator = create_orchestration_harness_from_registry(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=tmp_path / "checkpoints",
        )
        assert "session" in orchestrator.harnesses

    def test_orchestrator_has_notification(self, tmp_path):
        from forge_harness.orchestration_harness import (
            create_orchestration_harness_from_registry,
        )

        orchestrator = create_orchestration_harness_from_registry(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=tmp_path / "checkpoints",
        )
        assert "notification" in orchestrator.harnesses

    def test_orchestrator_has_human_gate(self, tmp_path):
        from forge_harness.orchestration_harness import (
            create_orchestration_harness_from_registry,
        )

        orchestrator = create_orchestration_harness_from_registry(
            domain="test-domain",
            project="test-project",
            checkpoint_dir=tmp_path / "checkpoints",
        )
        assert "human_gate" in orchestrator.harnesses


# ===========================================================================
# Meta-Learning Registry Tests
# ===========================================================================


class TestMetaLearningRegistry:
    """Tests for Meta-Learning harness registration and wiring."""

    @pytest.fixture
    def enabled_config(self, tmp_path):
        return HarnessConfig(
            domain="test",
            project="test",
            meta_learning_enabled=True,
            learning_store_dir=tmp_path / "learning",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )

    @pytest.fixture
    def disabled_config(self, tmp_path):
        return HarnessConfig(
            domain="test",
            project="test",
            meta_learning_enabled=False,
            learning_store_dir=tmp_path / "learning",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )

    def test_all_meta_learning_harnesses_registered(self, enabled_config):
        reg = HarnessRegistry(enabled_config)
        for name in ["learning_store", "code_atlas", "tech_diligence", "decision_engine"]:
            assert reg.has(name), f"{name!r} should be registered"

    def test_learning_store_is_correct_type(self, enabled_config):
        from forge_harness.meta_learning import LearningStore

        reg = HarnessRegistry(enabled_config)
        assert isinstance(reg.get("learning_store"), LearningStore)

    def test_decision_engine_disabled_returns_mock(self, disabled_config):
        reg = HarnessRegistry(disabled_config)
        assert isinstance(reg.get("decision_engine"), MockDecisionEngine)

    def test_decision_engine_enabled_returns_real(self, enabled_config):
        from forge_harness.meta_learning import DecisionEngine

        reg = HarnessRegistry(enabled_config)
        assert isinstance(reg.get("decision_engine"), DecisionEngine)

    def test_feedback_loop_manager_registered(self, enabled_config):
        reg = HarnessRegistry(enabled_config)
        assert reg.has("feedback_loop_manager")

    def test_failure_pattern_db_registered(self, enabled_config):
        reg = HarnessRegistry(enabled_config)
        assert reg.has("failure_pattern_db")

    def test_simple_history_registered(self, enabled_config):
        reg = HarnessRegistry(enabled_config)
        assert reg.has("simple_history")

    def test_list_available_includes_all_meta_harnesses(self, enabled_config):
        reg = HarnessRegistry(enabled_config)
        available = reg.list_available()
        for name in ["learning_store", "code_atlas", "tech_diligence", "decision_engine",
                     "feedback_loop_manager", "failure_pattern_db", "simple_history"]:
            assert name in available

    @pytest.mark.asyncio
    async def test_mock_decision_engine_statistics_all_false(self, disabled_config):
        reg = HarnessRegistry(disabled_config)
        engine = reg.get("decision_engine")
        stats = engine.get_statistics()
        assert stats["has_learning_store"] is False

    def test_decision_engine_wired_to_learning_store_same_instance(self, enabled_config):
        reg = HarnessRegistry(enabled_config)
        engine = reg.get("decision_engine")
        store = reg.get("learning_store")
        assert engine.learning_store is store

"""Extended tests for forge_harness/harness_registry.py.

Targets paths not exercised by test_harness_registry.py:

- MockDeploymentHarness: full_deploy, check_quality_gates, run_smoke_tests
- MockAnalyticsHarness: create_mvp_dashboard
- MockGrowthHarness: run_experiment
- MockLaunchHarness: launch
- Archived factory methods (notion_storage, analytics_harness, growth_harness, launch_harness)
  raise NotImplementedError
- HarnessRegistry._initializing set cleanup after successful get
- get_for_orchestration: skips unregistered optional harnesses
- HarnessConfig field mutation (setattr-based patching)
- create_harness_registry: kwargs with non-existent attr skipped gracefully
- HarnessRegistry with no custom factories argument (None path)
- list_available with added custom harness
- has() on an unregistered then registered name
- MockDecisionEngine.get_statistics has_learning_store key
- MockPosthogTracker: track with empty string event name
- register() overrides an existing built-in and then re-get returns new
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.harness_registry import (
    HarnessConfig,
    HarnessRegistry,
    MockAnalyticsHarness,
    MockContentHarness,
    MockDecisionEngine,
    MockDeploymentHarness,
    MockGrowthHarness,
    MockLaunchHarness,
    MockPosthogTracker,
    create_harness_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**kwargs) -> HarnessConfig:
    defaults = dict(domain="test-domain", project="test-project")
    defaults.update(kwargs)
    return HarnessConfig(**defaults)


def _registry(**kwargs) -> HarnessRegistry:
    return HarnessRegistry(_config(**kwargs))


# ===========================================================================
# MockDeploymentHarness
# ===========================================================================


class TestMockDeploymentHarness:
    """Tests for MockDeploymentHarness (returned by deployment factory when mocked)."""

    @pytest.mark.asyncio
    async def test_full_deploy_returns_dict(self):
        h = MockDeploymentHarness()
        result = await h.full_deploy()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_full_deploy_backend_url_is_none(self):
        h = MockDeploymentHarness()
        result = await h.full_deploy()
        assert result["backend_url"] is None

    @pytest.mark.asyncio
    async def test_full_deploy_frontend_url_is_none(self):
        h = MockDeploymentHarness()
        result = await h.full_deploy()
        assert result["frontend_url"] is None

    @pytest.mark.asyncio
    async def test_full_deploy_dry_run_true(self):
        h = MockDeploymentHarness(dry_run=True)
        assert h.dry_run is True
        result = await h.full_deploy()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_full_deploy_accepts_kwargs(self):
        h = MockDeploymentHarness()
        result = await h.full_deploy(domain="test", project="proj")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_check_quality_gates_returns_dict(self):
        h = MockDeploymentHarness()
        result = await h.check_quality_gates()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_check_quality_gates_coverage_zero(self):
        h = MockDeploymentHarness()
        result = await h.check_quality_gates()
        assert result["coverage"] == 0

    @pytest.mark.asyncio
    async def test_check_quality_gates_tests_passed_true(self):
        h = MockDeploymentHarness()
        result = await h.check_quality_gates()
        assert result["tests_passed"] is True

    @pytest.mark.asyncio
    async def test_run_smoke_tests_returns_dict(self):
        h = MockDeploymentHarness()
        result = await h.run_smoke_tests()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_run_smoke_tests_all_passed_true(self):
        h = MockDeploymentHarness()
        result = await h.run_smoke_tests()
        assert result["all_passed"] is True

    @pytest.mark.asyncio
    async def test_run_smoke_tests_accepts_kwargs(self):
        h = MockDeploymentHarness()
        result = await h.run_smoke_tests(target="staging")
        assert isinstance(result, dict)

    def test_dry_run_defaults_false(self):
        h = MockDeploymentHarness()
        assert h.dry_run is False


# ===========================================================================
# MockAnalyticsHarness
# ===========================================================================


class TestMockAnalyticsHarness:
    """Tests for MockAnalyticsHarness."""

    @pytest.mark.asyncio
    async def test_create_mvp_dashboard_returns_dict(self):
        h = MockAnalyticsHarness()
        result = await h.create_mvp_dashboard()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_create_mvp_dashboard_dashboard_id_none(self):
        h = MockAnalyticsHarness()
        result = await h.create_mvp_dashboard()
        assert result["dashboard_id"] is None

    @pytest.mark.asyncio
    async def test_create_mvp_dashboard_url_none(self):
        h = MockAnalyticsHarness()
        result = await h.create_mvp_dashboard()
        assert result["dashboard_url"] is None

    @pytest.mark.asyncio
    async def test_create_mvp_dashboard_accepts_kwargs(self):
        h = MockAnalyticsHarness()
        result = await h.create_mvp_dashboard(domain="test", project="proj")
        assert isinstance(result, dict)


# ===========================================================================
# MockGrowthHarness
# ===========================================================================


class TestMockGrowthHarness:
    """Tests for MockGrowthHarness."""

    @pytest.mark.asyncio
    async def test_run_experiment_returns_dict(self):
        h = MockGrowthHarness()
        result = await h.run_experiment()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_run_experiment_status_mock(self):
        h = MockGrowthHarness()
        result = await h.run_experiment()
        assert result["status"] == "mock"

    @pytest.mark.asyncio
    async def test_run_experiment_results_is_dict(self):
        h = MockGrowthHarness()
        result = await h.run_experiment()
        assert isinstance(result["results"], dict)

    @pytest.mark.asyncio
    async def test_run_experiment_accepts_kwargs(self):
        h = MockGrowthHarness()
        result = await h.run_experiment(experiment_id="exp-001")
        assert isinstance(result, dict)


# ===========================================================================
# MockLaunchHarness
# ===========================================================================


class TestMockLaunchHarness:
    """Tests for MockLaunchHarness."""

    @pytest.mark.asyncio
    async def test_launch_returns_dict(self):
        h = MockLaunchHarness()
        result = await h.launch()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_launch_status_mock(self):
        h = MockLaunchHarness()
        result = await h.launch()
        assert result["status"] == "mock"

    @pytest.mark.asyncio
    async def test_launch_url_is_none(self):
        h = MockLaunchHarness()
        result = await h.launch()
        assert result["url"] is None

    @pytest.mark.asyncio
    async def test_launch_accepts_kwargs(self):
        h = MockLaunchHarness()
        result = await h.launch(domain="test", version="1.0.0")
        assert isinstance(result, dict)


# ===========================================================================
# Archived factory methods raise NotImplementedError
# ===========================================================================


class TestArchivedFactories:
    """Tests for archived factory methods that raise NotImplementedError."""

    def test_notion_storage_raises_not_implemented(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_notion_storage(reg.config, reg)
        assert "archived" in str(exc_info.value).lower()

    def test_analytics_harness_raises_not_implemented(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_analytics_harness(reg.config, reg)
        assert "archived" in str(exc_info.value).lower()

    def test_growth_harness_raises_not_implemented(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_growth_harness(reg.config, reg)
        assert "archived" in str(exc_info.value).lower()

    def test_launch_harness_raises_not_implemented(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_launch_harness(reg.config, reg)
        assert "archived" in str(exc_info.value).lower()

    def test_notion_storage_message_includes_archive_path(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_notion_storage(reg.config, reg)
        assert "archive" in str(exc_info.value).lower()

    def test_analytics_harness_message_mentions_name(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_analytics_harness(reg.config, reg)
        assert "analytics_harness" in str(exc_info.value)

    def test_growth_harness_message_mentions_name(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_growth_harness(reg.config, reg)
        assert "growth_harness" in str(exc_info.value)

    def test_launch_harness_message_mentions_name(self):
        reg = _registry()
        with pytest.raises(NotImplementedError) as exc_info:
            reg._create_launch_harness(reg.config, reg)
        assert "launch_harness" in str(exc_info.value)


# ===========================================================================
# HarnessRegistry._initializing cleanup after success
# ===========================================================================


class TestInitializingSetCleanup:
    """Tests that _initializing set is cleaned up after successful creation."""

    def test_initializing_empty_after_successful_get(self):
        reg = _registry()
        reg.get("session")
        assert len(reg._initializing) == 0

    def test_initializing_empty_after_custom_factory_success(self):
        reg = _registry()
        reg.register("my_harness", lambda c, r: object())
        reg.get("my_harness")
        assert "my_harness" not in reg._initializing

    def test_initializing_empty_after_error(self):
        reg = _registry()
        reg.register("bad", lambda c, r: (_ for _ in ()).throw(ValueError("err")))
        try:
            reg.get("bad")
        except Exception:
            pass
        assert "bad" not in reg._initializing

    def test_initializing_empty_initially(self):
        reg = _registry()
        assert len(reg._initializing) == 0


# ===========================================================================
# HarnessRegistry.get_for_orchestration — optional harnesses skipped
# ===========================================================================


class TestGetForOrchestrationExtended:
    """Tests for get_for_orchestration skipping unregistered optional harnesses."""

    def test_notion_not_in_result_when_unregistered(self, tmp_path):
        config = HarnessConfig(
            domain="test",
            project="test",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )
        reg = HarnessRegistry(config)
        result = reg.get_for_orchestration()
        assert "notion" not in result

    def test_analytics_not_in_result_when_unregistered(self, tmp_path):
        config = HarnessConfig(
            domain="test",
            project="test",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )
        reg = HarnessRegistry(config)
        result = reg.get_for_orchestration()
        assert "analytics" not in result

    def test_result_is_always_a_dict(self, tmp_path):
        config = HarnessConfig(
            domain="test",
            project="test",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )
        reg = HarnessRegistry(config)
        result = reg.get_for_orchestration()
        assert isinstance(result, dict)

    def test_registered_essential_appears_in_result(self, tmp_path):
        """A registered essential harness appears in get_for_orchestration result."""
        sentinel = object()
        config = HarnessConfig(
            domain="test",
            project="test",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )
        reg = HarnessRegistry(config, custom_factories={"content": lambda c, r: sentinel})
        result = reg.get_for_orchestration()
        assert "content" in result

    def test_broken_essential_still_skipped_gracefully(self, tmp_path):
        """A broken essential harness is skipped without crashing get_for_orchestration."""
        config = HarnessConfig(
            domain="test",
            project="test",
            session_dir=tmp_path / "sessions",
            approval_storage_dir=tmp_path / "approvals",
        )
        reg = HarnessRegistry(config, custom_factories={
            "deployment": lambda c, r: (_ for _ in ()).throw(RuntimeError("broken"))
        })
        # Should not raise
        result = reg.get_for_orchestration()
        assert isinstance(result, dict)


# ===========================================================================
# HarnessRegistry list_available — dynamic changes
# ===========================================================================


class TestListAvailableDynamic:
    """Tests for list_available after runtime changes."""

    def test_register_new_harness_appears_in_list(self):
        reg = _registry()
        initial = set(reg.list_available())
        reg.register("brand_new", lambda c, r: None)
        updated = set(reg.list_available())
        assert "brand_new" in updated
        assert len(updated) == len(initial) + 1

    def test_list_always_sorted(self):
        reg = _registry()
        reg.register("zzz_last", lambda c, r: None)
        reg.register("aaa_first", lambda c, r: None)
        available = reg.list_available()
        assert available == sorted(available)

    def test_overriding_builtin_keeps_same_count(self):
        reg = _registry()
        before = len(reg.list_available())
        reg.register("session", lambda c, r: None)  # override existing
        after = len(reg.list_available())
        assert before == after

    def test_has_returns_true_after_register(self):
        reg = _registry()
        assert not reg.has("dynamic_harness")
        reg.register("dynamic_harness", lambda c, r: None)
        assert reg.has("dynamic_harness")


# ===========================================================================
# HarnessConfig — field mutations via setattr
# ===========================================================================


class TestHarnessConfigMutation:
    """Tests for HarnessConfig fields that are mutated at runtime."""

    def test_set_domain(self):
        config = HarnessConfig()
        config.domain = "new-domain"
        assert config.domain == "new-domain"

    def test_set_dry_run_true(self):
        config = HarnessConfig(dry_run=False)
        config.dry_run = True
        assert config.dry_run is True

    def test_set_code_atlas_url(self):
        config = HarnessConfig()
        config.code_atlas_url = "http://new-atlas"
        assert config.code_atlas_url == "http://new-atlas"

    def test_set_learning_store_dir(self, tmp_path):
        config = HarnessConfig()
        new_dir = tmp_path / "new-learning"
        config.learning_store_dir = new_dir
        assert config.learning_store_dir == new_dir

    def test_email_config_dict(self):
        config = HarnessConfig(email_config={"host": "smtp.test.com", "port": 587})
        assert config.email_config["host"] == "smtp.test.com"
        assert config.email_config["port"] == 587

    def test_email_config_default_none(self):
        config = HarnessConfig()
        assert config.email_config is None


# ===========================================================================
# create_harness_registry — edge cases
# ===========================================================================


class TestCreateHarnessRegistryEdgeCases:
    """Edge cases for create_harness_registry factory function."""

    def test_kwargs_with_non_existent_attr_silently_skipped(self):
        """Unknown kwargs that don't match HarnessConfig attributes are ignored."""
        registry = create_harness_registry(
            domain="d",
            project="p",
            nonexistent_attr_xyz="value",
        )
        assert isinstance(registry, HarnessRegistry)

    def test_config_none_triggers_env_load(self):
        """When config=None, HarnessConfig.from_env is called."""
        registry = create_harness_registry(domain="env-test", project="env-proj")
        assert registry.config.domain == "env-test"

    def test_returns_harness_registry_type(self):
        registry = create_harness_registry(domain="d", project="p")
        assert type(registry).__name__ == "HarnessRegistry"

    def test_config_stored_on_registry(self):
        config = HarnessConfig(domain="explicit", project="explicit-proj")
        registry = create_harness_registry(config=config)
        assert registry.config is config

    def test_multiple_kwargs_all_applied(self, tmp_path):
        registry = create_harness_registry(
            domain="d",
            project="p",
            dry_run=True,
            meta_learning_enabled=False,
        )
        assert registry.config.dry_run is True
        assert registry.config.meta_learning_enabled is False


# ===========================================================================
# MockDecisionEngine — additional statistics fields
# ===========================================================================


class TestMockDecisionEngineExtended:
    """Extended tests for MockDecisionEngine."""

    def test_get_statistics_has_learning_store_key(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        assert "has_learning_store" in stats

    def test_get_statistics_has_learning_store_false(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        assert stats["has_learning_store"] is False

    def test_get_statistics_all_boolean_bridge_fields(self):
        engine = MockDecisionEngine()
        stats = engine.get_statistics()
        for key in ("has_atlas_bridge", "has_diligence_bridge", "has_learning_store"):
            assert stats[key] is False

    @pytest.mark.asyncio
    async def test_record_outcome_with_extra_kwargs(self):
        """record_outcome accepts extra kwargs without error."""
        engine = MockDecisionEngine()
        await engine.record_outcome("d-001", success=False, reason="test")

    @pytest.mark.asyncio
    async def test_get_recommendation_confidence_is_unknown(self):
        from forge_harness.meta_learning.schemas import ConfidenceLevel, DecisionContext
        engine = MockDecisionEngine()
        ctx = DecisionContext(domain="d", project="p", file_paths=[], tags=[])
        rec = await engine.get_recommendation(ctx)
        assert rec.confidence == ConfidenceLevel.UNKNOWN

    @pytest.mark.asyncio
    async def test_get_recommendation_warnings_empty(self):
        from forge_harness.meta_learning.schemas import DecisionContext
        engine = MockDecisionEngine()
        ctx = DecisionContext(domain="d", project="p", file_paths=[], tags=[])
        rec = await engine.get_recommendation(ctx)
        assert rec.warnings == []

    @pytest.mark.asyncio
    async def test_get_recommendation_scores_all_zero(self):
        from forge_harness.meta_learning.schemas import DecisionContext
        engine = MockDecisionEngine()
        ctx = DecisionContext(domain="d", project="p", file_paths=[], tags=[])
        rec = await engine.get_recommendation(ctx)
        assert rec.scores.risk_score == 0.0
        assert rec.scores.confidence_score == 0.0
        assert rec.scores.complexity_score == 0.0
        assert rec.scores.precedent_score == 0.0


# ===========================================================================
# MockPosthogTracker — extended
# ===========================================================================


class TestMockPosthogTrackerExtended:
    """Extended tests for MockPosthogTracker."""

    @pytest.mark.asyncio
    async def test_track_empty_event_name(self):
        tracker = MockPosthogTracker()
        await tracker.track("", {})

    @pytest.mark.asyncio
    async def test_track_complex_properties(self):
        tracker = MockPosthogTracker()
        props = {"nested": {"key": "value"}, "list": [1, 2, 3], "count": 42}
        await tracker.track("complex_event", props)

    @pytest.mark.asyncio
    async def test_track_batch_with_single_event(self):
        tracker = MockPosthogTracker()
        await tracker.track_batch(["single_event"], count=1)

    @pytest.mark.asyncio
    async def test_track_batch_returns_none(self):
        tracker = MockPosthogTracker()
        result = await tracker.track_batch(["e1"], count=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_track_returns_none(self):
        tracker = MockPosthogTracker()
        result = await tracker.track("event")
        assert result is None


# ===========================================================================
# HarnessRegistry — instance caching behavior
# ===========================================================================


class TestInstanceCachingBehavior:
    """Tests for the lazy caching behavior of HarnessRegistry."""

    def test_posthog_is_cached_after_first_get(self):
        reg = _registry()
        p1 = reg.get("posthog")
        p2 = reg.get("posthog")
        assert p1 is p2

    def test_instances_dict_grows_with_each_unique_get(self):
        reg = _registry()
        before = len(reg._instances)
        reg.get("session")
        after_first = len(reg._instances)
        reg.get("posthog")
        after_second = len(reg._instances)
        assert after_first == before + 1
        assert after_second == before + 2

    def test_re_register_clears_old_instance(self):
        reg = _registry()
        old = reg.get("posthog")
        reg.register("posthog", lambda c, r: "new_posthog")
        # old instance should be cleared
        assert "posthog" not in reg._instances or reg._instances.get("posthog") != old

    def test_is_initialized_transitions_correctly(self):
        reg = _registry()
        assert not reg.is_initialized("posthog")
        reg.get("posthog")
        assert reg.is_initialized("posthog")

    def test_multiple_custom_factories_independent(self):
        sentinel_a = object()
        sentinel_b = object()
        reg = HarnessRegistry(HarnessConfig(), {
            "factory_a": lambda c, r: sentinel_a,
            "factory_b": lambda c, r: sentinel_b,
        })
        assert reg.get("factory_a") is sentinel_a
        assert reg.get("factory_b") is sentinel_b
        assert reg.get("factory_a") is not sentinel_b


# ===========================================================================
# HarnessRegistry — DEFAULT_FACTORIES class attribute
# ===========================================================================


class TestDefaultFactoriesClassAttribute:
    """Tests for the _DEFAULT_FACTORIES class attribute."""

    def test_default_factories_is_dict(self):
        assert isinstance(HarnessRegistry._DEFAULT_FACTORIES, dict)

    def test_default_factories_is_empty(self):
        """Built-in factories are registered in __init__, not _DEFAULT_FACTORIES."""
        assert len(HarnessRegistry._DEFAULT_FACTORIES) == 0

    def test_class_attribute_not_modified_by_instance(self):
        """Registering factories on an instance does not affect the class dict."""
        reg = HarnessRegistry(HarnessConfig())
        reg.register("runtime_factory", lambda c, r: None)
        assert "runtime_factory" not in HarnessRegistry._DEFAULT_FACTORIES

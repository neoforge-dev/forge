"""
Tests for Ralph Loop Harness
============================

Tests for forge_harness.ralph_loop module.
"""

import json
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestFeatureSpec:
    """Tests for FeatureSpec dataclass."""

    def test_feature_spec(self):
        """Test FeatureSpec creation."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        spec = FeatureSpec(
            id="test-001",
            name="Test Feature",
            description="A test feature",
        )
        assert spec.id == "test-001"
        assert spec.name == "Test Feature"
        assert spec.description == "A test feature"
        assert spec.status == FeatureStatus.PENDING
        assert spec.priority == "medium"
        assert spec.acceptance_criteria == []
        assert spec.depends_on == []
        assert spec.tests == []
        assert spec.estimated_tokens == 4000
        assert spec.attempts == 0
        assert spec.last_error is None

    def test_feature_spec_to_dict(self):
        """Test FeatureSpec.to_dict serialization."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        spec = FeatureSpec(
            id="auth-001",
            name="Authentication",
            description="Implement user auth",
            status=FeatureStatus.PASSING,
            priority="critical",
            acceptance_criteria=["Login works", "Logout works"],
            depends_on=["setup-001"],
            tests=["test_login", "test_logout"],
            estimated_tokens=5000,
            attempts=2,
            last_error=None,
        )

        data = spec.to_dict()

        assert data["id"] == "auth-001"
        assert data["name"] == "Authentication"
        assert data["status"] == "passing"  # Value, not enum
        assert data["priority"] == "critical"
        assert data["acceptance_criteria"] == ["Login works", "Logout works"]
        assert data["depends_on"] == ["setup-001"]
        assert data["tests"] == ["test_login", "test_logout"]
        assert data["estimated_tokens"] == 5000
        assert data["attempts"] == 2

    def test_feature_spec_from_dict(self):
        """Test FeatureSpec.from_dict deserialization."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        data = {
            "id": "db-001",
            "name": "Database Setup",
            "description": "Set up database",
            "status": "in_progress",
            "priority": "high",
            "acceptance_criteria": ["Tables created"],
            "depends_on": [],
            "tests": ["test_db_connection"],
            "estimated_tokens": 3000,
            "attempts": 1,
            "last_error": "Connection failed",
        }

        spec = FeatureSpec.from_dict(data)

        assert spec.id == "db-001"
        assert spec.name == "Database Setup"
        assert spec.status == FeatureStatus.IN_PROGRESS
        assert spec.priority == "high"
        assert spec.acceptance_criteria == ["Tables created"]
        assert spec.tests == ["test_db_connection"]
        assert spec.last_error == "Connection failed"

    def test_feature_spec_roundtrip(self):
        """Test FeatureSpec to_dict and from_dict roundtrip."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        original = FeatureSpec(
            id="api-001",
            name="API Endpoints",
            description="REST API",
            status=FeatureStatus.FAILING,
            priority="high",
            acceptance_criteria=["GET /users", "POST /users"],
            depends_on=["auth-001"],
            tests=["test_api"],
            estimated_tokens=6000,
            attempts=3,
            last_error="404 Not Found",
        )

        data = original.to_dict()
        restored = FeatureSpec.from_dict(data)

        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.last_error == original.last_error


class TestFeatureStatus:
    """Tests for FeatureStatus enum."""

    def test_feature_status(self):
        """Test FeatureStatus enum values."""
        from forge_harness.ralph_loop import FeatureStatus

        assert FeatureStatus.PENDING.value == "pending"
        assert FeatureStatus.IN_PROGRESS.value == "in_progress"
        assert FeatureStatus.PASSING.value == "passing"
        assert FeatureStatus.FAILING.value == "failing"
        assert FeatureStatus.BLOCKED.value == "blocked"
        assert FeatureStatus.SKIPPED.value == "skipped"

    def test_feature_status_from_value(self):
        """Test creating FeatureStatus from string value."""
        from forge_harness.ralph_loop import FeatureStatus

        assert FeatureStatus("pending") == FeatureStatus.PENDING
        assert FeatureStatus("passing") == FeatureStatus.PASSING
        assert FeatureStatus("blocked") == FeatureStatus.BLOCKED


class TestLoopConfig:
    """Tests for LoopConfig dataclass."""

    def test_loop_config(self):
        """Test LoopConfig defaults."""
        from forge_harness.ralph_loop import LoopConfig

        config = LoopConfig()

        assert config.max_iterations == 100
        assert config.max_failures_per_feature == 5
        assert config.checkpoint_interval == 10
        assert config.test_command == "uv run pytest tests/ -v --tb=short -x"
        assert config.timeout_seconds == 3600
        assert config.dry_run is False

    def test_loop_config_custom(self):
        """Test LoopConfig with custom values."""
        from forge_harness.ralph_loop import LoopConfig

        config = LoopConfig(
            max_iterations=50,
            max_failures_per_feature=3,
            checkpoint_interval=5,
            test_command="pytest -x",
            timeout_seconds=1800,
            dry_run=True,
        )

        assert config.max_iterations == 50
        assert config.max_failures_per_feature == 3
        assert config.checkpoint_interval == 5
        assert config.test_command == "pytest -x"
        assert config.timeout_seconds == 1800
        assert config.dry_run is True


class TestLoopResult:
    """Tests for LoopResult dataclass."""

    def test_loop_result(self):
        """Test LoopResult creation."""

        from forge_harness.ralph_loop import LoopResult

        result = LoopResult(
            success=True,
            iterations=42,
            features_completed=10,
            features_blocked=1,
            features_remaining=0,
            total_tokens=50000,
            duration_seconds=3600.5,
        )

        assert result.success is True
        assert result.iterations == 42
        assert result.features_completed == 10
        assert result.features_blocked == 1
        assert result.features_remaining == 0
        assert result.total_tokens == 50000
        assert result.duration_seconds == 3600.5
        assert result.checkpoint_path is None

    def test_loop_result_with_checkpoint(self):
        """Test LoopResult with checkpoint path."""

        from forge_harness.ralph_loop import LoopResult

        result = LoopResult(
            success=False,
            iterations=100,
            features_completed=8,
            features_blocked=2,
            features_remaining=5,
            total_tokens=80000,
            duration_seconds=7200.0,
            checkpoint_path=Path("/tmp/checkpoint.json"),
        )

        assert result.success is False
        assert result.checkpoint_path == Path("/tmp/checkpoint.json")


class TestFeatureStore:
    """Tests for FeatureStore."""

    @pytest.fixture
    def sample_features_json(self, tmp_path):
        """Create sample features.json."""
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "test-001",
                    "name": "First Feature",
                    "description": "First test feature",
                    "status": "pending",
                    "priority": "critical",
                    "depends_on": [],
                },
                {
                    "id": "test-002",
                    "name": "Second Feature",
                    "description": "Depends on first",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ["test-001"],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))
        return path

    def test_feature_store_load(self, sample_features_json):
        """Test FeatureStore.load reads features."""
        from forge_harness.ralph_loop import FeatureStatus, FeatureStore

        store = FeatureStore(sample_features_json)
        features = store.load()

        assert len(features) == 2
        # Critical should come first due to priority sorting
        assert features[0].id == "test-001"
        assert features[0].name == "First Feature"
        assert features[0].status == FeatureStatus.PENDING
        assert features[0].priority == "critical"
        assert features[1].id == "test-002"
        assert features[1].priority == "high"

    def test_feature_store_save(self, sample_features_json, tmp_path):
        """Test FeatureStore.save writes features."""
        from forge_harness.ralph_loop import FeatureStatus, FeatureStore

        # Load and modify
        store = FeatureStore(sample_features_json)
        store.load()

        # Update a feature status
        feature = store.get("test-001")
        feature.status = FeatureStatus.PASSING
        feature.attempts = 1
        store.update(feature)

        # Save
        store.save()

        # Reload and verify
        store2 = FeatureStore(sample_features_json)
        store2.load()
        restored = store2.get("test-001")

        assert restored.status == FeatureStatus.PASSING
        assert restored.attempts == 1

    async def test_feature_store_next(self, sample_features_json):
        """Test get_next_pending respects dependencies."""
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(sample_features_json)
        store.load()

        # First next should be test-001 (no dependencies)
        next_feature = await store.get_next_pending()
        assert next_feature is not None
        assert next_feature.id == "test-001"

        # test-002 depends on test-001, so it shouldn't be available yet
        # Even though test-002 is pending, its dependency isn't passing

    async def test_feature_store_next_with_passing(self, tmp_path):
        """Test get_next_pending when dependency is passing."""
        from forge_harness.ralph_loop import FeatureStore

        # Create features with passing dependency
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "base-001",
                    "name": "Base Feature",
                    "description": "Base",
                    "status": "passing",
                    "priority": "critical",
                    "depends_on": [],
                },
                {
                    "id": "dep-001",
                    "name": "Dependent Feature",
                    "description": "Depends on base",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ["base-001"],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        store.load()

        # Now dep-001 should be available since base-001 is passing
        next_feature = await store.get_next_pending()
        assert next_feature is not None
        assert next_feature.id == "dep-001"

    def test_feature_store_get_stats(self, sample_features_json):
        """Test get_stats returns correct counts."""
        from forge_harness.ralph_loop import FeatureStatus, FeatureStore

        store = FeatureStore(sample_features_json)
        store.load()

        stats = store.get_stats()

        assert stats["pending"] == 2
        assert stats["passing"] == 0
        assert stats["failing"] == 0
        assert stats["blocked"] == 0

        # Update one feature
        feature = store.get("test-001")
        feature.status = FeatureStatus.PASSING
        store.update(feature)

        stats = store.get_stats()
        assert stats["pending"] == 1
        assert stats["passing"] == 1

    def test_feature_store_load_missing_file(self, tmp_path):
        """Test load handles missing file gracefully."""
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(tmp_path / "nonexistent.json")
        features = store.load()

        assert features == []

    async def test_simple_history_influences_feature_selection(self, tmp_path):
        """Test that SimpleHistory success rates affect feature priority."""
        from forge_harness.ralph_loop import FeatureStatus, FeatureStore, LoopConfig
        from forge_harness.simple_history import SimpleHistory

        # Create history with high success rate for 'auth' features
        history_file = tmp_path / "test_history.jsonl"
        history = SimpleHistory(history_file=history_file)

        # Record 10 successful 'auth' feature outcomes
        for i in range(10):
            history.record("forge", "test", "feature:auth", success=True, context={})

        # Record 10 failed 'ui' feature outcomes
        for i in range(10):
            history.record("forge", "test", "feature:ui", success=False, context={})

        # Create features with same base priority but different types
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "auth-001",
                    "name": "Auth Feature",
                    "description": "Authentication module",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
                {
                    "id": "ui-001",
                    "name": "UI Feature",
                    "description": "User interface component",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
            ],
        }
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        store = FeatureStore(features_path)
        store.load()

        # Create config with default values
        config = LoopConfig()

        # Without history: order is based on priority only
        next_feature_no_history = await store.get_next_pending(simple_history=None)
        # Should get first one (auth-001) just by order
        assert next_feature_no_history.id in ["auth-001", "ui-001"]

        # With history: auth-001 should be boosted due to high success rate
        next_feature_with_history = await store.get_next_pending(
            simple_history=history, config=config
        )
        assert next_feature_with_history.id == "auth-001"  # History boosted
        assert hasattr(next_feature_with_history, "_history_boost")
        assert next_feature_with_history._history_boost == -0.5  # Priority boost

        # Mark auth-001 as passing so we can check if ui-001 is deprioritized
        auth_feature = store.get("auth-001")
        auth_feature.status = FeatureStatus.PASSING
        store.update(auth_feature)

        # Now only ui-001 remains, it should have a penalty
        next_feature = await store.get_next_pending(simple_history=history, config=config)
        assert next_feature.id == "ui-001"
        assert hasattr(next_feature, "_history_boost")
        assert next_feature._history_boost == 0.5  # Deprioritized due to low success rate

    async def test_minimum_sample_size_guard(self, tmp_path):
        """Test that new feature types with <3 samples don't get penalized."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig
        from forge_harness.simple_history import SimpleHistory

        # Create history with only 2 failed 'new-type' feature outcomes
        history_file = tmp_path / "test_history.jsonl"
        history = SimpleHistory(history_file=history_file)

        # Record only 2 failures for 'new-type' - below minimum sample size
        for i in range(2):
            history.record("forge", "test", "feature:new", success=False, context={})

        # Create a feature with the new type
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "new-001",
                    "name": "New Feature Type",
                    "description": "A new feature type with limited history",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
            ],
        }
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        store = FeatureStore(features_path)
        store.load()

        # Create config with default min_samples=3
        config = LoopConfig()

        # With history but insufficient samples: should NOT be penalized
        next_feature = await store.get_next_pending(simple_history=history, config=config)
        assert next_feature.id == "new-001"
        assert hasattr(next_feature, "_history_boost")
        assert next_feature._history_boost == 0  # No penalty despite 0% success rate

    async def test_config_thresholds_respected(self, tmp_path):
        """Test that custom config values are used for boost/penalty."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig
        from forge_harness.simple_history import SimpleHistory

        # Create history with 80% success rate for 'api' features
        history_file = tmp_path / "test_history.jsonl"
        history = SimpleHistory(history_file=history_file)

        # Record 8 successes and 2 failures for 'api'
        for i in range(8):
            history.record("forge", "test", "feature:api", success=True, context={})
        for i in range(2):
            history.record("forge", "test", "feature:api", success=False, context={})

        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "api-001",
                    "name": "API Feature",
                    "description": "API endpoint",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
            ],
        }
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        store = FeatureStore(features_path)
        store.load()

        # Custom config: higher threshold (0.9) and different boost value
        config = LoopConfig(
            history_threshold_high=0.9,  # 80% won't trigger boost
            history_boost_high=-1.0,  # Custom boost value
        )

        # With 80% success rate and 0.9 threshold: should NOT boost
        next_feature = await store.get_next_pending(simple_history=history, config=config)
        assert next_feature.id == "api-001"
        assert hasattr(next_feature, "_history_boost")
        assert next_feature._history_boost == 0  # No boost since 0.8 < 0.9

        # Now use default threshold (0.7): should boost
        config_default = LoopConfig()
        next_feature_boosted = await store.get_next_pending(
            simple_history=history, config=config_default
        )
        assert next_feature_boosted.id == "api-001"
        assert hasattr(next_feature_boosted, "_history_boost")
        assert next_feature_boosted._history_boost == -0.5  # Default boost

    @pytest.mark.asyncio
    async def test_feature_type_extraction_priority(self, tmp_path):
        """Test that category/tags take priority over ID prefix for feature type extraction."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness
        from forge_harness.simple_history import SimpleHistory

        # Create history with high success for 'authentication' category
        history_file = tmp_path / "test_history.jsonl"
        history = SimpleHistory(history_file=history_file)

        for i in range(10):
            history.record("forge", "test", "feature:authentication", success=True, context={})

        # Create features: one with category, one with tags, one with just ID
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "xyz-001",  # ID doesn't match history pattern
                    "name": "Feature with Category",
                    "description": "Should use category for matching",
                    "category": "authentication",  # Should match history
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
                {
                    "id": "abc-002",  # ID doesn't match history pattern
                    "name": "Feature with Tags",
                    "description": "Should use first tag for matching",
                    "tags": ["authentication", "security"],  # Should match history
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
            ],
        }
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        store = FeatureStore(features_path)
        store.load()
        config = LoopConfig(domain="forge", project="test")  # Match recorded domain
        harness = RalphLoopHarness(store, config, simple_history=history)

        # Both features should get boosted because they match 'authentication' via category/tags
        next_feature = await harness._select_next_feature()
        assert next_feature.id in ["xyz-001", "abc-002"]
        assert hasattr(next_feature, "_history_boost")
        # Boost is -0.5 (history_boost_high) + -0.25 (recent success boost) = -0.75
        assert (
            next_feature._history_boost == -0.75
        )  # Boosted due to category/tag match + recent success


class TestRalphLoopHarness:
    """Tests for RalphLoopHarness."""

    @pytest.fixture
    def sample_features(self, tmp_path):
        """Create sample features.json."""
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "feat-001",
                    "name": "First Feature",
                    "description": "First",
                    "status": "pending",
                    "priority": "critical",
                    "depends_on": [],
                    "tests": ["test_first"],
                },
                {
                    "id": "feat-002",
                    "name": "Second Feature",
                    "description": "Second",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ["feat-001"],
                    "tests": ["test_second"],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))
        return path

    @pytest.fixture
    def config(self):
        """Create test config."""
        from forge_harness.ralph_loop import LoopConfig

        return LoopConfig(max_iterations=5, dry_run=True, checkpoint_interval=2)

    @pytest.mark.asyncio
    async def test_ralph_loop_run(self, sample_features, config):
        """Test RalphLoopHarness.run basic execution."""
        from forge_harness.ralph_loop import FeatureStore, RalphLoopHarness

        store = FeatureStore(sample_features)
        harness = RalphLoopHarness(store, config)

        result = await harness.run()

        # In dry_run mode, tests pass and features complete
        assert result.iterations > 0
        assert result.success is True
        assert result.features_completed == 2
        assert result.features_remaining == 0

    @pytest.mark.asyncio
    async def test_select_next_feature_uses_recent_history(self, tmp_path):
        """SimpleHistory recent successes should boost matching features."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "alpha-001",
                    "name": "Alpha Feature",
                    "description": "Alpha",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
                {
                    "id": "beta-001",
                    "name": "Beta Feature",
                    "description": "Beta",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": [],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        class StubHistory:
            def __init__(self):
                self.called = False

            def get_recent(self, limit: int = 10):
                self.called = True
                return [{"action": "feature:beta", "success": True}]

            def get_success_rate(self, domain: str, action: str, limit: int = 10) -> float:
                return 0.5

            def _get_matching_records(self, domain: str, action: str, limit: int) -> list[dict]:
                return []

            def get_cross_domain_success_rate(
                self,
                action: str,
                exclude_domain: str | None = None,
                min_samples: int = 5,
                limit: int = 50,
            ) -> tuple[float, int]:
                return (0.5, 0)

        history = StubHistory()
        store = FeatureStore(path)
        store.load()
        harness = RalphLoopHarness(store, LoopConfig(), simple_history=history)

        selected = await harness._select_next_feature()

        assert history.called is True
        assert selected is not None
        assert selected.id == "beta-001"

    @pytest.mark.asyncio
    async def test_ralph_loop_complete(self, tmp_path):
        """Test loop completes when all features pass."""
        from forge_harness.ralph_loop import (
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        # Create features that are already passing
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "done-001",
                    "name": "Done Feature",
                    "description": "Already done",
                    "status": "passing",
                    "priority": "high",
                    "depends_on": [],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=10, dry_run=True)
        harness = RalphLoopHarness(store, config)

        result = await harness.run()

        assert result.success is True
        assert result.iterations == 1  # Should exit on first check
        assert result.features_completed == 1

    @pytest.mark.asyncio
    async def test_ralph_loop_max_iterations(self, tmp_path):
        """Test loop stops at max iterations."""
        from forge_harness.ralph_loop import (
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        # Create features with unsatisfied dependencies (will keep trying)
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "blocked-001",
                    "name": "Blocked Feature",
                    "description": "Can never complete",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ["nonexistent"],  # Dependency doesn't exist
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=3, dry_run=True)
        harness = RalphLoopHarness(store, config)

        result = await harness.run()

        # Should stop when no features available (unsatisfied deps)
        assert result.success is False
        assert result.iterations == 1  # Exits immediately when no feature available


class TestRalphLoopTests:
    """Tests for test running in RalphLoopHarness."""

    @pytest.fixture
    def harness(self, tmp_path):
        """Create a test harness with a simple features file."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=False,
            test_command="echo 'PASSED'",
            timeout_seconds=10,
        )
        return RalphLoopHarness(store, config)

    @pytest.mark.asyncio
    async def test_ralph_run_tests_pass(self, harness):
        """Test _run_tests with passing tests."""
        from forge_harness.ralph_loop import FeatureSpec

        feature = FeatureSpec(
            id="test-001",
            name="Test Feature",
            description="Test",
            tests=["test_example"],
        )

        passed, error = await harness._run_tests(feature)

        assert passed is True
        assert error is None

    @pytest.mark.asyncio
    async def test_ralph_run_tests_fail(self, tmp_path):
        """Test _run_tests with failing tests."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=False,
            test_command="exit 1",  # Always fails
            timeout_seconds=10,
        )
        harness = RalphLoopHarness(store, config)

        feature = FeatureSpec(
            id="test-002",
            name="Failing Feature",
            description="Fails",
        )

        passed, error = await harness._run_tests(feature)

        assert passed is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_ralph_run_tests_dry_run(self, tmp_path):
        """Test _run_tests in dry run mode."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=True,  # Dry run mode
            test_command="exit 1",  # Would fail if run
        )
        harness = RalphLoopHarness(store, config)

        feature = FeatureSpec(
            id="test-003",
            name="Dry Run Feature",
            description="Dry run",
        )

        passed, error = await harness._run_tests(feature)

        # In dry run mode, tests always pass
        assert passed is True
        assert error is None


class TestRalphLoopCheckpoints:
    """Tests for checkpoint save/resume."""

    @pytest.mark.asyncio
    async def test_ralph_checkpoint_save(self, tmp_path):
        """Test _save_checkpoint creates checkpoint."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "ckpt-001",
                    "name": "Checkpoint Feature",
                    "description": "Test",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": [],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        store.load()
        config = LoopConfig(max_iterations=5, dry_run=True)
        harness = RalphLoopHarness(store, config)
        harness._iteration = 5

        checkpoint_path = await harness._save_checkpoint()

        assert checkpoint_path.exists()
        assert ".forge/ralph_checkpoints" in str(checkpoint_path)

        # Verify checkpoint content
        checkpoint_data = json.loads(checkpoint_path.read_text())
        assert checkpoint_data["iteration"] == 5
        assert "timestamp" in checkpoint_data
        assert "stats" in checkpoint_data

    @pytest.mark.asyncio
    async def test_ralph_checkpoint_resume(self, tmp_path):
        """Test resume restores state correctly."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "resume-001",
                    "name": "Resume Feature",
                    "description": "Test",
                    "status": "passing",  # Already passing
                    "priority": "high",
                    "depends_on": [],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        # Create checkpoint
        checkpoint_dir = tmp_path / ".forge/ralph_checkpoints"
        checkpoint_dir.mkdir()
        checkpoint_path = checkpoint_dir / "checkpoint_test.json"
        checkpoint_data = {
            "iteration": 10,
            "timestamp": "2026-01-13T12:00:00Z",
            "features_path": str(path),
            "config": {"max_iterations": 20, "dry_run": True},
            "stats": {"pending": 0, "passing": 1},
        }
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=20, dry_run=True)
        harness = RalphLoopHarness(store, config)

        result = await harness.resume(checkpoint_path)

        # Should have resumed from iteration 10
        assert harness._iteration >= 10
        assert result.success is True


class TestGitHubFeatureTracker:
    """Tests for GitHubFeatureTracker."""

    def test_github_tracker_init(self):
        """Test GitHubFeatureTracker initialization."""
        from forge_harness.ralph_loop import GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", "token123")

        assert tracker.repo == "owner/repo"
        assert tracker.token == "token123"

    def test_github_tracker_headers(self):
        """Test header generation."""
        from forge_harness.ralph_loop import GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", "mytoken")
        headers = tracker._get_headers()

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer mytoken"
        assert "application/vnd.github+json" in headers["Accept"]

    def test_github_tracker_issue_body(self):
        """Test feature to issue body conversion."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus, GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", "token")

        feature = FeatureSpec(
            id="test-001",
            name="Test Feature",
            description="A test feature",
            status=FeatureStatus.PENDING,
            priority="high",
            acceptance_criteria=["Criteria 1", "Criteria 2"],
            depends_on=["dep-001"],
            tests=["test_example"],
        )

        body = tracker._feature_to_issue_body(feature)

        assert "## Test Feature" in body
        assert "A test feature" in body
        assert "test-001" in body
        assert "high" in body
        assert "- [ ] Criteria 1" in body
        assert "dep-001" in body
        assert "test_example" in body

    @pytest.mark.asyncio
    async def test_github_tracker_sync_no_token(self):
        """Test sync returns empty dict without token."""
        from forge_harness.ralph_loop import FeatureSpec, GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", None)
        tracker.token = None  # Ensure no token

        features = [FeatureSpec(id="f1", name="Feature 1", description="Test")]

        result = await tracker.sync_to_issues(features)

        assert result == {}

    @pytest.mark.asyncio
    async def test_github_tracker_status_no_token(self):
        """Test status update does nothing without token."""
        from forge_harness.ralph_loop import FeatureStatus, GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", None)
        tracker.token = None  # Ensure no token

        # Should not raise, just log warning
        await tracker.update_issue_status("feat-001", 123, FeatureStatus.PASSING)

    def test_github_tracker_status_labels(self):
        """Test status label mappings."""
        from forge_harness.ralph_loop import FeatureStatus, GitHubFeatureTracker

        assert GitHubFeatureTracker.STATUS_LABELS[FeatureStatus.PENDING] == "status:pending"
        assert GitHubFeatureTracker.STATUS_LABELS[FeatureStatus.PASSING] == "status:passing"
        assert GitHubFeatureTracker.STATUS_LABELS[FeatureStatus.BLOCKED] == "status:blocked"


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_ralph_loop(self, tmp_path):
        """Test create_ralph_loop factory."""
        from forge_harness.ralph_loop import RalphLoopHarness, create_ralph_loop

        # Create empty features file
        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        harness = create_ralph_loop(
            features_path=path,
            max_iterations=50,
            max_failures_per_feature=3,
            dry_run=True,
        )

        assert isinstance(harness, RalphLoopHarness)
        assert harness.config.max_iterations == 50
        assert harness.config.max_failures_per_feature == 3
        assert harness.config.dry_run is True

    def test_create_ralph_loop_with_orchestrator(self, tmp_path):
        """Test create_ralph_loop with orchestrator."""
        from unittest.mock import MagicMock

        from forge_harness.ralph_loop import create_ralph_loop

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        mock_orchestrator = MagicMock()
        harness = create_ralph_loop(
            features_path=path,
            orchestrator=mock_orchestrator,
        )

        assert harness.orchestrator is mock_orchestrator


class TestCreateFeaturesFromPlan:
    """Tests for create_features_from_plan function."""

    def test_create_features_from_plan_basic(self, tmp_path):
        """Test parsing basic PLAN.md with task tables."""
        from forge_harness.ralph_loop import create_features_from_plan

        plan_content = """# Test Plan

## Epic H1: Test Infrastructure
**Priority:** P0

### H1.1: Refactor main.py Tests

**Tasks:**

| Task ID | Task | Effort |
|---------|------|--------|
| H1.1.1 | Extract CLI parsing tests | 1.5h |
| H1.1.2 | Test find_forge_root | 30m |
| H1.1.3 | Test validate_environment | 30m |

### H1.2: Add repurpose_harness tests

| Task ID | Task | Effort |
|---------|------|--------|
| H1.2.1 | Test ContentPiece dataclass | 1h |
| H1.2.2 | Test RepurposeSuggestion | 1h |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_content)

        features = create_features_from_plan(plan_path)

        assert len(features) == 5
        assert features[0].id == "H1.1.1"
        assert features[0].name == "Extract CLI parsing tests"
        assert features[0].priority == "critical"  # P0
        assert features[0].estimated_tokens == 3000  # 1.5h * 2000

        assert features[1].id == "H1.1.2"
        assert features[1].estimated_tokens == 1000  # 30m

        assert features[3].id == "H1.2.1"
        assert features[4].id == "H1.2.2"

    def test_create_features_from_plan_file_not_found(self, tmp_path):
        """Test error when plan file doesn't exist."""
        from forge_harness.ralph_loop import create_features_from_plan

        with pytest.raises(FileNotFoundError):
            create_features_from_plan(tmp_path / "nonexistent.md")

    def test_create_features_from_plan_simple_table(self, tmp_path):
        """Test parsing simpler task table format."""
        from forge_harness.ralph_loop import create_features_from_plan

        plan_content = """# Sprint Plan

## Epic E1: MVP Features
**Priority:** P1

### Phase 1: Core Features

| Task | Est | Priority |
|------|-----|----------|
| Implement user login | 2h | P1 |
| Add password reset | 1h | P2 |
| Create dashboard | 4h | P1 |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_content)

        features = create_features_from_plan(plan_path)

        assert len(features) == 3
        assert features[0].name == "Implement user login"
        assert features[0].priority == "high"  # P1
        assert features[0].estimated_tokens == 4000  # 2h

        assert features[1].name == "Add password reset"
        assert features[2].name == "Create dashboard"

    def test_create_features_from_plan_empty(self, tmp_path):
        """Test parsing empty plan file."""
        from forge_harness.ralph_loop import create_features_from_plan

        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# Empty Plan\n\nNo tasks yet.")

        features = create_features_from_plan(plan_path)

        assert len(features) == 0

    def test_create_features_from_plan_mixed_priorities(self, tmp_path):
        """Test parsing plan with different priority levels."""
        from forge_harness.ralph_loop import create_features_from_plan

        plan_content = """# Plan

## Epic A: Critical Work
**Priority:** P0

| Task ID | Task | Effort |
|---------|------|--------|
| A.1 | Critical task | 2h |

## Epic B: High Priority
**Priority:** P1

| Task ID | Task | Effort |
|---------|------|--------|
| B.1 | High priority task | 2h |

## Epic C: Medium Priority
**Priority:** P2

| Task ID | Task | Effort |
|---------|------|--------|
| C.1 | Medium task | 2h |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_content)

        features = create_features_from_plan(plan_path)

        assert len(features) == 3
        assert features[0].priority == "critical"
        assert features[1].priority == "high"
        assert features[2].priority == "medium"

    def test_create_features_dependencies_inferred(self, tmp_path):
        """Test that dependencies are inferred from task ordering."""
        from forge_harness.ralph_loop import create_features_from_plan

        plan_content = """# Plan

## Epic H1: Tests

### H1.1: Phase 1

| Task ID | Task | Effort |
|---------|------|--------|
| H1.1.1 | First task | 1h |
| H1.1.2 | Second task | 1h |
| H1.1.3 | Third task | 1h |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_content)

        features = create_features_from_plan(plan_path)

        assert len(features) == 3
        # First task has no dependencies
        assert features[0].depends_on == []
        # Second task depends on first (same section H1.1.x)
        assert features[1].depends_on == ["H1.1.1"]
        # Third task depends on second
        assert features[2].depends_on == ["H1.1.2"]


class TestEstimateTokensFromEffort:
    """Tests for _estimate_tokens_from_effort helper."""

    def test_hours_parsing(self):
        """Test parsing hour formats."""
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        assert _estimate_tokens_from_effort("1h") == 2000
        assert _estimate_tokens_from_effort("2h") == 4000
        assert _estimate_tokens_from_effort("1.5h") == 3000
        assert _estimate_tokens_from_effort("0.5h") == 1000

    def test_minutes_parsing(self):
        """Test parsing minute formats."""
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        assert _estimate_tokens_from_effort("30m") == 1000
        assert _estimate_tokens_from_effort("60m") == 2000
        assert _estimate_tokens_from_effort("15m") == 500
        assert _estimate_tokens_from_effort("90m") == 3000

    def test_invalid_format(self):
        """Test handling of invalid formats."""
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        # Invalid formats should return default 4000
        assert _estimate_tokens_from_effort("invalid") == 4000
        assert _estimate_tokens_from_effort("") == 4000
        assert _estimate_tokens_from_effort("abc") == 4000


class TestAtlasIndexing:
    """Tests for Code Atlas indexing in RalphLoopHarness."""

    @pytest.fixture
    def mock_atlas_bridge(self):
        """Create mock CodeAtlasBridge."""
        bridge = AsyncMock()
        bridge.index_session = AsyncMock(return_value={"indexed": True, "message": "Success"})
        return bridge

    @pytest.fixture
    def harness_with_atlas(self, tmp_path, mock_atlas_bridge):
        """Create harness with mock Atlas bridge."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        return RalphLoopHarness(store, config, code_atlas_bridge=mock_atlas_bridge)

    @pytest.mark.asyncio
    async def test_index_feature_to_atlas_success(self, harness_with_atlas, mock_atlas_bridge):
        """Test _index_feature_to_atlas indexes successful feature."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        feature = FeatureSpec(
            id="auth-001",
            name="Implement authentication",
            description="Add JWT auth to API",
            status=FeatureStatus.PASSING,
            priority="critical",
            acceptance_criteria=["Login works", "Token refreshes"],
            attempts=1,
        )

        result = await harness_with_atlas._index_feature_to_atlas(feature, success=True)

        assert result["indexed"] is True
        mock_atlas_bridge.index_session.assert_called_once()

        # Verify the session summary structure
        call_args = mock_atlas_bridge.index_session.call_args
        session_summary = call_args.kwargs.get("session_summary") or call_args[1].get(
            "session_summary"
        )

        assert session_summary["feature_id"] == "auth-001"
        assert session_summary["feature_name"] == "Implement authentication"
        assert session_summary["success"] is True
        assert session_summary["domain"] == "test-domain"
        assert session_summary["project"] == "test-project"
        assert "priority:critical" in session_summary["tags"]
        assert "status:passing" in session_summary["tags"]
        assert "category:security" in session_summary["tags"]  # "auth" in name

    @pytest.mark.asyncio
    async def test_index_feature_to_atlas_failure(self, harness_with_atlas, mock_atlas_bridge):
        """Test _index_feature_to_atlas indexes failed feature."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        feature = FeatureSpec(
            id="api-001",
            name="Add API endpoint",
            description="REST endpoint for users",
            status=FeatureStatus.FAILING,
            priority="high",
            attempts=2,
        )

        result = await harness_with_atlas._index_feature_to_atlas(feature, success=False)

        assert result["indexed"] is True

        call_args = mock_atlas_bridge.index_session.call_args
        session_summary = call_args.kwargs.get("session_summary") or call_args[1].get(
            "session_summary"
        )

        assert session_summary["success"] is False
        assert "status:failing" in session_summary["tags"]
        assert "category:api" in session_summary["tags"]  # "api" in name

    @pytest.mark.asyncio
    async def test_index_feature_to_atlas_no_bridge(self, tmp_path):
        """Test _index_feature_to_atlas returns early without bridge."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=5, dry_run=True)
        harness = RalphLoopHarness(store, config)  # No atlas bridge

        feature = FeatureSpec(
            id="test-001",
            name="Test",
            description="Test feature",
        )

        result = await harness._index_feature_to_atlas(feature, success=True)

        assert result["indexed"] is False
        assert result["reason"] == "no_bridge"

    @pytest.mark.asyncio
    async def test_index_feature_to_atlas_handles_error(self, tmp_path):
        """Test _index_feature_to_atlas handles bridge errors gracefully."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        # Create mock bridge that raises error
        mock_bridge = AsyncMock()
        mock_bridge.index_session = AsyncMock(side_effect=Exception("Connection failed"))

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=5, dry_run=True)
        harness = RalphLoopHarness(store, config, code_atlas_bridge=mock_bridge)

        feature = FeatureSpec(
            id="test-001",
            name="Test",
            description="Test feature",
        )

        result = await harness._index_feature_to_atlas(feature, success=True)

        # Should return error result, not raise
        assert result["indexed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_index_feature_category_tags(self, harness_with_atlas, mock_atlas_bridge):
        """Test _index_feature_to_atlas adds correct category tags."""
        from forge_harness.ralph_loop import FeatureSpec

        test_cases = [
            ("security-001", "Add security headers", ["category:security"]),
            ("test-001", "Add unit tests", ["category:testing"]),
            ("refactor-001", "Refactor database layer", ["category:refactor"]),
            ("migrate-001", "Migration to new API", ["category:refactor"]),
            ("endpoint-001", "Create user endpoint", ["category:api"]),
        ]

        for feature_id, name, expected_tags in test_cases:
            mock_atlas_bridge.index_session.reset_mock()

            feature = FeatureSpec(
                id=feature_id,
                name=name,
                description="Test",
            )

            await harness_with_atlas._index_feature_to_atlas(feature, success=True)

            call_args = mock_atlas_bridge.index_session.call_args
            session_summary = call_args.kwargs.get("session_summary") or call_args[1].get(
                "session_summary"
            )

            for tag in expected_tags:
                assert tag in session_summary["tags"], f"Expected {tag} for feature {name}"

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_indexes_to_atlas(
        self, harness_with_atlas, mock_atlas_bridge
    ):
        """Test _trigger_feedback_loops indexes session summary to Atlas."""
        from datetime import datetime

        harness_with_atlas._iteration = 10
        harness_with_atlas._start_time = datetime.now(UTC)

        stats = {"passing": 5, "blocked": 1, "failing": 0, "pending": 0}

        await harness_with_atlas._trigger_feedback_loops(stats, duration=3600.0)

        # Should have called index_session
        mock_atlas_bridge.index_session.assert_called()

        call_args = mock_atlas_bridge.index_session.call_args
        session_summary = call_args.kwargs.get("session_summary") or call_args[1].get(
            "session_summary"
        )

        assert session_summary["features_completed"] == 5
        assert session_summary["features_blocked"] == 1
        assert session_summary["total_iterations"] == 10
        assert session_summary["duration_seconds"] == 3600.0
        assert "type:session_summary" in session_summary["tags"]

    @pytest.mark.asyncio
    async def test_run_loop_indexes_passing_features(self, tmp_path, mock_atlas_bridge):
        """Test that run() indexes each passing feature to Atlas."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "feat-001",
                    "name": "First Feature",
                    "description": "First",
                    "status": "pending",
                    "priority": "critical",
                    "depends_on": [],
                },
                {
                    "id": "feat-002",
                    "name": "Second Feature",
                    "description": "Second",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ["feat-001"],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=10,
            dry_run=True,  # Tests pass in dry run
            domain="test-domain",
            project="test-project",
        )
        harness = RalphLoopHarness(store, config, code_atlas_bridge=mock_atlas_bridge)

        result = await harness.run()

        assert result.success is True
        assert result.features_completed == 2

        # Should have indexed both features + session summary
        # Each feature indexes once on completion, plus session summary at end
        assert mock_atlas_bridge.index_session.call_count >= 2


class TestDecisionEngineRouting:
    """Tests for DecisionEngine routing in RalphLoopHarness."""

    @pytest.fixture
    def mock_decision_engine(self):
        """Create mock DecisionEngine."""

        engine = AsyncMock()
        engine.get_recommendation = AsyncMock()
        engine.record_outcome = AsyncMock()
        return engine

    @pytest.fixture
    def harness_with_engine(self, tmp_path, mock_decision_engine):
        """Create harness with mock DecisionEngine."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=10,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        return RalphLoopHarness(store, config, decision_engine=mock_decision_engine)

    def _make_recommendation(self, action, confidence, reasoning, decision_id):
        """Create a mock recommendation with all required attributes."""

        rec = MagicMock()
        rec.action = action
        rec.confidence = confidence
        rec.reasoning = reasoning
        rec.decision_id = decision_id
        return rec

    @pytest.mark.asyncio
    async def test_get_decision_proceed(self, harness_with_engine, mock_decision_engine):
        """Test _get_decision returns PROCEED recommendation."""
        from forge_harness.meta_learning.schemas import ConfidenceLevel, DecisionAction
        from forge_harness.ralph_loop import FeatureSpec

        recommendation = self._make_recommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            reasoning="Low risk feature",
            decision_id="dec-001",
        )
        mock_decision_engine.get_recommendation.return_value = recommendation

        feature = FeatureSpec(
            id="test-001",
            name="Test Feature",
            description="A simple test feature",
        )

        result = await harness_with_engine._get_decision(feature)

        assert result is not None
        assert result.action == DecisionAction.PROCEED
        assert result.confidence == ConfidenceLevel.HIGH
        mock_decision_engine.get_recommendation.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_decision_block(self, harness_with_engine, mock_decision_engine):
        """Test _get_decision returns BLOCK recommendation."""
        from forge_harness.meta_learning.schemas import ConfidenceLevel, DecisionAction
        from forge_harness.ralph_loop import FeatureSpec

        recommendation = self._make_recommendation(
            action=DecisionAction.BLOCK,
            confidence=ConfidenceLevel.HIGH,
            reasoning="High risk production operation",
            decision_id="dec-002",
        )
        mock_decision_engine.get_recommendation.return_value = recommendation

        feature = FeatureSpec(
            id="deploy-001",
            name="Production Deployment",
            description="Deploy to production",
        )

        result = await harness_with_engine._get_decision(feature)

        assert result is not None
        assert result.action == DecisionAction.BLOCK
        assert "production" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_get_decision_human_review(self, harness_with_engine, mock_decision_engine):
        """Test _get_decision returns HUMAN_REVIEW_REQUIRED recommendation."""
        from forge_harness.meta_learning.schemas import ConfidenceLevel, DecisionAction
        from forge_harness.ralph_loop import FeatureSpec

        recommendation = self._make_recommendation(
            action=DecisionAction.HUMAN_REVIEW_REQUIRED,
            confidence=ConfidenceLevel.MEDIUM,
            reasoning="Security-related changes need review",
            decision_id="dec-003",
        )
        mock_decision_engine.get_recommendation.return_value = recommendation

        feature = FeatureSpec(
            id="auth-001",
            name="Update Auth Logic",
            description="Modify authentication flow",
        )

        recommendation = await harness_with_engine._get_decision(feature)

        assert recommendation is not None
        assert recommendation.action == DecisionAction.HUMAN_REVIEW_REQUIRED

    @pytest.mark.asyncio
    async def test_get_decision_no_engine(self, tmp_path):
        """Test _get_decision returns None without engine."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=5, dry_run=True)
        harness = RalphLoopHarness(store, config)  # No engine

        feature = FeatureSpec(id="test-001", name="Test", description="Test")

        recommendation = await harness._get_decision(feature)

        assert recommendation is None

    @pytest.mark.asyncio
    async def test_get_decision_engine_error(self, harness_with_engine, mock_decision_engine):
        """Test _get_decision handles engine errors gracefully."""
        from forge_harness.ralph_loop import FeatureSpec

        mock_decision_engine.get_recommendation.side_effect = Exception("Engine error")

        feature = FeatureSpec(id="test-001", name="Test", description="Test")

        recommendation = await harness_with_engine._get_decision(feature)

        # Should return None on error, not raise
        assert recommendation is None

    @pytest.mark.asyncio
    async def test_record_outcome_success(self, harness_with_engine, mock_decision_engine):
        """Test _record_outcome records successful outcome."""
        from forge_harness.meta_learning.schemas import ConfidenceLevel, DecisionAction
        from forge_harness.ralph_loop import FeatureSpec

        # First get a decision to store the decision_id
        recommendation = self._make_recommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            reasoning="Low risk",
            decision_id="dec-001",
        )
        mock_decision_engine.get_recommendation.return_value = recommendation

        feature = FeatureSpec(id="test-001", name="Test", description="Test")

        # Get decision to populate _decision_ids
        await harness_with_engine._get_decision(feature)

        # Record outcome
        await harness_with_engine._record_outcome(feature, success=True)

        mock_decision_engine.record_outcome.assert_called_once_with(
            decision_id="dec-001",
            success=True,
        )

    @pytest.mark.asyncio
    async def test_record_outcome_failure(self, harness_with_engine, mock_decision_engine):
        """Test _record_outcome records failed outcome."""
        from forge_harness.meta_learning.schemas import ConfidenceLevel, DecisionAction
        from forge_harness.ralph_loop import FeatureSpec

        recommendation = self._make_recommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.MEDIUM,
            reasoning="Medium risk",
            decision_id="dec-002",
        )
        mock_decision_engine.get_recommendation.return_value = recommendation

        feature = FeatureSpec(id="test-002", name="Test", description="Test")

        await harness_with_engine._get_decision(feature)
        await harness_with_engine._record_outcome(feature, success=False)

        mock_decision_engine.record_outcome.assert_called_once_with(
            decision_id="dec-002",
            success=False,
        )

    @pytest.mark.asyncio
    async def test_record_outcome_no_decision_id(self, harness_with_engine, mock_decision_engine):
        """Test _record_outcome does nothing without prior decision."""
        from forge_harness.ralph_loop import FeatureSpec

        feature = FeatureSpec(id="test-003", name="Test", description="Test")

        # Record outcome without getting decision first
        await harness_with_engine._record_outcome(feature, success=True)

        # Should not call record_outcome since no decision_id stored
        mock_decision_engine.record_outcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_outcome_no_engine(self, tmp_path):
        """Test _record_outcome does nothing without engine."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=5, dry_run=True)
        harness = RalphLoopHarness(store, config)  # No engine

        feature = FeatureSpec(id="test-001", name="Test", description="Test")

        # Should not raise even without engine
        await harness._record_outcome(feature, success=True)


class TestAtlasContextQuery:
    """Tests for Code Atlas context query before implementation."""

    @pytest.fixture
    def mock_atlas_bridge(self):
        """Create mock CodeAtlasBridge."""
        bridge = AsyncMock()
        bridge.query_rag = AsyncMock()
        bridge.index_session = AsyncMock(return_value={"indexed": True})
        return bridge

    @pytest.fixture
    def harness_with_atlas(self, tmp_path, mock_atlas_bridge):
        """Create harness with mock Atlas bridge."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        return RalphLoopHarness(store, config, code_atlas_bridge=mock_atlas_bridge)

    @pytest.mark.asyncio
    async def test_query_atlas_high_confidence(self, harness_with_atlas, mock_atlas_bridge):
        """Test _query_atlas_for_context returns context with high confidence."""
        from forge_harness.meta_learning.schemas import AtlasRAGResponse
        from forge_harness.ralph_loop import FeatureSpec

        mock_atlas_bridge.query_rag.return_value = AtlasRAGResponse(
            answer="Use JWT with refresh tokens. See examples in auth_module.py.",
            confidence=0.85,
            sources=["auth_module.py", "token_service.py"],
        )

        feature = FeatureSpec(
            id="auth-001",
            name="Add JWT Authentication",
            description="Implement JWT auth",
        )

        context = await harness_with_atlas._query_atlas_for_context(feature)

        assert context is not None
        assert "Relevant Context from Past Sessions" in context
        assert "JWT" in context
        mock_atlas_bridge.query_rag.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_atlas_low_confidence(self, harness_with_atlas, mock_atlas_bridge):
        """Test _query_atlas_for_context returns None with low confidence."""
        from forge_harness.meta_learning.schemas import AtlasRAGResponse
        from forge_harness.ralph_loop import FeatureSpec

        mock_atlas_bridge.query_rag.return_value = AtlasRAGResponse(
            answer="No relevant patterns found.",
            confidence=0.3,  # Below 0.5 threshold
            sources=[],
        )

        feature = FeatureSpec(id="new-001", name="Brand New Feature", description="Novel")

        context = await harness_with_atlas._query_atlas_for_context(feature)

        assert context is None

    @pytest.mark.asyncio
    async def test_query_atlas_no_bridge(self, tmp_path):
        """Test _query_atlas_for_context returns None without bridge."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(max_iterations=5, dry_run=True)
        harness = RalphLoopHarness(store, config)  # No atlas bridge

        feature = FeatureSpec(id="test-001", name="Test", description="Test")

        context = await harness._query_atlas_for_context(feature)

        assert context is None

    @pytest.mark.asyncio
    async def test_query_atlas_error_handling(self, harness_with_atlas, mock_atlas_bridge):
        """Test _query_atlas_for_context handles errors gracefully."""
        from forge_harness.ralph_loop import FeatureSpec

        mock_atlas_bridge.query_rag.side_effect = Exception("Atlas connection failed")

        feature = FeatureSpec(id="test-001", name="Test", description="Test")

        context = await harness_with_atlas._query_atlas_for_context(feature)

        # Should return None on error, not raise
        assert context is None


class TestPatternRetrieval:
    """Tests for pattern retrieval from learning store in RalphLoopHarness."""

    @pytest.fixture
    def learning_store_with_patterns(self, tmp_path):
        """Create learning store with sample patterns."""
        from datetime import datetime

        from forge_harness.meta_learning.learning_store import LearningStore
        from forge_harness.meta_learning.schemas import PatternRecord

        learning_path = tmp_path / ".forge/learning"
        learning_path.mkdir()

        store = LearningStore(learning_path, auto_persist=True)

        # Add authentication patterns (need 35+ applications for 0.7 confidence)
        auth_pattern_1 = PatternRecord(
            pattern_id="auth-jwt-001",
            context_signature="auth_context_123",
            pattern_type="feature_implementation:authentication",
            total_applications=40,
            successful_applications=36,  # 90% effectiveness
            last_applied=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        store.record_pattern(auth_pattern_1)

        auth_pattern_2 = PatternRecord(
            pattern_id="auth-oauth-002",
            context_signature="auth_context_456",
            pattern_type="feature_implementation:authentication",
            total_applications=35,
            successful_applications=31,  # 89% effectiveness
            last_applied=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        store.record_pattern(auth_pattern_2)

        # Add API endpoint patterns
        api_pattern = PatternRecord(
            pattern_id="api-rest-001",
            context_signature="api_context_789",
            pattern_type="feature_implementation:api_endpoint:simple",
            total_applications=45,
            successful_applications=43,  # 96% effectiveness
            last_applied=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        store.record_pattern(api_pattern)

        # Add low confidence pattern (should be filtered out)
        low_conf_pattern = PatternRecord(
            pattern_id="low-conf-001",
            context_signature="low_context_999",
            pattern_type="feature_implementation:authentication",
            total_applications=2,  # Low applications = low confidence
            successful_applications=1,
            last_applied=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        store.record_pattern(low_conf_pattern)

        # Explicitly persist
        store.persist()
        return learning_path

    def test_extract_feature_type_authentication(self):
        """Test _extract_feature_type identifies authentication features."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        # Create minimal harness
        store = FeatureStore(Path("dummy.json"))
        config = LoopConfig()
        harness = RalphLoopHarness(store, config)

        # Test various authentication keywords
        test_cases = [
            ("auth-001", "Add JWT authentication", "Implement JWT token auth"),
            ("login-001", "User login feature", "Create login endpoint"),
            ("oauth-001", "OAuth integration", "Add OAuth2 support"),
        ]

        for feat_id, name, desc in test_cases:
            feature = FeatureSpec(id=feat_id, name=name, description=desc)
            feature_type = harness._extract_feature_type(feature)
            assert feature_type == "authentication", f"Failed for {name}"

    def test_extract_feature_type_api_endpoint(self):
        """Test _extract_feature_type identifies API endpoint features."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        store = FeatureStore(Path("dummy.json"))
        config = LoopConfig()
        harness = RalphLoopHarness(store, config)

        test_cases = [
            ("api-001", "REST API endpoints", "Create REST API"),
            ("crud-001", "CRUD operations", "Add CRUD for users"),
            ("graphql-001", "GraphQL API", "Implement GraphQL endpoint"),
        ]

        for feat_id, name, desc in test_cases:
            feature = FeatureSpec(id=feat_id, name=name, description=desc)
            feature_type = harness._extract_feature_type(feature)
            assert feature_type == "api_endpoint:simple", f"Failed for {name}"

    def test_extract_feature_type_testing(self):
        """Test _extract_feature_type identifies testing features."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        store = FeatureStore(Path("dummy.json"))
        config = LoopConfig()
        harness = RalphLoopHarness(store, config)

        test_cases = [
            ("test-001", "Unit tests", "Add unit tests for auth"),
            ("spec-001", "Integration tests", "Write integration test spec"),
        ]

        for feat_id, name, desc in test_cases:
            feature = FeatureSpec(id=feat_id, name=name, description=desc)
            feature_type = harness._extract_feature_type(feature)
            assert feature_type == "testing", f"Failed for {name}"

    def test_extract_feature_type_general(self):
        """Test _extract_feature_type returns general for unknown types."""
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        store = FeatureStore(Path("dummy.json"))
        config = LoopConfig()
        harness = RalphLoopHarness(store, config)

        feature = FeatureSpec(
            id="general-001",
            name="Generic feature",
            description="Some generic work",
        )
        feature_type = harness._extract_feature_type(feature)
        assert feature_type == "general"

    @pytest.mark.asyncio
    async def test_get_prior_patterns_auth_feature(self, tmp_path, learning_store_with_patterns):
        """Test _get_prior_patterns retrieves authentication patterns."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        # Verify learning store was created with patterns
        patterns_file = learning_store_with_patterns / "patterns.json"
        assert patterns_file.exists(), "Patterns file should exist"

        # Change working directory to tmp_path so .forge/learning is found
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            store = FeatureStore(path)
            config = LoopConfig(domain="test", project="test")
            harness = RalphLoopHarness(store, config)

            feature = FeatureSpec(
                id="auth-new",
                name="Add authentication",
                description="Implement user authentication",
            )

            patterns = await harness._get_prior_patterns(feature)

            # Should find authentication patterns (2 high confidence ones)
            assert len(patterns) > 0, f"Expected patterns but got {len(patterns)}"
            assert len(patterns) <= 5  # Max 5 patterns

            # All patterns should be authentication type
            for pattern in patterns:
                assert pattern.pattern_type == "feature_implementation:authentication"

            # Patterns should be sorted by effectiveness
            for i in range(len(patterns) - 1):
                assert patterns[i].effectiveness >= patterns[i + 1].effectiveness

        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_get_prior_patterns_api_feature(self, tmp_path, learning_store_with_patterns):
        """Test _get_prior_patterns retrieves API endpoint patterns."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            store = FeatureStore(path)
            config = LoopConfig(domain="test", project="test")
            harness = RalphLoopHarness(store, config)

            feature = FeatureSpec(
                id="api-new",
                name="Create REST API",
                description="Build REST API endpoints",
            )

            patterns = await harness._get_prior_patterns(feature)

            assert len(patterns) > 0
            for pattern in patterns:
                assert pattern.pattern_type == "feature_implementation:api_endpoint:simple"

        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_get_prior_patterns_no_store(self, tmp_path):
        """Test _get_prior_patterns handles missing learning store."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig()
        harness = RalphLoopHarness(store, config)

        feature = FeatureSpec(id="test", name="Test", description="Test")

        patterns = await harness._get_prior_patterns(feature)

        # Should return empty list without error
        assert patterns == []

    @pytest.mark.asyncio
    async def test_get_prior_patterns_filters_low_confidence(
        self, tmp_path, learning_store_with_patterns
    ):
        """Test _get_prior_patterns filters out low confidence patterns."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            store = FeatureStore(path)
            config = LoopConfig()
            harness = RalphLoopHarness(store, config)

            feature = FeatureSpec(
                id="auth-test",
                name="Authentication",
                description="Auth feature",
            )

            patterns = await harness._get_prior_patterns(feature)

            # Should not include the low confidence pattern (only 2 applications)
            pattern_ids = [p.pattern_id for p in patterns]
            assert "low-conf-001" not in pattern_ids

            # All returned patterns should have high confidence
            for pattern in patterns:
                assert pattern.confidence >= 0.7
                assert pattern.effectiveness >= 0.6

        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_implement_feature_enriches_with_patterns(
        self, tmp_path, learning_store_with_patterns
    ):
        """Test _implement_feature enriches feature with prior patterns."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            store = FeatureStore(path)
            config = LoopConfig(domain="test", project="test", dry_run=True)
            harness = RalphLoopHarness(store, config)

            feature = FeatureSpec(
                id="auth-003",
                name="Add JWT authentication",
                description="Implement JWT token-based authentication",
            )

            original_desc = feature.description

            await harness._implement_feature(feature)

            # Feature description should be enriched with pattern context
            assert feature.description != original_desc
            assert "Prior Patterns" in feature.description
            assert "effectiveness" in feature.description

        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_implement_feature_no_patterns_no_error(self, tmp_path):
        """Test _implement_feature continues without patterns."""
        from forge_harness.ralph_loop import (
            FeatureSpec,
            FeatureStore,
            LoopConfig,
            RalphLoopHarness,
        )

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(dry_run=True)
        harness = RalphLoopHarness(store, config)

        feature = FeatureSpec(
            id="test-001",
            name="Test feature",
            description="Test",
        )

        # Should not raise even without patterns
        success = await harness._implement_feature(feature)
        assert success is True


class TestFeedbackLoopTrigger:
    """Tests for session feedback loop triggering in RalphLoopHarness."""

    @pytest.fixture
    def mock_atlas_bridge(self):
        """Create mock CodeAtlasBridge."""
        bridge = AsyncMock()
        bridge.index_session = AsyncMock(return_value={"indexed": True, "message": "Success"})
        bridge.query_rag = AsyncMock()
        return bridge

    @pytest.fixture
    def mock_feedback_manager(self):
        """Create mock FeedbackLoopManager."""
        manager = AsyncMock()
        manager.on_session_complete = AsyncMock(
            return_value={
                "hooks": {
                    "post_session": {"indexed": True},
                    "optimization": {"optimized": True},
                    "debt_scan": {"features_generated": 3},
                }
            }
        )
        return manager

    @pytest.fixture
    def harness_with_feedback(self, tmp_path, mock_atlas_bridge, mock_feedback_manager):
        """Create harness with mock feedback components."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=10,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        return RalphLoopHarness(
            store,
            config,
            code_atlas_bridge=mock_atlas_bridge,
            feedback_loop_manager=mock_feedback_manager,
        )

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_with_manager(
        self, harness_with_feedback, mock_atlas_bridge, mock_feedback_manager
    ):
        """Test _trigger_feedback_loops calls FeedbackLoopManager."""
        from datetime import datetime

        harness_with_feedback._iteration = 15
        harness_with_feedback._start_time = datetime.now(UTC)

        stats = {"passing": 8, "blocked": 2, "failing": 1, "pending": 0}

        await harness_with_feedback._trigger_feedback_loops(stats, duration=1800.0)

        # Should have called both Atlas and feedback manager
        mock_atlas_bridge.index_session.assert_called_once()
        mock_feedback_manager.on_session_complete.assert_called_once()

        # Verify session summary passed to feedback manager
        call_args = mock_feedback_manager.on_session_complete.call_args
        session_summary = (
            call_args[0][0] if call_args[0] else call_args.kwargs.get("session_summary")
        )

        assert session_summary.features_completed == 8
        assert session_summary.features_blocked == 2
        assert session_summary.domain == "test-domain"
        assert session_summary.project == "test-project"

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_no_manager(self, tmp_path, mock_atlas_bridge):
        """Test _trigger_feedback_loops works without FeedbackLoopManager."""
        from datetime import datetime

        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=10,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        harness = RalphLoopHarness(
            store,
            config,
            code_atlas_bridge=mock_atlas_bridge,
            # No feedback_loop_manager
        )
        harness._iteration = 5
        harness._start_time = datetime.now(UTC)

        stats = {"passing": 3, "blocked": 1, "failing": 0, "pending": 0}

        # Should not raise even without manager
        await harness._trigger_feedback_loops(stats, duration=600.0)

        # Atlas should still be called
        mock_atlas_bridge.index_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_no_atlas_no_manager(self, tmp_path):
        """Test _trigger_feedback_loops with no Atlas and no FeedbackLoopManager."""
        from datetime import datetime

        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        harness = RalphLoopHarness(store, config)  # No bridges
        harness._iteration = 3
        harness._start_time = datetime.now(UTC)

        stats = {"passing": 2, "blocked": 0, "failing": 0, "pending": 1}

        # Should not raise
        await harness._trigger_feedback_loops(stats, duration=300.0)

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_atlas_error(self, tmp_path, mock_feedback_manager):
        """Test _trigger_feedback_loops handles Atlas errors gracefully."""
        from datetime import datetime

        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        mock_atlas = AsyncMock()
        mock_atlas.index_session = AsyncMock(side_effect=Exception("Atlas connection failed"))

        features = {"version": "1.0", "features": []}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=5,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        harness = RalphLoopHarness(
            store,
            config,
            code_atlas_bridge=mock_atlas,
            feedback_loop_manager=mock_feedback_manager,
        )
        harness._iteration = 5
        harness._start_time = datetime.now(UTC)

        stats = {"passing": 3, "blocked": 1, "failing": 0, "pending": 0}

        # Should not raise despite Atlas error
        await harness._trigger_feedback_loops(stats, duration=600.0)

        # Feedback manager should still be called even if Atlas fails
        mock_feedback_manager.on_session_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_manager_error(
        self, harness_with_feedback, mock_atlas_bridge, mock_feedback_manager
    ):
        """Test _trigger_feedback_loops handles FeedbackLoopManager errors."""
        from datetime import datetime

        mock_feedback_manager.on_session_complete.side_effect = Exception("Manager error")

        harness_with_feedback._iteration = 10
        harness_with_feedback._start_time = datetime.now(UTC)

        stats = {"passing": 5, "blocked": 0, "failing": 0, "pending": 0}

        # Should not raise despite manager error
        await harness_with_feedback._trigger_feedback_loops(stats, duration=1200.0)

        # Atlas should still have been called
        mock_atlas_bridge.index_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_feedback_loops_session_summary_structure(
        self, harness_with_feedback, mock_atlas_bridge, mock_feedback_manager
    ):
        """Test SessionSummary passed to FeedbackLoopManager has correct structure."""
        from datetime import datetime

        harness_with_feedback._iteration = 20
        harness_with_feedback._start_time = datetime.now(UTC)

        stats = {"passing": 10, "blocked": 3, "failing": 2, "pending": 0}

        await harness_with_feedback._trigger_feedback_loops(stats, duration=3600.0)

        # Verify the session summary dict passed to Atlas
        atlas_call = mock_atlas_bridge.index_session.call_args
        atlas_summary = atlas_call.kwargs.get("session_summary") or atlas_call[1].get(
            "session_summary"
        )

        assert "session_id" in atlas_summary
        assert atlas_summary["domain"] == "test-domain"
        assert atlas_summary["project"] == "test-project"
        assert atlas_summary["features_completed"] == 10
        assert atlas_summary["features_blocked"] == 3
        assert atlas_summary["features_failing"] == 2
        assert atlas_summary["total_iterations"] == 20
        assert atlas_summary["duration_seconds"] == 3600.0
        assert "type:session_summary" in atlas_summary["tags"]

    @pytest.mark.asyncio
    async def test_run_triggers_feedback_at_end(
        self, tmp_path, mock_atlas_bridge, mock_feedback_manager
    ):
        """Test that run() triggers feedback loops when completing."""
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "feat-001",
                    "name": "Test Feature",
                    "description": "Test",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": [],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        store = FeatureStore(path)
        config = LoopConfig(
            max_iterations=10,
            dry_run=True,
            domain="test-domain",
            project="test-project",
        )
        harness = RalphLoopHarness(
            store,
            config,
            code_atlas_bridge=mock_atlas_bridge,
            feedback_loop_manager=mock_feedback_manager,
        )

        result = await harness.run()

        assert result.success is True
        assert result.features_completed == 1

        # Feedback loops should have been triggered
        # Atlas gets called at least once (for feature and/or session)
        assert mock_atlas_bridge.index_session.call_count >= 1

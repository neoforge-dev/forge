"""Tests for LearningStore in meta_learning module."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.meta_learning.learning_store import (
    LearningStore,
    ThresholdAdjustment,
    ThresholdConfig,
)
from forge_harness.meta_learning.schemas import (
    DecisionAction,
    DecisionRecord,
    PatternRecord,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_storage():
    """Create a temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def learning_store(temp_storage):
    """Create a LearningStore instance with temp storage."""
    return LearningStore(
        storage_path=temp_storage,
        auto_persist=False,
        max_records=100,
    )


@pytest.fixture
def learning_store_with_persist(temp_storage):
    """Create a LearningStore with auto-persist enabled."""
    return LearningStore(
        storage_path=temp_storage,
        auto_persist=True,
        max_records=100,
    )


@pytest.fixture
def sample_pattern():
    """Create a sample PatternRecord."""
    return PatternRecord(
        pattern_id="pattern_001",
        context_signature="abc123",
        pattern_type="test_pattern",
        total_applications=5,
        successful_applications=4,
    )


@pytest.fixture
def sample_decision():
    """Create a sample DecisionRecord."""
    return DecisionRecord(
        decision_id="decision_001",
        context_signature="def456",
        domain="test-domain",
        project="test-project",
        recommended_action=DecisionAction.PROCEED,
    )


# =============================================================================
# Test: ThresholdConfig
# =============================================================================


class TestThresholdConfig:
    """Tests for ThresholdConfig dataclass."""

    def test_default_values(self):
        """Test ThresholdConfig has correct defaults."""
        config = ThresholdConfig()
        assert config.complexity_threshold == 0.5
        assert config.max_file_changes == 20
        assert config.max_lines_per_change == 500
        assert config.block_threshold == 0.3
        assert config.human_review_threshold == 0.5
        assert config.caution_threshold == 0.7
        assert config.min_confidence == 0.6

    def test_to_dict(self):
        """Test ThresholdConfig serialization."""
        config = ThresholdConfig(complexity_threshold=0.7)
        result = config.to_dict()
        assert isinstance(result, dict)
        assert result["complexity_threshold"] == 0.7

    def test_from_dict(self):
        """Test ThresholdConfig deserialization."""
        data = {"complexity_threshold": 0.8, "max_file_changes": 30}
        config = ThresholdConfig.from_dict(data)
        assert config.complexity_threshold == 0.8
        assert config.max_file_changes == 30

    def test_from_dict_ignores_extra_fields(self):
        """Test from_dict ignores unknown fields."""
        data = {"complexity_threshold": 0.8, "unknown_field": 999}
        config = ThresholdConfig.from_dict(data)
        assert config.complexity_threshold == 0.8
        assert not hasattr(config, "unknown_field")


# =============================================================================
# Test: ThresholdAdjustment
# =============================================================================


class TestThresholdAdjustment:
    """Tests for ThresholdAdjustment dataclass."""

    def test_to_dict(self):
        """Test ThresholdAdjustment serialization."""
        adj = ThresholdAdjustment(
            timestamp=datetime.now(UTC),
            domain="test-domain",
            project="test-project",
            success_rate=0.85,
            previous_thresholds={"complexity_threshold": 0.5},
            new_thresholds={"complexity_threshold": 0.6},
            reason="Test adjustment",
        )
        result = adj.to_dict()
        assert isinstance(result, dict)
        assert result["domain"] == "test-domain"
        assert result["success_rate"] == 0.85
        assert "timestamp" in result

    def test_from_dict(self):
        """Test ThresholdAdjustment deserialization."""
        data = {
            "timestamp": "2024-01-01T00:00:00+00:00",
            "domain": "test-domain",
            "project": "test-project",
            "success_rate": 0.9,
            "previous_thresholds": {"complexity_threshold": 0.5},
            "new_thresholds": {"complexity_threshold": 0.55},
            "reason": "Test",
        }
        adj = ThresholdAdjustment.from_dict(data)
        assert adj.domain == "test-domain"
        assert adj.success_rate == 0.9


# =============================================================================
# Test: Context Signature Generation
# =============================================================================


class TestComputeContextSignature:
    """Tests for compute_context_signature."""

    def test_same_inputs_same_signature(self):
        """Test identical inputs produce identical signatures."""
        sig1 = LearningStore.compute_context_signature(
            domain="test-domain",
            project="test-project",
            file_paths=["a.py", "b.py"],
            tags=["tag1", "tag2"],
        )
        sig2 = LearningStore.compute_context_signature(
            domain="test-domain",
            project="test-project",
            file_paths=["b.py", "a.py"],  # Different order
            tags=["tag2", "tag1"],  # Different order
        )
        assert sig1 == sig2

    def test_different_inputs_different_signatures(self):
        """Test different inputs produce different signatures."""
        sig1 = LearningStore.compute_context_signature(
            domain="test-domain",
            project="test-project",
            file_paths=["a.py"],
            tags=["tag1"],
        )
        sig2 = LearningStore.compute_context_signature(
            domain="other-domain",
            project="other-project",
            file_paths=["b.py"],
            tags=["tag2"],
        )
        assert sig1 != sig2

    def test_signature_length(self):
        """Test signature is 16 characters."""
        sig = LearningStore.compute_context_signature(
            domain="test",
            project="test",
            file_paths=[],
            tags=[],
        )
        assert len(sig) == 16

    def test_empty_inputs(self):
        """Test with empty inputs."""
        sig = LearningStore.compute_context_signature(
            domain="",
            project="",
            file_paths=[],
            tags=[],
        )
        assert len(sig) == 16


# =============================================================================
# Test: CRUD Operations - Patterns
# =============================================================================


class TestPatternCRUD:
    """Tests for pattern CRUD operations."""

    def test_record_pattern(self, learning_store, sample_pattern):
        """Test recording a new pattern."""
        learning_store.record_pattern(sample_pattern)
        assert learning_store.get_pattern("pattern_001") == sample_pattern

    def test_record_pattern_updates_existing(self, learning_store, sample_pattern):
        """Test recording updates existing pattern."""
        learning_store.record_pattern(sample_pattern)
        updated = PatternRecord(
            pattern_id="pattern_001",
            context_signature="abc123",
            pattern_type="test_pattern",
            total_applications=10,
            successful_applications=8,
        )
        learning_store.record_pattern(updated)
        result = learning_store.get_pattern("pattern_001")
        assert result.total_applications == 10

    def test_get_pattern_not_found(self, learning_store):
        """Test getting non-existent pattern returns None."""
        result = learning_store.get_pattern("nonexistent")
        assert result is None

    def test_get_patterns_by_context(self, learning_store):
        """Test querying patterns by context signature."""
        p1 = PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
        )
        p2 = PatternRecord(
            pattern_id="p2",
            context_signature="sig1",
            pattern_type="type2",
        )
        p3 = PatternRecord(
            pattern_id="p3",
            context_signature="sig2",
            pattern_type="type1",
        )
        learning_store.record_pattern(p1)
        learning_store.record_pattern(p2)
        learning_store.record_pattern(p3)

        results = learning_store.get_patterns_by_context("sig1")
        assert len(results) == 2

    def test_get_patterns_by_context_with_filters(self, learning_store):
        """Test context query with effectiveness/confidence filters."""
        p1 = PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
            total_applications=10,
            successful_applications=9,  # 0.9 effectiveness
        )
        p2 = PatternRecord(
            pattern_id="p2",
            context_signature="sig1",
            pattern_type="type2",
            total_applications=10,
            successful_applications=5,  # 0.5 effectiveness
        )
        learning_store.record_pattern(p1)
        learning_store.record_pattern(p2)

        results = learning_store.get_patterns_by_context("sig1", min_effectiveness=0.7)
        assert len(results) == 1
        assert results[0].pattern_id == "p1"

    def test_get_patterns_by_type(self, learning_store):
        """Test querying patterns by type."""
        p1 = PatternRecord(pattern_id="p1", context_signature="s1", pattern_type="type_a")
        p2 = PatternRecord(pattern_id="p2", context_signature="s2", pattern_type="type_b")
        p3 = PatternRecord(pattern_id="p3", context_signature="s3", pattern_type="type_a")
        learning_store.record_pattern(p1)
        learning_store.record_pattern(p2)
        learning_store.record_pattern(p3)

        results = learning_store.get_patterns_by_type("type_a")
        assert len(results) == 2


class TestPatternOutcome:
    """Tests for pattern outcome recording."""

    def test_update_pattern_outcome_success(self, learning_store, sample_pattern):
        """Test updating pattern with success outcome."""
        learning_store.record_pattern(sample_pattern)
        result = learning_store.update_pattern_outcome("pattern_001", success=True)
        assert result is not None
        assert result.total_applications == 6
        assert result.successful_applications == 5

    def test_update_pattern_outcome_failure(self, learning_store, sample_pattern):
        """Test updating pattern with failure outcome."""
        learning_store.record_pattern(sample_pattern)
        result = learning_store.update_pattern_outcome("pattern_001", success=False)
        assert result is not None
        assert result.total_applications == 6
        assert result.successful_applications == 4

    def test_update_pattern_outcome_not_found(self, learning_store):
        """Test updating non-existent pattern returns None."""
        result = learning_store.update_pattern_outcome("nonexistent", success=True)
        assert result is None


# =============================================================================
# Test: CRUD Operations - Decisions
# =============================================================================


class TestDecisionCRUD:
    """Tests for decision CRUD operations."""

    def test_record_decision(self, learning_store, sample_decision):
        """Test recording a new decision."""
        learning_store.record_decision(sample_decision)
        assert learning_store.get_decision("decision_001") == sample_decision

    def test_get_decision_not_found(self, learning_store):
        """Test getting non-existent decision returns None."""
        result = learning_store.get_decision("nonexistent")
        assert result is None

    def test_get_decisions_by_context(self, learning_store):
        """Test querying decisions by context signature."""
        d1 = DecisionRecord(
            decision_id="d1",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
        )
        d2 = DecisionRecord(
            decision_id="d2",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.BLOCK,
        )
        d3 = DecisionRecord(
            decision_id="d3",
            context_signature="sig2",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
        )
        learning_store.record_decision(d1)
        learning_store.record_decision(d2)
        learning_store.record_decision(d3)

        results = learning_store.get_decisions_by_context("sig1")
        assert len(results) == 2

    def test_get_decisions_by_context_with_outcome(self, learning_store):
        """Test filtering decisions by outcome."""
        d1 = DecisionRecord(
            decision_id="d1",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
            outcome_success=True,
        )
        d2 = DecisionRecord(
            decision_id="d2",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
        )
        learning_store.record_decision(d1)
        learning_store.record_decision(d2)

        results = learning_store.get_decisions_by_context("sig1", with_outcome_only=True)
        assert len(results) == 1

    def test_get_decisions_by_domain_project(self, learning_store):
        """Test querying decisions by domain/project."""
        d1 = DecisionRecord(
            decision_id="d1",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
        )
        d2 = DecisionRecord(
            decision_id="d2",
            context_signature="sig2",
            domain="domain1",
            project="proj2",
            recommended_action=DecisionAction.PROCEED,
        )
        learning_store.record_decision(d1)
        learning_store.record_decision(d2)

        results = learning_store.get_decisions_by_domain_project("domain1", "proj1")
        assert len(results) == 1


class TestDecisionOutcome:
    """Tests for decision outcome recording."""

    def test_record_outcome_success(self, learning_store, sample_decision):
        """Test recording successful outcome."""
        learning_store.record_decision(sample_decision)
        result = learning_store.record_outcome("decision_001", success=True)
        assert result is not None
        assert result.outcome_success is True
        assert result.outcome_recorded_at is not None

    def test_record_outcome_failure(self, learning_store, sample_decision):
        """Test recording failed outcome."""
        learning_store.record_decision(sample_decision)
        result = learning_store.record_outcome("decision_001", success=False)
        assert result is not None
        assert result.outcome_success is False

    def test_record_outcome_not_found(self, learning_store):
        """Test recording outcome for non-existent decision."""
        result = learning_store.record_outcome("nonexistent", success=True)
        assert result is None

    def test_record_outcome_with_actual_action(self, learning_store, sample_decision):
        """Test recording outcome with actual action."""
        learning_store.record_decision(sample_decision)
        result = learning_store.record_outcome(
            "decision_001",
            success=True,
            actual_action=DecisionAction.PROCEED_WITH_CAUTION,
        )
        assert result.actual_action == DecisionAction.PROCEED_WITH_CAUTION


# =============================================================================
# Test: Persistence
# =============================================================================


class TestPersistence:
    """Tests for data persistence."""

    def test_manual_persist(self, temp_storage):
        """Test manual persist writes files."""
        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        store.record_pattern(PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
        ))

        # Verify file doesn't exist before persist
        patterns_file = temp_storage / "patterns.json"
        assert not patterns_file.exists()

        # Persist manually
        store.persist()

        # Verify file exists after persist
        assert patterns_file.exists()

    def test_auto_persist(self, temp_storage):
        """Test auto-persist writes files immediately."""
        store = LearningStore(storage_path=temp_storage, auto_persist=True)
        store.record_pattern(PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
        ))

        patterns_file = temp_storage / "patterns.json"
        assert patterns_file.exists()

    def test_load_existing_data(self, temp_storage):
        """Test loading data from existing files."""
        # Create and persist data
        store1 = LearningStore(storage_path=temp_storage, auto_persist=True)
        store1.record_pattern(PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
        ))

        # Create new store with same path
        store2 = LearningStore(storage_path=temp_storage, auto_persist=False)
        assert store2.get_pattern("p1") is not None

    def test_persist_corrupted_json(self, temp_storage):
        """Test loading handles corrupted JSON gracefully."""
        # Write corrupted JSON
        patterns_file = temp_storage / "patterns.json"
        patterns_file.write_text("{ invalid json }")

        # Should not raise, just return empty
        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        assert len(store._patterns) == 0


# =============================================================================
# Test: Max Records / Trimming
# =============================================================================


class TestMaxRecords:
    """Tests for max records enforcement."""

    def test_pattern_trimming(self, temp_storage):
        """Test oldest patterns are trimmed when max exceeded."""
        store = LearningStore(storage_path=temp_storage, auto_persist=False, max_records=3)

        # Record 4 patterns
        for i in range(4):
            store.record_pattern(PatternRecord(
                pattern_id=f"p{i}",
                context_signature=f"sig{i}",
                pattern_type="type1",
            ))

        # Oldest should be removed
        assert store.get_pattern("p0") is None
        assert store.get_pattern("p1") is not None
        assert store.get_pattern("p3") is not None

    def test_decision_trimming(self, temp_storage):
        """Test oldest decisions are trimmed when max exceeded."""
        store = LearningStore(storage_path=temp_storage, auto_persist=False, max_records=3)

        # Record 4 decisions
        for i in range(4):
            store.record_decision(DecisionRecord(
                decision_id=f"d{i}",
                context_signature=f"sig{i}",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
            ))

        # Oldest should be removed
        assert store.get_decision("d0") is None
        assert store.get_decision("d1") is not None


# =============================================================================
# Test: Statistics
# =============================================================================


class TestStatistics:
    """Tests for statistics calculation."""

    def test_get_statistics_empty(self, learning_store):
        """Test statistics with no data."""
        stats = learning_store.get_statistics()
        assert stats["total_patterns"] == 0
        assert stats["total_decisions"] == 0
        assert stats["effective_patterns"] == 0
        assert stats["decision_success_rate"] == 0.0

    def test_get_statistics_with_data(self, learning_store):
        """Test statistics with data."""
        # Add patterns
        p1 = PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
            total_applications=10,
            successful_applications=8,
        )
        learning_store.record_pattern(p1)

        # Add decisions with outcomes
        d1 = DecisionRecord(
            decision_id="d1",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
            outcome_success=True,
        )
        learning_store.record_decision(d1)

        stats = learning_store.get_statistics()
        assert stats["total_patterns"] == 1
        assert stats["total_decisions"] == 1
        assert stats["completed_decisions"] == 1
        assert stats["successful_decisions"] == 1
        assert stats["decision_success_rate"] == 1.0


# =============================================================================
# Test: Threshold Management
# =============================================================================


class TestThresholdManagement:
    """Tests for threshold management."""

    def test_get_thresholds_default(self, learning_store):
        """Test getting default thresholds."""
        thresholds = learning_store.get_thresholds("domain1", "proj1")
        assert isinstance(thresholds, ThresholdConfig)
        assert thresholds.complexity_threshold == 0.5

    def test_set_thresholds(self, learning_store):
        """Test setting custom thresholds."""
        new_thresholds = ThresholdConfig(complexity_threshold=0.8)
        learning_store.set_thresholds("domain1", "proj1", new_thresholds)

        result = learning_store.get_thresholds("domain1", "proj1")
        assert result.complexity_threshold == 0.8

    def test_thresholds_persist_across_instances(self, temp_storage):
        """Test thresholds persist across store instances."""
        store1 = LearningStore(storage_path=temp_storage, auto_persist=True)
        store1.set_thresholds("domain1", "proj1", ThresholdConfig(complexity_threshold=0.9))

        store2 = LearningStore(storage_path=temp_storage, auto_persist=False)
        thresholds = store2.get_thresholds("domain1", "proj1")
        assert thresholds.complexity_threshold == 0.9


class TestSuccessRateCalculation:
    """Tests for success rate calculation."""

    def test_calculate_success_rate_insufficient_data(self, learning_store):
        """Test success rate returns None with insufficient data."""
        # Add one decision without outcome
        d1 = DecisionRecord(
            decision_id="d1",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
        )
        learning_store.record_decision(d1)

        # Should return None (need at least 5 decisions)
        result = learning_store.calculate_success_rate("domain1", "proj1")
        assert result is None

    def test_calculate_success_rate_with_outcomes(self, learning_store):
        """Test success rate calculation with outcomes."""
        # Add 5 decisions with outcomes
        for i in range(5):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
            learning_store.record_decision(d)

        result = learning_store.calculate_success_rate("domain1", "proj1")
        assert result == 1.0

    def test_calculate_success_rate_mixed(self, learning_store):
        """Test success rate with mixed outcomes."""
        for i in range(5):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=i < 3,  # 3 success, 2 failure
            )
            learning_store.record_decision(d)

        result = learning_store.calculate_success_rate("domain1", "proj1")
        assert result == 0.6  # 3/5


class TestThresholdAdjustment:
    """Tests for automatic threshold adjustment."""

    def test_adjust_thresholds_below_70(self, learning_store):
        """Test lowering thresholds when success rate < 70%."""
        # Add 5 failed decisions
        for i in range(5):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            learning_store.record_decision(d)

        result = learning_store.adjust_thresholds("domain1", "proj1")
        assert result is not None
        assert result.success_rate == 0.0
        assert "tightening" in result.reason.lower()

    def test_adjust_thresholds_above_95(self, learning_store):
        """Test raising thresholds when success rate > 95%."""
        # Add 20 successful decisions
        for i in range(20):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
            learning_store.record_decision(d)

        result = learning_store.adjust_thresholds("domain1", "proj1")
        assert result is not None
        assert result.success_rate == 1.0
        assert "relaxing" in result.reason.lower()

    def test_adjust_thresholds_no_change_in_range(self, learning_store):
        """Test no adjustment when success rate is in acceptable range."""
        # Add decisions with 80% success rate
        for i in range(5):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=i < 4,  # 4/5 = 80%
            )
            learning_store.record_decision(d)

        result = learning_store.adjust_thresholds("domain1", "proj1")
        assert result is None

    def test_adjust_thresholds_force(self, learning_store):
        """Test force adjustment with fewer than 5 decisions."""
        # Add 2 decisions
        for i in range(2):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            learning_store.record_decision(d)

        # Without force - should return None
        result = learning_store.adjust_thresholds("domain1", "proj1")
        assert result is None

        # With force - should adjust
        result = learning_store.adjust_thresholds("domain1", "proj1", force=True)
        assert result is not None


class TestThresholdHistory:
    """Tests for threshold history."""

    def test_get_threshold_history(self, learning_store):
        """Test getting threshold adjustment history."""
        # Add decisions and trigger adjustment
        for i in range(5):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            learning_store.record_decision(d)

        learning_store.adjust_thresholds("domain1", "proj1")

        history = learning_store.get_threshold_history()
        assert len(history) == 1

    def test_get_threshold_history_filtered(self, learning_store):
        """Test filtering threshold history."""
        # Add adjustments for different domains
        for i in range(5):
            d1 = DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            d2 = DecisionRecord(
                decision_id=f"d2_{i}",
                context_signature="sig2",
                domain="domain2",
                project="proj2",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            learning_store.record_decision(d1)
            learning_store.record_decision(d2)

        learning_store.adjust_thresholds("domain1", "proj1")
        learning_store.adjust_thresholds("domain2", "proj2")

        # Filter by domain
        history = learning_store.get_threshold_history(domain="domain1")
        assert len(history) == 1

        # Filter by project
        history = learning_store.get_threshold_history(project="proj2")
        assert len(history) == 1

    def test_get_threshold_history_limit(self, learning_store):
        """Test limiting threshold history."""
        # Add many adjustments
        for i in range(10):
            d = DecisionRecord(
                decision_id=f"d{i}",
                context_signature=f"sig{i}",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            learning_store.record_decision(d)
            learning_store.adjust_thresholds("domain1", "proj1", force=True)

        history = learning_store.get_threshold_history(limit=5)
        assert len(history) == 5


# =============================================================================
# Test: Clear
# =============================================================================


class TestClear:
    """Tests for clear operation."""

    def test_clear_all_data(self, learning_store):
        """Test clearing all stored data."""
        # Add data
        learning_store.record_pattern(PatternRecord(
            pattern_id="p1",
            context_signature="sig1",
            pattern_type="type1",
        ))
        learning_store.record_decision(DecisionRecord(
            decision_id="d1",
            context_signature="sig1",
            domain="domain1",
            project="proj1",
            recommended_action=DecisionAction.PROCEED,
        ))
        learning_store.set_thresholds("domain1", "proj1", ThresholdConfig(complexity_threshold=0.9))

        # Clear
        learning_store.clear()

        # Verify empty
        assert learning_store.get_pattern("p1") is None
        assert learning_store.get_decision("d1") is None
        assert learning_store.get_thresholds("domain1", "proj1").complexity_threshold == 0.5


# =============================================================================
# Test: Thread Safety
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_access(self, learning_store):
        """Test concurrent access to store."""
        import threading

        errors = []

        def write_patterns(start_id, count):
            try:
                for i in range(count):
                    learning_store.record_pattern(PatternRecord(
                        pattern_id=f"p{start_id}_{i}",
                        context_signature=f"sig{start_id}_{i}",
                        pattern_type="type1",
                    ))
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=write_patterns, args=(i, 10))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # All patterns should be recorded
        assert len(learning_store._patterns) == 50


# =============================================================================
# Test: Error Handling
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    def test_invalid_json_on_load(self, temp_storage):
        """Test handling invalid JSON during load."""
        patterns_file = temp_storage / "patterns.json"
        patterns_file.write_text('{"key": invalid}')

        # Should not raise
        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        assert len(store._patterns) == 0

    def test_missing_fields_on_load(self, temp_storage):
        """Test handling missing fields during load."""
        patterns_file = temp_storage / "patterns.json"
        # Missing required fields
        patterns_file.write_text('{"p1": {"pattern_id": "p1"}}')

        # Should not raise, just skip invalid
        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        assert len(store._patterns) == 0


# =============================================================================
# Test: From Config
# =============================================================================


class TestFromConfig:
    """Tests for from_config class method."""

    def test_from_config(self, temp_storage):
        """Test creating store from config."""
        from forge_harness.meta_learning.config import LearningStoreConfig

        config = LearningStoreConfig(
            storage_path=temp_storage,
            patterns_file="custom_patterns.json",
            decisions_file="custom_decisions.json",
            auto_persist=True,
            max_records=500,
        )

        store = LearningStore.from_config(config)

        assert store.storage_path == temp_storage
        assert store.patterns_file == "custom_patterns.json"
        assert store.decisions_file == "custom_decisions.json"
        assert store.auto_persist is True
        assert store.max_records == 500

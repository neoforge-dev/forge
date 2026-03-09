"""
Tests for the Meta-Learning LearningStore.

Verifies persistence, pattern/decision management, and analytics.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forge_harness.meta_learning.config import LearningStoreConfig
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


@pytest.fixture
def temp_storage():
    """Create a temporary directory for storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def store(temp_storage):
    """Create a LearningStore instance."""
    return LearningStore(
        storage_path=temp_storage,
        auto_persist=True,
    )


class TestLearningStoreBasics:
    """Basic LearningStore functionality tests."""

    def test_creation(self, temp_storage):
        """Test store creation."""
        store = LearningStore(storage_path=temp_storage)
        assert store.storage_path == temp_storage
        assert store.auto_persist is True

    def test_from_config(self, temp_storage):
        """Test creation from config."""
        config = LearningStoreConfig(
            storage_path=temp_storage,
            auto_persist=False,
            max_records=500,
        )
        store = LearningStore.from_config(config)
        assert store.auto_persist is False
        assert store.max_records == 500

    def test_context_signature_deterministic(self):
        """Test that context signatures are deterministic."""
        sig1 = LearningStore.compute_context_signature(
            domain="test",
            project="proj",
            file_paths=["a.py", "b.py"],
            tags=["x", "y"],
        )
        sig2 = LearningStore.compute_context_signature(
            domain="test",
            project="proj",
            file_paths=["b.py", "a.py"],  # Different order
            tags=["y", "x"],  # Different order
        )
        assert sig1 == sig2  # Should be same due to sorting

    def test_context_signature_different(self):
        """Test that different contexts produce different signatures."""
        sig1 = LearningStore.compute_context_signature(
            domain="test1",
            project="proj",
            file_paths=[],
            tags=[],
        )
        sig2 = LearningStore.compute_context_signature(
            domain="test2",
            project="proj",
            file_paths=[],
            tags=[],
        )
        assert sig1 != sig2


class TestPatternManagement:
    """Tests for pattern recording and retrieval."""

    def test_record_pattern(self, store):
        """Test recording a pattern."""
        pattern = PatternRecord(
            pattern_id="pat-001",
            context_signature="abc123",
            pattern_type="implementation",
            total_applications=5,
            successful_applications=4,
        )
        store.record_pattern(pattern)

        retrieved = store.get_pattern("pat-001")
        assert retrieved is not None
        assert retrieved.pattern_id == "pat-001"
        assert retrieved.effectiveness == 0.8

    def test_get_nonexistent_pattern(self, store):
        """Test getting a pattern that doesn't exist."""
        result = store.get_pattern("nonexistent")
        assert result is None

    def test_get_patterns_by_context(self, store):
        """Test filtering patterns by context."""
        ctx1 = "context-1"
        ctx2 = "context-2"

        store.record_pattern(
            PatternRecord(
                pattern_id="pat-1",
                context_signature=ctx1,
                pattern_type="test",
            )
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="pat-2",
                context_signature=ctx1,
                pattern_type="test",
            )
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="pat-3",
                context_signature=ctx2,
                pattern_type="test",
            )
        )

        patterns = store.get_patterns_by_context(ctx1)
        assert len(patterns) == 2
        assert all(p.context_signature == ctx1 for p in patterns)

    def test_get_patterns_with_effectiveness_filter(self, store):
        """Test filtering patterns by effectiveness threshold."""
        store.record_pattern(
            PatternRecord(
                pattern_id="good",
                context_signature="ctx",
                pattern_type="test",
                total_applications=10,
                successful_applications=9,  # 90% effective
            )
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="bad",
                context_signature="ctx",
                pattern_type="test",
                total_applications=10,
                successful_applications=3,  # 30% effective
            )
        )

        good_only = store.get_patterns_by_context(
            "ctx",
            min_effectiveness=0.5,
        )
        assert len(good_only) == 1
        assert good_only[0].pattern_id == "good"

    def test_get_patterns_by_type(self, store):
        """Test filtering patterns by type."""
        store.record_pattern(
            PatternRecord(
                pattern_id="p1",
                context_signature="ctx",
                pattern_type="bugfix",
            )
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="p2",
                context_signature="ctx",
                pattern_type="implementation",
            )
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="p3",
                context_signature="ctx",
                pattern_type="bugfix",
            )
        )

        bugfixes = store.get_patterns_by_type("bugfix")
        assert len(bugfixes) == 2

    def test_update_pattern_outcome(self, store):
        """Test updating pattern outcome."""
        store.record_pattern(
            PatternRecord(
                pattern_id="pat-1",
                context_signature="ctx",
                pattern_type="test",
                total_applications=10,
                successful_applications=8,
            )
        )

        # Update with success
        updated = store.update_pattern_outcome("pat-1", success=True)
        assert updated is not None
        assert updated.total_applications == 11
        assert updated.successful_applications == 9

        # Update with failure
        updated = store.update_pattern_outcome("pat-1", success=False)
        assert updated.total_applications == 12
        assert updated.successful_applications == 9  # Unchanged

    def test_update_nonexistent_pattern(self, store):
        """Test updating a pattern that doesn't exist."""
        result = store.update_pattern_outcome("nonexistent", success=True)
        assert result is None


class TestDecisionManagement:
    """Tests for decision recording and retrieval."""

    def test_record_decision(self, store):
        """Test recording a decision."""
        decision = DecisionRecord(
            decision_id="dec-001",
            context_signature="ctx123",
            domain="test-domain",
            project="test-project",
            recommended_action=DecisionAction.PROCEED,
        )
        store.record_decision(decision)

        retrieved = store.get_decision("dec-001")
        assert retrieved is not None
        assert retrieved.domain == "test-domain"
        assert retrieved.recommended_action == DecisionAction.PROCEED

    def test_get_nonexistent_decision(self, store):
        """Test getting a decision that doesn't exist."""
        result = store.get_decision("nonexistent")
        assert result is None

    def test_get_decisions_by_context(self, store):
        """Test filtering decisions by context."""
        store.record_decision(
            DecisionRecord(
                decision_id="d1",
                context_signature="ctx-a",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="d2",
                context_signature="ctx-a",
                domain="d",
                project="p",
                recommended_action=DecisionAction.BLOCK,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="d3",
                context_signature="ctx-b",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        decisions = store.get_decisions_by_context("ctx-a")
        assert len(decisions) == 2

    def test_get_decisions_with_outcome_only(self, store):
        """Test filtering decisions by outcome presence."""
        store.record_decision(
            DecisionRecord(
                decision_id="with-outcome",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="no-outcome",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        with_outcome = store.get_decisions_by_context(
            "ctx",
            with_outcome_only=True,
        )
        assert len(with_outcome) == 1
        assert with_outcome[0].decision_id == "with-outcome"

    def test_get_decisions_by_domain_project(self, store):
        """Test filtering decisions by domain/project."""
        store.record_decision(
            DecisionRecord(
                decision_id="d1",
                context_signature="ctx",
                domain="domain-a",
                project="project-1",
                recommended_action=DecisionAction.PROCEED,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="d2",
                context_signature="ctx",
                domain="domain-a",
                project="project-1",
                recommended_action=DecisionAction.PROCEED,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="d3",
                context_signature="ctx",
                domain="domain-b",
                project="project-1",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        decisions = store.get_decisions_by_domain_project(
            "domain-a",
            "project-1",
        )
        assert len(decisions) == 2

    def test_record_outcome(self, store):
        """Test recording a decision outcome."""
        store.record_decision(
            DecisionRecord(
                decision_id="dec-1",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        updated = store.record_outcome(
            "dec-1",
            success=True,
            actual_action=DecisionAction.PROCEED,
        )
        assert updated is not None
        assert updated.outcome_success is True
        assert updated.actual_action == DecisionAction.PROCEED
        assert updated.outcome_recorded_at is not None

    def test_record_outcome_nonexistent(self, store):
        """Test recording outcome for nonexistent decision."""
        result = store.record_outcome("nonexistent", success=True)
        assert result is None


class TestPersistence:
    """Tests for data persistence."""

    def test_auto_persist(self, temp_storage):
        """Test that auto_persist saves data."""
        store = LearningStore(
            storage_path=temp_storage,
            auto_persist=True,
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="p1",
                context_signature="ctx",
                pattern_type="test",
            )
        )

        # Check file exists
        patterns_file = temp_storage / "patterns.json"
        assert patterns_file.exists()

        # Load and verify
        with open(patterns_file) as f:
            data = json.load(f)
        assert "p1" in data

    def test_manual_persist(self, temp_storage):
        """Test manual persistence."""
        store = LearningStore(
            storage_path=temp_storage,
            auto_persist=False,
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="p1",
                context_signature="ctx",
                pattern_type="test",
            )
        )

        # File should not exist yet (or be empty)
        patterns_file = temp_storage / "patterns.json"
        # After initial load, file may be created
        # But our pattern shouldn't be there
        if patterns_file.exists():
            with open(patterns_file) as f:
                data = json.load(f)
            assert "p1" not in data

        # Now persist manually
        store.persist()

        with open(patterns_file) as f:
            data = json.load(f)
        assert "p1" in data

    def test_load_on_init(self, temp_storage):
        """Test that data is loaded on initialization."""
        # Create first store and add data
        store1 = LearningStore(storage_path=temp_storage)
        store1.record_pattern(
            PatternRecord(
                pattern_id="persistent",
                context_signature="ctx",
                pattern_type="test",
            )
        )
        store1.record_decision(
            DecisionRecord(
                decision_id="dec-persistent",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        # Create new store instance (simulating restart)
        store2 = LearningStore(storage_path=temp_storage)

        # Data should be loaded
        assert store2.get_pattern("persistent") is not None
        assert store2.get_decision("dec-persistent") is not None


class TestMaxRecords:
    """Tests for max records enforcement."""

    def test_pattern_trimming(self, temp_storage):
        """Test that old patterns are removed when exceeding max."""
        store = LearningStore(
            storage_path=temp_storage,
            max_records=5,
        )

        # Add 7 patterns
        for i in range(7):
            store.record_pattern(
                PatternRecord(
                    pattern_id=f"pat-{i}",
                    context_signature="ctx",
                    pattern_type="test",
                    last_applied=datetime(2024, 1, i + 1, tzinfo=UTC),
                )
            )

        # Should only have 5 (oldest removed)
        stats = store.get_statistics()
        assert stats["total_patterns"] == 5

        # Oldest (pat-0, pat-1) should be gone
        assert store.get_pattern("pat-0") is None
        assert store.get_pattern("pat-1") is None
        # Newest should remain
        assert store.get_pattern("pat-6") is not None

    def test_decision_trimming(self, temp_storage):
        """Test that old decisions are removed when exceeding max."""
        store = LearningStore(
            storage_path=temp_storage,
            max_records=5,
        )

        # Add 7 decisions
        for i in range(7):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-{i}",
                    context_signature="ctx",
                    domain="d",
                    project="p",
                    recommended_action=DecisionAction.PROCEED,
                    created_at=datetime(2024, 1, i + 1, tzinfo=UTC),
                )
            )

        stats = store.get_statistics()
        assert stats["total_decisions"] == 5


class TestStatistics:
    """Tests for statistics generation."""

    def test_empty_statistics(self, store):
        """Test statistics on empty store."""
        stats = store.get_statistics()
        assert stats["total_patterns"] == 0
        assert stats["total_decisions"] == 0
        assert stats["decision_success_rate"] == 0.0

    def test_pattern_statistics(self, store):
        """Test pattern statistics."""
        # Add effective pattern
        store.record_pattern(
            PatternRecord(
                pattern_id="effective",
                context_signature="ctx",
                pattern_type="test",
                total_applications=100,
                successful_applications=90,
            )
        )
        # Add ineffective pattern
        store.record_pattern(
            PatternRecord(
                pattern_id="ineffective",
                context_signature="ctx",
                pattern_type="test",
                total_applications=100,
                successful_applications=30,
            )
        )

        stats = store.get_statistics()
        assert stats["total_patterns"] == 2
        assert stats["effective_patterns"] == 1

    def test_decision_statistics(self, store):
        """Test decision statistics."""
        # Add decisions with outcomes
        store.record_decision(
            DecisionRecord(
                decision_id="success-1",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="success-2",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="failure",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
        )
        # Decision without outcome
        store.record_decision(
            DecisionRecord(
                decision_id="pending",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        stats = store.get_statistics()
        assert stats["total_decisions"] == 4
        assert stats["completed_decisions"] == 3
        assert stats["successful_decisions"] == 2
        assert abs(stats["decision_success_rate"] - 2 / 3) < 0.01

    def test_clear(self, store):
        """Test clearing the store."""
        store.record_pattern(
            PatternRecord(
                pattern_id="p1",
                context_signature="ctx",
                pattern_type="test",
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="d1",
                context_signature="ctx",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )
        )

        store.clear()

        stats = store.get_statistics()
        assert stats["total_patterns"] == 0
        assert stats["total_decisions"] == 0


class TestCorruptFileHandling:
    """Tests for handling corrupt JSON files."""

    def test_corrupt_patterns_file(self, temp_storage):
        """Test that corrupt patterns file is handled gracefully."""
        # Create a corrupt patterns file
        patterns_file = temp_storage / "patterns.json"
        patterns_file.write_text("{ invalid json }")

        # Store should initialize with empty patterns
        store = LearningStore(storage_path=temp_storage)
        assert store.get_statistics()["total_patterns"] == 0

    def test_corrupt_decisions_file(self, temp_storage):
        """Test that corrupt decisions file is handled gracefully."""
        # Create a corrupt decisions file
        decisions_file = temp_storage / "decisions.json"
        decisions_file.write_text("not json at all")

        # Store should initialize with empty decisions
        store = LearningStore(storage_path=temp_storage)
        assert store.get_statistics()["total_decisions"] == 0

    def test_invalid_pattern_schema(self, temp_storage):
        """Test handling of patterns file with invalid schema."""
        # Create patterns file with wrong structure
        patterns_file = temp_storage / "patterns.json"
        patterns_file.write_text(
            json.dumps(
                {
                    "bad-pattern": {
                        "not_a_valid_field": "value",
                    }
                }
            )
        )

        # Store should initialize with empty patterns (validation error)
        store = LearningStore(storage_path=temp_storage)
        assert store.get_statistics()["total_patterns"] == 0

    def test_invalid_decision_schema(self, temp_storage):
        """Test handling of decisions file with invalid schema."""
        # Create decisions file with wrong structure
        decisions_file = temp_storage / "decisions.json"
        decisions_file.write_text(
            json.dumps(
                {
                    "bad-decision": {
                        "missing_required_fields": True,
                    }
                }
            )
        )

        # Store should initialize with empty decisions (validation error)
        store = LearningStore(storage_path=temp_storage)
        assert store.get_statistics()["total_decisions"] == 0


class TestPersistSerialization:
    """Tests for persist serialization edge cases."""

    def test_persist_with_datetime(self, temp_storage):
        """Test that datetimes are serialized correctly."""
        store = LearningStore(storage_path=temp_storage, auto_persist=False)

        # Add pattern with explicit datetime
        store.record_pattern(
            PatternRecord(
                pattern_id="p1",
                context_signature="ctx",
                pattern_type="test",
                last_applied=datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC),
                created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            )
        )

        # Persist
        store.persist()

        # Reload and verify
        store2 = LearningStore(storage_path=temp_storage)
        pattern = store2.get_pattern("p1")
        assert pattern is not None
        assert pattern.last_applied.year == 2024
        assert pattern.last_applied.month == 6


class TestThresholdConfig:
    """Tests for ThresholdConfig dataclass."""

    def test_default_values(self):
        """Test default threshold values."""
        config = ThresholdConfig()
        assert config.complexity_threshold == 0.5
        assert config.max_file_changes == 20
        assert config.max_lines_per_change == 500
        assert config.block_threshold == 0.3
        assert config.human_review_threshold == 0.5
        assert config.caution_threshold == 0.7
        assert config.min_confidence == 0.6

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = ThresholdConfig(complexity_threshold=0.6)
        d = config.to_dict()
        assert d["complexity_threshold"] == 0.6
        assert "max_file_changes" in d

    def test_from_dict(self):
        """Test creation from dictionary."""
        d = {"complexity_threshold": 0.7, "max_file_changes": 30}
        config = ThresholdConfig.from_dict(d)
        assert config.complexity_threshold == 0.7
        assert config.max_file_changes == 30
        # Defaults should be applied for missing keys
        assert config.block_threshold == 0.3

    def test_from_dict_ignores_extra_keys(self):
        """Test that extra keys in dict are ignored."""
        d = {"complexity_threshold": 0.8, "unknown_key": "ignored"}
        config = ThresholdConfig.from_dict(d)
        assert config.complexity_threshold == 0.8


class TestThresholdAdjustment:
    """Tests for ThresholdAdjustment dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        adj = ThresholdAdjustment(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            domain="test-domain",
            project="test-project",
            success_rate=0.65,
            previous_thresholds={"a": 1},
            new_thresholds={"a": 2},
            reason="test reason",
        )
        d = adj.to_dict()
        assert d["domain"] == "test-domain"
        assert d["success_rate"] == 0.65
        assert d["timestamp"] == "2024-01-01T00:00:00+00:00"

    def test_from_dict(self):
        """Test creation from dictionary."""
        d = {
            "timestamp": "2024-01-01T00:00:00+00:00",
            "domain": "d",
            "project": "p",
            "success_rate": 0.8,
            "previous_thresholds": {},
            "new_thresholds": {},
            "reason": "test",
        }
        adj = ThresholdAdjustment.from_dict(d)
        assert adj.domain == "d"
        assert adj.project == "p"
        assert adj.success_rate == 0.8


class TestThresholdManagement:
    """Tests for threshold management in LearningStore (loop-003)."""

    def test_get_default_thresholds(self, store):
        """Test getting thresholds when none are set."""
        thresholds = store.get_thresholds("domain", "project")
        assert thresholds.complexity_threshold == 0.5
        assert thresholds.max_file_changes == 20

    def test_set_and_get_thresholds(self, store):
        """Test setting and getting thresholds."""
        custom = ThresholdConfig(
            complexity_threshold=0.8,
            max_file_changes=50,
        )
        store.set_thresholds("test-domain", "test-project", custom)

        retrieved = store.get_thresholds("test-domain", "test-project")
        assert retrieved.complexity_threshold == 0.8
        assert retrieved.max_file_changes == 50

    def test_threshold_persistence(self, temp_storage):
        """Test that thresholds persist across store instances."""
        # Set thresholds
        store1 = LearningStore(storage_path=temp_storage)
        custom = ThresholdConfig(complexity_threshold=0.9)
        store1.set_thresholds("domain", "project", custom)

        # Create new store instance
        store2 = LearningStore(storage_path=temp_storage)
        retrieved = store2.get_thresholds("domain", "project")
        assert retrieved.complexity_threshold == 0.9

    def test_calculate_success_rate_insufficient_data(self, store):
        """Test success rate returns None when not enough decisions."""
        rate = store.calculate_success_rate("domain", "project")
        assert rate is None

    def test_calculate_success_rate(self, store):
        """Test success rate calculation."""
        # Add 10 decisions: 7 successful, 3 failed
        for i in range(7):
            dec = DecisionRecord(
                decision_id=f"dec-success-{i}",
                context_signature="ctx",
                domain="test",
                project="proj",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
            store.record_decision(dec)

        for i in range(3):
            dec = DecisionRecord(
                decision_id=f"dec-fail-{i}",
                context_signature="ctx",
                domain="test",
                project="proj",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
            store.record_decision(dec)

        rate = store.calculate_success_rate("test", "proj")
        assert rate == 0.7  # 7/10

    def test_adjust_thresholds_insufficient_data(self, store):
        """Test adjustment returns None without enough decisions."""
        result = store.adjust_thresholds("domain", "project")
        assert result is None

    def test_adjust_thresholds_low_success_rate(self, store):
        """Test threshold tightening when success rate < 70%."""
        # Add 10 decisions: 5 successful, 5 failed (50%)
        for i in range(5):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-s-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=True,
                )
            )
        for i in range(5):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-f-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=False,
                )
            )

        result = store.adjust_thresholds("test", "proj")
        assert result is not None
        assert "below 70%" in result.reason
        assert result.success_rate == 0.5

        # Verify thresholds were tightened
        new_thresholds = store.get_thresholds("test", "proj")
        assert new_thresholds.complexity_threshold < 0.5  # Was 0.5, should be lower

    def test_adjust_thresholds_high_success_rate(self, store):
        """Test threshold relaxation when success rate > 95%."""
        # Add 21 decisions: 20 successful, 1 failed (20/21 = 95.2%)
        for i in range(20):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-s-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=True,
                )
            )
        store.record_decision(
            DecisionRecord(
                decision_id="dec-f-0",
                context_signature="ctx",
                domain="test",
                project="proj",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            )
        )

        result = store.adjust_thresholds("test", "proj")
        assert result is not None
        assert "above 95%" in result.reason

        # Verify thresholds were relaxed
        new_thresholds = store.get_thresholds("test", "proj")
        assert new_thresholds.complexity_threshold > 0.5  # Was 0.5, should be higher

    def test_adjust_thresholds_acceptable_range(self, store):
        """Test no adjustment when success rate is in acceptable range."""
        # Add 10 decisions: 8 successful (80%)
        for i in range(8):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-s-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=True,
                )
            )
        for i in range(2):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-f-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=False,
                )
            )

        result = store.adjust_thresholds("test", "proj")
        assert result is None  # No adjustment needed

    def test_get_threshold_history(self, store):
        """Test retrieving threshold adjustment history."""
        # Force some adjustments
        for i in range(5):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=False,
                )
            )

        store.adjust_thresholds("test", "proj")

        history = store.get_threshold_history()
        assert len(history) == 1
        assert history[0].domain == "test"
        assert history[0].project == "proj"

    def test_get_threshold_history_filtered(self, store):
        """Test filtering threshold history by domain/project."""
        # Create adjustments for multiple projects
        for proj in ["proj1", "proj2"]:
            for i in range(5):
                store.record_decision(
                    DecisionRecord(
                        decision_id=f"dec-{proj}-{i}",
                        context_signature="ctx",
                        domain="test",
                        project=proj,
                        recommended_action=DecisionAction.PROCEED,
                        outcome_success=False,
                    )
                )
            store.adjust_thresholds("test", proj)

        # Filter by project
        history = store.get_threshold_history(project="proj1")
        assert len(history) == 1
        assert history[0].project == "proj1"

    def test_threshold_history_persistence(self, temp_storage):
        """Test that threshold history persists across store instances."""
        store1 = LearningStore(storage_path=temp_storage)

        # Create an adjustment
        for i in range(5):
            store1.record_decision(
                DecisionRecord(
                    decision_id=f"dec-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=False,
                )
            )
        store1.adjust_thresholds("test", "proj")

        # Create new store instance
        store2 = LearningStore(storage_path=temp_storage)
        history = store2.get_threshold_history()
        assert len(history) == 1

    def test_clear_includes_thresholds(self, store):
        """Test that clear() also clears thresholds and history."""
        # Set some thresholds
        store.set_thresholds("test", "proj", ThresholdConfig(complexity_threshold=0.9))

        # Add decisions and create adjustment
        for i in range(5):
            store.record_decision(
                DecisionRecord(
                    decision_id=f"dec-{i}",
                    context_signature="ctx",
                    domain="test",
                    project="proj",
                    recommended_action=DecisionAction.PROCEED,
                    outcome_success=False,
                )
            )
        store.adjust_thresholds("test", "proj")

        # Verify data exists
        assert store.get_thresholds("test", "proj").complexity_threshold != 0.5
        assert len(store.get_threshold_history()) > 0

        # Clear
        store.clear()

        # Verify all cleared
        assert store.get_thresholds("test", "proj").complexity_threshold == 0.5
        assert len(store.get_threshold_history()) == 0

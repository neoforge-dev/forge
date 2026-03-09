"""Tests for iteration tracker."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from forge_harness.iteration.tracker import (
    IterationRecord,
    IterationTracker,
    create_tracker,
)


@pytest.fixture
def temp_storage(tmp_path):
    """Create temporary storage path."""
    return tmp_path / "iteration_history.json"


@pytest.fixture
def tracker(temp_storage):
    """Create tracker with temporary storage."""
    return IterationTracker(storage_path=temp_storage)


class TestIterationRecord:
    """Test IterationRecord dataclass."""

    def test_to_dict(self):
        """Test serialization to dict."""
        started = datetime.now()
        completed = started + timedelta(seconds=30)

        record = IterationRecord(
            iteration_number=1,
            started_at=started,
            completed_at=completed,
            features_completed=["feature1", "feature2"],
            tests_added=5,
            lines_added=100,
            commit_hash="abc123",
            duration_seconds=30.0,
            status="completed",
            notes="All good",
        )

        data = record.to_dict()

        assert data["iteration_number"] == 1
        assert data["started_at"] == started.isoformat()
        assert data["completed_at"] == completed.isoformat()
        assert data["features_completed"] == ["feature1", "feature2"]
        assert data["tests_added"] == 5
        assert data["lines_added"] == 100
        assert data["commit_hash"] == "abc123"
        assert data["duration_seconds"] == 30.0
        assert data["status"] == "completed"
        assert data["notes"] == "All good"

    def test_from_dict(self):
        """Test deserialization from dict."""
        started = datetime.now()
        completed = started + timedelta(seconds=30)

        data = {
            "iteration_number": 1,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "features_completed": ["feature1"],
            "tests_added": 3,
            "lines_added": 50,
            "commit_hash": "def456",
            "duration_seconds": 30.0,
            "status": "completed",
            "notes": "Done",
        }

        record = IterationRecord.from_dict(data)

        assert record.iteration_number == 1
        assert record.started_at == started
        assert record.completed_at == completed
        assert record.features_completed == ["feature1"]
        assert record.tests_added == 3
        assert record.lines_added == 50
        assert record.commit_hash == "def456"
        assert record.duration_seconds == 30.0
        assert record.status == "completed"
        assert record.notes == "Done"

    def test_from_dict_minimal(self):
        """Test deserialization with minimal data."""
        started = datetime.now()

        data = {
            "iteration_number": 1,
            "started_at": started.isoformat(),
        }

        record = IterationRecord.from_dict(data)

        assert record.iteration_number == 1
        assert record.started_at == started
        assert record.completed_at is None
        assert record.features_completed == []
        assert record.tests_added == 0
        assert record.lines_added == 0
        assert record.commit_hash is None
        assert record.duration_seconds == 0.0
        assert record.status == "in_progress"
        assert record.notes == ""


class TestIterationTracker:
    """Test IterationTracker class."""

    def test_init_creates_storage_dir(self, temp_storage):
        """Test that init creates storage directory."""
        tracker = IterationTracker(storage_path=temp_storage)

        assert tracker.storage_path == temp_storage
        assert tracker._history == []
        assert tracker._current is None

    def test_start_iteration(self, tracker):
        """Test starting an iteration."""
        record = tracker.start_iteration(1)

        assert record.iteration_number == 1
        assert record.status == "in_progress"
        assert tracker.get_current() == record

    def test_start_iteration_auto_fails_previous(self, tracker):
        """Test that starting new iteration fails previous in-progress iteration."""
        first = tracker.start_iteration(1)
        second = tracker.start_iteration(2)

        assert second.iteration_number == 2
        assert tracker.get_current() == second

        # First iteration should be in history with failed status
        assert len(tracker._history) == 1
        assert tracker._history[0].iteration_number == 1
        assert tracker._history[0].status == "failed"

    def test_complete_iteration(self, tracker):
        """Test completing an iteration."""
        tracker.start_iteration(1)

        record = tracker.complete_iteration(
            features=["feature1", "feature2"],
            tests_added=5,
            lines_added=100,
            commit_hash="abc123",
            notes="Success",
        )

        assert record.status == "completed"
        assert record.features_completed == ["feature1", "feature2"]
        assert record.tests_added == 5
        assert record.lines_added == 100
        assert record.commit_hash == "abc123"
        assert record.notes == "Success"
        assert record.duration_seconds >= 0.0  # Just verify it's calculated
        assert tracker.get_current() is None
        assert len(tracker._history) == 1

    def test_complete_iteration_without_current(self, tracker):
        """Test completing iteration when none in progress."""
        with pytest.raises(RuntimeError, match="No iteration in progress"):
            tracker.complete_iteration(features=[])

    def test_fail_iteration(self, tracker):
        """Test failing an iteration."""
        tracker.start_iteration(1)

        record = tracker.fail_iteration(reason="Tests failed")

        assert record.status == "failed"
        assert record.notes == "Tests failed"
        assert record.duration_seconds >= 0.0  # Just verify it's calculated
        assert tracker.get_current() is None
        assert len(tracker._history) == 1

    def test_fail_iteration_without_current(self, tracker):
        """Test failing iteration when none in progress."""
        with pytest.raises(RuntimeError, match="No iteration in progress"):
            tracker.fail_iteration()

    def test_get_stats_empty(self, tracker):
        """Test getting stats with no iterations."""
        stats = tracker.get_stats()

        assert stats.total_iterations == 0
        assert stats.completed_iterations == 0
        assert stats.failed_iterations == 0
        assert stats.total_features == 0
        assert stats.total_tests == 0
        assert stats.total_lines == 0
        assert stats.avg_duration == 0.0

    def test_get_stats_with_iterations(self, tracker):
        """Test getting stats with multiple iterations."""
        # Completed iteration 1
        tracker.start_iteration(1)
        tracker._current.started_at = datetime(2024, 1, 1, 12, 0, 0)
        tracker.complete_iteration(
            features=["f1", "f2"],
            tests_added=3,
            lines_added=50,
        )

        # Completed iteration 2
        tracker.start_iteration(2)
        tracker._current.started_at = datetime(2024, 1, 1, 12, 0, 0)
        tracker.complete_iteration(
            features=["f3"],
            tests_added=2,
            lines_added=30,
        )

        # Failed iteration 3
        tracker.start_iteration(3)
        tracker._current.started_at = datetime(2024, 1, 1, 12, 0, 0)
        tracker.fail_iteration()

        stats = tracker.get_stats()

        assert stats.total_iterations == 3
        assert stats.completed_iterations == 2
        assert stats.failed_iterations == 1
        assert stats.total_features == 3
        assert stats.total_tests == 5
        assert stats.total_lines == 80

    def test_get_recent(self, tracker):
        """Test getting recent iterations."""
        # Add 5 iterations
        for i in range(1, 6):
            tracker.start_iteration(i)
            tracker._current.started_at = datetime.now()
            tracker.complete_iteration(features=[f"f{i}"])

        recent = tracker.get_recent(count=3)

        assert len(recent) == 3
        assert recent[0].iteration_number == 3
        assert recent[1].iteration_number == 4
        assert recent[2].iteration_number == 5

    def test_get_recent_more_than_available(self, tracker):
        """Test getting recent when requesting more than available."""
        tracker.start_iteration(1)
        tracker._current.started_at = datetime.now()
        tracker.complete_iteration(features=["f1"])

        recent = tracker.get_recent(count=10)

        assert len(recent) == 1

    def test_history_persistence(self, tracker, temp_storage):
        """Test that history is saved and loaded correctly."""
        # Add an iteration
        tracker.start_iteration(1)
        tracker._current.started_at = datetime.now()
        tracker.complete_iteration(
            features=["feature1"],
            tests_added=3,
            lines_added=50,
            commit_hash="abc123",
            notes="Test",
        )

        # Create new tracker instance with same storage
        new_tracker = IterationTracker(storage_path=temp_storage)

        assert len(new_tracker._history) == 1
        record = new_tracker._history[0]
        assert record.iteration_number == 1
        assert record.features_completed == ["feature1"]
        assert record.tests_added == 3
        assert record.lines_added == 50
        assert record.commit_hash == "abc123"
        assert record.status == "completed"
        assert record.notes == "Test"

    def test_load_history_corrupt_json(self, temp_storage):
        """Test loading history with corrupt JSON."""
        temp_storage.parent.mkdir(parents=True, exist_ok=True)
        temp_storage.write_text("not valid json{")

        tracker = IterationTracker(storage_path=temp_storage)

        # Should not crash, just have empty history
        assert tracker._history == []

    def test_load_history_missing_keys(self, temp_storage):
        """Test loading history with missing keys."""
        temp_storage.parent.mkdir(parents=True, exist_ok=True)
        temp_storage.write_text('{"iterations": [{"iteration_number": 1}]}')

        tracker = IterationTracker(storage_path=temp_storage)

        # Should handle missing keys gracefully
        assert tracker._history == []

    def test_save_history_format(self, tracker, temp_storage):
        """Test that saved history has correct format."""
        tracker.start_iteration(1)
        tracker._current.started_at = datetime.now()
        tracker.complete_iteration(features=["f1"])

        assert temp_storage.exists()
        data = json.loads(temp_storage.read_text())

        assert "iterations" in data
        assert "last_updated" in data
        assert len(data["iterations"]) == 1
        assert data["iterations"][0]["iteration_number"] == 1


class TestCreateTracker:
    """Test create_tracker factory function."""

    def test_create_tracker_default_path(self):
        """Test creating tracker with default path."""
        tracker = create_tracker()

        assert isinstance(tracker, IterationTracker)
        assert tracker.storage_path == Path(".forge/learning/iteration_history.json")

    def test_create_tracker_custom_path(self, temp_storage):
        """Test creating tracker with custom path."""
        tracker = create_tracker(storage_path=temp_storage)

        assert isinstance(tracker, IterationTracker)
        assert tracker.storage_path == temp_storage


class TestInProgressIterationHandling:
    """Test handling of in-progress iterations."""

    def test_get_current_when_none(self, tracker):
        """Test getting current iteration when none exists."""
        assert tracker.get_current() is None

    def test_get_current_when_exists(self, tracker):
        """Test getting current iteration when one exists."""
        record = tracker.start_iteration(1)

        current = tracker.get_current()

        assert current is not None
        assert current.iteration_number == 1
        assert current is record

    def test_current_cleared_after_complete(self, tracker):
        """Test that current is cleared after completing."""
        tracker.start_iteration(1)
        tracker._current.started_at = datetime.now()
        tracker.complete_iteration(features=[])

        assert tracker.get_current() is None

    def test_current_cleared_after_fail(self, tracker):
        """Test that current is cleared after failing."""
        tracker.start_iteration(1)
        tracker._current.started_at = datetime.now()
        tracker.fail_iteration()

        assert tracker.get_current() is None

    def test_multiple_start_iterations_in_sequence(self, tracker):
        """Test starting multiple iterations in sequence."""
        tracker.start_iteration(1)
        tracker.start_iteration(2)
        tracker.start_iteration(3)

        # Should have 2 failed iterations in history
        assert len(tracker._history) == 2
        assert tracker._history[0].status == "failed"
        assert tracker._history[1].status == "failed"

        # Current should be iteration 3
        current = tracker.get_current()
        assert current is not None
        assert current.iteration_number == 3
        assert current.status == "in_progress"

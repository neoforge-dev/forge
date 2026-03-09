"""Tests for feature queue manager."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from forge_harness.iteration.feature_queue import (
    Feature,
    FeatureQueue,
    FeatureStatus,
    Priority,
    QueueStats,
    create_feature_queue,
)


# Feature dataclass tests
class TestFeatureDataclass:
    """Tests for Feature dataclass."""

    def test_feature_creation_minimal(self):
        """Test creating feature with minimal fields."""
        feature = Feature(id="test-1", title="Test Feature")
        assert feature.id == "test-1"
        assert feature.title == "Test Feature"
        assert feature.status == FeatureStatus.PENDING
        assert feature.priority == Priority.MEDIUM
        assert feature.description == ""
        assert feature.epic == ""
        assert feature.acceptance_criteria == []
        assert feature.test_command == ""
        assert feature.assigned_to is None
        assert feature.started_at is None
        assert feature.completed_at is None

    def test_feature_creation_full(self):
        """Test creating feature with all fields."""
        started = datetime.now()
        feature = Feature(
            id="test-1",
            title="Test Feature",
            description="Description",
            status=FeatureStatus.IN_PROGRESS,
            priority=Priority.HIGH,
            epic="Epic 1",
            acceptance_criteria=["AC 1", "AC 2"],
            test_command="pytest",
            assigned_to="agent-1",
            started_at=started,
        )
        assert feature.id == "test-1"
        assert feature.title == "Test Feature"
        assert feature.description == "Description"
        assert feature.status == FeatureStatus.IN_PROGRESS
        assert feature.priority == Priority.HIGH
        assert feature.epic == "Epic 1"
        assert feature.acceptance_criteria == ["AC 1", "AC 2"]
        assert feature.test_command == "pytest"
        assert feature.assigned_to == "agent-1"
        assert feature.started_at == started

    def test_feature_to_dict_minimal(self):
        """Test converting minimal feature to dict."""
        feature = Feature(id="test-1", title="Test Feature")
        data = feature.to_dict()
        assert data["id"] == "test-1"
        assert data["title"] == "Test Feature"
        assert data["status"] == "pending"
        assert data["priority"] == "medium"
        assert data["description"] == ""
        assert data["epic"] == ""
        assert data["acceptance_criteria"] == []
        assert data["test_command"] == ""
        assert data["assigned_to"] is None
        assert data["started_at"] is None
        assert data["completed_at"] is None

    def test_feature_to_dict_with_timestamps(self):
        """Test converting feature with timestamps to dict."""
        started = datetime(2025, 1, 1, 10, 0, 0)
        completed = datetime(2025, 1, 1, 12, 0, 0)
        feature = Feature(
            id="test-1",
            title="Test Feature",
            started_at=started,
            completed_at=completed,
        )
        data = feature.to_dict()
        assert data["started_at"] == "2025-01-01T10:00:00"
        assert data["completed_at"] == "2025-01-01T12:00:00"

    def test_feature_from_dict_minimal(self):
        """Test creating feature from minimal dict."""
        data = {"id": "test-1", "title": "Test Feature"}
        feature = Feature.from_dict(data)
        assert feature.id == "test-1"
        assert feature.title == "Test Feature"
        assert feature.status == FeatureStatus.PENDING
        assert feature.priority == Priority.MEDIUM

    def test_feature_from_dict_full(self):
        """Test creating feature from full dict."""
        data = {
            "id": "test-1",
            "title": "Test Feature",
            "description": "Description",
            "status": "in_progress",
            "priority": "high",
            "epic": "Epic 1",
            "acceptance_criteria": ["AC 1", "AC 2"],
            "test_command": "pytest",
            "assigned_to": "agent-1",
            "started_at": "2025-01-01T10:00:00",
            "completed_at": "2025-01-01T12:00:00",
        }
        feature = Feature.from_dict(data)
        assert feature.id == "test-1"
        assert feature.title == "Test Feature"
        assert feature.description == "Description"
        assert feature.status == FeatureStatus.IN_PROGRESS
        assert feature.priority == Priority.HIGH
        assert feature.epic == "Epic 1"
        assert feature.acceptance_criteria == ["AC 1", "AC 2"]
        assert feature.test_command == "pytest"
        assert feature.assigned_to == "agent-1"
        assert feature.started_at == datetime(2025, 1, 1, 10, 0, 0)
        assert feature.completed_at == datetime(2025, 1, 1, 12, 0, 0)

    def test_feature_roundtrip(self):
        """Test feature serialization roundtrip."""
        original = Feature(
            id="test-1",
            title="Test Feature",
            description="Description",
            status=FeatureStatus.COMPLETED,
            priority=Priority.LOW,
            epic="Epic 1",
            acceptance_criteria=["AC 1"],
            test_command="pytest",
        )
        data = original.to_dict()
        restored = Feature.from_dict(data)
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.description == original.description
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.epic == original.epic


# QueueStats dataclass tests
class TestQueueStatsDataclass:
    """Tests for QueueStats dataclass."""

    def test_queue_stats_creation_default(self):
        """Test creating queue stats with defaults."""
        stats = QueueStats()
        assert stats.total == 0
        assert stats.pending == 0
        assert stats.in_progress == 0
        assert stats.completed == 0
        assert stats.blocked == 0
        assert stats.by_priority == {}
        assert stats.by_epic == {}

    def test_queue_stats_creation_with_values(self):
        """Test creating queue stats with values."""
        stats = QueueStats(
            total=10,
            pending=3,
            in_progress=2,
            completed=5,
            blocked=0,
            by_priority={"high": 2, "medium": 5, "low": 3},
            by_epic={"epic1": 4, "epic2": 6},
        )
        assert stats.total == 10
        assert stats.pending == 3
        assert stats.in_progress == 2
        assert stats.completed == 5
        assert stats.blocked == 0
        assert stats.by_priority == {"high": 2, "medium": 5, "low": 3}
        assert stats.by_epic == {"epic1": 4, "epic2": 6}


# FeatureQueue tests
class TestFeatureQueue:
    """Tests for FeatureQueue class."""

    @pytest.fixture
    def temp_features_file(self, tmp_path):
        """Create a temporary features.json file."""
        return tmp_path / "features.json"

    @pytest.fixture
    def sample_features_data(self):
        """Sample features data for testing."""
        return {
            "version": "1.0",
            "description": "Test features",
            "features": [
                {
                    "id": "feature-1",
                    "title": "Feature 1",
                    "description": "First feature",
                    "status": "pending",
                    "priority": "high",
                    "epic": "Epic A",
                    "acceptance_criteria": ["AC 1"],
                    "test_command": "pytest",
                },
                {
                    "id": "feature-2",
                    "title": "Feature 2",
                    "status": "in_progress",
                    "priority": "medium",
                    "epic": "Epic A",
                },
                {
                    "id": "feature-3",
                    "title": "Feature 3",
                    "status": "completed",
                    "priority": "low",
                    "epic": "Epic B",
                },
                {
                    "id": "feature-4",
                    "title": "Feature 4",
                    "status": "pending",
                    "priority": "medium",
                    "epic": "Epic B",
                },
                {
                    "id": "feature-5",
                    "title": "Feature 5",
                    "status": "blocked",
                    "priority": "high",
                    "epic": "Epic A",
                },
            ],
            "metadata": {"created_at": "2025-01-01T00:00:00"},
        }

    def test_load_nonexistent_file(self, temp_features_file):
        """Test loading when file doesn't exist."""
        queue = FeatureQueue(temp_features_file)
        assert len(queue.get_all()) == 0

    def test_load_valid_file(self, temp_features_file, sample_features_data):
        """Test loading valid features file."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)
        features = queue.get_all()
        assert len(features) == 5
        assert features[0].id == "feature-1"
        assert features[0].title == "Feature 1"

    def test_load_invalid_json(self, temp_features_file):
        """Test loading invalid JSON file."""
        temp_features_file.write_text("{invalid json")
        queue = FeatureQueue(temp_features_file)
        assert len(queue.get_all()) == 0

    def test_save_creates_file(self, temp_features_file):
        """Test that save creates file with correct structure."""
        queue = FeatureQueue(temp_features_file)
        feature = Feature(id="test-1", title="Test Feature")
        queue.add_feature(feature)

        assert temp_features_file.exists()
        data = json.loads(temp_features_file.read_text())
        assert "version" in data
        assert "features" in data
        assert "metadata" in data
        assert len(data["features"]) == 1

    def test_get_all_returns_copy(self, temp_features_file, sample_features_data):
        """Test that get_all returns a copy."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        features1 = queue.get_all()
        features2 = queue.get_all()

        assert features1 is not features2
        assert len(features1) == len(features2)

    def test_get_by_id_existing(self, temp_features_file, sample_features_data):
        """Test getting feature by existing ID."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        feature = queue.get_by_id("feature-2")
        assert feature is not None
        assert feature.id == "feature-2"
        assert feature.title == "Feature 2"

    def test_get_by_id_nonexistent(self, temp_features_file, sample_features_data):
        """Test getting feature by nonexistent ID."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        feature = queue.get_by_id("nonexistent")
        assert feature is None

    def test_get_pending_sorted_by_priority(self, temp_features_file, sample_features_data):
        """Test getting pending features sorted by priority."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        pending = queue.get_pending()
        assert len(pending) == 2
        assert pending[0].id == "feature-1"  # high priority
        assert pending[1].id == "feature-4"  # medium priority

    def test_get_pending_with_limit(self, temp_features_file, sample_features_data):
        """Test getting pending features with limit."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        pending = queue.get_pending(limit=1)
        assert len(pending) == 1
        assert pending[0].id == "feature-1"

    def test_get_by_status(self, temp_features_file, sample_features_data):
        """Test getting features by status."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        completed = queue.get_by_status(FeatureStatus.COMPLETED)
        assert len(completed) == 1
        assert completed[0].id == "feature-3"

        in_progress = queue.get_by_status(FeatureStatus.IN_PROGRESS)
        assert len(in_progress) == 1
        assert in_progress[0].id == "feature-2"

    def test_get_by_epic(self, temp_features_file, sample_features_data):
        """Test getting features by epic."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        epic_a = queue.get_by_epic("Epic A")
        assert len(epic_a) == 3
        assert all(f.epic == "Epic A" for f in epic_a)

        epic_b = queue.get_by_epic("Epic B")
        assert len(epic_b) == 2
        assert all(f.epic == "Epic B" for f in epic_b)

    def test_get_by_priority(self, temp_features_file, sample_features_data):
        """Test getting features by priority."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        high = queue.get_by_priority(Priority.HIGH)
        assert len(high) == 2
        assert all(f.priority == Priority.HIGH for f in high)

        medium = queue.get_by_priority(Priority.MEDIUM)
        assert len(medium) == 2

        low = queue.get_by_priority(Priority.LOW)
        assert len(low) == 1

    def test_start_feature_existing(self, temp_features_file, sample_features_data):
        """Test starting an existing feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        result = queue.start_feature("feature-1", assigned_to="agent-1")
        assert result is True

        feature = queue.get_by_id("feature-1")
        assert feature.status == FeatureStatus.IN_PROGRESS
        assert feature.assigned_to == "agent-1"
        assert feature.started_at is not None

    def test_start_feature_nonexistent(self, temp_features_file, sample_features_data):
        """Test starting a nonexistent feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        result = queue.start_feature("nonexistent")
        assert result is False

    def test_complete_feature_existing(self, temp_features_file, sample_features_data):
        """Test completing an existing feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        result = queue.complete_feature("feature-2")
        assert result is True

        feature = queue.get_by_id("feature-2")
        assert feature.status == FeatureStatus.COMPLETED
        assert feature.completed_at is not None

    def test_complete_feature_nonexistent(self, temp_features_file, sample_features_data):
        """Test completing a nonexistent feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        result = queue.complete_feature("nonexistent")
        assert result is False

    def test_block_feature_existing(self, temp_features_file, sample_features_data):
        """Test blocking an existing feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        result = queue.block_feature("feature-1", reason="Dependency missing")
        assert result is True

        feature = queue.get_by_id("feature-1")
        assert feature.status == FeatureStatus.BLOCKED
        assert "Blocked: Dependency missing" in feature.description

    def test_block_feature_nonexistent(self, temp_features_file, sample_features_data):
        """Test blocking a nonexistent feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        result = queue.block_feature("nonexistent")
        assert result is False

    def test_add_feature_new(self, temp_features_file):
        """Test adding a new feature."""
        queue = FeatureQueue(temp_features_file)

        feature = Feature(id="new-1", title="New Feature")
        result = queue.add_feature(feature)
        assert result is True

        retrieved = queue.get_by_id("new-1")
        assert retrieved is not None
        assert retrieved.title == "New Feature"

    def test_add_feature_duplicate_id(self, temp_features_file, sample_features_data):
        """Test adding a feature with duplicate ID."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        feature = Feature(id="feature-1", title="Duplicate")
        result = queue.add_feature(feature)
        assert result is False

        # Verify original feature unchanged
        original = queue.get_by_id("feature-1")
        assert original.title == "Feature 1"

    def test_remove_feature_existing(self, temp_features_file, sample_features_data):
        """Test removing an existing feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        original_count = len(queue.get_all())
        result = queue.remove_feature("feature-1")
        assert result is True

        assert len(queue.get_all()) == original_count - 1
        assert queue.get_by_id("feature-1") is None

    def test_remove_feature_nonexistent(self, temp_features_file, sample_features_data):
        """Test removing a nonexistent feature."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        original_count = len(queue.get_all())
        result = queue.remove_feature("nonexistent")
        assert result is False

        assert len(queue.get_all()) == original_count

    def test_get_stats(self, temp_features_file, sample_features_data):
        """Test getting queue statistics."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        stats = queue.get_stats()
        assert stats.total == 5
        assert stats.pending == 2
        assert stats.in_progress == 1
        assert stats.completed == 1
        assert stats.blocked == 1
        assert stats.by_priority["high"] == 2
        assert stats.by_priority["medium"] == 2
        assert stats.by_priority["low"] == 1
        assert stats.by_epic["Epic A"] == 3
        assert stats.by_epic["Epic B"] == 2

    def test_get_stats_empty_queue(self, temp_features_file):
        """Test getting stats for empty queue."""
        queue = FeatureQueue(temp_features_file)
        stats = queue.get_stats()
        assert stats.total == 0
        assert stats.pending == 0
        assert stats.in_progress == 0
        assert stats.completed == 0
        assert stats.blocked == 0
        assert stats.by_priority == {}
        assert stats.by_epic == {}

    def test_get_next_feature(self, temp_features_file, sample_features_data):
        """Test getting the next feature to work on."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        next_feature = queue.get_next_feature()
        assert next_feature is not None
        assert next_feature.id == "feature-1"  # Highest priority pending

    def test_get_next_feature_empty_queue(self, temp_features_file):
        """Test getting next feature from empty queue."""
        queue = FeatureQueue(temp_features_file)
        next_feature = queue.get_next_feature()
        assert next_feature is None

    def test_metadata_preservation(self, temp_features_file, sample_features_data):
        """Test that metadata is preserved across save/load."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        # Add a feature to trigger save
        feature = Feature(id="new-1", title="New")
        queue.add_feature(feature)

        # Reload
        queue2 = FeatureQueue(temp_features_file)
        assert queue2._metadata.get("created_at") == "2025-01-01T00:00:00"

    def test_status_transition_persistence(self, temp_features_file, sample_features_data):
        """Test that status transitions persist across instances."""
        temp_features_file.write_text(json.dumps(sample_features_data))
        queue = FeatureQueue(temp_features_file)

        queue.start_feature("feature-1")

        # Create new instance and verify
        queue2 = FeatureQueue(temp_features_file)
        feature = queue2.get_by_id("feature-1")
        assert feature.status == FeatureStatus.IN_PROGRESS


# Factory function tests
class TestFactoryFunction:
    """Tests for create_feature_queue factory function."""

    def test_create_with_path(self, tmp_path):
        """Test factory function with path."""
        features_path = tmp_path / "features.json"
        queue = create_feature_queue(features_path)
        assert isinstance(queue, FeatureQueue)
        assert queue.features_path == features_path

    def test_create_without_path(self):
        """Test factory function without path."""
        queue = create_feature_queue()
        assert isinstance(queue, FeatureQueue)
        assert queue.features_path == Path("features.json")

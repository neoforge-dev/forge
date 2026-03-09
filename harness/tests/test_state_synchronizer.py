"""
Tests for StateSynchronizer - State consistency bridge.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.state_synchronizer import (
    FileTracker,
    StateSnapshot,
    StateSynchronizer,
    SyncEventType,
    create_synchronizer,
    get_synchronizer,
    reset_synchronizer,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_forge_root(tmp_path):
    """Create a temporary FORGE root with checkpoint directories."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()

    # Create checkpoint directories
    (forge_root / ".forge/approvals").mkdir()
    (forge_root / ".forge/orchestration_checkpoints").mkdir()
    (forge_root / ".forge/ralph_checkpoints").mkdir()
    (forge_root / ".forge/sessions").mkdir()
    (forge_root / ".forge/mvp_status").mkdir()

    return forge_root


@pytest.fixture
def synchronizer(temp_forge_root):
    """Create a StateSynchronizer instance."""
    return StateSynchronizer(temp_forge_root)


@pytest.fixture
def approval_data():
    """Sample approval data."""
    return {
        "id": "apr_test123",
        "type": "content",
        "status": "pending",
        "title": "Test Approval",
        "domain": "test-domain",
        "priority": "normal",
        "tier": "PHONE",
        "risk_score": 0.5,
        "created_at": datetime.now(UTC).isoformat(),
        "metadata": {"project": "test-project"},
    }


@pytest.fixture
def pipeline_data():
    """Sample pipeline checkpoint data."""
    return {
        "checkpoint_id": "cp_test123",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "pipeline": {
            "name": "test_pipeline",
            "steps": [
                {"name": "step1"},
                {"name": "step2"},
                {"name": "step3"},
            ],
        },
        "step_results": {},
    }


@pytest.fixture
def ralph_data():
    """Sample Ralph checkpoint data."""
    return {
        "state": "running",
        "current_feature": "F001",
        "iteration": 5,
        "max_iterations": 100,
        "failure_count": 1,
        "started_at": datetime.now(UTC).isoformat(),
    }


@pytest.fixture
def session_data():
    """Sample session data."""
    return {
        "session_id": "sess_test123",
        "agent_type": "claude-code",
        "agent_role": "builder",
        "domain": "test-domain",
        "project": "test-project",
        "status": "active",
        "current_task": "Implementing feature",
        "registered_at": datetime.now(UTC).isoformat(),
        "last_heartbeat": datetime.now(UTC).isoformat(),
    }


# =============================================================================
# FileTracker Tests
# =============================================================================


class TestFileTracker:
    """Tests for FileTracker class."""

    def test_compute_hash(self, tmp_path):
        """Test hash computation."""
        file_path = tmp_path / "test.json"
        file_path.write_text('{"test": true}')

        tracker = FileTracker(path=file_path)
        hash1 = tracker.compute_hash()

        assert hash1 is not None
        assert len(hash1) == 32  # MD5 hex length

        # Same content = same hash
        hash2 = tracker.compute_hash()
        assert hash1 == hash2

        # Different content = different hash
        file_path.write_text('{"test": false}')
        hash3 = tracker.compute_hash()
        assert hash3 != hash1

    def test_compute_hash_nonexistent(self, tmp_path):
        """Test hash for non-existent file."""
        tracker = FileTracker(path=tmp_path / "nonexistent.json")
        assert tracker.compute_hash() is None

    def test_has_changed_new_file(self, tmp_path):
        """Test change detection for new file."""
        file_path = tmp_path / "test.json"
        file_path.write_text('{"test": true}')

        tracker = FileTracker(path=file_path)
        # First check - no previous state
        assert tracker.has_changed() is True

        # After update, no change
        tracker.update()
        assert tracker.has_changed() is False

    def test_has_changed_modified_file(self, tmp_path):
        """Test change detection for modified file."""
        import time

        file_path = tmp_path / "test.json"
        file_path.write_text('{"test": true}')

        tracker = FileTracker(path=file_path)
        tracker.update()
        assert tracker.has_changed() is False

        # Small delay to ensure filesystem timestamp changes
        # Some filesystems have coarse timestamp granularity
        time.sleep(0.05)

        # Modify file
        file_path.write_text('{"test": false}')
        assert tracker.has_changed() is True

    def test_has_changed_deleted_file(self, tmp_path):
        """Test change detection for deleted file."""
        file_path = tmp_path / "test.json"
        file_path.write_text('{"test": true}')

        tracker = FileTracker(path=file_path)
        tracker.update()
        assert tracker.has_changed() is False

        # Delete file
        file_path.unlink()
        assert tracker.has_changed() is True


# =============================================================================
# StateSynchronizer Initialization Tests
# =============================================================================


class TestStateSynchronizerInit:
    """Tests for StateSynchronizer initialization."""

    def test_init_creates_directories(self, temp_forge_root):
        """Test that init ensures directories exist."""
        sync = StateSynchronizer(temp_forge_root)
        sync._ensure_directories()

        assert sync.approval_dir.exists()
        assert sync.pipeline_dir.exists()
        assert sync.ralph_dir.exists()
        assert sync.session_dir.exists()

    def test_init_with_custom_event_bus(self, temp_forge_root):
        """Test initialization with custom event bus."""
        mock_bus = MagicMock()
        sync = StateSynchronizer(temp_forge_root, event_bus=mock_bus)
        assert sync._event_bus is mock_bus


# =============================================================================
# Approval Synchronization Tests
# =============================================================================


class TestApprovalSync:
    """Tests for approval synchronization."""

    @pytest.mark.asyncio
    async def test_sync_new_approval(self, synchronizer, temp_forge_root, approval_data):
        """Test syncing a new approval file."""
        # Create approval file
        approval_file = temp_forge_root / ".forge/approvals" / "approval_test.json"
        approval_file.write_text(json.dumps(approval_data))

        events = await synchronizer._sync_approvals()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.APPROVAL_CREATED
        assert events[0].data["id"] == "apr_test123"

    @pytest.mark.asyncio
    async def test_sync_updated_approval(self, synchronizer, temp_forge_root, approval_data):
        """Test syncing an updated approval."""
        approval_file = temp_forge_root / ".forge/approvals" / "approval_test.json"
        approval_file.write_text(json.dumps(approval_data))

        # First sync
        await synchronizer._sync_approvals()

        # Update approval
        approval_data["status"] = "approved"
        approval_file.write_text(json.dumps(approval_data))

        # Second sync
        events = await synchronizer._sync_approvals()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.APPROVAL_UPDATED

    @pytest.mark.asyncio
    async def test_sync_expired_approval(self, synchronizer, temp_forge_root, approval_data):
        """Test syncing an expired approval."""
        approval_file = temp_forge_root / ".forge/approvals" / "approval_test.json"
        approval_file.write_text(json.dumps(approval_data))

        # First sync
        await synchronizer._sync_approvals()

        # Mark as expired
        approval_data["status"] = "expired"
        approval_file.write_text(json.dumps(approval_data))

        # Second sync
        events = await synchronizer._sync_approvals()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.APPROVAL_EXPIRED


# =============================================================================
# Pipeline Synchronization Tests
# =============================================================================


class TestPipelineSync:
    """Tests for pipeline synchronization."""

    @pytest.mark.asyncio
    async def test_sync_new_pipeline(self, synchronizer, temp_forge_root, pipeline_data):
        """Test syncing a new pipeline checkpoint."""
        pipeline_file = temp_forge_root / ".forge/orchestration_checkpoints" / "pipeline_test.json"
        pipeline_file.write_text(json.dumps(pipeline_data))

        events = await synchronizer._sync_pipelines()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.PIPELINE_STARTED

    @pytest.mark.asyncio
    async def test_sync_completed_pipeline(self, synchronizer, temp_forge_root, pipeline_data):
        """Test syncing a completed pipeline."""
        pipeline_file = temp_forge_root / ".forge/orchestration_checkpoints" / "pipeline_test.json"
        pipeline_file.write_text(json.dumps(pipeline_data))

        # First sync
        await synchronizer._sync_pipelines()

        # Complete pipeline
        pipeline_data["status"] = "completed"
        pipeline_file.write_text(json.dumps(pipeline_data))

        # Second sync
        events = await synchronizer._sync_pipelines()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.PIPELINE_COMPLETED

    @pytest.mark.asyncio
    async def test_sync_failed_pipeline(self, synchronizer, temp_forge_root, pipeline_data):
        """Test syncing a failed pipeline."""
        pipeline_file = temp_forge_root / ".forge/orchestration_checkpoints" / "pipeline_test.json"
        pipeline_file.write_text(json.dumps(pipeline_data))

        await synchronizer._sync_pipelines()

        pipeline_data["status"] = "failed"
        pipeline_data["error"] = "Test error"
        pipeline_file.write_text(json.dumps(pipeline_data))

        events = await synchronizer._sync_pipelines()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.PIPELINE_FAILED


# =============================================================================
# Ralph Synchronization Tests
# =============================================================================


class TestRalphSync:
    """Tests for Ralph loop synchronization."""

    @pytest.mark.asyncio
    async def test_sync_ralph_started(self, synchronizer, temp_forge_root, ralph_data):
        """Test syncing Ralph start."""
        ralph_file = temp_forge_root / ".forge/ralph_checkpoints" / "ralph_latest.json"
        ralph_file.write_text(json.dumps(ralph_data))

        events = await synchronizer._sync_ralph()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.RALPH_STARTED

    @pytest.mark.asyncio
    async def test_sync_ralph_iteration(self, synchronizer, temp_forge_root, ralph_data):
        """Test syncing Ralph iteration."""
        ralph_file = temp_forge_root / ".forge/ralph_checkpoints" / "ralph_latest.json"
        ralph_file.write_text(json.dumps(ralph_data))

        await synchronizer._sync_ralph()

        # Increment iteration
        ralph_data["iteration"] = 6
        ralph_file.write_text(json.dumps(ralph_data))

        events = await synchronizer._sync_ralph()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.RALPH_ITERATION

    @pytest.mark.asyncio
    async def test_sync_ralph_completed(self, synchronizer, temp_forge_root, ralph_data):
        """Test syncing Ralph completion."""
        ralph_file = temp_forge_root / ".forge/ralph_checkpoints" / "ralph_latest.json"
        ralph_file.write_text(json.dumps(ralph_data))

        await synchronizer._sync_ralph()

        ralph_data["state"] = "completed"
        ralph_file.write_text(json.dumps(ralph_data))

        events = await synchronizer._sync_ralph()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.RALPH_COMPLETED


# =============================================================================
# Session Synchronization Tests
# =============================================================================


class TestSessionSync:
    """Tests for session synchronization."""

    @pytest.mark.asyncio
    async def test_sync_new_session(self, synchronizer, temp_forge_root, session_data):
        """Test syncing a new session."""
        session_file = temp_forge_root / ".forge/sessions" / "session_test.json"
        session_file.write_text(json.dumps(session_data))

        events = await synchronizer._sync_sessions()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.SESSION_REGISTERED

    @pytest.mark.asyncio
    async def test_sync_stale_session(self, synchronizer, temp_forge_root, session_data):
        """Test detecting stale session."""
        # Make heartbeat old
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        session_data["last_heartbeat"] = old_time.isoformat()

        session_file = temp_forge_root / ".forge/sessions" / "session_test.json"
        session_file.write_text(json.dumps(session_data))

        events = await synchronizer._sync_sessions()

        assert len(events) == 1
        # Should be registered but marked as stale
        assert events[0].data.get("is_stale") is True

    @pytest.mark.asyncio
    async def test_sync_session_removed(self, synchronizer, temp_forge_root, session_data):
        """Test detecting removed session."""
        session_file = temp_forge_root / ".forge/sessions" / "session_test.json"
        session_file.write_text(json.dumps(session_data))

        # First sync - register session
        await synchronizer._sync_sessions()

        # Delete file
        session_file.unlink()

        # Second sync - detect removal
        events = await synchronizer._sync_sessions()

        assert len(events) == 1
        assert events[0].event_type == SyncEventType.SESSION_REMOVED


# =============================================================================
# Full Sync Tests
# =============================================================================


class TestFullSync:
    """Tests for full synchronization cycle."""

    @pytest.mark.asyncio
    async def test_sync_all(
        self,
        synchronizer,
        temp_forge_root,
        approval_data,
        pipeline_data,
        ralph_data,
        session_data,
    ):
        """Test full sync cycle."""
        # Create all checkpoint files
        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )
        (temp_forge_root / ".forge/orchestration_checkpoints" / "pipeline_test.json").write_text(
            json.dumps(pipeline_data)
        )
        (temp_forge_root / ".forge/ralph_checkpoints" / "ralph_latest.json").write_text(
            json.dumps(ralph_data)
        )
        (temp_forge_root / ".forge/sessions" / "session_test.json").write_text(
            json.dumps(session_data)
        )

        snapshot = await synchronizer.sync_all()

        assert isinstance(snapshot, StateSnapshot)
        assert snapshot.pending_approvals == 1
        assert snapshot.active_pipelines == 1
        assert snapshot.ralph is not None
        assert snapshot.ralph.active is True
        assert snapshot.active_sessions == 1


# =============================================================================
# State Snapshot Tests
# =============================================================================


class TestStateSnapshot:
    """Tests for state snapshot building."""

    @pytest.mark.asyncio
    async def test_get_state_snapshot(
        self, synchronizer, temp_forge_root, approval_data, pipeline_data
    ):
        """Test getting state snapshot."""
        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )
        (temp_forge_root / ".forge/orchestration_checkpoints" / "pipeline_test.json").write_text(
            json.dumps(pipeline_data)
        )

        await synchronizer.sync_all()
        snapshot = synchronizer.get_state_snapshot()

        assert snapshot is not None
        assert len(snapshot.approvals) == 1
        assert len(snapshot.pipelines) == 1

    def test_get_pending_approvals(self, synchronizer, temp_forge_root, approval_data):
        """Test getting pending approvals."""
        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )

        # Sync first
        asyncio.get_event_loop().run_until_complete(synchronizer.sync_all())

        approvals = synchronizer.get_pending_approvals()
        assert len(approvals) == 1
        assert approvals[0].id == "apr_test123"


# =============================================================================
# Callback Tests
# =============================================================================


class TestCallbacks:
    """Tests for callback functionality."""

    @pytest.mark.asyncio
    async def test_sync_callback(self, synchronizer, temp_forge_root, approval_data):
        """Test sync callback is called."""
        events_received = []

        def callback(event_type, data):
            events_received.append((event_type, data))

        synchronizer.on_change(callback)

        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )

        await synchronizer.sync_all()

        # Should have received at least the approval created event and sync complete
        assert len(events_received) >= 2
        event_types = [e[0] for e in events_received]
        assert SyncEventType.APPROVAL_CREATED in event_types
        assert SyncEventType.SYNC_COMPLETE in event_types

    @pytest.mark.asyncio
    async def test_async_callback(self, synchronizer, temp_forge_root, approval_data):
        """Test async callback is called."""
        events_received = []

        async def async_callback(event_type, data):
            events_received.append((event_type, data))

        synchronizer.on_change_async(async_callback)

        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )

        await synchronizer.sync_all()

        assert len(events_received) >= 2

    def test_remove_callback(self, synchronizer):
        """Test callback removal."""
        callback = MagicMock()
        synchronizer.on_change(callback)
        assert callback in synchronizer._callbacks

        synchronizer.remove_callback(callback)
        assert callback not in synchronizer._callbacks


# =============================================================================
# Event Bus Integration Tests
# =============================================================================


class TestEventBusIntegration:
    """Tests for event bus integration."""

    @pytest.mark.asyncio
    async def test_publish_to_event_bus(self, temp_forge_root, approval_data):
        """Test events are published to event bus."""
        mock_bus = AsyncMock()
        sync = StateSynchronizer(temp_forge_root, event_bus=mock_bus)

        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )

        await sync.sync_all()

        # Verify event bus was called
        assert mock_bus.publish.called
        call_args = [call[0] for call in mock_bus.publish.call_args_list]
        event_types = [args[0] for args in call_args]
        assert "approval.created" in event_types


# =============================================================================
# Lifecycle Tests
# =============================================================================


class TestLifecycle:
    """Tests for synchronizer lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop(self, synchronizer):
        """Test start and stop."""
        await synchronizer.start(poll_interval=0.1)
        assert synchronizer._running is True

        await asyncio.sleep(0.2)  # Let a sync cycle run

        await synchronizer.stop()
        assert synchronizer._running is False

    @pytest.mark.asyncio
    async def test_double_start(self, synchronizer):
        """Test starting twice logs warning."""
        await synchronizer.start(poll_interval=1.0)
        await synchronizer.start(poll_interval=1.0)  # Should warn
        await synchronizer.stop()


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Tests for statistics tracking."""

    @pytest.mark.asyncio
    async def test_get_stats(self, synchronizer, temp_forge_root, approval_data):
        """Test statistics collection."""
        (temp_forge_root / ".forge/approvals" / "approval_test.json").write_text(
            json.dumps(approval_data)
        )

        await synchronizer.sync_all()

        stats = synchronizer.get_stats()

        assert stats["sync_count"] == 1
        assert stats["tracked_files"] >= 1
        assert "last_sync_duration_ms" in stats

    def test_error_tracking(self, synchronizer):
        """Test error tracking."""
        synchronizer._add_error("Test error 1")
        synchronizer._add_error("Test error 2")

        errors = synchronizer.get_errors()
        assert len(errors) == 2
        assert errors[-1][1] == "Test error 2"


# =============================================================================
# Global Instance Tests
# =============================================================================


class TestGlobalInstance:
    """Tests for global singleton pattern."""

    def test_get_synchronizer_singleton(self, temp_forge_root):
        """Test singleton pattern."""
        reset_synchronizer()

        with patch.dict("os.environ", {"FORGE_ROOT": str(temp_forge_root)}):
            sync1 = get_synchronizer()
            sync2 = get_synchronizer()
            assert sync1 is sync2

        reset_synchronizer()

    def test_create_synchronizer_new_instance(self, temp_forge_root):
        """Test factory creates new instance."""
        sync1 = create_synchronizer(temp_forge_root)
        sync2 = create_synchronizer(temp_forge_root)
        assert sync1 is not sync2

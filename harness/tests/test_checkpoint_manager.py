"""
Comprehensive tests for forge_harness.checkpoint_manager module.

Tests cover:
- All dataclass fields and to_dict() methods
- CheckpointManager initialization
- get_active_pipelines with mock files
- get_ralph_state with mock files
- get_pending_approvals with various approval types
- get_active_sessions
- save_session
- Caching behavior (invalidate_cache)
- Singleton pattern with get_checkpoint_manager
- Error handling for missing/corrupt files
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from forge_harness.checkpoint_manager import (
    ApprovalRequest,
    CheckpointManager,
    PipelineCheckpoint,
    RalphState,
    SessionInfo,
    get_checkpoint_manager,
    reset_checkpoint_manager,
)

# =============================================================================
# Dataclass Tests
# =============================================================================


class TestPipelineCheckpoint:
    """Test PipelineCheckpoint dataclass."""

    def test_fields(self):
        """Test all fields are accessible."""
        checkpoint = PipelineCheckpoint(
            checkpoint_id="test-123",
            name="Test Pipeline",
            status="running",
            current_step="step_1",
            step_index=1,
            total_steps=5,
            started_at=datetime.now(UTC),
            error=None,
            domain="test-domain",
            project="test-project",
            context={"key": "value"},
        )

        assert checkpoint.checkpoint_id == "test-123"
        assert checkpoint.name == "Test Pipeline"
        assert checkpoint.status == "running"
        assert checkpoint.current_step == "step_1"
        assert checkpoint.step_index == 1
        assert checkpoint.total_steps == 5
        assert checkpoint.started_at is not None
        assert checkpoint.error is None
        assert checkpoint.domain == "test-domain"
        assert checkpoint.project == "test-project"
        assert checkpoint.context == {"key": "value"}

    def test_to_dict(self):
        """Test to_dict() method."""
        started_at = datetime.now(UTC)
        checkpoint = PipelineCheckpoint(
            checkpoint_id="test-123",
            name="Test Pipeline",
            status="running",
            current_step="step_1",
            step_index=1,
            total_steps=5,
            started_at=started_at,
            error="Some error",
            domain="test-domain",
            project="test-project",
            context={"key": "value"},
        )

        result = checkpoint.to_dict()

        assert result["checkpoint_id"] == "test-123"
        assert result["name"] == "Test Pipeline"
        assert result["status"] == "running"
        assert result["current_step"] == "step_1"
        assert result["step_index"] == 1
        assert result["total_steps"] == 5
        assert result["started_at"] == started_at.isoformat()
        assert result["error"] == "Some error"
        assert result["domain"] == "test-domain"
        assert result["project"] == "test-project"
        assert result["context"] == {"key": "value"}

    def test_to_dict_with_none_started_at(self):
        """Test to_dict() with None started_at."""
        checkpoint = PipelineCheckpoint(
            checkpoint_id="test-123",
            name="Test Pipeline",
            status="running",
            current_step="step_1",
            step_index=1,
            total_steps=5,
            started_at=None,
        )

        result = checkpoint.to_dict()
        assert result["started_at"] is None

    def test_default_values(self):
        """Test default field values."""
        checkpoint = PipelineCheckpoint(
            checkpoint_id="test-123",
            name="Test Pipeline",
            status="running",
            current_step="step_1",
            step_index=1,
            total_steps=5,
            started_at=None,
        )

        assert checkpoint.error is None
        assert checkpoint.domain is None
        assert checkpoint.project is None
        assert checkpoint.context == {}


class TestRalphState:
    """Test RalphState dataclass."""

    def test_fields(self):
        """Test all fields are accessible."""
        started_at = datetime.now(UTC)
        state = RalphState(
            active=True,
            state="running",
            current_feature="Feature 1",
            iteration=5,
            max_iterations=100,
            failure_count=2,
            started_at=started_at,
            domain="test-domain",
            project="test-project",
            checkpoint_file="checkpoint.json",
        )

        assert state.active is True
        assert state.state == "running"
        assert state.current_feature == "Feature 1"
        assert state.iteration == 5
        assert state.max_iterations == 100
        assert state.failure_count == 2
        assert state.started_at == started_at
        assert state.domain == "test-domain"
        assert state.project == "test-project"
        assert state.checkpoint_file == "checkpoint.json"

    def test_to_dict(self):
        """Test to_dict() method."""
        started_at = datetime.now(UTC)
        state = RalphState(
            active=True,
            state="running",
            current_feature="Feature 1",
            iteration=5,
            max_iterations=100,
            failure_count=2,
            started_at=started_at,
            domain="test-domain",
            project="test-project",
            checkpoint_file="checkpoint.json",
        )

        result = state.to_dict()

        assert result["active"] is True
        assert result["state"] == "running"
        assert result["current_feature"] == "Feature 1"
        assert result["iteration"] == 5
        assert result["max_iterations"] == 100
        assert result["failure_count"] == 2
        assert result["started_at"] == started_at.isoformat()
        assert result["domain"] == "test-domain"
        assert result["project"] == "test-project"
        assert result["checkpoint_file"] == "checkpoint.json"

    def test_to_dict_with_none_started_at(self):
        """Test to_dict() with None started_at."""
        state = RalphState(
            active=False,
            state="idle",
            current_feature=None,
            iteration=0,
            max_iterations=0,
            failure_count=0,
            started_at=None,
        )

        result = state.to_dict()
        assert result["started_at"] is None

    def test_default_values(self):
        """Test default field values."""
        state = RalphState(
            active=False,
            state="idle",
            current_feature=None,
            iteration=0,
            max_iterations=0,
            failure_count=0,
            started_at=None,
        )

        assert state.domain is None
        assert state.project is None
        assert state.checkpoint_file is None


class TestApprovalRequest:
    """Test ApprovalRequest dataclass."""

    def test_fields(self):
        """Test all fields are accessible."""
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(hours=24)
        request = ApprovalRequest(
            id="approval-123",
            type="deployment",
            title="Deploy to prod",
            description="Deploy version 1.0.0",
            domain="test-domain",
            priority="high",
            status="pending",
            tier="PHONE",
            risk_score=0.7,
            created_at=created_at,
            expires_at=expires_at,
            metadata={"version": "1.0.0"},
            tags=["production", "deployment"],
        )

        assert request.id == "approval-123"
        assert request.type == "deployment"
        assert request.title == "Deploy to prod"
        assert request.description == "Deploy version 1.0.0"
        assert request.domain == "test-domain"
        assert request.priority == "high"
        assert request.status == "pending"
        assert request.tier == "PHONE"
        assert request.risk_score == 0.7
        assert request.created_at == created_at
        assert request.expires_at == expires_at
        assert request.metadata == {"version": "1.0.0"}
        assert request.tags == ["production", "deployment"]

    def test_to_dict(self):
        """Test to_dict() method."""
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(hours=24)
        request = ApprovalRequest(
            id="approval-123",
            type="deployment",
            title="Deploy to prod",
            description="Deploy version 1.0.0",
            domain="test-domain",
            priority="high",
            status="pending",
            tier="PHONE",
            risk_score=0.7,
            created_at=created_at,
            expires_at=expires_at,
            metadata={"version": "1.0.0"},
            tags=["production", "deployment"],
        )

        result = request.to_dict()

        assert result["id"] == "approval-123"
        assert result["type"] == "deployment"
        assert result["title"] == "Deploy to prod"
        assert result["description"] == "Deploy version 1.0.0"
        assert result["domain"] == "test-domain"
        assert result["priority"] == "high"
        assert result["status"] == "pending"
        assert result["tier"] == "PHONE"
        assert result["risk_score"] == 0.7
        assert result["created_at"] == created_at.isoformat()
        assert result["expires_at"] == expires_at.isoformat()
        assert result["metadata"] == {"version": "1.0.0"}
        assert result["tags"] == ["production", "deployment"]

    def test_to_dict_with_none_expires_at(self):
        """Test to_dict() with None expires_at."""
        request = ApprovalRequest(
            id="approval-123",
            type="deployment",
            title="Deploy to prod",
            description="Deploy version 1.0.0",
            domain="test-domain",
            priority="high",
            status="pending",
            tier="PHONE",
            risk_score=0.7,
            created_at=datetime.now(UTC),
            expires_at=None,
        )

        result = request.to_dict()
        assert result["expires_at"] is None

    def test_default_values(self):
        """Test default field values."""
        request = ApprovalRequest(
            id="approval-123",
            type="deployment",
            title="Deploy to prod",
            description="Deploy version 1.0.0",
            domain="test-domain",
            priority="high",
            status="pending",
            tier="PHONE",
            risk_score=0.7,
            created_at=datetime.now(UTC),
        )

        assert request.expires_at is None
        assert request.metadata == {}
        assert request.tags == []


class TestSessionInfo:
    """Test SessionInfo dataclass."""

    def test_fields(self):
        """Test all fields are accessible."""
        started_at = datetime.now(UTC)
        last_activity = started_at + timedelta(hours=1)
        session = SessionInfo(
            session_id="session-123",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=started_at,
            last_activity=last_activity,
            completed_tasks=["task1", "task2"],
            pending_tasks=["task3", "task4"],
            context={"key": "value"},
        )

        assert session.session_id == "session-123"
        assert session.domain == "test-domain"
        assert session.project == "test-project"
        assert session.status == "active"
        assert session.started_at == started_at
        assert session.last_activity == last_activity
        assert session.completed_tasks == ["task1", "task2"]
        assert session.pending_tasks == ["task3", "task4"]
        assert session.context == {"key": "value"}

    def test_to_dict(self):
        """Test to_dict() method."""
        started_at = datetime.now(UTC)
        last_activity = started_at + timedelta(hours=1)
        session = SessionInfo(
            session_id="session-123",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=started_at,
            last_activity=last_activity,
            completed_tasks=["task1", "task2"],
            pending_tasks=["task3", "task4"],
            context={"key": "value"},
        )

        result = session.to_dict()

        assert result["session_id"] == "session-123"
        assert result["domain"] == "test-domain"
        assert result["project"] == "test-project"
        assert result["status"] == "active"
        assert result["started_at"] == started_at.isoformat()
        assert result["last_activity"] == last_activity.isoformat()
        assert result["completed_tasks"] == ["task1", "task2"]
        assert result["pending_tasks"] == ["task3", "task4"]
        assert result["context"] == {"key": "value"}

    def test_to_dict_with_none_last_activity(self):
        """Test to_dict() with None last_activity."""
        session = SessionInfo(
            session_id="session-123",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=datetime.now(UTC),
            last_activity=None,
        )

        result = session.to_dict()
        assert result["last_activity"] is None

    def test_default_values(self):
        """Test default field values."""
        session = SessionInfo(
            session_id="session-123",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=datetime.now(UTC),
        )

        assert session.last_activity is None
        assert session.completed_tasks == []
        assert session.pending_tasks == []
        assert session.context == {}


# =============================================================================
# CheckpointManager Tests
# =============================================================================


class TestCheckpointManagerInit:
    """Test CheckpointManager initialization."""

    def test_init_with_default_forge_root(self, tmp_path, monkeypatch):
        """Test initialization with default forge_root."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        manager = CheckpointManager()

        assert manager.forge_root == tmp_path
        assert manager._cache_ttl == CheckpointManager.DEFAULT_CACHE_TTL

    def test_init_with_custom_forge_root(self, tmp_path):
        """Test initialization with custom forge_root."""
        manager = CheckpointManager(forge_root=tmp_path)

        assert manager.forge_root == tmp_path

    def test_init_with_custom_cache_ttl(self, tmp_path):
        """Test initialization with custom cache_ttl."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)

        assert manager._cache_ttl == 10.0

    def test_directories_are_set(self, tmp_path):
        """Test that directory paths are correctly set."""
        manager = CheckpointManager(forge_root=tmp_path)

        assert manager.orchestration_dir == tmp_path / ".forge/orchestration_checkpoints"
        assert manager.ralph_dir == tmp_path / ".forge/ralph_checkpoints"
        assert manager.approvals_dir == tmp_path / ".forge/approvals"
        assert manager.sessions_dir == tmp_path / ".forge/sessions"

    def test_cache_is_initialized(self, tmp_path):
        """Test that cache structures are initialized."""
        manager = CheckpointManager(forge_root=tmp_path)

        assert isinstance(manager._cache, dict)
        assert len(manager._cache) == 0


class TestGetActivePipelines:
    """Test get_active_pipelines method."""

    def test_returns_empty_when_dir_does_not_exist(self, tmp_path):
        """Test returns empty list when orchestration dir doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        pipelines = manager.get_active_pipelines()

        assert pipelines == []

    def test_returns_running_pipeline(self, tmp_path):
        """Test returns pipeline with running status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        started_at = datetime.now(UTC)
        pipeline_data = {
            "status": "running",
            "pipeline": {
                "name": "Test Pipeline",
                "steps": [
                    {"name": "step1"},
                    {"name": "step2"},
                    {"name": "step3"},
                ],
            },
            "step_results": {"step1": {"status": "completed"}},
            "started_at": started_at.isoformat(),
            "domain": "test-domain",
            "project": "test-project",
            "context": {"key": "value"},
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-123.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 1
        assert pipelines[0].checkpoint_id == "pipeline-123"
        assert pipelines[0].name == "Test Pipeline"
        assert pipelines[0].status == "running"
        assert pipelines[0].current_step == "step2"
        assert pipelines[0].step_index == 1
        assert pipelines[0].total_steps == 3
        assert pipelines[0].domain == "test-domain"
        assert pipelines[0].project == "test-project"
        assert pipelines[0].context == {"key": "value"}

    def test_returns_paused_pipeline(self, tmp_path):
        """Test returns pipeline with paused status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "paused",
            "pipeline": {"name": "Paused Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-456.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 1
        assert pipelines[0].status == "paused"

    def test_returns_waiting_human_gate_pipeline(self, tmp_path):
        """Test returns pipeline with waiting_human_gate status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "waiting_human_gate",
            "pipeline": {"name": "Waiting Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-789.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 1
        assert pipelines[0].status == "waiting_human_gate"

    def test_excludes_completed_by_default(self, tmp_path):
        """Test excludes completed pipelines by default."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "completed",
            "pipeline": {"name": "Completed Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {"step1": {"status": "completed"}},
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-completed.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 0

    def test_includes_completed_when_requested(self, tmp_path):
        """Test includes completed pipelines when requested."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "completed",
            "pipeline": {"name": "Completed Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {"step1": {"status": "completed"}},
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-completed.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines(include_completed=True)

        assert len(pipelines) == 1
        assert pipelines[0].status == "completed"
        assert pipelines[0].current_step == "completed"

    def test_excludes_failed_status(self, tmp_path):
        """Test excludes pipelines with failed status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "failed",
            "pipeline": {"name": "Failed Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
            "error": "Something went wrong",
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-failed.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 0

    def test_sorts_by_start_time_desc(self, tmp_path):
        """Test pipelines are sorted by start time (most recent first)."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        older_time = datetime.now(UTC) - timedelta(hours=2)
        newer_time = datetime.now(UTC) - timedelta(hours=1)

        older_data = {
            "status": "running",
            "pipeline": {"name": "Older Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": older_time.isoformat(),
        }

        newer_data = {
            "status": "running",
            "pipeline": {"name": "Newer Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": newer_time.isoformat(),
        }

        (manager.orchestration_dir / "pipeline-older.json").write_text(json.dumps(older_data))
        (manager.orchestration_dir / "pipeline-newer.json").write_text(json.dumps(newer_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 2
        assert pipelines[0].name == "Newer Pipeline"
        assert pipelines[1].name == "Older Pipeline"

    def test_handles_invalid_json(self, tmp_path, caplog):
        """Test handles invalid JSON gracefully."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        (manager.orchestration_dir / "invalid.json").write_text("not valid json")

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 0

    def test_handles_missing_fields(self, tmp_path):
        """Test handles checkpoints with missing fields."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        minimal_data = {
            "status": "running",
            "pipeline": {"steps": []},
        }

        (manager.orchestration_dir / "minimal.json").write_text(json.dumps(minimal_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 1
        assert pipelines[0].name == "minimal"
        assert pipelines[0].started_at is None

    def test_caching_behavior(self, tmp_path):
        """Test results are cached."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "running",
            "pipeline": {"name": "Test Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.orchestration_dir / "pipeline-123.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        # First call
        pipelines1 = manager.get_active_pipelines()

        # Modify file
        pipeline_data["pipeline"]["name"] = "Modified Pipeline"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        # Second call should return cached result
        pipelines2 = manager.get_active_pipelines()

        assert pipelines1[0].name == "Test Pipeline"
        assert pipelines2[0].name == "Test Pipeline"  # Still cached


class TestGetPipeline:
    """Test get_pipeline method."""

    def test_returns_none_when_not_found(self, tmp_path):
        """Test returns None when checkpoint doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline = manager.get_pipeline("nonexistent")

        assert pipeline is None

    def test_returns_specific_pipeline(self, tmp_path):
        """Test returns specific pipeline by ID."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "running",
            "pipeline": {"name": "Specific Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
            "domain": "test-domain",
        }

        checkpoint_file = manager.orchestration_dir / "specific-123.json"
        checkpoint_file.write_text(json.dumps(pipeline_data))

        pipeline = manager.get_pipeline("specific-123")

        assert pipeline is not None
        assert pipeline.checkpoint_id == "specific-123"
        assert pipeline.name == "Specific Pipeline"
        assert pipeline.domain == "test-domain"

    def test_handles_invalid_json(self, tmp_path, caplog):
        """Test handles invalid JSON gracefully."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        (manager.orchestration_dir / "invalid-456.json").write_text("not valid json")

        pipeline = manager.get_pipeline("invalid-456")

        assert pipeline is None


class TestGetRalphState:
    """Test get_ralph_state method."""

    def test_returns_default_when_dir_does_not_exist(self, tmp_path):
        """Test returns default idle state when ralph dir doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        state = manager.get_ralph_state()

        assert state.active is False
        assert state.state == "idle"
        assert state.current_feature is None
        assert state.iteration == 0
        assert state.max_iterations == 0
        assert state.failure_count == 0
        assert state.started_at is None

    def test_returns_default_when_no_checkpoints(self, tmp_path):
        """Test returns default state when no checkpoint files exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        state = manager.get_ralph_state()

        assert state.active is False
        assert state.state == "idle"

    def test_returns_running_state(self, tmp_path):
        """Test returns active running state."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        started_at = datetime.now(UTC)
        ralph_data = {
            "state": "running",
            "current_feature": "Feature X",
            "iteration": 5,
            "max_iterations": 100,
            "failure_count": 1,
            "started_at": started_at.isoformat(),
            "domain": "test-domain",
            "project": "test-project",
        }

        checkpoint_file = manager.ralph_dir / "ralph_checkpoint_123.json"
        checkpoint_file.write_text(json.dumps(ralph_data))

        state = manager.get_ralph_state()

        assert state.active is True
        assert state.state == "running"
        assert state.current_feature == "Feature X"
        assert state.iteration == 5
        assert state.max_iterations == 100
        assert state.failure_count == 1
        assert state.domain == "test-domain"
        assert state.project == "test-project"
        assert state.checkpoint_file == "ralph_checkpoint_123.json"

    def test_returns_implementing_state(self, tmp_path):
        """Test returns active implementing state."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        ralph_data = {
            "state": "implementing",
            "current_feature": "Feature Y",
            "iteration": 3,
            "max_iterations": 100,
            "failure_count": 0,
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.ralph_dir / "ralph_checkpoint_456.json"
        checkpoint_file.write_text(json.dumps(ralph_data))

        state = manager.get_ralph_state()

        assert state.active is True
        assert state.state == "implementing"

    def test_returns_testing_state(self, tmp_path):
        """Test returns active testing state."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        ralph_data = {
            "state": "testing",
            "current_feature": "Feature Z",
            "iteration": 10,
            "max_iterations": 100,
            "failure_count": 2,
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.ralph_dir / "ralph_checkpoint_789.json"
        checkpoint_file.write_text(json.dumps(ralph_data))

        state = manager.get_ralph_state()

        assert state.active is True
        assert state.state == "testing"

    def test_returns_waiting_approval_state(self, tmp_path):
        """Test returns active waiting_approval state."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        ralph_data = {
            "state": "waiting_approval",
            "current_feature": "Feature A",
            "iteration": 7,
            "max_iterations": 100,
            "failure_count": 0,
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = manager.ralph_dir / "ralph_checkpoint_abc.json"
        checkpoint_file.write_text(json.dumps(ralph_data))

        state = manager.get_ralph_state()

        assert state.active is True
        assert state.state == "waiting_approval"

    def test_returns_inactive_idle_state(self, tmp_path):
        """Test returns inactive state for idle."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        ralph_data = {
            "state": "idle",
            "current_feature": None,
            "iteration": 0,
            "max_iterations": 100,
            "failure_count": 0,
            "started_at": None,
        }

        checkpoint_file = manager.ralph_dir / "ralph_checkpoint_idle.json"
        checkpoint_file.write_text(json.dumps(ralph_data))

        state = manager.get_ralph_state()

        assert state.active is False
        assert state.state == "idle"

    def test_uses_most_recent_checkpoint(self, tmp_path):
        """Test uses the most recent checkpoint file."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        older_data = {
            "state": "running",
            "current_feature": "Old Feature",
            "iteration": 1,
            "max_iterations": 100,
            "failure_count": 0,
        }

        newer_data = {
            "state": "running",
            "current_feature": "New Feature",
            "iteration": 5,
            "max_iterations": 100,
            "failure_count": 1,
        }

        older_file = manager.ralph_dir / "ralph_checkpoint_old.json"
        newer_file = manager.ralph_dir / "ralph_checkpoint_new.json"

        older_file.write_text(json.dumps(older_data))
        time.sleep(0.01)  # Ensure different mtime
        newer_file.write_text(json.dumps(newer_data))

        state = manager.get_ralph_state()

        assert state.current_feature == "New Feature"
        assert state.iteration == 5

    def test_handles_invalid_json(self, tmp_path):
        """Test handles invalid JSON gracefully."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        (manager.ralph_dir / "invalid.json").write_text("not valid json")

        state = manager.get_ralph_state()

        assert state.active is False
        assert state.state == "idle"

    def test_handles_missing_fields(self, tmp_path):
        """Test handles checkpoints with missing fields."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        minimal_data = {"state": "running"}

        (manager.ralph_dir / "minimal.json").write_text(json.dumps(minimal_data))

        state = manager.get_ralph_state()

        assert state.active is True
        assert state.state == "running"
        assert state.current_feature is None
        assert state.iteration == 0
        assert state.max_iterations == 100

    def test_caching_behavior(self, tmp_path):
        """Test results are cached."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)
        manager.ralph_dir.mkdir()

        ralph_data = {
            "state": "running",
            "current_feature": "Original Feature",
            "iteration": 5,
            "max_iterations": 100,
            "failure_count": 0,
        }

        checkpoint_file = manager.ralph_dir / "ralph_checkpoint.json"
        checkpoint_file.write_text(json.dumps(ralph_data))

        # First call
        state1 = manager.get_ralph_state()

        # Modify file
        ralph_data["current_feature"] = "Modified Feature"
        checkpoint_file.write_text(json.dumps(ralph_data))

        # Second call should return cached result
        state2 = manager.get_ralph_state()

        assert state1.current_feature == "Original Feature"
        assert state2.current_feature == "Original Feature"  # Still cached


class TestGetPendingApprovals:
    """Test get_pending_approvals method."""

    def test_returns_empty_when_dir_does_not_exist(self, tmp_path):
        """Test returns empty list when approvals dir doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        approvals = manager.get_pending_approvals()

        assert approvals == []

    def test_returns_pending_approval(self, tmp_path):
        """Test returns approval with pending status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        created_at = datetime.now(UTC)
        approval_data = {
            "id": "approval-123",
            "type": "deployment",
            "title": "Deploy to prod",
            "description": "Deploy version 1.0.0",
            "domain": "test-domain",
            "priority": "high",
            "status": "pending",
            "tier": "PHONE",
            "risk_score": 0.7,
            "created_at": created_at.isoformat(),
            "metadata": {"version": "1.0.0"},
            "tags": ["production"],
        }

        approval_file = manager.approvals_dir / "approval_123.json"
        approval_file.write_text(json.dumps(approval_data))

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 1
        assert approvals[0].id == "approval-123"
        assert approvals[0].type == "deployment"
        assert approvals[0].title == "Deploy to prod"
        assert approvals[0].priority == "high"
        assert approvals[0].status == "pending"
        assert approvals[0].tier == "PHONE"
        assert approvals[0].risk_score == 0.7

    def test_filters_by_status(self, tmp_path):
        """Test filters approvals by status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        pending_data = {
            "id": "approval-pending",
            "type": "deployment",
            "title": "Pending Approval",
            "description": "",
            "domain": "test",
            "priority": "normal",
            "status": "pending",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
        }

        approved_data = {
            "id": "approval-approved",
            "type": "deployment",
            "title": "Approved Approval",
            "description": "",
            "domain": "test",
            "priority": "normal",
            "status": "approved",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
        }

        (manager.approvals_dir / "approval_pending.json").write_text(json.dumps(pending_data))
        (manager.approvals_dir / "approval_approved.json").write_text(json.dumps(approved_data))

        pending_approvals = manager.get_pending_approvals(status_filter="pending")
        approved_approvals = manager.get_pending_approvals(status_filter="approved")

        assert len(pending_approvals) == 1
        assert pending_approvals[0].status == "pending"

        assert len(approved_approvals) == 1
        assert approved_approvals[0].status == "approved"

    def test_returns_all_when_no_filter(self, tmp_path):
        """Test returns all approvals when status_filter is None."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        for status in ["pending", "approved", "rejected"]:
            approval_data = {
                "id": f"approval-{status}",
                "type": "deployment",
                "title": f"{status.title()} Approval",
                "description": "",
                "domain": "test",
                "priority": "normal",
                "status": status,
                "tier": "PHONE",
                "risk_score": 0.5,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (manager.approvals_dir / f"approval_{status}.json").write_text(
                json.dumps(approval_data)
            )

        approvals = manager.get_pending_approvals(status_filter=None)

        assert len(approvals) == 3

    def test_sorts_by_priority_then_created_at(self, tmp_path):
        """Test sorts approvals by priority order then creation time."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        base_time = datetime.now(UTC)
        priorities = [
            ("low", base_time),
            ("normal", base_time - timedelta(hours=1)),
            ("high", base_time - timedelta(hours=2)),
            ("critical", base_time - timedelta(hours=3)),
        ]

        for i, (priority, created_at) in enumerate(priorities):
            approval_data = {
                "id": f"approval-{i}",
                "type": "deployment",
                "title": f"{priority.title()} Approval",
                "description": "",
                "domain": "test",
                "priority": priority,
                "status": "pending",
                "tier": "PHONE",
                "risk_score": 0.5,
                "created_at": created_at.isoformat(),
            }
            (manager.approvals_dir / f"approval_{i}.json").write_text(json.dumps(approval_data))

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 4
        assert approvals[0].priority == "critical"
        assert approvals[1].priority == "high"
        assert approvals[2].priority == "normal"
        assert approvals[3].priority == "low"

    def test_limits_results(self, tmp_path):
        """Test limits number of results returned."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        for i in range(5):
            approval_data = {
                "id": f"approval-{i}",
                "type": "deployment",
                "title": f"Approval {i}",
                "description": "",
                "domain": "test",
                "priority": "normal",
                "status": "pending",
                "tier": "PHONE",
                "risk_score": 0.5,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (manager.approvals_dir / f"approval_{i}.json").write_text(json.dumps(approval_data))

        approvals = manager.get_pending_approvals(limit=3)

        assert len(approvals) == 3

    def test_handles_various_approval_types(self, tmp_path):
        """Test handles different approval types."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        types = ["deployment", "feature", "security", "config"]
        for i, approval_type in enumerate(types):
            approval_data = {
                "id": f"approval-{i}",
                "type": approval_type,
                "title": f"{approval_type.title()} Approval",
                "description": "",
                "domain": "test",
                "priority": "normal",
                "status": "pending",
                "tier": "PHONE",
                "risk_score": 0.5,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (manager.approvals_dir / f"approval_{i}.json").write_text(json.dumps(approval_data))

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 4
        approval_types = [a.type for a in approvals]
        assert set(approval_types) == {"deployment", "feature", "security", "config"}

    def test_handles_different_tiers(self, tmp_path):
        """Test handles different tier values."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        tiers = ["WATCH", "PHONE", "DESKTOP"]
        for i, tier in enumerate(tiers):
            approval_data = {
                "id": f"approval-{i}",
                "type": "deployment",
                "title": f"{tier} Approval",
                "description": "",
                "domain": "test",
                "priority": "normal",
                "status": "pending",
                "tier": tier,
                "risk_score": 0.5,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (manager.approvals_dir / f"approval_{i}.json").write_text(json.dumps(approval_data))

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 3
        approval_tiers = [a.tier for a in approvals]
        assert set(approval_tiers) == {"WATCH", "PHONE", "DESKTOP"}

    def test_handles_expires_at(self, tmp_path):
        """Test handles expires_at field."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(hours=24)

        approval_data = {
            "id": "approval-123",
            "type": "deployment",
            "title": "Expiring Approval",
            "description": "",
            "domain": "test",
            "priority": "normal",
            "status": "pending",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        (manager.approvals_dir / "approval_123.json").write_text(json.dumps(approval_data))

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 1
        assert approvals[0].expires_at is not None

    def test_handles_invalid_json(self, tmp_path):
        """Test handles invalid JSON gracefully."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        (manager.approvals_dir / "approval_invalid.json").write_text("not valid json")

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 0

    def test_handles_missing_fields(self, tmp_path):
        """Test handles approvals with missing fields."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        minimal_data = {
            "status": "pending",
        }

        (manager.approvals_dir / "approval_minimal.json").write_text(json.dumps(minimal_data))

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 1
        assert approvals[0].id == "approval_minimal"
        assert approvals[0].type == "unknown"
        assert approvals[0].title == "Untitled"

    def test_caching_behavior(self, tmp_path):
        """Test results are cached."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)
        manager.approvals_dir.mkdir()

        approval_data = {
            "id": "approval-123",
            "type": "deployment",
            "title": "Original Title",
            "description": "",
            "domain": "test",
            "priority": "normal",
            "status": "pending",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
        }

        approval_file = manager.approvals_dir / "approval_123.json"
        approval_file.write_text(json.dumps(approval_data))

        # First call
        approvals1 = manager.get_pending_approvals()

        # Modify file
        approval_data["title"] = "Modified Title"
        approval_file.write_text(json.dumps(approval_data))

        # Second call should return cached result
        approvals2 = manager.get_pending_approvals()

        assert approvals1[0].title == "Original Title"
        assert approvals2[0].title == "Original Title"  # Still cached


class TestGetApproval:
    """Test get_approval method."""

    def test_returns_none_when_not_found(self, tmp_path):
        """Test returns None when approval doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        approval = manager.get_approval("nonexistent")

        assert approval is None

    def test_returns_none_when_dir_not_exists(self, tmp_path):
        """Test returns None when approvals dir doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)

        approval = manager.get_approval("nonexistent")

        assert approval is None

    def test_returns_specific_approval(self, tmp_path):
        """Test returns specific approval by ID."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        approval_data = {
            "id": "approval-123",
            "type": "deployment",
            "title": "Specific Approval",
            "description": "Test description",
            "domain": "test-domain",
            "priority": "high",
            "status": "pending",
            "tier": "PHONE",
            "risk_score": 0.7,
            "created_at": datetime.now(UTC).isoformat(),
        }

        # The glob pattern in get_approval is *{approval_id}*.json
        # So for "approval-123" it will glob "*approval-123*.json"
        # We need a filename that matches this pattern
        (manager.approvals_dir / "approval_approval-123_data.json").write_text(
            json.dumps(approval_data)
        )

        approval = manager.get_approval("approval-123")

        assert approval is not None
        assert approval.id == "approval-123"
        assert approval.title == "Specific Approval"


class TestGetApprovalCount:
    """Test get_approval_count method."""

    def test_returns_count_of_pending(self, tmp_path):
        """Test returns count of pending approvals."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        for i in range(3):
            approval_data = {
                "id": f"approval-{i}",
                "type": "deployment",
                "title": f"Approval {i}",
                "description": "",
                "domain": "test",
                "priority": "normal",
                "status": "pending",
                "tier": "PHONE",
                "risk_score": 0.5,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (manager.approvals_dir / f"approval_{i}.json").write_text(json.dumps(approval_data))

        count = manager.get_approval_count("pending")

        assert count == 3

    def test_returns_count_by_status(self, tmp_path):
        """Test returns count filtered by status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        for i, status in enumerate(["pending", "approved", "approved"]):
            approval_data = {
                "id": f"approval-{i}",
                "type": "deployment",
                "title": f"Approval {i}",
                "description": "",
                "domain": "test",
                "priority": "normal",
                "status": status,
                "tier": "PHONE",
                "risk_score": 0.5,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (manager.approvals_dir / f"approval_{i}.json").write_text(json.dumps(approval_data))

        pending_count = manager.get_approval_count("pending")
        approved_count = manager.get_approval_count("approved")

        assert pending_count == 1
        assert approved_count == 2


class TestGetActiveSessions:
    """Test get_active_sessions method."""

    def test_returns_empty_when_dir_does_not_exist(self, tmp_path):
        """Test returns empty list when sessions dir doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        sessions = manager.get_active_sessions()

        assert sessions == []

    def test_returns_active_session(self, tmp_path):
        """Test returns session with active status."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        started_at = datetime.now(UTC)
        session_data = {
            "session_id": "session-123",
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "started_at": started_at.isoformat(),
            "last_activity": started_at.isoformat(),
            "completed_tasks": ["task1", "task2"],
            "pending_tasks": ["task3"],
            "context": {"key": "value"},
        }

        session_file = manager.sessions_dir / "session_123.json"
        session_file.write_text(json.dumps(session_data))

        sessions = manager.get_active_sessions()

        assert len(sessions) == 1
        assert sessions[0].session_id == "session-123"
        assert sessions[0].domain == "test-domain"
        assert sessions[0].project == "test-project"
        assert sessions[0].status == "active"
        assert sessions[0].completed_tasks == ["task1", "task2"]
        assert sessions[0].pending_tasks == ["task3"]

    def test_filters_by_domain(self, tmp_path):
        """Test filters sessions by domain."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        for i, domain in enumerate(["domain-a", "domain-b"]):
            session_data = {
                "session_id": f"session-{i}",
                "domain": domain,
                "project": "test-project",
                "status": "active",
                "started_at": datetime.now(UTC).isoformat(),
            }
            (manager.sessions_dir / f"session_{i}.json").write_text(json.dumps(session_data))

        sessions = manager.get_active_sessions(domain="domain-a")

        assert len(sessions) == 1
        assert sessions[0].domain == "domain-a"

    def test_filters_by_project(self, tmp_path):
        """Test filters sessions by project."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        for i, project in enumerate(["project-x", "project-y"]):
            session_data = {
                "session_id": f"session-{i}",
                "domain": "test-domain",
                "project": project,
                "status": "active",
                "started_at": datetime.now(UTC).isoformat(),
            }
            (manager.sessions_dir / f"session_{i}.json").write_text(json.dumps(session_data))

        sessions = manager.get_active_sessions(project="project-x")

        assert len(sessions) == 1
        assert sessions[0].project == "project-x"

    def test_includes_recent_sessions(self, tmp_path):
        """Test includes recent non-active sessions."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        recent_time = datetime.now(UTC) - timedelta(hours=2)
        session_data = {
            "session_id": "session-recent",
            "domain": "test-domain",
            "project": "test-project",
            "status": "completed",
            "started_at": recent_time.isoformat(),
            "last_activity": recent_time.isoformat(),
        }

        (manager.sessions_dir / "session_recent.json").write_text(json.dumps(session_data))

        sessions = manager.get_active_sessions(include_recent=True, recent_hours=24)

        assert len(sessions) == 1
        assert sessions[0].status == "completed"

    def test_excludes_old_sessions(self, tmp_path):
        """Test excludes sessions older than recent_hours."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        old_time = datetime.now(UTC) - timedelta(hours=48)
        session_data = {
            "session_id": "session-old",
            "domain": "test-domain",
            "project": "test-project",
            "status": "completed",
            "started_at": old_time.isoformat(),
            "last_activity": old_time.isoformat(),
        }

        (manager.sessions_dir / "session_old.json").write_text(json.dumps(session_data))

        sessions = manager.get_active_sessions(include_recent=True, recent_hours=24)

        assert len(sessions) == 0

    def test_sorts_by_last_activity(self, tmp_path):
        """Test sorts sessions by last activity time."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        older_time = datetime.now(UTC) - timedelta(hours=2)
        newer_time = datetime.now(UTC) - timedelta(hours=1)

        older_data = {
            "session_id": "session-older",
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "started_at": older_time.isoformat(),
            "last_activity": older_time.isoformat(),
        }

        newer_data = {
            "session_id": "session-newer",
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "started_at": newer_time.isoformat(),
            "last_activity": newer_time.isoformat(),
        }

        (manager.sessions_dir / "session_older.json").write_text(json.dumps(older_data))
        (manager.sessions_dir / "session_newer.json").write_text(json.dumps(newer_data))

        sessions = manager.get_active_sessions()

        assert len(sessions) == 2
        assert sessions[0].session_id == "session-newer"
        assert sessions[1].session_id == "session-older"

    def test_handles_invalid_json(self, tmp_path):
        """Test handles invalid JSON gracefully."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        (manager.sessions_dir / "session_invalid.json").write_text("not valid json")

        sessions = manager.get_active_sessions()

        assert len(sessions) == 0

    def test_caching_behavior(self, tmp_path):
        """Test results are cached."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)
        manager.sessions_dir.mkdir()

        session_data = {
            "session_id": "session-123",
            "domain": "test-domain",
            "project": "original-project",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }

        session_file = manager.sessions_dir / "session_123.json"
        session_file.write_text(json.dumps(session_data))

        # First call
        sessions1 = manager.get_active_sessions()

        # Modify file
        session_data["project"] = "modified-project"
        session_file.write_text(json.dumps(session_data))

        # Second call should return cached result
        sessions2 = manager.get_active_sessions()

        assert sessions1[0].project == "original-project"
        assert sessions2[0].project == "original-project"  # Still cached


class TestGetSession:
    """Test get_session method."""

    def test_returns_none_when_not_found(self, tmp_path):
        """Test returns None when session doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        session = manager.get_session("nonexistent")

        assert session is None

    def test_returns_specific_session(self, tmp_path):
        """Test returns specific session by ID."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        session_data = {
            "session_id": "session-123",
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }

        (manager.sessions_dir / "session_session-123.json").write_text(json.dumps(session_data))

        session = manager.get_session("session-123")

        assert session is not None
        assert session.session_id == "session-123"

    def test_finds_by_partial_match(self, tmp_path):
        """Test finds session by partial ID match."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        session_data = {
            "session_id": "full-session-id-123",
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }

        (manager.sessions_dir / "session_full-session-id-123.json").write_text(
            json.dumps(session_data)
        )

        session = manager.get_session("session-id")

        assert session is not None
        assert session.session_id == "full-session-id-123"


class TestSaveSession:
    """Test save_session method."""

    def test_saves_session_successfully(self, tmp_path):
        """Test saves session to file."""
        manager = CheckpointManager(forge_root=tmp_path)

        session = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=datetime.now(UTC),
            completed_tasks=["task1"],
            pending_tasks=["task2"],
            context={"key": "value"},
        )

        result = manager.save_session(session)

        assert result is True
        assert (manager.sessions_dir / "session_test-session.json").exists()

    def test_creates_directory_if_not_exists(self, tmp_path):
        """Test creates sessions directory if it doesn't exist."""
        manager = CheckpointManager(forge_root=tmp_path)

        assert not manager.sessions_dir.exists()

        session = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=datetime.now(UTC),
        )

        result = manager.save_session(session)

        assert result is True
        assert manager.sessions_dir.exists()

    def test_saved_data_is_correct(self, tmp_path):
        """Test saved session data is correct."""
        manager = CheckpointManager(forge_root=tmp_path)

        started_at = datetime.now(UTC)
        session = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=started_at,
            completed_tasks=["task1"],
            pending_tasks=["task2"],
            context={"key": "value"},
        )

        manager.save_session(session)

        saved_data = json.loads((manager.sessions_dir / "session_test-session.json").read_text())

        assert saved_data["session_id"] == "test-session"
        assert saved_data["domain"] == "test-domain"
        assert saved_data["project"] == "test-project"
        assert saved_data["status"] == "active"
        assert saved_data["completed_tasks"] == ["task1"]
        assert saved_data["pending_tasks"] == ["task2"]
        assert saved_data["context"] == {"key": "value"}

    def test_invalidates_cache(self, tmp_path):
        """Test invalidates cache after save."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)

        # Pre-populate cache
        manager._set_cached("test_key", "test_value")
        assert manager._get_cached("test_key") == "test_value"

        session = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=datetime.now(UTC),
        )

        manager.save_session(session)

        # Cache should be cleared
        assert manager._get_cached("test_key") is None

    def test_updates_existing_session(self, tmp_path):
        """Test updates existing session file."""
        manager = CheckpointManager(forge_root=tmp_path)

        session1 = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="original-project",
            status="active",
            started_at=datetime.now(UTC),
        )

        manager.save_session(session1)

        session2 = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="updated-project",
            status="completed",
            started_at=datetime.now(UTC),
        )

        manager.save_session(session2)

        saved_data = json.loads((manager.sessions_dir / "session_test-session.json").read_text())

        assert saved_data["project"] == "updated-project"
        assert saved_data["status"] == "completed"


class TestCaching:
    """Test caching behavior."""

    def test_get_cached_returns_none_when_empty(self, tmp_path):
        """Test _get_cached returns None when cache is empty."""
        manager = CheckpointManager(forge_root=tmp_path)

        result = manager._get_cached("nonexistent")

        assert result is None

    def test_get_cached_returns_value_when_valid(self, tmp_path):
        """Test _get_cached returns value when still valid."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)

        manager._set_cached("test_key", "test_value")
        result = manager._get_cached("test_key")

        assert result == "test_value"

    def test_get_cached_returns_none_when_expired(self, tmp_path):
        """Test _get_cached returns None when cache expired."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=0.01)

        manager._set_cached("test_key", "test_value")
        time.sleep(0.02)
        result = manager._get_cached("test_key")

        assert result is None

    def test_invalidate_cache_clears_all(self, tmp_path):
        """Test invalidate_cache clears all entries."""
        manager = CheckpointManager(forge_root=tmp_path)

        manager._set_cached("key1", "value1")
        manager._set_cached("key2", "value2")

        manager.invalidate_cache()

        assert manager._get_cached("key1") is None
        assert manager._get_cached("key2") is None

    def test_invalidate_cache_clears_specific_key(self, tmp_path):
        """Test invalidate_cache clears specific key."""
        manager = CheckpointManager(forge_root=tmp_path)

        manager._set_cached("key1", "value1")
        manager._set_cached("key2", "value2")

        manager.invalidate_cache("key1")

        assert manager._get_cached("key1") is None
        assert manager._get_cached("key2") == "value2"


class TestGetSummary:
    """Test get_summary method."""

    def test_returns_summary_with_counts(self, tmp_path):
        """Test returns summary with all counts."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()
        manager.ralph_dir.mkdir()
        manager.approvals_dir.mkdir()
        manager.sessions_dir.mkdir()

        # Create some test data
        pipeline_data = {
            "status": "running",
            "pipeline": {"name": "Test", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
        }
        (manager.orchestration_dir / "pipeline.json").write_text(json.dumps(pipeline_data))

        approval_data = {
            "id": "approval-1",
            "type": "deployment",
            "title": "Test",
            "description": "",
            "domain": "test",
            "priority": "critical",
            "status": "pending",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
        }
        (manager.approvals_dir / "approval_1.json").write_text(json.dumps(approval_data))

        summary = manager.get_summary()

        assert "pipelines" in summary
        assert "ralph" in summary
        assert "approvals" in summary
        assert "sessions" in summary
        assert "timestamp" in summary

    def test_summary_has_correct_structure(self, tmp_path):
        """Test summary has correct nested structure."""
        manager = CheckpointManager(forge_root=tmp_path)

        summary = manager.get_summary()

        assert "active" in summary["pipelines"]
        assert "paused" in summary["pipelines"]
        assert "waiting_gate" in summary["pipelines"]
        assert "total" in summary["pipelines"]

        assert "active" in summary["ralph"]
        assert "state" in summary["ralph"]

        assert "pending" in summary["approvals"]
        assert "critical" in summary["approvals"]
        assert "high" in summary["approvals"]

        assert "active" in summary["sessions"]
        assert "recent" in summary["sessions"]

    def test_summary_is_cached(self, tmp_path):
        """Test summary is cached."""
        manager = CheckpointManager(forge_root=tmp_path, cache_ttl=10.0)

        summary1 = manager.get_summary()
        summary2 = manager.get_summary()

        # Should return same timestamp (cached)
        assert summary1["timestamp"] == summary2["timestamp"]


class TestSingletonPattern:
    """Test singleton pattern with get_checkpoint_manager."""

    def test_returns_same_instance(self, tmp_path, monkeypatch):
        """Test get_checkpoint_manager returns same instance."""
        reset_checkpoint_manager()
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))

        manager1 = get_checkpoint_manager()
        manager2 = get_checkpoint_manager()

        assert manager1 is manager2

    def test_uses_forge_root_from_first_call(self, tmp_path):
        """Test uses forge_root from first call only."""
        reset_checkpoint_manager()

        manager1 = get_checkpoint_manager(forge_root=tmp_path)
        manager2 = get_checkpoint_manager(forge_root="/different/path")

        assert manager1 is manager2
        assert manager1.forge_root == tmp_path

    def test_uses_cache_ttl_from_first_call(self, tmp_path):
        """Test uses cache_ttl from first call only."""
        reset_checkpoint_manager()

        manager1 = get_checkpoint_manager(forge_root=tmp_path, cache_ttl=5.0)
        manager2 = get_checkpoint_manager(forge_root=tmp_path, cache_ttl=10.0)

        assert manager1 is manager2
        assert manager1._cache_ttl == 5.0

    def test_reset_checkpoint_manager(self, tmp_path):
        """Test reset_checkpoint_manager clears singleton."""
        reset_checkpoint_manager()

        manager1 = get_checkpoint_manager(forge_root=tmp_path)
        reset_checkpoint_manager()
        manager2 = get_checkpoint_manager(forge_root=tmp_path)

        assert manager1 is not manager2


class TestErrorHandling:
    """Test error handling for various scenarios."""

    def test_handles_corrupt_pipeline_checkpoint(self, tmp_path, caplog):
        """Test handles corrupt pipeline checkpoint."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        (manager.orchestration_dir / "corrupt.json").write_text("{invalid json")

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 0

    def test_handles_corrupt_ralph_checkpoint(self, tmp_path):
        """Test handles corrupt Ralph checkpoint."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.ralph_dir.mkdir()

        (manager.ralph_dir / "corrupt.json").write_text("{invalid json")

        state = manager.get_ralph_state()

        assert state.active is False
        assert state.state == "idle"

    def test_handles_corrupt_approval(self, tmp_path):
        """Test handles corrupt approval file."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.approvals_dir.mkdir()

        (manager.approvals_dir / "approval_corrupt.json").write_text("{invalid json")

        approvals = manager.get_pending_approvals()

        assert len(approvals) == 0

    def test_handles_corrupt_session(self, tmp_path):
        """Test handles corrupt session file."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.sessions_dir.mkdir()

        (manager.sessions_dir / "session_corrupt.json").write_text("{invalid json")

        sessions = manager.get_active_sessions()

        assert len(sessions) == 0

    def test_handles_invalid_datetime_format(self, tmp_path):
        """Test handles invalid datetime format."""
        manager = CheckpointManager(forge_root=tmp_path)
        manager.orchestration_dir.mkdir()

        pipeline_data = {
            "status": "running",
            "pipeline": {"name": "Test", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": "not a valid datetime",
        }

        (manager.orchestration_dir / "pipeline.json").write_text(json.dumps(pipeline_data))

        pipelines = manager.get_active_pipelines()

        assert len(pipelines) == 1
        assert pipelines[0].started_at is None

    def test_handles_permission_error_on_save(self, tmp_path, monkeypatch):
        """Test handles permission error when saving session."""
        manager = CheckpointManager(forge_root=tmp_path)

        session = SessionInfo(
            session_id="test-session",
            domain="test-domain",
            project="test-project",
            status="active",
            started_at=datetime.now(UTC),
        )

        # Make write fail
        def mock_write_text(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "write_text", mock_write_text)

        result = manager.save_session(session)

        assert result is False

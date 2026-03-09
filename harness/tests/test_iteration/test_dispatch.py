"""Tests for Delegation Dispatcher (HRN-013).

Tests cover:
- Tmux session creation
- Task prompt generation
- Session tagging with metadata
- Task status tracking
- Agent selection and fallback
- Error handling
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.iteration.dispatch import (
    AgentType,
    DispatchResult,
    TaskTracker,
    TmuxDispatcher,
    dispatch_task,
    dispatch_tasks,
    get_dispatch_status,
    get_fallback_agent,
    select_agent_for_task,
)
from forge_harness.iteration.prioritizer import PrioritizedTask

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_task():
    """Create a sample PrioritizedTask."""
    return PrioritizedTask(
        feature_id="HRN-001",
        title="Task agent parallel dispatch",
        score=8.5,
        impact=9,
        urgency=8,
        effort=3,
        domain_weight=1.2,
        reasoning="High impact feature for multi-agent execution",
        status="pending",
        epic="Multi-Agent Executor",
        acceptance_criteria=[
            "Can dispatch 2-5 Task agents in parallel",
            "Each agent receives isolated task definition",
        ],
        test_command="pytest tests/test_parallel_dispatch.py -v",
        metadata={"priority": "high"},
    )


@pytest.fixture
def features_file(tmp_path):
    """Create a sample features.json file."""
    features_data = {
        "version": "2.0",
        "features": [
            {
                "id": "HRN-001",
                "title": "Task agent parallel dispatch",
                "description": "Dispatch N Task agents concurrently",
                "status": "pending",
                "priority": "high",
                "epic": "Multi-Agent Executor",
                "acceptance_criteria": [
                    "Can dispatch 2-5 Task agents in parallel",
                    "Each agent receives isolated task definition",
                ],
                "test_command": "pytest tests/test_parallel_dispatch.py -v",
            },
            {
                "id": "HRN-002",
                "title": "Codex CLI integration",
                "description": "Integrate codex exec",
                "status": "pending",
                "priority": "high",
                "epic": "Multi-Agent Executor",
            },
        ],
    }

    features_path = tmp_path / "features.json"
    with open(features_path, "w") as f:
        json.dump(features_data, f, indent=2)

    return features_path


@pytest.fixture
def mock_tmux():
    """Mock tmux commands."""
    with patch("subprocess.run") as mock_run:
        # Default: successful tmux commands
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock_run


# ============================================================================
# TmuxDispatcher Tests
# ============================================================================


class TestTmuxDispatcher:
    """Test TmuxDispatcher class."""

    def test_create_session_success(self, tmp_path, mock_tmux):
        """Test successful session creation."""
        dispatcher = TmuxDispatcher(tmp_path)

        # Mock _session_exists to return False
        with patch.object(dispatcher, "_session_exists", return_value=False):
            session_id = dispatcher.create_session("HRN-001", AgentType.BACKEND, tmp_path)

        assert session_id == "forge-task-HRN-001"

        # Verify tmux command was called correctly
        mock_tmux.assert_called_once()
        call_args = mock_tmux.call_args[0][0]
        assert call_args[0] == "tmux"
        assert call_args[1] == "new-session"
        assert call_args[3] == "-s"
        assert call_args[4] == "forge-task-HRN-001"

    def test_create_session_already_exists(self, tmp_path, mock_tmux):
        """Test handling of existing session."""
        dispatcher = TmuxDispatcher(tmp_path)

        # Mock: first call checks if session exists (returns 0 = exists)
        # Second call tries to create (not reached)
        mock_tmux.return_value.returncode = 0

        # Patch _session_exists to return True
        with patch.object(dispatcher, "_session_exists", return_value=True):
            session_id = dispatcher.create_session("HRN-001", AgentType.BACKEND, tmp_path)

        assert session_id is None

    def test_create_session_failure(self, tmp_path, mock_tmux):
        """Test handling of tmux failure."""
        dispatcher = TmuxDispatcher(tmp_path)

        # Mock: tmux command fails
        mock_tmux.side_effect = subprocess.CalledProcessError(1, "tmux", stderr=b"Error")

        # Mock _session_exists to return False
        with patch.object(dispatcher, "_session_exists", return_value=False):
            session_id = dispatcher.create_session("HRN-001", AgentType.BACKEND, tmp_path)

        assert session_id is None

    def test_send_command_success(self, tmp_path, mock_tmux):
        """Test sending command to session."""
        dispatcher = TmuxDispatcher(tmp_path)

        result = dispatcher.send_command("forge-task-HRN-001", "echo 'Hello'")

        assert result is True
        mock_tmux.assert_called_once()
        call_args = mock_tmux.call_args[0][0]
        assert call_args[0] == "tmux"
        assert call_args[1] == "send-keys"
        assert call_args[3] == "forge-task-HRN-001"
        assert call_args[4] == "echo 'Hello'"

    def test_send_command_failure(self, tmp_path, mock_tmux):
        """Test handling of send command failure."""
        dispatcher = TmuxDispatcher(tmp_path)

        mock_tmux.side_effect = subprocess.CalledProcessError(1, "tmux", stderr=b"Error")

        result = dispatcher.send_command("forge-task-HRN-001", "echo 'Hello'")

        assert result is False

    def test_tag_session_success(self, tmp_path, mock_tmux):
        """Test tagging session with metadata."""
        dispatcher = TmuxDispatcher(tmp_path)

        tags = {"task_id": "HRN-001", "agent_type": "backend-engineer"}

        result = dispatcher.tag_session("forge-task-HRN-001", tags)

        assert result is True
        assert mock_tmux.call_count == 2  # One call per tag

    def test_get_session_output(self, tmp_path, mock_tmux):
        """Test capturing session output."""
        dispatcher = TmuxDispatcher(tmp_path)

        # Mock output
        mock_tmux.return_value.stdout = "Task output\nLine 2\nLine 3"

        output = dispatcher.get_session_output("forge-task-HRN-001", lines=50)

        assert "Task output" in output
        mock_tmux.assert_called_once()
        call_args = mock_tmux.call_args[0][0]
        assert call_args[1] == "capture-pane"


# ============================================================================
# TaskTracker Tests
# ============================================================================


class TestTaskTracker:
    """Test TaskTracker class."""

    def test_save_and_load_dispatch(self, tmp_path):
        """Test saving and loading dispatch results."""
        tracking_dir = tmp_path / ".forge/dispatch"
        tracker = TaskTracker(tracking_dir)

        result = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
            metadata={"epic": "Multi-Agent Executor"},
        )

        # Save
        tracker.save_dispatch("HRN-001", result)

        # Load
        loaded = tracker.load_dispatch("HRN-001")

        assert loaded is not None
        assert loaded.task_id == "HRN-001"
        assert loaded.session_id == "forge-task-HRN-001"
        assert loaded.agent_type == AgentType.BACKEND
        assert loaded.status == "in_progress"
        assert loaded.success is True

    def test_load_nonexistent_dispatch(self, tmp_path):
        """Test loading non-existent dispatch."""
        tracking_dir = tmp_path / ".forge/dispatch"
        tracker = TaskTracker(tracking_dir)

        loaded = tracker.load_dispatch("NONEXISTENT")

        assert loaded is None

    def test_update_status(self, tmp_path):
        """Test updating task status."""
        tracking_dir = tmp_path / ".forge/dispatch"
        tracker = TaskTracker(tracking_dir)

        result = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
        )

        tracker.save_dispatch("HRN-001", result)
        tracker.update_status("HRN-001", "completed")

        loaded = tracker.load_dispatch("HRN-001")
        assert loaded.status == "completed"


# ============================================================================
# Agent Selection Tests
# ============================================================================


class TestAgentSelection:
    """Test agent selection logic."""

    def test_select_agent_backend_task(self, sample_task):
        """Test selecting backend agent for backend tasks."""
        sample_task.title = "API endpoint implementation"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.BACKEND

    def test_select_agent_frontend_task(self, sample_task):
        """Test selecting frontend agent for frontend tasks."""
        sample_task.title = "React component development"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.FRONTEND

    def test_select_agent_test_task(self, sample_task):
        """Test selecting QA agent for test tasks."""
        sample_task.title = "E2E test implementation"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.QA

    def test_select_agent_debug_task(self, sample_task):
        """Test selecting debug agent for debug tasks."""
        sample_task.title = "Fix authentication error"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.DEBUG

    def test_select_agent_content_task(self, sample_task):
        """Test selecting content agent for content tasks."""
        sample_task.title = "Blog post content generation"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.CONTENT

    def test_select_agent_explicit_type(self, sample_task):
        """Test selecting agent from explicit metadata."""
        sample_task.metadata["agent_type"] = "qa-tester"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.QA

    def test_select_agent_default(self, sample_task):
        """Test default agent selection for unknown tasks."""
        sample_task.title = "Unknown task type"

        agent = select_agent_for_task(sample_task)

        assert agent == AgentType.BACKEND

    def test_get_fallback_agent_backend(self):
        """Test getting fallback for backend agent."""
        fallback = get_fallback_agent(AgentType.BACKEND)

        assert fallback in [AgentType.EXTERNAL_OPENCODE, AgentType.EXTERNAL_CODEX, AgentType.CLAUDE]

    def test_get_fallback_agent_frontend(self):
        """Test getting fallback for frontend agent."""
        fallback = get_fallback_agent(AgentType.FRONTEND)

        assert fallback in [AgentType.EXTERNAL_CURSOR, AgentType.CLAUDE]


# ============================================================================
# Dispatch Task Tests
# ============================================================================


class TestDispatchTask:
    """Test dispatch_task function."""

    def test_dispatch_task_success(self, tmp_path, features_file, mock_tmux):
        """Test successful task dispatch."""
        # Mock successful tmux operations
        mock_tmux.return_value = MagicMock(returncode=0, stdout="", stderr=b"")

        # Mock get_fleet_status and _session_exists to avoid actual tmux calls
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch(
                "forge_harness.iteration.dispatch.TmuxDispatcher._session_exists",
                return_value=False,
            ):
                result = dispatch_task("HRN-001", features_file, tmp_path)

        assert result.success is True
        assert result.task_id == "HRN-001"
        assert result.session_id == "forge-task-HRN-001"
        assert result.agent_type is not None
        assert result.status == "in_progress"
        assert result.dispatched_at is not None

    def test_dispatch_task_not_found(self, tmp_path, features_file):
        """Test dispatching non-existent task."""
        result = dispatch_task("NONEXISTENT", features_file, tmp_path)

        assert result.success is False
        assert result.status == "not_found"
        assert result.error is not None

    def test_dispatch_task_session_creation_failure(self, tmp_path, features_file, mock_tmux):
        """Test handling of session creation failure."""
        # Mock: session creation fails
        mock_tmux.side_effect = subprocess.CalledProcessError(1, "tmux", stderr=b"Error")

        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch(
                "forge_harness.iteration.dispatch.TmuxDispatcher._session_exists",
                return_value=False,
            ):
                result = dispatch_task("HRN-001", features_file, tmp_path)

        assert result.success is False
        assert result.status == "failed"

    def test_dispatch_task_with_force_agent(self, tmp_path, features_file, mock_tmux):
        """Test dispatching with forced agent type."""
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch(
                "forge_harness.iteration.dispatch.TmuxDispatcher._session_exists",
                return_value=False,
            ):
                result = dispatch_task("HRN-001", features_file, tmp_path, force_agent=AgentType.QA)

        assert result.success is True
        assert result.agent_type == AgentType.QA

    def test_dispatch_task_saves_tracking(self, tmp_path, features_file, mock_tmux):
        """Test that dispatch saves tracking file."""
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch(
                "forge_harness.iteration.dispatch.TmuxDispatcher._session_exists",
                return_value=False,
            ):
                result = dispatch_task("HRN-001", features_file, tmp_path)

        tracking_file = tmp_path / ".forge/dispatch" / "HRN-001.json"
        assert tracking_file.exists()

        with open(tracking_file) as f:
            data = json.load(f)

        assert data["task_id"] == "HRN-001"
        assert data["session_id"] == "forge-task-HRN-001"
        assert data["status"] == "in_progress"


# ============================================================================
# Dispatch Multiple Tasks Tests
# ============================================================================


class TestDispatchTasks:
    """Test dispatch_tasks function."""

    def test_dispatch_multiple_tasks(self, tmp_path, features_file, mock_tmux):
        """Test dispatching multiple tasks."""
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch("time.sleep"):  # Skip delays in tests
                results = dispatch_tasks(["HRN-001", "HRN-002"], features_file, tmp_path)

        assert len(results) == 2
        assert results[0].task_id == "HRN-001"
        assert results[1].task_id == "HRN-002"

    def test_dispatch_respects_max_parallel(self, tmp_path, features_file, mock_tmux):
        """Test that dispatch respects max_parallel limit."""
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch("time.sleep"):
                results = dispatch_tasks(
                    ["HRN-001", "HRN-002", "HRN-003"], features_file, tmp_path, max_parallel=2
                )

        # Should only dispatch first 2 tasks
        assert len(results) == 2

    def test_dispatch_empty_list(self, tmp_path, features_file):
        """Test dispatching empty task list."""
        results = dispatch_tasks([], features_file, tmp_path)

        assert len(results) == 0


# ============================================================================
# Status Query Tests
# ============================================================================


class TestGetDispatchStatus:
    """Test get_dispatch_status function."""

    def test_get_dispatch_status_exists(self, tmp_path, features_file, mock_tmux):
        """Test getting status for dispatched task."""
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch(
                "forge_harness.iteration.dispatch.TmuxDispatcher._session_exists",
                return_value=False,
            ):
                dispatch_task("HRN-001", features_file, tmp_path)

        status = get_dispatch_status("HRN-001", tmp_path)

        assert status is not None
        assert status.task_id == "HRN-001"

    def test_get_dispatch_status_not_found(self, tmp_path):
        """Test getting status for non-existent task."""
        status = get_dispatch_status("NONEXISTENT", tmp_path)

        assert status is None


# ============================================================================
# Integration Tests
# ============================================================================


class TestDispatchIntegration:
    """Integration tests for dispatch workflow."""

    def test_full_dispatch_workflow(self, tmp_path, features_file, mock_tmux):
        """Test complete dispatch workflow."""
        # 1. Dispatch task
        with patch("forge_harness.iteration.dispatch.get_fleet_status", return_value=None):
            with patch(
                "forge_harness.iteration.dispatch.TmuxDispatcher._session_exists",
                return_value=False,
            ):
                result = dispatch_task("HRN-001", features_file, tmp_path)

        assert result.success is True

        # 2. Check status
        status = get_dispatch_status("HRN-001", tmp_path)
        assert status is not None
        assert status.status == "in_progress"

        # 3. Update status
        tracker = TaskTracker(tmp_path / ".forge/dispatch")
        tracker.update_status("HRN-001", "completed")

        # 4. Verify update
        updated_status = get_dispatch_status("HRN-001", tmp_path)
        assert updated_status.status == "completed"

    def test_dispatch_result_to_dict(self):
        """Test DispatchResult serialization."""
        result = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )

        data = result.to_dict()

        assert data["success"] is True
        assert data["task_id"] == "HRN-001"
        assert data["agent_type"] == "backend-engineer"
        assert "dispatched_at" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

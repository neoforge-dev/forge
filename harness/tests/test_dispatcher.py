"""
Tests for forge_harness.iteration.dispatcher module.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.dispatcher import (
    AgentType,
    DispatchError,
    DispatchResult,
    DispatchStatus,
    TaskNotFoundError,
    _build_prompt,
    _create_session,
    _load_task,
    _send_to_session,
    dispatch_task,
    select_agent,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_features():
    """Sample features.json data."""
    return {
        "version": "2.0",
        "features": [
            {
                "id": "HRN-001",
                "title": "Task agent parallel dispatch",
                "description": "Dispatch N Task agents concurrently via tmux sessions",
                "status": "pending",
                "priority": "high",
                "acceptance_criteria": [
                    "Can dispatch 2-5 agents in parallel",
                    "Each agent runs in separate tmux session",
                    "Agent type auto-selected from keywords",
                ],
                "test_command": "pytest tests/test_dispatcher.py -v",
            },
            {
                "id": "HRN-002",
                "title": "Backend API endpoint",
                "description": "Build FastAPI endpoint for user authentication",
                "status": "pending",
                "priority": "high",
                "acceptance_criteria": [
                    "POST /auth/login endpoint",
                    "JWT token generation",
                ],
            },
            {
                "id": "HRN-003",
                "title": "Frontend component",
                "description": "Create React component for login form",
                "status": "pending",
                "priority": "medium",
                "acceptance_criteria": ["Email and password fields", "Submit button"],
            },
            {
                "id": "HRN-004",
                "title": "Research task",
                "description": "Research best practices for API rate limiting",
                "status": "pending",
                "priority": "low",
            },
        ],
    }


@pytest.fixture
def temp_features_file(tmp_path, sample_features):
    """Create temporary features.json file."""
    features_path = tmp_path / "features.json"
    with open(features_path, "w") as f:
        json.dump(sample_features, f)
    return features_path


# =============================================================================
# Test Enums
# =============================================================================


def test_agent_type_enum():
    """AgentType enum has correct values."""
    assert AgentType.OPENCODE.value == "opencode"
    assert AgentType.CODEX.value == "codex"
    assert AgentType.GEMINI.value == "gemini"
    assert AgentType.CLAUDE.value == "claude"


def test_agent_type_command_template():
    """AgentType has correct command templates."""
    assert AgentType.OPENCODE.command_template == "opencode run '{prompt}'"
    assert AgentType.CODEX.command_template == "codex exec '{prompt}'"
    assert AgentType.GEMINI.command_template == "gemini -y '{prompt}'"
    assert AgentType.CLAUDE.command_template == "claude '{prompt}'"


def test_dispatch_status_enum():
    """DispatchStatus enum has correct values."""
    assert DispatchStatus.PENDING.value == "pending"
    assert DispatchStatus.RUNNING.value == "running"
    assert DispatchStatus.COMPLETED.value == "completed"
    assert DispatchStatus.FAILED.value == "failed"


# =============================================================================
# Test DispatchResult Dataclass
# =============================================================================


def test_dispatch_result_dataclass():
    """DispatchResult has correct structure."""
    result = DispatchResult(
        task_id="HRN-001",
        session_id="opencode-HRN-001",
        agent_type=AgentType.OPENCODE,
        started_at=datetime.now(),
        status=DispatchStatus.RUNNING,
        prompt="Test prompt",
        working_directory="/path/to/project",
    )

    assert result.task_id == "HRN-001"
    assert result.session_id == "opencode-HRN-001"
    assert result.agent_type == AgentType.OPENCODE
    assert result.status == DispatchStatus.RUNNING
    assert result.prompt == "Test prompt"
    assert result.working_directory == "/path/to/project"


def test_dispatch_result_to_dict():
    """DispatchResult.to_dict() returns correct dictionary."""
    now = datetime.now()
    result = DispatchResult(
        task_id="HRN-001",
        session_id="opencode-HRN-001",
        agent_type=AgentType.OPENCODE,
        started_at=now,
        status=DispatchStatus.RUNNING,
        prompt="Test prompt",
    )

    data = result.to_dict()

    assert data["task_id"] == "HRN-001"
    assert data["session_id"] == "opencode-HRN-001"
    assert data["agent_type"] == "opencode"
    assert data["started_at"] == now.isoformat()
    assert data["status"] == "running"
    assert data["prompt"] == "Test prompt"


def test_dispatch_result_from_dict():
    """DispatchResult.from_dict() creates correct instance."""
    data = {
        "task_id": "HRN-001",
        "session_id": "opencode-HRN-001",
        "agent_type": "opencode",
        "started_at": "2025-01-29T12:00:00+00:00",
        "status": "running",
        "prompt": "Test prompt",
        "working_directory": "/path",
    }

    result = DispatchResult.from_dict(data)

    assert result.task_id == "HRN-001"
    assert result.agent_type == AgentType.OPENCODE
    assert result.status == DispatchStatus.RUNNING
    assert result.working_directory == "/path"


def test_dispatch_result_json_serialization():
    """DispatchResult can be serialized to/from JSON."""
    result = DispatchResult(
        task_id="HRN-001",
        session_id="opencode-HRN-001",
        agent_type=AgentType.OPENCODE,
        started_at=datetime.now(),
        status=DispatchStatus.RUNNING,
        prompt="Test prompt",
    )

    # Serialize to JSON
    json_str = result.to_json()
    assert isinstance(json_str, str)

    # Deserialize from JSON
    result2 = DispatchResult.from_json(json_str)
    assert result2.task_id == result.task_id
    assert result2.agent_type == result.agent_type


# =============================================================================
# Test Helper Functions
# =============================================================================


def test_load_task_finds_task(temp_features_file):
    """_load_task finds task by ID."""
    task = _load_task("HRN-001", temp_features_file)

    assert task["id"] == "HRN-001"
    assert task["title"] == "Task agent parallel dispatch"
    assert "description" in task


def test_load_task_raises_not_found(temp_features_file):
    """_load_task raises TaskNotFoundError for missing ID."""
    with pytest.raises(TaskNotFoundError) as exc_info:
        _load_task("HRN-999", temp_features_file)

    assert "HRN-999" in str(exc_info.value)


def test_load_task_raises_file_not_found():
    """_load_task raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        _load_task("HRN-001", Path("/nonexistent/features.json"))


def test_build_prompt_includes_criteria():
    """_build_prompt includes acceptance criteria."""
    task = {
        "title": "Test task",
        "description": "Test description",
        "acceptance_criteria": ["Criterion 1", "Criterion 2"],
        "test_command": "pytest tests/",
    }

    prompt = _build_prompt(task)

    assert "Test task" in prompt
    assert "Test description" in prompt
    assert "Criterion 1" in prompt
    assert "Criterion 2" in prompt
    assert "pytest tests/" in prompt


def test_build_prompt_handles_string_criteria():
    """_build_prompt handles string acceptance criteria."""
    task = {
        "title": "Test task",
        "acceptance_criteria": "Single criterion string",
    }

    prompt = _build_prompt(task)

    assert "Single criterion string" in prompt


def test_build_prompt_handles_missing_fields():
    """_build_prompt handles missing optional fields."""
    task = {"title": "Minimal task"}

    prompt = _build_prompt(task)

    assert "Minimal task" in prompt
    # Should not crash with missing fields


# =============================================================================
# Test Agent Selection
# =============================================================================


def test_select_agent_backend():
    """select_agent selects opencode for backend tasks."""
    task = {"title": "Backend API", "description": "Build FastAPI endpoint"}
    assert select_agent(task) == AgentType.OPENCODE


def test_select_agent_frontend():
    """select_agent selects codex for frontend tasks."""
    task = {"title": "Frontend component", "description": "Create React UI"}
    assert select_agent(task) == AgentType.CODEX


def test_select_agent_test():
    """select_agent selects codex for test tasks."""
    task = {"title": "Test coverage", "description": "Add pytest tests"}
    assert select_agent(task) == AgentType.CODEX


def test_select_agent_research():
    """select_agent selects gemini for research tasks."""
    task = {"title": "Research task", "description": "Analyze best practices"}
    assert select_agent(task) == AgentType.GEMINI


def test_select_agent_default():
    """select_agent defaults to codex for unknown tasks."""
    task = {"title": "Generic task", "description": "Do something"}
    assert select_agent(task) == AgentType.CODEX


# =============================================================================
# Test tmux Session Management
# =============================================================================


@patch("forge_harness.iteration.dispatcher.subprocess.run")
def test_create_session_new(mock_run):
    """_create_session creates new tmux session."""
    # First call (has-session) returns non-zero (session doesn't exist)
    # Second call (new-session) returns zero (success)
    mock_run.side_effect = [
        Mock(returncode=1),  # has-session fails
        Mock(returncode=0),  # new-session succeeds
    ]

    result = _create_session("test-session", "/path/to/project")

    assert result is True
    assert mock_run.call_count == 2

    # Verify has-session call
    assert mock_run.call_args_list[0][0][0] == [
        "tmux",
        "has-session",
        "-t",
        "test-session",
    ]

    # Verify new-session call
    assert mock_run.call_args_list[1][0][0][:4] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
    ]


@patch("forge_harness.iteration.dispatcher.subprocess.run")
def test_create_session_exists(mock_run):
    """_create_session returns True if session already exists."""
    # has-session returns zero (session exists)
    mock_run.return_value = Mock(returncode=0)

    result = _create_session("test-session")

    assert result is True
    assert mock_run.call_count == 1


@patch("forge_harness.iteration.dispatcher.subprocess.run")
def test_create_session_timeout(mock_run):
    """_create_session raises on timeout."""
    mock_run.side_effect = subprocess.TimeoutExpired("tmux", 30)

    with pytest.raises(subprocess.TimeoutExpired):
        _create_session("test-session", timeout=30)


@patch("forge_harness.iteration.dispatcher.subprocess.run")
def test_create_session_handles_missing_tmux(mock_run):
    """_create_session handles missing tmux gracefully."""
    mock_run.side_effect = FileNotFoundError()

    result = _create_session("test-session")

    assert result is False


@patch("forge_harness.iteration.dispatcher.subprocess.run")
def test_send_to_session_success(mock_run):
    """_send_to_session sends command successfully."""
    mock_run.return_value = Mock(returncode=0)

    result = _send_to_session("test-session", "test prompt", AgentType.OPENCODE, timeout=30)

    assert result is True

    # Verify send-keys call
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "send-keys"
    assert call_args[2] == "-t"
    assert call_args[3] == "test-session"
    assert "opencode run" in call_args[4]
    assert call_args[5] == "Enter"


@patch("forge_harness.iteration.dispatcher.subprocess.run")
def test_send_to_session_timeout(mock_run):
    """_send_to_session raises on timeout."""
    mock_run.side_effect = subprocess.TimeoutExpired("tmux", 30)

    with pytest.raises(subprocess.TimeoutExpired):
        _send_to_session("test-session", "prompt", AgentType.CODEX, timeout=30)


# =============================================================================
# Test Main dispatch_task Function
# =============================================================================


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_success(mock_create, mock_send, temp_features_file, sample_features):
    """dispatch_task successfully dispatches a task."""
    mock_create.return_value = True
    mock_send.return_value = True

    result = dispatch_task("HRN-001", features_path=temp_features_file)

    assert result.task_id == "HRN-001"
    assert result.status == DispatchStatus.RUNNING
    assert "parallel dispatch" in result.prompt.lower()

    # Verify session creation
    mock_create.assert_called_once()
    assert "HRN-001" in mock_create.call_args[0][0]

    # Verify prompt sent
    mock_send.assert_called_once()


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_backend(mock_create, mock_send, temp_features_file):
    """dispatch_task selects opencode for backend task."""
    mock_create.return_value = True
    mock_send.return_value = True

    result = dispatch_task("HRN-002", features_path=temp_features_file)

    assert result.agent_type == AgentType.OPENCODE
    assert "opencode-HRN-002" in result.session_id


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_frontend(mock_create, mock_send, temp_features_file):
    """dispatch_task selects codex for frontend task."""
    mock_create.return_value = True
    mock_send.return_value = True

    result = dispatch_task("HRN-003", features_path=temp_features_file)

    assert result.agent_type == AgentType.CODEX
    assert "codex-HRN-003" in result.session_id


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_research(mock_create, mock_send, temp_features_file):
    """dispatch_task selects gemini for research task."""
    mock_create.return_value = True
    mock_send.return_value = True

    result = dispatch_task("HRN-004", features_path=temp_features_file)

    assert result.agent_type == AgentType.GEMINI
    assert "gemini-HRN-004" in result.session_id


def test_dispatch_task_raises_task_not_found(temp_features_file):
    """dispatch_task raises TaskNotFoundError for missing task."""
    with pytest.raises(TaskNotFoundError):
        dispatch_task("HRN-999", features_path=temp_features_file)


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_raises_on_session_failure(mock_create, mock_send, temp_features_file):
    """dispatch_task raises DispatchError when session creation fails."""
    mock_create.return_value = False

    with pytest.raises(DispatchError) as exc_info:
        dispatch_task("HRN-001", features_path=temp_features_file)

    assert "Failed to create tmux session" in str(exc_info.value)


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_raises_on_send_failure(mock_create, mock_send, temp_features_file):
    """dispatch_task raises DispatchError when send fails."""
    mock_create.return_value = True
    mock_send.return_value = False

    with pytest.raises(DispatchError) as exc_info:
        dispatch_task("HRN-001", features_path=temp_features_file)

    assert "Failed to send prompt to session" in str(exc_info.value)


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_with_working_dir(mock_create, mock_send, temp_features_file):
    """dispatch_task passes working directory to session."""
    mock_create.return_value = True
    mock_send.return_value = True

    result = dispatch_task(
        "HRN-001", features_path=temp_features_file, working_dir="/path/to/project"
    )

    assert result.working_directory == "/path/to/project"

    # Verify working_dir passed to create_session
    mock_create.assert_called_once()
    assert mock_create.call_args[0][1] == "/path/to/project"


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_accepts_path_object(mock_create, mock_send, temp_features_file):
    """dispatch_task accepts Path object for features_path."""
    mock_create.return_value = True
    mock_send.return_value = True

    # Pass as Path object
    result = dispatch_task("HRN-001", features_path=temp_features_file)

    assert result.task_id == "HRN-001"


@patch("forge_harness.iteration.dispatcher._send_to_session")
@patch("forge_harness.iteration.dispatcher._create_session")
def test_dispatch_task_accepts_string_path(mock_create, mock_send, temp_features_file):
    """dispatch_task accepts string for features_path."""
    mock_create.return_value = True
    mock_send.return_value = True

    # Pass as string
    result = dispatch_task("HRN-001", features_path=str(temp_features_file))

    assert result.task_id == "HRN-001"

"""Tests for fleet priority dispatch system."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.fleet.priority_dispatch import (
    AGENT_CAPABILITIES,
    AgentStatus,
    DispatchResult,
    dispatch_task,
    get_idle_agents,
    get_pending_tasks,
    log_dispatch,
    match_task_to_agent,
    update_heartbeat,
)
from forge_harness.iteration.prioritizer import PrioritizedTask

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_task() -> PrioritizedTask:
    """Create a sample prioritized task."""
    return PrioritizedTask(
        task_id="TEST-001",
        title="Implement user authentication API",
        score=8.5,
        impact=9.0,
        urgency=8.0,
        effort=3.0,
        domain_weight=1.5,
        reasoning="High priority backend task",
        dependencies=[],
        status="pending",
        acceptance_criteria=[
            "POST /api/auth/login endpoint works",
            "JWT tokens are issued correctly",
            "Password hashing is secure",
        ],
        test_command="pytest tests/test_auth.py",
        metadata={
            "domain": "neoforge-dev",
            "priority": "high",
            "description": "Backend API authentication system",
        },
    )


@pytest.fixture
def idle_tech_agent() -> AgentStatus:
    """Create an idle tech agent."""
    return AgentStatus(
        name="tech",
        is_idle=True,
        last_output="Type your message...",
        capabilities=AGENT_CAPABILITIES["tech"],
    )


@pytest.fixture
def idle_qa_agent() -> AgentStatus:
    """Create an idle QA agent."""
    return AgentStatus(
        name="qa",
        is_idle=True,
        last_output="Command: _",
        capabilities=AGENT_CAPABILITIES["qa"],
    )


@pytest.fixture
def temp_forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root with sample features.json files."""
    forge_root = tmp_path / "FORGE"
    forge_root.mkdir()

    # Create sample features.json files
    domain1 = forge_root / "neoforge-dev" / "test-project"
    domain1.mkdir(parents=True)

    features1 = {
        "features": [
            {
                "id": "NF-001",
                "title": "Backend API endpoint",
                "status": "pending",
                "priority": "high",
                "acceptance_criteria": ["API responds correctly"],
                "dependencies": [],
                "estimated_tokens": 5000,
            },
            {
                "id": "NF-002",
                "title": "Frontend component",
                "status": "complete",
                "priority": "medium",
                "acceptance_criteria": [],
                "dependencies": [],
                "estimated_tokens": 3000,
            },
        ]
    }

    (domain1 / "features.json").write_text(json.dumps(features1))

    domain2 = forge_root / "harness" / "test-tool"
    domain2.mkdir(parents=True)

    features2 = {
        "features": [
            {
                "id": "HRN-001",
                "title": "Test coverage improvement",
                "status": "pending",
                "priority": "medium",
                "acceptance_criteria": ["Coverage above 80%"],
                "dependencies": [],
                "estimated_tokens": 8000,
            }
        ]
    }

    (domain2 / "features.json").write_text(json.dumps(features2))

    return forge_root


# =============================================================================
# Test get_pending_tasks
# =============================================================================


def test_get_pending_tasks(temp_forge_root: Path) -> None:
    """Test getting pending tasks from portfolio."""
    tasks = get_pending_tasks(temp_forge_root)

    # Should find 2 pending tasks (NF-001 and HRN-001)
    assert len(tasks) == 2

    # Check they're sorted by priority score
    assert tasks[0].score >= tasks[1].score

    # Verify task structure
    assert all(hasattr(t, "feature_id") for t in tasks)
    assert all(hasattr(t, "title") for t in tasks)
    assert all(hasattr(t, "score") for t in tasks)


def test_get_pending_tasks_empty_portfolio(tmp_path: Path) -> None:
    """Test getting tasks from empty portfolio."""
    empty_root = tmp_path / "empty_forge"
    empty_root.mkdir()

    tasks = get_pending_tasks(empty_root)
    assert len(tasks) == 0


def test_get_pending_tasks_filters_completed(temp_forge_root: Path) -> None:
    """Test that completed tasks are filtered out."""
    tasks = get_pending_tasks(temp_forge_root)

    # NF-002 is complete and should be filtered
    task_ids = [t.feature_id for t in tasks]
    assert "NF-002" not in task_ids
    assert "NF-001" in task_ids


# =============================================================================
# Test get_idle_agents
# =============================================================================


@patch("forge_harness.fleet.priority_dispatch.subprocess.run")
def test_get_idle_agents_identifies_idle(mock_run: MagicMock) -> None:
    """Test identifying idle agents from tmux output."""
    # Mock tmux outputs
    outputs = {
        "forge:tech": "Type your message...\ncontext left: 50%",
        "forge:qa": "Working on feature implementation...",
        "forge:game": "$ _",  # Shell prompt
        "forge:codex": "Composing response...",
        "forge:gemini": "INSERT mode",
        "forge:kimi": "Running tests...",
    }

    def mock_tmux_capture(args, **kwargs):
        window = args[3]  # -t parameter
        output = outputs.get(window, "")
        result = MagicMock()
        result.returncode = 0
        result.stdout = output
        return result

    mock_run.side_effect = mock_tmux_capture

    idle_agents = get_idle_agents()

    # Should identify tech, game, and gemini as idle
    idle_names = {a.name for a in idle_agents}
    assert "tech" in idle_names
    assert "game" in idle_names
    assert "gemini" in idle_names

    # QA, codex, and kimi should be busy
    assert "qa" not in idle_names
    assert "codex" not in idle_names
    assert "kimi" not in idle_names


@patch("forge_harness.fleet.priority_dispatch.subprocess.run")
def test_get_idle_agents_handles_missing_window(mock_run: MagicMock) -> None:
    """Test handling when tmux window doesn't exist."""

    def mock_tmux_capture(args, **kwargs):
        result = MagicMock()
        result.returncode = 1  # Error
        result.stdout = ""
        return result

    mock_run.side_effect = mock_tmux_capture

    idle_agents = get_idle_agents()

    # Should return empty list when all windows fail
    assert len(idle_agents) == 0


# =============================================================================
# Test match_task_to_agent
# =============================================================================


def test_match_task_to_agent_domain_match(
    sample_task: PrioritizedTask, idle_tech_agent: AgentStatus
) -> None:
    """Test task matching based on domain."""
    agents = [idle_tech_agent]

    # Task is in neoforge-dev domain, tech agent handles neoforge-dev
    matched = match_task_to_agent(sample_task, agents)

    assert matched is not None
    assert matched.name == "tech"


def test_match_task_to_agent_keyword_match(idle_qa_agent: AgentStatus) -> None:
    """Test task matching based on keywords."""
    # Create a testing-focused task
    task = PrioritizedTask(
        task_id="TEST-002",
        title="Add test coverage for authentication",
        score=7.0,
        impact=6.0,
        urgency=7.0,
        effort=2.0,
        domain_weight=1.0,
        reasoning="QA task",
        metadata={
            "domain": "codeswiftr-com",
            "description": "Testing and validation of auth module",
        },
    )

    agents = [idle_qa_agent]
    matched = match_task_to_agent(task, agents)

    assert matched is not None
    assert matched.name == "qa"


def test_match_task_to_agent_fallback(sample_task: PrioritizedTask) -> None:
    """Test fallback to first available agent when no strong match."""
    # Create an agent with no matching capabilities
    unrelated_agent = AgentStatus(
        name="unrelated",
        is_idle=True,
        last_output="Ready",
        capabilities={"domains": ["unrelated-domain"], "keywords": []},
    )

    agents = [unrelated_agent]
    matched = match_task_to_agent(sample_task, agents)

    # Should still match (fallback behavior)
    assert matched is not None
    assert matched.name == "unrelated"


def test_match_task_to_agent_no_agents(sample_task: PrioritizedTask) -> None:
    """Test matching with no available agents."""
    matched = match_task_to_agent(sample_task, [])
    assert matched is None


# =============================================================================
# Test dispatch_task
# =============================================================================


@patch("forge_harness.fleet.priority_dispatch.subprocess.run")
def test_dispatch_task_success(
    mock_run: MagicMock, sample_task: PrioritizedTask, idle_tech_agent: AgentStatus
) -> None:
    """Test successful task dispatch."""
    mock_run.return_value = MagicMock(returncode=0)

    result = dispatch_task(idle_tech_agent, sample_task)

    assert result.success
    assert result.agent == "tech"
    assert result.task.feature_id == "TEST-001"
    assert "Successfully dispatched" in result.message

    # Verify tmux send-keys was called correctly
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "tmux"
    assert args[1] == "send-keys"
    assert args[3] == "forge:tech"


@patch("forge_harness.fleet.priority_dispatch.subprocess.run")
def test_dispatch_task_failure(
    mock_run: MagicMock, sample_task: PrioritizedTask, idle_tech_agent: AgentStatus
) -> None:
    """Test failed task dispatch."""
    import subprocess

    mock_run.side_effect = subprocess.CalledProcessError(1, "tmux")

    result = dispatch_task(idle_tech_agent, sample_task)

    assert not result.success
    assert result.agent == "tech"
    assert "tmux send-keys failed" in result.message


# =============================================================================
# Test log_dispatch
# =============================================================================


def test_log_dispatch(tmp_path: Path, sample_task: PrioritizedTask) -> None:
    """Test logging dispatch results."""
    from datetime import UTC, datetime

    log_file = tmp_path / "dispatch.log"

    result = DispatchResult(
        success=True,
        agent="tech",
        task=sample_task,
        message="Test dispatch",
        timestamp=datetime.now(UTC),
    )

    log_dispatch(result, log_file)

    # Verify log file created
    assert log_file.exists()

    # Verify log content
    log_content = log_file.read_text()
    assert "SUCCESS" in log_content
    assert "tech" in log_content
    assert "TEST-001" in log_content


def test_log_dispatch_creates_directory(tmp_path: Path, sample_task: PrioritizedTask) -> None:
    """Test that log_dispatch creates parent directories."""
    from datetime import UTC, datetime

    log_file = tmp_path / "deep" / "nested" / "path" / "dispatch.log"

    result = DispatchResult(
        success=False,
        agent="qa",
        task=sample_task,
        message="Test failure",
        timestamp=datetime.now(UTC),
    )

    log_dispatch(result, log_file)

    assert log_file.exists()
    assert "FAILED" in log_file.read_text()


# =============================================================================
# Test update_heartbeat
# =============================================================================


def test_update_heartbeat_creates_file(tmp_path: Path) -> None:
    """Test heartbeat file creation."""
    update_heartbeat(tmp_path)

    heartbeat_file = tmp_path / ".forge" / "heartbeat" / "forge.json"
    assert heartbeat_file.exists()

    data = json.loads(heartbeat_file.read_text())
    assert data["heartbeat_count"] == 1
    assert "timestamp" in data


def test_update_heartbeat_increments(tmp_path: Path) -> None:
    """Test heartbeat count increments."""
    # First update
    update_heartbeat(tmp_path)

    # Second update
    update_heartbeat(tmp_path)

    heartbeat_file = tmp_path / ".forge" / "heartbeat" / "forge.json"
    data = json.loads(heartbeat_file.read_text())
    assert data["heartbeat_count"] == 2


def test_update_heartbeat_preserves_existing(tmp_path: Path) -> None:
    """Test that heartbeat update preserves existing data."""
    heartbeat_file = tmp_path / ".forge" / "heartbeat" / "forge.json"
    heartbeat_file.parent.mkdir(parents=True)

    # Write initial data
    initial_data = {
        "heartbeat_count": 10,
        "custom_field": "preserved",
    }
    heartbeat_file.write_text(json.dumps(initial_data))

    # Update
    update_heartbeat(tmp_path)

    # Verify count incremented and custom field preserved
    data = json.loads(heartbeat_file.read_text())
    assert data["heartbeat_count"] == 11
    assert data["custom_field"] == "preserved"


# =============================================================================
# Integration Tests
# =============================================================================


def test_agent_capabilities_coverage() -> None:
    """Test that all expected agents are defined."""
    expected_agents = ["tech", "qa", "game", "codex", "gemini", "kimi"]

    for agent in expected_agents:
        assert agent in AGENT_CAPABILITIES
        assert "domains" in AGENT_CAPABILITIES[agent]
        assert "keywords" in AGENT_CAPABILITIES[agent]


def test_dispatch_instruction_format(sample_task: PrioritizedTask) -> None:
    """Test that dispatch instructions are well-formatted."""
    from forge_harness.fleet.priority_dispatch import _format_task_instruction

    instruction = _format_task_instruction(sample_task)

    # Should include key information
    assert "TEST-001" in instruction
    assert sample_task.title in instruction
    assert "high" in instruction.lower() or "8.5" in instruction

    # Should include some acceptance criteria
    assert "POST /api/auth/login" in instruction or "Acceptance Criteria" in instruction

    # Should include test command
    assert "pytest tests/test_auth.py" in instruction or "Test:" in instruction


# =============================================================================
# Test Dispatch Cooldown
# =============================================================================


def test_is_recently_dispatched_empty_cache() -> None:
    """Test cooldown check with empty cache."""
    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        is_recently_dispatched,
    )

    # Clear cache
    _recently_dispatched.clear()

    # Should return False for unknown task
    assert not is_recently_dispatched("UNKNOWN-001")


def test_mark_task_dispatched() -> None:
    """Test marking a task as dispatched."""
    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        is_recently_dispatched,
        mark_task_dispatched,
    )

    # Clear cache
    _recently_dispatched.clear()

    task_id = "TEST-DISPATCH-001"

    # Mark as dispatched
    mark_task_dispatched(task_id)

    # Should be in cooldown
    assert is_recently_dispatched(task_id)

    # Should be in cache
    assert task_id in _recently_dispatched


def test_cooldown_expiration() -> None:
    """Test that cooldown expires after specified time."""
    from datetime import timedelta

    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        is_recently_dispatched,
        mark_task_dispatched,
    )

    # Clear cache
    _recently_dispatched.clear()

    task_id = "TEST-EXPIRE-001"

    # Mark as dispatched
    mark_task_dispatched(task_id)

    # Should be in cooldown
    assert is_recently_dispatched(task_id, cooldown_minutes=30)

    # Simulate expired cooldown by backdating
    from datetime import UTC, datetime

    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=31)

    # Should NOT be in cooldown anymore
    assert not is_recently_dispatched(task_id, cooldown_minutes=30)

    # Should be removed from cache
    assert task_id not in _recently_dispatched


def test_cleanup_expired_cooldowns() -> None:
    """Test cleanup of expired cooldown entries."""
    from datetime import UTC, datetime, timedelta

    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        cleanup_expired_cooldowns,
    )

    # Clear cache
    _recently_dispatched.clear()

    # Add some entries
    now = datetime.now(UTC)
    _recently_dispatched["TASK-001"] = now  # Fresh
    _recently_dispatched["TASK-002"] = now - timedelta(minutes=25)  # Still valid
    _recently_dispatched["TASK-003"] = now - timedelta(minutes=31)  # Expired
    _recently_dispatched["TASK-004"] = now - timedelta(minutes=60)  # Expired

    # Run cleanup
    removed = cleanup_expired_cooldowns(cooldown_minutes=30)

    # Should remove 2 expired entries
    assert removed == 2

    # Should keep only fresh entries
    assert "TASK-001" in _recently_dispatched
    assert "TASK-002" in _recently_dispatched
    assert "TASK-003" not in _recently_dispatched
    assert "TASK-004" not in _recently_dispatched


def test_cooldown_prevents_duplicate_dispatch(sample_task: PrioritizedTask) -> None:
    """Test that cooldown prevents re-dispatching the same task."""
    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        is_recently_dispatched,
        mark_task_dispatched,
    )

    # Clear cache
    _recently_dispatched.clear()

    task_id = sample_task.feature_id

    # First dispatch - should be allowed
    assert not is_recently_dispatched(task_id)
    mark_task_dispatched(task_id)

    # Second dispatch attempt - should be blocked
    assert is_recently_dispatched(task_id)


def test_cooldown_custom_duration() -> None:
    """Test cooldown with custom duration."""
    from datetime import UTC, datetime, timedelta

    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        is_recently_dispatched,
        mark_task_dispatched,
    )

    # Clear cache
    _recently_dispatched.clear()

    task_id = "TEST-CUSTOM-001"

    # Mark as dispatched
    mark_task_dispatched(task_id)

    # Should be in cooldown with short duration
    assert is_recently_dispatched(task_id, cooldown_minutes=5)

    # Backdate to 10 minutes ago
    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=10)

    # Should NOT be in cooldown with short duration
    assert not is_recently_dispatched(task_id, cooldown_minutes=5)

    # Should still be in cooldown with longer duration
    # Re-add for this check
    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=10)
    assert is_recently_dispatched(task_id, cooldown_minutes=15)


def test_multiple_tasks_cooldown() -> None:
    """Test cooldown tracking for multiple tasks."""
    from forge_harness.fleet.priority_dispatch import (
        _recently_dispatched,
        is_recently_dispatched,
        mark_task_dispatched,
    )

    # Clear cache
    _recently_dispatched.clear()

    tasks = ["TASK-A", "TASK-B", "TASK-C"]

    # Mark all as dispatched
    for task_id in tasks:
        mark_task_dispatched(task_id)

    # All should be in cooldown
    for task_id in tasks:
        assert is_recently_dispatched(task_id)

    # Cache should have all tasks
    assert len(_recently_dispatched) == 3

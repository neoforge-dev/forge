"""Integration tests for forge tasks CLI."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli


@pytest.fixture
def runner():
    return CliRunner()

@pytest.fixture
def mock_client():
    with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_cls:
        client = MagicMock()
        mock_cls.from_env.return_value = client
        yield client

def test_forge_tasks_workflow(runner, mock_client):
    """Test a full workflow of listing, creating, and showing a task."""

    # 1. List tasks (empty)
    mock_client.sync_list_tasks.return_value = []
    result = runner.invoke(cli, ["tasks", "list"])
    assert result.exit_code == 0
    assert "No tasks found" in result.output or "No tasks" in result.output

    # 2. Create a task
    mock_client.sync_create_task.return_value = {
        "id": "task-123",
        "subject": "Test Integration Task",
        "status": "pending"
    }
    result = runner.invoke(cli, ["tasks", "create", "Test Integration Task", "--description", "Integration test description"])
    assert result.exit_code == 0
    assert "Created task task-123" in result.output

    # 3. Show the task
    mock_client.sync_get_task.return_value = {
        "id": "task-123",
        "subject": "Test Integration Task",
        "description": "Integration test description",
        "status": "pending",
        "priority": "medium",
        "claimed_by": None
    }
    result = runner.invoke(cli, ["tasks", "show", "task-123"])
    assert result.exit_code == 0
    assert "task-123" in result.output
    assert "Test Integration Task" in result.output

    # 4. List tasks (now one task)
    mock_client.sync_list_tasks.return_value = [
        {"id": "task-123", "subject": "Test Integration Task", "status": "pending", "priority": "medium", "claimed_by": None}
    ]
    result = runner.invoke(cli, ["tasks", "list"])
    assert result.exit_code == 0
    assert "task-123" in result.output

def test_forge_tasks_recommended(runner, mock_client):
    """Test getting recommended tasks."""
    mock_client.sync_get_recommended_tasks.return_value = [
        {"id": "rec-001", "subject": "Recommended Task", "score": 0.95}
    ]
    result = runner.invoke(cli, ["tasks", "recommended"])
    assert result.exit_code == 0
    assert "rec-001" in result.output
    assert "Recommended Task" in result.output

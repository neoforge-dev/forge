"""Tests for task dependency system.

Covers:
- list_ready_tasks: no deps → ready, unmet deps → blocked, all deps met → ready
- Cycle detection: A→B→A should flag both as blocked
- CLI forge tasks ready command
- CLI forge tasks create --depends-on
- Edge cases: depends on nonexistent task, depends on self
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.task_queue import Task, TaskPriority, TaskQueue, TaskStatus

# ===========================================================================
# TaskQueueHandler.list_ready_tasks unit tests
# ===========================================================================


class TestListReadyTasks:
    """Tests for the handler's list_ready_tasks method."""

    @pytest.fixture
    def tmp_queue(self, tmp_path):
        return TaskQueue(tmp_path / "tasks")

    @pytest.fixture
    def handler(self, tmp_queue):
        from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

        return TaskQueueHandler(state_store=None, task_queue=tmp_queue)

    @pytest.mark.asyncio
    async def test_no_deps_are_ready(self, handler, tmp_queue):
        """Tasks with no dependencies should be ready."""
        await tmp_queue.add(Task(
            id="T1", title="Task 1", description="No deps",
            status=TaskStatus.PENDING,
        ))
        await tmp_queue.add(Task(
            id="T2", title="Task 2", description="No deps either",
            status=TaskStatus.PENDING,
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T1" in ready_ids
        assert "T2" in ready_ids
        assert result["blocked_count"] == 0

    @pytest.mark.asyncio
    async def test_unmet_deps_are_blocked(self, handler, tmp_queue):
        """Tasks with unmet dependencies should be blocked."""
        await tmp_queue.add(Task(
            id="T1", title="Setup auth", description="Auth setup",
            status=TaskStatus.PENDING,
        ))
        await tmp_queue.add(Task(
            id="T2", title="Deploy API", description="Needs auth",
            status=TaskStatus.PENDING, depends_on=["T1"],
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T1" in ready_ids
        assert "T2" not in ready_ids
        assert result["blocked_count"] == 1

    @pytest.mark.asyncio
    async def test_met_deps_are_ready(self, handler, tmp_queue):
        """Tasks whose dependencies are all completed should be ready."""
        await tmp_queue.add(Task(
            id="T1", title="Setup auth", description="Auth setup",
            status=TaskStatus.COMPLETED,
        ))
        await tmp_queue.add(Task(
            id="T2", title="Deploy API", description="Needs auth",
            status=TaskStatus.PENDING, depends_on=["T1"],
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T2" in ready_ids
        assert result["blocked_count"] == 0

    @pytest.mark.asyncio
    async def test_cycle_detection(self, handler, tmp_queue):
        """Tasks in a dependency cycle should be blocked."""
        await tmp_queue.add(Task(
            id="A", title="Task A", description="Depends on B",
            status=TaskStatus.PENDING, depends_on=["B"],
        ))
        await tmp_queue.add(Task(
            id="B", title="Task B", description="Depends on A",
            status=TaskStatus.PENDING, depends_on=["A"],
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "A" not in ready_ids
        assert "B" not in ready_ids
        assert result["blocked_count"] == 2

    @pytest.mark.asyncio
    async def test_depends_on_nonexistent(self, handler, tmp_queue):
        """Task depending on a nonexistent task should be blocked."""
        await tmp_queue.add(Task(
            id="T1", title="Task 1", description="Depends on ghost",
            status=TaskStatus.PENDING, depends_on=["NONEXISTENT"],
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T1" not in ready_ids
        assert result["blocked_count"] == 1

    @pytest.mark.asyncio
    async def test_depends_on_self(self, handler, tmp_queue):
        """Task depending on itself should be blocked (cycle)."""
        await tmp_queue.add(Task(
            id="T1", title="Task 1", description="Self dep",
            status=TaskStatus.PENDING, depends_on=["T1"],
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T1" not in ready_ids
        assert result["blocked_count"] == 1

    @pytest.mark.asyncio
    async def test_claimed_tasks_excluded(self, handler, tmp_queue):
        """Claimed pending tasks should not appear in ready list."""
        await tmp_queue.add(Task(
            id="T1", title="Task 1", description="Claimed",
            status=TaskStatus.PENDING, assigned_agent="forge:opencode",
        ))
        result = await handler.list_ready_tasks()
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T1" not in ready_ids

    @pytest.mark.asyncio
    async def test_priority_filter(self, handler, tmp_queue):
        """Priority filter should only return matching tasks."""
        await tmp_queue.add(Task(
            id="T1", title="High task", description="High priority",
            status=TaskStatus.PENDING, priority=TaskPriority.HIGH,
        ))
        await tmp_queue.add(Task(
            id="T2", title="Low task", description="Low priority",
            status=TaskStatus.PENDING, priority=TaskPriority.LOW,
        ))
        result = await handler.list_ready_tasks(priority="high")
        ready_ids = [t["id"] for t in result["ready"]]
        assert "T1" in ready_ids
        assert "T2" not in ready_ids

    @pytest.mark.asyncio
    async def test_limit(self, handler, tmp_queue):
        """Limit should cap the number of returned tasks."""
        for i in range(5):
            await tmp_queue.add(Task(
                id=f"T{i}", title=f"Task {i}", description=f"Task {i}",
                status=TaskStatus.PENDING,
            ))
        result = await handler.list_ready_tasks(limit=2)
        assert len(result["ready"]) == 2


# ===========================================================================
# _task_to_dict / _dict_to_task depends_on round-trip
# ===========================================================================


class TestTaskDictRoundTrip:
    """Verify depends_on survives serialization round-trips."""

    def test_task_to_dict_includes_depends_on(self):
        from forge_harness.webhook_server.handlers.task_handler import _task_to_dict

        task = Task(
            id="T1", title="Test", description="Desc",
            depends_on=["T0", "T-1"],
        )
        d = _task_to_dict(task)
        assert d["depends_on"] == ["T0", "T-1"]

    def test_task_to_dict_empty_depends_on(self):
        from forge_harness.webhook_server.handlers.task_handler import _task_to_dict

        task = Task(id="T1", title="Test", description="Desc")
        d = _task_to_dict(task)
        assert d["depends_on"] == []

    def test_dict_to_task_with_depends_on(self):
        from forge_harness.webhook_server.handlers.task_handler import _dict_to_task

        task = _dict_to_task({"subject": "Test", "description": "Desc", "depends_on": ["A", "B"]})
        assert task.depends_on == ["A", "B"]


# ===========================================================================
# Handler create_task with depends_on
# ===========================================================================


class TestCreateTaskWithDeps:
    """Test that create_task stores and returns depends_on."""

    @pytest.fixture
    def tmp_queue(self, tmp_path):
        return TaskQueue(tmp_path / "tasks")

    @pytest.fixture
    def handler(self, tmp_queue):
        from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

        return TaskQueueHandler(state_store=None, task_queue=tmp_queue)

    @pytest.mark.asyncio
    async def test_create_with_depends_on(self, handler):
        result = await handler.create_task(
            subject="Deploy API",
            description="Needs auth first",
            depends_on=["AUTH-001"],
        )
        assert result["depends_on"] == ["AUTH-001"]

    @pytest.mark.asyncio
    async def test_create_without_depends_on(self, handler):
        result = await handler.create_task(
            subject="Simple task",
            description="No deps",
        )
        assert result["depends_on"] == []


# ===========================================================================
# Handler update_task with depends_on
# ===========================================================================


class TestUpdateTaskWithDeps:
    """Test that update_task can set/clear depends_on."""

    @pytest.fixture
    def tmp_queue(self, tmp_path):
        return TaskQueue(tmp_path / "tasks")

    @pytest.fixture
    def handler(self, tmp_queue):
        from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

        return TaskQueueHandler(state_store=None, task_queue=tmp_queue)

    @pytest.mark.asyncio
    async def test_update_adds_depends_on(self, handler):
        created = await handler.create_task(subject="Task A", description="No deps yet")
        task_id = created["id"]
        updated = await handler.update_task(task_id, {"depends_on": ["X", "Y"]})
        assert updated["depends_on"] == ["X", "Y"]

    @pytest.mark.asyncio
    async def test_update_clears_depends_on(self, handler):
        created = await handler.create_task(
            subject="Task B", description="Has deps", depends_on=["Z"],
        )
        task_id = created["id"]
        updated = await handler.update_task(task_id, {"depends_on": []})
        assert updated["depends_on"] == []


# ===========================================================================
# CLI: forge tasks ready
# ===========================================================================


class TestReadyCLI:
    """Test the forge tasks ready CLI command."""

    @pytest.fixture
    def runner(self):
        from click.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_ready_empty(self, runner, forge_root):
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks._get_client") as mock_get:
                mock_client = MagicMock()
                mock_client.sync_list_ready_tasks.return_value = {
                    "tasks": [], "count": 0, "blocked_count": 0,
                }
                mock_get.return_value = mock_client
                result = runner.invoke(cli, ["tasks", "ready"])
                assert result.exit_code == 0
                assert "No ready tasks" in result.output

    def test_ready_with_tasks(self, runner, forge_root):
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks._get_client") as mock_get:
                mock_client = MagicMock()
                mock_client.sync_list_ready_tasks.return_value = {
                    "tasks": [
                        {"id": "T1", "subject": "Setup auth", "priority": "high", "depends_on": [], "created_at": "2026-02-27"},
                    ],
                    "count": 1,
                    "blocked_count": 2,
                }
                mock_get.return_value = mock_client
                result = runner.invoke(cli, ["tasks", "ready"])
                assert result.exit_code == 0
                assert "Setup auth" in result.output
                assert "2 task(s) blocked" in result.output

    def test_ready_json(self, runner, forge_root):
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks._get_client") as mock_get:
                mock_client = MagicMock()
                mock_client.sync_list_ready_tasks.return_value = {
                    "tasks": [{"id": "T1", "subject": "Auth", "priority": "high"}],
                    "count": 1,
                    "blocked_count": 0,
                }
                mock_get.return_value = mock_client
                result = runner.invoke(cli, ["tasks", "--json", "ready"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True


# ===========================================================================
# CLI: forge tasks create --depends-on
# ===========================================================================


class TestCreateDependsOnCLI:
    """Test --depends-on flag on forge tasks create."""

    @pytest.fixture
    def runner(self):
        from click.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_create_with_depends_on(self, runner, forge_root):
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks._get_client") as mock_get:
                mock_client = MagicMock()
                mock_client.sync_create_task.return_value = {
                    "id": "T1", "subject": "Deploy API", "depends_on": ["AUTH-001"],
                }
                mock_get.return_value = mock_client
                result = runner.invoke(cli, [
                    "tasks", "create", "Deploy API",
                    "-d", "Deploy after auth",
                    "--depends-on", "AUTH-001",
                ])
                assert result.exit_code == 0
                # Verify depends_on was passed to the client
                call_kwargs = mock_client.sync_create_task.call_args
                assert call_kwargs.kwargs.get("depends_on") == ["AUTH-001"]

    def test_create_with_multiple_depends_on(self, runner, forge_root):
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks._get_client") as mock_get:
                mock_client = MagicMock()
                mock_client.sync_create_task.return_value = {
                    "id": "T1", "subject": "Final step", "depends_on": ["A", "B"],
                }
                mock_get.return_value = mock_client
                result = runner.invoke(cli, [
                    "tasks", "create", "Final step",
                    "-d", "Depends on A and B",
                    "--depends-on", "A,B",
                ])
                assert result.exit_code == 0
                call_kwargs = mock_client.sync_create_task.call_args
                assert call_kwargs.kwargs.get("depends_on") == ["A", "B"]

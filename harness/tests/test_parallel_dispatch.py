"""Tests for parallel_dispatch.py - Parallel Task Dispatch"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.parallel_dispatch import (
    DispatchResult,
    ParallelDispatcher,
    TaskDefinition,
    TaskResult,
    example_agent,
)


class TestTaskDefinition:
    """Tests for TaskDefinition dataclass."""

    def test_initialization_with_defaults(self):
        """Should initialize with default context."""
        task = TaskDefinition(task_id="task_001", description="Test task")

        assert task.task_id == "task_001"
        assert task.description == "Test task"
        assert task.context == {}

    def test_initialization_with_context(self):
        """Should initialize with provided context."""
        task = TaskDefinition(
            task_id="task_002",
            description="Test with context",
            context={"key": "value", "number": 42},
        )

        assert task.context["key"] == "value"
        assert task.context["number"] == 42


class TestTaskResult:
    """Tests for TaskResult dataclass."""

    def test_success_result(self):
        """Should create successful result."""
        result = TaskResult(
            task_id="task_001",
            success=True,
            output="Success output",
            workspace_path=Path("/tmp/workspace"),
            duration_seconds=5.0,
        )

        assert result.success is True
        assert result.output == "Success output"
        assert result.error is None
        assert result.duration_seconds == 5.0

    def test_failure_result(self):
        """Should create failure result with error."""
        result = TaskResult(
            task_id="task_001",
            success=False,
            output="",
            error="Something went wrong",
            workspace_path=Path("/tmp/workspace"),
            duration_seconds=2.0,
        )

        assert result.success is False
        assert result.error == "Something went wrong"


class TestDispatchResult:
    """Tests for DispatchResult dataclass."""

    def test_initialization(self):
        """Should initialize with provided lists."""
        completed = [MagicMock(spec=TaskResult)]
        failed = [MagicMock(spec=TaskResult)]
        timed_out = [MagicMock(spec=TaskDefinition)]

        result = DispatchResult(
            total_dispatched=3,
            completed_tasks=completed,
            failed_tasks=failed,
            timed_out_tasks=timed_out,
        )

        assert result.total_dispatched == 3
        assert len(result.completed_tasks) == 1
        assert len(result.failed_tasks) == 1
        assert len(result.timed_out_tasks) == 1


class TestParallelDispatcher:
    """Tests for ParallelDispatcher."""

    def test_default_initialization(self):
        """Should initialize with default max agents."""
        dispatcher = ParallelDispatcher()

        assert dispatcher.max_concurrent_agents == 5

    def test_max_agents_clamped_upper(self):
        """Should clamp max agents to 5."""
        dispatcher = ParallelDispatcher(max_concurrent_agents=10)

        assert dispatcher.max_concurrent_agents == 5

    def test_max_agents_clamped_lower(self):
        """Should clamp max agents to at least 1."""
        dispatcher = ParallelDispatcher(max_concurrent_agents=0)

        assert dispatcher.max_concurrent_agents == 1

    def test_custom_max_agents(self):
        """Should accept valid custom max agents."""
        dispatcher = ParallelDispatcher(max_concurrent_agents=3)

        assert dispatcher.max_concurrent_agents == 3

    @pytest.mark.asyncio
    async def test_run_single_agent_task_success(self, tmp_path):
        """Should successfully run a single agent task."""
        dispatcher = ParallelDispatcher()
        task_def = TaskDefinition(task_id="test_001", description="Test task")

        async def mock_agent(task, workspace):
            return TaskResult(
                task_id=task.task_id,
                success=True,
                output="Success",
                workspace_path=workspace,
            )

        result = await dispatcher._run_single_agent_task(
            mock_agent, task_def, tmp_path, timeout_seconds=30
        )

        assert result.success is True
        assert result.task_id == "test_001"
        assert result.workspace_path is not None

    @pytest.mark.asyncio
    async def test_run_single_agent_task_timeout(self, tmp_path):
        """Should handle task timeout."""
        dispatcher = ParallelDispatcher()
        task_def = TaskDefinition(task_id="test_timeout", description="Slow task")

        async def slow_agent(task, workspace):
            await asyncio.sleep(100)  # Will timeout
            return TaskResult(task_id=task.task_id, success=True, output="", workspace_path=workspace)

        result = await dispatcher._run_single_agent_task(
            slow_agent, task_def, tmp_path, timeout_seconds=0.1
        )

        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_run_single_agent_task_exception(self, tmp_path):
        """Should handle agent exception."""
        dispatcher = ParallelDispatcher()
        task_def = TaskDefinition(task_id="test_error", description="Error task")

        async def failing_agent(task, workspace):
            raise ValueError("Simulated error")

        result = await dispatcher._run_single_agent_task(
            failing_agent, task_def, tmp_path, timeout_seconds=30
        )

        assert result.success is False
        assert "Simulated error" in result.error

    @pytest.mark.asyncio
    async def test_run_single_agent_task_workspace_creation(self, tmp_path):
        """Should create isolated workspace."""
        dispatcher = ParallelDispatcher()
        task_def = TaskDefinition(task_id="test_ws", description="Workspace test")

        async def mock_agent(task, workspace):
            return TaskResult(
                task_id=task.task_id,
                success=True,
                output="",
                workspace_path=workspace,
            )

        result = await dispatcher._run_single_agent_task(
            mock_agent, task_def, tmp_path, timeout_seconds=30
        )

        expected_workspace = tmp_path / "agent_workspace_test_ws"
        assert expected_workspace.exists()
        assert result.workspace_path == expected_workspace

    @pytest.mark.asyncio
    async def test_run_single_agent_task_workspace_reuse(self, tmp_path):
        """Should handle existing workspace."""
        dispatcher = ParallelDispatcher()
        task_def = TaskDefinition(task_id="test_reuse", description="Reuse test")

        # Create workspace first
        existing_ws = tmp_path / "agent_workspace_test_reuse"
        existing_ws.mkdir()
        (existing_ws / "old_file.txt").write_text("old content")

        async def mock_agent(task, workspace):
            return TaskResult(
                task_id=task.task_id,
                success=True,
                output="",
                workspace_path=workspace,
            )

        await dispatcher._run_single_agent_task(
            mock_agent, task_def, tmp_path, timeout_seconds=30
        )

        # Old file should be gone (workspace was cleaned)
        assert not (existing_ws / "old_file.txt").exists()

    @pytest.mark.asyncio
    async def test_dispatch_single_task(self, tmp_path):
        """Should dispatch single task."""
        dispatcher = ParallelDispatcher()
        task = TaskDefinition(task_id="single", description="Single task")

        async def mock_agent(task_def, workspace):
            return TaskResult(
                task_id=task_def.task_id,
                success=True,
                output="Done",
                workspace_path=workspace,
            )

        result = await dispatcher.dispatch([task], mock_agent, temp_dir_base=tmp_path)

        assert result.total_dispatched == 1
        assert len(result.completed_tasks) == 1
        assert len(result.failed_tasks) == 0

    @pytest.mark.asyncio
    async def test_dispatch_multiple_tasks(self, tmp_path):
        """Should dispatch multiple tasks concurrently."""
        dispatcher = ParallelDispatcher(max_concurrent_agents=3)
        tasks = [
            TaskDefinition(task_id=f"task_{i}", description=f"Task {i}")
            for i in range(5)
        ]

        async def mock_agent(task_def, workspace):
            await asyncio.sleep(0.01)  # Small delay
            return TaskResult(
                task_id=task_def.task_id,
                success=True,
                output=f"Completed {task_def.task_id}",
                workspace_path=workspace,
            )

        result = await dispatcher.dispatch(tasks, mock_agent, temp_dir_base=tmp_path)

        assert result.total_dispatched == 5
        assert len(result.completed_tasks) == 5

    @pytest.mark.asyncio
    async def test_dispatch_with_failures(self, tmp_path):
        """Should handle mixed success and failures."""
        dispatcher = ParallelDispatcher()
        tasks = [
            TaskDefinition(task_id="success", description="Will succeed"),
            TaskDefinition(task_id="fail", description="Will fail"),
        ]

        async def mixed_agent(task_def, workspace):
            if task_def.task_id == "fail":
                return TaskResult(
                    task_id=task_def.task_id,
                    success=False,
                    output="",
                    error="Failed",
                    workspace_path=workspace,
                )
            return TaskResult(
                task_id=task_def.task_id,
                success=True,
                output="Success",
                workspace_path=workspace,
            )

        result = await dispatcher.dispatch(tasks, mixed_agent, temp_dir_base=tmp_path)

        assert result.total_dispatched == 2
        assert len(result.completed_tasks) == 1
        assert len(result.failed_tasks) == 1

    @pytest.mark.asyncio
    async def test_dispatch_with_timeouts(self, tmp_path):
        """Should track timed out tasks separately."""
        dispatcher = ParallelDispatcher()
        tasks = [
            TaskDefinition(task_id="fast", description="Fast task"),
            TaskDefinition(task_id="slow", description="Slow task"),
        ]

        async def timeout_agent(task_def, workspace):
            if task_def.task_id == "slow":
                await asyncio.sleep(10)  # Will timeout
            return TaskResult(
                task_id=task_def.task_id,
                success=True,
                output="Done",
                workspace_path=workspace,
            )

        result = await dispatcher.dispatch(
            tasks, timeout_agent, timeout_seconds=0.1, temp_dir_base=tmp_path
        )

        assert result.total_dispatched == 2
        assert len(result.timed_out_tasks) == 1
        assert result.timed_out_tasks[0].task_id == "slow"

    @pytest.mark.asyncio
    async def test_dispatch_concurrency_limit(self, tmp_path):
        """Should respect concurrency limit."""
        max_concurrent = 2
        dispatcher = ParallelDispatcher(max_concurrent_agents=max_concurrent)
        tasks = [TaskDefinition(task_id=f"task_{i}", description=f"Task {i}") for i in range(5)]

        concurrent_count = 0
        max_observed = 0

        async def counting_agent(task_def, workspace):
            nonlocal concurrent_count, max_observed
            concurrent_count += 1
            max_observed = max(max_observed, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return TaskResult(
                task_id=task_def.task_id,
                success=True,
                output="",
                workspace_path=workspace,
            )

        await dispatcher.dispatch(tasks, counting_agent, temp_dir_base=tmp_path)

        assert max_observed <= max_concurrent

    @pytest.mark.asyncio
    async def test_dispatch_without_temp_dir_base(self):
        """Should create temp directory when not provided."""
        dispatcher = ParallelDispatcher()
        task = TaskDefinition(task_id="temp_test", description="Test")

        async def mock_agent(task_def, workspace):
            return TaskResult(
                task_id=task_def.task_id,
                success=True,
                output="",
                workspace_path=workspace,
            )

        result = await dispatcher.dispatch([task], mock_agent)

        assert result.total_dispatched == 1
        assert len(result.completed_tasks) == 1


class TestExampleAgent:
    """Tests for example_agent function."""

    @pytest.mark.asyncio
    async def test_example_agent_success(self, tmp_path):
        """Should complete successfully."""
        task = TaskDefinition(
            task_id="example_001", description="Example task", context={"delay": 0.01}
        )

        result = await example_agent(task, tmp_path)

        assert result.success is True
        assert "successfully processed" in result.output
        assert result.workspace_path == tmp_path

    @pytest.mark.asyncio
    async def test_example_agent_writes_output(self, tmp_path):
        """Should write output file."""
        task = TaskDefinition(task_id="example_002", description="Write test", context={})

        await example_agent(task, tmp_path)

        output_file = tmp_path / "agent_output.txt"
        assert output_file.exists()
        assert "example_002" in output_file.read_text()

    @pytest.mark.asyncio
    async def test_example_agent_failure(self, tmp_path):
        """Should handle simulated failure."""
        task = TaskDefinition(
            task_id="example_fail", description="Fail test", context={"should_fail": True}
        )

        result = await example_agent(task, tmp_path)

        assert result.success is False
        assert "Simulated failure" in result.error

    @pytest.mark.asyncio
    async def test_example_agent_timeout_simulation(self, tmp_path):
        """Should simulate timeout behavior."""
        task = TaskDefinition(
            task_id="example_timeout",
            description="Timeout test",
            context={"should_timeout": True},
        )

        # This would normally timeout, but we're testing the flag
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(example_agent(task, tmp_path), timeout=0.1)

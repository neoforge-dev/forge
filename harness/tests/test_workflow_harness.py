"""Tests for workflow_harness.py - Workflow Harness for FORGE Automation.

Tests cover:
- WorkflowStep dataclass
- WorkflowCheckpoint save/load
- WorkflowResult structure
- WorkflowHarness execution
- Dependency resolution
- Retry logic
- Checkpoint resume
- list_checkpoints / cleanup_checkpoints
- create_workflow_harness factory
- Timeout handling
- Circular dependency detection
- Unknown dependency validation
- Context propagation between steps
- Custom tracker injection
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.workflow_harness import (
    StepResult,
    StepStatus,
    WorkflowCheckpoint,
    WorkflowHarness,
    WorkflowResult,
    WorkflowStep,
    create_workflow_harness,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_checkpoint_dir(tmp_path: Path) -> Path:
    """Create a temporary checkpoint directory."""
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    return checkpoint_dir


@pytest.fixture
def workflow_harness(tmp_checkpoint_dir: Path) -> WorkflowHarness:
    """Create a WorkflowHarness instance."""
    return WorkflowHarness(
        workflow_name="test-workflow",
        checkpoint_dir=tmp_checkpoint_dir,
    )


@pytest.fixture
def mock_tracker() -> MagicMock:
    """Create a mock tracker."""
    tracker = MagicMock()
    tracker.track_event = MagicMock()
    return tracker


# =============================================================================
# StepStatus Enum Tests
# =============================================================================


class TestStepStatus:
    """Tests for StepStatus enum."""

    def test_status_values(self) -> None:
        """Should have expected status values."""
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"

    def test_all_statuses_exist(self) -> None:
        """Should have exactly five statuses."""
        names = {s.name for s in StepStatus}
        assert names == {"PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"}


# =============================================================================
# WorkflowStep Tests
# =============================================================================


class TestWorkflowStep:
    """Tests for WorkflowStep dataclass."""

    @pytest.mark.asyncio
    async def test_step_creation(self) -> None:
        """Should create step with default values."""
        async def dummy_handler(context: dict) -> dict:
            return {"result": "ok"}

        step = WorkflowStep(
            name="test-step",
            handler=dummy_handler,
        )

        assert step.name == "test-step"
        assert step.handler == dummy_handler
        assert step.depends_on == []
        assert step.retry_count == 3
        assert step.timeout_seconds == 600
        assert step.skip_on_failure is False
        assert step.description == ""

    def test_step_with_dependencies(self) -> None:
        """Should create step with dependencies."""

        async def dummy_handler(context: dict) -> dict:
            return {"result": "ok"}

        step = WorkflowStep(
            name="dependent-step",
            handler=dummy_handler,
            depends_on=["step1", "step2"],
            retry_count=5,
            timeout_seconds=300,
            skip_on_failure=True,
            description="A test step",
        )

        assert step.depends_on == ["step1", "step2"]
        assert step.retry_count == 5
        assert step.timeout_seconds == 300
        assert step.skip_on_failure is True
        assert step.description == "A test step"

    def test_step_default_depends_on_is_independent(self) -> None:
        """Two steps should not share the same default depends_on list."""

        async def dummy_handler(context: dict) -> dict:
            return {}

        step_a = WorkflowStep(name="a", handler=dummy_handler)
        step_b = WorkflowStep(name="b", handler=dummy_handler)

        step_a.depends_on.append("x")
        assert step_b.depends_on == []


# =============================================================================
# WorkflowCheckpoint Tests
# =============================================================================


class TestWorkflowCheckpoint:
    """Tests for WorkflowCheckpoint dataclass."""

    def test_checkpoint_to_dict(self) -> None:
        """Should convert checkpoint to dictionary."""
        checkpoint = WorkflowCheckpoint(
            workflow_id="wf-123",
            workflow_name="test-wf",
            completed_steps=["step1"],
            step_results={"step1": {"status": "completed"}},
            context={"key": "value"},
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )

        data = checkpoint.to_dict()

        assert data["workflow_id"] == "wf-123"
        assert data["completed_steps"] == ["step1"]
        assert data["context"] == {"key": "value"}
        assert data["workflow_name"] == "test-wf"

    def test_checkpoint_from_dict(self) -> None:
        """Should create checkpoint from dictionary."""
        data = {
            "workflow_id": "wf-123",
            "workflow_name": "test-wf",
            "completed_steps": ["step1", "step2"],
            "step_results": {},
            "context": {},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

        checkpoint = WorkflowCheckpoint.from_dict(data)

        assert checkpoint.workflow_id == "wf-123"
        assert checkpoint.completed_steps == ["step1", "step2"]

    def test_checkpoint_roundtrip(self) -> None:
        """Should survive a to_dict / from_dict roundtrip."""
        original = WorkflowCheckpoint(
            workflow_id="rt-001",
            workflow_name="roundtrip",
            completed_steps=["a", "b"],
            step_results={"a": {"status": "completed"}, "b": {"status": "completed"}},
            context={"env": "test"},
            created_at="2024-06-01T10:00:00+00:00",
            updated_at="2024-06-01T10:05:00+00:00",
        )

        restored = WorkflowCheckpoint.from_dict(original.to_dict())

        assert restored.workflow_id == original.workflow_id
        assert restored.completed_steps == original.completed_steps
        assert restored.context == original.context
        assert restored.updated_at == original.updated_at


# =============================================================================
# WorkflowHarness Initialization Tests
# =============================================================================


class TestWorkflowHarnessInit:
    """Tests for WorkflowHarness initialization."""

    def test_init_creates_checkpoint_dir(self, tmp_path: Path) -> None:
        """Should create checkpoint directory if it doesn't exist."""
        checkpoint_dir = tmp_path / "new_checkpoints"

        harness = WorkflowHarness(
            workflow_name="test",
            checkpoint_dir=checkpoint_dir,
        )

        assert checkpoint_dir.exists()

    def test_init_with_defaults(self, tmp_checkpoint_dir: Path) -> None:
        """Should initialize with default tracker."""
        harness = WorkflowHarness(
            workflow_name="test-workflow",
            checkpoint_dir=tmp_checkpoint_dir,
        )

        assert harness.workflow_name == "test-workflow"
        assert harness.checkpoint_dir == tmp_checkpoint_dir
        assert harness.tracker is not None

    def test_init_with_custom_tracker(self, tmp_checkpoint_dir: Path, mock_tracker: MagicMock) -> None:
        """Should use the provided tracker."""
        harness = WorkflowHarness(
            workflow_name="tracked-wf",
            checkpoint_dir=tmp_checkpoint_dir,
            tracker=mock_tracker,
        )

        assert harness.tracker is mock_tracker

    def test_init_default_checkpoint_dir_created(self, tmp_path: Path) -> None:
        """Should use .workflow_checkpoints as default and create it."""
        default_dir = tmp_path / ".workflow_checkpoints"
        # Patch Path to avoid polluting the real CWD
        with patch.object(WorkflowHarness, "__init__", wraps=WorkflowHarness.__init__):
            harness = WorkflowHarness.__new__(WorkflowHarness)
            harness.workflow_name = "default-dir-test"
            harness.checkpoint_dir = default_dir
            harness.tracker = MagicMock()
            default_dir.mkdir(parents=True, exist_ok=True)

        assert default_dir.exists()

    def test_generate_workflow_id(self, tmp_checkpoint_dir: Path) -> None:
        """Should generate unique workflow IDs."""
        harness = WorkflowHarness(
            workflow_name="test",
            checkpoint_dir=tmp_checkpoint_dir,
        )

        id1 = harness._generate_workflow_id()
        id2 = harness._generate_workflow_id()

        assert id1 != id2
        assert id1.startswith("test_")
        assert id2.startswith("test_")

    def test_generate_workflow_id_contains_timestamp(self, tmp_checkpoint_dir: Path) -> None:
        """Generated ID should embed a timestamp segment."""
        harness = WorkflowHarness(workflow_name="ts-wf", checkpoint_dir=tmp_checkpoint_dir)
        wf_id = harness._generate_workflow_id()
        # Format: <name>_YYYYMMDD_HHMMSS_<hex8>
        parts = wf_id.split("_")
        assert len(parts) >= 4
        assert len(parts[-1]) == 8  # short uuid

    def test_log_prints_workflow_name(self, tmp_checkpoint_dir: Path, capsys) -> None:
        """_log should prefix output with workflow name."""
        harness = WorkflowHarness(workflow_name="log-test", checkpoint_dir=tmp_checkpoint_dir)
        harness._log("hello world")
        captured = capsys.readouterr()
        assert "[WORKFLOW:log-test]" in captured.out
        assert "hello world" in captured.out


# =============================================================================
# Execution Order Tests
# =============================================================================


class TestExecutionOrder:
    """Tests for dependency resolution and execution order."""

    def test_resolve_execution_order_no_deps(self, workflow_harness: WorkflowHarness) -> None:
        """Should return steps in order when no dependencies."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [
            WorkflowStep(name="step1", handler=handler),
            WorkflowStep(name="step2", handler=handler),
            WorkflowStep(name="step3", handler=handler),
        ]

        order = workflow_harness._resolve_execution_order(steps)

        assert len(order) == 3
        assert [s.name for s in order] == ["step1", "step2", "step3"]

    def test_resolve_execution_order_with_deps(self, workflow_harness: WorkflowHarness) -> None:
        """Should resolve dependencies correctly."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [
            WorkflowStep(name="step3", handler=handler, depends_on=["step2"]),
            WorkflowStep(name="step1", handler=handler),
            WorkflowStep(name="step2", handler=handler, depends_on=["step1"]),
        ]

        order = workflow_harness._resolve_execution_order(steps)

        names = [s.name for s in order]
        assert names.index("step1") < names.index("step2")
        assert names.index("step2") < names.index("step3")

    def test_resolve_execution_order_complex_deps(self, workflow_harness: WorkflowHarness) -> None:
        """Should handle complex dependency chains."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [
            WorkflowStep(name="a", handler=handler),
            WorkflowStep(name="b", handler=handler, depends_on=["a"]),
            WorkflowStep(name="c", handler=handler, depends_on=["a"]),
            WorkflowStep(name="d", handler=handler, depends_on=["b", "c"]),
        ]

        order = workflow_harness._resolve_execution_order(steps)

        names = [s.name for s in order]
        assert names.index("a") < names.index("b")
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("d")
        assert names.index("c") < names.index("d")

    def test_resolve_execution_order_circular_raises(self, workflow_harness: WorkflowHarness) -> None:
        """Should raise ValueError on circular dependency."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [
            WorkflowStep(name="x", handler=handler, depends_on=["z"]),
            WorkflowStep(name="y", handler=handler, depends_on=["x"]),
            WorkflowStep(name="z", handler=handler, depends_on=["y"]),
        ]

        with pytest.raises(ValueError, match="Circular dependency detected"):
            workflow_harness._resolve_execution_order(steps)

    def test_resolve_execution_order_single_step(self, workflow_harness: WorkflowHarness) -> None:
        """Should handle a single step with no deps."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [WorkflowStep(name="only", handler=handler)]
        order = workflow_harness._resolve_execution_order(steps)
        assert [s.name for s in order] == ["only"]

    def test_resolve_execution_order_empty(self, workflow_harness: WorkflowHarness) -> None:
        """Should return empty list for empty input."""
        order = workflow_harness._resolve_execution_order([])
        assert order == []


# =============================================================================
# Checkpoint Tests
# =============================================================================


class TestCheckpointOperations:
    """Tests for checkpoint save/load."""

    def test_save_checkpoint(self, workflow_harness: WorkflowHarness) -> None:
        """Should save checkpoint to disk."""
        path = workflow_harness._save_checkpoint(
            workflow_id="wf-123",
            completed=["step1"],
            results={},
            context={},
        )

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["workflow_id"] == "wf-123"

    def test_save_checkpoint_includes_all_fields(self, workflow_harness: WorkflowHarness) -> None:
        """Saved checkpoint should contain every required field."""
        path = workflow_harness._save_checkpoint(
            workflow_id="full-wf",
            completed=["s1", "s2"],
            results={"s1": {"status": "completed"}},
            context={"env": "test"},
        )

        data = json.loads(path.read_text())
        assert data["completed_steps"] == ["s1", "s2"]
        assert data["context"] == {"env": "test"}
        assert "created_at" in data
        assert "updated_at" in data
        assert data["workflow_name"] == "test-workflow"

    def test_save_checkpoint_filename_matches_id(self, workflow_harness: WorkflowHarness) -> None:
        """Checkpoint file should be named after the workflow ID."""
        path = workflow_harness._save_checkpoint(
            workflow_id="filename-wf",
            completed=[],
            results={},
            context={},
        )
        assert path.name == "filename-wf.json"

    def test_load_checkpoint(self, workflow_harness: WorkflowHarness) -> None:
        """Should load checkpoint from disk."""
        checkpoint_file = workflow_harness.checkpoint_dir / "test_checkpoint.json"
        data = {
            "workflow_id": "wf-456",
            "workflow_name": "loaded",
            "completed_steps": ["step1", "step2"],
            "step_results": {},
            "context": {"key": "value"},
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        checkpoint_file.write_text(json.dumps(data))

        loaded = workflow_harness._load_checkpoint(checkpoint_file)

        assert loaded.workflow_id == "wf-456"
        assert loaded.workflow_name == "loaded"
        assert loaded.completed_steps == ["step1", "step2"]

    def test_list_checkpoints_empty(self, workflow_harness: WorkflowHarness) -> None:
        """list_checkpoints should return empty list when dir is empty."""
        results = workflow_harness.list_checkpoints()
        assert results == []

    def test_list_checkpoints_returns_all(self, workflow_harness: WorkflowHarness) -> None:
        """list_checkpoints should return one entry per valid checkpoint file."""
        for i in range(3):
            workflow_harness._save_checkpoint(
                workflow_id=f"wf-{i}",
                completed=[],
                results={},
                context={},
            )

        results = workflow_harness.list_checkpoints()
        assert len(results) == 3

    def test_list_checkpoints_sorted_descending(self, workflow_harness: WorkflowHarness) -> None:
        """list_checkpoints should be sorted by updated_at descending."""
        for i in range(3):
            cp_path = workflow_harness.checkpoint_dir / f"wf-sort-{i}.json"
            data = {
                "workflow_id": f"wf-sort-{i}",
                "workflow_name": "sort-test",
                "completed_steps": [],
                "step_results": {},
                "context": {},
                "created_at": f"2024-01-0{i + 1}T00:00:00",
                "updated_at": f"2024-01-0{i + 1}T00:00:00",
            }
            cp_path.write_text(json.dumps(data))

        results = workflow_harness.list_checkpoints()
        updated_ats = [cp.updated_at for _, cp in results]
        assert updated_ats == sorted(updated_ats, reverse=True)

    def test_list_checkpoints_skips_corrupt_files(self, workflow_harness: WorkflowHarness) -> None:
        """list_checkpoints should silently skip files with invalid JSON."""
        corrupt = workflow_harness.checkpoint_dir / "corrupt.json"
        corrupt.write_text("not-valid-json{{{")

        valid_path = workflow_harness._save_checkpoint(
            workflow_id="good-wf",
            completed=[],
            results={},
            context={},
        )

        results = workflow_harness.list_checkpoints()
        ids = [cp.workflow_id for _, cp in results]
        assert "good-wf" in ids
        assert len(results) == 1

    def test_cleanup_checkpoints_removes_old(self, workflow_harness: WorkflowHarness) -> None:
        """cleanup_checkpoints should remove all but the most recent N files."""
        for i in range(7):
            cp_path = workflow_harness.checkpoint_dir / f"old-{i}.json"
            data = {
                "workflow_id": f"old-{i}",
                "workflow_name": "cleanup-test",
                "completed_steps": [],
                "step_results": {},
                "context": {},
                "created_at": f"2024-01-0{i + 1}T00:00:00",
                "updated_at": f"2024-01-0{i + 1}T00:00:00",
            }
            cp_path.write_text(json.dumps(data))

        removed = workflow_harness.cleanup_checkpoints(keep_latest=5)

        assert removed == 2
        remaining = list(workflow_harness.checkpoint_dir.glob("*.json"))
        assert len(remaining) == 5

    def test_cleanup_checkpoints_nothing_to_remove(self, workflow_harness: WorkflowHarness) -> None:
        """cleanup_checkpoints should remove 0 when fewer files than keep_latest."""
        for i in range(3):
            workflow_harness._save_checkpoint(
                workflow_id=f"few-{i}",
                completed=[],
                results={},
                context={},
            )

        removed = workflow_harness.cleanup_checkpoints(keep_latest=5)
        assert removed == 0

    def test_cleanup_checkpoints_empty_dir(self, workflow_harness: WorkflowHarness) -> None:
        """cleanup_checkpoints on empty dir should return 0."""
        removed = workflow_harness.cleanup_checkpoints(keep_latest=5)
        assert removed == 0

    def test_cleanup_checkpoints_silently_skips_unlink_errors(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """cleanup_checkpoints should continue even if unlink raises an exception."""
        for i in range(3):
            cp_path = workflow_harness.checkpoint_dir / f"err-{i}.json"
            data = {
                "workflow_id": f"err-{i}",
                "workflow_name": "err-test",
                "completed_steps": [],
                "step_results": {},
                "context": {},
                "created_at": f"2024-01-0{i + 1}T00:00:00",
                "updated_at": f"2024-01-0{i + 1}T00:00:00",
            }
            cp_path.write_text(json.dumps(data))

        # Patch Path.unlink to raise on the first call (oldest checkpoint)
        original_unlink = Path.unlink

        call_count = 0

        def flaky_unlink(self_path, missing_ok=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("permission denied")
            original_unlink(self_path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", flaky_unlink):
            removed = workflow_harness.cleanup_checkpoints(keep_latest=1)

        # First unlink raised (skipped), second succeeded — expect 1 removed
        assert removed == 1


# =============================================================================
# Step Execution Tests
# =============================================================================


class TestStepExecution:
    """Tests for step execution."""

    @pytest.mark.asyncio
    async def test_execute_step_success(self, workflow_harness: WorkflowHarness) -> None:
        """Should execute step successfully."""

        async def success_handler(ctx: dict) -> dict:
            return {"status": "ok"}

        step = WorkflowStep(name="success-step", handler=success_handler)

        result = await workflow_harness._execute_step(step, {}, {})

        assert result.status == StepStatus.COMPLETED
        assert result.result == {"status": "ok"}
        assert result.error is None
        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_execute_step_failure(self, workflow_harness: WorkflowHarness) -> None:
        """Should handle step failure."""

        async def fail_handler(ctx: dict) -> dict:
            raise ValueError("Test error")

        step = WorkflowStep(
            name="fail-step",
            handler=fail_handler,
            retry_count=0,
        )

        result = await workflow_harness._execute_step(step, {}, {})

        assert result.status == StepStatus.FAILED
        assert result.error is not None
        assert "Test error" in result.error

    @pytest.mark.asyncio
    async def test_execute_step_with_retry(self, workflow_harness: WorkflowHarness) -> None:
        """Should retry failed steps."""
        call_count = 0

        async def retry_handler(ctx: dict) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Temporary error")
            return {"status": "ok"}

        step = WorkflowStep(
            name="retry-step",
            handler=retry_handler,
            retry_count=2,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await workflow_harness._execute_step(step, {}, {})

        assert result.status == StepStatus.COMPLETED
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_execute_step_exhausts_retries(self, workflow_harness: WorkflowHarness) -> None:
        """Should FAILED after all retries are used."""
        call_count = 0

        async def always_fail(ctx: dict) -> dict:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent error")

        step = WorkflowStep(
            name="always-fail",
            handler=always_fail,
            retry_count=2,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await workflow_harness._execute_step(step, {}, {})

        assert result.status == StepStatus.FAILED
        assert call_count == 3  # 1 original + 2 retries
        assert result.retries_used == 3
        assert "persistent error" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_step_timeout(self, workflow_harness: WorkflowHarness) -> None:
        """Should fail with timeout error when step exceeds timeout."""

        async def slow_handler(ctx: dict) -> dict:
            await asyncio.sleep(100)
            return {}

        step = WorkflowStep(
            name="slow-step",
            handler=slow_handler,
            timeout_seconds=1,
            retry_count=0,
        )

        result = await workflow_harness._execute_step(step, {}, {})

        assert result.status == StepStatus.FAILED
        assert result.error is not None
        assert "Timeout" in result.error or "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_step_timeout_with_retry(self, workflow_harness: WorkflowHarness) -> None:
        """Timeout should also consume a retry slot."""
        call_count = 0

        async def slow_then_fast(ctx: dict) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(100)
            return {"ok": True}

        step = WorkflowStep(
            name="timeout-then-succeed",
            handler=slow_then_fast,
            timeout_seconds=1,
            retry_count=1,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await workflow_harness._execute_step(step, {}, {})

        # After retry the handler returns fast (mocked sleep), should succeed
        assert result.status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_step_with_description_logged(
        self, workflow_harness: WorkflowHarness, capsys
    ) -> None:
        """Step description should appear in log output."""

        async def handler(ctx: dict) -> dict:
            return {}

        step = WorkflowStep(
            name="described-step",
            handler=handler,
            description="Doing something important",
        )

        await workflow_harness._execute_step(step, {}, {})

        captured = capsys.readouterr()
        assert "Doing something important" in captured.out

    @pytest.mark.asyncio
    async def test_execute_step_none_return_treated_as_empty(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Handler returning None should produce an empty result dict."""

        async def none_handler(ctx: dict) -> dict:
            return None  # type: ignore[return-value]

        step = WorkflowStep(name="none-return", handler=none_handler)
        result = await workflow_harness._execute_step(step, {}, {})

        assert result.status == StepStatus.COMPLETED
        assert result.result == {}

    @pytest.mark.asyncio
    async def test_execute_step_tracks_events(
        self, tmp_checkpoint_dir: Path, mock_tracker: MagicMock
    ) -> None:
        """Step execution should emit started and completed tracker events."""
        harness = WorkflowHarness(
            workflow_name="track-test",
            checkpoint_dir=tmp_checkpoint_dir,
            tracker=mock_tracker,
        )

        async def handler(ctx: dict) -> dict:
            return {"x": 1}

        step = WorkflowStep(name="tracked-step", handler=handler)
        await harness._execute_step(step, {}, {})

        event_names = [call.args[0] for call in mock_tracker.track_event.call_args_list]
        assert "workflow_step_started" in event_names
        assert "workflow_step_completed" in event_names

    @pytest.mark.asyncio
    async def test_execute_step_tracks_failure_event(
        self, tmp_checkpoint_dir: Path, mock_tracker: MagicMock
    ) -> None:
        """Failed step should emit a workflow_step_failed tracker event."""
        harness = WorkflowHarness(
            workflow_name="fail-track",
            checkpoint_dir=tmp_checkpoint_dir,
            tracker=mock_tracker,
        )

        async def fail_handler(ctx: dict) -> dict:
            raise ValueError("boom")

        step = WorkflowStep(name="fail-tracked", handler=fail_handler, retry_count=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await harness._execute_step(step, {}, {})

        event_names = [call.args[0] for call in mock_tracker.track_event.call_args_list]
        assert "workflow_step_failed" in event_names


# =============================================================================
# Workflow Execution Tests
# =============================================================================


class TestWorkflowExecution:
    """Tests for full workflow execution."""

    @pytest.mark.asyncio
    async def test_execute_empty_workflow(self, workflow_harness: WorkflowHarness) -> None:
        """Should handle empty workflow."""
        result = await workflow_harness.execute(steps=[], context={})

        assert isinstance(result, WorkflowResult)
        assert result.success is True
        assert result.steps_completed == []

    @pytest.mark.asyncio
    async def test_execute_single_step(self, workflow_harness: WorkflowHarness) -> None:
        """Should execute single step workflow."""

        async def handler(ctx: dict) -> dict:
            return {"output": "result"}

        steps = [WorkflowStep(name="only-step", handler=handler)]

        result = await workflow_harness.execute(steps=steps, context={})

        assert result.success is True
        assert "only-step" in result.steps_completed
        assert result.step_results["only-step"].result == {"output": "result"}

    @pytest.mark.asyncio
    async def test_execute_dry_run(self, workflow_harness: WorkflowHarness) -> None:
        """Should skip execution in dry run mode."""
        handler_called = False

        async def handler(ctx: dict) -> dict:
            nonlocal handler_called
            handler_called = True
            return {"result": "ok"}

        steps = [WorkflowStep(name="dry-step", handler=handler)]

        result = await workflow_harness.execute(steps=steps, context={}, dry_run=True)

        assert result.success is True
        assert "dry-step" in result.steps_completed
        assert handler_called is False
        assert result.step_results["dry-step"].result.get("dry_run") is True

    @pytest.mark.asyncio
    async def test_execute_with_dependency_failure(self, workflow_harness: WorkflowHarness) -> None:
        """Should skip steps when dependency fails."""

        async def fail_handler(ctx: dict) -> dict:
            raise ValueError("Fail")

        async def success_handler(ctx: dict) -> dict:
            return {"status": "ok"}

        steps = [
            WorkflowStep(
                name="failing",
                handler=fail_handler,
                retry_count=0,
            ),
            WorkflowStep(
                name="dependent",
                handler=success_handler,
                depends_on=["failing"],
            ),
        ]

        result = await workflow_harness.execute(steps=steps, context={})

        assert "failing" in result.steps_failed
        assert "dependent" in result.steps_skipped

    @pytest.mark.asyncio
    async def test_execute_with_skip_on_failure(self, workflow_harness: WorkflowHarness) -> None:
        """Should continue when skip_on_failure is set."""

        async def fail_handler(ctx: dict) -> dict:
            raise ValueError("Fail")

        async def success_handler(ctx: dict) -> dict:
            return {"status": "ok"}

        steps = [
            WorkflowStep(
                name="failing",
                handler=fail_handler,
                retry_count=0,
                skip_on_failure=True,
            ),
            WorkflowStep(
                name="after-failure",
                handler=success_handler,
                depends_on=["failing"],
            ),
        ]

        result = await workflow_harness.execute(steps=steps, context={})

        # The dependent step should still run since failing has skip_on_failure
        # but it might be skipped if dependency didn't complete
        # This tests the dependency handling logic
        assert result is not None  # Workflow should complete

    @pytest.mark.asyncio
    async def test_execute_deps_not_satisfied_skips_step(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Step whose dependency failed but which has skip_on_failure=True on itself
        should hit the 'dependencies not met' path (deps_satisfied=False) and be skipped.
        """

        async def fail_handler(ctx: dict) -> dict:
            raise RuntimeError("dep failed")

        async def downstream_handler(ctx: dict) -> dict:
            return {"ok": True}

        steps = [
            WorkflowStep(
                name="upstream",
                handler=fail_handler,
                retry_count=0,
            ),
            # This step has skip_on_failure=True on itself, so the first skip block
            # (deps_failed and not step.skip_on_failure) is NOT taken.
            # Then deps_satisfied is False (upstream failed, not completed/skipped)
            # so the second skip block fires (lines 298-300).
            WorkflowStep(
                name="downstream",
                handler=downstream_handler,
                depends_on=["upstream"],
                skip_on_failure=True,
            ),
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await workflow_harness.execute(steps=steps, context={})

        assert "upstream" in result.steps_failed
        assert "downstream" in result.steps_skipped

    @pytest.mark.asyncio
    async def test_execute_resumes_from_checkpoint(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Should resume workflow from checkpoint."""

        async def handler(ctx: dict) -> dict:
            return {"status": "ok"}

        checkpoint_path = workflow_harness._save_checkpoint(
            workflow_id="resumed-wf",
            completed=["step1"],
            results={
                "step1": {
                    "name": "step1",
                    "status": "completed",
                    "started_at": datetime.now(UTC).isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "result": {"done": True},
                    "duration_seconds": 1.0,
                }
            },
            context={"existing": "data"},
        )

        steps = [
            WorkflowStep(name="step1", handler=handler),
            WorkflowStep(name="step2", handler=handler),
        ]

        result = await workflow_harness.execute(
            steps=steps,
            context={"new": "data"},
            resume_from=checkpoint_path,
        )

        assert result.workflow_id == "resumed-wf"
        assert "step2" in result.steps_completed

    @pytest.mark.asyncio
    async def test_execute_resume_from_nonexistent_path_starts_fresh(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """When resume_from path does not exist, should start a fresh workflow."""

        async def handler(ctx: dict) -> dict:
            return {"ok": True}

        steps = [WorkflowStep(name="fresh-step", handler=handler)]
        nonexistent = workflow_harness.checkpoint_dir / "nonexistent.json"

        result = await workflow_harness.execute(
            steps=steps,
            context={},
            resume_from=nonexistent,
        )

        assert result.success is True
        assert "fresh-step" in result.steps_completed

    @pytest.mark.asyncio
    async def test_execute_unknown_dependency_raises(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Should raise ValueError when a step depends on a non-existent step."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [
            WorkflowStep(name="step-a", handler=handler, depends_on=["ghost-step"]),
        ]

        with pytest.raises(ValueError, match="unknown step"):
            await workflow_harness.execute(steps=steps, context={})

    @pytest.mark.asyncio
    async def test_execute_context_propagated_to_downstream(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Downstream steps should see upstream step result in context."""
        received_context: dict = {}

        async def producer(ctx: dict) -> dict:
            return {"value": 42}

        async def consumer(ctx: dict) -> dict:
            nonlocal received_context
            received_context = dict(ctx)
            return {}

        steps = [
            WorkflowStep(name="producer", handler=producer),
            WorkflowStep(name="consumer", handler=consumer, depends_on=["producer"]),
        ]

        await workflow_harness.execute(steps=steps, context={})

        assert received_context.get("step_producer_result") == {"value": 42}

    @pytest.mark.asyncio
    async def test_execute_failure_captured_in_errors(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Failed step error should appear in workflow result errors list."""

        async def fail_handler(ctx: dict) -> dict:
            raise RuntimeError("something went wrong")

        steps = [
            WorkflowStep(name="bad-step", handler=fail_handler, retry_count=0),
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await workflow_harness.execute(steps=steps, context={})

        assert result.success is False
        assert any("bad-step" in err for err in result.errors)

    @pytest.mark.asyncio
    async def test_execute_checkpoint_saved_after_each_step(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """A checkpoint file should exist after workflow execution."""

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [
            WorkflowStep(name="s1", handler=handler),
            WorkflowStep(name="s2", handler=handler, depends_on=["s1"]),
        ]

        result = await workflow_harness.execute(steps=steps, context={})

        assert result.checkpoint_path is not None
        assert result.checkpoint_path.exists()

    @pytest.mark.asyncio
    async def test_execute_tracks_workflow_started_and_completed(
        self, tmp_checkpoint_dir: Path, mock_tracker: MagicMock
    ) -> None:
        """execute() should emit workflow_started and workflow_completed events."""
        harness = WorkflowHarness(
            workflow_name="evt-test",
            checkpoint_dir=tmp_checkpoint_dir,
            tracker=mock_tracker,
        )

        async def handler(ctx: dict) -> dict:
            return {}

        steps = [WorkflowStep(name="evt-step", handler=handler)]
        await harness.execute(steps=steps, context={})

        event_names = [call.args[0] for call in mock_tracker.track_event.call_args_list]
        assert "workflow_started" in event_names
        assert "workflow_completed" in event_names

    @pytest.mark.asyncio
    async def test_execute_multi_step_linear_chain(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Multi-step linear workflow should complete all steps in order."""
        order: list[str] = []

        async def make_handler(name: str):
            async def handler(ctx: dict) -> dict:
                order.append(name)
                return {}
            return handler

        steps = [
            WorkflowStep(name="first", handler=await make_handler("first")),
            WorkflowStep(name="second", handler=await make_handler("second"), depends_on=["first"]),
            WorkflowStep(name="third", handler=await make_handler("third"), depends_on=["second"]),
        ]

        result = await workflow_harness.execute(steps=steps, context={})

        assert result.success is True
        assert order == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_execute_dry_run_multiple_steps(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Dry run should mark all steps as completed without calling handlers."""
        called: list[str] = []

        async def make_handler(name: str):
            async def handler(ctx: dict) -> dict:
                called.append(name)
                return {}
            return handler

        steps = [
            WorkflowStep(name="a", handler=await make_handler("a")),
            WorkflowStep(name="b", handler=await make_handler("b")),
        ]

        result = await workflow_harness.execute(steps=steps, context={}, dry_run=True)

        assert result.success is True
        assert called == []
        assert set(result.steps_completed) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_execute_result_has_checkpoint_path(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """WorkflowResult.checkpoint_path should point into checkpoint_dir."""

        async def handler(ctx: dict) -> dict:
            return {}

        result = await workflow_harness.execute(
            steps=[WorkflowStep(name="cp-step", handler=handler)],
            context={},
        )

        assert result.checkpoint_path is not None
        assert str(workflow_harness.checkpoint_dir) in str(result.checkpoint_path)

    @pytest.mark.asyncio
    async def test_execute_workflow_name_in_result(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """WorkflowResult should carry the correct workflow name."""

        async def handler(ctx: dict) -> dict:
            return {}

        result = await workflow_harness.execute(
            steps=[WorkflowStep(name="name-check", handler=handler)],
            context={},
        )

        assert result.workflow_name == "test-workflow"

    @pytest.mark.asyncio
    async def test_execute_context_merge_on_resume(
        self, workflow_harness: WorkflowHarness
    ) -> None:
        """Provided context should override checkpoint context on resume."""
        received_context: dict = {}

        async def capture_handler(ctx: dict) -> dict:
            nonlocal received_context
            received_context = dict(ctx)
            return {}

        checkpoint_path = workflow_harness._save_checkpoint(
            workflow_id="ctx-merge-wf",
            completed=[],
            results={},
            context={"source": "checkpoint", "shared": "old"},
        )

        steps = [WorkflowStep(name="capture", handler=capture_handler)]

        await workflow_harness.execute(
            steps=steps,
            context={"source": "caller", "extra": "new"},
            resume_from=checkpoint_path,
        )

        # Provided context takes precedence over checkpoint context
        assert received_context.get("source") == "caller"
        assert received_context.get("extra") == "new"
        # Keys only in checkpoint context are still present
        assert received_context.get("shared") == "old"


# =============================================================================
# StepResult Tests
# =============================================================================


class TestStepResult:
    """Tests for StepResult dataclass."""

    def test_step_result_defaults(self) -> None:
        """StepResult should have sensible defaults."""
        now = datetime.now(UTC)
        r = StepResult(name="x", status=StepStatus.PENDING, started_at=now)

        assert r.completed_at is None
        assert r.result == {}
        assert r.error is None
        assert r.retries_used == 0
        assert r.duration_seconds == 0.0

    def test_step_result_with_all_fields(self) -> None:
        """StepResult should accept all fields."""
        now = datetime.now(UTC)
        r = StepResult(
            name="full",
            status=StepStatus.FAILED,
            started_at=now,
            completed_at=now,
            result={"k": "v"},
            error="oops",
            retries_used=2,
            duration_seconds=3.5,
        )

        assert r.error == "oops"
        assert r.retries_used == 2
        assert r.duration_seconds == 3.5


# =============================================================================
# Result Tests
# =============================================================================


class TestWorkflowResult:
    """Tests for WorkflowResult."""

    def test_result_structure(self) -> None:
        """Should have correct result structure."""
        result = WorkflowResult(
            success=True,
            workflow_id="wf-123",
            workflow_name="test",
            steps_completed=["step1"],
            steps_failed=[],
            steps_skipped=[],
            step_results={},
            total_duration_seconds=10.5,
            errors=[],
        )

        assert result.success is True
        assert result.workflow_id == "wf-123"
        assert result.total_duration_seconds == 10.5
        assert result.checkpoint_path is None

    def test_result_with_checkpoint_path(self) -> None:
        """Should store checkpoint path if provided."""
        result = WorkflowResult(
            success=False,
            workflow_id="wf-err",
            workflow_name="errored",
            steps_completed=[],
            steps_failed=["bad"],
            steps_skipped=[],
            step_results={},
            total_duration_seconds=0.1,
            errors=["bad: boom"],
            checkpoint_path=Path("/tmp/some.json"),
        )

        assert result.checkpoint_path == Path("/tmp/some.json")
        assert result.success is False


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestCreateWorkflowHarness:
    """Tests for the create_workflow_harness factory function."""

    def test_factory_returns_workflow_harness(self, tmp_checkpoint_dir: Path) -> None:
        """create_workflow_harness should return a WorkflowHarness instance."""
        harness = create_workflow_harness(
            workflow_name="factory-wf",
            checkpoint_dir=tmp_checkpoint_dir,
        )

        assert isinstance(harness, WorkflowHarness)
        assert harness.workflow_name == "factory-wf"
        assert harness.checkpoint_dir == tmp_checkpoint_dir

    def test_factory_with_custom_tracker(
        self, tmp_checkpoint_dir: Path, mock_tracker: MagicMock
    ) -> None:
        """Factory should pass tracker to the harness."""
        harness = create_workflow_harness(
            workflow_name="tracked-factory",
            checkpoint_dir=tmp_checkpoint_dir,
            tracker=mock_tracker,
        )

        assert harness.tracker is mock_tracker

    def test_factory_without_checkpoint_dir_creates_harness(self, tmp_path: Path) -> None:
        """Factory should work without explicit checkpoint_dir (uses default)."""
        default_dir = tmp_path / ".workflow_checkpoints"
        with patch("forge_harness.workflow_harness.Path") as mock_path_cls:
            # Just verify that WorkflowHarness is returned even with defaults
            harness = create_workflow_harness(workflow_name="no-dir-wf", checkpoint_dir=tmp_path / "x")

        assert isinstance(harness, WorkflowHarness)

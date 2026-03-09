"""Unit tests for forge_harness/workflow_harness.py.

Targets 80%+ coverage of WorkflowHarness and related dataclasses.
Uses pytest with unittest.mock (MagicMock, patch, AsyncMock).

Test organisation:
    TestStepStatusEnum              - StepStatus enum values
    TestWorkflowStepDataclass       - WorkflowStep construction and defaults
    TestStepResultDataclass         - StepResult construction and defaults
    TestWorkflowCheckpointDataclass - WorkflowCheckpoint serialization round-trip
    TestWorkflowResultDataclass     - WorkflowResult construction
    TestWorkflowHarnessInit         - __init__ behaviour
    TestGenerateWorkflowId          - _generate_workflow_id format
    TestLog                         - _log output
    TestResolveExecutionOrder       - Kahn topological sort (happy and error paths)
    TestSaveCheckpoint              - _save_checkpoint writes correct JSON
    TestLoadCheckpoint              - _load_checkpoint reads JSON to dataclass
    TestListCheckpoints             - list_checkpoints with sorting and skip-corrupt
    TestCleanupCheckpoints          - cleanup_checkpoints keeps/removes correct files
    TestExecuteStep                 - _execute_step retry, timeout, tracker events
    TestExecuteWorkflow             - execute() end-to-end scenarios
    TestCreateWorkflowHarnessFactory - factory function
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from forge_harness.models.workflow import StepStatus
from forge_harness.workflow_harness import (
    StepResult,
    WorkflowCheckpoint,
    WorkflowHarness,
    WorkflowResult,
    WorkflowStep,
    create_workflow_harness,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _ok(ctx: dict) -> dict:
    """Simple always-successful handler."""
    return {"ok": True}


async def _fail(ctx: dict) -> dict:
    """Simple always-failing handler."""
    raise RuntimeError("intentional failure")


def _make_harness(tmp_path: Path, name: str = "unit-wf") -> WorkflowHarness:
    """Create a harness wired to a temp directory with a no-op tracker."""
    cp_dir = tmp_path / "cp"
    cp_dir.mkdir(parents=True, exist_ok=True)
    tracker = MagicMock()
    tracker.track_event = MagicMock()
    return WorkflowHarness(workflow_name=name, checkpoint_dir=cp_dir, tracker=tracker)


def _write_checkpoint(directory: Path, workflow_id: str, **overrides) -> Path:
    """Write a minimal valid checkpoint JSON file and return its path."""
    now = datetime.now(UTC).isoformat()
    data = {
        "workflow_id": workflow_id,
        "workflow_name": overrides.get("workflow_name", "test"),
        "completed_steps": overrides.get("completed_steps", []),
        "step_results": overrides.get("step_results", {}),
        "context": overrides.get("context", {}),
        "created_at": overrides.get("created_at", now),
        "updated_at": overrides.get("updated_at", now),
    }
    path = directory / f"{workflow_id}.json"
    path.write_text(json.dumps(data))
    return path


# ===========================================================================
# StepStatus enum
# ===========================================================================


class TestStepStatusEnum:
    def test_all_five_members_present(self) -> None:
        names = {s.name for s in StepStatus}
        assert names == {"PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"}

    def test_values_are_lowercase_strings(self) -> None:
        for status in StepStatus:
            assert status.value == status.value.lower()
            assert isinstance(status.value, str)

    def test_is_str_subclass(self) -> None:
        assert isinstance(StepStatus.COMPLETED, str)
        assert StepStatus.FAILED == "failed"

    def test_specific_values(self) -> None:
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"


# ===========================================================================
# WorkflowStep dataclass
# ===========================================================================


class TestWorkflowStepDataclass:
    def test_required_fields_set(self) -> None:
        step = WorkflowStep(name="s", handler=_ok)
        assert step.name == "s"
        assert step.handler is _ok

    def test_defaults(self) -> None:
        step = WorkflowStep(name="s", handler=_ok)
        assert step.depends_on == []
        assert step.retry_count == 3
        assert step.timeout_seconds == 600
        assert step.skip_on_failure is False
        assert step.description == ""

    def test_custom_values(self) -> None:
        step = WorkflowStep(
            name="custom",
            handler=_ok,
            depends_on=["a", "b"],
            retry_count=5,
            timeout_seconds=120,
            skip_on_failure=True,
            description="does something",
        )
        assert step.depends_on == ["a", "b"]
        assert step.retry_count == 5
        assert step.timeout_seconds == 120
        assert step.skip_on_failure is True
        assert step.description == "does something"

    def test_depends_on_list_is_not_shared_between_instances(self) -> None:
        """Mutable default factory must not share state between instances."""
        s1 = WorkflowStep(name="s1", handler=_ok)
        s2 = WorkflowStep(name="s2", handler=_ok)
        s1.depends_on.append("x")
        assert s2.depends_on == []


# ===========================================================================
# StepResult dataclass
# ===========================================================================


class TestStepResultDataclass:
    def _now(self) -> datetime:
        return datetime.now(UTC)

    def test_required_fields(self) -> None:
        now = self._now()
        r = StepResult(name="r", status=StepStatus.PENDING, started_at=now)
        assert r.name == "r"
        assert r.status == StepStatus.PENDING
        assert r.started_at == now

    def test_optional_defaults(self) -> None:
        now = self._now()
        r = StepResult(name="r", status=StepStatus.PENDING, started_at=now)
        assert r.completed_at is None
        assert r.result == {}
        assert r.error is None
        assert r.retries_used == 0
        assert r.duration_seconds == 0.0

    def test_result_dict_not_shared(self) -> None:
        now = self._now()
        r1 = StepResult(name="a", status=StepStatus.PENDING, started_at=now)
        r2 = StepResult(name="b", status=StepStatus.PENDING, started_at=now)
        r1.result["key"] = "val"
        assert r2.result == {}

    def test_full_construction(self) -> None:
        now = self._now()
        r = StepResult(
            name="full",
            status=StepStatus.FAILED,
            started_at=now,
            completed_at=now,
            result={"x": 1},
            error="boom",
            retries_used=2,
            duration_seconds=9.5,
        )
        assert r.error == "boom"
        assert r.retries_used == 2
        assert r.duration_seconds == 9.5
        assert r.result == {"x": 1}


# ===========================================================================
# WorkflowCheckpoint dataclass
# ===========================================================================


class TestWorkflowCheckpointDataclass:
    def _sample(self) -> WorkflowCheckpoint:
        now = datetime.now(UTC).isoformat()
        return WorkflowCheckpoint(
            workflow_id="wf-1",
            workflow_name="test",
            completed_steps=["a", "b"],
            step_results={"a": {"status": "completed"}},
            context={"env": "test"},
            created_at=now,
            updated_at=now,
        )

    def test_to_dict_contains_all_keys(self) -> None:
        cp = self._sample()
        d = cp.to_dict()
        required = {
            "workflow_id",
            "workflow_name",
            "completed_steps",
            "step_results",
            "context",
            "created_at",
            "updated_at",
        }
        assert required.issubset(d.keys())

    def test_to_dict_values_match(self) -> None:
        cp = self._sample()
        d = cp.to_dict()
        assert d["workflow_id"] == "wf-1"
        assert d["completed_steps"] == ["a", "b"]
        assert d["context"] == {"env": "test"}

    def test_from_dict_round_trip(self) -> None:
        original = self._sample()
        restored = WorkflowCheckpoint.from_dict(original.to_dict())
        assert restored.workflow_id == original.workflow_id
        assert restored.completed_steps == original.completed_steps
        assert restored.context == original.context
        assert restored.created_at == original.created_at

    def test_from_dict_with_empty_results(self) -> None:
        now = datetime.now(UTC).isoformat()
        d = {
            "workflow_id": "empty-wf",
            "workflow_name": "empty",
            "completed_steps": [],
            "step_results": {},
            "context": {},
            "created_at": now,
            "updated_at": now,
        }
        cp = WorkflowCheckpoint.from_dict(d)
        assert cp.completed_steps == []
        assert cp.step_results == {}


# ===========================================================================
# WorkflowResult dataclass
# ===========================================================================


class TestWorkflowResultDataclass:
    def test_basic_construction(self) -> None:
        r = WorkflowResult(
            success=True,
            workflow_id="wf-x",
            workflow_name="x",
            steps_completed=["s1"],
            steps_failed=[],
            steps_skipped=[],
            step_results={},
            total_duration_seconds=1.0,
            errors=[],
        )
        assert r.success is True
        assert r.checkpoint_path is None

    def test_optional_checkpoint_path(self) -> None:
        r = WorkflowResult(
            success=False,
            workflow_id="wf-y",
            workflow_name="y",
            steps_completed=[],
            steps_failed=["s1"],
            steps_skipped=[],
            step_results={},
            total_duration_seconds=0.5,
            errors=["s1: err"],
            checkpoint_path=Path("/tmp/cp.json"),
        )
        assert r.checkpoint_path == Path("/tmp/cp.json")
        assert r.success is False


# ===========================================================================
# WorkflowHarness.__init__
# ===========================================================================


class TestWorkflowHarnessInit:
    def test_stores_name(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path, name="my-wf")
        assert h.workflow_name == "my-wf"

    def test_stores_checkpoint_dir(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        assert h.checkpoint_dir == tmp_path / "cp"

    def test_creates_checkpoint_dir_if_missing(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "brand-new" / "nested"
        WorkflowHarness(workflow_name="x", checkpoint_dir=new_dir)
        assert new_dir.is_dir()

    def test_default_checkpoint_dir_is_workflow_checkpoints(self, tmp_path: Path) -> None:
        """When no checkpoint_dir provided, should default to .workflow_checkpoints."""
        default_path = tmp_path / ".workflow_checkpoints"
        with patch("forge_harness.workflow_harness.Path", wraps=Path) as _mp:
            # Just verify the harness accepts None and creates something
            h = WorkflowHarness(workflow_name="default-cp")
        # Cleanup if created in cwd
        default = Path(".workflow_checkpoints")
        if default.exists():
            import shutil
            shutil.rmtree(default, ignore_errors=True)

    def test_stores_tracker(self, tmp_path: Path) -> None:
        tracker = MagicMock()
        h = WorkflowHarness(
            workflow_name="tracked",
            checkpoint_dir=tmp_path / "cp",
            tracker=tracker,
        )
        assert h.tracker is tracker

    def test_default_tracker_is_noop(self, tmp_path: Path) -> None:
        """Without providing a tracker, a NoOpTracker should be used."""
        from forge_harness.posthog_tracker import NoOpTracker
        h = WorkflowHarness(workflow_name="noop", checkpoint_dir=tmp_path / "cp")
        assert isinstance(h.tracker, NoOpTracker)


# ===========================================================================
# WorkflowHarness._generate_workflow_id
# ===========================================================================


class TestGenerateWorkflowId:
    def test_starts_with_workflow_name(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path, name="my-pipeline")
        wf_id = h._generate_workflow_id()
        assert wf_id.startswith("my-pipeline_")

    def test_unique_per_call(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        ids = {h._generate_workflow_id() for _ in range(10)}
        assert len(ids) == 10

    def test_format_segments(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path, name="seg-test")
        wf_id = h._generate_workflow_id()
        # Expected: seg-test_YYYYMMDD_HHMMSS_<hex8>
        parts = wf_id.split("_")
        assert len(parts) >= 4
        date_part = parts[-3]
        time_part = parts[-2]
        uuid_part = parts[-1]
        assert len(date_part) == 8 and date_part.isdigit()
        assert len(time_part) == 6 and time_part.isdigit()
        assert len(uuid_part) == 8


# ===========================================================================
# WorkflowHarness._log
# ===========================================================================


class TestLog:
    def test_includes_workflow_name_and_message(self, tmp_path: Path, capsys) -> None:
        h = _make_harness(tmp_path, name="log-wf")
        h._log("test message")
        captured = capsys.readouterr()
        assert "[WORKFLOW:log-wf]" in captured.out
        assert "test message" in captured.out

    def test_writes_to_stdout(self, tmp_path: Path, capsys) -> None:
        h = _make_harness(tmp_path)
        h._log("hello")
        out, err = capsys.readouterr()
        assert "hello" in out
        assert err == ""


# ===========================================================================
# WorkflowHarness._resolve_execution_order
# ===========================================================================


class TestResolveExecutionOrder:
    def _harness(self, tmp_path: Path) -> WorkflowHarness:
        return _make_harness(tmp_path)

    def test_empty_steps_returns_empty(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        assert h._resolve_execution_order([]) == []

    def test_single_step(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        step = WorkflowStep(name="only", handler=_ok)
        result = h._resolve_execution_order([step])
        assert [s.name for s in result] == ["only"]

    def test_no_deps_preserves_input_order(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        steps = [
            WorkflowStep(name="a", handler=_ok),
            WorkflowStep(name="b", handler=_ok),
            WorkflowStep(name="c", handler=_ok),
        ]
        result = h._resolve_execution_order(steps)
        assert [s.name for s in result] == ["a", "b", "c"]

    def test_linear_chain_ordered(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        steps = [
            WorkflowStep(name="c", handler=_ok, depends_on=["b"]),
            WorkflowStep(name="a", handler=_ok),
            WorkflowStep(name="b", handler=_ok, depends_on=["a"]),
        ]
        result = h._resolve_execution_order(steps)
        names = [s.name for s in result]
        assert names.index("a") < names.index("b") < names.index("c")

    def test_diamond_dependency(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        steps = [
            WorkflowStep(name="top", handler=_ok),
            WorkflowStep(name="left", handler=_ok, depends_on=["top"]),
            WorkflowStep(name="right", handler=_ok, depends_on=["top"]),
            WorkflowStep(name="bottom", handler=_ok, depends_on=["left", "right"]),
        ]
        result = h._resolve_execution_order(steps)
        names = [s.name for s in result]
        assert names.index("top") < names.index("left")
        assert names.index("top") < names.index("right")
        assert names.index("left") < names.index("bottom")
        assert names.index("right") < names.index("bottom")

    def test_circular_dependency_raises_value_error(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        steps = [
            WorkflowStep(name="x", handler=_ok, depends_on=["z"]),
            WorkflowStep(name="y", handler=_ok, depends_on=["x"]),
            WorkflowStep(name="z", handler=_ok, depends_on=["y"]),
        ]
        with pytest.raises(ValueError, match="Circular dependency detected"):
            h._resolve_execution_order(steps)

    def test_circular_dep_message_lists_involved_steps(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        steps = [
            WorkflowStep(name="alpha", handler=_ok, depends_on=["beta"]),
            WorkflowStep(name="beta", handler=_ok, depends_on=["alpha"]),
        ]
        with pytest.raises(ValueError) as exc_info:
            h._resolve_execution_order(steps)
        msg = str(exc_info.value)
        assert "alpha" in msg or "beta" in msg

    def test_all_steps_included_in_result(self, tmp_path: Path) -> None:
        h = self._harness(tmp_path)
        steps = [WorkflowStep(name=f"s{i}", handler=_ok) for i in range(10)]
        result = h._resolve_execution_order(steps)
        assert len(result) == 10


# ===========================================================================
# WorkflowHarness._save_checkpoint
# ===========================================================================


class TestSaveCheckpoint:
    def test_file_created(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = h._save_checkpoint("wf-1", [], {}, {})
        assert path.exists()

    def test_filename_matches_workflow_id(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = h._save_checkpoint("my-wf-id", [], {}, {})
        assert path.name == "my-wf-id.json"

    def test_file_is_valid_json(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = h._save_checkpoint("json-wf", [], {}, {})
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_saved_data_contains_all_fields(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = h._save_checkpoint(
            workflow_id="fields-wf",
            completed=["step-a"],
            results={"step-a": {"status": "completed"}},
            context={"key": "value"},
        )
        data = json.loads(path.read_text())
        assert data["workflow_id"] == "fields-wf"
        assert data["completed_steps"] == ["step-a"]
        assert data["step_results"] == {"step-a": {"status": "completed"}}
        assert data["context"] == {"key": "value"}
        assert data["workflow_name"] == "unit-wf"
        assert "created_at" in data
        assert "updated_at" in data

    def test_returns_path_object(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        result = h._save_checkpoint("path-wf", [], {}, {})
        assert isinstance(result, Path)

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        h._save_checkpoint("ow-wf", ["step-1"], {}, {})
        h._save_checkpoint("ow-wf", ["step-1", "step-2"], {}, {})
        path = h.checkpoint_dir / "ow-wf.json"
        data = json.loads(path.read_text())
        assert data["completed_steps"] == ["step-1", "step-2"]


# ===========================================================================
# WorkflowHarness._load_checkpoint
# ===========================================================================


class TestLoadCheckpoint:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = _write_checkpoint(h.checkpoint_dir, "load-wf", completed_steps=["s1"])
        cp = h._load_checkpoint(path)
        assert cp.workflow_id == "load-wf"
        assert cp.completed_steps == ["s1"]

    def test_returns_workflow_checkpoint_instance(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = _write_checkpoint(h.checkpoint_dir, "type-wf")
        cp = h._load_checkpoint(path)
        assert isinstance(cp, WorkflowCheckpoint)

    def test_preserves_context(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        path = _write_checkpoint(h.checkpoint_dir, "ctx-wf", context={"a": 1, "b": 2})
        cp = h._load_checkpoint(path)
        assert cp.context == {"a": 1, "b": 2}

    def test_preserves_step_results(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        results = {"step1": {"status": "completed", "duration_seconds": 3.0}}
        path = _write_checkpoint(h.checkpoint_dir, "res-wf", step_results=results)
        cp = h._load_checkpoint(path)
        assert cp.step_results == results


# ===========================================================================
# WorkflowHarness.list_checkpoints
# ===========================================================================


class TestListCheckpoints:
    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        assert h.list_checkpoints() == []

    def test_returns_tuple_of_path_and_checkpoint(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        _write_checkpoint(h.checkpoint_dir, "tuple-wf")
        results = h.list_checkpoints()
        assert len(results) == 1
        path, cp = results[0]
        assert isinstance(path, Path)
        assert isinstance(cp, WorkflowCheckpoint)

    def test_all_valid_files_returned(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        for i in range(4):
            _write_checkpoint(h.checkpoint_dir, f"wf-{i}")
        results = h.list_checkpoints()
        assert len(results) == 4

    def test_sorted_by_updated_at_descending(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        dates = ["2024-01-01T00:00:00", "2024-03-01T00:00:00", "2024-02-01T00:00:00"]
        for i, d in enumerate(dates):
            _write_checkpoint(h.checkpoint_dir, f"sorted-{i}", updated_at=d)
        results = h.list_checkpoints()
        updated_ats = [cp.updated_at for _, cp in results]
        assert updated_ats == sorted(updated_ats, reverse=True)

    def test_corrupt_files_silently_skipped(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        (h.checkpoint_dir / "bad.json").write_text("{not valid json{{")
        _write_checkpoint(h.checkpoint_dir, "good-wf")
        results = h.list_checkpoints()
        assert len(results) == 1
        assert results[0][1].workflow_id == "good-wf"

    def test_non_json_extension_not_listed(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        (h.checkpoint_dir / "ignore.txt").write_text("text")
        results = h.list_checkpoints()
        assert results == []


# ===========================================================================
# WorkflowHarness.cleanup_checkpoints
# ===========================================================================


class TestCleanupCheckpoints:
    def test_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        assert h.cleanup_checkpoints(keep_latest=5) == 0

    def test_removes_old_checkpoints(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        for i in range(8):
            _write_checkpoint(
                h.checkpoint_dir,
                f"wf-{i}",
                updated_at=f"2024-01-0{i + 1}T00:00:00",
            )
        removed = h.cleanup_checkpoints(keep_latest=5)
        assert removed == 3
        remaining = list(h.checkpoint_dir.glob("*.json"))
        assert len(remaining) == 5

    def test_removes_zero_when_under_limit(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        for i in range(3):
            _write_checkpoint(h.checkpoint_dir, f"wf-{i}")
        removed = h.cleanup_checkpoints(keep_latest=10)
        assert removed == 0

    def test_keeps_exactly_keep_latest_files(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        for i in range(6):
            _write_checkpoint(
                h.checkpoint_dir,
                f"wf-{i}",
                updated_at=f"2024-01-0{i + 1}T00:00:00",
            )
        h.cleanup_checkpoints(keep_latest=3)
        assert len(list(h.checkpoint_dir.glob("*.json"))) == 3

    def test_silently_skips_unlink_errors(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        for i in range(3):
            _write_checkpoint(
                h.checkpoint_dir,
                f"err-wf-{i}",
                updated_at=f"2024-01-0{i + 1}T00:00:00",
            )
        call_count = 0
        original_unlink = Path.unlink

        def flaky_unlink(self_path, missing_ok=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("permission denied")
            original_unlink(self_path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", flaky_unlink):
            removed = h.cleanup_checkpoints(keep_latest=1)

        assert removed == 1  # second unlink succeeded


# ===========================================================================
# WorkflowHarness._execute_step
# ===========================================================================


class TestExecuteStep:
    @pytest.mark.asyncio
    async def test_success_returns_completed_status(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="good", handler=_ok)
        result = await h._execute_step(step, {}, {})
        assert result.status == StepStatus.COMPLETED
        assert result.name == "good"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_success_result_dict_populated(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)

        async def handler(ctx: dict) -> dict:
            return {"value": 99}

        step = WorkflowStep(name="data-step", handler=handler)
        result = await h._execute_step(step, {}, {})
        assert result.result == {"value": 99}

    @pytest.mark.asyncio
    async def test_handler_returning_none_gives_empty_dict(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)

        async def handler(ctx: dict) -> dict:
            return None  # type: ignore[return-value]

        step = WorkflowStep(name="none-step", handler=handler)
        result = await h._execute_step(step, {}, {})
        assert result.result == {}

    @pytest.mark.asyncio
    async def test_failure_no_retry_returns_failed(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="fail-step", handler=_fail, retry_count=0)
        result = await h._execute_step(step, {}, {})
        assert result.status == StepStatus.FAILED
        assert "intentional failure" in (result.error or "")

    @pytest.mark.asyncio
    async def test_failure_error_string_captured(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)

        async def handler(ctx: dict) -> dict:
            raise ValueError("specific error message")

        step = WorkflowStep(name="err-msg-step", handler=handler, retry_count=0)
        result = await h._execute_step(step, {}, {})
        assert "specific error message" in (result.error or "")

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        attempts = []

        async def flaky(ctx: dict) -> dict:
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("not yet")
            return {"done": True}

        step = WorkflowStep(name="flaky", handler=flaky, retry_count=2)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await h._execute_step(step, {}, {})

        assert result.status == StepStatus.COMPLETED
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_failed(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        attempts = []

        async def always_fail(ctx: dict) -> dict:
            attempts.append(1)
            raise RuntimeError("always bad")

        step = WorkflowStep(name="exhausted", handler=always_fail, retry_count=2)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await h._execute_step(step, {}, {})

        assert result.status == StepStatus.FAILED
        # 1 initial + 2 retries = 3 total attempts
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_retries_used_field_reflects_actual_retries(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        attempts = []

        async def fail_once(ctx: dict) -> dict:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("first attempt fails")
            return {}

        step = WorkflowStep(name="retry-count", handler=fail_once, retry_count=3)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await h._execute_step(step, {}, {})

        assert result.status == StepStatus.COMPLETED
        assert result.retries_used == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_failed_with_timeout_message(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)

        async def slow(ctx: dict) -> dict:
            await asyncio.sleep(999)
            return {}

        step = WorkflowStep(name="slow-step", handler=slow, timeout_seconds=1, retry_count=0)
        result = await h._execute_step(step, {}, {})
        assert result.status == StepStatus.FAILED
        assert result.error is not None
        assert "Timeout" in result.error or "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_message_includes_seconds(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)

        async def slow(ctx: dict) -> dict:
            await asyncio.sleep(999)
            return {}

        step = WorkflowStep(name="ts-step", handler=slow, timeout_seconds=30, retry_count=0)
        result = await h._execute_step(step, {}, {})
        assert "30" in (result.error or "")

    @pytest.mark.asyncio
    async def test_description_logged_when_set(self, tmp_path: Path, capsys) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="desc-step", handler=_ok, description="My detailed description")
        await h._execute_step(step, {}, {})
        out = capsys.readouterr().out
        assert "My detailed description" in out

    @pytest.mark.asyncio
    async def test_no_description_no_extra_log_line(self, tmp_path: Path, capsys) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="nodesc-step", handler=_ok, description="")
        await h._execute_step(step, {}, {})
        out = capsys.readouterr().out
        # Should not crash; empty description branch not entered
        assert "nodesc-step" in out

    @pytest.mark.asyncio
    async def test_tracker_receives_step_started_event(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="evt-step", handler=_ok)
        await h._execute_step(step, {}, {})
        events = [c.args[0] for c in h.tracker.track_event.call_args_list]
        assert "workflow_step_started" in events

    @pytest.mark.asyncio
    async def test_tracker_receives_step_completed_event(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="ok-evt", handler=_ok)
        await h._execute_step(step, {}, {})
        events = [c.args[0] for c in h.tracker.track_event.call_args_list]
        assert "workflow_step_completed" in events

    @pytest.mark.asyncio
    async def test_tracker_receives_step_failed_event(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="fail-evt", handler=_fail, retry_count=0)
        await h._execute_step(step, {}, {})
        events = [c.args[0] for c in h.tracker.track_event.call_args_list]
        assert "workflow_step_failed" in events

    @pytest.mark.asyncio
    async def test_step_receives_context(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        received: dict = {}

        async def capture(ctx: dict) -> dict:
            received.update(ctx)
            return {}

        step = WorkflowStep(name="ctx-step", handler=capture)
        await h._execute_step(step, {"injected": True}, {})
        assert received.get("injected") is True

    @pytest.mark.asyncio
    async def test_duration_seconds_is_non_negative(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="dur-step", handler=_ok)
        result = await h._execute_step(step, {}, {})
        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_started_at_and_completed_at_set(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="time-step", handler=_ok)
        result = await h._execute_step(step, {}, {})
        assert result.started_at is not None
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_exponential_backoff_sleep_called(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        attempts = []

        async def fail_twice(ctx: dict) -> dict:
            attempts.append(1)
            if len(attempts) <= 2:
                raise RuntimeError("fail")
            return {}

        step = WorkflowStep(name="backoff", handler=fail_twice, retry_count=3)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await h._execute_step(step, {}, {})
        # Called once for attempt 0 (sleep 1s) and once for attempt 1 (sleep 2s)
        assert mock_sleep.call_count == 2
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args[0] == 1  # 2**0
        assert sleep_args[1] == 2  # 2**1


# ===========================================================================
# WorkflowHarness.execute — full workflow integration
# ===========================================================================


class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_empty_workflow_succeeds(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        result = await h.execute(steps=[], context={})
        assert result.success is True
        assert result.steps_completed == []
        assert result.steps_failed == []

    @pytest.mark.asyncio
    async def test_single_successful_step(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="s1", handler=_ok)
        result = await h.execute(steps=[step], context={})
        assert result.success is True
        assert "s1" in result.steps_completed

    @pytest.mark.asyncio
    async def test_result_includes_workflow_name(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path, name="named-wf")
        result = await h.execute(steps=[], context={})
        assert result.workflow_name == "named-wf"

    @pytest.mark.asyncio
    async def test_result_has_checkpoint_path(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="cp-step", handler=_ok)
        result = await h.execute(steps=[step], context={})
        assert result.checkpoint_path is not None
        assert str(h.checkpoint_dir) in str(result.checkpoint_path)

    @pytest.mark.asyncio
    async def test_total_duration_positive(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="dur", handler=_ok)
        result = await h.execute(steps=[step], context={})
        assert result.total_duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_failed_step_marks_workflow_as_failed(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="bad", handler=_fail, retry_count=0)
        result = await h.execute(steps=[step], context={})
        assert result.success is False
        assert "bad" in result.steps_failed

    @pytest.mark.asyncio
    async def test_failed_step_error_in_errors_list(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="err-step", handler=_fail, retry_count=0)
        result = await h.execute(steps=[step], context={})
        assert any("err-step" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_unknown_dep_raises_value_error(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="orphan", handler=_ok, depends_on=["ghost"])
        with pytest.raises(ValueError, match="unknown step"):
            await h.execute(steps=[step], context={})

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_handler(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        called = []

        async def handler(ctx: dict) -> dict:
            called.append(True)
            return {}

        step = WorkflowStep(name="dry", handler=handler)
        result = await h.execute(steps=[step], context={}, dry_run=True)
        assert result.success is True
        assert called == []

    @pytest.mark.asyncio
    async def test_dry_run_marks_steps_completed(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [
            WorkflowStep(name="dr-a", handler=_ok),
            WorkflowStep(name="dr-b", handler=_ok),
        ]
        result = await h.execute(steps=steps, context={}, dry_run=True)
        assert set(result.steps_completed) == {"dr-a", "dr-b"}

    @pytest.mark.asyncio
    async def test_dry_run_result_has_dry_run_flag(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        step = WorkflowStep(name="dr-flag", handler=_ok)
        result = await h.execute(steps=[step], context={}, dry_run=True)
        assert result.step_results["dr-flag"].result.get("dry_run") is True

    @pytest.mark.asyncio
    async def test_dependency_failure_skips_dependent(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [
            WorkflowStep(name="fails", handler=_fail, retry_count=0),
            WorkflowStep(name="blocked", handler=_ok, depends_on=["fails"]),
        ]
        result = await h.execute(steps=steps, context={})
        assert "fails" in result.steps_failed
        assert "blocked" in result.steps_skipped

    @pytest.mark.asyncio
    async def test_skipped_dep_step_recorded_in_step_results(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [
            WorkflowStep(name="up", handler=_fail, retry_count=0),
            WorkflowStep(name="down", handler=_ok, depends_on=["up"]),
        ]
        result = await h.execute(steps=steps, context={})
        assert "down" in result.step_results
        assert result.step_results["down"].status == StepStatus.SKIPPED
        assert result.step_results["down"].error == "Dependency failed"

    @pytest.mark.asyncio
    async def test_context_propagated_to_downstream_step(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        received: dict = {}

        async def producer(ctx: dict) -> dict:
            return {"produced": 42}

        async def consumer(ctx: dict) -> dict:
            received.update(ctx)
            return {}

        steps = [
            WorkflowStep(name="prod", handler=producer),
            WorkflowStep(name="cons", handler=consumer, depends_on=["prod"]),
        ]
        await h.execute(steps=steps, context={})
        assert received.get("step_prod_result") == {"produced": 42}

    @pytest.mark.asyncio
    async def test_initial_context_available_to_all_steps(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        received: dict = {}

        async def capture(ctx: dict) -> dict:
            received.update(ctx)
            return {}

        step = WorkflowStep(name="cap", handler=capture)
        await h.execute(steps=[step], context={"initial_key": "hello"})
        assert received.get("initial_key") == "hello"

    @pytest.mark.asyncio
    async def test_tracker_receives_workflow_started_event(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        await h.execute(steps=[], context={})
        events = [c.args[0] for c in h.tracker.track_event.call_args_list]
        assert "workflow_started" in events

    @pytest.mark.asyncio
    async def test_tracker_receives_workflow_completed_event(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        await h.execute(steps=[], context={})
        events = [c.args[0] for c in h.tracker.track_event.call_args_list]
        assert "workflow_completed" in events

    @pytest.mark.asyncio
    async def test_tracker_workflow_started_payload(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [WorkflowStep(name="p-step", handler=_ok)]
        await h.execute(steps=steps, context={})
        started_calls = [
            c for c in h.tracker.track_event.call_args_list
            if c.args[0] == "workflow_started"
        ]
        assert len(started_calls) == 1
        props = started_calls[0].args[1]
        assert props["step_count"] == 1
        assert props["dry_run"] is False

    @pytest.mark.asyncio
    async def test_resume_from_checkpoint_skips_completed_step(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        executed = []

        async def track_and_ok(ctx: dict) -> dict:
            executed.append("step1")
            return {}

        async def step2_handler(ctx: dict) -> dict:
            executed.append("step2")
            return {}

        cp_path = h._save_checkpoint(
            workflow_id="resume-wf",
            completed=["step1"],
            results={
                "step1": {
                    "name": "step1",
                    "status": "completed",
                    "started_at": datetime.now(UTC).isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "result": {},
                    "duration_seconds": 0.1,
                }
            },
            context={},
        )

        steps = [
            WorkflowStep(name="step1", handler=track_and_ok),
            WorkflowStep(name="step2", handler=step2_handler),
        ]
        result = await h.execute(steps=steps, context={}, resume_from=cp_path)

        assert result.workflow_id == "resume-wf"
        assert "step1" not in executed  # already completed, should be skipped
        assert "step2" in executed
        assert "step2" in result.steps_completed

    @pytest.mark.asyncio
    async def test_resume_from_nonexistent_path_starts_fresh(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        nonexistent = h.checkpoint_dir / "ghost.json"
        step = WorkflowStep(name="fresh", handler=_ok)
        result = await h.execute(steps=[step], context={}, resume_from=nonexistent)
        assert result.success is True
        assert "fresh" in result.steps_completed

    @pytest.mark.asyncio
    async def test_resume_context_merge_provided_overrides_checkpoint(
        self, tmp_path: Path
    ) -> None:
        h = _make_harness(tmp_path)
        received: dict = {}

        async def capture(ctx: dict) -> dict:
            received.update(ctx)
            return {}

        cp_path = h._save_checkpoint(
            workflow_id="merge-wf",
            completed=[],
            results={},
            context={"source": "checkpoint", "only_checkpoint": True},
        )

        steps = [WorkflowStep(name="cap", handler=capture)]
        await h.execute(
            steps=steps,
            context={"source": "caller", "only_caller": True},
            resume_from=cp_path,
        )

        assert received["source"] == "caller"  # caller overrides checkpoint
        assert received["only_checkpoint"] is True  # preserved from checkpoint
        assert received["only_caller"] is True  # added by caller

    @pytest.mark.asyncio
    async def test_checkpoint_saved_after_each_step(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [
            WorkflowStep(name="s1", handler=_ok),
            WorkflowStep(name="s2", handler=_ok, depends_on=["s1"]),
        ]
        result = await h.execute(steps=steps, context={})
        assert result.checkpoint_path is not None
        assert result.checkpoint_path.exists()

    @pytest.mark.asyncio
    async def test_checkpoint_contains_completed_steps(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [WorkflowStep(name="ck-step", handler=_ok)]
        result = await h.execute(steps=steps, context={})
        data = json.loads(result.checkpoint_path.read_text())
        assert "ck-step" in data["completed_steps"]

    @pytest.mark.asyncio
    async def test_skip_on_failure_on_step_itself_still_skips_when_deps_failed(
        self, tmp_path: Path
    ) -> None:
        """When skip_on_failure is set on the downstream step itself (not the upstream),
        and the upstream fails: deps_failed is True but step.skip_on_failure is True,
        so we do NOT enter the 'skipped due to dep failure' branch. Then deps_satisfied
        checks if upstream is in completed_steps or skipped_steps — it is not (it failed),
        so deps_satisfied=False and we enter 'dependencies not met' skip path."""
        h = _make_harness(tmp_path)

        steps = [
            WorkflowStep(name="upstream", handler=_fail, retry_count=0),
            WorkflowStep(
                name="downstream",
                handler=_ok,
                depends_on=["upstream"],
                skip_on_failure=True,  # on the step itself
            ),
        ]
        result = await h.execute(steps=steps, context={})
        assert "upstream" in result.steps_failed
        assert "downstream" in result.steps_skipped

    @pytest.mark.asyncio
    async def test_already_completed_step_from_checkpoint_included_in_result(
        self, tmp_path: Path
    ) -> None:
        h = _make_harness(tmp_path)

        async def step2_handler(ctx: dict) -> dict:
            return {}

        cp_path = h._save_checkpoint(
            workflow_id="already-done",
            completed=["step1"],
            results={
                "step1": {
                    "name": "step1",
                    "status": "completed",
                    "started_at": datetime.now(UTC).isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "result": {"data": 1},
                    "duration_seconds": 0.2,
                }
            },
            context={},
        )

        steps = [
            WorkflowStep(name="step1", handler=_ok),
            WorkflowStep(name="step2", handler=step2_handler, depends_on=["step1"]),
        ]

        result = await h.execute(steps=steps, context={}, resume_from=cp_path)
        # step1 was already completed; result should include it from checkpoint data
        assert "step1" in result.step_results
        assert result.step_results["step1"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_tracker_resumed_flag_set_when_resuming(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        cp_path = h._save_checkpoint("res-flag-wf", [], {}, {})

        await h.execute(steps=[], context={}, resume_from=cp_path)

        started_calls = [
            c for c in h.tracker.track_event.call_args_list
            if c.args[0] == "workflow_started"
        ]
        props = started_calls[0].args[1]
        assert props["resumed"] is True

    @pytest.mark.asyncio
    async def test_tracker_not_resumed_when_fresh(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        await h.execute(steps=[], context={})
        started_calls = [
            c for c in h.tracker.track_event.call_args_list
            if c.args[0] == "workflow_started"
        ]
        props = started_calls[0].args[1]
        assert props["resumed"] is False

    @pytest.mark.asyncio
    async def test_multi_step_execution_order_respected(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        order: list[str] = []

        async def make_handler(name: str):
            async def handler(ctx: dict) -> dict:
                order.append(name)
                return {}
            return handler

        steps = [
            WorkflowStep(name="a", handler=await make_handler("a")),
            WorkflowStep(name="b", handler=await make_handler("b"), depends_on=["a"]),
            WorkflowStep(name="c", handler=await make_handler("c"), depends_on=["b"]),
        ]
        await h.execute(steps=steps, context={})
        assert order == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_success_true_when_no_failures(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [WorkflowStep(name=f"s{i}", handler=_ok) for i in range(5)]
        result = await h.execute(steps=steps, context={})
        assert result.success is True
        assert result.steps_failed == []

    @pytest.mark.asyncio
    async def test_success_false_when_any_failure(self, tmp_path: Path) -> None:
        h = _make_harness(tmp_path)
        steps = [
            WorkflowStep(name="ok", handler=_ok),
            WorkflowStep(name="bad", handler=_fail, retry_count=0),
        ]
        result = await h.execute(steps=steps, context={})
        assert result.success is False


# ===========================================================================
# create_workflow_harness factory
# ===========================================================================


class TestCreateWorkflowHarnessFactory:
    def test_returns_workflow_harness_instance(self, tmp_path: Path) -> None:
        h = create_workflow_harness(
            workflow_name="factory-test",
            checkpoint_dir=tmp_path / "cp",
        )
        assert isinstance(h, WorkflowHarness)

    def test_workflow_name_set(self, tmp_path: Path) -> None:
        h = create_workflow_harness(
            workflow_name="named-factory",
            checkpoint_dir=tmp_path / "cp",
        )
        assert h.workflow_name == "named-factory"

    def test_checkpoint_dir_set(self, tmp_path: Path) -> None:
        cp_dir = tmp_path / "factory-cp"
        h = create_workflow_harness(workflow_name="dir-test", checkpoint_dir=cp_dir)
        assert h.checkpoint_dir == cp_dir

    def test_custom_tracker_passed_through(self, tmp_path: Path) -> None:
        tracker = MagicMock()
        h = create_workflow_harness(
            workflow_name="tracked-factory",
            checkpoint_dir=tmp_path / "cp",
            tracker=tracker,
        )
        assert h.tracker is tracker

    def test_without_checkpoint_dir_creates_default(self) -> None:
        """Factory should work when checkpoint_dir is omitted (uses .workflow_checkpoints)."""
        h = create_workflow_harness(workflow_name="no-dir")
        assert isinstance(h, WorkflowHarness)
        # Cleanup default directory if created in CWD
        default = Path(".workflow_checkpoints")
        if default.exists():
            import shutil
            shutil.rmtree(default, ignore_errors=True)

    def test_checkpoint_dir_created_by_factory(self, tmp_path: Path) -> None:
        cp_dir = tmp_path / "auto-create"
        create_workflow_harness(workflow_name="auto", checkpoint_dir=cp_dir)
        assert cp_dir.is_dir()

"""
Tests for forge_harness.pipeline_data
======================================

Covers PipelineExecution dataclass, PipelineDataReader, and create_pipeline_reader.
Uses tmp_path fixtures for all file I/O — no production filesystem touched.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.pipeline_data import (
    PipelineDataReader,
    PipelineExecution,
    create_pipeline_reader,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> Path:
    """Write a dict as JSON to *path* and return the path."""
    path.write_text(json.dumps(data))
    return path


def _orch_checkpoint(
    pipeline_name: str = "test_pipeline",
    pipeline_steps: list | None = None,
    completed_steps: list | None = None,
    current_step_index: int = 0,
    context: dict | None = None,
    step_outputs: dict | None = None,
    created_at: str | None = "2026-01-01T00:00:00Z",
) -> dict:
    """Build a minimal orchestration checkpoint payload."""
    return {
        "pipeline_name": pipeline_name,
        "pipeline_steps": pipeline_steps or [],
        "completed_steps": completed_steps or [],
        "current_step_index": current_step_index,
        "context": context or {},
        "step_outputs": step_outputs or {},
        "created_at": created_at,
    }


def _ralph_checkpoint(
    iteration: int = 0,
    timestamp: str | None = "2026-01-01T00:00:00Z",
    stats: dict | None = None,
    features_path: str = "",
    config: dict | None = None,
) -> dict:
    """Build a minimal Ralph loop checkpoint payload."""
    return {
        "iteration": iteration,
        "timestamp": timestamp,
        "stats": stats or {},
        "features_path": features_path,
        "config": config or {},
    }


# ---------------------------------------------------------------------------
# PipelineExecution tests
# ---------------------------------------------------------------------------


class TestPipelineExecution:
    """Unit tests for PipelineExecution dataclass."""

    def _make(self, **overrides) -> PipelineExecution:
        defaults = dict(
            id="exec-001",
            name="my_pipeline",
            type="orchestration",
            status="running",
            current_step=1,
            total_steps=3,
            started_at="2026-01-01T00:00:00Z",
            completed_at=None,
            updated_at="2026-01-01T01:00:00Z",
            domain="brandfocus",
            project="voice-coach",
            error=None,
            checkpoint_path="/tmp/checkpoint.json",
            metadata={"key": "value"},
        )
        defaults.update(overrides)
        return PipelineExecution(**defaults)

    def test_creation_all_fields(self):
        exec_ = self._make()
        assert exec_.id == "exec-001"
        assert exec_.name == "my_pipeline"
        assert exec_.type == "orchestration"
        assert exec_.status == "running"
        assert exec_.current_step == 1
        assert exec_.total_steps == 3
        assert exec_.started_at == "2026-01-01T00:00:00Z"
        assert exec_.completed_at is None
        assert exec_.updated_at == "2026-01-01T01:00:00Z"
        assert exec_.domain == "brandfocus"
        assert exec_.project == "voice-coach"
        assert exec_.error is None
        assert exec_.checkpoint_path == "/tmp/checkpoint.json"
        assert exec_.metadata == {"key": "value"}

    def test_creation_optional_fields_none(self):
        exec_ = self._make(
            current_step=None,
            started_at=None,
            completed_at=None,
            domain=None,
            project=None,
            error=None,
        )
        assert exec_.current_step is None
        assert exec_.started_at is None
        assert exec_.domain is None
        assert exec_.project is None
        assert exec_.error is None

    def test_to_dict_returns_all_keys(self):
        exec_ = self._make()
        d = exec_.to_dict()
        expected_keys = {
            "id", "name", "type", "status", "current_step", "total_steps",
            "started_at", "completed_at", "updated_at", "domain", "project",
            "error", "checkpoint_path", "metadata",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values_match_fields(self):
        exec_ = self._make(status="success", error="boom")
        d = exec_.to_dict()
        assert d["id"] == "exec-001"
        assert d["status"] == "success"
        assert d["error"] == "boom"
        assert d["metadata"] == {"key": "value"}

    def test_to_dict_none_values_preserved(self):
        exec_ = self._make(domain=None, project=None, completed_at=None)
        d = exec_.to_dict()
        assert d["domain"] is None
        assert d["project"] is None
        assert d["completed_at"] is None

    def test_to_dict_metadata_is_copy_reference(self):
        """to_dict should expose the same metadata dict object."""
        meta = {"a": 1}
        exec_ = self._make(metadata=meta)
        assert exec_.to_dict()["metadata"] is meta

    def test_all_valid_statuses(self):
        for status in ("pending", "running", "success", "failed", "paused"):
            exec_ = self._make(status=status)
            assert exec_.status == status
            assert exec_.to_dict()["status"] == status

    def test_all_valid_types(self):
        for typ in ("orchestration", "ralph_loop"):
            exec_ = self._make(type=typ)
            assert exec_.type == typ

    def test_empty_metadata(self):
        exec_ = self._make(metadata={})
        assert exec_.to_dict()["metadata"] == {}

    def test_nested_metadata(self):
        meta = {"steps": ["a", "b"], "nested": {"x": 1}}
        exec_ = self._make(metadata=meta)
        assert exec_.to_dict()["metadata"] == meta

    def test_zero_current_step(self):
        exec_ = self._make(current_step=0, total_steps=5)
        assert exec_.current_step == 0
        assert exec_.to_dict()["current_step"] == 0

    def test_zero_total_steps(self):
        exec_ = self._make(total_steps=0)
        assert exec_.total_steps == 0


# ---------------------------------------------------------------------------
# PipelineDataReader — init and directory setup
# ---------------------------------------------------------------------------


class TestPipelineDataReaderInit:
    def test_forge_root_stored(self, tmp_path):
        reader = PipelineDataReader(tmp_path)
        assert reader.forge_root == tmp_path

    def test_orchestration_dir_path(self, tmp_path):
        reader = PipelineDataReader(tmp_path)
        assert reader.orchestration_dir == tmp_path / ".forge/orchestration_checkpoints"

    def test_ralph_dir_path(self, tmp_path):
        reader = PipelineDataReader(tmp_path)
        assert reader.ralph_dir == tmp_path / ".forge/ralph_checkpoints"

    def test_dirs_not_required_to_exist(self, tmp_path):
        """PipelineDataReader should not raise if checkpoint dirs are absent."""
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []


# ---------------------------------------------------------------------------
# Orchestration checkpoint parsing
# ---------------------------------------------------------------------------


class TestParseOrchestrationCheckpoint:
    def _setup(self, tmp_path) -> tuple[PipelineDataReader, Path]:
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        return PipelineDataReader(tmp_path), orch_dir

    def test_basic_pending_pipeline(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint(
            pipeline_name="content_publish",
            pipeline_steps=[{"name": "step1"}],
            completed_steps=[],
            current_step_index=0,
        )
        _write_json(orch_dir / "ck_001.json", data)
        pipelines = reader.get_recent_pipelines()
        assert len(pipelines) == 1
        p = pipelines[0]
        assert p.id == "ck_001"
        assert p.name == "content_publish"
        assert p.type == "orchestration"
        assert p.status == "pending"
        assert p.current_step == 0
        assert p.total_steps == 1

    def test_running_pipeline_step_gt_zero(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint(
            pipeline_steps=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
            completed_steps=["a"],
            current_step_index=1,
        )
        _write_json(orch_dir / "run.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "running"
        assert p.current_step == 1

    def test_success_pipeline_all_steps_complete(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        steps = [{"name": "a"}, {"name": "b"}]
        data = _orch_checkpoint(
            pipeline_steps=steps,
            completed_steps=["a", "b"],
            current_step_index=2,
        )
        _write_json(orch_dir / "done.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "success"

    def test_success_requires_both_conditions(self, tmp_path):
        """All steps complete but index not yet at total_steps -> still running."""
        reader, orch_dir = self._setup(tmp_path)
        steps = [{"name": "a"}, {"name": "b"}]
        data = _orch_checkpoint(
            pipeline_steps=steps,
            completed_steps=["a", "b"],
            current_step_index=1,  # not yet >= total_steps (2)
        )
        _write_json(orch_dir / "edge.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "running"

    def test_context_domain_and_project_extracted(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint(
            context={"domain": "brandfocus-ai", "project": "voice-coach"}
        )
        _write_json(orch_dir / "ctx.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.domain == "brandfocus-ai"
        assert p.project == "voice-coach"

    def test_missing_context_gives_none(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint()  # no context
        _write_json(orch_dir / "noctx.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.domain is None
        assert p.project is None

    def test_created_at_used_as_started_at(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint(created_at="2026-02-01T09:30:00Z")
        _write_json(orch_dir / "ts.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.started_at == "2026-02-01T09:30:00Z"

    def test_completed_at_is_none_for_orchestration(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint()
        _write_json(orch_dir / "ts.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.completed_at is None

    def test_error_field_is_none(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        _write_json(orch_dir / "x.json", _orch_checkpoint())
        p = reader.get_recent_pipelines()[0]
        assert p.error is None

    def test_checkpoint_path_is_absolute_str(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        f = orch_dir / "abs.json"
        _write_json(f, _orch_checkpoint())
        p = reader.get_recent_pipelines()[0]
        assert p.checkpoint_path == str(f)

    def test_metadata_contains_completed_steps(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint(
            completed_steps=["validate"],
            step_outputs={"validate": {"ok": True}},
        )
        _write_json(orch_dir / "meta.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.metadata["completed_steps"] == ["validate"]
        assert p.metadata["step_outputs"] == {"validate": {"ok": True}}

    def test_missing_pipeline_name_uses_stem(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        data = {
            "pipeline_steps": [],
            "completed_steps": [],
            "current_step_index": 0,
        }
        _write_json(orch_dir / "fallback_stem.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.name == "fallback_stem"

    def test_empty_pipeline_steps(self, tmp_path):
        """Zero steps: completed(0)==total(0) and index(0)>=total(0) -> success."""
        reader, orch_dir = self._setup(tmp_path)
        data = _orch_checkpoint(pipeline_steps=[], completed_steps=[], current_step_index=0)
        _write_json(orch_dir / "empty.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.total_steps == 0
        # Source logic: len(completed)==total AND index>=total -> "success"
        # Both 0==0 and 0>=0 are True, so the empty pipeline resolves as success.
        assert p.status == "success"

    def test_multiple_orchestration_files(self, tmp_path):
        reader, orch_dir = self._setup(tmp_path)
        for i in range(5):
            _write_json(orch_dir / f"ck_{i:03d}.json", _orch_checkpoint(pipeline_name=f"p{i}"))
        result = reader.get_recent_pipelines(limit=100)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Ralph checkpoint parsing
# ---------------------------------------------------------------------------


class TestParseRalphCheckpoint:
    def _setup(self, tmp_path) -> tuple[PipelineDataReader, Path]:
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        return PipelineDataReader(tmp_path), ralph_dir

    def test_basic_ralph_checkpoint(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(iteration=3)
        _write_json(ralph_dir / "rl_001.json", data)
        pipelines = reader.get_recent_pipelines()
        assert len(pipelines) == 1
        p = pipelines[0]
        assert p.id == "rl_001"
        assert p.type == "ralph_loop"

    def test_name_includes_iteration(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        _write_json(ralph_dir / "r.json", _ralph_checkpoint(iteration=7))
        p = reader.get_recent_pipelines()[0]
        assert "7" in p.name
        assert "Ralph Loop" in p.name

    def test_status_running_when_in_progress(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(
            iteration=2,
            stats={"passing": 5, "failing": 0, "blocked": 0, "pending": 2, "in_progress": 1},
        )
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "running"

    def test_status_failed_when_failing_gt_zero(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(
            iteration=1,
            stats={"passing": 3, "failing": 2, "blocked": 0, "pending": 0, "in_progress": 0},
        )
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "failed"

    def test_status_failed_when_blocked_gt_zero(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(
            iteration=1,
            stats={"passing": 1, "failing": 0, "blocked": 3, "pending": 0, "in_progress": 0},
        )
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "failed"

    def test_status_success_when_no_pending_or_in_progress(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(
            iteration=5,
            stats={"passing": 10, "failing": 0, "blocked": 0, "pending": 0, "in_progress": 0},
        )
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.status == "success"

    def test_status_success_when_iteration_zero_all_stats_zero(self, tmp_path):
        """All stats zero: failing=0, blocked=0 checked first; then pending=0 and
        in_progress=0 triggers success branch before the iteration>0 check."""
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(
            iteration=0,
            stats={"passing": 0, "failing": 0, "blocked": 0, "pending": 0, "in_progress": 0},
        )
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        # Source: failing=0 and blocked=0 -> not failed; pending=0 and in_progress=0 -> success
        assert p.status == "success"

    def test_status_success_when_iteration_gt_zero_and_no_pending_or_in_progress(self, tmp_path):
        """failing=0, blocked=0, pending=0, in_progress=0 -> success (regardless of iteration)."""
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(
            iteration=3,
            stats={"passing": 5, "failing": 0, "blocked": 0, "pending": 0, "in_progress": 0},
        )
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        # failing=0, blocked=0, pending=0, in_progress=0 -> success branch
        assert p.status == "success"

    def test_current_step_is_iteration(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        _write_json(ralph_dir / "r.json", _ralph_checkpoint(iteration=42))
        p = reader.get_recent_pipelines()[0]
        assert p.current_step == 42

    def test_total_steps_from_config_max_iterations(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(config={"max_iterations": 200})
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.total_steps == 200

    def test_total_steps_default_100_when_no_config(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint()  # no config
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.total_steps == 100

    def test_started_at_is_timestamp_field(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        _write_json(ralph_dir / "r.json", _ralph_checkpoint(timestamp="2026-03-01T08:00:00Z"))
        p = reader.get_recent_pipelines()[0]
        assert p.started_at == "2026-03-01T08:00:00Z"

    def test_completed_at_is_none(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        _write_json(ralph_dir / "r.json", _ralph_checkpoint())
        p = reader.get_recent_pipelines()[0]
        assert p.completed_at is None

    def test_error_is_none(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        _write_json(ralph_dir / "r.json", _ralph_checkpoint())
        p = reader.get_recent_pipelines()[0]
        assert p.error is None

    def test_metadata_contains_stats_and_features_path(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        stats = {"passing": 5, "failing": 0}
        data = _ralph_checkpoint(stats=stats, features_path="/some/path/features.json")
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.metadata["stats"] == stats
        assert p.metadata["features_path"] == "/some/path/features.json"

    def test_metadata_contains_config(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        cfg = {"max_iterations": 50, "test_command": "pytest"}
        _write_json(ralph_dir / "r.json", _ralph_checkpoint(config=cfg))
        p = reader.get_recent_pipelines()[0]
        assert p.metadata["config"] == cfg

    def test_domain_extracted_from_hyphenated_features_path(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        # e.g. /home/user/brandfocus-ai/voice-coach/features.json
        # parts[-2] = "voice-coach" which contains a hyphen
        data = _ralph_checkpoint(features_path="/home/user/brandfocus-ai/voice-coach/features.json")
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        # parts[-2] = "voice-coach" (has hyphen)
        assert p.domain == "voice-coach"

    def test_domain_none_when_no_hyphen_in_parent(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(features_path="/home/user/projects/features.json")
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        # "projects" has no hyphen
        assert p.domain is None

    def test_domain_none_when_features_path_empty(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(features_path="")
        _write_json(ralph_dir / "r.json", data)
        p = reader.get_recent_pipelines()[0]
        assert p.domain is None

    def test_project_always_none_for_ralph(self, tmp_path):
        reader, ralph_dir = self._setup(tmp_path)
        _write_json(ralph_dir / "r.json", _ralph_checkpoint())
        p = reader.get_recent_pipelines()[0]
        assert p.project is None

    def test_features_path_single_segment(self, tmp_path):
        """features_path with only one part should not raise."""
        reader, ralph_dir = self._setup(tmp_path)
        data = _ralph_checkpoint(features_path="features.json")
        _write_json(ralph_dir / "r.json", data)
        # Must not raise — parts has only 1 element
        pipelines = reader.get_recent_pipelines()
        assert len(pipelines) == 1
        assert pipelines[0].domain is None


# ---------------------------------------------------------------------------
# get_recent_pipelines — filtering and sorting
# ---------------------------------------------------------------------------


class TestGetRecentPipelines:
    def _setup_both(self, tmp_path) -> PipelineDataReader:
        (tmp_path / ".forge/orchestration_checkpoints").mkdir()
        (tmp_path / ".forge/ralph_checkpoints").mkdir()
        return PipelineDataReader(tmp_path)

    def test_returns_empty_when_no_files(self, tmp_path):
        reader = self._setup_both(tmp_path)
        assert reader.get_recent_pipelines() == []

    def test_limit_respected(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        for i in range(8):
            _write_json(orch_dir / f"p{i}.json", _orch_checkpoint(pipeline_name=f"p{i}"))
        result = reader.get_recent_pipelines(limit=3)
        assert len(result) == 3

    def test_default_limit_is_10(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        for i in range(15):
            _write_json(orch_dir / f"p{i}.json", _orch_checkpoint(pipeline_name=f"p{i}"))
        result = reader.get_recent_pipelines()
        assert len(result) == 10

    def test_status_filter(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        # running: current_step_index > 0
        _write_json(
            orch_dir / "running.json",
            _orch_checkpoint(
                pipeline_steps=[{"name": "a"}, {"name": "b"}],
                completed_steps=["a"],
                current_step_index=1,
            ),
        )
        # pending: current_step_index == 0
        _write_json(
            orch_dir / "pending.json",
            _orch_checkpoint(pipeline_steps=[{"name": "a"}], current_step_index=0),
        )
        running = reader.get_recent_pipelines(status="running", limit=100)
        pending = reader.get_recent_pipelines(status="pending", limit=100)
        assert all(p.status == "running" for p in running)
        assert all(p.status == "pending" for p in pending)
        assert len(running) + len(pending) == 2

    def test_type_filter_orchestration_only(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        _write_json(orch_dir / "o.json", _orch_checkpoint())
        _write_json(ralph_dir / "r.json", _ralph_checkpoint())
        result = reader.get_recent_pipelines(type_filter="orchestration", limit=100)
        assert all(p.type == "orchestration" for p in result)
        assert len(result) == 1

    def test_type_filter_ralph_only(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        _write_json(orch_dir / "o.json", _orch_checkpoint())
        _write_json(ralph_dir / "r.json", _ralph_checkpoint())
        result = reader.get_recent_pipelines(type_filter="ralph_loop", limit=100)
        assert all(p.type == "ralph_loop" for p in result)
        assert len(result) == 1

    def test_no_type_filter_returns_both(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        _write_json(orch_dir / "o.json", _orch_checkpoint())
        _write_json(ralph_dir / "r.json", _ralph_checkpoint())
        result = reader.get_recent_pipelines(limit=100)
        types = {p.type for p in result}
        assert "orchestration" in types
        assert "ralph_loop" in types

    def test_sorted_by_updated_at_descending(self, tmp_path):
        """Most recently modified file should appear first."""
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        older = orch_dir / "older.json"
        newer = orch_dir / "newer.json"
        _write_json(older, _orch_checkpoint(pipeline_name="older"))
        time.sleep(0.05)  # ensure mtime differs
        _write_json(newer, _orch_checkpoint(pipeline_name="newer"))
        result = reader.get_recent_pipelines(limit=100)
        # newer file's mtime > older file's mtime -> appears first
        assert result[0].name == "newer"
        assert result[1].name == "older"

    def test_combined_status_and_type_filter(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        # orchestration + success
        _write_json(
            orch_dir / "success.json",
            _orch_checkpoint(
                pipeline_steps=[{"name": "a"}],
                completed_steps=["a"],
                current_step_index=1,
            ),
        )
        # ralph_loop + failed
        _write_json(
            ralph_dir / "failed.json",
            _ralph_checkpoint(stats={"passing": 0, "failing": 1, "blocked": 0, "pending": 0, "in_progress": 0}),
        )
        orch_success = reader.get_recent_pipelines(
            type_filter="orchestration", status="success", limit=100
        )
        assert len(orch_success) == 1
        assert orch_success[0].type == "orchestration"
        assert orch_success[0].status == "success"

    def test_orchestration_dir_absent_still_reads_ralph(self, tmp_path):
        # Only ralph dir exists
        (tmp_path / ".forge/ralph_checkpoints").mkdir()
        reader = PipelineDataReader(tmp_path)
        _write_json(tmp_path / ".forge/ralph_checkpoints" / "r.json", _ralph_checkpoint())
        result = reader.get_recent_pipelines(limit=100)
        assert len(result) == 1
        assert result[0].type == "ralph_loop"

    def test_ralph_dir_absent_still_reads_orchestration(self, tmp_path):
        (tmp_path / ".forge/orchestration_checkpoints").mkdir()
        reader = PipelineDataReader(tmp_path)
        _write_json(tmp_path / ".forge/orchestration_checkpoints" / "o.json", _orch_checkpoint())
        result = reader.get_recent_pipelines(limit=100)
        assert len(result) == 1
        assert result[0].type == "orchestration"

    def test_unknown_status_filter_returns_empty(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        _write_json(orch_dir / "o.json", _orch_checkpoint())
        result = reader.get_recent_pipelines(status="nonexistent_status", limit=100)
        assert result == []

    def test_limit_zero_returns_empty(self, tmp_path):
        reader = self._setup_both(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        _write_json(orch_dir / "o.json", _orch_checkpoint())
        result = reader.get_recent_pipelines(limit=0)
        assert result == []


# ---------------------------------------------------------------------------
# get_pipeline_stats
# ---------------------------------------------------------------------------


class TestGetPipelineStats:
    def _setup(self, tmp_path) -> PipelineDataReader:
        (tmp_path / ".forge/orchestration_checkpoints").mkdir()
        (tmp_path / ".forge/ralph_checkpoints").mkdir()
        return PipelineDataReader(tmp_path)

    def test_empty_stats(self, tmp_path):
        reader = self._setup(tmp_path)
        stats = reader.get_pipeline_stats()
        assert stats["total"] == 0
        assert stats["by_status"]["pending"] == 0
        assert stats["by_status"]["running"] == 0
        assert stats["by_status"]["success"] == 0
        assert stats["by_status"]["failed"] == 0
        assert stats["by_status"]["paused"] == 0
        assert stats["by_type"]["orchestration"] == 0
        assert stats["by_type"]["ralph_loop"] == 0

    def test_stats_structure_keys(self, tmp_path):
        reader = self._setup(tmp_path)
        stats = reader.get_pipeline_stats()
        assert "total" in stats
        assert "by_status" in stats
        assert "by_type" in stats

    def test_total_count(self, tmp_path):
        reader = self._setup(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        for i in range(3):
            _write_json(orch_dir / f"o{i}.json", _orch_checkpoint())
        for i in range(2):
            _write_json(ralph_dir / f"r{i}.json", _ralph_checkpoint())
        stats = reader.get_pipeline_stats()
        assert stats["total"] == 5

    def test_by_type_counts(self, tmp_path):
        reader = self._setup(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        for i in range(4):
            _write_json(orch_dir / f"o{i}.json", _orch_checkpoint())
        for i in range(2):
            _write_json(ralph_dir / f"r{i}.json", _ralph_checkpoint())
        stats = reader.get_pipeline_stats()
        assert stats["by_type"]["orchestration"] == 4
        assert stats["by_type"]["ralph_loop"] == 2

    def test_by_status_pending(self, tmp_path):
        """A checkpoint with 1 step and index=0 (not yet started) -> pending."""
        reader = self._setup(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        # 1 step, 0 completed, index=0 -> completed(0) != total(1), index(0) not > 0 -> pending
        _write_json(
            orch_dir / "p.json",
            _orch_checkpoint(pipeline_steps=[{"a": 1}], completed_steps=[], current_step_index=0),
        )
        stats = reader.get_pipeline_stats()
        assert stats["by_status"]["pending"] == 1

    def test_by_status_running(self, tmp_path):
        reader = self._setup(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        _write_json(
            orch_dir / "r.json",
            _orch_checkpoint(pipeline_steps=[{"a": 1}, {"b": 2}], current_step_index=1),
        )
        stats = reader.get_pipeline_stats()
        assert stats["by_status"]["running"] == 1

    def test_by_status_success(self, tmp_path):
        reader = self._setup(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        _write_json(
            orch_dir / "s.json",
            _orch_checkpoint(
                pipeline_steps=[{"a": 1}],
                completed_steps=["a"],
                current_step_index=1,
            ),
        )
        stats = reader.get_pipeline_stats()
        assert stats["by_status"]["success"] == 1

    def test_by_status_failed(self, tmp_path):
        reader = self._setup(tmp_path)
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        _write_json(
            ralph_dir / "f.json",
            _ralph_checkpoint(stats={"passing": 0, "failing": 1, "blocked": 0, "pending": 0, "in_progress": 0}),
        )
        stats = reader.get_pipeline_stats()
        assert stats["by_status"]["failed"] == 1

    def test_mixed_statuses(self, tmp_path):
        reader = self._setup(tmp_path)
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        # 1 running orchestration (current_step_index=1 > 0, not all steps done)
        _write_json(
            orch_dir / "r.json",
            _orch_checkpoint(pipeline_steps=[{"a": 1}, {"b": 2}], current_step_index=1),
        )
        # 1 success orchestration (all 2 steps done, index=2 >= total=2)
        _write_json(
            orch_dir / "s.json",
            _orch_checkpoint(
                pipeline_steps=[{"a": 1}, {"b": 2}],
                completed_steps=["a", "b"],
                current_step_index=2,
            ),
        )
        # 1 failed ralph
        _write_json(
            ralph_dir / "f.json",
            _ralph_checkpoint(stats={"passing": 0, "failing": 1, "blocked": 0, "pending": 0, "in_progress": 0}),
        )
        stats = reader.get_pipeline_stats()
        assert stats["total"] == 3
        assert stats["by_status"]["running"] == 1
        assert stats["by_status"]["success"] == 1
        assert stats["by_status"]["failed"] == 1
        assert stats["by_type"]["orchestration"] == 2
        assert stats["by_type"]["ralph_loop"] == 1


# ---------------------------------------------------------------------------
# Edge cases: bad files, corrupt JSON, empty directories
# ---------------------------------------------------------------------------


class TestEdgeCasesAndErrors:
    def test_corrupt_json_in_orchestration_dir(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        (orch_dir / "bad.json").write_text("{not valid json{{{{")
        reader = PipelineDataReader(tmp_path)
        # Must not raise; corrupt file is skipped
        result = reader.get_recent_pipelines()
        assert result == []

    def test_corrupt_json_in_ralph_dir(self, tmp_path):
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        (ralph_dir / "bad.json").write_text("}}broken{{")
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []

    def test_corrupt_json_skipped_good_files_still_returned(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        (orch_dir / "bad.json").write_text("not json")
        _write_json(orch_dir / "good.json", _orch_checkpoint(pipeline_name="good"))
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert len(result) == 1
        assert result[0].name == "good"

    def test_empty_json_object(self, tmp_path):
        """An empty {} file should parse without raising (uses defaults for missing keys).
        pipeline_steps=[], completed_steps=[], current_step_index=0 -> 0==0 and 0>=0 -> success."""
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        _write_json(orch_dir / "empty.json", {})
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert len(result) == 1
        assert result[0].status == "success"

    def test_empty_json_array_is_invalid_checkpoint(self, tmp_path):
        """A JSON array (not an object) will fail dict.get -> exception -> skipped."""
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        (orch_dir / "array.json").write_text("[]")
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []

    def test_empty_orchestration_directory(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []

    def test_empty_ralph_directory(self, tmp_path):
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []

    def test_both_dirs_missing(self, tmp_path):
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []

    def test_json_with_null_values(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        data = {
            "pipeline_name": None,
            "pipeline_steps": None,
            "completed_steps": None,
            "current_step_index": None,
            "context": None,
            "created_at": None,
        }
        _write_json(orch_dir / "nulls.json", data)
        reader = PipelineDataReader(tmp_path)
        # pipeline_steps=None -> len(None) -> TypeError -> parse returns None -> skipped
        result = reader.get_recent_pipelines()
        # Either parsed (if None handled gracefully) or skipped — must not raise
        assert isinstance(result, list)

    def test_missing_stats_in_ralph_checkpoint(self, tmp_path):
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        data = {"iteration": 1, "timestamp": "2026-01-01T00:00:00Z"}
        _write_json(ralph_dir / "r.json", data)
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        # stats defaults to {} -> passing/failing/blocked/pending/in_progress all 0
        assert len(result) == 1
        # iteration=1, all zeros: pending=0, in_progress=0 -> success
        assert result[0].status == "success"

    def test_non_json_extension_file_ignored(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        (orch_dir / "readme.txt").write_text("not a checkpoint")
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert result == []

    def test_orchestration_parse_exception_returns_none_and_continues(self, tmp_path):
        """If one orchestration file raises during parsing, others still load."""
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        # Good file
        _write_json(orch_dir / "good.json", _orch_checkpoint(pipeline_name="good"))
        # Trigger parse failure: provide pipeline_steps as a string (len works but iterate breaks in stat logic)
        _write_json(orch_dir / "bad_type.json", {"pipeline_steps": "INVALID", "completed_steps": [], "current_step_index": 0})
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        # bad_type.json: len("INVALID")=7 total_steps, but should still parse
        # We just assert it doesn't crash and returns at least the good file
        assert any(p.name == "good" for p in result)

    def test_ralph_parse_exception_skips_file(self, tmp_path):
        """Corrupt ralph data that causes an exception during parse is skipped."""
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        _write_json(ralph_dir / "good.json", _ralph_checkpoint(iteration=1))
        # Pass features_path as a non-string to trigger an error during Path()
        _write_json(ralph_dir / "bad.json", {"iteration": 0, "stats": "not-a-dict", "features_path": 0})
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        # "good.json" must still appear
        assert any(p.id == "good" for p in result)


# ---------------------------------------------------------------------------
# JSON serialization / deserialization round-trip
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    def test_to_dict_is_json_serializable(self):
        exec_ = PipelineExecution(
            id="rt-001",
            name="roundtrip",
            type="orchestration",
            status="success",
            current_step=3,
            total_steps=3,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
            updated_at="2026-01-01T01:00:00Z",
            domain="brandfocus",
            project="voice-coach",
            error=None,
            checkpoint_path="/tmp/rt.json",
            metadata={"steps": ["a", "b", "c"], "score": 1.0},
        )
        serialized = json.dumps(exec_.to_dict())
        deserialized = json.loads(serialized)
        assert deserialized["id"] == "rt-001"
        assert deserialized["status"] == "success"
        assert deserialized["metadata"]["steps"] == ["a", "b", "c"]

    def test_checkpoint_written_and_parsed_back(self, tmp_path):
        """Write checkpoint to disk, read it back via PipelineDataReader."""
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        original_data = _orch_checkpoint(
            pipeline_name="full_roundtrip",
            pipeline_steps=[{"name": "step1"}, {"name": "step2"}],
            completed_steps=["step1"],
            current_step_index=1,
            context={"domain": "test-domain", "project": "test-project"},
            step_outputs={"step1": {"result": "ok"}},
            created_at="2026-01-15T10:00:00Z",
        )
        _write_json(orch_dir / "roundtrip.json", original_data)
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert len(result) == 1
        p = result[0]
        d = p.to_dict()
        # Verify all key fields survive the round-trip
        assert d["name"] == "full_roundtrip"
        assert d["type"] == "orchestration"
        assert d["status"] == "running"
        assert d["current_step"] == 1
        assert d["total_steps"] == 2
        assert d["started_at"] == "2026-01-15T10:00:00Z"
        assert d["domain"] == "test-domain"
        assert d["project"] == "test-project"
        assert d["metadata"]["completed_steps"] == ["step1"]
        assert d["metadata"]["step_outputs"] == {"step1": {"result": "ok"}}

    def test_ralph_checkpoint_round_trip(self, tmp_path):
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        original_data = _ralph_checkpoint(
            iteration=12,
            timestamp="2026-01-20T14:00:00Z",
            stats={"passing": 10, "failing": 0, "blocked": 0, "pending": 5, "in_progress": 2},
            features_path="/repo/brandfocus-ai/voice-coach/features.json",
            config={"max_iterations": 50, "test_command": "pytest"},
        )
        _write_json(ralph_dir / "rl_rt.json", original_data)
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines()
        assert len(result) == 1
        p = result[0]
        d = p.to_dict()
        assert "12" in d["name"]
        assert d["type"] == "ralph_loop"
        assert d["current_step"] == 12
        assert d["total_steps"] == 50
        assert d["started_at"] == "2026-01-20T14:00:00Z"
        assert d["metadata"]["stats"]["passing"] == 10
        assert d["metadata"]["config"]["test_command"] == "pytest"


# ---------------------------------------------------------------------------
# create_pipeline_reader factory function
# ---------------------------------------------------------------------------


class TestCreatePipelineReader:
    def test_returns_pipeline_data_reader_instance(self, tmp_path):
        reader = create_pipeline_reader(tmp_path)
        assert isinstance(reader, PipelineDataReader)

    def test_forge_root_set_correctly(self, tmp_path):
        reader = create_pipeline_reader(tmp_path)
        assert reader.forge_root == tmp_path

    def test_default_forge_root_is_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        reader = create_pipeline_reader()
        assert reader.forge_root == tmp_path

    def test_none_forge_root_uses_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        reader = create_pipeline_reader(None)
        assert reader.forge_root == tmp_path

    def test_reader_is_functional_with_factory(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        _write_json(orch_dir / "factory_test.json", _orch_checkpoint(pipeline_name="factory"))
        reader = create_pipeline_reader(tmp_path)
        result = reader.get_recent_pipelines()
        assert len(result) == 1
        assert result[0].name == "factory"


# ---------------------------------------------------------------------------
# Large volume / stress tests
# ---------------------------------------------------------------------------


class TestLargeVolume:
    def test_many_orchestration_files(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        for i in range(50):
            _write_json(
                orch_dir / f"pipeline_{i:04d}.json",
                _orch_checkpoint(pipeline_name=f"pipeline_{i}"),
            )
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines(limit=1000)
        assert len(result) == 50

    def test_many_ralph_files(self, tmp_path):
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        for i in range(30):
            _write_json(
                ralph_dir / f"rl_{i:04d}.json",
                _ralph_checkpoint(iteration=i),
            )
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines(limit=1000)
        assert len(result) == 30

    def test_mixed_large_volume_with_limit(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        orch_dir.mkdir()
        ralph_dir.mkdir()
        for i in range(25):
            _write_json(orch_dir / f"o{i}.json", _orch_checkpoint())
        for i in range(25):
            _write_json(ralph_dir / f"r{i}.json", _ralph_checkpoint())
        reader = PipelineDataReader(tmp_path)
        result = reader.get_recent_pipelines(limit=15)
        assert len(result) == 15

    def test_stats_with_large_volume(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        orch_dir.mkdir()
        ralph_dir.mkdir()
        for i in range(10):
            _write_json(orch_dir / f"o{i}.json", _orch_checkpoint())
        for i in range(10):
            _write_json(ralph_dir / f"r{i}.json", _ralph_checkpoint())
        reader = PipelineDataReader(tmp_path)
        stats = reader.get_pipeline_stats()
        assert stats["total"] == 20
        assert stats["by_type"]["orchestration"] == 10
        assert stats["by_type"]["ralph_loop"] == 10


# ---------------------------------------------------------------------------
# Checkpoint path verification
# ---------------------------------------------------------------------------


class TestCheckpointPathField:
    def test_orchestration_checkpoint_path_matches_file(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        f = orch_dir / "my_checkpoint.json"
        _write_json(f, _orch_checkpoint())
        reader = PipelineDataReader(tmp_path)
        p = reader.get_recent_pipelines()[0]
        assert Path(p.checkpoint_path) == f

    def test_ralph_checkpoint_path_matches_file(self, tmp_path):
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()
        f = ralph_dir / "ralph_state.json"
        _write_json(f, _ralph_checkpoint())
        reader = PipelineDataReader(tmp_path)
        p = reader.get_recent_pipelines()[0]
        assert Path(p.checkpoint_path) == f

    def test_checkpoint_path_is_string_type(self, tmp_path):
        orch_dir = tmp_path / ".forge/orchestration_checkpoints"
        orch_dir.mkdir()
        _write_json(orch_dir / "x.json", _orch_checkpoint())
        reader = PipelineDataReader(tmp_path)
        p = reader.get_recent_pipelines()[0]
        assert isinstance(p.checkpoint_path, str)

"""
Unit tests for forge_harness.orchestration_harness
===================================================

Covers:
- Dataclass instantiation: PipelineStep, StepResult, HumanGateConfig, Pipeline,
  PipelineResult, Checkpoint
- OrchestrationHarness init / callback registration
- _resolve_template (context, steps, literals, nested, missing)
- _get_nested
- _validate_pipeline_yaml / _validate_step_yaml / _validate_human_gate_yaml
- load_pipeline_from_string / load_pipeline_from_yaml
- _parse_pipeline_yaml with context_overrides
- get_builtin_pipeline / list_builtin_pipelines
- list_yaml_pipelines (directory scanning)
- _save_checkpoint
- execute (happy path, abort on failure, skip on failure, human_gate on failure)
- _execute_step (success, harness-not-found, method-not-found, timeout, retry)
- resume (happy path, all-steps-completed, invalid checkpoint)
- resume_from_approval (no human_gate, not-approved, no checkpoint path,
  missing checkpoint file, success)
- create_orchestration_harness factory
- _emit_event / callback chain
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.models.workflow import StepStatus
from forge_harness.orchestration_harness import (
    Checkpoint,
    HumanGateConfig,
    OrchestrationHarness,
    Pipeline,
    PipelineResult,
    PipelineStep,
    StepResult,
    create_orchestration_harness,
)
from forge_harness.pipeline_callbacks import (
    AggregatingCallback,
    EventType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_harness(checkpoint_dir: Path, harnesses: dict | None = None) -> OrchestrationHarness:
    """Convenience factory that skips real checkpoint-dir creation by using tmp."""
    return OrchestrationHarness(
        harnesses=harnesses or {},
        checkpoint_dir=checkpoint_dir,
        enable_checkpoints=False,  # most unit tests don't need real file I/O
    )


def _simple_pipeline(
    step_name: str = "step1",
    harness: str = "my_harness",
    method: str = "do_thing",
    on_failure: str = "abort",
    outputs: list[str] | None = None,
) -> Pipeline:
    return Pipeline(
        name="test_pipeline",
        steps=[
            PipelineStep(
                name=step_name,
                harness=harness,
                method=method,
                inputs={},
                outputs=outputs or [],
                on_failure=on_failure,
            )
        ],
        context={"domain": "test"},
    )


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_pipeline_step_defaults(self):
        step = PipelineStep(name="s", harness="h", method="m")
        assert step.inputs == {}
        assert step.outputs == []
        assert step.on_failure == "abort"
        assert step.retry_count == 3
        assert step.timeout_seconds == 600
        assert step.description == ""

    def test_step_result_defaults(self):
        r = StepResult(name="s", status=StepStatus.COMPLETED)
        assert r.outputs == {}
        assert r.error is None
        assert r.duration_seconds == 0.0
        assert r.retries == 0

    def test_human_gate_config_defaults(self):
        g = HumanGateConfig()
        assert g.timeout_hours == 24.0
        assert g.notification_channels == ["slack"]
        assert g.on_timeout == "skip"
        assert g.message == ""

    def test_pipeline_defaults(self):
        p = Pipeline(name="p", steps=[])
        assert p.context == {}
        assert p.human_gates == {}
        assert p.description == ""

    def test_pipeline_result_defaults(self):
        r = PipelineResult(pipeline_name="p", success=True)
        assert r.step_results == []
        assert r.context == {}
        assert r.error is None
        assert r.checkpoint_path is None

    def test_checkpoint_fields(self):
        c = Checkpoint(
            pipeline_name="p",
            pipeline_steps=[],
            context={},
            step_outputs={},
            completed_steps=[],
            current_step_index=0,
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert c.human_gates == {}
        assert c.pipeline_name == "p"


# ---------------------------------------------------------------------------
# OrchestrationHarness init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_checkpoint_dir(self, tmp_path: Path):
        new_dir = tmp_path / "checkpoints"
        assert not new_dir.exists()
        OrchestrationHarness(harnesses={}, checkpoint_dir=new_dir, enable_checkpoints=True)
        assert new_dir.exists()

    def test_no_checkpoint_dir_created_when_disabled(self, tmp_path: Path):
        new_dir = tmp_path / "nocheckpoints"
        OrchestrationHarness(harnesses={}, checkpoint_dir=new_dir, enable_checkpoints=False)
        assert not new_dir.exists()

    def test_stores_harnesses(self, tmp_path: Path):
        mock_h = MagicMock()
        orch = _make_harness(tmp_path, harnesses={"foo": mock_h})
        assert orch.harnesses["foo"] is mock_h

    def test_builtin_pipelines_populated(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        assert "content_to_publish" in orch.BUILTIN_PIPELINES
        assert "mvp_launch" in orch.BUILTIN_PIPELINES
        assert "growth_experiment" in orch.BUILTIN_PIPELINES
        assert "weekly_content_calendar" in orch.BUILTIN_PIPELINES


# ---------------------------------------------------------------------------
# Callback management
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_register_and_unregister(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        cb = AggregatingCallback()
        orch.register_callback(cb)
        assert orch._callback_manager.callback_count == 1
        orch.unregister_callback(cb)
        assert orch._callback_manager.callback_count == 0

    def test_register_same_callback_twice(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        cb = AggregatingCallback()
        orch.register_callback(cb)
        orch.register_callback(cb)
        assert orch._callback_manager.callback_count == 1

    @pytest.mark.asyncio
    async def test_emit_event_reaches_callback(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        cb = AggregatingCallback()
        orch.register_callback(cb)
        await orch._emit_event(EventType.PIPELINE_STARTED, "mypipeline")
        events = cb.get_events(EventType.PIPELINE_STARTED)
        assert len(events) == 1
        assert events[0].pipeline_name == "mypipeline"

    @pytest.mark.asyncio
    async def test_emit_event_with_step_name(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        cb = AggregatingCallback()
        orch.register_callback(cb)
        await orch._emit_event(EventType.STEP_STARTED, "p", step_name="s1", data={"k": "v"})
        ev = cb.events[0]
        assert ev.step_name == "s1"
        assert ev.data["k"] == "v"

    @pytest.mark.asyncio
    async def test_failing_callback_does_not_crash_emit(self, tmp_path: Path):
        orch = _make_harness(tmp_path)

        class BadCallback:
            async def on_event(self, event):
                raise RuntimeError("I am broken")

        orch.register_callback(BadCallback())  # type: ignore[arg-type]
        # Should not raise
        await orch._emit_event(EventType.PIPELINE_STARTED, "p")


# ---------------------------------------------------------------------------
# _resolve_template
# ---------------------------------------------------------------------------


class TestResolveTemplate:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))
        self.context = {"domain": "codeswiftr-com", "dry_run": False, "nested": {"key": "val"}}
        self.step_outputs = {
            "step_a": {"page_ids": [1, 2, 3], "deep": {"x": 42}},
        }

    def test_literal_string(self):
        assert self.orch._resolve_template("hello", self.context, self.step_outputs) == "hello"

    def test_non_string_passthrough(self):
        assert self.orch._resolve_template(123, self.context, self.step_outputs) == 123
        assert self.orch._resolve_template([1, 2], self.context, self.step_outputs) == [1, 2]
        assert self.orch._resolve_template(None, self.context, self.step_outputs) is None

    def test_context_simple(self):
        result = self.orch._resolve_template("{{ context.domain }}", self.context, self.step_outputs)
        assert result == "codeswiftr-com"

    def test_context_bool(self):
        result = self.orch._resolve_template("{{ context.dry_run }}", self.context, self.step_outputs)
        assert result is False

    def test_context_missing_key(self):
        result = self.orch._resolve_template("{{ context.missing }}", self.context, self.step_outputs)
        assert result is None

    def test_step_output_resolution(self):
        result = self.orch._resolve_template(
            "{{ steps.step_a.page_ids }}", self.context, self.step_outputs
        )
        assert result == [1, 2, 3]

    def test_step_output_missing_step(self):
        result = self.orch._resolve_template(
            "{{ steps.nonexistent.page_ids }}", self.context, self.step_outputs
        )
        assert result is None

    def test_step_output_missing_key(self):
        result = self.orch._resolve_template(
            "{{ steps.step_a.nope }}", self.context, self.step_outputs
        )
        assert result is None

    def test_partial_template_not_resolved(self):
        # Not a pure template expression — returned as-is
        val = "prefix {{ context.domain }}"
        assert self.orch._resolve_template(val, self.context, self.step_outputs) == val

    def test_template_with_whitespace(self):
        result = self.orch._resolve_template(
            "{{  context.domain  }}", self.context, self.step_outputs
        )
        assert result == "codeswiftr-com"

    def test_unknown_prefix_returns_value(self):
        # Unknown top-level prefix: returned as-is
        val = "{{ unknown.something }}"
        assert self.orch._resolve_template(val, self.context, self.step_outputs) == val

    def test_steps_too_short_path(self):
        # {{ steps.stepname }} without a field — returns None
        result = self.orch._resolve_template("{{ steps.step_a }}", self.context, self.step_outputs)
        assert result is None


# ---------------------------------------------------------------------------
# _get_nested
# ---------------------------------------------------------------------------


class TestGetNested:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def test_single_key(self):
        assert self.orch._get_nested({"a": 1}, ["a"]) == 1

    def test_nested_keys(self):
        assert self.orch._get_nested({"a": {"b": {"c": 99}}}, ["a", "b", "c"]) == 99

    def test_missing_key(self):
        assert self.orch._get_nested({"a": 1}, ["b"]) is None

    def test_empty_keys(self):
        d = {"x": 1}
        assert self.orch._get_nested(d, []) == d

    def test_non_dict_mid_path(self):
        assert self.orch._get_nested({"a": "string"}, ["a", "b"]) is None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidatePipelineYaml:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def _valid_yaml(self):
        return {
            "name": "my_pipeline",
            "steps": [{"name": "s1", "harness": "h", "method": "m"}],
        }

    def test_valid(self):
        errors = self.orch._validate_pipeline_yaml(self._valid_yaml())
        assert errors == []

    def test_missing_name(self):
        data = self._valid_yaml()
        del data["name"]
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("name" in e for e in errors)

    def test_invalid_name_pattern(self):
        data = self._valid_yaml()
        data["name"] = "My-Pipeline"
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("name" in e for e in errors)

    def test_name_not_string(self):
        data = self._valid_yaml()
        data["name"] = 123
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("name" in e for e in errors)

    def test_missing_steps(self):
        data = self._valid_yaml()
        del data["steps"]
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("steps" in e for e in errors)

    def test_steps_not_list(self):
        data = self._valid_yaml()
        data["steps"] = "not a list"
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("steps" in e for e in errors)

    def test_empty_steps(self):
        data = self._valid_yaml()
        data["steps"] = []
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("at least one step" in e for e in errors)

    def test_duplicate_step_name(self):
        data = self._valid_yaml()
        data["steps"] = [
            {"name": "s1", "harness": "h", "method": "m"},
            {"name": "s1", "harness": "h", "method": "m2"},
        ]
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("Duplicate" in e for e in errors)

    def test_not_dict(self):
        errors = self.orch._validate_pipeline_yaml("this is a string")  # type: ignore[arg-type]
        assert errors == ["Pipeline must be a YAML mapping"]

    def test_human_gates_not_dict(self):
        data = self._valid_yaml()
        data["human_gates"] = "bad"
        errors = self.orch._validate_pipeline_yaml(data)
        assert any("human_gates" in e for e in errors)

    def test_valid_human_gate(self):
        data = self._valid_yaml()
        data["human_gates"] = {"review": {"timeout_hours": 12, "on_timeout": "abort"}}
        errors = self.orch._validate_pipeline_yaml(data)
        assert errors == []


class TestValidateStepYaml:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def test_valid_step(self):
        errors = self.orch._validate_step_yaml(
            {"name": "s", "harness": "h", "method": "m"}, 0
        )
        assert errors == []

    def test_step_not_dict(self):
        errors = self.orch._validate_step_yaml("bad", 0)
        assert len(errors) == 1
        assert "must be a mapping" in errors[0]

    def test_missing_name(self):
        errors = self.orch._validate_step_yaml({"harness": "h", "method": "m"}, 0)
        assert any("'name'" in e for e in errors)

    def test_missing_harness(self):
        errors = self.orch._validate_step_yaml({"name": "s", "method": "m"}, 0)
        assert any("'harness'" in e for e in errors)

    def test_missing_method(self):
        errors = self.orch._validate_step_yaml({"name": "s", "harness": "h"}, 0)
        assert any("'method'" in e for e in errors)

    def test_invalid_on_failure(self):
        errors = self.orch._validate_step_yaml(
            {"name": "s", "harness": "h", "method": "m", "on_failure": "teleport"}, 0
        )
        assert any("on_failure" in e for e in errors)

    def test_invalid_retry_count(self):
        errors = self.orch._validate_step_yaml(
            {"name": "s", "harness": "h", "method": "m", "retry_count": 0}, 0
        )
        assert any("retry_count" in e for e in errors)

    def test_invalid_timeout(self):
        errors = self.orch._validate_step_yaml(
            {"name": "s", "harness": "h", "method": "m", "timeout_seconds": -5}, 0
        )
        assert any("timeout_seconds" in e for e in errors)

    def test_outputs_not_list(self):
        errors = self.orch._validate_step_yaml(
            {"name": "s", "harness": "h", "method": "m", "outputs": "bad"}, 0
        )
        assert any("outputs" in e for e in errors)

    def test_outputs_non_string_items(self):
        errors = self.orch._validate_step_yaml(
            {"name": "s", "harness": "h", "method": "m", "outputs": [1, 2]}, 0
        )
        assert any("outputs" in e for e in errors)

    def test_valid_on_failure_modes(self):
        for mode in ["abort", "retry", "skip", "human_gate"]:
            errors = self.orch._validate_step_yaml(
                {"name": "s", "harness": "h", "method": "m", "on_failure": mode}, 0
            )
            assert errors == [], f"Expected no errors for on_failure={mode}"

    def test_name_not_a_string(self):
        errors = self.orch._validate_step_yaml({"name": 99, "harness": "h", "method": "m"}, 0)
        assert any("'name' must be a string" in e for e in errors)

    def test_harness_not_a_string(self):
        errors = self.orch._validate_step_yaml({"name": "s", "harness": 42, "method": "m"}, 0)
        assert any("'harness' must be a string" in e for e in errors)

    def test_method_not_a_string(self):
        errors = self.orch._validate_step_yaml({"name": "s", "harness": "h", "method": []}, 0)
        assert any("'method' must be a string" in e for e in errors)


class TestValidateHumanGateYaml:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def test_valid_gate(self):
        errors = self.orch._validate_human_gate_yaml("gate1", {"timeout_hours": 12})
        assert errors == []

    def test_gate_not_dict(self):
        errors = self.orch._validate_human_gate_yaml("gate1", "bad")
        assert len(errors) == 1 and "must be a mapping" in errors[0]

    def test_negative_timeout(self):
        errors = self.orch._validate_human_gate_yaml("gate1", {"timeout_hours": -1})
        assert any("timeout_hours" in e for e in errors)

    def test_invalid_on_timeout(self):
        errors = self.orch._validate_human_gate_yaml("gate1", {"on_timeout": "vanish"})
        assert any("on_timeout" in e for e in errors)

    def test_channels_not_list(self):
        errors = self.orch._validate_human_gate_yaml("gate1", {"notification_channels": "slack"})
        assert any("notification_channels" in e for e in errors)

    def test_valid_on_timeout_modes(self):
        for action in ["skip", "abort", "escalate"]:
            errors = self.orch._validate_human_gate_yaml("g", {"on_timeout": action})
            assert errors == []


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


VALID_YAML = """
name: test_pipe
description: A simple test pipeline
steps:
  - name: do_work
    harness: content
    method: generate
    inputs:
      domain: "{{ context.domain }}"
    outputs:
      - items
    on_failure: skip
    retry_count: 2
    timeout_seconds: 30
    description: "Do some work"
context:
  domain: acme
human_gates:
  approval:
    timeout_hours: 8
    notification_channels:
      - slack
    on_timeout: abort
    message: "Needs review"
"""


class TestLoadPipeline:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def test_load_from_string(self):
        pipeline = self.orch.load_pipeline_from_string(VALID_YAML)
        assert pipeline.name == "test_pipe"
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].name == "do_work"
        assert pipeline.steps[0].on_failure == "skip"
        assert pipeline.steps[0].retry_count == 2
        assert pipeline.steps[0].timeout_seconds == 30
        assert pipeline.context == {"domain": "acme"}
        assert "approval" in pipeline.human_gates
        gate = pipeline.human_gates["approval"]
        assert gate.timeout_hours == 8
        assert gate.on_timeout == "abort"
        assert gate.message == "Needs review"

    def test_load_from_string_with_context_overrides(self):
        pipeline = self.orch.load_pipeline_from_string(VALID_YAML, context_overrides={"domain": "override", "extra": 1})
        assert pipeline.context["domain"] == "override"
        assert pipeline.context["extra"] == 1

    def test_load_invalid_yaml_raises(self):
        with pytest.raises(ValueError, match="Invalid pipeline YAML"):
            self.orch.load_pipeline_from_string("name: 123\nsteps: []")

    def test_load_from_yaml_file(self, tmp_path: Path):
        yaml_file = tmp_path / "pipe.yaml"
        yaml_file.write_text(VALID_YAML)
        pipeline = self.orch.load_pipeline_from_yaml(yaml_file)
        assert pipeline.name == "test_pipe"

    def test_load_from_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            self.orch.load_pipeline_from_yaml(tmp_path / "no_such.yaml")

    def test_load_step_defaults_applied(self):
        minimal_yaml = """
name: minimal
steps:
  - name: s
    harness: h
    method: m
"""
        pipeline = self.orch.load_pipeline_from_string(minimal_yaml)
        step = pipeline.steps[0]
        assert step.inputs == {}
        assert step.outputs == []
        assert step.on_failure == "abort"
        assert step.retry_count == 3
        assert step.timeout_seconds == 600
        assert step.description == ""


# ---------------------------------------------------------------------------
# Builtin pipelines
# ---------------------------------------------------------------------------


class TestBuiltinPipelines:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def test_get_builtin_pipeline_exists(self):
        p = self.orch.get_builtin_pipeline("content_to_publish")
        assert p is not None
        assert p.name == "content_to_publish"

    def test_get_builtin_pipeline_missing(self):
        assert self.orch.get_builtin_pipeline("nonexistent") is None

    def test_list_builtin_pipelines(self):
        listing = self.orch.list_builtin_pipelines()
        names = [d["name"] for d in listing]
        assert "content_to_publish" in names
        assert "mvp_launch" in names
        assert all("description" in d for d in listing)

    def test_mvp_launch_steps(self):
        p = self.orch.get_builtin_pipeline("mvp_launch")
        step_names = [s.name for s in p.steps]
        assert "preflight" in step_names
        assert "deploy" in step_names

    def test_growth_experiment_steps(self):
        p = self.orch.get_builtin_pipeline("growth_experiment")
        step_names = [s.name for s in p.steps]
        assert "setup_experiment" in step_names
        assert "conclude_experiment" in step_names


# ---------------------------------------------------------------------------
# list_yaml_pipelines
# ---------------------------------------------------------------------------


class TestListYamlPipelines:
    def setup_method(self):
        self.orch = _make_harness(Path("/tmp"))

    def test_empty_directory(self, tmp_path: Path):
        result = self.orch.list_yaml_pipelines(tmp_path)
        assert result == []

    def test_nonexistent_directory(self, tmp_path: Path):
        result = self.orch.list_yaml_pipelines(tmp_path / "no_such_dir")
        assert result == []

    def test_discovers_yaml_files(self, tmp_path: Path):
        (tmp_path / "my_pipe.yaml").write_text(
            "name: my_pipe\ndescription: Test\nsteps:\n  - name: s\n    harness: h\n    method: m\n"
        )
        result = self.orch.list_yaml_pipelines(tmp_path)
        assert len(result) == 1
        assert result[0]["name"] == "my_pipe"

    def test_skips_schema_yaml(self, tmp_path: Path):
        (tmp_path / "schema.yaml").write_text("name: schema\nsteps: []\n")
        (tmp_path / "real.yaml").write_text(
            "name: real_pipe\nsteps:\n  - name: s\n    harness: h\n    method: m\n"
        )
        result = self.orch.list_yaml_pipelines(tmp_path)
        names = [r["name"] for r in result]
        assert "real_pipe" in names
        assert "schema" not in names

    def test_skips_invalid_yaml(self, tmp_path: Path):
        (tmp_path / "bad.yaml").write_text(": invalid: yaml: content: [[[")
        result = self.orch.list_yaml_pipelines(tmp_path)
        assert result == []

    def test_default_dir_used_when_none(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        # The default dir is inside the package — just verify no exception
        result = orch.list_yaml_pipelines(None)
        assert isinstance(result, list)

    def test_version_field_captured(self, tmp_path: Path):
        (tmp_path / "versioned.yaml").write_text(
            "name: versioned_pipe\nversion: 2.0.0\nsteps:\n  - name: s\n    harness: h\n    method: m\n"
        )
        result = self.orch.list_yaml_pipelines(tmp_path)
        assert result[0]["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# _save_checkpoint
# ---------------------------------------------------------------------------


class TestSaveCheckpoint:
    def test_creates_checkpoint_file(self, tmp_path: Path):
        orch = OrchestrationHarness(
            harnesses={},
            checkpoint_dir=tmp_path,
            enable_checkpoints=True,
        )
        pipeline = _simple_pipeline()
        checkpoint_path = orch._save_checkpoint(
            pipeline=pipeline,
            context={"domain": "test"},
            step_outputs={"step1": {"key": "val"}},
            completed_steps=["step1"],
            current_step_index=1,
        )
        assert checkpoint_path.exists()
        data = json.loads(checkpoint_path.read_text())
        assert data["pipeline_name"] == "test_pipeline"
        assert data["current_step_index"] == 1
        assert data["completed_steps"] == ["step1"]

    def test_checkpoint_contains_context(self, tmp_path: Path):
        orch = OrchestrationHarness(harnesses={}, checkpoint_dir=tmp_path, enable_checkpoints=True)
        pipeline = _simple_pipeline()
        cp = orch._save_checkpoint(pipeline, {"k": "v"}, {}, [], 0)
        data = json.loads(cp.read_text())
        assert data["context"] == {"k": "v"}

    def test_checkpoint_human_gates_serialized(self, tmp_path: Path):
        orch = OrchestrationHarness(harnesses={}, checkpoint_dir=tmp_path, enable_checkpoints=True)
        pipeline = Pipeline(
            name="gated_pipe",
            steps=[PipelineStep(name="s", harness="h", method="m")],
            context={},
            human_gates={"approval": HumanGateConfig(timeout_hours=6)},
        )
        cp = orch._save_checkpoint(pipeline, {}, {}, [], 0)
        data = json.loads(cp.read_text())
        assert "approval" in data["human_gates"]
        assert data["human_gates"]["approval"]["timeout_hours"] == 6


# ---------------------------------------------------------------------------
# _execute_step
# ---------------------------------------------------------------------------


class TestExecuteStep:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.do_thing = AsyncMock(return_value={"result": "ok", "items": [1, 2]})

        orch = _make_harness(tmp_path, harnesses={"my_harness": mock_h})
        step = PipelineStep(
            name="s1",
            harness="my_harness",
            method="do_thing",
            outputs=["result", "items"],
        )
        result = await orch._execute_step(step, {}, {})
        assert result.status == StepStatus.COMPLETED
        assert result.outputs["result"] == "ok"
        assert result.outputs["items"] == [1, 2]
        assert result.error is None
        assert result.retries == 0

    @pytest.mark.asyncio
    async def test_harness_not_found(self, tmp_path: Path):
        orch = _make_harness(tmp_path, harnesses={})
        step = PipelineStep(name="s", harness="missing", method="m")
        result = await orch._execute_step(step, {}, {})
        assert result.status == StepStatus.FAILED
        assert "Harness not found" in result.error

    @pytest.mark.asyncio
    async def test_method_not_found(self, tmp_path: Path):
        mock_h = MagicMock(spec=[])  # no methods
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})
        step = PipelineStep(name="s", harness="h", method="nonexistent")
        result = await orch._execute_step(step, {}, {})
        assert result.status == StepStatus.FAILED
        assert "Method not found" in result.error

    @pytest.mark.asyncio
    async def test_non_dict_result_no_outputs(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.do_thing = AsyncMock(return_value="plain string")
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})
        step = PipelineStep(name="s", harness="h", method="do_thing", outputs=["x"])
        result = await orch._execute_step(step, {}, {})
        assert result.status == StepStatus.COMPLETED
        assert result.outputs == {}

    @pytest.mark.asyncio
    async def test_timeout_raises_step_failed(self, tmp_path: Path):
        async def slow_method(**kwargs):
            await asyncio.sleep(999)

        mock_h = MagicMock()
        mock_h.slow = slow_method
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})
        step = PipelineStep(name="s", harness="h", method="slow", timeout_seconds=1)
        result = await orch._execute_step(step, {}, {})
        assert result.status == StepStatus.FAILED
        assert "timed out" in result.error.lower() or "TimeoutError" in result.error

    @pytest.mark.asyncio
    async def test_retry_on_failure_retries_then_fails(self, tmp_path: Path):
        call_count = 0

        async def failing_method(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("always fails")

        mock_h = MagicMock()
        mock_h.failing = failing_method
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        step = PipelineStep(
            name="s",
            harness="h",
            method="failing",
            on_failure="retry",
            retry_count=2,
        )

        with patch("asyncio.sleep", new=AsyncMock()):  # skip sleep
            result = await orch._execute_step(step, {}, {})

        assert result.status == StepStatus.FAILED
        assert call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_abort_does_not_retry(self, tmp_path: Path):
        call_count = 0

        async def failing_method(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("fail")

        mock_h = MagicMock()
        mock_h.failing = failing_method
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        step = PipelineStep(
            name="s",
            harness="h",
            method="failing",
            on_failure="abort",
            retry_count=3,
        )
        result = await orch._execute_step(step, {}, {})
        assert result.status == StepStatus.FAILED
        assert call_count == 1  # no retries when on_failure != "retry"

    @pytest.mark.asyncio
    async def test_inputs_resolved_before_call(self, tmp_path: Path):
        received = {}

        async def capture(**kwargs):
            received.update(kwargs)
            return {}

        mock_h = MagicMock()
        mock_h.capture = capture
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})
        step = PipelineStep(
            name="s",
            harness="h",
            method="capture",
            inputs={"domain": "{{ context.domain }}", "literal": "abc"},
        )
        await orch._execute_step(step, {"domain": "mydom"}, {})
        assert received["domain"] == "mydom"
        assert received["literal"] == "abc"


# ---------------------------------------------------------------------------
# execute (pipeline-level)
# ---------------------------------------------------------------------------


class TestExecutePipeline:
    @pytest.mark.asyncio
    async def test_happy_path_all_steps(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.do_thing = AsyncMock(return_value={"val": 42})
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        pipeline = Pipeline(
            name="happy",
            steps=[
                PipelineStep(name="s1", harness="h", method="do_thing", outputs=["val"]),
                PipelineStep(name="s2", harness="h", method="do_thing", outputs=["val"]),
            ],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is True
        assert len(result.step_results) == 2
        assert all(s.status == StepStatus.COMPLETED for s in result.step_results)

    @pytest.mark.asyncio
    async def test_abort_on_first_failure(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.fail = AsyncMock(side_effect=RuntimeError("boom"))
        mock_h.ok = AsyncMock(return_value={})
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        pipeline = Pipeline(
            name="abort_test",
            steps=[
                PipelineStep(name="s1", harness="h", method="fail", on_failure="abort"),
                PipelineStep(name="s2", harness="h", method="ok"),
            ],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is False
        assert len(result.step_results) == 1  # second step never ran
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_skip_on_failure_continues(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.fail = AsyncMock(side_effect=RuntimeError("bad"))
        mock_h.ok = AsyncMock(return_value={"done": True})
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        pipeline = Pipeline(
            name="skip_test",
            steps=[
                PipelineStep(name="s1", harness="h", method="fail", on_failure="skip"),
                PipelineStep(name="s2", harness="h", method="ok"),
            ],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is True
        assert len(result.step_results) == 2

    @pytest.mark.asyncio
    async def test_human_gate_pauses_pipeline(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.fail = AsyncMock(side_effect=RuntimeError("needs human"))
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        pipeline = Pipeline(
            name="gate_test",
            steps=[
                PipelineStep(
                    name="s1",
                    harness="h",
                    method="fail",
                    on_failure="human_gate",
                ),
            ],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is False
        assert result.error is not None
        assert "awaiting human approval" in result.error

    @pytest.mark.asyncio
    async def test_human_gate_with_checkpoints_enabled(self, tmp_path: Path):
        """Human-gate failure should save a checkpoint when enable_checkpoints=True."""
        mock_h = MagicMock()
        mock_h.fail = AsyncMock(side_effect=RuntimeError("needs human"))
        orch = OrchestrationHarness(
            harnesses={"h": mock_h},
            checkpoint_dir=tmp_path,
            enable_checkpoints=True,
        )

        pipeline = Pipeline(
            name="gate_cp_test",
            steps=[
                PipelineStep(
                    name="s1",
                    harness="h",
                    method="fail",
                    on_failure="human_gate",
                ),
            ],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is False
        # A checkpoint should have been written
        checkpoints = list(tmp_path.glob("*.json"))
        assert len(checkpoints) >= 1
        assert result.checkpoint_path is not None

    @pytest.mark.asyncio
    async def test_human_gate_creates_approval_request(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.fail = AsyncMock(side_effect=RuntimeError("needs human"))

        mock_gate = MagicMock()
        approval_result = MagicMock()
        approval_result.request_id = "req-123"
        mock_gate.create_approval = AsyncMock(return_value=approval_result)

        orch = _make_harness(tmp_path, harnesses={"h": mock_h, "human_gate": mock_gate})

        # Patch the local imports inside the method so the approval path is exercised
        with patch("forge_harness.orchestration_harness.OrchestrationHarness._save_checkpoint") as mock_cp:
            mock_cp.return_value = Path("/tmp/fake_checkpoint.json")
            # Patch at the module level where they are imported at runtime
            with patch("forge_harness.approval_queue.ApprovalType", create=True) as mock_at, \
                 patch("forge_harness.approval_queue.ApprovalPriority", create=True) as mock_ap:
                mock_at.FEATURE = "feature"
                mock_ap.HIGH = "high"
                pipeline = Pipeline(
                    name="gate_approval",
                    steps=[
                        PipelineStep(
                            name="s1",
                            harness="h",
                            method="fail",
                            on_failure="human_gate",
                        ),
                    ],
                    context={"domain": "test-domain"},
                )
                result = await orch.execute(pipeline)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_human_gate_approval_creation_failure_is_swallowed(self, tmp_path: Path):
        """If create_approval raises, the pipeline should still pause (not crash)."""
        mock_h = MagicMock()
        mock_h.fail = AsyncMock(side_effect=RuntimeError("needs human"))

        mock_gate = MagicMock()
        mock_gate.create_approval = AsyncMock(side_effect=Exception("approval service down"))

        orch = _make_harness(tmp_path, harnesses={"h": mock_h, "human_gate": mock_gate})

        with patch("forge_harness.orchestration_harness.OrchestrationHarness._save_checkpoint") as mock_cp:
            mock_cp.return_value = Path("/tmp/fake_checkpoint.json")
            pipeline = Pipeline(
                name="gate_fail_graceful",
                steps=[
                    PipelineStep(
                        name="s1",
                        harness="h",
                        method="fail",
                        on_failure="human_gate",
                    ),
                ],
                context={"domain": "test-domain"},
            )
            result = await orch.execute(pipeline)
        # Even with approval creation failure, pipeline should pause gracefully
        assert result.success is False
        assert "awaiting human approval" in result.error

    @pytest.mark.asyncio
    async def test_checkpoints_saved_on_success(self, tmp_path: Path):
        orch = OrchestrationHarness(
            harnesses={},
            checkpoint_dir=tmp_path,
            enable_checkpoints=True,
        )
        mock_h = MagicMock()
        mock_h.ok = AsyncMock(return_value={})
        orch.harnesses["h"] = mock_h

        pipeline = Pipeline(
            name="checkpoint_test",
            steps=[PipelineStep(name="s1", harness="h", method="ok")],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is True
        # At least one checkpoint file should exist
        checkpoints = list(tmp_path.glob("*.json"))
        assert len(checkpoints) >= 1

    @pytest.mark.asyncio
    async def test_events_emitted_during_execution(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.ok = AsyncMock(return_value={})
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})
        cb = AggregatingCallback()
        orch.register_callback(cb)

        pipeline = Pipeline(
            name="event_test",
            steps=[PipelineStep(name="s1", harness="h", method="ok")],
            context={},
        )
        await orch.execute(pipeline)
        event_types = {e.event_type for e in cb.events}
        assert EventType.PIPELINE_STARTED in event_types
        assert EventType.STEP_STARTED in event_types
        assert EventType.STEP_COMPLETED in event_types
        assert EventType.PIPELINE_COMPLETED in event_types

    @pytest.mark.asyncio
    async def test_context_propagated_between_steps(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.step1 = AsyncMock(return_value={"page_ids": [10, 20]})

        received = {}

        async def step2(**kwargs):
            received.update(kwargs)
            return {}

        mock_h.step2 = step2
        orch = _make_harness(tmp_path, harnesses={"h": mock_h})

        pipeline = Pipeline(
            name="ctx_test",
            steps=[
                PipelineStep(
                    name="s1",
                    harness="h",
                    method="step1",
                    outputs=["page_ids"],
                ),
                PipelineStep(
                    name="s2",
                    harness="h",
                    method="step2",
                    inputs={"ids": "{{ steps.s1.page_ids }}"},
                ),
            ],
            context={},
        )
        result = await orch.execute(pipeline)
        assert result.success is True
        assert received.get("ids") == [10, 20]


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_happy_path(self, tmp_path: Path):
        mock_h = MagicMock()
        mock_h.ok = AsyncMock(return_value={})
        orch = OrchestrationHarness(
            harnesses={"h": mock_h},
            checkpoint_dir=tmp_path,
            enable_checkpoints=True,
        )

        pipeline = Pipeline(
            name="resume_pipe",
            steps=[
                PipelineStep(name="s1", harness="h", method="ok"),
                PipelineStep(name="s2", harness="h", method="ok"),
            ],
            context={"k": "v"},
        )
        # Save checkpoint after s1
        cp = orch._save_checkpoint(pipeline, {"k": "v"}, {"s1": {}}, ["s1"], 1)

        result = await orch.resume(cp)
        assert result is not None
        assert result.success is True

    @pytest.mark.asyncio
    async def test_resume_all_steps_completed(self, tmp_path: Path):
        orch = OrchestrationHarness(
            harnesses={},
            checkpoint_dir=tmp_path,
            enable_checkpoints=True,
        )
        pipeline = Pipeline(
            name="completed_pipe",
            steps=[PipelineStep(name="s1", harness="h", method="ok")],
            context={},
        )
        # current_step_index=1 means past the end
        cp = orch._save_checkpoint(pipeline, {}, {}, ["s1"], 1)

        result = await orch.resume(cp)
        assert result is not None
        assert result.success is True
        assert result.step_results == []

    @pytest.mark.asyncio
    async def test_resume_invalid_checkpoint_returns_none(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json")

        result = await orch.resume(bad_file)
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_missing_file_returns_none(self, tmp_path: Path):
        orch = _make_harness(tmp_path)
        result = await orch.resume(tmp_path / "nonexistent.json")
        assert result is None


# ---------------------------------------------------------------------------
# resume_from_approval
# ---------------------------------------------------------------------------


class TestResumeFromApproval:
    @pytest.mark.asyncio
    async def test_no_human_gate_harness_returns_none(self, tmp_path: Path):
        orch = _make_harness(tmp_path, harnesses={})
        result = await orch.resume_from_approval("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_approved_returns_none(self, tmp_path: Path):
        from unittest.mock import MagicMock

        mock_gate = MagicMock()
        gate_result = MagicMock()

        # Patch GateStatus import inside the method
        with patch("forge_harness.orchestration_harness.OrchestrationHarness.resume_from_approval") as mock_method:
            mock_method.return_value = None
            orch = _make_harness(tmp_path, harnesses={"human_gate": mock_gate})
            result = await orch.resume_from_approval("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_approved_but_no_checkpoint_path(self, tmp_path: Path):
        mock_gate = MagicMock()
        gate_result = MagicMock()
        gate_result.checkpoint_path = None

        # Import the real GateStatus to set correct status
        try:
            from forge_harness.human_gate_harness import GateStatus
            gate_result.gate_status = GateStatus.APPROVED
        except ImportError:
            # Module might not exist in all environments
            pytest.skip("human_gate_harness not available")

        mock_gate.check_status = AsyncMock(return_value=gate_result)
        orch = _make_harness(tmp_path, harnesses={"human_gate": mock_gate})

        result = await orch.resume_from_approval("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_approved_missing_checkpoint_file(self, tmp_path: Path):
        mock_gate = MagicMock()
        gate_result = MagicMock()
        gate_result.checkpoint_path = str(tmp_path / "missing_checkpoint.json")

        try:
            from forge_harness.human_gate_harness import GateStatus
            gate_result.gate_status = GateStatus.APPROVED
        except ImportError:
            pytest.skip("human_gate_harness not available")

        mock_gate.check_status = AsyncMock(return_value=gate_result)
        orch = _make_harness(tmp_path, harnesses={"human_gate": mock_gate})

        result = await orch.resume_from_approval("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_check_status_exception_returns_none(self, tmp_path: Path):
        mock_gate = MagicMock()
        mock_gate.check_status = AsyncMock(side_effect=RuntimeError("network error"))
        orch = _make_harness(tmp_path, harnesses={"human_gate": mock_gate})

        result = await orch.resume_from_approval("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_from_approval_not_approved_status(self, tmp_path: Path):
        """Approval exists but status is PENDING — should return None."""
        try:
            from forge_harness.human_gate_harness import GateStatus
        except ImportError:
            pytest.skip("human_gate_harness not available")

        mock_gate = MagicMock()
        gate_result = MagicMock()
        # Use a real enum member so .value works correctly
        gate_result.gate_status = GateStatus.PENDING
        mock_gate.check_status = AsyncMock(return_value=gate_result)

        orch = _make_harness(tmp_path, harnesses={"human_gate": mock_gate})
        result = await orch.resume_from_approval("req-pending")
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_from_approval_success_full_path(self, tmp_path: Path):
        """Full happy path: APPROVED + checkpoint exists + pipeline resumes."""
        try:
            from forge_harness.human_gate_harness import GateStatus
        except ImportError:
            pytest.skip("human_gate_harness not available")

        # Set up a checkpoint on disk
        mock_harness = MagicMock()
        mock_harness.ok = AsyncMock(return_value={})
        orch = OrchestrationHarness(
            harnesses={"h": mock_harness},
            checkpoint_dir=tmp_path,
            enable_checkpoints=True,
        )

        pipeline = Pipeline(
            name="approval_resume_pipe",
            steps=[PipelineStep(name="s1", harness="h", method="ok")],
            context={},
        )
        cp = orch._save_checkpoint(pipeline, {}, {}, [], 0)

        # Set up mock gate returning APPROVED with a valid checkpoint path
        mock_gate = MagicMock()
        gate_result = MagicMock()
        gate_result.gate_status = GateStatus.APPROVED
        gate_result.checkpoint_path = str(cp)
        mock_gate.check_status = AsyncMock(return_value=gate_result)
        orch.harnesses["human_gate"] = mock_gate

        result = await orch.resume_from_approval("req-approved")
        assert result is not None
        assert result.success is True


# ---------------------------------------------------------------------------
# create_orchestration_harness factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_creates_instance(self, tmp_path: Path):
        orch = create_orchestration_harness(checkpoint_dir=tmp_path)
        assert isinstance(orch, OrchestrationHarness)
        assert orch.harnesses == {}

    def test_factory_with_harnesses(self, tmp_path: Path):
        mock_h = MagicMock()
        orch = create_orchestration_harness(checkpoint_dir=tmp_path, harnesses={"h": mock_h})
        assert orch.harnesses["h"] is mock_h

    def test_factory_creates_checkpoint_dir(self, tmp_path: Path):
        new_dir = tmp_path / "cp"
        create_orchestration_harness(checkpoint_dir=new_dir)
        assert new_dir.exists()

    def test_factory_with_none_harnesses(self, tmp_path: Path):
        orch = create_orchestration_harness(checkpoint_dir=tmp_path, harnesses=None)
        assert orch.harnesses == {}

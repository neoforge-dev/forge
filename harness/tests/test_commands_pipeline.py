"""Comprehensive tests for forge_harness/commands/pipeline.py.

Covers all four pipeline sub-commands:
  - pipeline execute
  - pipeline list
  - pipeline status
  - pipeline resume

All external dependencies (OrchestrationHarness, HarnessRegistry,
filesystem, asyncio.run) are mocked so tests are hermetic and fast.

Patching strategy: Because pipeline.py uses *lazy imports* inside each
command function (e.g. ``from ..orchestration_harness import ...``), the
imported names are not in the module's global namespace.  We must patch
the names at their *definition* location, i.e.:
  - forge_harness.orchestration_harness.OrchestrationHarness
  - forge_harness.harness_registry.HarnessRegistry
  - forge_harness.harness_registry.create_harness_registry
  - forge_harness.orchestration_harness.create_orchestration_harness
and patch ``asyncio.run`` via ``forge_harness.commands.pipeline.asyncio.run``
(asyncio is imported at module level so this attribute does exist).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from forge_harness.commands.pipeline import pipeline

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _make_pipeline_obj(name: str = "test_pipe", n_steps: int = 2) -> MagicMock:
    """Return a MagicMock that looks like an OrchestrationHarness Pipeline."""
    step = MagicMock()
    step.name = "step1"
    step.harness = "content"
    step.method = "generate"

    pipe = MagicMock()
    pipe.name = name
    pipe.steps = [step] * n_steps
    pipe.context = {}
    return pipe


def _make_step_result(name: str = "step1", error: str | None = None) -> MagicMock:
    sr = MagicMock()
    sr.name = name
    sr.error = error
    sr.status = MagicMock()
    sr.status.value = "completed"
    return sr


def _make_pipeline_result(
    success: bool = True,
    n_steps: int = 2,
    duration: float = 1.5,
    error: str | None = None,
) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.duration_seconds = duration
    result.step_results = [_make_step_result() for _ in range(n_steps)]
    result.error = error
    result.pipeline_name = "test_pipe"
    result.context = {"domain": "test-domain"}
    result.checkpoint_path = None
    return result


# ===========================================================================
# pipeline execute -- dry-run
# ===========================================================================


class TestPipelineExecuteDryRun:
    """Tests for 'pipeline execute --dry-run'."""

    def _invoke_dry_run(
        self,
        runner: CliRunner,
        pipe_obj: MagicMock,
        pipeline_name: str = "my_pipe",
        context_json: str = "{}",
    ):
        with patch(
            "forge_harness.orchestration_harness.OrchestrationHarness"
        ) as MockOrch:
            orch_inst = MagicMock()
            orch_inst.get_builtin_pipeline.return_value = pipe_obj
            MockOrch.return_value = orch_inst

            # Patch inside the command's local import scope
            with patch.dict(
                sys.modules,
                {
                    "forge_harness.orchestration_harness": sys.modules.get(
                        "forge_harness.orchestration_harness",
                        MagicMock(OrchestrationHarness=MockOrch),
                    )
                },
            ):
                fake_module = MagicMock()
                fake_module.OrchestrationHarness = MockOrch

                with patch.dict(
                    sys.modules,
                    {"forge_harness.orchestration_harness": fake_module},
                ):
                    args = ["execute", pipeline_name, "--dry-run"]
                    if context_json != "{}":
                        args += ["--context", context_json]
                    result = runner.invoke(pipeline, args, catch_exceptions=False)
        return result, orch_inst

    def test_dry_run_exits_zero(self, runner):
        pipe_obj = _make_pipeline_obj()
        result, _ = self._invoke_dry_run(runner, pipe_obj)
        assert result.exit_code == 0

    def test_dry_run_prints_pipeline_name(self, runner):
        pipe_obj = _make_pipeline_obj()
        result, _ = self._invoke_dry_run(runner, pipe_obj, pipeline_name="alpha_pipe")
        assert "alpha_pipe" in result.output

    def test_dry_run_lists_step_count(self, runner):
        pipe_obj = _make_pipeline_obj(n_steps=3)
        result, _ = self._invoke_dry_run(runner, pipe_obj)
        assert "Steps: 3" in result.output

    def test_dry_run_shows_step_detail(self, runner):
        step = MagicMock()
        step.name = "generate_content"
        step.harness = "content"
        step.method = "run"
        pipe_obj = MagicMock()
        pipe_obj.steps = [step]
        pipe_obj.context = {}
        result, _ = self._invoke_dry_run(runner, pipe_obj)
        assert "generate_content" in result.output
        assert "content.run" in result.output

    def test_dry_run_shows_context(self, runner):
        pipe_obj = _make_pipeline_obj()
        pipe_obj.context = {"env": "staging"}
        result, _ = self._invoke_dry_run(runner, pipe_obj)
        assert "staging" in result.output

    def test_dry_run_pipeline_not_found_exits_1(self, runner):
        fake_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.get_builtin_pipeline.return_value = None
        fake_module.OrchestrationHarness.return_value = orch_inst

        with patch.dict(
            sys.modules, {"forge_harness.orchestration_harness": fake_module}
        ):
            result = runner.invoke(
                pipeline, ["execute", "nonexistent_pipe", "--dry-run"]
            )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_dry_run_invalid_json_context_exits_1(self, runner):
        result = runner.invoke(
            pipeline,
            ["execute", "my_pipe", "--context", "{bad json}", "--dry-run"],
        )
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_dry_run_enable_checkpoints_false(self, runner):
        """Dry-run should pass enable_checkpoints=False to OrchestrationHarness."""
        pipe_obj = _make_pipeline_obj()
        fake_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.get_builtin_pipeline.return_value = pipe_obj
        fake_module.OrchestrationHarness.return_value = orch_inst

        with patch.dict(
            sys.modules, {"forge_harness.orchestration_harness": fake_module}
        ):
            runner.invoke(pipeline, ["execute", "my_pipe", "--dry-run"])

        _, kwargs = fake_module.OrchestrationHarness.call_args
        assert kwargs.get("enable_checkpoints") is False

    def test_dry_run_uses_custom_checkpoint_dir(self, runner, tmp_path):
        pipe_obj = _make_pipeline_obj()
        fake_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.get_builtin_pipeline.return_value = pipe_obj
        fake_module.OrchestrationHarness.return_value = orch_inst

        with patch.dict(
            sys.modules, {"forge_harness.orchestration_harness": fake_module}
        ):
            runner.invoke(
                pipeline,
                [
                    "execute",
                    "my_pipe",
                    "--checkpoint-dir",
                    str(tmp_path),
                    "--dry-run",
                ],
            )

        _, kwargs = fake_module.OrchestrationHarness.call_args
        assert Path(kwargs["checkpoint_dir"]) == tmp_path


# ===========================================================================
# pipeline execute -- real run
# ===========================================================================


class TestPipelineExecuteReal:
    """Tests for 'pipeline execute' (live run, no --dry-run)."""

    def _invoke_execute(
        self,
        runner: CliRunner,
        pipe_obj: MagicMock | None,
        exec_result=None,
        pipeline_name: str = "my_pipe",
        exec_exception: Exception | None = None,
        context_str: str = "{}",
    ):
        fake_orch_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.get_builtin_pipeline.return_value = pipe_obj
        fake_orch_module.OrchestrationHarness.return_value = orch_inst

        fake_reg_module = MagicMock()
        reg_inst = MagicMock()
        reg_inst.get_harnesses.return_value = {}
        fake_reg_module.HarnessRegistry.return_value = reg_inst

        if exec_exception:
            async_side_effect = exec_exception
        else:
            async_side_effect = None

        with patch.dict(
            sys.modules,
            {
                "forge_harness.orchestration_harness": fake_orch_module,
                "forge_harness.harness_registry": fake_reg_module,
            },
        ), patch(
            "forge_harness.commands.pipeline.asyncio"
        ) as mock_asyncio:
            if exec_exception:
                mock_asyncio.run.side_effect = exec_exception
            else:
                mock_asyncio.run.return_value = exec_result

            args = ["execute", pipeline_name]
            if context_str != "{}":
                args += ["--context", context_str]

            result = runner.invoke(pipeline, args, catch_exceptions=False)

        return result, orch_inst, fake_orch_module, fake_reg_module

    def test_execute_success_exits_zero(self, runner):
        pipe_obj = _make_pipeline_obj()
        exec_result = _make_pipeline_result(success=True, n_steps=2)
        result, *_ = self._invoke_execute(runner, pipe_obj, exec_result)
        assert result.exit_code == 0

    def test_execute_success_prints_completed(self, runner):
        pipe_obj = _make_pipeline_obj()
        exec_result = _make_pipeline_result(success=True)
        result, *_ = self._invoke_execute(runner, pipe_obj, exec_result)
        assert "completed successfully" in result.output

    def test_execute_success_prints_step_count(self, runner):
        pipe_obj = _make_pipeline_obj()
        exec_result = _make_pipeline_result(success=True, n_steps=3)
        result, *_ = self._invoke_execute(runner, pipe_obj, exec_result)
        assert "Steps completed: 3" in result.output

    def test_execute_success_prints_duration(self, runner):
        pipe_obj = _make_pipeline_obj()
        exec_result = _make_pipeline_result(success=True, duration=4.2)
        result, *_ = self._invoke_execute(runner, pipe_obj, exec_result)
        assert "4.2s" in result.output

    def test_execute_failure_exits_1(self, runner):
        pipe_obj = _make_pipeline_obj()
        sr = _make_step_result(name="step1", error="timeout")
        exec_result = _make_pipeline_result(success=False)
        exec_result.step_results = [sr]
        result, *_ = self._invoke_execute(runner, pipe_obj, exec_result)
        assert result.exit_code == 1

    def test_execute_failure_prints_step_error(self, runner):
        pipe_obj = _make_pipeline_obj()
        sr = _make_step_result(name="generate", error="Network timeout")
        exec_result = _make_pipeline_result(success=False)
        exec_result.step_results = [sr]
        result, *_ = self._invoke_execute(runner, pipe_obj, exec_result)
        assert "Network timeout" in result.output

    def test_execute_pipeline_not_found_exits_1(self, runner):
        # pipe_obj = None means pipeline not found
        result, *_ = self._invoke_execute(
            runner, pipe_obj=None, exec_result=None
        )
        assert result.exit_code == 1

    def test_execute_pipeline_not_found_prints_error(self, runner):
        result, *_ = self._invoke_execute(
            runner, pipe_obj=None, exec_result=None, pipeline_name="ghost"
        )
        assert "not found" in result.output

    def test_execute_invalid_json_exits_1(self, runner):
        result = runner.invoke(
            pipeline,
            ["execute", "my_pipe", "--context", "not-json"],
        )
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_execute_exception_during_run_exits_1(self, runner):
        pipe_obj = _make_pipeline_obj()
        result, *_ = self._invoke_execute(
            runner, pipe_obj, exec_result=None,
            exec_exception=RuntimeError("disk full"),
        )
        assert result.exit_code == 1

    def test_execute_exception_prints_error_message(self, runner):
        pipe_obj = _make_pipeline_obj()
        result, *_ = self._invoke_execute(
            runner, pipe_obj, exec_result=None,
            exec_exception=RuntimeError("disk full"),
        )
        assert "disk full" in result.output

    def test_execute_context_json_merged_into_pipeline(self, runner):
        pipe_obj = _make_pipeline_obj()
        # Use a real dict so we can verify the update actually happened
        pipe_obj.context = {"existing": "value"}
        exec_result = _make_pipeline_result(success=True)

        fake_orch_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.get_builtin_pipeline.return_value = pipe_obj
        fake_orch_module.OrchestrationHarness.return_value = orch_inst

        fake_reg_module = MagicMock()
        reg_inst = MagicMock()
        reg_inst.get_harnesses.return_value = {}
        fake_reg_module.HarnessRegistry.return_value = reg_inst

        with patch.dict(
            sys.modules,
            {
                "forge_harness.orchestration_harness": fake_orch_module,
                "forge_harness.harness_registry": fake_reg_module,
            },
        ), patch(
            "forge_harness.commands.pipeline.asyncio"
        ) as mock_asyncio:
            mock_asyncio.run.return_value = exec_result

            runner.invoke(
                pipeline,
                ["execute", "my_pipe", "--context", '{"env": "prod"}'],
                catch_exceptions=False,
            )

        # Verify the context dict was updated with the provided JSON values
        assert pipe_obj.context.get("env") == "prod"

    def test_execute_default_context_leaves_pipeline_context_unchanged(self, runner):
        """Default --context '{}' means the context dict is updated with empty dict (no changes)."""
        pipe_obj = _make_pipeline_obj()
        pipe_obj.context = {"pre": "existing"}
        exec_result = _make_pipeline_result(success=True)

        fake_orch_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.get_builtin_pipeline.return_value = pipe_obj
        fake_orch_module.OrchestrationHarness.return_value = orch_inst

        fake_reg_module = MagicMock()
        reg_inst = MagicMock()
        reg_inst.get_harnesses.return_value = {}
        fake_reg_module.HarnessRegistry.return_value = reg_inst

        with patch.dict(
            sys.modules,
            {
                "forge_harness.orchestration_harness": fake_orch_module,
                "forge_harness.harness_registry": fake_reg_module,
            },
        ), patch(
            "forge_harness.commands.pipeline.asyncio"
        ) as mock_asyncio:
            mock_asyncio.run.return_value = exec_result

            runner.invoke(pipeline, ["execute", "my_pipe"], catch_exceptions=False)

        # Pre-existing context key should still be there (empty dict update is a no-op)
        assert pipe_obj.context.get("pre") == "existing"


# ===========================================================================
# pipeline list
# ===========================================================================


class TestPipelineList:
    """Tests for 'pipeline list'."""

    def _invoke_list(
        self,
        runner: CliRunner,
        pipelines_info: list,
        n_steps: int = 2,
        show_yaml: bool = False,
        forge_root=None,
    ):
        fake_module = MagicMock()
        orch_inst = MagicMock()
        orch_inst.list_builtin_pipelines.return_value = pipelines_info
        pipe_obj = _make_pipeline_obj(n_steps=n_steps)
        orch_inst.get_builtin_pipeline.return_value = pipe_obj
        fake_module.OrchestrationHarness.return_value = orch_inst

        with patch.dict(
            sys.modules, {"forge_harness.orchestration_harness": fake_module}
        ), patch(
            "forge_harness.commands.pipeline.find_forge_root",
            return_value=forge_root,
        ):
            args = ["list"]
            if show_yaml:
                args.append("--yaml")
            result = runner.invoke(pipeline, args, catch_exceptions=False)

        return result

    def test_list_exits_zero(self, runner):
        result = self._invoke_list(runner, [])
        assert result.exit_code == 0

    def test_list_shows_available_pipelines_header(self, runner):
        result = self._invoke_list(runner, [])
        assert "Available Pipelines" in result.output

    def test_list_shows_builtin_header(self, runner):
        result = self._invoke_list(runner, [])
        assert "Built-in" in result.output

    def test_list_shows_pipeline_names(self, runner):
        info = [
            {"name": "content_to_publish", "description": "Publish content"},
            {"name": "deploy_app", "description": "Deploy app"},
        ]
        result = self._invoke_list(runner, info)
        assert "content_to_publish" in result.output
        assert "deploy_app" in result.output

    def test_list_shows_pipeline_descriptions(self, runner):
        info = [{"name": "alpha", "description": "Generate and publish"}]
        result = self._invoke_list(runner, info)
        assert "Generate and publish" in result.output

    def test_list_shows_step_count(self, runner):
        info = [{"name": "alpha", "description": "desc"}]
        result = self._invoke_list(runner, info, n_steps=4)
        assert "Steps: 4" in result.output

    def test_list_yaml_no_root_skips_yaml_section(self, runner):
        result = self._invoke_list(runner, [], show_yaml=True, forge_root=None)
        assert result.exit_code == 0
        assert "YAML Pipelines" not in result.output

    def test_list_yaml_shows_section_header(self, runner, tmp_path):
        pipelines_dir = tmp_path / "harness" / "pipelines"
        pipelines_dir.mkdir(parents=True)
        (pipelines_dir / "custom.yaml").write_text("name: custom")

        result = self._invoke_list(
            runner, [], show_yaml=True, forge_root=tmp_path
        )
        assert "YAML Pipelines" in result.output

    def test_list_yaml_shows_file_stem(self, runner, tmp_path):
        pipelines_dir = tmp_path / "harness" / "pipelines"
        pipelines_dir.mkdir(parents=True)
        (pipelines_dir / "my_pipeline.yaml").write_text("name: my_pipeline")

        result = self._invoke_list(
            runner, [], show_yaml=True, forge_root=tmp_path
        )
        assert "my_pipeline" in result.output

    def test_list_yaml_no_pipelines_dir_shows_message(self, runner, tmp_path):
        # tmp_path has no harness/pipelines subdir
        result = self._invoke_list(
            runner, [], show_yaml=True, forge_root=tmp_path
        )
        assert "no pipelines/ directory" in result.output

    def test_list_yaml_empty_pipelines_dir_shows_none_found(self, runner, tmp_path):
        pipelines_dir = tmp_path / "harness" / "pipelines"
        pipelines_dir.mkdir(parents=True)

        result = self._invoke_list(
            runner, [], show_yaml=True, forge_root=tmp_path
        )
        assert "none found" in result.output

    def test_list_yaml_discovers_yml_extension(self, runner, tmp_path):
        pipelines_dir = tmp_path / "harness" / "pipelines"
        pipelines_dir.mkdir(parents=True)
        (pipelines_dir / "alpha.yml").write_text("name: alpha")

        result = self._invoke_list(
            runner, [], show_yaml=True, forge_root=tmp_path
        )
        assert "alpha" in result.output

    def test_list_multiple_yaml_files_all_shown(self, runner, tmp_path):
        pipelines_dir = tmp_path / "harness" / "pipelines"
        pipelines_dir.mkdir(parents=True)
        (pipelines_dir / "pipe1.yaml").write_text("")
        (pipelines_dir / "pipe2.yml").write_text("")

        result = self._invoke_list(
            runner, [], show_yaml=True, forge_root=tmp_path
        )
        assert "pipe1" in result.output
        assert "pipe2" in result.output


# ===========================================================================
# pipeline status
# ===========================================================================


class TestPipelineStatus:
    """Tests for 'pipeline status CHECKPOINT_PATH'."""

    def _write_checkpoint(self, path: Path, data: dict, filename: str = "pipeline.json") -> Path:
        cp_file = path / filename
        cp_file.write_text(json.dumps(data))
        return cp_file

    def test_status_exits_zero(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"pipeline_name": "alpha"})
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert result.exit_code == 0

    def test_status_shows_pipeline_name(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"pipeline_name": "my_pipe"})
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "my_pipe" in result.output

    def test_status_shows_checkpoint_filename_header(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"pipeline_name": "p"}, "chk.json")
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "chk.json" in result.output

    def test_status_shows_created_at(self, runner, tmp_path):
        cp_file = self._write_checkpoint(
            tmp_path, {"pipeline_name": "p", "created_at": "2026-01-01T00:00:00"}
        )
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "2026-01-01T00:00:00" in result.output

    def test_status_shows_updated_at(self, runner, tmp_path):
        cp_file = self._write_checkpoint(
            tmp_path, {"pipeline_name": "p", "updated_at": "2026-02-15T12:00:00"}
        )
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "2026-02-15T12:00:00" in result.output

    def test_status_shows_step_name(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "generate", "status": "completed", "duration_seconds": 3.2}
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "generate" in result.output

    def test_status_shows_completed_icon(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "s", "status": "completed", "duration_seconds": 1.0}
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "\u2713" in result.output  # ✓

    def test_status_shows_failed_icon(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "s", "status": "failed", "duration_seconds": 0.5}
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "\u2717" in result.output  # ✗

    def test_status_shows_pending_icon(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "s", "status": "pending", "duration_seconds": 0.0}
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "\u25cb" in result.output  # ○

    def test_status_shows_running_icon(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "s", "status": "running", "duration_seconds": 0.0}
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "\u25b8" in result.output  # ▸

    def test_status_unknown_status_shows_question_mark(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "s", "status": "weird_status", "duration_seconds": 0.0}
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "?" in result.output

    def test_status_shows_step_error_message(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {
                    "name": "deploy",
                    "status": "failed",
                    "duration_seconds": 0.1,
                    "error": "Connection refused",
                }
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "Connection refused" in result.output

    def test_status_shows_context_values(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "context": {"domain": "voice-coach", "env": "prod"},
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "voice-coach" in result.output
        assert "prod" in result.output

    def test_status_invalid_json_exits_1(self, runner, tmp_path):
        cp_file = tmp_path / "bad.json"
        cp_file.write_text("{bad json}")
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert result.exit_code == 1
        assert "Invalid checkpoint file" in result.output

    def test_status_missing_pipeline_name_shows_unknown(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {})
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "unknown" in result.output

    def test_status_empty_step_results_no_crash(self, runner, tmp_path):
        cp_file = self._write_checkpoint(
            tmp_path, {"pipeline_name": "p", "step_results": []}
        )
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert result.exit_code == 0

    def test_status_no_step_results_key_no_crash(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"pipeline_name": "p"})
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert result.exit_code == 0

    def test_status_multiple_step_results_all_shown(self, runner, tmp_path):
        data = {
            "pipeline_name": "p",
            "step_results": [
                {"name": "step_a", "status": "completed", "duration_seconds": 1.0},
                {"name": "step_b", "status": "failed", "duration_seconds": 2.0},
            ],
        }
        cp_file = self._write_checkpoint(tmp_path, data)
        result = runner.invoke(pipeline, ["status", str(cp_file)])
        assert "step_a" in result.output
        assert "step_b" in result.output


# ===========================================================================
# pipeline resume
# ===========================================================================


class TestPipelineResume:
    """Tests for 'pipeline resume CHECKPOINT_PATH'."""

    def _write_checkpoint(self, path: Path, data: dict, filename: str = "resume.json") -> Path:
        cp_file = path / filename
        cp_file.write_text(json.dumps(data))
        return cp_file

    def _invoke_resume(
        self,
        runner: CliRunner,
        cp_file: Path,
        resume_result=None,
        domain: str | None = None,
        registry_error: Exception | None = None,
    ):
        fake_reg_module = MagicMock()
        if registry_error:
            fake_reg_module.create_harness_registry.side_effect = registry_error
        else:
            reg_inst = MagicMock()
            reg_inst.get_for_orchestration.return_value = {}
            fake_reg_module.create_harness_registry.return_value = reg_inst

        fake_orch_module = MagicMock()
        orch_inst = MagicMock()
        fake_orch_module.create_orchestration_harness.return_value = orch_inst

        with patch.dict(
            sys.modules,
            {
                "forge_harness.harness_registry": fake_reg_module,
                "forge_harness.orchestration_harness": fake_orch_module,
            },
        ), patch(
            "forge_harness.commands.pipeline.asyncio"
        ) as mock_asyncio:
            mock_asyncio.run.return_value = resume_result

            args = ["resume", str(cp_file)]
            if domain:
                args += ["--domain", domain]

            result = runner.invoke(pipeline, args, catch_exceptions=False)

        return result, fake_reg_module

    def test_resume_success_exits_zero(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert result.exit_code == 0

    def test_resume_success_prints_completed(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "completed successfully" in result.output

    def test_resume_success_prints_pipeline_name(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        resume_result.pipeline_name = "content_to_publish"
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "content_to_publish" in result.output

    def test_resume_success_prints_duration(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True, duration=7.3)
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "7.3s" in result.output

    def test_resume_success_prints_steps_completed(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True, n_steps=3)
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "Steps completed: 3" in result.output

    def test_resume_success_shows_final_context(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        resume_result.context = {"domain": "voice-coach"}
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "voice-coach" in result.output

    def test_resume_failure_exits_1(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=False, error="step blew up")
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert result.exit_code == 1

    def test_resume_failure_prints_error(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=False, error="timeout reached")
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "timeout reached" in result.output

    def test_resume_none_result_exits_1(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        result, _ = self._invoke_resume(runner, cp_file, resume_result=None)
        assert result.exit_code == 1

    def test_resume_invalid_json_exits_1(self, runner, tmp_path):
        cp_file = tmp_path / "bad.json"
        cp_file.write_text("{bad json}")

        fake_reg_module = MagicMock()
        fake_orch_module = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "forge_harness.harness_registry": fake_reg_module,
                "forge_harness.orchestration_harness": fake_orch_module,
            },
        ):
            result = runner.invoke(pipeline, ["resume", str(cp_file)])

        assert result.exit_code == 1

    def test_resume_domain_option_passed_to_registry(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        _, fake_reg_module = self._invoke_resume(
            runner, cp_file, resume_result, domain="voice-coach"
        )
        fake_reg_module.create_harness_registry.assert_called_once_with(
            domain="voice-coach"
        )

    def test_resume_domain_inferred_from_checkpoint(self, runner, tmp_path):
        cp_file = self._write_checkpoint(
            tmp_path, {"context": {"domain": "ad-guild"}}
        )
        resume_result = _make_pipeline_result(success=True)
        _, fake_reg_module = self._invoke_resume(runner, cp_file, resume_result)
        fake_reg_module.create_harness_registry.assert_called_once_with(
            domain="ad-guild"
        )

    def test_resume_no_domain_passes_none(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        _, fake_reg_module = self._invoke_resume(runner, cp_file, resume_result)
        fake_reg_module.create_harness_registry.assert_called_once_with(domain=None)

    def test_resume_prints_checkpoint_filename(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}}, "resume.json")
        resume_result = _make_pipeline_result(success=True)
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "resume.json" in result.output

    def test_resume_failure_prints_checkpoint_path_if_set(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=False, error="err")
        resume_result.checkpoint_path = "/some/paused_checkpoint.json"
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert "/some/paused_checkpoint.json" in result.output

    def test_resume_failure_no_checkpoint_path_no_crash(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=False, error="err")
        resume_result.checkpoint_path = None
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        assert result.exit_code == 1

    def test_resume_exception_from_registry_exits_1(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        result, _ = self._invoke_resume(
            runner,
            cp_file,
            resume_result=None,
            registry_error=RuntimeError("registry broken"),
        )
        assert result.exit_code == 1

    def test_resume_long_context_value_truncated(self, runner, tmp_path):
        cp_file = self._write_checkpoint(tmp_path, {"context": {}})
        resume_result = _make_pipeline_result(success=True)
        resume_result.context = {"data": "x" * 200}
        result, _ = self._invoke_resume(runner, cp_file, resume_result)
        # Value > 80 chars gets truncated with "..."
        assert "..." in result.output


# ===========================================================================
# pipeline group structure
# ===========================================================================


class TestPipelineGroup:
    """Smoke tests for the pipeline click group itself."""

    def test_pipeline_group_is_a_click_group(self):
        import click

        assert isinstance(pipeline, click.Group)

    def test_pipeline_group_has_execute_command(self):
        assert "execute" in pipeline.commands

    def test_pipeline_group_has_list_command(self):
        assert "list" in pipeline.commands

    def test_pipeline_group_has_status_command(self):
        assert "status" in pipeline.commands

    def test_pipeline_group_has_resume_command(self):
        assert "resume" in pipeline.commands

    def test_pipeline_help_exits_zero(self, runner):
        result = runner.invoke(pipeline, ["--help"])
        assert result.exit_code == 0

    def test_execute_help_exits_zero(self, runner):
        result = runner.invoke(pipeline, ["execute", "--help"])
        assert result.exit_code == 0

    def test_list_help_exits_zero(self, runner):
        result = runner.invoke(pipeline, ["list", "--help"])
        assert result.exit_code == 0

    def test_status_help_exits_zero(self, runner):
        result = runner.invoke(pipeline, ["status", "--help"])
        assert result.exit_code == 0

    def test_resume_help_exits_zero(self, runner):
        result = runner.invoke(pipeline, ["resume", "--help"])
        assert result.exit_code == 0

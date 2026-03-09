"""Tests for forge_harness.webhook_server.services.evaluator.

Covers:
- CheckResult and EvaluatorResult Pydantic models
- EvaluatorOrchestrator.run_checks() — all-pass, required-fail, optional-fail
- Timeout handling (command exceeds timeout_seconds)
- auto_approve_on_pass logic (True / False)
- Empty checks list (vacuous pass)
- Command not found / subprocess error
- get_summary() aggregation
- Singleton factory (get/reset_evaluator_orchestrator)

All subprocess calls are mocked via asyncio.create_subprocess_exec.
Target: 90%+ statement coverage.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
)
from forge_harness.webhook_server.services.evaluator import (
    CheckResult,
    EvaluatorOrchestrator,
    EvaluatorResult,
    get_evaluator_orchestrator,
    reset_evaluator_orchestrator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_check(
    name: str = "test-check",
    command: str = "true",
    timeout_seconds: int = 30,
    required: bool = True,
) -> AcceptanceCheck:
    """Build an AcceptanceCheck with sensible defaults."""
    return AcceptanceCheck(
        name=name,
        command=command,
        timeout_seconds=timeout_seconds,
        required=required,
    )


def _make_profile(
    checks: list[AcceptanceCheck] | None = None,
    auto_approve_on_pass: bool = False,
    max_retries: int = 0,
) -> EvaluatorProfile:
    """Build an EvaluatorProfile with sensible defaults."""
    return EvaluatorProfile(
        checks=checks or [],
        auto_approve_on_pass=auto_approve_on_pass,
        max_retries=max_retries,
    )


def _make_proc(returncode: int = 0, stdout: bytes = b"ok", stderr: bytes = b"") -> MagicMock:
    """Return a mock subprocess that resolves communicate() immediately."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


# ---------------------------------------------------------------------------
# Singleton hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Guarantee the module singleton is cleared before and after every test."""
    reset_evaluator_orchestrator()
    yield
    reset_evaluator_orchestrator()


# ---------------------------------------------------------------------------
# CheckResult model
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_required_fields(self):
        result = CheckResult(
            name="unit-tests",
            passed=True,
            command="pytest",
            duration_seconds=1.5,
        )
        assert result.name == "unit-tests"
        assert result.passed is True
        assert result.command == "pytest"
        assert result.duration_seconds == 1.5
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.required is True  # default

    def test_optional_fields(self):
        result = CheckResult(
            name="lint",
            passed=False,
            command="ruff .",
            stdout="W001 warning",
            stderr="error detail",
            duration_seconds=0.5,
            required=False,
        )
        assert result.required is False
        assert result.stdout == "W001 warning"
        assert result.stderr == "error detail"

    def test_duration_must_be_non_negative(self):
        with pytest.raises(Exception):
            CheckResult(name="x", passed=True, command="x", duration_seconds=-1.0)


# ---------------------------------------------------------------------------
# EvaluatorResult model
# ---------------------------------------------------------------------------


class TestEvaluatorResult:
    def test_basic_construction(self):
        cr = CheckResult(name="a", passed=True, command="true", duration_seconds=0.1)
        result = EvaluatorResult(
            task_id="task-001",
            check_results=[cr],
            all_passed=True,
            required_passed=True,
            duration_seconds=0.1,
        )
        assert result.task_id == "task-001"
        assert len(result.check_results) == 1
        assert result.auto_approved is False  # default

    def test_auto_approved_default_false(self):
        result = EvaluatorResult(
            task_id="t",
            check_results=[],
            all_passed=True,
            required_passed=True,
            duration_seconds=0.0,
        )
        assert result.auto_approved is False

    def test_duration_must_be_non_negative(self):
        with pytest.raises(Exception):
            EvaluatorResult(
                task_id="t",
                check_results=[],
                all_passed=True,
                required_passed=True,
                duration_seconds=-0.5,
            )


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.run_checks — all checks passing
# ---------------------------------------------------------------------------


class TestRunChecksAllPass:
    async def test_all_pass_sets_all_passed_true(self):
        checks = [
            _make_check("unit-tests", "pytest -q"),
            _make_check("lint", "ruff ."),
        ]
        profile = _make_profile(checks=checks)
        proc = _make_proc(returncode=0, stdout=b"2 passed", stderr=b"")

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-001", profile)

        assert result.all_passed is True
        assert result.required_passed is True
        assert len(result.check_results) == 2
        assert all(cr.passed for cr in result.check_results)

    async def test_stdout_and_stderr_captured(self):
        checks = [_make_check("greet", "echo hello")]
        profile = _make_profile(checks=checks)
        proc = _make_proc(returncode=0, stdout=b"hello\n", stderr=b"warning\n")

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-002", profile)

        cr = result.check_results[0]
        assert cr.stdout == "hello\n"
        assert cr.stderr == "warning\n"

    async def test_task_id_propagated(self):
        profile = _make_profile(checks=[_make_check()])
        proc = _make_proc()

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("my-task-id", profile)

        assert result.task_id == "my-task-id"

    async def test_duration_seconds_positive(self):
        profile = _make_profile(checks=[_make_check()])
        proc = _make_proc()

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.duration_seconds >= 0.0

    async def test_check_result_preserves_required_flag(self):
        checks = [
            _make_check("required-check", required=True),
            _make_check("optional-check", required=False),
        ]
        profile = _make_profile(checks=checks)
        proc = _make_proc()

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.check_results[0].required is True
        assert result.check_results[1].required is False


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.run_checks — required check failing
# ---------------------------------------------------------------------------


class TestRunChecksRequiredFail:
    async def test_required_fail_sets_all_passed_false(self):
        checks = [_make_check("unit-tests", required=True)]
        profile = _make_profile(checks=checks)
        proc = _make_proc(returncode=1, stdout=b"", stderr=b"FAILED 3 errors")

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.all_passed is False
        assert result.required_passed is False

    async def test_required_fail_auto_approved_false_regardless_of_profile(self):
        checks = [_make_check("unit-tests", required=True)]
        profile = _make_profile(checks=checks, auto_approve_on_pass=True)
        proc = _make_proc(returncode=1)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.auto_approved is False

    async def test_remaining_checks_still_run_after_required_fail(self):
        checks = [
            _make_check("fail-check", required=True),
            _make_check("second-check", required=False),
        ]
        profile = _make_profile(checks=checks)

        fail_proc = _make_proc(returncode=1)
        pass_proc = _make_proc(returncode=0)
        call_seq = [fail_proc, pass_proc]

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=call_seq),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert len(result.check_results) == 2
        assert result.check_results[0].passed is False
        assert result.check_results[1].passed is True


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.run_checks — optional check failing
# ---------------------------------------------------------------------------


class TestRunChecksOptionalFail:
    async def test_optional_fail_all_passed_false_required_passed_true(self):
        checks = [
            _make_check("required-check", required=True),
            _make_check("optional-check", required=False),
        ]
        profile = _make_profile(checks=checks)

        pass_proc = _make_proc(returncode=0)
        fail_proc = _make_proc(returncode=1)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=[pass_proc, fail_proc]),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.all_passed is False
        assert result.required_passed is True

    async def test_optional_only_fail_auto_approved_when_profile_flag_true(self):
        checks = [
            _make_check("required-check", required=True),
            _make_check("optional-check", required=False),
        ]
        profile = _make_profile(checks=checks, auto_approve_on_pass=True)

        pass_proc = _make_proc(returncode=0)
        fail_proc = _make_proc(returncode=1)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=[pass_proc, fail_proc]),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        # required passed + auto_approve_on_pass → auto_approved
        assert result.auto_approved is True


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    async def test_timeout_marks_check_failed(self):
        checks = [_make_check("slow-check", command="sleep 100", timeout_seconds=1)]
        profile = _make_profile(checks=checks)

        # Simulate proc.communicate() timing out
        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.all_passed is False
        assert result.required_passed is False
        cr = result.check_results[0]
        assert cr.passed is False
        assert "Timed out" in cr.stderr

    async def test_timeout_kills_process(self):
        checks = [_make_check("slow-check", timeout_seconds=1)]
        profile = _make_profile(checks=checks)

        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("t", profile)

        proc.kill.assert_called_once()

    async def test_timeout_process_already_exited_no_raise(self):
        """ProcessLookupError during kill() after timeout is swallowed."""
        checks = [_make_check("slow-check", timeout_seconds=1)]
        profile = _make_profile(checks=checks)

        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock(side_effect=ProcessLookupError())
        proc.wait = AsyncMock(return_value=None)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            # Must not raise
            result = await orch.run_checks("t", profile)

        assert result.check_results[0].passed is False

    async def test_timeout_duration_recorded(self):
        checks = [_make_check("slow-check", timeout_seconds=1)]
        profile = _make_profile(checks=checks)

        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.check_results[0].duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# auto_approve_on_pass logic
# ---------------------------------------------------------------------------


class TestAutoApprove:
    async def test_auto_approve_false_when_flag_false(self):
        checks = [_make_check(required=True)]
        profile = _make_profile(checks=checks, auto_approve_on_pass=False)
        proc = _make_proc(returncode=0)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.required_passed is True
        assert result.auto_approved is False

    async def test_auto_approve_true_when_flag_true_and_required_pass(self):
        checks = [_make_check(required=True)]
        profile = _make_profile(checks=checks, auto_approve_on_pass=True)
        proc = _make_proc(returncode=0)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.auto_approved is True

    async def test_auto_approve_false_when_flag_true_but_required_fail(self):
        checks = [_make_check(required=True)]
        profile = _make_profile(checks=checks, auto_approve_on_pass=True)
        proc = _make_proc(returncode=1)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.auto_approved is False


# ---------------------------------------------------------------------------
# Empty checks list
# ---------------------------------------------------------------------------


class TestEmptyChecks:
    async def test_empty_checks_all_passed_true(self):
        profile = _make_profile(checks=[], auto_approve_on_pass=False)
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("t", profile)

        assert result.all_passed is True
        assert result.required_passed is True
        assert result.check_results == []

    async def test_empty_checks_auto_approve_respects_flag(self):
        profile_false = _make_profile(checks=[], auto_approve_on_pass=False)
        profile_true = _make_profile(checks=[], auto_approve_on_pass=True)

        orch = EvaluatorOrchestrator()
        result_false = await orch.run_checks("t1", profile_false)
        result_true = await orch.run_checks("t2", profile_true)

        assert result_false.auto_approved is False
        assert result_true.auto_approved is True

    async def test_empty_checks_duration_non_negative(self):
        profile = _make_profile(checks=[])
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("t", profile)

        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Command not found / subprocess error
# ---------------------------------------------------------------------------


class TestSubprocessError:
    async def test_file_not_found_marks_check_failed(self):
        checks = [_make_check("missing-cmd", command="nonexistent-binary-xyz")]
        profile = _make_profile(checks=checks)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("No such file")),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.all_passed is False
        assert result.required_passed is False
        cr = result.check_results[0]
        assert cr.passed is False
        assert "No such file" in cr.stderr

    async def test_os_error_marks_check_failed(self):
        checks = [_make_check("bad-cmd", command="bad-cmd")]
        profile = _make_profile(checks=checks)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("Permission denied")),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        cr = result.check_results[0]
        assert cr.passed is False
        assert "Permission denied" in cr.stderr

    async def test_general_exception_marks_check_failed(self):
        checks = [_make_check("flaky-cmd")]
        profile = _make_profile(checks=checks)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=RuntimeError("internal error")),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.check_results[0].passed is False

    async def test_malformed_command_marks_check_failed(self):
        """shlex.split raises ValueError for unterminated quotes."""
        checks = [_make_check("bad-shell", command="echo 'unterminated")]
        profile = _make_profile(checks=checks)

        orch = EvaluatorOrchestrator()
        # No mock needed — shlex.split raises before subprocess is called
        result = await orch.run_checks("t", profile)

        cr = result.check_results[0]
        assert cr.passed is False
        assert "parse error" in cr.stderr.lower()

    async def test_subprocess_error_command_name_preserved(self):
        checks = [_make_check("my-check", command="fail-cmd")]
        profile = _make_profile(checks=checks)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("not found")),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.check_results[0].name == "my-check"
        assert result.check_results[0].command == "fail-cmd"


# ---------------------------------------------------------------------------
# get_summary()
# ---------------------------------------------------------------------------


class TestGetSummary:
    async def test_empty_history_returns_zeroes(self):
        orch = EvaluatorOrchestrator()
        summary = orch.get_summary()

        assert summary["total"] == 0
        assert summary["passed"] == 0
        assert summary["required_passed"] == 0
        assert summary["auto_approved"] == 0
        assert summary["pass_rate"] == 0.0

    async def test_summary_after_single_pass(self):
        profile = _make_profile(checks=[_make_check()], auto_approve_on_pass=True)
        proc = _make_proc(returncode=0)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("t", profile)

        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["required_passed"] == 1
        assert summary["auto_approved"] == 1
        assert summary["pass_rate"] == 1.0

    async def test_summary_after_single_fail(self):
        profile = _make_profile(checks=[_make_check()], auto_approve_on_pass=True)
        proc = _make_proc(returncode=1)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("t", profile)

        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 0
        assert summary["required_passed"] == 0
        assert summary["auto_approved"] == 0
        assert summary["pass_rate"] == 0.0

    async def test_summary_mixed_results(self):
        profile = _make_profile(checks=[_make_check()])

        pass_proc = _make_proc(returncode=0)
        fail_proc = _make_proc(returncode=1)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=[pass_proc, fail_proc]),
        ):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("t1", profile)
            await orch.run_checks("t2", profile)

        summary = orch.get_summary()
        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["pass_rate"] == 0.5

    async def test_summary_window_limits_history(self):
        profile = _make_profile(checks=[_make_check()])
        orch = EvaluatorOrchestrator(summary_window=3)

        for i in range(5):
            proc = _make_proc(returncode=0)
            with patch(
                "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=proc),
            ):
                await orch.run_checks(f"t{i}", profile)

        summary = orch.get_summary()
        assert summary["total"] == 3  # window capped at 3

    async def test_summary_empty_checks_counted(self):
        """Empty check lists still contribute a result to history."""
        profile = _make_profile(checks=[])
        orch = EvaluatorOrchestrator()
        await orch.run_checks("t", profile)

        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 1  # vacuous pass


# ---------------------------------------------------------------------------
# work_dir is passed to subprocess
# ---------------------------------------------------------------------------


class TestWorkDir:
    async def test_work_dir_forwarded_to_subprocess(self, tmp_path):
        checks = [_make_check("check", command="pwd")]
        profile = _make_profile(checks=checks)
        proc = _make_proc(returncode=0, stdout=str(tmp_path).encode())

        create_mock = AsyncMock(return_value=proc)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=create_mock,
        ):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("t", profile, work_dir=str(tmp_path))

        _, kwargs = create_mock.call_args
        assert kwargs.get("cwd") == str(tmp_path)

    async def test_no_work_dir_passes_none(self):
        checks = [_make_check("check", command="true")]
        profile = _make_profile(checks=checks)
        proc = _make_proc()

        create_mock = AsyncMock(return_value=proc)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=create_mock,
        ):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("t", profile, work_dir=None)

        _, kwargs = create_mock.call_args
        assert kwargs.get("cwd") is None


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    def test_returns_evaluator_orchestrator_instance(self):
        orch = get_evaluator_orchestrator()
        assert isinstance(orch, EvaluatorOrchestrator)

    def test_same_instance_on_repeated_calls(self):
        first = get_evaluator_orchestrator()
        second = get_evaluator_orchestrator()
        assert first is second

    def test_reset_clears_singleton(self):
        first = get_evaluator_orchestrator()
        reset_evaluator_orchestrator()
        second = get_evaluator_orchestrator()
        assert first is not second

    def test_reset_is_idempotent(self):
        reset_evaluator_orchestrator()
        reset_evaluator_orchestrator()  # Must not raise

    def test_custom_summary_window_applied_on_first_call(self):
        orch = get_evaluator_orchestrator(summary_window=5)
        assert orch._history.maxlen == 5

    def test_second_call_ignores_summary_window_arg(self):
        first = get_evaluator_orchestrator(summary_window=5)
        second = get_evaluator_orchestrator(summary_window=999)
        # Both should be the same object with maxlen=5
        assert first is second
        assert first._history.maxlen == 5

    def test_reset_allows_new_window_on_next_call(self):
        get_evaluator_orchestrator(summary_window=5)
        reset_evaluator_orchestrator()
        orch = get_evaluator_orchestrator(summary_window=50)
        assert orch._history.maxlen == 50


# ---------------------------------------------------------------------------
# Services __init__ re-exports
# ---------------------------------------------------------------------------


class TestServicesInit:
    def test_check_result_importable_from_services(self):
        from forge_harness.webhook_server.services import CheckResult as CheckResultAlias

        assert CheckResultAlias is CheckResult

    def test_evaluator_result_importable_from_services(self):
        from forge_harness.webhook_server.services import EvaluatorResult as EvaluatorResultAlias

        assert EvaluatorResultAlias is EvaluatorResult

    def test_evaluator_orchestrator_importable_from_services(self):
        from forge_harness.webhook_server.services import (
            EvaluatorOrchestrator as EvaluatorOrchestratorAlias,
        )

        assert EvaluatorOrchestratorAlias is EvaluatorOrchestrator

    def test_get_evaluator_orchestrator_importable_from_services(self):
        from forge_harness.webhook_server.services import (
            get_evaluator_orchestrator as geo,
        )

        assert callable(geo)

    def test_reset_evaluator_orchestrator_importable_from_services(self):
        from forge_harness.webhook_server.services import (
            reset_evaluator_orchestrator as reo,
        )

        assert callable(reo)


# ---------------------------------------------------------------------------
# Multi-check ordering
# ---------------------------------------------------------------------------


class TestCheckOrdering:
    async def test_results_in_profile_order(self):
        checks = [
            _make_check("first", command="echo 1"),
            _make_check("second", command="echo 2"),
            _make_check("third", command="echo 3"),
        ]
        profile = _make_profile(checks=checks)

        procs = [_make_proc(returncode=0) for _ in checks]

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=procs),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        names = [cr.name for cr in result.check_results]
        assert names == ["first", "second", "third"]

    async def test_command_recorded_per_check(self):
        checks = [
            _make_check("a", command="cmd-a"),
            _make_check("b", command="cmd-b"),
        ]
        profile = _make_profile(checks=checks)
        procs = [_make_proc(), _make_proc()]

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=procs),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("t", profile)

        assert result.check_results[0].command == "cmd-a"
        assert result.check_results[1].command == "cmd-b"

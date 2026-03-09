"""Comprehensive unit tests for the evaluator orchestrator service.

Covers:
- CheckResult model: creation, field validation, defaults, frozen config
- EvaluatorResult model: creation, field validation, auto_approved semantics
- EvaluatorOrchestrator.__init__: summary_window parameter
- EvaluatorOrchestrator.run_checks: sequential execution, aggregation,
  all_passed, required_passed, auto_approved, duration, history appended
- EvaluatorOrchestrator.get_summary: empty, single, multiple results,
  pass_rate calculation, window eviction
- EvaluatorOrchestrator._run_single_check: success (exit 0), failure (exit 1),
  timeout handling, malformed command, FileNotFoundError / OS error,
  binary / non-UTF-8 output, optional (non-required) check failing
- Singleton: get_evaluator_orchestrator, reset_evaluator_orchestrator,
  thread-safety (lock re-use), subsequent calls return same instance
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
    _SUMMARY_WINDOW,
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
    max_retries: int = 1,
) -> EvaluatorProfile:
    """Build an EvaluatorProfile with sensible defaults."""
    return EvaluatorProfile(
        checks=checks or [],
        auto_approve_on_pass=auto_approve_on_pass,
        max_retries=max_retries,
    )


def _make_orchestrator(summary_window: int = 10) -> EvaluatorOrchestrator:
    """Create a fresh EvaluatorOrchestrator (not the singleton)."""
    return EvaluatorOrchestrator(summary_window=summary_window)


# ---------------------------------------------------------------------------
# CheckResult model
# ---------------------------------------------------------------------------


class TestCheckResult:
    """Tests for the CheckResult Pydantic model."""

    def test_minimal_creation(self):
        """CheckResult can be created with all required fields."""
        result = CheckResult(
            name="lint",
            passed=True,
            command="ruff check .",
            duration_seconds=1.5,
        )
        assert result.name == "lint"
        assert result.passed is True
        assert result.command == "ruff check ."
        assert result.duration_seconds == 1.5

    def test_default_stdout_stderr_empty(self):
        """stdout and stderr default to empty strings."""
        result = CheckResult(
            name="check",
            passed=False,
            command="false",
            duration_seconds=0.1,
        )
        assert result.stdout == ""
        assert result.stderr == ""

    def test_default_required_is_true(self):
        """required defaults to True."""
        result = CheckResult(
            name="check",
            passed=True,
            command="echo hi",
            duration_seconds=0.0,
        )
        assert result.required is True

    def test_optional_check_fields(self):
        """All optional fields can be supplied explicitly."""
        result = CheckResult(
            name="smoke",
            passed=False,
            command="curl localhost",
            stdout="output",
            stderr="error",
            duration_seconds=2.3,
            required=False,
        )
        assert result.stdout == "output"
        assert result.stderr == "error"
        assert result.required is False

    def test_duration_seconds_ge_zero(self):
        """duration_seconds must be >= 0."""
        with pytest.raises(Exception):
            CheckResult(
                name="check",
                passed=True,
                command="true",
                duration_seconds=-0.1,
            )

    def test_model_is_mutable(self):
        """CheckResult is NOT frozen — fields can be mutated post-creation."""
        result = CheckResult(
            name="check",
            passed=True,
            command="true",
            duration_seconds=0.5,
        )
        result.passed = False
        assert result.passed is False


# ---------------------------------------------------------------------------
# EvaluatorResult model
# ---------------------------------------------------------------------------


class TestEvaluatorResult:
    """Tests for the EvaluatorResult Pydantic model."""

    def _make_check_result(self, passed: bool = True, required: bool = True) -> CheckResult:
        return CheckResult(
            name="c",
            passed=passed,
            command="true",
            duration_seconds=0.1,
            required=required,
        )

    def test_minimal_creation(self):
        """EvaluatorResult can be created with required fields."""
        result = EvaluatorResult(
            task_id="TASK-001",
            all_passed=True,
            required_passed=True,
            duration_seconds=1.0,
        )
        assert result.task_id == "TASK-001"
        assert result.all_passed is True
        assert result.required_passed is True
        assert result.check_results == []

    def test_auto_approved_defaults_false(self):
        """auto_approved defaults to False."""
        result = EvaluatorResult(
            task_id="TASK-001",
            all_passed=True,
            required_passed=True,
            duration_seconds=0.5,
        )
        assert result.auto_approved is False

    def test_auto_approved_explicit_true(self):
        """auto_approved can be set to True explicitly."""
        result = EvaluatorResult(
            task_id="TASK-002",
            all_passed=True,
            required_passed=True,
            duration_seconds=0.5,
            auto_approved=True,
        )
        assert result.auto_approved is True

    def test_duration_seconds_ge_zero(self):
        """duration_seconds must be >= 0."""
        with pytest.raises(Exception):
            EvaluatorResult(
                task_id="T",
                all_passed=True,
                required_passed=True,
                duration_seconds=-1.0,
            )

    def test_check_results_list(self):
        """check_results stores the provided list."""
        cr = self._make_check_result()
        result = EvaluatorResult(
            task_id="T",
            check_results=[cr],
            all_passed=True,
            required_passed=True,
            duration_seconds=0.1,
        )
        assert len(result.check_results) == 1
        assert result.check_results[0].name == "c"


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator — init
# ---------------------------------------------------------------------------


class TestEvaluatorOrchestratorInit:
    """Tests for EvaluatorOrchestrator initialisation."""

    def test_default_summary_window(self):
        """Default summary_window is _SUMMARY_WINDOW (100)."""
        orch = EvaluatorOrchestrator()
        assert orch._history.maxlen == _SUMMARY_WINDOW

    def test_custom_summary_window(self):
        """Custom summary_window is respected."""
        orch = EvaluatorOrchestrator(summary_window=42)
        assert orch._history.maxlen == 42

    def test_history_starts_empty(self):
        """History deque starts empty."""
        orch = _make_orchestrator()
        assert len(orch._history) == 0


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.get_summary — no runs
# ---------------------------------------------------------------------------


class TestGetSummaryEmpty:
    """get_summary with no history."""

    def test_empty_summary(self):
        """get_summary returns all-zero dict when history is empty."""
        orch = _make_orchestrator()
        summary = orch.get_summary()
        assert summary == {
            "total": 0,
            "passed": 0,
            "required_passed": 0,
            "auto_approved": 0,
            "pass_rate": 0.0,
        }


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.get_summary — with data
# ---------------------------------------------------------------------------


class TestGetSummaryWithData:
    """get_summary with pre-populated history."""

    def _inject_results(self, orch: EvaluatorOrchestrator, specs: list[dict]) -> None:
        """Directly inject EvaluatorResult objects into history."""
        for spec in specs:
            orch._history.append(
                EvaluatorResult(
                    task_id=spec.get("task_id", "T"),
                    all_passed=spec.get("all_passed", False),
                    required_passed=spec.get("required_passed", False),
                    duration_seconds=spec.get("duration_seconds", 1.0),
                    auto_approved=spec.get("auto_approved", False),
                )
            )

    def test_single_passing_result(self):
        orch = _make_orchestrator()
        self._inject_results(orch, [{"all_passed": True, "required_passed": True}])
        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["required_passed"] == 1
        assert summary["pass_rate"] == 1.0

    def test_single_failing_result(self):
        orch = _make_orchestrator()
        self._inject_results(orch, [{"all_passed": False, "required_passed": False}])
        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 0
        assert summary["required_passed"] == 0
        assert summary["pass_rate"] == 0.0

    def test_mixed_results_pass_rate(self):
        """pass_rate = passed / total, rounded to 4dp."""
        orch = _make_orchestrator()
        self._inject_results(
            orch,
            [
                {"all_passed": True, "required_passed": True},
                {"all_passed": True, "required_passed": True},
                {"all_passed": False, "required_passed": False},
            ],
        )
        summary = orch.get_summary()
        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["required_passed"] == 2
        assert summary["pass_rate"] == round(2 / 3, 4)

    def test_auto_approved_count(self):
        """auto_approved counter tallies correctly."""
        orch = _make_orchestrator()
        self._inject_results(
            orch,
            [
                {"all_passed": True, "required_passed": True, "auto_approved": True},
                {"all_passed": True, "required_passed": True, "auto_approved": False},
                {"all_passed": False, "required_passed": False, "auto_approved": False},
            ],
        )
        summary = orch.get_summary()
        assert summary["auto_approved"] == 1

    def test_window_eviction(self):
        """History deque evicts oldest entries beyond summary_window."""
        orch = EvaluatorOrchestrator(summary_window=3)
        for i in range(5):
            orch._history.append(
                EvaluatorResult(
                    task_id=f"T-{i}",
                    all_passed=(i % 2 == 0),
                    required_passed=(i % 2 == 0),
                    duration_seconds=0.1,
                )
            )
        summary = orch.get_summary()
        assert summary["total"] == 3  # window capped at 3

    def test_required_passed_vs_all_passed(self):
        """required_passed and all_passed are counted independently."""
        orch = _make_orchestrator()
        # required_passed=True but all_passed=False (optional check failed)
        self._inject_results(
            orch,
            [{"all_passed": False, "required_passed": True}],
        )
        summary = orch.get_summary()
        assert summary["passed"] == 0
        assert summary["required_passed"] == 1


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.run_checks — subprocess mocking helpers
# ---------------------------------------------------------------------------


def _mock_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    """Return a mock asyncio Process with communicate() returning the given data."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.run_checks — happy paths
# ---------------------------------------------------------------------------


class TestRunChecksSuccess:
    """run_checks with passing commands."""

    @pytest.mark.asyncio
    async def test_empty_profile_returns_all_passed(self):
        """Profile with no checks produces all_passed=True vacuously."""
        orch = _make_orchestrator()
        profile = _make_profile(checks=[])
        result = await orch.run_checks(task_id="T-empty", profile=profile)

        assert result.task_id == "T-empty"
        assert result.check_results == []
        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is False

    @pytest.mark.asyncio
    async def test_empty_profile_auto_approve_on_pass_true(self):
        """Vacuous required_passed + auto_approve_on_pass=True => auto_approved=True."""
        orch = _make_orchestrator()
        profile = _make_profile(checks=[], auto_approve_on_pass=True)
        result = await orch.run_checks(task_id="T-vac", profile=profile)

        assert result.required_passed is True
        assert result.auto_approved is True

    @pytest.mark.asyncio
    async def test_single_passing_check(self):
        """A single required check that exits 0 produces all_passed=True."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0, stdout=b"ok\n", stderr=b"")
        profile = _make_profile(
            checks=[_make_check(name="lint", command="ruff check .")],
            auto_approve_on_pass=True,
        )

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-1", profile=profile)

        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is True
        assert len(result.check_results) == 1
        assert result.check_results[0].name == "lint"
        assert result.check_results[0].passed is True
        assert result.check_results[0].stdout == "ok\n"

    @pytest.mark.asyncio
    async def test_multiple_passing_checks(self):
        """All checks passing => all_passed + required_passed + auto_approved."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0, stdout=b"ok", stderr=b"")
        checks = [
            _make_check(name="c1", command="echo 1"),
            _make_check(name="c2", command="echo 2"),
            _make_check(name="c3", command="echo 3"),
        ]
        profile = _make_profile(checks=checks, auto_approve_on_pass=True)

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-multi", profile=profile)

        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is True
        assert len(result.check_results) == 3

    @pytest.mark.asyncio
    async def test_result_appended_to_history(self):
        """Completed result is appended to the orchestrator's history deque."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        profile = _make_profile(checks=[_make_check()], auto_approve_on_pass=False)

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-hist", profile=profile)

        assert len(orch._history) == 1
        assert orch._history[0] is result

    @pytest.mark.asyncio
    async def test_work_dir_forwarded_to_subprocess(self):
        """work_dir is passed as cwd to create_subprocess_exec."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        profile = _make_profile(checks=[_make_check(command="true")])

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec:
            await orch.run_checks(task_id="T-wd", profile=profile, work_dir="/tmp/workdir")

        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs.get("cwd") == "/tmp/workdir"

    @pytest.mark.asyncio
    async def test_work_dir_none_passes_none(self):
        """When work_dir is None, cwd=None is forwarded."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        profile = _make_profile(checks=[_make_check(command="true")])

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec:
            await orch.run_checks(task_id="T-nwd", profile=profile, work_dir=None)

        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs.get("cwd") is None


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator.run_checks — failure scenarios
# ---------------------------------------------------------------------------


class TestRunChecksFailure:
    """run_checks with failing commands."""

    @pytest.mark.asyncio
    async def test_required_check_fails_blocks_auto_approve(self):
        """A required check failing prevents auto_approved even if profile allows it."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=1, stderr=b"test failed")
        profile = _make_profile(
            checks=[_make_check(name="tests", required=True)],
            auto_approve_on_pass=True,
        )

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-fail", profile=profile)

        assert result.all_passed is False
        assert result.required_passed is False
        assert result.auto_approved is False
        assert result.check_results[0].passed is False

    @pytest.mark.asyncio
    async def test_optional_check_fails_does_not_block_required_passed(self):
        """An optional check failing does not affect required_passed."""
        orch = _make_orchestrator()

        # optional check fails (returncode=1), required check passes (returncode=0)
        pass_proc = _mock_process(returncode=0)
        fail_proc = _mock_process(returncode=1)

        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            call_count["n"] += 1
            return pass_proc if call_count["n"] == 1 else fail_proc

        profile = _make_profile(
            checks=[
                _make_check(name="required-check", required=True),
                _make_check(name="optional-check", required=False),
            ],
            auto_approve_on_pass=True,
        )

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await orch.run_checks(task_id="T-opt", profile=profile)

        assert result.required_passed is True
        assert result.auto_approved is True
        assert result.all_passed is False  # optional check failed
        assert result.check_results[0].passed is True
        assert result.check_results[1].passed is False

    @pytest.mark.asyncio
    async def test_all_checks_run_even_after_failure(self):
        """All checks execute sequentially; a failing check does not short-circuit."""
        orch = _make_orchestrator()

        procs = [
            _mock_process(returncode=1),  # check 1 fails
            _mock_process(returncode=0),  # check 2 passes
            _mock_process(returncode=0),  # check 3 passes
        ]
        proc_iter = iter(procs)

        async def side_effect(*args, **kwargs):
            return next(proc_iter)

        profile = _make_profile(
            checks=[
                _make_check(name="c1"),
                _make_check(name="c2"),
                _make_check(name="c3"),
            ]
        )

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await orch.run_checks(task_id="T-seq", profile=profile)

        assert len(result.check_results) == 3
        assert result.check_results[0].passed is False
        assert result.check_results[1].passed is True
        assert result.check_results[2].passed is True

    @pytest.mark.asyncio
    async def test_stderr_captured_on_failure(self):
        """stderr is captured from failing process."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=2, stdout=b"", stderr=b"fatal: not a git repo")
        profile = _make_profile(checks=[_make_check(command="git status")])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-stderr", profile=profile)

        cr = result.check_results[0]
        assert cr.passed is False
        assert "not a git repo" in cr.stderr

    @pytest.mark.asyncio
    async def test_no_required_checks_required_passed_vacuously_true(self):
        """When all checks are optional, required_passed is vacuously True."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=1)  # all fail
        profile = _make_profile(
            checks=[
                _make_check(name="opt1", required=False),
                _make_check(name="opt2", required=False),
            ],
            auto_approve_on_pass=True,
        )

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-noReq", profile=profile)

        assert result.required_passed is True
        assert result.auto_approved is True
        assert result.all_passed is False


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator._run_single_check — timeout
# ---------------------------------------------------------------------------


class TestRunSingleCheckTimeout:
    """_run_single_check handles subprocess timeouts."""

    @pytest.mark.asyncio
    async def test_timeout_marks_check_failed(self):
        """A subprocess timeout produces passed=False with a timeout message in stderr."""
        orch = _make_orchestrator()
        check = _make_check(name="slow-test", command="sleep 999", timeout_seconds=1)

        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.passed is False
        assert "Timed out" in result.stderr
        assert "1s" in result.stderr
        assert result.stdout == ""

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        """On timeout, proc.kill() is called."""
        orch = _make_orchestrator()
        check = _make_check(name="slow", command="sleep 999", timeout_seconds=1)

        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            await orch._run_single_check(check, work_dir=None)

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_process_already_exited_no_error(self):
        """ProcessLookupError on kill() is silently swallowed."""
        orch = _make_orchestrator()
        check = _make_check(name="gone", command="sleep 1", timeout_seconds=1)

        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock(side_effect=ProcessLookupError())
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.passed is False


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator._run_single_check — malformed command
# ---------------------------------------------------------------------------


class TestRunSingleCheckMalformedCommand:
    """_run_single_check handles unparseable command strings."""

    @pytest.mark.asyncio
    async def test_malformed_command_immediate_failure(self):
        """An unclosed quote in command triggers immediate CheckResult failure."""
        orch = _make_orchestrator()
        # Unclosed quote — shlex.split raises ValueError
        check = _make_check(name="bad-cmd", command="echo 'unclosed")

        result = await orch._run_single_check(check, work_dir=None)

        assert result.passed is False
        assert "Command parse error" in result.stderr
        assert result.stdout == ""

    @pytest.mark.asyncio
    async def test_malformed_command_check_name_and_required_preserved(self):
        """name and required are preserved even on a parse error."""
        orch = _make_orchestrator()
        check = _make_check(name="critical", command="echo 'bad", required=True)

        result = await orch._run_single_check(check, work_dir=None)

        assert result.name == "critical"
        assert result.required is True

    @pytest.mark.asyncio
    async def test_malformed_command_no_subprocess_launched(self):
        """No subprocess is spawned when the command fails to parse."""
        orch = _make_orchestrator()
        check = _make_check(name="bad", command="'broken")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_exec:
            await orch._run_single_check(check, work_dir=None)

        mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator._run_single_check — OS-level errors
# ---------------------------------------------------------------------------


class TestRunSingleCheckOsError:
    """_run_single_check handles OS-level exceptions (e.g. command not found)."""

    @pytest.mark.asyncio
    async def test_file_not_found_error(self):
        """FileNotFoundError (command not found) produces a failed CheckResult."""
        orch = _make_orchestrator()
        check = _make_check(name="missing", command="nonexistent_binary_xyz")

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("No such file")),
        ):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.passed is False
        assert "No such file" in result.stderr
        assert result.stdout == ""

    @pytest.mark.asyncio
    async def test_permission_error(self):
        """PermissionError produces a failed CheckResult."""
        orch = _make_orchestrator()
        check = _make_check(name="no-perm", command="/root/secret_script")

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=PermissionError("Permission denied")),
        ):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.passed is False
        assert "Permission denied" in result.stderr

    @pytest.mark.asyncio
    async def test_generic_exception_produces_failure(self):
        """Any other exception produces a failed CheckResult with error in stderr."""
        orch = _make_orchestrator()
        check = _make_check(name="explode", command="true")

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.passed is False
        assert "unexpected" in result.stderr


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator._run_single_check — encoding
# ---------------------------------------------------------------------------


class TestRunSingleCheckEncoding:
    """_run_single_check decodes binary/invalid-UTF-8 output without raising."""

    @pytest.mark.asyncio
    async def test_valid_utf8_output(self):
        """Normal UTF-8 stdout/stderr is decoded correctly."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0, stdout="héllo".encode(), stderr=b"")
        check = _make_check(command="echo héllo")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert "héllo" in result.stdout

    @pytest.mark.asyncio
    async def test_binary_output_does_not_raise(self):
        """Invalid UTF-8 bytes in stdout are replaced rather than raising."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0, stdout=b"\xff\xfe binary \x00", stderr=b"")
        check = _make_check(command="binary-tool")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        # Should not raise; result must be a str
        assert isinstance(result.stdout, str)

    @pytest.mark.asyncio
    async def test_binary_stderr_does_not_raise(self):
        """Invalid UTF-8 bytes in stderr are replaced rather than raising."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=1, stdout=b"", stderr=b"\xc0\x80 bad bytes")
        check = _make_check(command="bad-tool")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert isinstance(result.stderr, str)


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator._run_single_check — duration
# ---------------------------------------------------------------------------


class TestRunSingleCheckDuration:
    """_run_single_check records wall-clock duration."""

    @pytest.mark.asyncio
    async def test_duration_is_non_negative(self):
        """duration_seconds is always >= 0."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        check = _make_check(command="true")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_duration_recorded_on_timeout(self):
        """duration_seconds is recorded even on a timeout."""
        orch = _make_orchestrator()
        check = _make_check(command="sleep 999", timeout_seconds=1)

        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_duration_recorded_on_parse_error(self):
        """duration_seconds is recorded even when the command can't be parsed."""
        orch = _make_orchestrator()
        check = _make_check(command="'bad")

        result = await orch._run_single_check(check, work_dir=None)

        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# EvaluatorOrchestrator — command field on CheckResult
# ---------------------------------------------------------------------------


class TestCheckResultCommandField:
    """The command field on CheckResult mirrors AcceptanceCheck.command."""

    @pytest.mark.asyncio
    async def test_command_field_matches_check_command(self):
        """result.command matches the check's original command string."""
        orch = _make_orchestrator()
        cmd = "pytest tests/ -x --tb=short"
        proc = _mock_process(returncode=0)
        check = _make_check(command=cmd)

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.command == cmd

    @pytest.mark.asyncio
    async def test_command_field_preserved_on_error(self):
        """result.command is preserved even when an OS error occurs."""
        orch = _make_orchestrator()
        cmd = "nonexistent_tool"
        check = _make_check(command=cmd)

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("not found")),
        ):
            result = await orch._run_single_check(check, work_dir=None)

        assert result.command == cmd


# ---------------------------------------------------------------------------
# run_checks — duration totals
# ---------------------------------------------------------------------------


class TestRunChecksDuration:
    """Total duration in EvaluatorResult is the combined wall-clock time."""

    @pytest.mark.asyncio
    async def test_total_duration_is_non_negative(self):
        """duration_seconds on EvaluatorResult is >= 0."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        profile = _make_profile(checks=[_make_check(), _make_check(name="c2")])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-dur", profile=profile)

        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Singleton: get_evaluator_orchestrator / reset_evaluator_orchestrator
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for the module-level singleton factory."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_evaluator_orchestrator()

    def teardown_method(self):
        """Reset singleton after each test to avoid cross-test pollution."""
        reset_evaluator_orchestrator()

    def test_first_call_creates_instance(self):
        """get_evaluator_orchestrator returns an EvaluatorOrchestrator on first call."""
        inst = get_evaluator_orchestrator()
        assert isinstance(inst, EvaluatorOrchestrator)

    def test_subsequent_calls_return_same_instance(self):
        """All subsequent calls return the identical singleton object."""
        inst1 = get_evaluator_orchestrator()
        inst2 = get_evaluator_orchestrator()
        assert inst1 is inst2

    def test_summary_window_only_applied_on_first_call(self):
        """summary_window argument on subsequent calls has no effect."""
        inst1 = get_evaluator_orchestrator(summary_window=25)
        inst2 = get_evaluator_orchestrator(summary_window=99)
        assert inst1 is inst2
        assert inst1._history.maxlen == 25  # first-call value wins

    def test_reset_allows_new_instance(self):
        """After reset, a new singleton is created on next get call."""
        inst1 = get_evaluator_orchestrator()
        reset_evaluator_orchestrator()
        inst2 = get_evaluator_orchestrator()
        assert inst1 is not inst2

    def test_reset_clears_history(self):
        """New instance after reset has empty history."""
        inst1 = get_evaluator_orchestrator()
        inst1._history.append(
            EvaluatorResult(
                task_id="T",
                all_passed=True,
                required_passed=True,
                duration_seconds=0.1,
            )
        )
        reset_evaluator_orchestrator()
        inst2 = get_evaluator_orchestrator()
        assert len(inst2._history) == 0

    def test_default_summary_window_on_singleton(self):
        """Default singleton uses _SUMMARY_WINDOW."""
        inst = get_evaluator_orchestrator()
        assert inst._history.maxlen == _SUMMARY_WINDOW

    def test_thread_safety_single_instance(self):
        """Concurrent get calls from multiple threads all obtain same instance."""
        import threading

        instances: list[EvaluatorOrchestrator] = []
        lock = threading.Lock()

        def grab():
            i = get_evaluator_orchestrator()
            with lock:
                instances.append(i)

        threads = [threading.Thread(target=grab) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(instances) == 20
        first = instances[0]
        assert all(i is first for i in instances)


# ---------------------------------------------------------------------------
# run_checks — integration-style: profile with mixed required/optional
# ---------------------------------------------------------------------------


class TestRunChecksMixedProfile:
    """Integration-style tests with mixed required and optional checks."""

    @pytest.mark.asyncio
    async def test_mixed_checks_partial_required_failure(self):
        """Two required checks, one fails: required_passed=False, auto_approved=False."""
        orch = _make_orchestrator()

        results_seq = [
            _mock_process(returncode=0),  # required passes
            _mock_process(returncode=1),  # required fails
            _mock_process(returncode=1),  # optional fails
        ]
        seq = iter(results_seq)

        async def side_effect(*args, **kwargs):
            return next(seq)

        profile = _make_profile(
            checks=[
                _make_check(name="r1", required=True),
                _make_check(name="r2", required=True),
                _make_check(name="o1", required=False),
            ],
            auto_approve_on_pass=True,
        )

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await orch.run_checks(task_id="T-mixed", profile=profile)

        assert result.required_passed is False
        assert result.all_passed is False
        assert result.auto_approved is False

    @pytest.mark.asyncio
    async def test_required_pass_optional_fail_auto_approved(self):
        """Required checks pass, optional fails: required_passed=True, auto_approved=True."""
        orch = _make_orchestrator()

        results_seq = [
            _mock_process(returncode=0),  # required passes
            _mock_process(returncode=1),  # optional fails
        ]
        seq = iter(results_seq)

        async def side_effect(*args, **kwargs):
            return next(seq)

        profile = _make_profile(
            checks=[
                _make_check(name="req", required=True),
                _make_check(name="opt", required=False),
            ],
            auto_approve_on_pass=True,
        )

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await orch.run_checks(task_id="T-ra-of", profile=profile)

        assert result.required_passed is True
        assert result.auto_approved is True
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_auto_approve_false_never_auto_approves(self):
        """auto_approve_on_pass=False means auto_approved is always False."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        profile = _make_profile(
            checks=[_make_check(required=True)],
            auto_approve_on_pass=False,
        )

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await orch.run_checks(task_id="T-naa", profile=profile)

        assert result.required_passed is True
        assert result.auto_approved is False

    @pytest.mark.asyncio
    async def test_multiple_runs_accumulate_in_history(self):
        """Successive run_checks calls each append to history."""
        orch = _make_orchestrator()
        proc = _mock_process(returncode=0)
        profile = _make_profile(checks=[_make_check()])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            await orch.run_checks(task_id="T-A", profile=profile)
            await orch.run_checks(task_id="T-B", profile=profile)
            await orch.run_checks(task_id="T-C", profile=profile)

        assert len(orch._history) == 3
        task_ids = [r.task_id for r in orch._history]
        assert task_ids == ["T-A", "T-B", "T-C"]

"""Unit tests for forge_harness.webhook_server.services.evaluator.

All external dependencies (subprocess, time, logger) are mocked so that no
real network, filesystem, or OS calls are made.
"""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
)

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.evaluator import (
    _SUMMARY_WINDOW,
    CheckResult,
    EvaluatorOrchestrator,
    EvaluatorResult,
    get_evaluator_orchestrator,
    reset_evaluator_orchestrator,
)

# ===========================================================================
# Helpers / factories
# ===========================================================================


def make_check(
    name: str = "unit-tests",
    command: str = "pytest tests/",
    timeout_seconds: int = 60,
    required: bool = True,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        name=name,
        command=command,
        timeout_seconds=timeout_seconds,
        required=required,
    )


def make_profile(
    checks: list[AcceptanceCheck] | None = None,
    auto_approve_on_pass: bool = False,
) -> EvaluatorProfile:
    return EvaluatorProfile(
        checks=checks or [],
        auto_approve_on_pass=auto_approve_on_pass,
    )


def make_fake_process(returncode: int = 0, stdout: bytes = b"ok", stderr: bytes = b"") -> MagicMock:
    """Return a mock that looks like an asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


# ===========================================================================
# CheckResult model tests
# ===========================================================================


class TestCheckResultModel:
    def test_basic_construction(self):
        cr = CheckResult(
            name="lint",
            passed=True,
            command="ruff check .",
            stdout="All good",
            stderr="",
            duration_seconds=0.5,
            required=True,
        )
        assert cr.name == "lint"
        assert cr.passed is True
        assert cr.command == "ruff check ."
        assert cr.stdout == "All good"
        assert cr.stderr == ""
        assert cr.duration_seconds == 0.5
        assert cr.required is True

    def test_defaults(self):
        cr = CheckResult(
            name="x",
            passed=False,
            command="echo hi",
            duration_seconds=1.0,
        )
        assert cr.stdout == ""
        assert cr.stderr == ""
        assert cr.required is True

    def test_duration_must_be_non_negative(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            CheckResult(
                name="x",
                passed=True,
                command="echo",
                duration_seconds=-0.1,
            )


# ===========================================================================
# EvaluatorResult model tests
# ===========================================================================


class TestEvaluatorResultModel:
    def test_basic_construction(self):
        er = EvaluatorResult(
            task_id="t-1",
            check_results=[],
            all_passed=True,
            required_passed=True,
            duration_seconds=0.0,
            auto_approved=True,
        )
        assert er.task_id == "t-1"
        assert er.auto_approved is True

    def test_auto_approved_defaults_false(self):
        er = EvaluatorResult(
            task_id="t-2",
            all_passed=False,
            required_passed=False,
            duration_seconds=1.0,
        )
        assert er.auto_approved is False

    def test_duration_must_be_non_negative(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            EvaluatorResult(
                task_id="t-3",
                all_passed=True,
                required_passed=True,
                duration_seconds=-1.0,
            )


# ===========================================================================
# EvaluatorOrchestrator — get_summary() with empty history
# ===========================================================================


class TestGetSummaryEmpty:
    def test_returns_zeros_when_no_history(self):
        orch = EvaluatorOrchestrator(summary_window=10)
        summary = orch.get_summary()
        assert summary == {
            "total": 0,
            "passed": 0,
            "required_passed": 0,
            "auto_approved": 0,
            "pass_rate": 0.0,
        }


# ===========================================================================
# EvaluatorOrchestrator — run_checks() happy paths
# ===========================================================================


class TestRunChecksHappyPath:
    @pytest.mark.asyncio
    async def test_empty_profile_returns_all_passed(self):
        """An empty check list: vacuously all_passed and required_passed."""
        orch = EvaluatorOrchestrator()
        profile = make_profile(checks=[])
        result = await orch.run_checks(task_id="t-empty", profile=profile)

        assert result.task_id == "t-empty"
        assert result.check_results == []
        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is False  # auto_approve_on_pass defaults False
        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_single_passing_check(self):
        proc = make_fake_process(returncode=0, stdout=b"PASSED", stderr=b"")
        check = make_check(name="unit-tests", command="pytest tests/")
        profile = make_profile(checks=[check], auto_approve_on_pass=True)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-pass", profile=profile
            )

        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is True
        assert len(result.check_results) == 1
        cr = result.check_results[0]
        assert cr.name == "unit-tests"
        assert cr.passed is True
        assert cr.stdout == "PASSED"
        assert cr.required is True

    @pytest.mark.asyncio
    async def test_single_failing_required_check(self):
        proc = make_fake_process(returncode=1, stdout=b"", stderr=b"1 test failed")
        check = make_check(name="unit-tests", command="pytest tests/", required=True)
        profile = make_profile(checks=[check], auto_approve_on_pass=True)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-fail", profile=profile
            )

        assert result.all_passed is False
        assert result.required_passed is False
        assert result.auto_approved is False
        cr = result.check_results[0]
        assert cr.passed is False
        assert cr.stderr == "1 test failed"

    @pytest.mark.asyncio
    async def test_optional_check_failure_does_not_block_auto_approve(self):
        """A failing optional check: required_passed=True, auto_approved=True."""
        proc_pass = make_fake_process(returncode=0)
        proc_fail = make_fake_process(returncode=1)

        check_required = make_check(name="required", command="cmd-req", required=True)
        check_optional = make_check(name="optional", command="cmd-opt", required=False)
        profile = make_profile(
            checks=[check_required, check_optional], auto_approve_on_pass=True
        )

        call_count = 0
        procs = [proc_pass, proc_fail]

        async def fake_create(*args, **kwargs):
            nonlocal call_count
            p = procs[call_count]
            call_count += 1
            return p

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-opt", profile=profile
            )

        assert result.all_passed is False  # optional check failed
        assert result.required_passed is True  # required check passed
        assert result.auto_approved is True

    @pytest.mark.asyncio
    async def test_multiple_checks_all_pass(self):
        checks = [
            make_check(name=f"check-{i}", command=f"cmd-{i}") for i in range(3)
        ]
        profile = make_profile(checks=checks, auto_approve_on_pass=True)

        async def fake_create(*args, **kwargs):
            return make_fake_process(returncode=0)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-multi", profile=profile
            )

        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is True
        assert len(result.check_results) == 3

    @pytest.mark.asyncio
    async def test_all_checks_run_even_when_first_fails(self):
        """No short-circuiting — every check is always executed."""
        procs = [make_fake_process(returncode=1), make_fake_process(returncode=0)]
        call_count = 0

        async def fake_create(*args, **kwargs):
            nonlocal call_count
            p = procs[call_count]
            call_count += 1
            return p

        checks = [make_check(name=f"chk-{i}", command=f"cmd-{i}") for i in range(2)]
        profile = make_profile(checks=checks)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-no-short-circuit", profile=profile
            )

        assert call_count == 2
        assert result.check_results[0].passed is False
        assert result.check_results[1].passed is True

    @pytest.mark.asyncio
    async def test_work_dir_forwarded_to_subprocess(self):
        proc = make_fake_process(returncode=0)
        check = make_check()
        profile = make_profile(checks=[check])

        captured_kwargs: dict = {}

        async def fake_create(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return proc

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await EvaluatorOrchestrator().run_checks(
                task_id="t-cwd", profile=profile, work_dir="/some/dir"
            )

        assert captured_kwargs.get("cwd") == "/some/dir"

    @pytest.mark.asyncio
    async def test_work_dir_none_when_not_provided(self):
        proc = make_fake_process(returncode=0)
        check = make_check()
        profile = make_profile(checks=[check])
        captured_kwargs: dict = {}

        async def fake_create(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return proc

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await EvaluatorOrchestrator().run_checks(task_id="t-nocwd", profile=profile)

        assert captured_kwargs.get("cwd") is None

    @pytest.mark.asyncio
    async def test_auto_approve_false_even_when_required_passed(self):
        """auto_approve_on_pass=False should yield auto_approved=False regardless."""
        proc = make_fake_process(returncode=0)
        profile = make_profile(checks=[make_check()], auto_approve_on_pass=False)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-no-auto", profile=profile
            )

        assert result.required_passed is True
        assert result.auto_approved is False

    @pytest.mark.asyncio
    async def test_stdout_stderr_decoded_utf8(self):
        out = "héllo wörld".encode()
        err = "wärning".encode()
        proc = make_fake_process(returncode=0, stdout=out, stderr=err)
        profile = make_profile(checks=[make_check()])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-utf8", profile=profile
            )

        cr = result.check_results[0]
        assert cr.stdout == "héllo wörld"
        assert cr.stderr == "wärning"

    @pytest.mark.asyncio
    async def test_binary_output_uses_replace_error_handler(self):
        """Invalid UTF-8 bytes should not raise — errors='replace' is used."""
        proc = make_fake_process(returncode=0, stdout=b"\xff\xfe", stderr=b"\x80")
        profile = make_profile(checks=[make_check()])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-binary", profile=profile
            )

        # No exception raised; replacement characters present
        cr = result.check_results[0]
        assert "\ufffd" in cr.stdout or cr.stdout != ""


# ===========================================================================
# Timeout handling
# ===========================================================================


class TestTimeoutHandling:
    @pytest.mark.asyncio
    async def test_timeout_marks_check_failed(self):
        """When wait_for times out the check is recorded as failed."""
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        # communicate must be an AsyncMock so it returns a proper coroutine
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_wait_for(coro, timeout):
            # Cancel/close the coroutine to avoid the "was never awaited" warning
            coro.close()
            raise TimeoutError

        check = make_check(name="slow", command="sleep 9999", timeout_seconds=1)
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.wait_for",
            new=fake_wait_for,
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-timeout", profile=profile
            )

        cr = result.check_results[0]
        assert cr.passed is False
        assert "Timed out" in cr.stderr
        assert "1s" in cr.stderr
        assert cr.stdout == ""

    @pytest.mark.asyncio
    async def test_timeout_process_already_exited_no_error(self):
        """ProcessLookupError on proc.kill() is silenced."""
        proc = MagicMock()
        proc.kill = MagicMock(side_effect=ProcessLookupError)
        proc.wait = AsyncMock(return_value=None)

        async def fake_wait_for(coro, timeout):
            raise TimeoutError

        check = make_check(name="fast-exit", command="true", timeout_seconds=1)
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.wait_for",
            new=fake_wait_for,
        ):
            # Should not raise
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-timeout-exit", profile=profile
            )

        assert result.check_results[0].passed is False


# ===========================================================================
# Malformed command (shlex.split raises ValueError)
# ===========================================================================


class TestMalformedCommand:
    @pytest.mark.asyncio
    async def test_parse_error_marks_check_failed(self):
        check = make_check(name="bad-cmd", command="echo 'unclosed quote")
        profile = make_profile(checks=[check])

        # shlex.split raises ValueError for malformed input
        result = await EvaluatorOrchestrator().run_checks(
            task_id="t-parse-err", profile=profile
        )

        cr = result.check_results[0]
        assert cr.passed is False
        assert "Command parse error" in cr.stderr
        assert cr.name == "bad-cmd"
        assert cr.command == check.command

    @pytest.mark.asyncio
    async def test_parse_error_check_is_required(self):
        check = make_check(name="bad", command="echo 'unclosed quote", required=True)
        profile = make_profile(checks=[check], auto_approve_on_pass=True)

        result = await EvaluatorOrchestrator().run_checks(
            task_id="t-parse-required", profile=profile
        )

        assert result.required_passed is False
        assert result.auto_approved is False


# ===========================================================================
# Unexpected OS-level exception
# ===========================================================================


class TestUnexpectedException:
    @pytest.mark.asyncio
    async def test_file_not_found_marks_check_failed(self):
        """FileNotFoundError from create_subprocess_exec is caught."""
        check = make_check(name="missing-cmd", command="nonexistent_binary --flag")
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("No such file")),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-fnf", profile=profile
            )

        cr = result.check_results[0]
        assert cr.passed is False
        assert "No such file" in cr.stderr

    @pytest.mark.asyncio
    async def test_generic_exception_marks_check_failed(self):
        check = make_check(name="boom", command="boom")
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=RuntimeError("unexpected!")),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-exc", profile=profile
            )

        cr = result.check_results[0]
        assert cr.passed is False
        assert "unexpected!" in cr.stderr
        assert cr.stdout == ""


# ===========================================================================
# History / get_summary()
# ===========================================================================


class TestHistory:
    @pytest.mark.asyncio
    async def test_result_appended_to_history(self):
        orch = EvaluatorOrchestrator(summary_window=10)
        profile = make_profile()
        await orch.run_checks(task_id="t-hist", profile=profile)
        assert len(orch._history) == 1

    @pytest.mark.asyncio
    async def test_history_window_respected(self):
        """History deque must not grow beyond summary_window."""
        orch = EvaluatorOrchestrator(summary_window=3)
        profile = make_profile()
        for i in range(5):
            await orch.run_checks(task_id=f"t-{i}", profile=profile)
        assert len(orch._history) == 3

    @pytest.mark.asyncio
    async def test_get_summary_counts(self):
        orch = EvaluatorOrchestrator(summary_window=50)
        proc_pass = make_fake_process(returncode=0)
        proc_fail = make_fake_process(returncode=1)

        check = make_check()
        profile_auto = make_profile(checks=[check], auto_approve_on_pass=True)
        profile_no_auto = make_profile(checks=[check], auto_approve_on_pass=False)

        # 2 passing runs with auto-approve
        for _ in range(2):
            with patch(
                "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=make_fake_process(returncode=0)),
            ):
                await orch.run_checks(task_id="pass", profile=profile_auto)

        # 1 failing run (no auto-approve possible)
        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=make_fake_process(returncode=1)),
        ):
            await orch.run_checks(task_id="fail", profile=profile_no_auto)

        summary = orch.get_summary()
        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["required_passed"] == 2
        assert summary["auto_approved"] == 2
        assert summary["pass_rate"] == round(2 / 3, 4)

    def test_get_summary_pass_rate_zero_when_empty(self):
        orch = EvaluatorOrchestrator()
        assert orch.get_summary()["pass_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_get_summary_all_failed(self):
        orch = EvaluatorOrchestrator(summary_window=10)
        check = make_check(required=True)
        profile = make_profile(checks=[check], auto_approve_on_pass=True)

        for _ in range(3):
            with patch(
                "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=make_fake_process(returncode=1)),
            ):
                await orch.run_checks(task_id="fail", profile=profile)

        summary = orch.get_summary()
        assert summary["passed"] == 0
        assert summary["required_passed"] == 0
        assert summary["auto_approved"] == 0
        assert summary["pass_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_get_summary_no_checks_are_vacuously_all_passed(self):
        """Profiles with no checks should count as all_passed and required_passed."""
        orch = EvaluatorOrchestrator()
        profile = make_profile(checks=[], auto_approve_on_pass=False)
        await orch.run_checks(task_id="t-vacuous", profile=profile)

        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["required_passed"] == 1
        assert summary["auto_approved"] == 0  # auto_approve_on_pass is False


# ===========================================================================
# Singleton factory
# ===========================================================================


class TestSingletonFactory:
    def setup_method(self):
        reset_evaluator_orchestrator()

    def teardown_method(self):
        reset_evaluator_orchestrator()

    def test_returns_evaluator_orchestrator_instance(self):
        inst = get_evaluator_orchestrator()
        assert isinstance(inst, EvaluatorOrchestrator)

    def test_returns_same_instance_on_repeated_calls(self):
        a = get_evaluator_orchestrator()
        b = get_evaluator_orchestrator()
        assert a is b

    def test_reset_allows_new_instance(self):
        first = get_evaluator_orchestrator()
        reset_evaluator_orchestrator()
        second = get_evaluator_orchestrator()
        assert first is not second

    def test_summary_window_applied_on_first_call(self):
        inst = get_evaluator_orchestrator(summary_window=7)
        assert inst._history.maxlen == 7

    def test_summary_window_ignored_on_subsequent_calls(self):
        """After first creation, window arg is ignored."""
        inst_a = get_evaluator_orchestrator(summary_window=7)
        inst_b = get_evaluator_orchestrator(summary_window=999)
        assert inst_a is inst_b
        assert inst_b._history.maxlen == 7

    def test_default_summary_window(self):
        inst = get_evaluator_orchestrator()
        assert inst._history.maxlen == _SUMMARY_WINDOW


# ===========================================================================
# Duration tracking
# ===========================================================================


class TestDurationTracking:
    @pytest.mark.asyncio
    async def test_check_result_duration_non_negative(self):
        proc = make_fake_process(returncode=0)
        profile = make_profile(checks=[make_check()])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-dur", profile=profile
            )

        assert result.duration_seconds >= 0.0
        assert result.check_results[0].duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_total_duration_at_least_sum_of_checks(self):
        """Total duration >= sum of individual check durations (parallel not used)."""
        proc = make_fake_process(returncode=0)
        checks = [make_check(name=f"c-{i}", command=f"cmd-{i}") for i in range(3)]
        profile = make_profile(checks=checks)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-total-dur", profile=profile
            )

        check_sum = sum(cr.duration_seconds for cr in result.check_results)
        assert result.duration_seconds >= check_sum * 0.9  # small tolerance


# ===========================================================================
# Command shlex splitting
# ===========================================================================


class TestCommandSplitting:
    @pytest.mark.asyncio
    async def test_command_args_split_and_passed_correctly(self):
        """shlex.split should break the command string into argv list."""
        proc = make_fake_process(returncode=0)
        check = make_check(command="pytest tests/ -v --tb=short")
        profile = make_profile(checks=[check])
        captured_args: list = []

        async def fake_create(*args, **kwargs):
            captured_args.extend(args)
            return proc

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await EvaluatorOrchestrator().run_checks(task_id="t-args", profile=profile)

        assert captured_args == ["pytest", "tests/", "-v", "--tb=short"]

    @pytest.mark.asyncio
    async def test_command_with_quoted_arg(self):
        proc = make_fake_process(returncode=0)
        check = make_check(command='echo "hello world"')
        profile = make_profile(checks=[check])
        captured_args: list = []

        async def fake_create(*args, **kwargs):
            captured_args.extend(args)
            return proc

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await EvaluatorOrchestrator().run_checks(task_id="t-quoted", profile=profile)

        assert captured_args == ["echo", "hello world"]


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_required_checks_vacuously_required_passed(self):
        """All checks are optional — required_passed must still be True."""
        proc = make_fake_process(returncode=1)  # optional check fails
        check = make_check(required=False)
        profile = make_profile(checks=[check], auto_approve_on_pass=True)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-vacuous-req", profile=profile
            )

        assert result.required_passed is True
        assert result.auto_approved is True
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_check_command_stored_in_result(self):
        proc = make_fake_process(returncode=0)
        check = make_check(command="my-tool --check")
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-cmd-stored", profile=profile
            )

        assert result.check_results[0].command == "my-tool --check"

    @pytest.mark.asyncio
    async def test_check_name_preserved_in_result(self):
        proc = make_fake_process(returncode=0)
        check = make_check(name="my-special-check")
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-name", profile=profile
            )

        assert result.check_results[0].name == "my-special-check"

    @pytest.mark.asyncio
    async def test_required_flag_reflected_in_check_result(self):
        proc = make_fake_process(returncode=0)
        check = make_check(required=False)
        profile = make_profile(checks=[check])

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-req-flag", profile=profile
            )

        assert result.check_results[0].required is False

    @pytest.mark.asyncio
    async def test_mix_required_and_optional_both_fail(self):
        procs = [make_fake_process(returncode=1), make_fake_process(returncode=1)]
        call_count = 0

        async def fake_create(*args, **kwargs):
            nonlocal call_count
            p = procs[call_count]
            call_count += 1
            return p

        checks = [
            make_check(name="req", command="cmd1", required=True),
            make_check(name="opt", command="cmd2", required=False),
        ]
        profile = make_profile(checks=checks, auto_approve_on_pass=True)

        with patch(
            "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            result = await EvaluatorOrchestrator().run_checks(
                task_id="t-both-fail", profile=profile
            )

        assert result.all_passed is False
        assert result.required_passed is False
        assert result.auto_approved is False

    @pytest.mark.asyncio
    async def test_orchestrator_custom_summary_window(self):
        orch = EvaluatorOrchestrator(summary_window=5)
        assert orch._history.maxlen == 5

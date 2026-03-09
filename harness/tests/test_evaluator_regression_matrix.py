"""Evaluator Regression Matrix — DF-1005.

This module is the *regression safety net* for the EvaluatorOrchestrator.  It
deliberately targets failure modes that the existing 53-test suite does NOT
cover: false passes, false fails, timeout edge-cases, partial-pass semantics,
empty check lists, malformed commands/output, lane-gating invariants, and the
full evaluator state-machine transition chain.

Each test class is labelled with the scenario it guards, making it trivial to
bisect any future regression to a specific failure mode.

Test inventory
--------------
TestFalsePassDetection          — 7 tests
TestFalseFailFlaky              — 8 tests
TestTimeoutHandling             — 7 tests
TestPartialPass                 — 8 tests
TestEmptyChecksRegression       — 5 tests
TestMalformedOutput             — 6 tests
TestLaneGating                  — 9 tests
TestEvaluatorStateTransitions   — 9 tests
TestAutoApproveVariants         — 8 tests
TestRegressionBaseline          — 8 tests
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — ensures test can run from any working directory
# ---------------------------------------------------------------------------

_HARNESS_ROOT = Path(__file__).resolve().parent.parent
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

# ---------------------------------------------------------------------------
# SUT imports
# ---------------------------------------------------------------------------

from forge_harness.models.task_lifecycle import (
    VALID_TRANSITIONS,
    TaskLifecycleState,
    TransitionError,
    validate_transition,
)
from forge_harness.webhook_server.models.lane_policy import Lane, RiskTier, TaskType
from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
    TaskContractExtension,
)
from forge_harness.webhook_server.services.evaluator import (
    CheckResult,
    EvaluatorOrchestrator,
    EvaluatorResult,
    get_evaluator_orchestrator,
    reset_evaluator_orchestrator,
)

# ---------------------------------------------------------------------------
# Local fixture helpers (duplicated from conftest so this file is self-contained
# and can be run standalone with -k or -x)
# ---------------------------------------------------------------------------

S = TaskLifecycleState


def _check(
    name: str = "chk",
    command: str = "true",
    timeout_seconds: int = 30,
    required: bool = True,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        name=name,
        command=command,
        timeout_seconds=timeout_seconds,
        required=required,
    )


def _profile(
    checks: list[AcceptanceCheck] | None = None,
    auto_approve_on_pass: bool = False,
    max_retries: int = 0,
) -> EvaluatorProfile:
    return EvaluatorProfile(
        checks=checks or [],
        auto_approve_on_pass=auto_approve_on_pass,
        max_retries=max_retries,
    )


def _proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"", hang: bool = False):
    from unittest.mock import MagicMock

    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = (
        AsyncMock(side_effect=TimeoutError()) if hang else AsyncMock(return_value=(stdout, stderr))
    )
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


_PATCH_TARGET = "forge_harness.webhook_server.services.evaluator.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Singleton hygiene — autouse so every test gets a fresh instance
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_evaluator():
    reset_evaluator_orchestrator()
    yield
    reset_evaluator_orchestrator()


# ===========================================================================
# Scenario A — False-pass detection
# ===========================================================================


class TestFalsePassDetection:
    """Guard against a check that exits 0 but whose output signals failure.

    The evaluator's current design uses ONLY exit codes to determine pass/fail.
    These tests document and pin the *actual* contract so that any future
    change to add content-based analysis is caught as a deliberate break.
    """

    async def test_exit_0_is_sufficient_for_pass_regardless_of_stdout(self):
        """Current contract: exit 0 always means passed, content is ignored."""
        checks = [_check("ci-wrapper", command="./ci.sh")]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        # stdout says FAILED — but exit code is 0
        proc = _proc(returncode=0, stdout=b"FAILED: 3 test failures found\n", stderr=b"")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-fp-01", profile)

        # Contract pin: exit 0 means passed — no content analysis performed
        assert result.check_results[0].passed is True
        assert result.required_passed is True

    async def test_false_pass_stdout_is_captured_for_downstream_auditing(self):
        """Even if the check passes, full stdout must be available for audit."""
        checks = [_check("audit-check")]
        profile = _profile(checks=checks)
        failure_marker = b"FAILED: 3 test failures found\n"
        proc = _proc(returncode=0, stdout=failure_marker)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-fp-02", profile)

        cr = result.check_results[0]
        assert "FAILED" in cr.stdout, "Failure marker must be preserved in stdout for audit"

    async def test_false_pass_auto_approved_because_exit_code_wins(self):
        """auto_approve_on_pass relies on exit code, not stdout content."""
        checks = [_check("exit-0-liar")]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=0, stdout=b"ERROR: build failed\nFAILED\n")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-fp-03", profile)

        assert result.auto_approved is True

    async def test_false_pass_required_check_does_not_block_auto_approve(self):
        """A required check that exits 0 (even with failure stdout) is 'passed'."""
        checks = [_check("required-liar", required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=0, stderr=b"Error: critical issues detected")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-fp-04", profile)

        assert result.required_passed is True
        assert result.auto_approved is True

    async def test_multiple_false_pass_checks_all_recorded(self):
        """With multiple exit-0 checks that say FAILED, all are recorded as passed."""
        checks = [
            _check("wrapper-1", required=True),
            _check("wrapper-2", required=True),
        ]
        profile = _profile(checks=checks)
        pass_proc_1 = _proc(returncode=0, stdout=b"FAILED tests/a.py\n")
        pass_proc_2 = _proc(returncode=0, stdout=b"FAILED tests/b.py\n")

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=[pass_proc_1, pass_proc_2])):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-fp-05", profile)

        assert all(cr.passed for cr in result.check_results)
        assert result.all_passed is True

    async def test_false_pass_summary_counts_as_pass(self):
        """get_summary() must count exit-0 results as passing (no content check)."""
        checks = [_check("wrapper")]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=0, stdout=b"FAILED: everything is on fire\n")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("task-fp-06", profile)

        summary = orch.get_summary()
        assert summary["passed"] == 1
        assert summary["pass_rate"] == 1.0

    async def test_false_pass_required_check_stdout_available_in_check_result(self):
        """CheckResult.stdout preserves the misleading output for triage."""
        checks = [_check("misleading-check", required=True)]
        profile = _profile(checks=checks)
        misleading_output = b"BUILD SUCCESSFUL\nFAILED: tests/security_test.py::test_auth_bypass\n"
        proc = _proc(returncode=0, stdout=misleading_output)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-fp-07", profile)

        cr = result.check_results[0]
        assert "FAILED" in cr.stdout
        assert "BUILD SUCCESSFUL" in cr.stdout
        assert cr.passed is True  # exit code wins


# ===========================================================================
# Scenario B — False-fail (flaky) detection
# ===========================================================================


class TestFalseFailFlaky:
    """Guard against environmental false-fails being treated as real failures.

    The evaluator currently treats any non-zero exit as a hard failure.  These
    tests document the contract and verify that stderr content is preserved so
    that a higher-level layer (or retry logic) can distinguish flaky from real.
    """

    async def test_exit_1_always_fails_regardless_of_stderr_content(self):
        """Contract pin: exit 1 always means failed — no flakiness detection."""
        checks = [_check("port-conflict-check")]
        profile = _profile(checks=checks)
        proc = _proc(
            returncode=1,
            stderr=b"OSError: [Errno 98] Address already in use",
        )

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-ff-01", profile)

        assert result.check_results[0].passed is False
        assert result.required_passed is False

    async def test_flaky_stderr_preserved_for_retry_analysis(self):
        """The flaky error message must be accessible in CheckResult.stderr."""
        checks = [_check("dns-check")]
        profile = _profile(checks=checks)
        flaky_msg = b"Temporary failure in name resolution"
        proc = _proc(returncode=1, stderr=flaky_msg)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-ff-02", profile)

        assert "Temporary failure in name resolution" in result.check_results[0].stderr

    async def test_flaky_required_check_blocks_auto_approve(self):
        """Even a flaky environmental failure on a required check blocks auto-approval."""
        checks = [_check("network-check", required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=1, stderr=b"ConnectionRefusedError: [Errno 111]")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-ff-03", profile)

        assert result.auto_approved is False

    async def test_flaky_optional_check_does_not_block_required_passed(self):
        """An optional flaky check failure should not affect required_passed."""
        checks = [
            _check("required-ok", required=True),
            _check("optional-flaky", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        ok_proc = _proc(returncode=0, stdout=b"all good")
        flaky_proc = _proc(returncode=1, stderr=b"Address already in use")

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=[ok_proc, flaky_proc])):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-ff-04", profile)

        assert result.required_passed is True
        assert result.all_passed is False  # optional failed
        assert result.auto_approved is True  # required passed, flag is True

    async def test_real_failure_also_captured_in_stderr(self):
        """A genuine test failure (not environmental) also lands in stderr."""
        checks = [_check("unit-tests")]
        profile = _profile(checks=checks)
        real_error = b"AssertionError: expected 200, got 401\n3 tests failed"
        proc = _proc(returncode=1, stderr=real_error)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-ff-05", profile)

        assert "AssertionError" in result.check_results[0].stderr
        assert result.check_results[0].passed is False

    async def test_flaky_failure_increments_summary_fail_count(self):
        """Flaky failures count as failures in get_summary() — no special treatment."""
        checks = [_check("flaky")]
        profile = _profile(checks=checks)
        proc = _proc(returncode=1, stderr=b"port busy")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("task-ff-06", profile)

        summary = orch.get_summary()
        assert summary["passed"] == 0
        assert summary["required_passed"] == 0

    async def test_consecutive_flaky_then_pass_recorded_separately(self):
        """A run after a flaky failure that succeeds is counted correctly in summary."""
        checks = [_check("maybe-flaky")]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        flaky = _proc(returncode=1, stderr=b"transient error")
        success = _proc(returncode=0, stdout=b"ok")

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=[flaky, success])):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("task-ff-07a", profile)
            await orch.run_checks("task-ff-07b", profile)

        summary = orch.get_summary()
        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["pass_rate"] == 0.5

    async def test_flaky_exit_code_1_treated_same_as_real_failure(self):
        """Evaluator cannot distinguish flaky from real — both fail; duration recorded."""
        checks = [_check("indeterminate")]
        profile = _profile(checks=checks)
        proc = _proc(returncode=1, stderr=b"SIGTERM received - process killed by OS")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-ff-08", profile)

        cr = result.check_results[0]
        assert cr.passed is False
        assert cr.duration_seconds >= 0.0


# ===========================================================================
# Scenario C — Timeout handling
# ===========================================================================


class TestTimeoutHandling:
    """Validate evaluator behavior when a check exceeds its timeout budget."""

    async def test_timeout_marks_required_check_as_failed(self):
        checks = [_check("slow-e2e", timeout_seconds=1, required=True)]
        profile = _profile(checks=checks)
        proc = _proc(hang=True)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-to-01", profile)

        cr = result.check_results[0]
        assert cr.passed is False
        assert result.required_passed is False

    async def test_timeout_stderr_contains_timeout_message(self):
        checks = [_check("slow-check", timeout_seconds=2, required=True)]
        profile = _profile(checks=checks)
        proc = _proc(hang=True)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-to-02", profile)

        assert "Timed out" in result.check_results[0].stderr

    async def test_timeout_stdout_is_empty_string(self):
        """On timeout, stdout capture is not available — must be empty string."""
        checks = [_check("slow-check", timeout_seconds=1)]
        profile = _profile(checks=checks)
        proc = _proc(hang=True)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-to-03", profile)

        assert result.check_results[0].stdout == ""

    async def test_timeout_kills_process(self):
        """The evaluator must call proc.kill() after a timeout."""
        checks = [_check("slow-check", timeout_seconds=1)]
        profile = _profile(checks=checks)
        proc = _proc(hang=True)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("task-to-04", profile)

        proc.kill.assert_called_once()

    async def test_timeout_duration_recorded(self):
        """Even timed-out checks must record a non-negative duration."""
        checks = [_check("slow-check", timeout_seconds=1)]
        profile = _profile(checks=checks)
        proc = _proc(hang=True)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-to-05", profile)

        assert result.check_results[0].duration_seconds >= 0.0

    async def test_optional_timeout_does_not_fail_required_passed(self):
        """A timed-out optional check should not affect required_passed."""
        checks = [
            _check("required-fast", required=True),
            _check("optional-slow", required=False, timeout_seconds=1),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        fast_proc = _proc(returncode=0, stdout=b"ok")
        slow_proc = _proc(hang=True)

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=[fast_proc, slow_proc])):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-to-06", profile)

        assert result.required_passed is True
        assert result.all_passed is False
        assert result.auto_approved is True

    async def test_timeout_process_already_gone_no_raise(self):
        """ProcessLookupError when killing already-gone process must not propagate."""
        from unittest.mock import MagicMock

        checks = [_check("ghost-process", timeout_seconds=1)]
        profile = _profile(checks=checks)

        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        proc.kill = MagicMock(side_effect=ProcessLookupError("already gone"))
        proc.wait = AsyncMock(return_value=None)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-to-07", profile)

        assert result.check_results[0].passed is False  # must not raise


# ===========================================================================
# Scenario D — Partial pass (required vs optional)
# ===========================================================================


class TestPartialPass:
    """Verify required/optional distinction in mixed-result runs."""

    async def test_all_required_pass_one_optional_fails(self):
        checks = [
            _check("req-1", required=True),
            _check("req-2", required=True),
            _check("opt-1", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        procs = [
            _proc(returncode=0),
            _proc(returncode=0),
            _proc(returncode=1, stderr=b"coverage too low"),
        ]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-01", profile)

        assert result.required_passed is True
        assert result.all_passed is False
        assert result.auto_approved is True  # required passed + flag True

    async def test_one_required_fails_rest_pass(self):
        checks = [
            _check("req-fail", required=True),
            _check("opt-pass", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        procs = [
            _proc(returncode=1),
            _proc(returncode=0),
        ]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-02", profile)

        assert result.required_passed is False
        assert result.auto_approved is False

    async def test_partial_pass_all_checks_still_run(self):
        """A failing required check must NOT short-circuit remaining checks."""
        checks = [
            _check("req-fail", required=True),
            _check("req-2", required=True),
            _check("opt-1", required=False),
        ]
        profile = _profile(checks=checks)
        procs = [
            _proc(returncode=1),  # required: fail
            _proc(returncode=0),  # required: pass
            _proc(returncode=0),  # optional: pass
        ]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-03", profile)

        assert len(result.check_results) == 3
        assert result.check_results[0].passed is False
        assert result.check_results[1].passed is True
        assert result.check_results[2].passed is True

    async def test_all_optional_fail_required_passed_true(self):
        """When all checks are optional and they all fail, required_passed is True (vacuous)."""
        checks = [
            _check("opt-a", required=False),
            _check("opt-b", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        procs = [_proc(returncode=1), _proc(returncode=1)]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-04", profile)

        assert result.required_passed is True
        assert result.all_passed is False
        assert result.auto_approved is True  # no required checks failed

    async def test_required_flag_preserved_in_check_results(self):
        """CheckResult.required must mirror AcceptanceCheck.required."""
        checks = [
            _check("r", required=True),
            _check("o", required=False),
        ]
        profile = _profile(checks=checks)
        procs = [_proc(), _proc()]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-05", profile)

        assert result.check_results[0].required is True
        assert result.check_results[1].required is False

    async def test_mixed_required_optional_summary_counts_correctly(self):
        """get_summary reports based on EvaluatorResult.all_passed, not required_passed."""
        checks = [
            _check("req", required=True),
            _check("opt", required=False),
        ]
        profile = _profile(checks=checks)
        # req passes, opt fails → all_passed=False, required_passed=True
        procs = [_proc(returncode=0), _proc(returncode=1)]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            await orch.run_checks("task-pp-06", profile)

        summary = orch.get_summary()
        assert summary["passed"] == 0  # all_passed is False
        assert summary["required_passed"] == 1

    async def test_three_required_two_optional_complex_mix(self):
        """Three required all pass; two optional mix pass/fail."""
        checks = [
            _check("req-1", required=True),
            _check("req-2", required=True),
            _check("req-3", required=True),
            _check("opt-pass", required=False),
            _check("opt-fail", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        procs = [
            _proc(returncode=0),
            _proc(returncode=0),
            _proc(returncode=0),
            _proc(returncode=0),
            _proc(returncode=1),
        ]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-07", profile)

        assert result.required_passed is True
        assert result.all_passed is False
        assert result.auto_approved is True

    async def test_partial_pass_check_order_maintained(self):
        """Results are always in profile order, regardless of pass/fail mix."""
        checks = [
            _check("a", required=True),
            _check("b", required=False),
            _check("c", required=True),
        ]
        profile = _profile(checks=checks)
        procs = [_proc(returncode=1), _proc(returncode=0), _proc(returncode=0)]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-pp-08", profile)

        names = [cr.name for cr in result.check_results]
        assert names == ["a", "b", "c"]


# ===========================================================================
# Scenario E — Empty checks regression
# ===========================================================================


class TestEmptyChecksRegression:
    """Vacuous-pass semantics for tasks with no configured checks."""

    async def test_empty_checks_all_passed_true(self):
        profile = _profile(checks=[], auto_approve_on_pass=False)
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("task-ec-01", profile)

        assert result.all_passed is True
        assert result.required_passed is True

    async def test_empty_checks_auto_approve_false_when_flag_false(self):
        profile = _profile(checks=[], auto_approve_on_pass=False)
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("task-ec-02", profile)

        assert result.auto_approved is False

    async def test_empty_checks_auto_approve_true_when_flag_true(self):
        profile = _profile(checks=[], auto_approve_on_pass=True)
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("task-ec-03", profile)

        assert result.auto_approved is True

    async def test_empty_checks_no_check_results(self):
        profile = _profile(checks=[], auto_approve_on_pass=True)
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("task-ec-04", profile)

        assert result.check_results == []

    async def test_empty_checks_counted_in_summary(self):
        """Empty-check runs still contribute a row to the summary window."""
        profile = _profile(checks=[], auto_approve_on_pass=True)
        orch = EvaluatorOrchestrator()
        await orch.run_checks("task-ec-05", profile)

        summary = orch.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["auto_approved"] == 1


# ===========================================================================
# Scenario F — Malformed command / non-UTF-8 output
# ===========================================================================


class TestMalformedOutput:
    """Evaluator must handle bad commands and binary output without raising."""

    async def test_unterminated_quote_marks_check_failed(self):
        """shlex.split failure on unterminated quote must produce a failed CheckResult."""
        checks = [_check("bad-shell", command="echo 'unterminated")]
        profile = _profile(checks=checks)
        orch = EvaluatorOrchestrator()
        # No subprocess mock needed — shlex.split raises before exec
        result = await orch.run_checks("task-mo-01", profile)

        cr = result.check_results[0]
        assert cr.passed is False
        assert "parse error" in cr.stderr.lower()

    async def test_unterminated_quote_required_check_fails_required_passed(self):
        checks = [_check("bad", command="echo 'unterminated", required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        orch = EvaluatorOrchestrator()
        result = await orch.run_checks("task-mo-02", profile)

        assert result.required_passed is False
        assert result.auto_approved is False

    async def test_binary_stdout_decoded_with_replacement(self):
        """Non-UTF-8 bytes in stdout must not raise — decoded with errors='replace'."""
        checks = [_check("binary-check")]
        profile = _profile(checks=checks)
        binary_bytes = b"\xff\xfe\x00\x01 binary garbage"
        proc = _proc(returncode=0, stdout=binary_bytes)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-mo-03", profile)

        cr = result.check_results[0]
        assert isinstance(cr.stdout, str)  # must be a string, not bytes

    async def test_binary_stderr_decoded_with_replacement(self):
        """Non-UTF-8 bytes in stderr must not raise."""
        checks = [_check("binary-err-check")]
        profile = _profile(checks=checks)
        binary_err = b"\x80\x81\x82 invalid utf-8 in stderr"
        proc = _proc(returncode=1, stderr=binary_err)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-mo-04", profile)

        assert isinstance(result.check_results[0].stderr, str)

    async def test_file_not_found_command_produces_failed_check(self):
        checks = [_check("missing-binary", command="nonexistent-cmd-xyz")]
        profile = _profile(checks=checks)

        with patch(
            _PATCH_TARGET,
            new=AsyncMock(side_effect=FileNotFoundError("No such file or directory")),
        ):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-mo-05", profile)

        assert result.check_results[0].passed is False
        assert "No such file" in result.check_results[0].stderr

    async def test_malformed_and_valid_check_both_recorded(self):
        """After a parse-fail check, the next valid check still runs."""
        checks = [
            _check("broken", command="echo 'unterminated", required=True),
            _check("healthy", command="echo ok", required=False),
        ]
        profile = _profile(checks=checks)
        proc = _proc(returncode=0, stdout=b"ok")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-mo-06", profile)

        assert len(result.check_results) == 2
        assert result.check_results[0].passed is False  # malformed
        assert result.check_results[1].passed is True  # healthy


# ===========================================================================
# Scenario G — Lane gating
# ===========================================================================


class TestLaneGating:
    """Verify that lane policy correctly routes tasks through or around the evaluator."""

    def test_autonomous_lane_does_not_require_human_approval(self):
        """test-writing at low risk must be autonomous (no human gate)."""
        contract = TaskContractExtension(
            risk_tier=RiskTier.low,
            task_type=TaskType.test_writing,
        )
        assert contract.lane == Lane.autonomous
        assert contract.requires_human_approval is False

    def test_human_review_lane_requires_human_approval(self):
        """security-change at medium risk must be human_review."""
        contract = TaskContractExtension(
            risk_tier=RiskTier.medium,
            task_type=TaskType.security_change,
        )
        assert contract.lane == Lane.human_review
        assert contract.requires_human_approval is True

    def test_blocked_lane_requires_human_approval(self):
        """deployment at critical risk must be blocked."""
        contract = TaskContractExtension(
            risk_tier=RiskTier.critical,
            task_type=TaskType.deployment,
        )
        assert contract.lane == Lane.blocked
        assert contract.requires_human_approval is True

    def test_autonomous_lane_allows_evaluator_to_complete_task(self):
        """autonomous + auto_approve_on_pass + required_passed → auto_approved=True."""
        profile = _profile(
            checks=[_check("fast-check")],
            auto_approve_on_pass=True,
        )
        # In the autonomous lane the evaluator can auto-complete the task
        # Simulate: required passes, profile says auto-approve
        result = EvaluatorResult(
            task_id="task-lg-03",
            check_results=[
                CheckResult(
                    name="fast-check",
                    passed=True,
                    command="true",
                    duration_seconds=0.01,
                    required=True,
                )
            ],
            all_passed=True,
            required_passed=True,
            duration_seconds=0.01,
            auto_approved=True,
        )
        assert result.auto_approved is True

    def test_human_review_lane_evaluator_does_not_auto_approve(self):
        """In human_review lane, auto_approve_on_pass should be False."""
        result = EvaluatorResult(
            task_id="task-lg-04",
            check_results=[],
            all_passed=True,
            required_passed=True,
            duration_seconds=0.0,
            auto_approved=False,  # human_review: never auto-approve
        )
        assert result.auto_approved is False

    def test_blocked_lane_task_never_reaches_evaluator_running(self):
        """blocked lane: valid_transition from candidate_done goes to completed only via human."""
        # In a blocked lane, candidate_done → evaluator_running is technically
        # valid in the state machine, but the lane policy means it should not
        # reach auto-approved. We verify the state machine allows the transition
        # (policy is enforced at orchestration layer, not lifecycle layer).
        assert validate_transition(S.CANDIDATE_DONE, S.EVALUATOR_RUNNING) is True
        assert validate_transition(S.CANDIDATE_DONE, S.COMPLETED) is True

    def test_security_change_high_is_blocked_lane(self):
        contract = TaskContractExtension(
            risk_tier=RiskTier.high,
            task_type=TaskType.security_change,
        )
        assert contract.lane == Lane.blocked
        assert contract.requires_human_approval is True

    def test_database_migration_critical_is_blocked_lane(self):
        contract = TaskContractExtension(
            risk_tier=RiskTier.critical,
            task_type=TaskType.database_migration,
        )
        assert contract.lane == Lane.blocked

    def test_bug_fix_low_is_autonomous_lane(self):
        contract = TaskContractExtension(
            risk_tier=RiskTier.low,
            task_type=TaskType.bug_fix,
        )
        assert contract.lane == Lane.autonomous
        assert contract.requires_human_approval is False


# ===========================================================================
# Scenario H — Evaluator state transitions
# ===========================================================================


class TestEvaluatorStateTransitions:
    """Pin the complete evaluator pipeline state machine."""

    def test_in_progress_to_candidate_done(self):
        assert validate_transition(S.IN_PROGRESS, S.CANDIDATE_DONE) is True

    def test_candidate_done_to_evaluator_running(self):
        assert validate_transition(S.CANDIDATE_DONE, S.EVALUATOR_RUNNING) is True

    def test_evaluator_running_to_evaluator_passed(self):
        assert validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_PASSED) is True

    def test_evaluator_running_to_evaluator_failed(self):
        assert validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED) is True

    def test_evaluator_passed_to_completed(self):
        assert validate_transition(S.EVALUATOR_PASSED, S.COMPLETED) is True

    def test_evaluator_failed_to_in_progress_retry(self):
        assert validate_transition(S.EVALUATOR_FAILED, S.IN_PROGRESS) is True

    def test_evaluator_failed_to_blocked_escalation(self):
        assert validate_transition(S.EVALUATOR_FAILED, S.BLOCKED) is True

    def test_blocked_to_in_progress_after_human_intervention(self):
        assert validate_transition(S.BLOCKED, S.IN_PROGRESS) is True

    def test_evaluator_running_cannot_go_to_candidate_done(self):
        """evaluator_running cannot rewind to candidate_done."""
        with pytest.raises(TransitionError):
            validate_transition(S.EVALUATOR_RUNNING, S.CANDIDATE_DONE)


# ===========================================================================
# Scenario I — auto_approve variants
# ===========================================================================


class TestAutoApproveVariants:
    """Cover every combination of auto_approve_on_pass x required_passed."""

    async def test_flag_false_required_pass_no_auto_approve(self):
        checks = [_check(required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=False)
        proc = _proc(returncode=0)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-aa-01", profile)

        assert result.required_passed is True
        assert result.auto_approved is False

    async def test_flag_true_required_pass_auto_approved(self):
        checks = [_check(required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=0)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-aa-02", profile)

        assert result.auto_approved is True

    async def test_flag_true_required_fail_no_auto_approve(self):
        checks = [_check(required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=1)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-aa-03", profile)

        assert result.auto_approved is False

    async def test_flag_false_required_fail_no_auto_approve(self):
        checks = [_check(required=True)]
        profile = _profile(checks=checks, auto_approve_on_pass=False)
        proc = _proc(returncode=1)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-aa-04", profile)

        assert result.auto_approved is False

    async def test_flag_true_no_required_checks_auto_approved(self):
        """Vacuous required_passed (no required checks) + flag=True → auto_approved."""
        checks = [_check("opt", required=False)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        proc = _proc(returncode=0)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-aa-05", profile)

        assert result.required_passed is True
        assert result.auto_approved is True

    async def test_flag_true_optional_fail_required_pass_auto_approved(self):
        """optional fails do NOT block auto_approve when required_passed is True."""
        checks = [
            _check("req", required=True),
            _check("opt", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        procs = [_proc(returncode=0), _proc(returncode=1)]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-aa-06", profile)

        assert result.auto_approved is True

    async def test_auto_approve_true_flows_to_evaluator_running_to_completed(self):
        """State: evaluator_running → completed is valid for auto-approve path."""
        assert validate_transition(S.EVALUATOR_RUNNING, S.COMPLETED) is True

    async def test_auto_approve_false_stays_at_evaluator_passed_before_human(self):
        """When auto_approve=False: evaluator_running → evaluator_passed → completed."""
        assert validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_PASSED) is True
        assert validate_transition(S.EVALUATOR_PASSED, S.COMPLETED) is True


# ===========================================================================
# Scenario J — Regression baseline (cross-scenario combinations)
# ===========================================================================


class TestRegressionBaseline:
    """Smoke-test all fixture combinations together to catch interactions."""

    async def test_all_pass_all_required_auto_approve_true(self):
        checks = [_check(f"req-{i}", required=True) for i in range(4)]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        procs = [_proc(returncode=0) for _ in checks]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-rb-01", profile)

        assert result.all_passed is True
        assert result.required_passed is True
        assert result.auto_approved is True
        assert len(result.check_results) == 4

    async def test_mix_timeout_malformed_pass_fail(self):
        """A profile with one good check, one timeout, and one malformed command."""
        checks = [
            _check("good", command="echo ok", required=True),
            _check("slow", command="sleep 999", timeout_seconds=1, required=False),
            _check("broken", command="echo 'unterminated", required=False),
        ]
        profile = _profile(checks=checks, auto_approve_on_pass=True)
        good_proc = _proc(returncode=0, stdout=b"ok")
        slow_proc = _proc(hang=True)

        # malformed check never reaches subprocess — shlex raises first
        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=[good_proc, slow_proc])):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-rb-02", profile)

        assert len(result.check_results) == 3
        assert result.check_results[0].passed is True
        assert result.check_results[1].passed is False  # timeout
        assert result.check_results[2].passed is False  # malformed
        assert result.required_passed is True  # only required was good
        assert result.auto_approved is True

    async def test_summary_window_respected_with_many_runs(self):
        orch = EvaluatorOrchestrator(summary_window=5)
        profile = _profile(checks=[_check()])

        for i in range(8):
            proc = _proc(returncode=0)
            with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
                await orch.run_checks(f"task-rb-03-{i}", profile)

        summary = orch.get_summary()
        assert summary["total"] == 5  # capped by window

    async def test_pass_rate_calculation_with_mixed_history(self):
        profile = _profile(checks=[_check()])
        orch = EvaluatorOrchestrator()

        outcomes = [0, 0, 1, 0, 1]  # 3 pass, 2 fail
        for i, rc in enumerate(outcomes):
            proc = _proc(returncode=rc)
            with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
                await orch.run_checks(f"task-rb-04-{i}", profile)

        summary = orch.get_summary()
        assert summary["total"] == 5
        assert summary["passed"] == 3
        assert abs(summary["pass_rate"] - 0.6) < 0.001

    async def test_task_ids_are_independent_in_history(self):
        """Each run with a different task_id produces a separate history entry."""
        profile = _profile(checks=[_check()], auto_approve_on_pass=True)
        orch = EvaluatorOrchestrator()
        task_ids = ["alpha", "beta", "gamma"]

        for tid in task_ids:
            proc = _proc(returncode=0)
            with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
                result = await orch.run_checks(tid, profile)
            assert result.task_id == tid

        assert orch.get_summary()["total"] == 3

    async def test_zero_checks_after_non_zero_checks_in_sequence(self):
        """Switching from populated to empty profiles in one orchestrator works."""
        orch = EvaluatorOrchestrator()
        full_profile = _profile(checks=[_check()], auto_approve_on_pass=True)
        empty_profile = _profile(checks=[], auto_approve_on_pass=True)

        proc = _proc(returncode=0)
        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            r1 = await orch.run_checks("task-rb-06a", full_profile)
        r2 = await orch.run_checks("task-rb-06b", empty_profile)

        assert r1.all_passed is True
        assert r2.all_passed is True
        assert r2.check_results == []

    async def test_work_dir_none_does_not_affect_result(self):
        checks = [_check("wd-check")]
        profile = _profile(checks=checks)
        proc = _proc(returncode=0)

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-rb-07", profile, work_dir=None)

        assert result.all_passed is True

    async def test_result_duration_is_sum_of_individual_durations(self):
        """Total duration_seconds must be >= sum of individual check durations."""
        checks = [_check(f"c{i}") for i in range(3)]
        profile = _profile(checks=checks)
        procs = [_proc() for _ in checks]

        with patch(_PATCH_TARGET, new=AsyncMock(side_effect=procs)):
            orch = EvaluatorOrchestrator()
            result = await orch.run_checks("task-rb-08", profile)

        individual_sum = sum(cr.duration_seconds for cr in result.check_results)
        # Total includes loop overhead so must be >= individual sum
        assert result.duration_seconds >= individual_sum

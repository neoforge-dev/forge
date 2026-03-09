"""Shared fixtures for the evaluator regression matrix.

Every fixture here captures one failure mode that is *not* covered by the
existing 53-test suite:

Scenario A  — False-pass
    A check command exits 0 but its stdout signals failure.  The evaluator
    must not silently promote a task whose output content indicates errors.

Scenario B  — False-fail (flaky)
    A check exits 1 due to a transient environmental problem, not real code
    failure.  Distinguishable because stderr contains a known flaky marker
    ("address already in use", "temporary failure resolving", etc.).

Scenario C  — Timeout
    A check hangs indefinitely.  The evaluator must kill it within the
    configured timeout_seconds and record a failure.

Scenario D  — Partial pass
    A mix of required and optional checks where required ones pass and
    optional ones fail.  required_passed should be True; all_passed False.

Scenario E  — Empty checks
    No checks configured.  all_passed and required_passed are both vacuously
    True; auto_approved depends solely on the profile flag.

Scenario F  — Malformed command / output
    A check whose command string contains unterminated quotes (shlex.split
    raises ValueError) or that emits binary/non-UTF-8 bytes.

Scenario G  — Lane gating
    Autonomous lane (task_type=test_writing, risk_tier=low) does NOT require
    human approval.  Human-review lane (task_type=security_change,
    risk_tier=medium) DOES.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.models.task_lifecycle import TaskLifecycleState
from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
)
from forge_harness.webhook_server.services.evaluator import (
    EvaluatorOrchestrator,
    reset_evaluator_orchestrator,
)

# ---------------------------------------------------------------------------
# Singleton hygiene — reset before/after every test in this package
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_evaluator_singleton():
    reset_evaluator_orchestrator()
    yield
    reset_evaluator_orchestrator()


# ---------------------------------------------------------------------------
# Generic subprocess mock helpers
# ---------------------------------------------------------------------------


def make_proc(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    hang: bool = False,
) -> MagicMock:
    """Build a mock subprocess object for asyncio.create_subprocess_exec.

    When *hang* is True the communicate() coroutine raises TimeoutError to
    simulate a process that never exits within its timeout window.
    """
    proc = MagicMock()
    proc.returncode = returncode
    if hang:
        proc.communicate = AsyncMock(side_effect=TimeoutError())
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


def make_check(
    name: str = "check",
    command: str = "true",
    timeout_seconds: int = 30,
    required: bool = True,
) -> AcceptanceCheck:
    """Construct an AcceptanceCheck with sensible defaults."""
    return AcceptanceCheck(
        name=name,
        command=command,
        timeout_seconds=timeout_seconds,
        required=required,
    )


def make_profile(
    checks: list[AcceptanceCheck] | None = None,
    auto_approve_on_pass: bool = False,
    max_retries: int = 0,
) -> EvaluatorProfile:
    """Construct an EvaluatorProfile with sensible defaults."""
    return EvaluatorProfile(
        checks=checks or [],
        auto_approve_on_pass=auto_approve_on_pass,
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# Scenario A — False-pass fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def false_pass_check():
    """A check that exits 0 but whose stdout contains failure markers.

    This simulates a buggy wrapper script that always exits 0 regardless of
    the underlying tool's exit code (a common CI anti-pattern).
    """
    return make_check(
        name="false-pass-check",
        command="echo 'FAILED: 3 test failures found'",
        required=True,
    )


@pytest.fixture
def false_pass_proc():
    """Subprocess mock: exit 0 but stdout says FAILED."""
    return make_proc(
        returncode=0,
        stdout=b"FAILED: 3 test failures found\n",
        stderr=b"",
    )


@pytest.fixture
def false_pass_profile(false_pass_check):
    """Profile with a single false-pass check, auto_approve enabled."""
    return make_profile(checks=[false_pass_check], auto_approve_on_pass=True)


# ---------------------------------------------------------------------------
# Scenario B — False-fail (flaky) fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flaky_check():
    """A check that exits 1 due to a transient port-in-use error."""
    return make_check(
        name="flaky-port-check",
        command="pytest --port=8080",
        required=True,
    )


@pytest.fixture
def flaky_proc():
    """Subprocess mock: exit 1 with a transient environment error in stderr."""
    return make_proc(
        returncode=1,
        stdout=b"",
        stderr=b"OSError: [Errno 98] Address already in use: port 8080",
    )


@pytest.fixture
def non_flaky_proc():
    """Subprocess mock: exit 1 with a real test failure (not environmental)."""
    return make_proc(
        returncode=1,
        stdout=b"FAILED tests/test_auth.py::test_login\n",
        stderr=b"AssertionError: expected 200, got 401",
    )


# ---------------------------------------------------------------------------
# Scenario C — Timeout fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def timeout_check():
    """A check with a very short timeout to trigger the timeout path."""
    return make_check(
        name="slow-integration-test",
        command="sleep 999",
        timeout_seconds=1,
        required=True,
    )


@pytest.fixture
def hanging_proc():
    """Subprocess mock that simulates a process that never exits."""
    return make_proc(hang=True)


# ---------------------------------------------------------------------------
# Scenario D — Partial-pass fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def partial_pass_checks():
    """Two required checks pass, one optional check fails."""
    return [
        make_check("required-unit-tests", required=True),
        make_check("required-type-check", required=True),
        make_check("optional-coverage-gate", required=False),
    ]


@pytest.fixture
def partial_pass_procs():
    """Three procs: pass, pass, fail — matching partial_pass_checks order."""
    return [
        make_proc(returncode=0, stdout=b"All tests passed"),
        make_proc(returncode=0, stdout=b"No type errors"),
        make_proc(returncode=1, stderr=b"Coverage 72% < threshold 80%"),
    ]


# ---------------------------------------------------------------------------
# Scenario E — Empty checks fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_profile_auto_approve():
    """Profile with zero checks and auto_approve_on_pass=True."""
    return make_profile(checks=[], auto_approve_on_pass=True)


@pytest.fixture
def empty_profile_no_auto():
    """Profile with zero checks and auto_approve_on_pass=False."""
    return make_profile(checks=[], auto_approve_on_pass=False)


# ---------------------------------------------------------------------------
# Scenario F — Malformed command / binary output fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def malformed_command_check():
    """A check whose command has an unterminated shell quote."""
    return make_check(
        name="malformed-cmd",
        command="echo 'unterminated",
        required=True,
    )


@pytest.fixture
def binary_output_proc():
    """Subprocess mock that emits non-UTF-8 binary bytes in stdout/stderr."""
    return make_proc(
        returncode=0,
        stdout=b"\xff\xfe binary garbage \x00\x01\x02",
        stderr=b"\x80\x81\x82 more binary",
    )


# ---------------------------------------------------------------------------
# Scenario G — Lane policy fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def autonomous_lane_contract():
    """TaskContractExtension for an autonomous lane task (test-writing, low)."""
    from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
    from forge_harness.webhook_server.models.task_contract import TaskContractExtension

    return TaskContractExtension(
        risk_tier=RiskTier.low,
        task_type=TaskType.test_writing,
    )


@pytest.fixture
def human_review_lane_contract():
    """TaskContractExtension for a human-review lane (security-change, medium)."""
    from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
    from forge_harness.webhook_server.models.task_contract import TaskContractExtension

    return TaskContractExtension(
        risk_tier=RiskTier.medium,
        task_type=TaskType.security_change,
    )


@pytest.fixture
def blocked_lane_contract():
    """TaskContractExtension for a blocked lane (deployment, critical)."""
    from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
    from forge_harness.webhook_server.models.task_contract import TaskContractExtension

    return TaskContractExtension(
        risk_tier=RiskTier.critical,
        task_type=TaskType.deployment,
    )


# ---------------------------------------------------------------------------
# Re-export convenience symbols for test modules in this package
# ---------------------------------------------------------------------------


__all__ = [
    "make_proc",
    "make_check",
    "make_profile",
    "TaskLifecycleState",
    "EvaluatorOrchestrator",
]

"""Evaluator Orchestrator Service
==================================

Runs acceptance checks defined in an :class:`EvaluatorProfile` against task
outputs.  Each check is executed as a subprocess command; the results are
aggregated into an :class:`EvaluatorResult` that records per-check outcomes
and the overall pass/fail verdict.

Usage
-----
Inject the singleton::

    from forge_harness.webhook_server.services.evaluator import (
        get_evaluator_orchestrator,
    )

    evaluator = get_evaluator_orchestrator()
    result = await evaluator.run_checks(
        task_id="task-123",
        profile=profile,
        work_dir="/path/to/work",
    )
    if result.auto_approved:
        # promote task to completed state automatically
        ...

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-1003)
harness/forge_harness/webhook_server/models/task_contract.py
harness/forge_harness/webhook_server/models/task_lifecycle.py
"""

from __future__ import annotations

import asyncio
import shlex
import time
from collections import deque
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Maximum number of past results kept for get_summary()
# ---------------------------------------------------------------------------
_SUMMARY_WINDOW = 100


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Outcome of a single :class:`AcceptanceCheck` execution.

    Attributes:
        name:             Check label (mirrors :attr:`AcceptanceCheck.name`).
        passed:           ``True`` when the command exited with code 0.
        command:          The command string that was executed.
        stdout:           Captured standard output (empty string on timeout).
        stderr:           Captured standard error (empty string on timeout).
        duration_seconds: Wall-clock seconds the check took to complete.
        required:         Whether this check must pass to allow auto-approval.
    """

    name: str = Field(..., description="Check label.")
    passed: bool = Field(..., description="True when the command exited with code 0.")
    command: str = Field(..., description="The command string that was executed.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    duration_seconds: float = Field(..., ge=0.0, description="Wall-clock seconds the check took.")
    required: bool = Field(
        default=True,
        description="Whether this check must pass for auto-approval.",
    )

    model_config = {"frozen": False, "populate_by_name": True}


class EvaluatorResult(BaseModel):
    """Aggregated outcome of all acceptance checks for a single task.

    Attributes:
        task_id:          The task that was evaluated.
        check_results:    Per-check outcomes in execution order.
        all_passed:       ``True`` only when every check (required and
                          optional) exited with code 0.
        required_passed:  ``True`` when all *required* checks passed.
                          ``True`` vacuously when there are no required checks.
        duration_seconds: Total wall-clock seconds for all checks combined.
        auto_approved:    ``True`` when ``required_passed`` is ``True`` and
                          the profile's ``auto_approve_on_pass`` flag is set.
    """

    task_id: str = Field(..., description="Task that was evaluated.")
    check_results: list[CheckResult] = Field(
        default_factory=list,
        description="Per-check outcomes in execution order.",
    )
    all_passed: bool = Field(..., description="True only when every check passed.")
    required_passed: bool = Field(
        ...,
        description="True when all required checks passed (vacuously True with no required checks).",
    )
    duration_seconds: float = Field(
        ..., ge=0.0, description="Total wall-clock seconds for all checks."
    )
    auto_approved: bool = Field(
        default=False,
        description="True when required_passed and profile.auto_approve_on_pass.",
    )

    model_config = {"frozen": False, "populate_by_name": True}


# ---------------------------------------------------------------------------
# Evaluator orchestrator
# ---------------------------------------------------------------------------


class EvaluatorOrchestrator:
    """Runs :class:`AcceptanceCheck` commands and aggregates results.

    Each check is executed via :func:`asyncio.create_subprocess_exec` in a
    fresh subprocess.  Commands are split with :func:`shlex.split` so they
    work correctly with arguments containing spaces.  stdout/stderr are
    captured; a timeout causes the check to be marked as failed.

    Args:
        summary_window: Number of past :class:`EvaluatorResult` objects kept
            for :meth:`get_summary`.  Defaults to 100.
    """

    def __init__(self, summary_window: int = _SUMMARY_WINDOW) -> None:
        self._history: deque[EvaluatorResult] = deque(maxlen=summary_window)
        logger.info(
            "EvaluatorOrchestrator initialised",
            extra={"summary_window": summary_window},
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_checks(
        self,
        task_id: str,
        profile: EvaluatorProfile,
        work_dir: str | None = None,
    ) -> EvaluatorResult:
        """Execute all checks in *profile* and return an aggregated result.

        Checks are executed sequentially in the order defined by
        ``profile.checks``.  Every check is always run — a failed required
        check does not short-circuit the remaining checks.

        Args:
            task_id:  Identifier of the task being evaluated.
            profile:  :class:`EvaluatorProfile` carrying the checks and policy.
            work_dir: Working directory passed to each subprocess.  When
                      ``None`` the subprocess inherits the current working
                      directory.

        Returns:
            An :class:`EvaluatorResult` with the complete evaluation outcome.
        """
        logger.info(
            "Starting evaluator run",
            extra={
                "task_id": task_id,
                "check_count": len(profile.checks),
                "auto_approve_on_pass": profile.auto_approve_on_pass,
                "work_dir": work_dir,
            },
        )

        wall_start = time.monotonic()
        check_results: list[CheckResult] = []

        for check in profile.checks:
            result = await self._run_single_check(check, work_dir=work_dir)
            check_results.append(result)

        total_duration = time.monotonic() - wall_start

        all_passed = all(r.passed for r in check_results)
        # Vacuously True when there are no required checks
        required_passed = all(r.passed for r in check_results if r.required)
        auto_approved = required_passed and profile.auto_approve_on_pass

        evaluator_result = EvaluatorResult(
            task_id=task_id,
            check_results=check_results,
            all_passed=all_passed,
            required_passed=required_passed,
            duration_seconds=total_duration,
            auto_approved=auto_approved,
        )

        self._history.append(evaluator_result)

        logger.info(
            "Evaluator run complete",
            extra={
                "task_id": task_id,
                "all_passed": all_passed,
                "required_passed": required_passed,
                "auto_approved": auto_approved,
                "duration_seconds": round(total_duration, 3),
            },
        )

        return evaluator_result

    def get_summary(self) -> dict[str, Any]:
        """Return a summary dict of the last N evaluation results.

        Returns:
            A dict with:

            - ``total``: number of results in the window
            - ``passed``: number of results where ``all_passed`` is ``True``
            - ``required_passed``: number where ``required_passed`` is ``True``
            - ``auto_approved``: number where ``auto_approved`` is ``True``
            - ``pass_rate``: ratio of ``passed / total`` (``0.0`` when empty)
        """
        results = list(self._history)
        total = len(results)
        if total == 0:
            return {
                "total": 0,
                "passed": 0,
                "required_passed": 0,
                "auto_approved": 0,
                "pass_rate": 0.0,
            }

        passed = sum(1 for r in results if r.all_passed)
        req_passed = sum(1 for r in results if r.required_passed)
        auto_approved = sum(1 for r in results if r.auto_approved)

        return {
            "total": total,
            "passed": passed,
            "required_passed": req_passed,
            "auto_approved": auto_approved,
            "pass_rate": round(passed / total, 4),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_single_check(
        self,
        check: AcceptanceCheck,
        work_dir: str | None,
    ) -> CheckResult:
        """Execute a single :class:`AcceptanceCheck` and return its result.

        Uses ``asyncio.create_subprocess_exec`` for subprocess management.
        stdout and stderr are captured as bytes and decoded as UTF-8 with
        ``errors="replace"`` so binary output does not raise.

        A :class:`asyncio.TimeoutError` is caught and treated as a failed
        check; a broad :class:`Exception` guard handles ``FileNotFoundError``
        (command not found) and other OS-level errors.

        Args:
            check:    The acceptance check to run.
            work_dir: Working directory for the subprocess (``None`` → inherit).

        Returns:
            :class:`CheckResult` with the observed outcome.
        """
        start = time.monotonic()

        try:
            args = shlex.split(check.command)
        except ValueError as exc:
            # Malformed shell command — treat as an immediate failure
            duration = time.monotonic() - start
            logger.warning(
                "Failed to parse check command",
                extra={"check": check.name, "command": check.command, "error": str(exc)},
            )
            return CheckResult(
                name=check.name,
                passed=False,
                command=check.command,
                stdout="",
                stderr=f"Command parse error: {exc}",
                duration_seconds=duration,
                required=check.required,
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )

            try:
                raw_stdout, raw_stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(check.timeout_seconds),
                )
            except TimeoutError:
                # Kill the lingering process then record failure
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass  # process already exited

                duration = time.monotonic() - start
                logger.warning(
                    "Check timed out",
                    extra={
                        "check": check.name,
                        "timeout_seconds": check.timeout_seconds,
                        "duration_seconds": round(duration, 3),
                    },
                )
                return CheckResult(
                    name=check.name,
                    passed=False,
                    command=check.command,
                    stdout="",
                    stderr=f"Timed out after {check.timeout_seconds}s",
                    duration_seconds=duration,
                    required=check.required,
                )

            duration = time.monotonic() - start
            passed = proc.returncode == 0
            stdout = raw_stdout.decode("utf-8", errors="replace")
            stderr = raw_stderr.decode("utf-8", errors="replace")

            log_fn = logger.debug if passed else logger.warning
            log_fn(
                "Check %s",
                "passed" if passed else "failed",
                extra={
                    "check": check.name,
                    "returncode": proc.returncode,
                    "required": check.required,
                    "duration_seconds": round(duration, 3),
                },
            )

            return CheckResult(
                name=check.name,
                passed=passed,
                command=check.command,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                required=check.required,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error(
                "Check raised an unexpected error",
                extra={
                    "check": check.name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return CheckResult(
                name=check.name,
                passed=False,
                command=check.command,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
                required=check.required,
            )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_evaluator_instance: EvaluatorOrchestrator | None = None
_evaluator_lock: Lock = Lock()


def get_evaluator_orchestrator(
    summary_window: int = _SUMMARY_WINDOW,
) -> EvaluatorOrchestrator:
    """Return the global :class:`EvaluatorOrchestrator` singleton.

    On the first call, the instance is created with *summary_window*.
    Subsequent calls return the cached instance regardless of the arguments.

    To replace the singleton (e.g. in tests) call
    :func:`reset_evaluator_orchestrator` first.

    Args:
        summary_window: Number of past results to retain for
            :meth:`EvaluatorOrchestrator.get_summary`.  Only used on the
            first call.

    Returns:
        The singleton :class:`EvaluatorOrchestrator`.
    """
    global _evaluator_instance
    with _evaluator_lock:
        if _evaluator_instance is None:
            _evaluator_instance = EvaluatorOrchestrator(summary_window=summary_window)
        return _evaluator_instance


def reset_evaluator_orchestrator() -> None:
    """Reset the singleton — intended for use in tests only."""
    global _evaluator_instance
    with _evaluator_lock:
        _evaluator_instance = None

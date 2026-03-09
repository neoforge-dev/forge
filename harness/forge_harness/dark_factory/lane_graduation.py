"""Lane Graduation Criteria — DF-5005
======================================

Formalises the safety and reliability gates that a work-cell lane must
satisfy before it can be promoted from one rollout stage to the next.

Graduation path::

    human_review → canary → enabled

Each promotion requires that the lane's recent metrics (over a configurable
observation window) meet **all** criteria defined in the gate.  If any
criterion fails, graduation is blocked and the failing metric is reported
so the operator can investigate.

Criteria (default gates)
------------------------

human_review → canary:
    - Evaluator pass rate ≥ 95% over the last 7 days
    - Escaped defects = 0 over the last 7 days
    - Minimum 20 task completions in the observation window
    - No requeue rate spike (≤ 10%)

canary → enabled:
    - Evaluator pass rate ≥ 98% over the last 14 days
    - Escaped defects = 0 over the last 14 days
    - Audit pass rate ≥ 95% (from DF-4003 audit sampler)
    - Minimum 50 task completions in the observation window
    - No requeue rate spike (≤ 5%)
    - Mean cycle time ≤ 2x the lane's SLO target

Design principles
-----------------
* **Config-driven**: thresholds are plain dicts, overridable at runtime.
* **Evidence-based**: every graduation attempt produces a
  :class:`GraduationReport` with pass/fail per criterion.
* **Non-destructive**: this module only *evaluates* readiness; the actual
  stage promotion is done by :class:`CanaryControl` (DF-4004).
* **Singleton**: :func:`get_lane_graduation` / :func:`reset_lane_graduation`.
* **Thread-safe**: all state guarded by ``RLock``.

Usage
-----
::

    from forge_harness.dark_factory.lane_graduation import (
        get_lane_graduation,
        LaneMetrics,
    )

    grad = get_lane_graduation()

    metrics = LaneMetrics(
        evaluator_pass_rate=0.97,
        escaped_defects=0,
        total_completions=35,
        requeue_rate=0.03,
        audit_pass_rate=1.0,
        mean_cycle_time_minutes=15.0,
        slo_target_minutes=30.0,
    )

    report = grad.evaluate("docs", "canary", metrics)
    if report.can_graduate:
        # Safe to promote via CanaryControl.set_stage()
        ...
    else:
        for c in report.failed_criteria:
            print(f"BLOCKED: {c.name} — {c.reason}")

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-5005)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Default graduation gate configs
# ---------------------------------------------------------------------------

DEFAULT_CANARY_GATE: dict[str, Any] = {
    "min_evaluator_pass_rate": 0.95,
    "max_escaped_defects": 0,
    "min_completions": 20,
    "max_requeue_rate": 0.10,
    "observation_days": 7,
}

DEFAULT_ENABLED_GATE: dict[str, Any] = {
    "min_evaluator_pass_rate": 0.98,
    "max_escaped_defects": 0,
    "min_completions": 50,
    "max_requeue_rate": 0.05,
    "min_audit_pass_rate": 0.95,
    "max_cycle_time_ratio": 2.0,  # mean_cycle_time / slo_target
    "observation_days": 14,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneMetrics:
    """Observed metrics for a lane over an observation window.

    All rates are floats in [0, 1].  Counts are non-negative integers.
    Time values are in minutes.
    """

    evaluator_pass_rate: float
    escaped_defects: int
    total_completions: int
    requeue_rate: float
    audit_pass_rate: float = 1.0
    mean_cycle_time_minutes: float = 0.0
    slo_target_minutes: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_pass_rate": self.evaluator_pass_rate,
            "escaped_defects": self.escaped_defects,
            "total_completions": self.total_completions,
            "requeue_rate": self.requeue_rate,
            "audit_pass_rate": self.audit_pass_rate,
            "mean_cycle_time_minutes": self.mean_cycle_time_minutes,
            "slo_target_minutes": self.slo_target_minutes,
        }


@dataclass(frozen=True)
class CriterionResult:
    """Result of evaluating a single graduation criterion.

    Attributes:
        name:      Short criterion name (e.g. "evaluator_pass_rate").
        passed:    Whether the criterion was met.
        threshold: The required threshold value.
        actual:    The observed value.
        reason:    Human-readable explanation (especially for failures).
    """

    name: str
    passed: bool
    threshold: Any
    actual: Any
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "threshold": self.threshold,
            "actual": self.actual,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GraduationReport:
    """Full report of a graduation evaluation.

    Attributes:
        report_id:        Unique ID for traceability.
        lane:             The work-cell lane evaluated.
        target_stage:     The stage being evaluated for (canary or enabled).
        can_graduate:     True if ALL criteria passed.
        criteria:         List of all criterion results.
        failed_criteria:  Convenience list of only the failures.
        timestamp:        UTC timestamp of the evaluation.
    """

    report_id: str
    lane: str
    target_stage: str
    can_graduate: bool
    criteria: tuple[CriterionResult, ...]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def failed_criteria(self) -> list[CriterionResult]:
        return [c for c in self.criteria if not c.passed]

    @property
    def passed_criteria(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "lane": self.lane,
            "target_stage": self.target_stage,
            "can_graduate": self.can_graduate,
            "criteria": [c.to_dict() for c in self.criteria],
            "failed_count": len(self.failed_criteria),
            "passed_count": len(self.passed_criteria),
            "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# Lane Graduation
# ---------------------------------------------------------------------------


class LaneGraduation:
    """Evaluates whether a lane meets graduation criteria for promotion.

    This module is advisory — it produces a :class:`GraduationReport` but
    does not itself change rollout state.  The operator or automation layer
    uses the report to decide whether to call
    :meth:`CanaryControl.set_stage`.

    Args:
        canary_gate: Criteria for human_review → canary promotion.
        enabled_gate: Criteria for canary → enabled promotion.
    """

    def __init__(
        self,
        canary_gate: dict[str, Any] | None = None,
        enabled_gate: dict[str, Any] | None = None,
    ) -> None:
        self._lock = RLock()
        self._canary_gate = dict(canary_gate or DEFAULT_CANARY_GATE)
        self._enabled_gate = dict(enabled_gate or DEFAULT_ENABLED_GATE)
        self._reports: list[GraduationReport] = []
        logger.info("LaneGraduation initialised")

    @property
    def canary_gate(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._canary_gate)

    @property
    def enabled_gate(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._enabled_gate)

    def update_canary_gate(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Update canary gate thresholds. Returns new config."""
        with self._lock:
            self._canary_gate.update(overrides)
            logger.info("Canary gate updated: %s", overrides)
            return dict(self._canary_gate)

    def update_enabled_gate(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Update enabled gate thresholds. Returns new config."""
        with self._lock:
            self._enabled_gate.update(overrides)
            logger.info("Enabled gate updated: %s", overrides)
            return dict(self._enabled_gate)

    def evaluate(
        self,
        lane: str,
        target_stage: str,
        metrics: LaneMetrics,
    ) -> GraduationReport:
        """Evaluate whether a lane meets graduation criteria.

        Args:
            lane: The work-cell lane being evaluated.
            target_stage: "canary" or "enabled".
            metrics: Observed lane metrics.

        Returns:
            A :class:`GraduationReport` with pass/fail per criterion.

        Raises:
            ValueError: If target_stage is not "canary" or "enabled".
        """
        if target_stage not in ("canary", "enabled"):
            raise ValueError(
                f"target_stage must be 'canary' or 'enabled', got {target_stage!r}"
            )

        with self._lock:
            gate = self._canary_gate if target_stage == "canary" else self._enabled_gate

        results: list[CriterionResult] = []

        # --- Evaluator pass rate ---
        min_pass = gate.get("min_evaluator_pass_rate", 0.95)
        results.append(CriterionResult(
            name="evaluator_pass_rate",
            passed=metrics.evaluator_pass_rate >= min_pass,
            threshold=min_pass,
            actual=metrics.evaluator_pass_rate,
            reason=(
                ""
                if metrics.evaluator_pass_rate >= min_pass
                else f"Pass rate {metrics.evaluator_pass_rate:.1%} below required {min_pass:.1%}"
            ),
        ))

        # --- Escaped defects ---
        max_defects = gate.get("max_escaped_defects", 0)
        results.append(CriterionResult(
            name="escaped_defects",
            passed=metrics.escaped_defects <= max_defects,
            threshold=max_defects,
            actual=metrics.escaped_defects,
            reason=(
                ""
                if metrics.escaped_defects <= max_defects
                else f"{metrics.escaped_defects} escaped defects exceeds limit of {max_defects}"
            ),
        ))

        # --- Minimum completions ---
        min_completions = gate.get("min_completions", 20)
        results.append(CriterionResult(
            name="min_completions",
            passed=metrics.total_completions >= min_completions,
            threshold=min_completions,
            actual=metrics.total_completions,
            reason=(
                ""
                if metrics.total_completions >= min_completions
                else f"Only {metrics.total_completions} completions; need {min_completions}"
            ),
        ))

        # --- Requeue rate ---
        max_requeue = gate.get("max_requeue_rate", 0.10)
        results.append(CriterionResult(
            name="requeue_rate",
            passed=metrics.requeue_rate <= max_requeue,
            threshold=max_requeue,
            actual=metrics.requeue_rate,
            reason=(
                ""
                if metrics.requeue_rate <= max_requeue
                else f"Requeue rate {metrics.requeue_rate:.1%} exceeds limit of {max_requeue:.1%}"
            ),
        ))

        # --- Audit pass rate (enabled gate only) ---
        if target_stage == "enabled":
            min_audit = gate.get("min_audit_pass_rate", 0.95)
            results.append(CriterionResult(
                name="audit_pass_rate",
                passed=metrics.audit_pass_rate >= min_audit,
                threshold=min_audit,
                actual=metrics.audit_pass_rate,
                reason=(
                    ""
                    if metrics.audit_pass_rate >= min_audit
                    else f"Audit pass rate {metrics.audit_pass_rate:.1%} below {min_audit:.1%}"
                ),
            ))

            # --- Cycle time ratio ---
            max_ratio = gate.get("max_cycle_time_ratio", 2.0)
            if metrics.slo_target_minutes > 0:
                ratio = metrics.mean_cycle_time_minutes / metrics.slo_target_minutes
            else:
                ratio = 0.0
            results.append(CriterionResult(
                name="cycle_time_ratio",
                passed=ratio <= max_ratio,
                threshold=max_ratio,
                actual=round(ratio, 2),
                reason=(
                    ""
                    if ratio <= max_ratio
                    else f"Cycle time ratio {ratio:.1f}x exceeds {max_ratio:.1f}x SLO"
                ),
            ))

        can_graduate = all(r.passed for r in results)

        report = GraduationReport(
            report_id=str(uuid.uuid4()),
            lane=lane,
            target_stage=target_stage,
            can_graduate=can_graduate,
            criteria=tuple(results),
        )

        with self._lock:
            self._reports.append(report)

        logger.info(
            "Graduation %s: lane=%s target=%s (%d/%d criteria passed)",
            "APPROVED" if can_graduate else "BLOCKED",
            lane,
            target_stage,
            len(report.passed_criteria),
            len(report.criteria),
        )

        return report

    def get_reports(
        self,
        lane: str | None = None,
        limit: int = 50,
    ) -> list[GraduationReport]:
        """Get graduation reports, optionally filtered by lane."""
        with self._lock:
            reports = self._reports
            if lane:
                reports = [r for r in reports if r.lane == lane]
            return list(reversed(reports[-limit:]))

    def reset(self) -> None:
        """Reset all state to defaults. For testing."""
        with self._lock:
            self._canary_gate = dict(DEFAULT_CANARY_GATE)
            self._enabled_gate = dict(DEFAULT_ENABLED_GATE)
            self._reports.clear()
            logger.info("LaneGraduation reset")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LaneGraduation | None = None
_instance_lock = RLock()


def get_lane_graduation() -> LaneGraduation:
    """Get the shared LaneGraduation singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = LaneGraduation()
        return _instance


def reset_lane_graduation() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.reset()
        _instance = None

"""Adaptive Threshold Tuning — DF-5001
=======================================

Adjusts Dark Factory quality thresholds based on observed evaluator
outcomes.  The goal is to tighten thresholds when quality is high (to
catch more edge cases) and relax them when quality drops (to avoid
overwhelming the human review queue).

All threshold changes are:
- **Bounded**: each adjustment is clamped to a configurable max step size.
- **Audited**: every change is logged with before/after values and reason.
- **Revertible**: the previous threshold set can be restored instantly.

Tuning targets
--------------

1. **Audit sample rate** — increased when escaped defects rise, decreased
   when audit pass rate is consistently high.
2. **Evaluator pass threshold** — the minimum pass rate required for auto-
   completion.  Tightened when quality is high, relaxed under pressure.
3. **Requeue rate threshold** — max allowable requeue rate before a lane
   is flagged for review.

The tuner does NOT directly modify the :class:`AuditSampler` or
:class:`LaneGraduation` configs.  Instead it produces
:class:`ThresholdAdjustment` recommendations that the orchestration layer
can apply (or reject).

Design principles
-----------------
* **Conservative**: default max step is 2% per adjustment cycle.
* **Evidence-based**: adjustments require a minimum observation count.
* **Singleton**: :func:`get_adaptive_thresholds` / :func:`reset_adaptive_thresholds`.
* **Thread-safe**: ``RLock`` guarded.

Usage
-----
::

    from forge_harness.dark_factory.adaptive_thresholds import (
        get_adaptive_thresholds,
        ObservedMetrics,
    )

    tuner = get_adaptive_thresholds()

    metrics = ObservedMetrics(
        evaluator_pass_rate=0.99,
        audit_pass_rate=0.98,
        escaped_defects=0,
        requeue_rate=0.02,
        total_tasks=150,
    )

    adjustments = tuner.compute_adjustments(metrics)
    for adj in adjustments:
        print(f"{adj.parameter}: {adj.old_value} → {adj.new_value} ({adj.reason})")

    # Apply (or not) — then acknowledge
    tuner.apply_adjustments(adjustments, operator="system")

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-5001)
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
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    # Current threshold values
    "audit_sample_rate": 0.125,       # 12.5% (from DF-4003 default)
    "min_evaluator_pass_rate": 0.95,  # Lane graduation canary gate
    "max_requeue_rate": 0.10,         # Lane graduation canary gate

    # Tuning bounds — absolute min/max for each parameter
    "audit_sample_rate_min": 0.05,    # Never below 5%
    "audit_sample_rate_max": 0.50,    # Never above 50%
    "min_evaluator_pass_rate_min": 0.90,
    "min_evaluator_pass_rate_max": 0.99,
    "max_requeue_rate_min": 0.02,
    "max_requeue_rate_max": 0.20,

    # Step size — max change per adjustment cycle
    "max_step": 0.02,                 # 2% per cycle

    # Minimum observations before tuning
    "min_observations": 30,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedMetrics:
    """Metrics observed over a tuning window.

    All rates are floats in [0, 1].
    """

    evaluator_pass_rate: float
    audit_pass_rate: float
    escaped_defects: int
    requeue_rate: float
    total_tasks: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_pass_rate": self.evaluator_pass_rate,
            "audit_pass_rate": self.audit_pass_rate,
            "escaped_defects": self.escaped_defects,
            "requeue_rate": self.requeue_rate,
            "total_tasks": self.total_tasks,
        }


@dataclass(frozen=True)
class ThresholdAdjustment:
    """A recommended change to a single threshold parameter.

    Attributes:
        adjustment_id:  Unique ID for traceability.
        parameter:      Name of the parameter to adjust.
        old_value:      Current value.
        new_value:      Recommended new value.
        direction:      "tighten" or "relax".
        reason:         Human-readable explanation.
        confidence:     Confidence in this adjustment (0-1).
        timestamp:      When the adjustment was computed.
    """

    adjustment_id: str
    parameter: str
    old_value: float
    new_value: float
    direction: str
    reason: str
    confidence: float = 0.5
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjustment_id": self.adjustment_id,
            "parameter": self.parameter,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "direction": self.direction,
            "reason": self.reason,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AdjustmentRecord:
    """Record of an applied adjustment set.

    Attributes:
        record_id:     Unique ID.
        adjustments:   The adjustments that were applied.
        operator:      Who applied them.
        applied_at:    When they were applied.
        reverted:      Whether this set has been reverted.
        reverted_at:   When it was reverted (if applicable).
    """

    record_id: str
    adjustments: list[ThresholdAdjustment]
    operator: str
    applied_at: datetime
    reverted: bool = False
    reverted_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "adjustments": [a.to_dict() for a in self.adjustments],
            "operator": self.operator,
            "applied_at": self.applied_at.isoformat(),
            "reverted": self.reverted,
            "reverted_at": self.reverted_at.isoformat() if self.reverted_at else None,
        }


# ---------------------------------------------------------------------------
# Adaptive Thresholds
# ---------------------------------------------------------------------------


class AdaptiveThresholds:
    """Threshold tuning engine for Dark Factory quality parameters.

    Computes bounded, audited adjustments based on observed metrics.
    Does not directly modify other DF modules — produces recommendations.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._lock = RLock()
        self._config = dict(config or DEFAULT_CONFIG)
        self._history: list[AdjustmentRecord] = []
        self._snapshots: list[dict[str, Any]] = []  # for revert
        logger.info("AdaptiveThresholds initialised")

    @property
    def config(self) -> dict[str, Any]:
        """Current threshold configuration (copy)."""
        with self._lock:
            return dict(self._config)

    def get_threshold(self, parameter: str) -> float:
        """Get current value of a threshold parameter."""
        with self._lock:
            return float(self._config.get(parameter, 0.0))

    def _clamp(self, parameter: str, value: float) -> float:
        """Clamp a value to the configured bounds for a parameter."""
        lo = self._config.get(f"{parameter}_min", 0.0)
        hi = self._config.get(f"{parameter}_max", 1.0)
        return max(float(lo), min(float(hi), value))

    def _step(self, current: float, target_direction: float) -> float:
        """Compute a bounded step in the given direction.

        Args:
            current: Current value.
            target_direction: Positive to increase, negative to decrease.

        Returns:
            New value after applying bounded step.
        """
        max_step = self._config.get("max_step", 0.02)
        delta = max(-max_step, min(max_step, target_direction))
        return current + delta

    def compute_adjustments(
        self,
        metrics: ObservedMetrics,
    ) -> list[ThresholdAdjustment]:
        """Compute threshold adjustments based on observed metrics.

        Returns a list of recommended adjustments (may be empty if no
        changes are warranted).

        Args:
            metrics: Observed metrics from the tuning window.

        Returns:
            List of :class:`ThresholdAdjustment` recommendations.
        """
        with self._lock:
            min_obs = self._config.get("min_observations", 30)
            if metrics.total_tasks < min_obs:
                logger.debug(
                    "Insufficient observations (%d < %d); no adjustments",
                    metrics.total_tasks,
                    min_obs,
                )
                return []

            adjustments: list[ThresholdAdjustment] = []

            # --- Audit sample rate ---
            current_sample = self._config["audit_sample_rate"]
            if metrics.escaped_defects > 0:
                # Defects found: increase sampling
                new_sample = self._step(current_sample, 0.02)
                new_sample = self._clamp("audit_sample_rate", new_sample)
                if new_sample != current_sample:
                    adjustments.append(ThresholdAdjustment(
                        adjustment_id=str(uuid.uuid4()),
                        parameter="audit_sample_rate",
                        old_value=current_sample,
                        new_value=round(new_sample, 4),
                        direction="tighten",
                        reason=f"{metrics.escaped_defects} escaped defects; increasing audit sampling",
                        confidence=0.9,
                    ))
            elif metrics.audit_pass_rate >= 0.98 and metrics.total_tasks >= 50:
                # High quality sustained: decrease sampling (cost savings)
                new_sample = self._step(current_sample, -0.01)
                new_sample = self._clamp("audit_sample_rate", new_sample)
                if new_sample != current_sample:
                    adjustments.append(ThresholdAdjustment(
                        adjustment_id=str(uuid.uuid4()),
                        parameter="audit_sample_rate",
                        old_value=current_sample,
                        new_value=round(new_sample, 4),
                        direction="relax",
                        reason=f"Audit pass rate {metrics.audit_pass_rate:.1%} sustained over {metrics.total_tasks} tasks",
                        confidence=0.7,
                    ))

            # --- Evaluator pass rate threshold ---
            current_eval = self._config["min_evaluator_pass_rate"]
            if metrics.evaluator_pass_rate >= 0.99 and metrics.escaped_defects == 0:
                # Excellent quality: tighten threshold
                new_eval = self._step(current_eval, 0.01)
                new_eval = self._clamp("min_evaluator_pass_rate", new_eval)
                if new_eval != current_eval:
                    adjustments.append(ThresholdAdjustment(
                        adjustment_id=str(uuid.uuid4()),
                        parameter="min_evaluator_pass_rate",
                        old_value=current_eval,
                        new_value=round(new_eval, 4),
                        direction="tighten",
                        reason=f"Eval pass rate {metrics.evaluator_pass_rate:.1%} with 0 defects; raising bar",
                        confidence=0.6,
                    ))
            elif metrics.evaluator_pass_rate < current_eval * 0.95:
                # Quality dropping significantly: relax to avoid queue overflow
                new_eval = self._step(current_eval, -0.01)
                new_eval = self._clamp("min_evaluator_pass_rate", new_eval)
                if new_eval != current_eval:
                    adjustments.append(ThresholdAdjustment(
                        adjustment_id=str(uuid.uuid4()),
                        parameter="min_evaluator_pass_rate",
                        old_value=current_eval,
                        new_value=round(new_eval, 4),
                        direction="relax",
                        reason=f"Eval pass rate {metrics.evaluator_pass_rate:.1%} significantly below threshold {current_eval:.1%}",
                        confidence=0.5,
                    ))

            # --- Requeue rate threshold ---
            current_requeue = self._config["max_requeue_rate"]
            if metrics.requeue_rate <= current_requeue * 0.3 and metrics.total_tasks >= 50:
                # Requeue rate well under threshold: tighten
                new_requeue = self._step(current_requeue, -0.01)
                new_requeue = self._clamp("max_requeue_rate", new_requeue)
                if new_requeue != current_requeue:
                    adjustments.append(ThresholdAdjustment(
                        adjustment_id=str(uuid.uuid4()),
                        parameter="max_requeue_rate",
                        old_value=current_requeue,
                        new_value=round(new_requeue, 4),
                        direction="tighten",
                        reason=f"Requeue rate {metrics.requeue_rate:.1%} well below {current_requeue:.1%}; tightening",
                        confidence=0.6,
                    ))
            elif metrics.requeue_rate > current_requeue:
                # Over threshold: relax to avoid cascading failures
                new_requeue = self._step(current_requeue, 0.02)
                new_requeue = self._clamp("max_requeue_rate", new_requeue)
                if new_requeue != current_requeue:
                    adjustments.append(ThresholdAdjustment(
                        adjustment_id=str(uuid.uuid4()),
                        parameter="max_requeue_rate",
                        old_value=current_requeue,
                        new_value=round(new_requeue, 4),
                        direction="relax",
                        reason=f"Requeue rate {metrics.requeue_rate:.1%} exceeds {current_requeue:.1%}; relaxing to absorb pressure",
                        confidence=0.8,
                    ))

            return adjustments

    def apply_adjustments(
        self,
        adjustments: list[ThresholdAdjustment],
        operator: str,
    ) -> AdjustmentRecord:
        """Apply a set of threshold adjustments.

        Saves a snapshot of the current config for revert capability.

        Args:
            adjustments: Adjustments to apply.
            operator: Who is applying them.

        Returns:
            An :class:`AdjustmentRecord` for the audit trail.

        Raises:
            ValueError: If adjustments is empty or operator is empty.
        """
        if not adjustments:
            raise ValueError("No adjustments to apply")
        if not operator:
            raise ValueError("operator must be non-empty")

        with self._lock:
            # Snapshot current config
            self._snapshots.append(dict(self._config))

            # Apply each adjustment
            for adj in adjustments:
                if adj.parameter in self._config:
                    self._config[adj.parameter] = adj.new_value
                    logger.info(
                        "Threshold adjusted: %s %.4f → %.4f (%s)",
                        adj.parameter,
                        adj.old_value,
                        adj.new_value,
                        adj.direction,
                    )

            record = AdjustmentRecord(
                record_id=str(uuid.uuid4()),
                adjustments=list(adjustments),
                operator=operator,
                applied_at=datetime.now(UTC),
            )
            self._history.append(record)
            return record

    def revert_last(self, operator: str) -> AdjustmentRecord | None:
        """Revert the most recent adjustment set.

        Restores the config snapshot taken before the last apply.

        Args:
            operator: Who is reverting.

        Returns:
            The reverted :class:`AdjustmentRecord`, or None if nothing to revert.
        """
        with self._lock:
            if not self._snapshots or not self._history:
                return None

            # Restore previous config
            self._config = self._snapshots.pop()

            # Mark the last record as reverted
            record = self._history[-1]
            record.reverted = True
            record.reverted_at = datetime.now(UTC)

            logger.info(
                "Threshold adjustments reverted by %s (record=%s)",
                operator,
                record.record_id,
            )
            return record

    def get_history(self, limit: int = 20) -> list[AdjustmentRecord]:
        """Get adjustment history, most recent first."""
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def reset(self) -> None:
        """Reset all state to defaults. For testing."""
        with self._lock:
            self._config = dict(DEFAULT_CONFIG)
            self._history.clear()
            self._snapshots.clear()
            logger.info("AdaptiveThresholds reset")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: AdaptiveThresholds | None = None
_instance_lock = RLock()


def get_adaptive_thresholds() -> AdaptiveThresholds:
    """Get the shared AdaptiveThresholds singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AdaptiveThresholds()
        return _instance


def reset_adaptive_thresholds() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.reset()
        _instance = None

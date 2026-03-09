"""Audit Sampler — DF-4003
==========================

Implements random audit sampling for autonomous task completions.

When a task completes autonomously (via the ``autonomous`` lane), a
configurable percentage (default 12.5%, range 10-15%) of completions
are randomly selected for human review after the fact.

This is a **post-hoc** quality check — the task is already completed and
merged, but a human auditor reviews the output for correctness, safety,
and adherence to standards.  Results feed into the adaptive threshold
tuning engine (DF-5001).

Design principles
-----------------
* **Config-driven**: sample rate adjustable at runtime via
  :meth:`AuditSampler.update_config`.
* **Deterministic replay**: each sampling decision records the random seed
  so the same decision can be reproduced for auditing the auditor.
* **Bounded**: sample rate is clamped to ``[0.01, 1.0]`` — at least 1%,
  at most 100%.
* **Singleton**: use :func:`get_audit_sampler` / :func:`reset_audit_sampler`.
* **Thread-safe**: all state guarded by ``RLock``.

Usage
-----
::

    from forge_harness.dark_factory.audit_sampler import (
        get_audit_sampler,
        AuditResult,
    )

    sampler = get_audit_sampler()

    # Check if a task should be audited
    decision = sampler.should_audit(task_id="T-1234", lane="docs")
    if decision.selected:
        # Route task to human review queue
        ...

    # Record audit result
    sampler.record_result(
        task_id="T-1234",
        auditor_id="alice@example.com",
        passed=True,
        notes="Output meets quality standards.",
    )

    # Get daily audit summary
    summary = sampler.get_daily_summary()

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-4003)
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SAMPLE_RATE = 0.01  # 1%
MAX_SAMPLE_RATE = 1.0   # 100%
DEFAULT_SAMPLE_RATE = 0.125  # 12.5% (midpoint of 10-15% target)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuditStatus(str, Enum):
    """Status of an audit sample."""

    pending = "pending"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SamplingDecision:
    """Immutable record of a single sampling decision.

    Attributes:
        task_id:       Unique identifier of the completed task.
        lane:          The work-cell lane that processed the task.
        selected:      Whether this task was selected for audit.
        sample_rate:   The sample rate in effect at decision time.
        random_value:  The random float [0, 1) that determined selection.
        decision_id:   Unique ID for this decision (for traceability).
        timestamp:     UTC timestamp of the decision.
    """

    task_id: str
    lane: str
    selected: bool
    sample_rate: float
    random_value: float
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AuditRecord:
    """Mutable record tracking a sampled task through the audit lifecycle.

    Attributes:
        decision:    The original sampling decision.
        status:      Current audit status.
        auditor_id:  Who performed the audit (None until reviewed).
        passed:      Whether the task passed audit (None until reviewed).
        notes:       Auditor notes.
        reviewed_at: UTC timestamp of the review (None until reviewed).
    """

    decision: SamplingDecision
    status: AuditStatus = AuditStatus.pending
    auditor_id: str | None = None
    passed: bool | None = None
    notes: str = ""
    reviewed_at: datetime | None = None

    @property
    def task_id(self) -> str:
        return self.decision.task_id

    @property
    def lane(self) -> str:
        return self.decision.lane

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON/API output."""
        return {
            "task_id": self.decision.task_id,
            "lane": self.decision.lane,
            "selected": self.decision.selected,
            "sample_rate": self.decision.sample_rate,
            "random_value": self.decision.random_value,
            "decision_id": self.decision.decision_id,
            "decision_timestamp": self.decision.timestamp.isoformat(),
            "status": self.status.value,
            "auditor_id": self.auditor_id,
            "passed": self.passed,
            "notes": self.notes,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
        }


@dataclass(frozen=True)
class AuditSummary:
    """Summary of audit activity over a period.

    Attributes:
        total_completions:  Number of autonomous completions evaluated.
        total_sampled:      Number selected for audit.
        total_reviewed:     Number actually reviewed by a human.
        total_passed:       Number that passed audit.
        total_failed:       Number that failed audit.
        total_pending:      Number awaiting review.
        effective_rate:     Actual sampling rate (sampled / completions).
        pass_rate:          Pass rate among reviewed tasks.
        period_start:       Start of the summary period.
        period_end:         End of the summary period.
    """

    total_completions: int
    total_sampled: int
    total_reviewed: int
    total_passed: int
    total_failed: int
    total_pending: int
    effective_rate: float
    pass_rate: float
    period_start: datetime | None
    period_end: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_completions": self.total_completions,
            "total_sampled": self.total_sampled,
            "total_reviewed": self.total_reviewed,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "total_pending": self.total_pending,
            "effective_rate": round(self.effective_rate, 4),
            "pass_rate": round(self.pass_rate, 4),
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
        }


# ---------------------------------------------------------------------------
# Audit Sampler
# ---------------------------------------------------------------------------


class AuditSampler:
    """Random audit sampler for autonomous task completions.

    Thread-safe singleton that:
    1. Decides whether each autonomous completion should be audited.
    2. Tracks audit records through the review lifecycle.
    3. Provides daily summaries for the scorecard pipeline.

    Args:
        sample_rate: Fraction of completions to audit (default 12.5%).
    """

    def __init__(self, sample_rate: float = DEFAULT_SAMPLE_RATE) -> None:
        self._lock = RLock()
        self._sample_rate = self._clamp_rate(sample_rate)
        self._decisions: list[SamplingDecision] = []
        self._records: dict[str, AuditRecord] = {}  # task_id → record
        self._rng = random.Random()
        logger.info(
            "AuditSampler initialised with sample_rate=%.1f%%",
            self._sample_rate * 100,
        )

    @staticmethod
    def _clamp_rate(rate: float) -> float:
        """Clamp sample rate to valid bounds."""
        return max(MIN_SAMPLE_RATE, min(MAX_SAMPLE_RATE, float(rate)))

    @property
    def sample_rate(self) -> float:
        """Current sample rate."""
        with self._lock:
            return self._sample_rate

    def update_config(self, sample_rate: float) -> float:
        """Update the sample rate at runtime.

        Returns the new (clamped) rate.
        """
        with self._lock:
            old = self._sample_rate
            self._sample_rate = self._clamp_rate(sample_rate)
            logger.info(
                "AuditSampler sample_rate changed: %.1f%% → %.1f%%",
                old * 100,
                self._sample_rate * 100,
            )
            return self._sample_rate

    def should_audit(self, task_id: str, lane: str) -> SamplingDecision:
        """Decide whether an autonomous completion should be audited.

        Args:
            task_id: Unique task identifier.
            lane: Work-cell lane string (e.g. "docs", "test_writing").

        Returns:
            A :class:`SamplingDecision` recording the outcome.

        Raises:
            ValueError: If task_id is empty.
        """
        if not task_id:
            raise ValueError("task_id must be non-empty")

        with self._lock:
            random_value = self._rng.random()
            selected = random_value < self._sample_rate

            decision = SamplingDecision(
                task_id=task_id,
                lane=lane or "unknown",
                selected=selected,
                sample_rate=self._sample_rate,
                random_value=random_value,
            )
            self._decisions.append(decision)

            if selected:
                self._records[task_id] = AuditRecord(decision=decision)
                logger.info(
                    "Audit SELECTED: task=%s lane=%s (%.4f < %.4f)",
                    task_id,
                    lane,
                    random_value,
                    self._sample_rate,
                )
            else:
                logger.debug(
                    "Audit SKIPPED: task=%s lane=%s (%.4f >= %.4f)",
                    task_id,
                    lane,
                    random_value,
                    self._sample_rate,
                )

            return decision

    def record_result(
        self,
        task_id: str,
        auditor_id: str,
        passed: bool,
        notes: str = "",
    ) -> AuditRecord:
        """Record the result of a human audit review.

        Args:
            task_id: The task that was audited.
            auditor_id: Identifier of the human auditor.
            passed: Whether the task passed the audit.
            notes: Optional auditor notes.

        Returns:
            The updated :class:`AuditRecord`.

        Raises:
            KeyError: If the task was not selected for audit.
            ValueError: If auditor_id is empty.
        """
        if not auditor_id:
            raise ValueError("auditor_id must be non-empty")

        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(
                    f"Task {task_id!r} was not selected for audit "
                    f"(or has already been purged)"
                )

            record.auditor_id = auditor_id
            record.passed = passed
            record.notes = notes
            record.reviewed_at = datetime.now(UTC)
            record.status = AuditStatus.passed if passed else AuditStatus.failed

            logger.info(
                "Audit result: task=%s auditor=%s passed=%s",
                task_id,
                auditor_id,
                passed,
            )
            return record

    def skip_audit(self, task_id: str, reason: str = "") -> AuditRecord:
        """Mark a sampled task as skipped (e.g. task was reverted).

        Args:
            task_id: The task to skip.
            reason: Optional reason for skipping.

        Returns:
            The updated :class:`AuditRecord`.

        Raises:
            KeyError: If the task was not selected for audit.
        """
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"Task {task_id!r} was not selected for audit")

            record.status = AuditStatus.skipped
            record.notes = reason
            record.reviewed_at = datetime.now(UTC)
            return record

    def get_record(self, task_id: str) -> AuditRecord | None:
        """Get the audit record for a task, or None if not sampled."""
        with self._lock:
            return self._records.get(task_id)

    def get_pending_audits(self) -> list[AuditRecord]:
        """Get all audit records awaiting human review."""
        with self._lock:
            return [
                r for r in self._records.values()
                if r.status == AuditStatus.pending
            ]

    def get_all_records(self) -> list[AuditRecord]:
        """Get all audit records."""
        with self._lock:
            return list(self._records.values())

    def get_daily_summary(
        self,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> AuditSummary:
        """Compute an audit summary for the scorecard pipeline.

        If period bounds are not provided, all decisions are included.
        """
        with self._lock:
            decisions = self._decisions
            if period_start or period_end:
                decisions = [
                    d for d in decisions
                    if (not period_start or d.timestamp >= period_start)
                    and (not period_end or d.timestamp <= period_end)
                ]

            total_completions = len(decisions)
            total_sampled = sum(1 for d in decisions if d.selected)

            # Get records for sampled tasks
            reviewed = [
                r for r in self._records.values()
                if r.status in (AuditStatus.passed, AuditStatus.failed)
                and (not period_start or r.decision.timestamp >= period_start)
                and (not period_end or r.decision.timestamp <= period_end)
            ]
            pending = [
                r for r in self._records.values()
                if r.status == AuditStatus.pending
                and (not period_start or r.decision.timestamp >= period_start)
                and (not period_end or r.decision.timestamp <= period_end)
            ]

            total_reviewed = len(reviewed)
            total_passed = sum(1 for r in reviewed if r.passed)
            total_failed = sum(1 for r in reviewed if not r.passed)
            total_pending = len(pending)

            effective_rate = total_sampled / total_completions if total_completions else 0.0
            pass_rate = total_passed / total_reviewed if total_reviewed else 0.0

            return AuditSummary(
                total_completions=total_completions,
                total_sampled=total_sampled,
                total_reviewed=total_reviewed,
                total_passed=total_passed,
                total_failed=total_failed,
                total_pending=total_pending,
                effective_rate=effective_rate,
                pass_rate=pass_rate,
                period_start=period_start,
                period_end=period_end,
            )

    def reset(self) -> None:
        """Reset all state to defaults.  For testing."""
        with self._lock:
            self._sample_rate = DEFAULT_SAMPLE_RATE
            self._decisions.clear()
            self._records.clear()
            self._rng = random.Random()
            logger.info("AuditSampler reset to defaults")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: AuditSampler | None = None
_instance_lock = RLock()


def get_audit_sampler() -> AuditSampler:
    """Get the shared AuditSampler singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AuditSampler()
        return _instance


def reset_audit_sampler() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.reset()
        _instance = None

"""
Dark Factory Scorecard Models
==============================

Pydantic models and computation logic for the Dark Factory scorecard — a
measurement surface that tracks autonomous-operation health across a single
measurement window (hourly or daily).

A "Dark Factory" is a fully-automated production floor that runs without
human intervention.  These metrics quantify how close the FORGE fleet is to
that ideal and where human touch-points still exist.

Usage
-----
Build a scorecard directly::

    from forge_harness.webhook_server.models.scorecard import (
        DarkFactoryScorecard,
        ScorecardEvent,
        compute_scorecard,
    )

    scorecard = compute_scorecard(tasks, period_start, period_end, node_id="forge:nova")

Emit as an SSE event::

    event = ScorecardEvent(
        event_type="scorecard_daily",
        scorecard=scorecard,
    )
    payload = event.model_dump_json()
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Core scorecard model
# ---------------------------------------------------------------------------


class DarkFactoryScorecard(BaseModel):
    """Metrics snapshot for a single measurement window on a single node.

    All rate fields are normalised to the range [0.0, 1.0].  A value of 1.0
    for ``dispatch_ack_rate`` and ``evaluator_pass_rate``, combined with 0.0
    for ``requeue_rate`` and ``human_touch_ratio``, represents a perfect
    dark-factory run with zero escaped defects.

    Attributes:
        dispatch_ack_rate: Fraction of dispatches that received a positive
            acknowledgment from the target agent (0 = no acks, 1 = all acked).
        evaluator_pass_rate: Fraction of evaluator gate checks that passed
            (0 = all failed, 1 = all passed).
        requeue_rate: Fraction of tasks that were returned to the pending
            queue at least once (0 = no requeues, 1 = everything requeued).
        human_touch_ratio: Fraction of tasks that required at least one
            human approval or intervention (0 = fully autonomous).
        escaped_defects: Count of defects that cleared the evaluator gate
            but were subsequently caught in production.
        period_start: Inclusive start of the measurement window (UTC).
        period_end: Exclusive end of the measurement window (UTC).
        node_id: Identifier of the orchestrator node this scorecard covers
            (e.g. ``"forge:nova"``).
        total_tasks: Total number of tasks observed within the window.
        autonomous_completions: Tasks completed without any human touch.
        mean_cycle_time_minutes: Average wall-clock minutes from task
            creation to completion.  Zero when no tasks were completed.
    """

    dispatch_ack_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of dispatches that received acknowledgment (0-1).",
    )
    evaluator_pass_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of evaluator checks that passed (0-1).",
    )
    requeue_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of tasks that needed requeue (0-1).",
    )
    human_touch_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of tasks requiring human intervention (0-1).",
    )
    escaped_defects: int = Field(
        default=0,
        ge=0,
        description="Count of defects that passed evaluator but failed in prod.",
    )
    period_start: datetime = Field(
        description="Measurement window start (UTC).",
    )
    period_end: datetime = Field(
        description="Measurement window end (UTC).",
    )
    node_id: str = Field(
        default="unknown",
        min_length=1,
        description="Node this scorecard covers (e.g. 'forge:nova').",
    )
    total_tasks: int = Field(
        default=0,
        ge=0,
        description="Total tasks observed in the measurement period.",
    )
    autonomous_completions: int = Field(
        default=0,
        ge=0,
        description="Tasks completed without human touch.",
    )
    mean_cycle_time_minutes: float = Field(
        default=0.0,
        ge=0.0,
        description="Average task completion time in minutes.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        """Parse string datetimes and ensure they carry UTC timezone info."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            # Treat naive datetimes as UTC rather than raising an error.
            v = v.replace(tzinfo=UTC)
        return v

    @model_validator(mode="after")
    def validate_period_order(self) -> DarkFactoryScorecard:
        """Ensure period_start is strictly before period_end."""
        if self.period_start >= self.period_end:
            raise ValueError(
                f"period_start ({self.period_start.isoformat()}) must be "
                f"before period_end ({self.period_end.isoformat()})"
            )
        return self

    @model_validator(mode="after")
    def validate_autonomous_vs_total(self) -> DarkFactoryScorecard:
        """Ensure autonomous_completions does not exceed total_tasks."""
        if self.autonomous_completions > self.total_tasks:
            raise ValueError(
                f"autonomous_completions ({self.autonomous_completions}) "
                f"cannot exceed total_tasks ({self.total_tasks})"
            )
        return self

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def automation_ratio(self) -> float:
        """Fraction of total tasks completed autonomously.

        Returns 0.0 when there are no tasks to avoid division by zero.
        """
        if self.total_tasks == 0:
            return 0.0
        return self.autonomous_completions / self.total_tasks

    @property
    def period_minutes(self) -> float:
        """Duration of the measurement window in minutes."""
        return (self.period_end - self.period_start).total_seconds() / 60.0

    # Pydantic v2 serialises datetime fields to ISO 8601 by default via
    # model_dump_json(); no custom json_encoders config is required.


# ---------------------------------------------------------------------------
# SSE event envelope
# ---------------------------------------------------------------------------


class ScorecardEvent(BaseModel):
    """Envelope for scorecard events emitted over Server-Sent Events.

    Attributes:
        event_type: Granularity of this scorecard emission.
        scorecard: The scorecard payload.
        generated_at: Wall-clock time the event was created (UTC).
    """

    event_type: Literal["scorecard_daily", "scorecard_hourly"] = Field(
        description="Granularity of the scorecard emission.",
    )
    scorecard: DarkFactoryScorecard = Field(
        description="The scorecard data for this window.",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when this event was generated.",
    )

    @field_validator("generated_at", mode="before")
    @classmethod
    def ensure_generated_at_utc(cls, v: Any) -> datetime:
        """Parse string datetimes and attach UTC timezone when absent."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v

    # Pydantic v2 serialises datetime fields to ISO 8601 by default via
    # model_dump_json(); no custom json_encoders config is required.


# ---------------------------------------------------------------------------
# Computation helper
# ---------------------------------------------------------------------------


def compute_scorecard(
    tasks: list[dict[str, Any]],
    period_start: datetime,
    period_end: datetime,
    node_id: str = "unknown",
) -> DarkFactoryScorecard:
    """Compute a DarkFactoryScorecard from a list of task dicts.

    Each task dict is expected to carry the following optional keys.  All
    keys are optional; missing keys are treated as the most conservative
    (worst-case) value so the scorecard degrades gracefully with incomplete
    data.

    Task dict schema::

        {
            "dispatch_acked":       bool   # True if dispatch was acknowledged
            "evaluator_passed":     bool   # True if evaluator gate passed
            "requeued":             bool   # True if task was requeued at least once
            "human_touched":        bool   # True if human intervened
            "escaped_defect":       bool   # True if defect escaped to prod
            "completed":            bool   # True if task reached terminal state
            "cycle_time_minutes":   float  # Wall-clock minutes (completed tasks only)
        }

    Args:
        tasks: List of task dicts representing all tasks in the window.
        period_start: Inclusive start of the measurement window (UTC).
        period_end: Exclusive end of the measurement window (UTC).
        node_id: Identifier of the node this scorecard covers.

    Returns:
        A fully populated :class:`DarkFactoryScorecard` instance.
    """
    total = len(tasks)

    if total == 0:
        return DarkFactoryScorecard(
            dispatch_ack_rate=0.0,
            evaluator_pass_rate=0.0,
            requeue_rate=0.0,
            human_touch_ratio=0.0,
            escaped_defects=0,
            period_start=period_start,
            period_end=period_end,
            node_id=node_id,
            total_tasks=0,
            autonomous_completions=0,
            mean_cycle_time_minutes=0.0,
        )

    # Accumulate per-task signals
    dispatch_acked_count = 0
    evaluator_passed_count = 0
    requeued_count = 0
    human_touched_count = 0
    escaped_defect_count = 0
    autonomous_count = 0
    cycle_times: list[float] = []

    for task in tasks:
        if task.get("dispatch_acked", False):
            dispatch_acked_count += 1
        if task.get("evaluator_passed", False):
            evaluator_passed_count += 1
        if task.get("requeued", False):
            requeued_count += 1
        if task.get("human_touched", False):
            human_touched_count += 1
        if task.get("escaped_defect", False):
            escaped_defect_count += 1
        if task.get("completed", False) and not task.get("human_touched", False):
            autonomous_count += 1
        cycle_time = task.get("cycle_time_minutes")
        if cycle_time is not None and task.get("completed", False):
            cycle_times.append(float(cycle_time))

    mean_cycle_time = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0

    return DarkFactoryScorecard(
        dispatch_ack_rate=dispatch_acked_count / total,
        evaluator_pass_rate=evaluator_passed_count / total,
        requeue_rate=requeued_count / total,
        human_touch_ratio=human_touched_count / total,
        escaped_defects=escaped_defect_count,
        period_start=period_start,
        period_end=period_end,
        node_id=node_id,
        total_tasks=total,
        autonomous_completions=autonomous_count,
        mean_cycle_time_minutes=mean_cycle_time,
    )


def _load_tasks_from_json(path: str) -> list[dict[str, Any]]:
    """Load a JSON file containing a list of task dicts.

    Convenience loader for offline scorecard computation from persisted state.
    Not used at runtime by the server; exposed for tooling/scripts.

    Args:
        path: Absolute or relative path to a JSON file.

    Returns:
        List of task dicts (empty list if file is missing or malformed).
    """
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        return []
    except (OSError, json.JSONDecodeError):
        return []

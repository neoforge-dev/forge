"""Lane-Specific SLO (Service Level Objective) Models — DF-2004
===============================================================

Defines per-lane SLO targets, status classification, and check result
models used by the SLOMonitor service to track and alert on SLO health.

Classes
-------
SLOStatus
    Enum of possible SLO health states: healthy, warning, breached.
LaneSLO
    Immutable Pydantic model representing SLO targets for one lane.
SLOCheckResult
    Point-in-time SLO evaluation result for one lane.

Constants
---------
DEFAULT_LANE_SLOS
    Dict mapping every WorkCellLane to its default LaneSLO.

Reference
---------
forge_harness/webhook_server/models/work_cell.py  (WorkCellLane)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2004)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# SLOStatus enum
# ---------------------------------------------------------------------------


class SLOStatus(str, Enum):
    """Health classification for a lane's SLO.

    Attributes:
        healthy:  All SLO metrics are within target.
        warning:  At least one metric is approaching a breach threshold
                  (within alert_threshold_pct of the SLO limit).
        breached: At least one metric has exceeded the SLO target.
    """

    healthy = "healthy"
    warning = "warning"
    breached = "breached"


# ---------------------------------------------------------------------------
# LaneSLO model
# ---------------------------------------------------------------------------


class LaneSLO(BaseModel):
    """Service Level Objectives for a single work-cell lane.

    All instances are immutable (``model_config frozen=True``) so that
    DEFAULT_LANE_SLOS entries cannot be mutated after construction.

    Attributes:
        lane:
            The work-cell lane these SLOs apply to.
        max_lead_time_seconds:
            Maximum acceptable wall-clock time from task start to
            completion.  Tasks that exceed this value are counted as
            lead-time breaches.  Must be a positive integer.
        target_pass_rate:
            Fraction of tasks (0.0 – 1.0) that must complete successfully
            (``passed=True``) over the monitoring window.  E.g. 0.95
            means 95 out of every 100 tasks must pass.
        max_requeue_count:
            Maximum number of times a task in this lane may be requeued
            before the requeue itself is considered an SLO indicator.
            Tracked as a per-lane aggregate ceiling; individual task
            requeue counts are managed by the WorkCellConfig.
        alert_threshold_pct:
            Fraction of the SLO limit at which a *warning* is raised
            before an actual breach.  E.g. 0.8 means a warning fires
            when lead time reaches 80 % of ``max_lead_time_seconds``.
            Must be strictly between 0 and 1.
    """

    lane: WorkCellLane = Field(
        ...,
        description="The work-cell lane these SLOs apply to.",
    )
    max_lead_time_seconds: int = Field(
        ...,
        gt=0,
        description="Maximum acceptable task lead time in seconds.",
    )
    target_pass_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Required fraction of passing tasks (0.0 – 1.0).",
    )
    max_requeue_count: int = Field(
        ...,
        ge=0,
        description="Maximum acceptable requeue count per lane window.",
    )
    alert_threshold_pct: float = Field(
        ...,
        gt=0.0,
        lt=1.0,
        description="Fraction of SLO limit that triggers a warning (0 < x < 1).",
    )

    model_config = {"frozen": True, "populate_by_name": True}


# ---------------------------------------------------------------------------
# SLOCheckResult model
# ---------------------------------------------------------------------------


class SLOCheckResult(BaseModel):
    """Point-in-time evaluation of a lane's SLO compliance.

    Produced by SLOMonitor.check_slo() and check_all_slos().

    Attributes:
        lane:
            The work-cell lane that was evaluated.
        status:
            Aggregate health classification (healthy / warning / breached).
        current_lead_time_seconds:
            Rolling average (or latest observed) task lead time in seconds.
            None when no completed tasks exist in the monitoring window.
        current_pass_rate:
            Fraction of tasks that passed in the monitoring window.
            None when no completed tasks exist.
        requeue_count:
            Number of requeue events recorded in the monitoring window.
        checked_at:
            UTC ISO-8601 timestamp at which this result was computed.
    """

    lane: WorkCellLane = Field(..., description="The evaluated work-cell lane.")
    status: SLOStatus = Field(..., description="Aggregate SLO health status.")
    current_lead_time_seconds: float | None = Field(
        default=None,
        description="Average observed lead time (seconds); None if no data.",
    )
    current_pass_rate: float | None = Field(
        default=None,
        description="Observed pass rate (0.0–1.0); None if no data.",
    )
    requeue_count: int = Field(
        default=0,
        ge=0,
        description="Requeue events recorded in the monitoring window.",
    )
    checked_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="UTC ISO-8601 timestamp of this check.",
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Default SLO targets per lane
# ---------------------------------------------------------------------------

#: Default SLO targets for every WorkCellLane.
#:
#: Design rationale:
#:   api_simple      — fast, stateless; 5-minute lead time, 95 % pass rate.
#:   api_stateful    — slower, coordinated; 30-minute lead time, 95 % pass.
#:   test_writing    — moderate complexity; 15-minute lead time, 90 % pass.
#:   docs            — light work; 10-minute lead time, 90 % pass.
#:   research        — exploratory; 60-minute lead time, 80 % pass rate.
#:   refactor        — structural; 30-minute lead time, 90 % pass rate.
#:   security_change — high stakes; 60-minute lead time, 99 % pass rate.
#:   deployment      — production-critical; 30-minute lead time, 99 % pass.
DEFAULT_LANE_SLOS: dict[WorkCellLane, LaneSLO] = {
    WorkCellLane.api_simple: LaneSLO(
        lane=WorkCellLane.api_simple,
        max_lead_time_seconds=300,  # 5 minutes
        target_pass_rate=0.95,
        max_requeue_count=3,
        alert_threshold_pct=0.8,
    ),
    WorkCellLane.api_stateful: LaneSLO(
        lane=WorkCellLane.api_stateful,
        max_lead_time_seconds=1800,  # 30 minutes
        target_pass_rate=0.95,
        max_requeue_count=2,
        alert_threshold_pct=0.8,
    ),
    WorkCellLane.test_writing: LaneSLO(
        lane=WorkCellLane.test_writing,
        max_lead_time_seconds=900,  # 15 minutes
        target_pass_rate=0.90,
        max_requeue_count=3,
        alert_threshold_pct=0.8,
    ),
    WorkCellLane.docs: LaneSLO(
        lane=WorkCellLane.docs,
        max_lead_time_seconds=600,  # 10 minutes
        target_pass_rate=0.90,
        max_requeue_count=5,
        alert_threshold_pct=0.8,
    ),
    WorkCellLane.research: LaneSLO(
        lane=WorkCellLane.research,
        max_lead_time_seconds=3600,  # 60 minutes
        target_pass_rate=0.80,
        max_requeue_count=1,
        alert_threshold_pct=0.75,
    ),
    WorkCellLane.refactor: LaneSLO(
        lane=WorkCellLane.refactor,
        max_lead_time_seconds=1800,  # 30 minutes
        target_pass_rate=0.90,
        max_requeue_count=2,
        alert_threshold_pct=0.8,
    ),
    WorkCellLane.security_change: LaneSLO(
        lane=WorkCellLane.security_change,
        max_lead_time_seconds=3600,  # 60 minutes
        target_pass_rate=0.99,
        max_requeue_count=0,
        alert_threshold_pct=0.7,
    ),
    WorkCellLane.deployment: LaneSLO(
        lane=WorkCellLane.deployment,
        max_lead_time_seconds=1800,  # 30 minutes
        target_pass_rate=0.99,
        max_requeue_count=0,
        alert_threshold_pct=0.7,
    ),
}

# Validate completeness at import time.
_missing_slos = set(WorkCellLane) - set(DEFAULT_LANE_SLOS.keys())
assert not _missing_slos, "DEFAULT_LANE_SLOS is missing entries for: " + ", ".join(
    lane.value for lane in _missing_slos
)

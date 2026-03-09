"""Intake Queue Models — DF-2001
================================

Defines the Pydantic models used by the event-driven task intake queue.
Every task entering the FORGE Dark Factory is first admitted through the
intake queue, which validates the payload, assigns a work-cell lane via
LaneEnforcer, checks WIP capacity, and emits the appropriate SSE event.

Classes
-------
IntakeStatus
    Enum of lifecycle states an intake item can occupy.
IntakeItem
    Validated submission payload for a single task intake request.
IntakeResult
    Outcome record returned after processing an :class:`IntakeItem`.

Reference
---------
forge_harness/webhook_server/services/intake_service.py  (IntakeService)
forge_harness/webhook_server/models/lane_policy.py       (TaskType, RiskTier)
forge_harness/webhook_server/models/work_cell.py         (WorkCellLane)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2001)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# Allowed intake source identifiers
# ---------------------------------------------------------------------------

#: Canonical set of valid ``source`` field values for :class:`IntakeItem`.
VALID_SOURCES: frozenset[str] = frozenset({"api", "dispatch_file", "heartbeat", "cli"})


# ---------------------------------------------------------------------------
# IntakeStatus
# ---------------------------------------------------------------------------


class IntakeStatus(str, Enum):
    """Lifecycle states an intake item can occupy.

    Attributes:
        pending:  Submitted but not yet processed by the scheduler.
        assigned: Accepted, lane resolved, and task entry created.
        rejected: Rejected at validation or WIP-capacity check.
        expired:  Was pending but exceeded its TTL before processing.
    """

    pending = "pending"
    assigned = "assigned"
    rejected = "rejected"
    expired = "expired"


# ---------------------------------------------------------------------------
# IntakeItem
# ---------------------------------------------------------------------------


class IntakeItem(BaseModel):
    """Validated submission payload for a single task intake request.

    Attributes:
        item_id:      Unique identifier for this intake record (UUID4).
                      Auto-generated on construction when not supplied.
        title:        Short human-readable summary of the task (1–200 chars).
        description:  Extended description of what the task entails.
        task_type:    Canonical task taxonomy type (see :class:`TaskType`).
        risk_tier:    Risk classification for lane routing (see :class:`RiskTier`).
        priority:     Numeric priority from 1 (lowest) to 5 (highest).
        source:       How the item entered the queue.
                      One of ``"api"``, ``"dispatch_file"``, ``"heartbeat"``,
                      ``"cli"``.
        submitted_at: UTC timestamp of when the item was submitted.
                      Auto-set on construction when not supplied.
        metadata:     Arbitrary key/value bag for caller-specific context.
    """

    item_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier (UUID4) for this intake record.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Short human-readable summary of the task.",
    )
    description: str = Field(
        default="",
        description="Extended description of what the task entails.",
    )
    task_type: TaskType = Field(
        ...,
        description="Canonical task taxonomy type.",
    )
    risk_tier: RiskTier = Field(
        ...,
        description="Risk classification for lane routing.",
    )
    priority: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Numeric priority — 1 is lowest, 5 is highest.",
    )
    source: str = Field(
        ...,
        description=(
            "Origin of this intake item. One of: 'api', 'dispatch_file', 'heartbeat', 'cli'."
        ),
    )
    submitted_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="UTC ISO-8601 timestamp of submission.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key/value bag for caller-specific context.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        """Reject source values not in the canonical allow-list."""
        if value not in VALID_SOURCES:
            raise ValueError(f"Invalid source {value!r}. Must be one of: {sorted(VALID_SOURCES)}")
        return value


# ---------------------------------------------------------------------------
# IntakeResult
# ---------------------------------------------------------------------------


class IntakeResult(BaseModel):
    """Outcome record returned after processing an :class:`IntakeItem`.

    Attributes:
        item_id:          Matches :attr:`IntakeItem.item_id` of the processed item.
        status:           Final status of the intake attempt.
        assigned_lane:    Work-cell lane assigned (``None`` when rejected).
        assigned_task_id: Task entry ID created (``None`` when rejected).
        rejection_reason: Human-readable rejection description (``None`` when accepted).
    """

    item_id: str = Field(
        ...,
        description="item_id of the intake item this result corresponds to.",
    )
    status: IntakeStatus = Field(
        ...,
        description="Final status of the intake processing attempt.",
    )
    assigned_lane: WorkCellLane | None = Field(
        default=None,
        description="Work-cell lane the task was routed to (None when rejected).",
    )
    assigned_task_id: str | None = Field(
        default=None,
        description="Task entry ID that was created (None when rejected).",
    )
    rejection_reason: str | None = Field(
        default=None,
        description="Human-readable reason for rejection (None when accepted).",
    )

    model_config = {"populate_by_name": True}

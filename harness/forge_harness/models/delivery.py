"""Delivery State Model for cross-node message tracking in FORGE xnode relay.

Defines the canonical delivery states and legally valid transitions for
messages sent between nodes via ``.forge/xnode/lead-inbox/`` JSONL files
and acknowledged via ``.forge/xnode/acks/`` JSON files.

State machine overview
-----------------------

    queued ──► dispatched ──► delivered ──► acked
       │            │              │
       │            │              └──► nacked
       │            │
       │            └──► failed
       │
       └──────────────────────────────► expired (TTL elapsed before any progress)

Any non-terminal state may also transition to ``expired`` or ``failed`` as
out-of-band failure paths.

Terminal states: acked, nacked, failed, expired
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class DeliveryState(str, Enum):
    """Canonical delivery states for a cross-node xnode message.

    String-valued so that states round-trip through JSON without extra
    serialisation plumbing (matches the pattern used by TaskLifecycleState).
    """

    # Pre-dispatch states
    QUEUED = "queued"  # Message created locally; not yet sent to target node.
    DISPATCHED = "dispatched"  # Message written to lead-inbox or emitted via realtime.

    # Receipt states
    DELIVERED = "delivered"  # Target node listener has received and written the message.
    ACKED = "acked"  # Target agent explicitly acknowledged (status=ack).
    NACKED = "nacked"  # Target agent explicitly rejected (status=nack).

    # Failure / expiry states (terminal)
    FAILED = "failed"  # Delivery attempt failed definitively (I/O error, etc.).
    EXPIRED = "expired"  # TTL elapsed before the message was acked/nacked.


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

#: Maps each state to the frozenset of states it is legally allowed to
#: transition into.  An empty frozenset marks a terminal state.
VALID_DELIVERY_TRANSITIONS: dict[DeliveryState, frozenset[DeliveryState]] = {
    # Pre-dispatch lane
    DeliveryState.QUEUED: frozenset(
        {
            DeliveryState.DISPATCHED,
            DeliveryState.FAILED,
            DeliveryState.EXPIRED,
        }
    ),
    # In-flight lane
    DeliveryState.DISPATCHED: frozenset(
        {
            DeliveryState.DELIVERED,
            DeliveryState.FAILED,
            DeliveryState.EXPIRED,
        }
    ),
    # Receipt lane
    DeliveryState.DELIVERED: frozenset(
        {
            DeliveryState.ACKED,
            DeliveryState.NACKED,
            DeliveryState.EXPIRED,
        }
    ),
    # --- Terminal states (empty transition sets) ---
    DeliveryState.ACKED: frozenset(),
    DeliveryState.NACKED: frozenset(),
    DeliveryState.FAILED: frozenset(),
    DeliveryState.EXPIRED: frozenset(),
}

#: Convenience set of terminal states (no valid outbound transitions).
TERMINAL_DELIVERY_STATES: frozenset[DeliveryState] = frozenset(
    state for state, targets in VALID_DELIVERY_TRANSITIONS.items() if not targets
)


# ---------------------------------------------------------------------------
# Contract helpers
# ---------------------------------------------------------------------------


class DeliveryTransitionError(ValueError):
    """Raised when a delivery state transition violates the contract.

    Attributes:
        from_state: The state the delivery record is currently in.
        to_state:   The state that was requested.
        allowed:    The set of states that would have been valid.
    """

    def __init__(
        self,
        from_state: DeliveryState,
        to_state: DeliveryState,
        allowed: frozenset[DeliveryState],
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed

        if allowed:
            allowed_names = ", ".join(sorted(s.value for s in allowed))
            detail = f"allowed next states: [{allowed_names}]"
        else:
            detail = "state is terminal; no further transitions are permitted"

        super().__init__(
            f"Invalid delivery transition {from_state.value!r} -> {to_state.value!r}: {detail}"
        )


def validate_delivery_transition(
    from_state: DeliveryState | str,
    to_state: DeliveryState | str,
) -> bool:
    """Assert that a delivery state transition is valid under the contract.

    Args:
        from_state: Current delivery state (``DeliveryState`` or its string value).
        to_state:   Requested next delivery state.

    Returns:
        ``True`` when the transition is valid.

    Raises:
        DeliveryTransitionError: When the requested transition is not in the contract.
        ValueError: When either argument is not a recognised ``DeliveryState`` value.
    """
    if not isinstance(from_state, DeliveryState):
        from_state = DeliveryState(from_state)  # raises ValueError for unknown values
    if not isinstance(to_state, DeliveryState):
        to_state = DeliveryState(to_state)  # raises ValueError for unknown values

    allowed = VALID_DELIVERY_TRANSITIONS[from_state]
    if to_state not in allowed:
        raise DeliveryTransitionError(from_state, to_state, allowed)

    return True


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DeliveryRecord(BaseModel):
    """Pydantic model representing a single cross-node message delivery record.

    Covers the full lifecycle from initial queuing through to ack/nack/failure.
    Field names map directly to the envelope shape produced by ``forge lead send``
    and consumed by ``forge lead ack`` / ``forge xnode relay``.
    """

    # Identity
    message_id: str = Field(
        ..., description="Unique message identifier (e.g. msg_20260222_abc12345)."
    )
    task_id: str | None = Field(None, description="Associated task identifier (e.g. IS-219).")

    # Topology
    source_node: str = Field(..., description="Hostname of the sending node (e.g. nova).")
    target_node: str = Field(..., description="Hostname of the target node (e.g. sati).")
    source_agent: str | None = Field(None, description="Source agent window (e.g. orchestrator).")
    target_agent: str | None = Field(None, description="Target agent window.")

    # State
    state: DeliveryState = Field(DeliveryState.QUEUED, description="Current delivery state.")

    # Timestamps
    created_at: datetime = Field(..., description="When the DeliveryRecord was created (UTC).")
    dispatched_at: datetime | None = Field(
        None, description="When the message was written to the inbox or emitted."
    )
    delivered_at: datetime | None = Field(
        None, description="When the target node received the message."
    )
    acked_at: datetime | None = Field(None, description="When the ack/nack was recorded.")
    expires_at: datetime | None = Field(None, description="UTC expiry time from the message TTL.")

    # Diagnostics
    error: str | None = Field(None, description="Error detail when state is 'failed'.")
    payload_summary: str = Field(
        "",
        description="First 120 characters of the message content for quick inspection.",
        max_length=120,
    )

    @field_validator("payload_summary", mode="before")
    @classmethod
    def truncate_payload_summary(cls, v: object) -> str:
        """Truncate payload_summary to 120 characters if it is longer."""
        if isinstance(v, str) and len(v) > 120:
            return v[:120]
        return str(v) if v is not None else ""

    @model_validator(mode="after")
    def check_error_on_failed(self) -> DeliveryRecord:
        """Warn (via convention) that error should be set when state is failed.

        We intentionally do not hard-fail here because records may be
        constructed incrementally — callers are responsible for populating
        error before persisting.
        """
        return self

    model_config = {"use_enum_values": False}


class DeliveryStats(BaseModel):
    """Aggregated delivery statistics for a set of DeliveryRecord instances.

    Intended for dashboards and health checks.  Build via
    ``DeliveryStats.from_records(records)``.
    """

    total: int = Field(0, description="Total number of delivery records.")
    by_state: dict[str, int] = Field(
        default_factory=dict,
        description="Count of records per DeliveryState value.",
    )
    mean_delivery_seconds: float | None = Field(
        None,
        description=(
            "Mean seconds from created_at to acked_at/nacked_at for "
            "terminal records that have both timestamps."
        ),
    )

    @classmethod
    def from_records(cls, records: list[DeliveryRecord]) -> DeliveryStats:
        """Compute statistics from a list of DeliveryRecord instances.

        Args:
            records: Iterable of DeliveryRecord objects.

        Returns:
            Populated DeliveryStats instance.
        """
        by_state: dict[str, int] = {s.value: 0 for s in DeliveryState}
        delivery_times: list[float] = []

        for record in records:
            state_value = (
                record.state.value if isinstance(record.state, DeliveryState) else str(record.state)
            )
            by_state[state_value] = by_state.get(state_value, 0) + 1

            # Compute delivery time for records that were fully resolved
            if record.acked_at is not None and record.created_at is not None:
                delta: timedelta = record.acked_at - record.created_at
                delivery_times.append(delta.total_seconds())

        mean_delivery: float | None = None
        if delivery_times:
            mean_delivery = sum(delivery_times) / len(delivery_times)

        return cls(
            total=len(records),
            by_state={k: v for k, v in by_state.items() if v > 0},
            mean_delivery_seconds=mean_delivery,
        )

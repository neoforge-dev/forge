"""TaskLease Model for FORGE Control Plane (CP-2001).

Defines the canonical lease states and legally valid transitions for task
ownership and path locking across the FORGE multi-agent fleet.

A lease represents exclusive ownership of a task by a specific agent on a
specific node.  It controls:

- **Task ownership** — which agent is responsible for a task at any moment.
- **Path locking** — mutual exclusion over a file path being edited.
- **Renewal** — extending an active lease before it times out.
- **Requeue** — returning a task to the scheduler after release or expiry.

State machine overview
-----------------------

    unclaimed ──► claimed ──► active ──► renewing ──► active  (loop)
        ▲             │          │            │
        │             │          │            └──► expired ──► requeued ──► claimed
        │             │          └──────────────► releasing ──► requeued
        │             │                                      └──► unclaimed
        │             └──► releasing ──► requeued
        │             └──► expired  ──► requeued
        └──────────────────────────────────────── (from requeued / releasing)

Terminal states: none (every state can eventually be reclaimed or released).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class LeaseState(str, Enum):
    """Canonical lease states for a FORGE control-plane task lease.

    String-valued so that states round-trip through JSON without extra
    serialisation plumbing (consistent with DeliveryState and TaskLifecycleState).
    """

    UNCLAIMED = "unclaimed"  # No agent holds this lease; task is available.
    CLAIMED = "claimed"  # An agent has staked a claim; not yet executing.
    ACTIVE = "active"  # Agent is actively working on the task.
    RENEWING = "renewing"  # Lease renewal handshake is in progress.
    RELEASING = "releasing"  # Agent is gracefully releasing the lease.
    EXPIRED = "expired"  # Lease TTL elapsed without renewal.
    REQUEUED = "requeued"  # Task returned to the scheduler queue.


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

#: Maps each state to the frozenset of states it is legally allowed to
#: transition into.  The graph has no terminal states — every lease can
#: eventually be reclaimed.
VALID_LEASE_TRANSITIONS: dict[LeaseState, frozenset[LeaseState]] = {
    LeaseState.UNCLAIMED: frozenset(
        {
            LeaseState.CLAIMED,
        }
    ),
    LeaseState.CLAIMED: frozenset(
        {
            LeaseState.ACTIVE,
            LeaseState.RELEASING,
            LeaseState.EXPIRED,
        }
    ),
    LeaseState.ACTIVE: frozenset(
        {
            LeaseState.RENEWING,
            LeaseState.RELEASING,
            LeaseState.EXPIRED,
        }
    ),
    LeaseState.RENEWING: frozenset(
        {
            LeaseState.ACTIVE,
            LeaseState.EXPIRED,
        }
    ),
    LeaseState.RELEASING: frozenset(
        {
            LeaseState.REQUEUED,
            LeaseState.UNCLAIMED,
        }
    ),
    LeaseState.EXPIRED: frozenset(
        {
            LeaseState.REQUEUED,
            LeaseState.UNCLAIMED,
        }
    ),
    LeaseState.REQUEUED: frozenset(
        {
            LeaseState.CLAIMED,
        }
    ),
}


# ---------------------------------------------------------------------------
# Contract helpers
# ---------------------------------------------------------------------------


class LeaseTransitionError(Exception):
    """Raised when a lease state transition violates the control-plane contract.

    Attributes:
        from_state: The state the lease is currently in.
        to_state:   The state that was requested.
        allowed:    The set of states that would have been valid.
    """

    def __init__(
        self,
        from_state: LeaseState,
        to_state: LeaseState,
        allowed: frozenset[LeaseState],
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
            f"Invalid lease transition {from_state.value!r} -> {to_state.value!r}: {detail}"
        )


def validate_lease_transition(
    from_state: LeaseState | str,
    to_state: LeaseState | str,
) -> bool:
    """Assert that a lease state transition is valid under the contract.

    Args:
        from_state: Current lease state (``LeaseState`` or its string value).
        to_state:   Requested next lease state.

    Returns:
        ``True`` when the transition is valid.

    Raises:
        LeaseTransitionError: When the requested transition is not in the contract.
        ValueError: When either argument is not a recognised ``LeaseState`` value.
    """
    if not isinstance(from_state, LeaseState):
        from_state = LeaseState(from_state)  # raises ValueError for unknown values
    if not isinstance(to_state, LeaseState):
        to_state = LeaseState(to_state)  # raises ValueError for unknown values

    allowed = VALID_LEASE_TRANSITIONS[from_state]
    if to_state not in allowed:
        raise LeaseTransitionError(from_state, to_state, allowed)

    return True


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TaskLease(BaseModel):
    """Pydantic model representing exclusive task ownership by a fleet agent.

    A ``TaskLease`` tracks which agent on which node holds the right to work
    on a particular task, for how long, and which file path (if any) is under
    exclusive edit.

    Lifecycle methods (``claim``, ``renew``, ``release``) enforce the
    ``VALID_LEASE_TRANSITIONS`` contract and raise ``LeaseTransitionError`` on
    any illegal move.
    """

    # Identity
    lease_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique lease identifier (UUID4).",
    )
    task_id: str = Field(..., description="Task identifier this lease belongs to.")

    # Ownership
    owner_node: str = Field(
        default="",
        description="Hostname of the owning node (e.g. 'nova', 'prya').",
    )
    owner_agent: str = Field(
        default="",
        description="Agent window identifier (e.g. 'forge:claude', 'forge:glm').",
    )

    # State
    state: LeaseState = Field(
        default=LeaseState.UNCLAIMED,
        description="Current lease state.",
    )

    # Timestamps
    claimed_at: datetime | None = Field(
        None,
        description="UTC timestamp when the lease was first claimed.",
    )
    expires_at: datetime | None = Field(
        None,
        description="UTC timestamp when the current lease grant expires.",
    )

    # Lease configuration
    lease_duration_seconds: int = Field(
        default=300,
        description="Lease TTL in seconds (default: 5 minutes).",
        gt=0,
    )
    renewals: int = Field(
        default=0,
        description="Number of times this lease has been renewed.",
        ge=0,
    )
    max_renewals: int = Field(
        default=10,
        description="Maximum number of renewals allowed before forced release.",
        ge=0,
    )

    # Path locking
    path_lock: str | None = Field(
        None,
        description="File path under exclusive edit lock (e.g. 'src/api/routes.py').",
    )

    # Extensibility
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata for higher-level systems.",
    )

    # Audit timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when this TaskLease record was created.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the last state mutation.",
    )

    model_config = {"use_enum_values": False}

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def is_expired(self) -> bool:
        """Return ``True`` if the current time has passed ``expires_at``.

        Returns ``False`` when ``expires_at`` is ``None`` (lease has not been
        granted yet and therefore cannot have expired).
        """
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    # ------------------------------------------------------------------
    # State-machine methods
    # ------------------------------------------------------------------

    def claim(self, node: str, agent: str) -> None:
        """Transition the lease to ``claimed`` and record the owner.

        Args:
            node:  Hostname of the claiming node (e.g. ``"nova"``).
            agent: Agent identifier (e.g. ``"forge:claude"``).

        Raises:
            LeaseTransitionError: If the current state does not allow a
                transition to ``claimed``.
        """
        validate_lease_transition(self.state, LeaseState.CLAIMED)
        self.owner_node = node
        self.owner_agent = agent
        self.claimed_at = datetime.now(UTC)
        self.expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_duration_seconds)
        self.state = LeaseState.CLAIMED
        self.updated_at = datetime.now(UTC)

    def renew(self, duration: int | None = None) -> None:
        """Extend the lease expiry and increment the renewal counter.

        Transitions through ``renewing`` back to ``active``.  Callers should
        check ``renewals < max_renewals`` before calling, or handle the
        ``LeaseTransitionError`` when the lease is not in a renewable state.

        Args:
            duration: Optional override for ``lease_duration_seconds``.  When
                ``None`` the current ``lease_duration_seconds`` is reused.

        Raises:
            LeaseTransitionError: If the current state does not allow a
                transition to ``renewing`` or from ``renewing`` back to
                ``active``.
        """
        validate_lease_transition(self.state, LeaseState.RENEWING)
        self.state = LeaseState.RENEWING
        self.updated_at = datetime.now(UTC)

        effective_duration = duration if duration is not None else self.lease_duration_seconds
        validate_lease_transition(self.state, LeaseState.ACTIVE)
        self.expires_at = datetime.now(UTC) + timedelta(seconds=effective_duration)
        self.renewals += 1
        self.state = LeaseState.ACTIVE
        self.updated_at = datetime.now(UTC)

    def release(self) -> None:
        """Begin a graceful release of the lease and clear the path lock.

        Transitions the lease to ``releasing`` and clears ``path_lock``.
        The caller is responsible for subsequently moving to ``requeued`` or
        ``unclaimed`` once the release handshake completes.

        Raises:
            LeaseTransitionError: If the current state does not allow a
                transition to ``releasing``.
        """
        validate_lease_transition(self.state, LeaseState.RELEASING)
        self.path_lock = None
        self.state = LeaseState.RELEASING
        self.updated_at = datetime.now(UTC)

    model_config = {"use_enum_values": False}


# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------


class LeaseStats(BaseModel):
    """Aggregated statistics over a collection of ``TaskLease`` instances.

    Intended for fleet dashboards and health-check endpoints.  Build via
    ``LeaseStats.from_leases(leases)``.
    """

    total: int = Field(0, description="Total number of leases.")
    by_state: dict[str, int] = Field(
        default_factory=dict,
        description="Count of leases per LeaseState value.",
    )
    active_path_locks: int = Field(
        0,
        description="Number of leases currently holding a path_lock.",
    )
    expired_count: int = Field(
        0,
        description="Number of leases in the 'expired' state.",
    )
    mean_renewals: float | None = Field(
        None,
        description="Mean renewal count across all leases that have been renewed at least once.",
    )

    @classmethod
    def from_leases(cls, leases: list[TaskLease]) -> LeaseStats:
        """Compute statistics from a list of ``TaskLease`` instances.

        Args:
            leases: Collection of ``TaskLease`` objects to aggregate.

        Returns:
            Populated ``LeaseStats`` instance.
        """
        by_state: dict[str, int] = {s.value: 0 for s in LeaseState}
        active_path_locks = 0
        expired_count = 0
        renewal_counts: list[int] = []

        for lease in leases:
            state_value = (
                lease.state.value if isinstance(lease.state, LeaseState) else str(lease.state)
            )
            by_state[state_value] = by_state.get(state_value, 0) + 1

            if lease.path_lock is not None:
                active_path_locks += 1

            if (
                isinstance(lease.state, LeaseState) and lease.state is LeaseState.EXPIRED
            ) or state_value == "expired":
                expired_count += 1

            if lease.renewals > 0:
                renewal_counts.append(lease.renewals)

        mean_renewals: float | None = None
        if renewal_counts:
            mean_renewals = sum(renewal_counts) / len(renewal_counts)

        return cls(
            total=len(leases),
            by_state={k: v for k, v in by_state.items() if v > 0},
            active_path_locks=active_path_locks,
            expired_count=expired_count,
            mean_renewals=mean_renewals,
        )

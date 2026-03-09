"""Path Lock Models — DF-3002
============================

Pydantic models for the path-lock subsystem that prevents conflicting agents
from being recommended for tasks whose file paths are already locked by another
agent.

Classes
-------
PathLockType
    Enum of available lock modes (exclusive vs shared).
PathLock
    A single acquired lock record binding an agent to a file-system path.
LockConflict
    Describes a conflict detected between a requested lock and an existing one.

Reference
---------
forge_harness/webhook_server/services/path_lock_registry.py
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-3002)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# PathLockType enum
# ---------------------------------------------------------------------------


class PathLockType(str, Enum):
    """Mode used when acquiring a path lock.

    Attributes:
        exclusive: Only one agent may hold a lock on this path.  Any other
                   lock request (exclusive or shared) is rejected while an
                   exclusive lock is held.
        shared:    Multiple agents may concurrently hold shared locks on the
                   same path.  A new shared lock is only rejected when an
                   exclusive lock is already held by a different agent.
    """

    exclusive = "exclusive"
    shared = "shared"


# ---------------------------------------------------------------------------
# PathLock model
# ---------------------------------------------------------------------------


class PathLock(BaseModel):
    """A single acquired path lock binding an agent to a file-system path.

    Attributes:
        lock_id
            Unique identifier for this lock instance (UUID4).
        agent_id
            Stable, unique identifier of the agent that holds the lock
            (e.g. ``"forge:nova"``).
        path
            File-system path (or logical resource path) that is locked.
            The path is treated as a plain string; callers are responsible
            for normalising paths before acquiring locks.
        lock_type
            The mode of the lock — :attr:`PathLockType.exclusive` or
            :attr:`PathLockType.shared`.
        acquired_at
            UTC timestamp when the lock was successfully acquired.
        expires_at
            Optional UTC timestamp after which the lock is considered
            expired and eligible for :meth:`cleanup_expired` removal.
            ``None`` means the lock does not expire automatically.
        task_id
            Optional identifier of the task that triggered the lock
            acquisition.  Used for cross-referencing with the task ledger.
    """

    model_config = ConfigDict(frozen=True)

    lock_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique lock identifier (UUID4).",
    )
    agent_id: str = Field(..., description="Agent that holds the lock.")
    path: str = Field(..., description="File-system or logical path being locked.")
    lock_type: PathLockType = Field(
        default=PathLockType.exclusive,
        description="Lock mode: exclusive blocks all; shared allows other shared.",
    )
    acquired_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the lock was acquired.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the lock expires.  None = no expiry.",
    )
    task_id: str | None = Field(
        default=None,
        description="Optional task identifier associated with this lock.",
    )


# ---------------------------------------------------------------------------
# LockConflict model
# ---------------------------------------------------------------------------


class LockConflict(BaseModel):
    """Describes a conflict between a requested lock and an existing lock.

    Returned by
    :meth:`~forge_harness.webhook_server.services.path_lock_registry
    .PathLockRegistry.check_conflicts` to identify which existing locks
    would block a new acquisition.

    Attributes:
        lock_id
            Identifier of the existing lock that causes the conflict.
        agent_id
            Agent that holds the conflicting lock.
        path
            The locked path that conflicts with the requested path.
        conflict_reason
            Human-readable explanation of why the conflict exists (e.g.
            ``"exclusive lock held by agent-x"``).
    """

    model_config = ConfigDict(frozen=True)

    lock_id: str = Field(..., description="Identifier of the conflicting lock.")
    agent_id: str = Field(..., description="Agent holding the conflicting lock.")
    path: str = Field(..., description="Path that has the conflicting lock.")
    conflict_reason: str = Field(
        ...,
        description="Human-readable reason for the conflict.",
    )

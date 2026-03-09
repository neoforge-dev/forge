"""Human Gate Policy — DF-4002
================================

Implements hard-gate enforcement for sensitive work-cell lanes.  A hard gate
means that no agent or automated process may advance the task to a terminal
state without an explicit human approval record.

This module answers the question: **"May this task proceed past the gate?"**

Unlike the :class:`LanePolicyEnforcer`, which answers *should* a task have
human review, the :class:`HumanGatePolicy` *actively enforces* the gate —
it raises :class:`HumanGateViolationError` when a bypass is attempted without a
valid override.

Hard-gated lanes (by default)
-------------------------------
- ``security_change``  — auth, JWT, encryption, API keys, payment flows
- ``deployment``       — any release to staging or production

Override mechanism
------------------
A gate may be bypassed only when **all three** conditions are met:

1. ``force_override=True`` is explicitly passed.
2. ``override_reason`` is a non-empty string (minimum 20 characters).
3. The caller has a valid ``overrider_id``.

Every override attempt (permitted or rejected) is written to an in-memory
audit log accessible via :meth:`HumanGatePolicy.get_audit_log`.  Callers
responsible for persistence should drain the log and write it to durable
storage.

Singleton
---------
Use :func:`get_human_gate_policy` to obtain the shared instance.
Use :func:`reset_human_gate_policy` in tests to start from a clean slate.

Usage
-----
::

    from forge_harness.dark_factory.human_gate_policy import (
        get_human_gate_policy,
        HumanGateViolationError,
    )

    gate = get_human_gate_policy()

    # Normal path — enforcement raises if gate not satisfied
    try:
        result = gate.enforce(task, approved=False)
    except HumanGateViolationError as exc:
        # Task is blocked; route to human approval queue
        ...

    # Emergency override with full audit trail
    result = gate.enforce(
        task,
        approved=False,
        force_override=True,
        override_reason="Time-critical incident; evaluator confirmed safe. Ref: FORGE#999",
        overrider_id="alice@example.com",
    )
    assert result.status == GateStatus.override_permitted

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-4002)
forge_harness/dark_factory/lane_policy_enforcer.py  (LanePolicyEnforcer)
forge_harness/webhook_server/models/evaluator_override.py  (OverridePolicy)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import LaneResolver, WorkCellLane

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum character length for an override reason.
_MIN_OVERRIDE_REASON_LEN: int = 20

#: Work-cell lanes that are hard-gated by default.
#: Any agent attempting to auto-complete tasks in these lanes without explicit
#: human approval triggers a :class:`HumanGateViolationError`.
DEFAULT_HARD_GATED_LANES: frozenset[WorkCellLane] = frozenset(
    {
        WorkCellLane.security_change,
        WorkCellLane.deployment,
    }
)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class GateStatus(str, Enum):
    """Outcome of a :meth:`HumanGatePolicy.enforce` call.

    Attributes:
        not_gated:          The lane is not a hard gate; task may proceed freely.
        approved:           The task is hard-gated but a valid human approval was
                            provided; the gate is satisfied.
        blocked:            The task is hard-gated, no approval was provided, and
                            no override was attempted.  Task must wait for a human.
        override_permitted: The task is hard-gated, no approval was provided, but
                            a valid override was accepted.  The override is fully
                            logged in the audit trail.
        override_rejected:  An override was attempted but was rejected (e.g.
                            missing ``override_reason`` or ``overrider_id``).
                            A :class:`HumanGateViolationError` is raised.
    """

    not_gated = "not_gated"
    approved = "approved"
    blocked = "blocked"
    override_permitted = "override_permitted"
    override_rejected = "override_rejected"


@dataclass(frozen=True)
class GateResult:
    """Immutable result of a :meth:`HumanGatePolicy.enforce` call.

    Attributes:
        task_id:         Unique identifier of the task being evaluated.
        work_cell_lane:  The granular work-cell lane the task is assigned to.
        status:          :class:`GateStatus` describing the outcome.
        is_gated:        True when the lane is a hard gate.
        may_proceed:     True when the task is allowed to proceed past the gate
                         (i.e. ``not_gated``, ``approved``, or
                         ``override_permitted``).
        override_id:     UUID of the override record, when an override was used.
                         ``None`` otherwise.
        override_reason: The override justification, when applicable.
        overrider_id:    Identity of the person who issued the override.
        evaluated_at:    UTC timestamp of the gate evaluation.
    """

    task_id: str
    work_cell_lane: WorkCellLane
    status: GateStatus
    is_gated: bool
    may_proceed: bool
    override_id: str | None = None
    override_reason: str | None = None
    overrider_id: str | None = None
    evaluated_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )


@dataclass
class AuditEntry:
    """Single record in the human gate audit log.

    Attributes:
        entry_id:        UUID identifying this specific audit record.
        task_id:         The task being evaluated.
        work_cell_lane:  The lane the task is assigned to.
        status:          The :class:`GateStatus` outcome.
        approved:        Whether the caller indicated human approval was
                         already obtained.
        force_override:  Whether a force override was attempted.
        override_reason: The justification provided with the override.
        overrider_id:    Identity of the person who issued the override.
        recorded_at:     UTC timestamp when this entry was created.
        rejected_reason: When the override was rejected, a brief description
                         of why.  ``None`` for non-rejection events.
    """

    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    work_cell_lane: WorkCellLane = WorkCellLane.api_simple
    status: GateStatus = GateStatus.not_gated
    approved: bool = False
    force_override: bool = False
    override_reason: str | None = None
    overrider_id: str | None = None
    recorded_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    rejected_reason: str | None = None


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class HumanGateViolationError(Exception):
    """Raised when a hard gate is violated.

    A violation occurs when:
    - A task in a hard-gated lane is submitted without human approval **and**
    - No valid override is provided.

    Attributes:
        task_id:        The task that triggered the violation.
        work_cell_lane: The lane that is hard-gated.
        rejected_reason: A description of why the gate was not satisfied.
        audit_entry_id: The ID of the corresponding :class:`AuditEntry`.
    """

    def __init__(
        self,
        task_id: str,
        work_cell_lane: WorkCellLane,
        rejected_reason: str,
        audit_entry_id: str | None = None,
    ) -> None:
        self.task_id = task_id
        self.work_cell_lane = work_cell_lane
        self.rejected_reason = rejected_reason
        self.audit_entry_id = audit_entry_id
        super().__init__(
            f"Human gate violation for task {task_id!r} in lane "
            f"{work_cell_lane.value!r}: {rejected_reason}"
        )


# ---------------------------------------------------------------------------
# HumanGatePolicy
# ---------------------------------------------------------------------------


class HumanGatePolicy:
    """Enforces hard human gates on sensitive work-cell lanes.

    Responsibilities:
    - Determine whether a lane is hard-gated (configurable).
    - Enforce the gate: raise :class:`HumanGateViolationError` when a task in a
      hard-gated lane attempts to proceed without either human approval or a
      valid audited override.
    - Provide an in-memory audit log of all enforcement decisions.

    Thread Safety
    -------------
    All mutable state is guarded by an :class:`threading.RLock`.

    Args:
        hard_gated_lanes: Set of :class:`WorkCellLane` values that are
                          hard-gated.  Defaults to
                          :data:`DEFAULT_HARD_GATED_LANES`.
    """

    def __init__(
        self,
        hard_gated_lanes: frozenset[WorkCellLane] | None = None,
    ) -> None:
        self._lock: RLock = RLock()
        self._resolver: LaneResolver = LaneResolver()
        self._hard_gated_lanes: frozenset[WorkCellLane] = (
            hard_gated_lanes
            if hard_gated_lanes is not None
            else DEFAULT_HARD_GATED_LANES
        )
        self._audit_log: list[AuditEntry] = []
        logger.info(
            "HumanGatePolicy initialised",
            extra={
                "hard_gated_lanes": [lane.value for lane in self._hard_gated_lanes]
            },
        )

    # ------------------------------------------------------------------
    # Primary enforcement method
    # ------------------------------------------------------------------

    def enforce(
        self,
        task: dict[str, Any],
        *,
        approved: bool = False,
        force_override: bool = False,
        override_reason: str = "",
        overrider_id: str = "",
    ) -> GateResult:
        """Enforce the human gate for a task.

        For lanes that are not hard-gated, returns immediately with
        :attr:`GateStatus.not_gated`.

        For hard-gated lanes, three outcomes are possible:

        1. **Approved** — ``approved=True`` was passed; gate is satisfied.
           Returns :attr:`GateStatus.approved`.

        2. **Override permitted** — ``approved=False`` but all override
           conditions are met (``force_override=True``, ``override_reason``
           meets minimum length, ``overrider_id`` is non-empty).
           The override is logged.  Returns :attr:`GateStatus.override_permitted`.

        3. **Blocked / override rejected** — Gate is not satisfied and no
           valid override was provided.  Raises :class:`HumanGateViolationError`.

        Args:
            task:            Dict with ``"task_id"``, ``"task_type"``, and
                             ``"risk_tier"`` keys.  ``"task_id"`` is optional
                             (defaults to ``"<unknown>"``).
            approved:        Pass ``True`` when the caller has already obtained
                             a valid human approval record.
            force_override:  Pass ``True`` to attempt a gate bypass.  Must be
                             accompanied by ``override_reason`` and
                             ``overrider_id``.
            override_reason: Minimum-20-character justification for the bypass.
            overrider_id:    Identity (e.g. email or username) of the person
                             issuing the override.

        Returns:
            A :class:`GateResult` when the gate is satisfied or not applicable.

        Raises:
            HumanGateViolationError: When the gate is not satisfied and no valid
                                override is provided.
            KeyError:           When ``task_type`` or ``risk_tier`` is absent.
            ValueError:         When an unrecognised enum string is passed.
        """
        task_id, work_cell_lane = self._resolve_task(task)

        is_gated = work_cell_lane in self._hard_gated_lanes

        if not is_gated:
            # Fast path: not a hard-gated lane — always allowed.
            result = GateResult(
                task_id=task_id,
                work_cell_lane=work_cell_lane,
                status=GateStatus.not_gated,
                is_gated=False,
                may_proceed=True,
            )
            self._record_audit(
                task_id=task_id,
                work_cell_lane=work_cell_lane,
                status=GateStatus.not_gated,
                approved=approved,
                force_override=force_override,
                override_reason=override_reason or None,
                overrider_id=overrider_id or None,
            )
            logger.debug(
                "HumanGatePolicy.enforce: task=%s lane=%s → not_gated",
                task_id,
                work_cell_lane.value,
            )
            return result

        # --- Hard-gated path ---

        if approved:
            # Human approval was explicitly provided.
            result = GateResult(
                task_id=task_id,
                work_cell_lane=work_cell_lane,
                status=GateStatus.approved,
                is_gated=True,
                may_proceed=True,
            )
            self._record_audit(
                task_id=task_id,
                work_cell_lane=work_cell_lane,
                status=GateStatus.approved,
                approved=True,
                force_override=False,
                override_reason=None,
                overrider_id=None,
            )
            logger.info(
                "HumanGatePolicy.enforce: task=%s lane=%s → approved",
                task_id,
                work_cell_lane.value,
            )
            return result

        # No approval — check whether an override is being attempted.
        if force_override:
            rejected_reason = self._validate_override_conditions(
                override_reason=override_reason,
                overrider_id=overrider_id,
            )

            if rejected_reason:
                # Override attempt is invalid — record and raise.
                override_id = str(uuid.uuid4())
                audit = self._record_audit(
                    task_id=task_id,
                    work_cell_lane=work_cell_lane,
                    status=GateStatus.override_rejected,
                    approved=False,
                    force_override=True,
                    override_reason=override_reason or None,
                    overrider_id=overrider_id or None,
                    rejected_reason=rejected_reason,
                    entry_id=override_id,
                )
                logger.warning(
                    "HumanGatePolicy.enforce: task=%s lane=%s override REJECTED: %s",
                    task_id,
                    work_cell_lane.value,
                    rejected_reason,
                )
                raise HumanGateViolationError(
                    task_id=task_id,
                    work_cell_lane=work_cell_lane,
                    rejected_reason=rejected_reason,
                    audit_entry_id=audit.entry_id,
                )

            # Override is valid — permit with full audit record.
            override_id = str(uuid.uuid4())
            self._record_audit(
                task_id=task_id,
                work_cell_lane=work_cell_lane,
                status=GateStatus.override_permitted,
                approved=False,
                force_override=True,
                override_reason=override_reason,
                overrider_id=overrider_id,
                entry_id=override_id,
            )
            result = GateResult(
                task_id=task_id,
                work_cell_lane=work_cell_lane,
                status=GateStatus.override_permitted,
                is_gated=True,
                may_proceed=True,
                override_id=override_id,
                override_reason=override_reason,
                overrider_id=overrider_id,
            )
            logger.warning(
                "HumanGatePolicy.enforce: task=%s lane=%s override PERMITTED "
                "by %s (id=%s)",
                task_id,
                work_cell_lane.value,
                overrider_id,
                override_id,
            )
            return result

        # No approval and no override — task is blocked.
        audit = self._record_audit(
            task_id=task_id,
            work_cell_lane=work_cell_lane,
            status=GateStatus.blocked,
            approved=False,
            force_override=False,
            override_reason=None,
            overrider_id=None,
            rejected_reason="No human approval provided and no override attempted.",
        )
        logger.info(
            "HumanGatePolicy.enforce: task=%s lane=%s → blocked (no approval)",
            task_id,
            work_cell_lane.value,
        )
        raise HumanGateViolationError(
            task_id=task_id,
            work_cell_lane=work_cell_lane,
            rejected_reason="No human approval provided for hard-gated lane.",
            audit_entry_id=audit.entry_id,
        )

    # ------------------------------------------------------------------
    # Gate configuration
    # ------------------------------------------------------------------

    def is_lane_gated(self, lane: WorkCellLane) -> bool:
        """Return True when *lane* is currently configured as a hard gate.

        Args:
            lane: The work-cell lane to check.

        Returns:
            True when the lane is hard-gated.
        """
        with self._lock:
            return lane in self._hard_gated_lanes

    def add_gated_lane(self, lane: WorkCellLane) -> None:
        """Add *lane* to the set of hard-gated lanes.

        Args:
            lane: The work-cell lane to gate.
        """
        with self._lock:
            self._hard_gated_lanes = self._hard_gated_lanes | {lane}
            logger.info(
                "HumanGatePolicy.add_gated_lane: %s added to hard gates",
                lane.value,
            )

    def remove_gated_lane(self, lane: WorkCellLane) -> None:
        """Remove *lane* from the set of hard-gated lanes.

        Use with caution — removing a default hard gate (``security_change``,
        ``deployment``) weakens the security posture.

        Args:
            lane: The work-cell lane to un-gate.
        """
        with self._lock:
            self._hard_gated_lanes = self._hard_gated_lanes - {lane}
            logger.warning(
                "HumanGatePolicy.remove_gated_lane: %s REMOVED from hard gates",
                lane.value,
            )

    def get_gated_lanes(self) -> frozenset[WorkCellLane]:
        """Return the current set of hard-gated lanes.

        Returns:
            Frozenset of :class:`WorkCellLane` values.
        """
        with self._lock:
            return self._hard_gated_lanes

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def get_audit_log(self) -> list[AuditEntry]:
        """Return a snapshot of the in-memory audit log.

        The returned list is a copy — mutations do not affect the internal log.

        Returns:
            List of :class:`AuditEntry` objects in chronological order.
        """
        with self._lock:
            return list(self._audit_log)

    def get_audit_log_for_task(self, task_id: str) -> list[AuditEntry]:
        """Return all audit entries for a specific task.

        Args:
            task_id: The task identifier to filter on.

        Returns:
            List of :class:`AuditEntry` objects in chronological order.
        """
        with self._lock:
            return [e for e in self._audit_log if e.task_id == task_id]

    def get_override_entries(self) -> list[AuditEntry]:
        """Return all audit entries where an override was attempted.

        Includes both permitted and rejected overrides.

        Returns:
            List of :class:`AuditEntry` with
            ``force_override=True`` in chronological order.
        """
        with self._lock:
            return [e for e in self._audit_log if e.force_override]

    def clear_audit_log(self) -> int:
        """Clear the in-memory audit log and return the number of entries removed.

        Intended for tests or after draining to durable storage.

        Returns:
            Number of entries that were cleared.
        """
        with self._lock:
            count = len(self._audit_log)
            self._audit_log.clear()
            logger.debug("HumanGatePolicy.clear_audit_log: cleared %d entries", count)
            return count

    # ------------------------------------------------------------------
    # Reset (tests)
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Restore default config and clear audit log — intended for tests only."""
        with self._lock:
            self._hard_gated_lanes = DEFAULT_HARD_GATED_LANES
            self._audit_log.clear()
            logger.debug("HumanGatePolicy reset to defaults")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_task(
        self, task: dict[str, Any]
    ) -> tuple[str, WorkCellLane]:
        """Extract task_id and resolve work-cell lane from a task dict.

        Args:
            task: Dict with ``task_type``, ``risk_tier``, and optionally
                  ``task_id`` keys.

        Returns:
            Tuple of (task_id, WorkCellLane).

        Raises:
            KeyError:   When ``task_type`` or ``risk_tier`` is absent.
            ValueError: When an unrecognised enum string is passed.
        """
        task_id: str = str(task.get("task_id", "<unknown>"))

        raw_type = task["task_type"]
        raw_risk = task["risk_tier"]
        task_type = raw_type if isinstance(raw_type, TaskType) else TaskType(raw_type)
        risk_tier = raw_risk if isinstance(raw_risk, RiskTier) else RiskTier(raw_risk)

        work_cell_lane = self._resolver.resolve(task_type, risk_tier)
        return task_id, work_cell_lane

    @staticmethod
    def _validate_override_conditions(
        override_reason: str,
        overrider_id: str,
    ) -> str:
        """Validate override conditions and return a rejection reason or empty string.

        Args:
            override_reason: Justification text supplied by the caller.
            overrider_id:    Identity of the person issuing the override.

        Returns:
            An empty string when all conditions are met, or a non-empty
            rejection reason string when conditions are not met.
        """
        if not overrider_id or not overrider_id.strip():
            return "override_reason must be provided for a forced gate bypass."

        if not override_reason or not override_reason.strip():
            return "override_reason must be provided for a forced gate bypass."

        if len(override_reason.strip()) < _MIN_OVERRIDE_REASON_LEN:
            return (
                f"override_reason must be at least {_MIN_OVERRIDE_REASON_LEN} "
                f"characters (got {len(override_reason.strip())})."
            )

        return ""

    def _record_audit(
        self,
        *,
        task_id: str,
        work_cell_lane: WorkCellLane,
        status: GateStatus,
        approved: bool,
        force_override: bool,
        override_reason: str | None,
        overrider_id: str | None,
        rejected_reason: str | None = None,
        entry_id: str | None = None,
    ) -> AuditEntry:
        """Create and append an :class:`AuditEntry` to the internal log.

        Args:
            task_id:         The task being evaluated.
            work_cell_lane:  The lane being gated.
            status:          The :class:`GateStatus` outcome.
            approved:        Whether caller indicated human approval.
            force_override:  Whether an override was attempted.
            override_reason: Override justification (or None).
            overrider_id:    Overrider identity (or None).
            rejected_reason: Rejection description (or None).
            entry_id:        Optional pre-generated UUID to use as the entry ID.

        Returns:
            The newly created :class:`AuditEntry`.
        """
        entry = AuditEntry(
            entry_id=entry_id or str(uuid.uuid4()),
            task_id=task_id,
            work_cell_lane=work_cell_lane,
            status=status,
            approved=approved,
            force_override=force_override,
            override_reason=override_reason,
            overrider_id=overrider_id,
            rejected_reason=rejected_reason,
        )
        with self._lock:
            self._audit_log.append(entry)
        return entry


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_gate_policy_instance: HumanGatePolicy | None = None
_gate_policy_lock: RLock = RLock()


def get_human_gate_policy() -> HumanGatePolicy:
    """Return the global :class:`HumanGatePolicy` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton in tests call :func:`reset_human_gate_policy`
    first.

    Returns:
        The singleton :class:`HumanGatePolicy`.
    """
    global _gate_policy_instance
    with _gate_policy_lock:
        if _gate_policy_instance is None:
            _gate_policy_instance = HumanGatePolicy()
            logger.info("HumanGatePolicy singleton created")
        return _gate_policy_instance


def reset_human_gate_policy() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to
    :func:`get_human_gate_policy` will create a fresh instance.
    """
    global _gate_policy_instance
    with _gate_policy_lock:
        _gate_policy_instance = None
        logger.debug("HumanGatePolicy singleton reset")

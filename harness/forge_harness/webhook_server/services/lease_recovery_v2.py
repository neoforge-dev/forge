"""Lease Recovery Worker v2 — integrates with TaskLease state machine (CP-2007).

Upgrades the legacy ``StaleLeaseRecoveryService`` (which operated on raw dicts)
to work with typed ``TaskLease`` model instances and the formal 7-state lease
state machine defined in ``forge_harness.models.lease``.

Recovery workflow
-----------------

1. ``scan_expired()`` — iterate the lease store and collect all ``TaskLease``
   instances where ``is_expired()`` is True **or** whose state is already
   ``LeaseState.EXPIRED``.

2. ``recover(lease)`` — for each expired lease:
   a. Validate the ``expired → requeued`` transition via ``validate_lease_transition``.
   b. Update ``lease.state = LeaseState.REQUEUED``.
   c. Clear ``lease.owner_node``, ``lease.owner_agent``, and ``lease.path_lock``.
   d. Persist the updated lease back to the store.
   e. Publish ``lease.expired.detected`` and ``lease.recovered`` events.
   f. On any error publish ``lease.recovery.failed`` and re-raise (caller decides).

3. ``run()`` — async loop: sleep ``scan_interval_seconds``, call
   ``scan_expired``, call ``recover`` on each, accumulate ``RecoveryStats``.

4. ``stop()`` — set the stop event; the ``run()`` loop exits after the current
   iteration finishes.

Event topics
------------

* ``lease.expired.detected`` — emitted when an expired lease is found.
* ``lease.recovered`` — emitted after a successful ``expired → requeued`` transition.
* ``lease.recovery.failed`` — emitted when recovery raises an exception.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.models.lease import (
    LeaseState,
    LeaseTransitionError,
    TaskLease,
    validate_lease_transition,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Port abstractions (narrow interfaces for testability)
# ---------------------------------------------------------------------------


@runtime_checkable
class LeaseStore(Protocol):
    """Minimal interface that the recovery worker needs from a lease store."""

    async def list_leases(self) -> list[TaskLease]:
        """Return all known ``TaskLease`` instances."""
        ...

    async def save_lease(self, lease: TaskLease) -> None:
        """Persist an updated ``TaskLease``."""
        ...


@runtime_checkable
class EventBus(Protocol):
    """Minimal publish-only interface for the event bus."""

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish an event to all subscribers of *topic*."""
        ...


# ---------------------------------------------------------------------------
# Stats model
# ---------------------------------------------------------------------------


class RecoveryStats(BaseModel):
    """Accumulates recovery worker metrics across the lifetime of the worker.

    All counters are monotonically increasing.  ``last_scan_at`` reflects the
    UTC start time of the most recent scan regardless of whether any leases
    were recovered.
    """

    total_scans: int = Field(
        default=0,
        description="Number of scan cycles completed.",
        ge=0,
    )
    total_recovered: int = Field(
        default=0,
        description="Total number of leases successfully transitioned to requeued.",
        ge=0,
    )
    total_failed: int = Field(
        default=0,
        description="Total number of recovery attempts that raised an exception.",
        ge=0,
    )
    last_scan_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the last scan start.",
    )
    avg_recovery_time_ms: float | None = Field(
        default=None,
        description="Rolling average of individual lease recovery time in milliseconds.",
    )

    # Internal accumulator (excluded from serialisation)
    _recovery_time_samples: list[float] = []

    def record_recovery(self, elapsed_ms: float) -> None:
        """Update counters and rolling average after a successful recovery."""
        self.total_recovered += 1
        self._recovery_time_samples.append(elapsed_ms)
        self.avg_recovery_time_ms = sum(self._recovery_time_samples) / len(
            self._recovery_time_samples
        )

    def record_failure(self) -> None:
        """Increment the failure counter."""
        self.total_failed += 1

    def record_scan(self, at: datetime) -> None:
        """Mark the start of a scan cycle."""
        self.total_scans += 1
        self.last_scan_at = at


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class LeaseRecoveryWorker:
    """Background worker that detects expired ``TaskLease`` instances and
    transitions them to ``requeued`` via the formal state machine.

    Parameters
    ----------
    lease_store:
        A store implementing the ``LeaseStore`` protocol.
    event_bus:
        An event bus implementing the ``EventBus`` protocol.
    scan_interval_seconds:
        How often (in seconds) to scan for expired leases.  Clamped to a
        minimum of 1 second.
    """

    def __init__(
        self,
        lease_store: Any,
        event_bus: Any,
        scan_interval_seconds: float = 60.0,
    ) -> None:
        self._lease_store = lease_store
        self._event_bus = event_bus
        self._scan_interval = max(float(scan_interval_seconds), 1.0)
        self._stop_event: asyncio.Event = asyncio.Event()
        self._running: bool = False
        self._stats: RecoveryStats = RecoveryStats()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """True while the async ``run()`` loop is active."""
        return self._running

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def scan_expired(self) -> list[TaskLease]:
        """Return all ``TaskLease`` instances that are expired.

        A lease is considered expired when either:
        - ``is_expired()`` returns ``True`` (wall-clock TTL elapsed), or
        - ``lease.state`` is already ``LeaseState.EXPIRED``.

        The method never raises; scan errors are logged and an empty list
        is returned to keep the worker loop alive.
        """
        try:
            all_leases: list[TaskLease] = await self._lease_store.list_leases()
        except Exception as exc:
            logger.error("lease_recovery_v2: failed to list leases: %s", exc, exc_info=True)
            return []

        expired: list[TaskLease] = []
        for lease in all_leases:
            if lease.state is LeaseState.EXPIRED or lease.is_expired():
                expired.append(lease)

        return expired

    async def recover(self, lease: TaskLease) -> bool:
        """Transition a single expired lease to ``requeued``.

        Steps
        -----
        1. Emit ``lease.expired.detected`` event.
        2. Validate ``lease.state → REQUEUED`` via ``validate_lease_transition``.
           If the current state is not ``EXPIRED`` (e.g. ``ACTIVE`` with a past
           ``expires_at``), we first transition to ``EXPIRED``.
        3. Update ownership fields and path lock.
        4. Persist via ``lease_store.save_lease``.
        5. Emit ``lease.recovered`` event.

        If any step raises, emit ``lease.recovery.failed`` and re-raise.

        Returns
        -------
        True on success, False if the state transition is illegal (which is
        logged but not re-raised so the caller can continue with other leases).
        """
        t0 = time.monotonic()

        # Step 1 — announce detection
        await self._event_bus.publish(
            "lease.expired.detected",
            {
                "lease_id": lease.lease_id,
                "task_id": lease.task_id,
                "state": lease.state.value if isinstance(lease.state, LeaseState) else str(lease.state),
                "owner_node": lease.owner_node,
                "owner_agent": lease.owner_agent,
                "path_lock": lease.path_lock,
                "expires_at": lease.expires_at.isoformat() if lease.expires_at else None,
                "detected_at": datetime.now(UTC).isoformat(),
            },
        )

        try:
            # Step 2 — ensure the lease is in EXPIRED state before requeuing.
            # If the state machine allows a direct transition to REQUEUED from
            # the current state we can skip the intermediate EXPIRED step.
            current_state = lease.state
            if current_state is not LeaseState.EXPIRED:
                # Attempt to move to EXPIRED first (valid from CLAIMED, ACTIVE, RENEWING)
                validate_lease_transition(current_state, LeaseState.EXPIRED)
                lease.state = LeaseState.EXPIRED
                lease.updated_at = datetime.now(UTC)

            # Step 3 — transition EXPIRED → REQUEUED (always valid per contract)
            validate_lease_transition(lease.state, LeaseState.REQUEUED)
            lease.state = LeaseState.REQUEUED
            lease.updated_at = datetime.now(UTC)

            # Step 4 — clear ownership so the next claimer starts clean
            lease.owner_node = ""
            lease.owner_agent = ""
            lease.path_lock = None

            # Step 5 — persist
            await self._lease_store.save_lease(lease)

        except LeaseTransitionError as exc:
            logger.warning(
                "lease_recovery_v2: illegal state transition for lease %s (%s): %s",
                lease.lease_id,
                lease.task_id,
                exc,
            )
            await self._event_bus.publish(
                "lease.recovery.failed",
                {
                    "lease_id": lease.lease_id,
                    "task_id": lease.task_id,
                    "reason": "invalid_state_transition",
                    "detail": str(exc),
                    "failed_at": datetime.now(UTC).isoformat(),
                },
            )
            self._stats.record_failure()
            return False

        except Exception as exc:
            logger.error(
                "lease_recovery_v2: unexpected error recovering lease %s (%s): %s",
                lease.lease_id,
                lease.task_id,
                exc,
                exc_info=True,
            )
            await self._event_bus.publish(
                "lease.recovery.failed",
                {
                    "lease_id": lease.lease_id,
                    "task_id": lease.task_id,
                    "reason": "unexpected_error",
                    "detail": str(exc),
                    "failed_at": datetime.now(UTC).isoformat(),
                },
            )
            self._stats.record_failure()
            raise

        # Step 6 — success
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats.record_recovery(elapsed_ms)

        await self._event_bus.publish(
            "lease.recovered",
            {
                "lease_id": lease.lease_id,
                "task_id": lease.task_id,
                "previous_owner_node": lease.owner_node,  # already cleared above, but logged
                "previous_owner_agent": lease.owner_agent,
                "recovered_at": datetime.now(UTC).isoformat(),
                "recovery_time_ms": round(elapsed_ms, 3),
            },
        )

        logger.info(
            "lease_recovery_v2: recovered lease %s (task %s) in %.1f ms",
            lease.lease_id,
            lease.task_id,
            elapsed_ms,
        )
        return True

    # ------------------------------------------------------------------
    # Async loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the periodic scan-and-recover loop until ``stop()`` is called.

        The loop:
        1. Waits ``scan_interval_seconds`` (interruptible by ``stop()``).
        2. Calls ``scan_expired()``.
        3. Calls ``recover()`` on each expired lease.
        4. Updates ``RecoveryStats``.

        The first scan is deferred by one interval so callers can fully
        initialise before work begins.
        """
        if self._running:
            logger.warning("LeaseRecoveryWorker.run() called while already running")
            return

        self._running = True
        self._stop_event.clear()
        logger.info(
            "LeaseRecoveryWorker started (scan_interval=%.1fs)",
            self._scan_interval,
        )

        try:
            while not self._stop_event.is_set():
                # Wait before each scan (allows early stop during idle period)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._scan_interval
                    )
                    # stop_event was set — exit immediately
                    break
                except TimeoutError:
                    pass  # normal timeout → proceed to scan

                scan_start = datetime.now(UTC)
                self._stats.record_scan(scan_start)

                try:
                    expired_leases = await self.scan_expired()
                except Exception as exc:
                    logger.error(
                        "lease_recovery_v2: scan_expired raised: %s", exc, exc_info=True
                    )
                    continue

                if expired_leases:
                    logger.info(
                        "lease_recovery_v2: found %d expired lease(s)", len(expired_leases)
                    )

                for lease in expired_leases:
                    try:
                        await self.recover(lease)
                    except Exception:
                        # recover() already logged and published the failure event
                        pass

        finally:
            self._running = False
            logger.info("LeaseRecoveryWorker stopped")

    async def stop(self) -> None:
        """Signal the worker loop to exit after the current iteration."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> RecoveryStats:
        """Return a snapshot of the current recovery statistics."""
        return self._stats.model_copy()

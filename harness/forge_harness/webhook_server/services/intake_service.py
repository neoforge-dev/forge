"""Intake Service — DF-2001
===========================

Event-driven task intake queue that replaces the manual dispatch-*.md pull
workflow.  Every task submitted to the FORGE Dark Factory passes through
``IntakeService.submit``, which:

1. Validates the :class:`IntakeItem` payload (already done by Pydantic).
2. Assigns a :class:`WorkCellLane` via :class:`LaneEnforcer`.
3. Checks per-lane WIP capacity — rejects when the lane is full.
4. Creates a task entry record (stored in-memory + JSONL persistence).
5. Emits ``task.intake.accepted`` or ``task.intake.rejected`` via
   :class:`EventEmitter`.

Thread Safety
-------------
All mutable state (``_items``, ``_status_index``, ``_source_index``) is
guarded by a single :class:`threading.RLock`.  The RLock is re-entrant so
that internal helpers can be called while the outer lock is already held.

Singleton Pattern
-----------------
Use :func:`get_intake_service` to obtain the shared instance.  In tests call
:func:`reset_intake_service` before each test to start from a clean slate.

Persistence
-----------
Items are appended to ``.forge/intake/queue.jsonl`` (one JSON object per
line) via :meth:`_persist_item`.  On construction :meth:`_load_persisted`
replays the file and populates the in-memory index.  This gives the service
crash-recovery semantics: items survive a service restart.

Usage
-----
::

    from forge_harness.webhook_server.services.intake_service import (
        get_intake_service,
        reset_intake_service,
    )

    svc = get_intake_service()
    result = svc.submit(IntakeItem(
        title="Fix auth bug",
        task_type=TaskType.bug_fix,
        risk_tier=RiskTier.low,
        source="api",
    ))
    # result.status == IntakeStatus.assigned

    pending = svc.list_pending()
    stats = svc.get_stats()

Reference
---------
forge_harness/webhook_server/models/intake_queue.py    (IntakeItem, IntakeResult)
forge_harness/webhook_server/services/lane_enforcer.py (LaneEnforcer)
forge_harness/webhook_server/services/event_emitter.py (EventEmitter)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2001)
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from pathlib import Path
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.intake_queue import (
    IntakeItem,
    IntakeResult,
    IntakeStatus,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.lane_enforcer import get_lane_enforcer

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Default persistence path
# ---------------------------------------------------------------------------

_DEFAULT_QUEUE_PATH = Path(".forge/intake/queue.jsonl")

# ---------------------------------------------------------------------------
# IntakeService
# ---------------------------------------------------------------------------


class IntakeService:
    """Event-driven intake queue for FORGE Dark Factory tasks.

    Responsibilities:
    - Accept :class:`IntakeItem` submissions from any source.
    - Reject items whose lane is at WIP capacity.
    - Persist all items to JSONL for crash recovery.
    - Emit SSE events for accepted and rejected items.
    - Expose ``list_pending`` and ``get_stats`` for observability.

    Attributes:
        _queue_path:    Path to the JSONL persistence file.
        _lock:          Re-entrant lock protecting all mutable state.
        _items:         Ordered list of all known :class:`IntakeItem` objects.
        _status_index:  ``item_id`` → current :class:`IntakeStatus` mapping.
        _results:       ``item_id`` → :class:`IntakeResult` mapping.
        _source_index:  Per-source submission counts (all statuses).
    """

    def __init__(self, queue_path: Path | None = None) -> None:
        self._queue_path: Path = queue_path or _DEFAULT_QUEUE_PATH
        self._lock: RLock = RLock()

        # item_id → IntakeItem
        self._items: dict[str, IntakeItem] = {}

        # item_id → IntakeStatus (current state)
        self._status_index: dict[str, IntakeStatus] = {}

        # item_id → IntakeResult
        self._results: dict[str, IntakeResult] = {}

        # source → total submission count (all statuses)
        self._source_index: dict[str, int] = defaultdict(int)

        # Replay persisted items on startup
        self._load_persisted()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, item: IntakeItem) -> IntakeResult:
        """Process a single intake item.

        Steps:
        1. Record the item and set initial status to ``pending``.
        2. Assign a work-cell lane via :class:`LaneEnforcer`.
        3. Check WIP capacity — reject if the lane is full.
        4. Create a task entry and move status to ``assigned``.
        5. Emit ``task.intake.accepted`` or ``task.intake.rejected`` event.
        6. Persist the item to JSONL.

        Args:
            item: A validated :class:`IntakeItem` to process.

        Returns:
            :class:`IntakeResult` describing the outcome of this submission.
        """
        with self._lock:
            # Record submission
            self._items[item.item_id] = item
            self._status_index[item.item_id] = IntakeStatus.pending
            self._source_index[item.source] += 1

            logger.info(
                "IntakeService.submit: item_id=%s title=%r type=%s risk=%s source=%s",
                item.item_id,
                item.title,
                item.task_type.value,
                item.risk_tier.value,
                item.source,
            )

            # Resolve the target lane without consuming a WIP slot.
            # LaneResolver is stateless; we access it directly so we can check
            # capacity before committing the assignment.
            from forge_harness.webhook_server.models.work_cell import (
                LaneResolver,
                get_lane_config,
            )

            enforcer = get_lane_enforcer()
            resolver = LaneResolver()
            lane: WorkCellLane = resolver.resolve(item.task_type, item.risk_tier)

            # Check WIP capacity before assigning.  check_wip returns True when
            # active < max_wip, meaning there is a free slot.
            wip_available = enforcer.check_wip(lane)
            max_wip = get_lane_config(lane).max_wip

            if not wip_available:
                # WIP full — reject without touching the enforcer state
                reason = (
                    f"Lane '{lane.value}' is at WIP capacity "
                    f"(max_wip={max_wip}). "
                    "Submit again when capacity is available."
                )
                result = IntakeResult(
                    item_id=item.item_id,
                    status=IntakeStatus.rejected,
                    assigned_lane=None,
                    assigned_task_id=None,
                    rejection_reason=reason,
                )
                self._status_index[item.item_id] = IntakeStatus.rejected
                self._results[item.item_id] = result
                self._persist_item(item, result)
                self._emit_event("task.intake.rejected", item, result)
                logger.warning(
                    "IntakeService.submit: REJECTED item_id=%s reason=%s",
                    item.item_id,
                    reason,
                )
                return result

            # WIP capacity available — record the assignment with the enforcer
            # (increments the active counter for this lane) then create a task entry.
            enforcer.assign_lane(item.item_id, item.task_type, item.risk_tier)
            task_id = str(uuid.uuid4())
            result = IntakeResult(
                item_id=item.item_id,
                status=IntakeStatus.assigned,
                assigned_lane=lane,
                assigned_task_id=task_id,
                rejection_reason=None,
            )
            self._status_index[item.item_id] = IntakeStatus.assigned
            self._results[item.item_id] = result
            self._persist_item(item, result)
            self._emit_event("task.intake.accepted", item, result)
            logger.info(
                "IntakeService.submit: ACCEPTED item_id=%s lane=%s task_id=%s",
                item.item_id,
                lane.value,
                task_id,
            )
            return result

    def list_pending(self, limit: int = 50) -> list[IntakeItem]:
        """Return up to *limit* items currently in ``pending`` status.

        Items are returned in submission order (oldest first).

        Args:
            limit: Maximum number of items to return (default 50).

        Returns:
            List of :class:`IntakeItem` objects with status ``pending``.
        """
        with self._lock:
            pending = [
                item
                for item_id, item in self._items.items()
                if self._status_index.get(item_id) == IntakeStatus.pending
            ]
        return pending[:limit]

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of queue statistics.

        Counts items grouped by status and by source.

        Returns:
            Dict with keys:
            - ``by_status``:  mapping of status value → count.
            - ``by_source``:  mapping of source string → count.
            - ``total``:      total number of items ever submitted.

        Example::

            {
                "by_status": {"pending": 2, "assigned": 5, "rejected": 1, "expired": 0},
                "by_source": {"api": 6, "dispatch_file": 2, "heartbeat": 0, "cli": 0},
                "total": 8,
            }
        """
        with self._lock:
            by_status: dict[str, int] = {s.value: 0 for s in IntakeStatus}
            for status in self._status_index.values():
                by_status[status.value] += 1

            by_source: dict[str, int] = dict(self._source_index)

            return {
                "by_status": by_status,
                "by_source": by_source,
                "total": len(self._items),
            }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_item(self, item: IntakeItem, result: IntakeResult) -> None:
        """Append a single item + result record to the JSONL file.

        Each line is a JSON object containing both the item payload and the
        final intake result.  This is append-only; the file is never rewritten.
        Errors are logged but do not propagate — persistence failures must not
        break the submission path.

        Args:
            item:   The submitted :class:`IntakeItem`.
            result: The :class:`IntakeResult` produced for the item.
        """
        try:
            self._queue_path.parent.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {
                "item": item.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
            with self._queue_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "IntakeService._persist_item: failed to write item_id=%s: %s",
                item.item_id,
                exc,
            )

    def _load_persisted(self) -> None:
        """Replay the JSONL persistence file into the in-memory index.

        Called once during construction.  Silently skips malformed lines
        (logs a warning for each one) so that a partially-written file does
        not prevent startup.

        Note: This replay is for observability and stats only.  The
        LaneEnforcer state is NOT replayed because enforcer counters should
        reflect the live system state, not historical records.
        """
        if not self._queue_path.exists():
            logger.debug(
                "IntakeService._load_persisted: no queue file at %s, starting fresh",
                self._queue_path,
            )
            return

        loaded = 0
        errors = 0
        with self._queue_path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    item = IntakeItem.model_validate(record["item"])
                    result = IntakeResult.model_validate(record["result"])
                    self._items[item.item_id] = item
                    self._status_index[item.item_id] = IntakeStatus(result.status)
                    self._results[item.item_id] = result
                    self._source_index[item.source] += 1
                    loaded += 1
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    logger.warning(
                        "IntakeService._load_persisted: skipping malformed line %d: %s",
                        lineno,
                        exc,
                    )

        logger.info(
            "IntakeService._load_persisted: loaded %d items (%d errors) from %s",
            loaded,
            errors,
            self._queue_path,
        )

    # ------------------------------------------------------------------
    # Event emission helper
    # ------------------------------------------------------------------

    def _emit_event(
        self,
        event_type: str,
        item: IntakeItem,
        result: IntakeResult,
    ) -> None:
        """Emit an SSE event via the EventEmitter singleton.

        Falls back to a log warning on any error so that event emission
        failures never break the intake path.

        Args:
            event_type: Dot-notation event type string.
            item:       The :class:`IntakeItem` that was processed.
            result:     The :class:`IntakeResult` for the item.
        """
        try:
            from forge_harness.webhook_server.models.sse_events import SSEEventType
            from forge_harness.webhook_server.services.event_emitter import (
                get_event_emitter,
            )

            # Map string event_type to SSEEventType enum value.
            # task.intake.accepted / task.intake.rejected are not in the current
            # SSEEventType enum.  We use task_status_changed as the closest
            # canonical type and include the specific event in the payload.
            emitter = get_event_emitter()
            payload: dict[str, Any] = {
                "event": event_type,
                "item_id": item.item_id,
                "title": item.title,
                "task_type": item.task_type.value,
                "risk_tier": item.risk_tier.value,
                "source": item.source,
                "status": result.status.value,
                "assigned_lane": result.assigned_lane.value if result.assigned_lane else None,
                "assigned_task_id": result.assigned_task_id,
                "rejection_reason": result.rejection_reason,
            }
            emitter.emit(
                SSEEventType.task_status_changed,
                payload,
                source="intake-service",
            )
            logger.debug(
                "IntakeService._emit_event: emitted %s for item_id=%s",
                event_type,
                item.item_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "IntakeService._emit_event: failed to emit %s for item_id=%s: %s",
                event_type,
                item.item_id,
                exc,
            )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_intake_service_instance: IntakeService | None = None
_intake_service_lock: RLock = RLock()


def get_intake_service(queue_path: Path | None = None) -> IntakeService:
    """Return the global :class:`IntakeService` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    Args:
        queue_path: Override the default JSONL persistence path.
                    Only used on the *first* call; ignored on subsequent calls.

    Returns:
        The singleton :class:`IntakeService`.
    """
    global _intake_service_instance
    with _intake_service_lock:
        if _intake_service_instance is None:
            _intake_service_instance = IntakeService(queue_path=queue_path)
            logger.info("IntakeService singleton created")
        return _intake_service_instance


def reset_intake_service() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to :func:`get_intake_service`
    will create a fresh instance.
    """
    global _intake_service_instance
    with _intake_service_lock:
        _intake_service_instance = None
        logger.debug("IntakeService singleton reset")

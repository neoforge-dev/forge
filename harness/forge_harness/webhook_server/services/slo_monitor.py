"""SLO Monitor Service — DF-2004
================================

Thread-safe singleton that records task lifecycle events, computes per-lane
SLO compliance, and emits ``task.slo.warning`` / ``task.slo.breached`` events
via the EventEmitter when thresholds are crossed.

Thread Safety
-------------
All mutable state is guarded by a single :class:`threading.RLock`.

Singleton Pattern
-----------------
Use :func:`get_slo_monitor` to obtain the shared instance.  In tests call
:func:`reset_slo_monitor` before each test to start from a clean slate.

Usage
-----
::

    from forge_harness.webhook_server.services.slo_monitor import (
        get_slo_monitor,
        reset_slo_monitor,
    )

    monitor = get_slo_monitor()
    monitor.record_task_start("task-001", WorkCellLane.api_simple)
    # ... task executes ...
    monitor.record_task_complete("task-001", passed=True)

    result = monitor.check_slo(WorkCellLane.api_simple)
    # SLOCheckResult(lane=api_simple, status=healthy, ...)

Reference
---------
forge_harness/webhook_server/models/lane_slo.py  (LaneSLO, DEFAULT_LANE_SLOS)
forge_harness/webhook_server/services/event_emitter.py  (EventEmitter)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2004)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from threading import RLock
from typing import NamedTuple

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.lane_slo import (
    DEFAULT_LANE_SLOS,
    LaneSLO,
    SLOCheckResult,
    SLOStatus,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


class _TaskRecord(NamedTuple):
    """Immutable record of a single task's SLO-relevant data."""

    task_id: str
    lane: WorkCellLane
    started_at: datetime
    completed_at: datetime | None
    passed: bool | None
    lead_time_seconds: float | None


# ---------------------------------------------------------------------------
# SLOMonitor
# ---------------------------------------------------------------------------


class SLOMonitor:
    """Records task lifecycle events and evaluates per-lane SLO compliance.

    Attributes:
        _lock:         Re-entrant lock guarding all mutable state.
        _start_times:  Mapping of task_id -> (lane, start datetime).
        _records:      Completed task records in insertion order.
        _requeue_counts: Per-lane requeue event counter.
        _slos:         SLO targets per lane (from DEFAULT_LANE_SLOS by default).
    """

    def __init__(
        self,
        slos: dict[WorkCellLane, LaneSLO] | None = None,
    ) -> None:
        self._lock: RLock = RLock()
        # task_id -> (lane, start_datetime)
        self._start_times: dict[str, tuple[WorkCellLane, datetime]] = {}
        # Completed _TaskRecord list (append-only; bounded by window on read)
        self._records: list[_TaskRecord] = []
        # Per-lane requeue counter
        self._requeue_counts: dict[WorkCellLane, int] = defaultdict(int)
        # SLO definitions — allow injection for testing
        self._slos: dict[WorkCellLane, LaneSLO] = (
            slos if slos is not None else dict(DEFAULT_LANE_SLOS)
        )

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_task_start(self, task_id: str, lane: WorkCellLane) -> None:
        """Record the start time of a task in the given lane.

        Idempotent: if *task_id* is already started, the existing record is
        kept and a debug warning is emitted.

        Args:
            task_id: Unique identifier of the task.
            lane:    The work-cell lane the task is executing in.
        """
        with self._lock:
            if task_id in self._start_times:
                logger.debug(
                    "SLOMonitor.record_task_start: task %s already started; ignoring.",
                    task_id,
                )
                return
            self._start_times[task_id] = (lane, datetime.now(UTC))
            logger.debug(
                "SLOMonitor.record_task_start: task=%s lane=%s",
                task_id,
                lane.value,
            )

    def record_task_complete(self, task_id: str, passed: bool) -> _TaskRecord | None:
        """Record the completion of a task and compute its lead time.

        If the task was never started, a warning is logged and ``None`` is
        returned without recording any data.

        After recording, :meth:`check_slo` is called for the task's lane and
        ``task.slo.warning`` or ``task.slo.breached`` events are emitted if
        the lane is no longer healthy.

        Args:
            task_id: Unique identifier of the task.
            passed:  True when the task completed successfully; False otherwise.

        Returns:
            The completed :class:`_TaskRecord`, or None when task_id is unknown.
        """
        with self._lock:
            entry = self._start_times.pop(task_id, None)
            if entry is None:
                logger.warning(
                    "SLOMonitor.record_task_complete: unknown task_id=%s; skipping.",
                    task_id,
                )
                return None

            lane, started_at = entry
            completed_at = datetime.now(UTC)
            lead_time = (completed_at - started_at).total_seconds()

            record = _TaskRecord(
                task_id=task_id,
                lane=lane,
                started_at=started_at,
                completed_at=completed_at,
                passed=passed,
                lead_time_seconds=lead_time,
            )
            self._records.append(record)

            logger.debug(
                "SLOMonitor.record_task_complete: task=%s lane=%s passed=%s lead_time=%.2fs",
                task_id,
                lane.value,
                passed,
                lead_time,
            )

        # Evaluate SLO outside the lock to avoid deadlock if the EventEmitter
        # callback tries to call back into this monitor.
        result = self.check_slo(lane)
        self._emit_if_needed(result)
        return record

    def record_requeue(self, lane: WorkCellLane) -> None:
        """Increment the requeue counter for a lane.

        Called by the task scheduler when a timed-out task is re-enqueued.
        The counter feeds into :meth:`check_slo` breach detection.

        Args:
            lane: The work-cell lane whose requeue count should be incremented.
        """
        with self._lock:
            self._requeue_counts[lane] += 1
            logger.debug(
                "SLOMonitor.record_requeue: lane=%s count=%d",
                lane.value,
                self._requeue_counts[lane],
            )

    # ------------------------------------------------------------------
    # SLO evaluation
    # ------------------------------------------------------------------

    def check_slo(self, lane: WorkCellLane) -> SLOCheckResult:
        """Compute the current SLO compliance status for a single lane.

        Uses all completed task records to calculate:
        - Average lead time across completed tasks.
        - Pass rate (passed tasks / total completed tasks).
        - Requeue count accumulated since the last reset.

        Status logic (checked in order, first match wins):
        1. breached — average lead time > max_lead_time_seconds, OR
                      pass rate < target_pass_rate, OR
                      requeue_count > max_requeue_count.
        2. warning  — average lead time > max_lead_time_seconds * alert_threshold_pct, OR
                      pass rate < target_pass_rate + (1 - target_pass_rate) * (1 - alert_threshold_pct).
        3. healthy  — all metrics within target.

        When no completed tasks exist the status is ``healthy`` and the
        numeric fields are ``None``.

        Args:
            lane: The work-cell lane to evaluate.

        Returns:
            A :class:`SLOCheckResult` with the current status.
        """
        with self._lock:
            slo = self._slos[lane]
            lane_records = [r for r in self._records if r.lane == lane]
            requeue_count = self._requeue_counts[lane]

        if not lane_records:
            # No data — cannot be breached yet.
            status = SLOStatus.healthy
            if requeue_count > slo.max_requeue_count:
                status = SLOStatus.breached
            return SLOCheckResult(
                lane=lane,
                status=status,
                current_lead_time_seconds=None,
                current_pass_rate=None,
                requeue_count=requeue_count,
            )

        # Compute metrics from records that have valid lead times.
        lead_times = [r.lead_time_seconds for r in lane_records if r.lead_time_seconds is not None]
        avg_lead_time: float | None = sum(lead_times) / len(lead_times) if lead_times else None

        total = len(lane_records)
        passed_count = sum(1 for r in lane_records if r.passed is True)
        pass_rate: float | None = passed_count / total if total > 0 else None

        # Determine status.
        status = self._classify_status(slo, avg_lead_time, pass_rate, requeue_count)

        return SLOCheckResult(
            lane=lane,
            status=status,
            current_lead_time_seconds=avg_lead_time,
            current_pass_rate=pass_rate,
            requeue_count=requeue_count,
        )

    def check_all_slos(self) -> list[SLOCheckResult]:
        """Evaluate SLO compliance for every registered lane.

        Returns:
            A list of :class:`SLOCheckResult` — one entry per :class:`WorkCellLane`,
            in :class:`WorkCellLane` enum definition order.
        """
        return [self.check_slo(lane) for lane in WorkCellLane]

    def get_breaches(self, window_minutes: int = 60) -> list[SLOCheckResult]:
        """Return SLO check results for lanes currently in a breached state.

        Only lanes whose *current* computed status is ``SLOStatus.breached``
        are returned.  The ``window_minutes`` parameter constrains which
        completed-task records are included when computing each lane's status
        so that stale data does not inflate breach counts.

        Args:
            window_minutes: How far back (in minutes) to look when evaluating
                            lane SLOs.  Records older than this window are
                            excluded from computations.  Must be > 0.

        Returns:
            A (possibly empty) list of :class:`SLOCheckResult` for breached lanes.
        """
        if window_minutes <= 0:
            window_minutes = 60

        cutoff = datetime.now(UTC).timestamp() - window_minutes * 60

        with self._lock:
            # Snapshot records within the window.
            windowed_records = [
                r
                for r in self._records
                if r.completed_at is not None and r.completed_at.timestamp() >= cutoff
            ]
            requeue_snapshot = dict(self._requeue_counts)
            slos_snapshot = dict(self._slos)

        breaches: list[SLOCheckResult] = []
        for lane in WorkCellLane:
            slo = slos_snapshot[lane]
            lane_records = [r for r in windowed_records if r.lane == lane]
            requeue_count = requeue_snapshot.get(lane, 0)

            if not lane_records and requeue_count <= slo.max_requeue_count:
                continue

            lead_times = [
                r.lead_time_seconds for r in lane_records if r.lead_time_seconds is not None
            ]
            avg_lead_time: float | None = sum(lead_times) / len(lead_times) if lead_times else None
            total = len(lane_records)
            passed_count = sum(1 for r in lane_records if r.passed is True)
            pass_rate: float | None = passed_count / total if total > 0 else None

            status = self._classify_status(slo, avg_lead_time, pass_rate, requeue_count)
            if status == SLOStatus.breached:
                breaches.append(
                    SLOCheckResult(
                        lane=lane,
                        status=status,
                        current_lead_time_seconds=avg_lead_time,
                        current_pass_rate=pass_rate,
                        requeue_count=requeue_count,
                    )
                )

        return breaches

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_status(
        slo: LaneSLO,
        avg_lead_time: float | None,
        pass_rate: float | None,
        requeue_count: int,
    ) -> SLOStatus:
        """Classify the SLO status given current metrics.

        Args:
            slo:            The SLO targets for the lane.
            avg_lead_time:  Observed average lead time, or None.
            pass_rate:      Observed pass rate (0-1), or None.
            requeue_count:  Observed requeue count.

        Returns:
            The :class:`SLOStatus` classification.
        """
        # --- Breach checks ---
        if avg_lead_time is not None and avg_lead_time > slo.max_lead_time_seconds:
            return SLOStatus.breached
        if pass_rate is not None and pass_rate < slo.target_pass_rate:
            return SLOStatus.breached
        if requeue_count > slo.max_requeue_count:
            return SLOStatus.breached

        # --- Warning checks ---
        lead_time_warn_threshold = slo.max_lead_time_seconds * slo.alert_threshold_pct
        if avg_lead_time is not None and avg_lead_time > lead_time_warn_threshold:
            return SLOStatus.warning

        # Pass-rate warning: warn when pass_rate falls below
        # target + (1 - target) * (1 - alert_threshold_pct).
        # Example: target=0.95, threshold=0.8 → warn below 0.95 + 0.05*0.2 = 0.96?
        # Simpler intent: warn when pass_rate is within the "warning band"
        # above the target, i.e. when pass_rate < target + slack * (1 - threshold).
        # We use: warn_floor = target_pass_rate + (1 - target_pass_rate) * (1 - alert_threshold_pct)
        # That gives a point above the target — anything between warn_floor and 1.0 is healthy.
        # Below warn_floor but above target → warning.  Below target → breach.
        pass_rate_warn_floor = slo.target_pass_rate + (1.0 - slo.target_pass_rate) * (
            1.0 - slo.alert_threshold_pct
        )
        if pass_rate is not None and pass_rate < pass_rate_warn_floor:
            return SLOStatus.warning

        return SLOStatus.healthy

    def _emit_if_needed(self, result: SLOCheckResult) -> None:
        """Emit an SSE event when the lane status is warning or breached.

        Uses a best-effort import of the EventEmitter to avoid circular
        imports and to allow the monitor to function in environments where
        the event system is unavailable.

        Args:
            result: The SLO check result to potentially emit.
        """
        if result.status == SLOStatus.healthy:
            return

        try:
            from forge_harness.webhook_server.models.sse_events import SSEEventType
            from forge_harness.webhook_server.services.event_emitter import (
                get_event_emitter,
            )

            # Map SLOStatus to SSEEventType string key.
            if result.status == SLOStatus.warning:
                event_type = SSEEventType.task_slo_warning
            else:
                event_type = SSEEventType.task_slo_breached

            emitter = get_event_emitter()
            emitter.emit(
                event_type=event_type,
                data={
                    "lane": result.lane.value,
                    "status": result.status.value,
                    "current_lead_time_seconds": result.current_lead_time_seconds,
                    "current_pass_rate": result.current_pass_rate,
                    "requeue_count": result.requeue_count,
                    "checked_at": result.checked_at,
                },
                source="slo_monitor",
            )
            logger.info(
                "SLOMonitor: emitted %s for lane=%s",
                event_type.value,
                result.lane.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SLOMonitor._emit_if_needed: failed to emit event: %s", exc)

    def reset(self) -> None:
        """Clear all recorded state.

        Intended for use in tests only.  After calling reset() the monitor
        behaves as if freshly constructed.
        """
        with self._lock:
            self._start_times.clear()
            self._records.clear()
            self._requeue_counts.clear()


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_monitor_instance: SLOMonitor | None = None
_monitor_lock: RLock = RLock()


def get_slo_monitor() -> SLOMonitor:
    """Return the global :class:`SLOMonitor` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton in tests call :func:`reset_slo_monitor` first.

    Returns:
        The singleton :class:`SLOMonitor`.
    """
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = SLOMonitor()
            logger.info("SLOMonitor singleton created")
        return _monitor_instance


def reset_slo_monitor() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to :func:`get_slo_monitor`
    will create a fresh instance.
    """
    global _monitor_instance
    with _monitor_lock:
        _monitor_instance = None
        logger.debug("SLOMonitor singleton reset")

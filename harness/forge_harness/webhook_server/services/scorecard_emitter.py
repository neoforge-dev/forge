"""Scorecard Emitter Service
=============================

Gathers task data, computes a DarkFactoryScorecard for the requested
measurement window, and publishes the result to the EventBus as an SSE
event so connected Command-Center clients receive live dark-factory health
metrics without polling.

Usage
-----
Inject the singleton into startup routines::

    from forge_harness.webhook_server.services.scorecard_emitter import (
        get_scorecard_emitter,
    )

    emitter = get_scorecard_emitter()
    await emitter.compute_and_emit(period="hourly")

Schedule recurring emission::

    await emitter.schedule_hourly()   # every 60 minutes
    await emitter.schedule_daily()    # every 24 hours
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.scorecard import (
    ScorecardEvent,
    compute_scorecard,
)
from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Type alias for task data sources
# ---------------------------------------------------------------------------

# A task source is any async callable that returns a list of task dicts
# compatible with compute_scorecard().
TaskSource = Callable[[], Coroutine[Any, Any, list[dict[str, Any]]]]

# Period durations
_PERIOD_DURATION: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(hours=24),
}

# EventBus event type strings matching ScorecardEvent.event_type literal
_EVENT_TYPE_MAP: dict[str, str] = {
    "hourly": "scorecard_hourly",
    "daily": "scorecard_daily",
}

# Default node ID when none is configured
_DEFAULT_NODE_ID = "forge:unknown"


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------


class ScorecardEmitter:
    """Computes and emits DarkFactoryScorecard events over the EventBus.

    The emitter decouples scorecard computation from task storage.  Callers
    supply a *task_source* — an async callable that returns task dicts in the
    format expected by :func:`compute_scorecard`.  This allows the emitter to
    be unit-tested with mock data sources without touching the file system or
    Redis.

    Args:
        task_source: Async callable that returns ``list[dict[str, Any]]`` of
            task dicts.  Must not raise for normal empty results; only hard
            errors (I/O failures, etc.) should propagate.
        event_bus: EventBus instance to publish events on.  Defaults to the
            global singleton from :func:`get_event_bus`.
        node_id: Node identifier embedded in the scorecard (e.g.
            ``"forge:nova"``).  Defaults to ``"forge:unknown"``.
    """

    def __init__(
        self,
        task_source: TaskSource,
        event_bus: EventBus | None = None,
        node_id: str = _DEFAULT_NODE_ID,
    ) -> None:
        self._task_source = task_source
        self._event_bus = event_bus or get_event_bus()
        self._node_id = node_id
        self._running_tasks: list[asyncio.Task[None]] = []
        logger.info(
            "ScorecardEmitter initialised",
            extra={"node_id": node_id},
        )

    # ------------------------------------------------------------------
    # Core emission
    # ------------------------------------------------------------------

    async def compute_and_emit(self, period: str = "hourly") -> ScorecardEvent:
        """Gather tasks, compute scorecard, publish to EventBus.

        Args:
            period: Measurement window granularity — ``"hourly"`` (default)
                or ``"daily"``.  Controls both the window duration used for
                ``period_start``/``period_end`` and the SSE event type.

        Returns:
            The :class:`ScorecardEvent` that was published so callers can
            inspect or log it without subscribing to the bus.

        Raises:
            ValueError: If *period* is not ``"hourly"`` or ``"daily"``.
            Exception: Any unhandled error from the task source propagates
                after being logged so the caller (scheduler) can decide
                whether to retry.
        """
        if period not in _PERIOD_DURATION:
            raise ValueError(
                f"Unknown period {period!r}. Must be one of: "
                + ", ".join(sorted(_PERIOD_DURATION))
            )

        period_end = datetime.now(UTC)
        period_start = period_end - _PERIOD_DURATION[period]
        event_type = _EVENT_TYPE_MAP[period]

        logger.debug(
            "Computing scorecard",
            extra={
                "period": period,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "node_id": self._node_id,
            },
        )

        # --- Fetch tasks -------------------------------------------------------
        try:
            tasks = await self._task_source()
        except Exception as exc:
            logger.error(
                "Task source failed — scorecard emission aborted",
                extra={"period": period, "error": str(exc)},
            )
            raise

        task_count = len(tasks)
        logger.debug(
            "Task source returned %d tasks",
            task_count,
            extra={"period": period, "task_count": task_count},
        )

        # --- Compute -----------------------------------------------------------
        scorecard = compute_scorecard(
            tasks=tasks,
            period_start=period_start,
            period_end=period_end,
            node_id=self._node_id,
        )

        # --- Build SSE envelope ------------------------------------------------
        event = ScorecardEvent(
            event_type=event_type,  # type: ignore[arg-type]
            scorecard=scorecard,
        )

        # --- Publish -----------------------------------------------------------
        payload = event.model_dump(mode="json")
        await self._event_bus.publish(
            event_type=event_type,
            data=payload,
            source="scorecard-emitter",
        )

        logger.info(
            "Scorecard emitted",
            extra={
                "period": period,
                "event_type": event_type,
                "total_tasks": scorecard.total_tasks,
                "autonomous_completions": scorecard.autonomous_completions,
                "dispatch_ack_rate": scorecard.dispatch_ack_rate,
                "evaluator_pass_rate": scorecard.evaluator_pass_rate,
                "human_touch_ratio": scorecard.human_touch_ratio,
            },
        )

        return event

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    async def schedule_hourly(
        self,
        *,
        interval_seconds: float = 3600.0,
        run_immediately: bool = True,
    ) -> None:
        """Run compute_and_emit("hourly") on a repeating interval.

        This coroutine runs indefinitely.  Cancel the returned asyncio.Task
        (or the enclosing scope) to stop the loop.

        Args:
            interval_seconds: Seconds between emissions.  Defaults to 3600
                (one hour).
            run_immediately: Emit once before waiting for the first interval.
                Defaults to ``True``.
        """
        await self._run_schedule("hourly", interval_seconds=interval_seconds, run_immediately=run_immediately)

    async def schedule_daily(
        self,
        *,
        interval_seconds: float = 86400.0,
        run_immediately: bool = True,
    ) -> None:
        """Run compute_and_emit("daily") on a repeating interval.

        Args:
            interval_seconds: Seconds between emissions.  Defaults to 86400
                (24 hours).
            run_immediately: Emit once before waiting for the first interval.
                Defaults to ``True``.
        """
        await self._run_schedule("daily", interval_seconds=interval_seconds, run_immediately=run_immediately)

    async def _run_schedule(
        self,
        period: str,
        *,
        interval_seconds: float,
        run_immediately: bool,
    ) -> None:
        """Internal scheduler loop shared by hourly and daily schedules.

        Args:
            period: ``"hourly"`` or ``"daily"``.
            interval_seconds: Wait time between successive emissions.
            run_immediately: Emit before the first sleep when ``True``.
        """
        logger.info(
            "Starting %s scorecard schedule (interval=%.0fs)",
            period,
            interval_seconds,
            extra={"period": period, "interval_seconds": interval_seconds},
        )

        if run_immediately:
            await self._safe_emit(period)

        while True:
            await asyncio.sleep(interval_seconds)
            await self._safe_emit(period)

    async def _safe_emit(self, period: str) -> None:
        """Emit without propagating exceptions so the scheduler stays alive.

        Errors are logged at ERROR level; the scheduler loop continues on the
        next interval.  This prevents a transient I/O failure from permanently
        silencing the scorecard feed.
        """
        try:
            await self.compute_and_emit(period)
        except Exception as exc:
            logger.error(
                "Scorecard emission failed (will retry on next interval)",
                extra={"period": period, "error": str(exc)},
            )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_emitter_instance: ScorecardEmitter | None = None
_emitter_lock: Lock = Lock()


def get_scorecard_emitter(
    task_source: TaskSource | None = None,
    event_bus: EventBus | None = None,
    node_id: str = _DEFAULT_NODE_ID,
) -> ScorecardEmitter:
    """Return the global ScorecardEmitter singleton.

    On the first call, *task_source* **must** be provided so the emitter
    knows where to fetch tasks from.  Subsequent calls return the cached
    instance regardless of the arguments supplied.

    To replace the singleton (e.g. in tests) call
    :func:`reset_scorecard_emitter` first.

    Args:
        task_source: Async callable returning task dicts.  Required on the
            first call; ignored on subsequent calls.
        event_bus: EventBus to use.  Defaults to global singleton.
        node_id: Node identifier for the scorecard.  Defaults to
            ``"forge:unknown"``.

    Returns:
        The singleton :class:`ScorecardEmitter`.

    Raises:
        ValueError: When called for the first time without a *task_source*.
    """
    global _emitter_instance
    with _emitter_lock:
        if _emitter_instance is None:
            if task_source is None:
                raise ValueError(
                    "task_source is required when creating the ScorecardEmitter singleton for the first time"
                )
            _emitter_instance = ScorecardEmitter(
                task_source=task_source,
                event_bus=event_bus,
                node_id=node_id,
            )
        return _emitter_instance


def reset_scorecard_emitter() -> None:
    """Reset the singleton — intended for use in tests only."""
    global _emitter_instance
    with _emitter_lock:
        _emitter_instance = None

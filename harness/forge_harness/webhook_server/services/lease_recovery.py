"""Lease recovery worker for stale task ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class StaleLeaseRecoveryService:
    """Background worker that requeues tasks with expired leases."""

    def __init__(
        self,
        *,
        task_handler: Any,
        event_bus: Any,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.task_handler = task_handler
        self.event_bus = event_bus
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def running(self) -> bool:
        """Whether the service loop is active."""
        return self._running

    @staticmethod
    def _parse_expiry(lease: dict[str, Any]) -> datetime | None:
        """Parse lease expiry into a UTC timestamp."""
        raw_value = lease.get("lease_expires_at")
        if raw_value is None:
            return None

        if isinstance(raw_value, datetime):
            parsed = raw_value
        elif isinstance(raw_value, str):
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    async def scan_and_recover(self) -> int:
        """Scan task leases and requeue stale ownership records."""
        tasks = await self.task_handler.list_tasks(limit=10000)
        now = self._now_provider()
        recovered = 0

        for task in tasks:
            lease = task.get("lease")
            if not isinstance(lease, dict):
                continue

            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue

            expires_at = self._parse_expiry(lease)
            if expires_at is None or expires_at > now:
                continue

            try:
                requeued_task = await self.task_handler.requeue_task(task_id)
            except Exception as e:
                logger.warning(f"Failed to auto-requeue task {task_id}: {e}")
                continue

            if not requeued_task:
                continue

            recovered += 1

            payload = {
                "task_id": task_id,
                "expired_at": expires_at.isoformat(),
                "owner_node": lease.get("owner_node"),
                "owner_agent": lease.get("owner_agent"),
                "path_lock": lease.get("path_lock"),
                "task": requeued_task,
            }
            await self.event_bus.publish("task.lease.expired", payload)
            await self.event_bus.publish(
                "task.requeued",
                {
                    "task_id": task_id,
                    "task": requeued_task,
                    "reason": "lease_expired_auto_recovery",
                },
            )

        return recovered

    async def start(self, poll_interval: float = 30.0) -> None:
        """Run recovery loop until stop() is called."""
        if self._running:
            logger.warning("StaleLeaseRecoveryService already running")
            return

        interval = max(float(poll_interval), 1.0)
        self._running = True
        self._stop_event.clear()
        logger.info(f"StaleLeaseRecoveryService started with {interval:.1f}s poll interval")

        try:
            while not self._stop_event.is_set():
                try:
                    recovered = await self.scan_and_recover()
                    if recovered:
                        logger.info(f"Recovered {recovered} stale task lease(s)")
                except Exception as e:
                    logger.error(f"Lease recovery scan failed: {e}", exc_info=True)

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except TimeoutError:
                    continue
        finally:
            self._running = False
            logger.info("StaleLeaseRecoveryService stopped")

    async def stop(self) -> None:
        """Signal the recovery loop to stop."""
        self._stop_event.set()

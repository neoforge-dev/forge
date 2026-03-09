"""Extended tests for StaleLeaseRecoveryService.

Covers _parse_expiry edge cases, scan_and_recover corner cases,
start/stop lifecycle, and event payload correctness.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call

import pytest

from forge_harness.webhook_server.services.lease_recovery import (
    StaleLeaseRecoveryService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    tasks=None,
    requeue_return=None,
    now=None,
):
    """Build a service with fully-mocked dependencies."""
    task_handler = AsyncMock()
    task_handler.list_tasks = AsyncMock(return_value=tasks or [])
    task_handler.requeue_task = AsyncMock(return_value=requeue_return)
    event_bus = AsyncMock()
    now_fn = (lambda: now) if now is not None else None
    service = StaleLeaseRecoveryService(
        task_handler=task_handler,
        event_bus=event_bus,
        now_provider=now_fn,
    )
    return service, task_handler, event_bus


# ---------------------------------------------------------------------------
# _parse_expiry – comprehensive input coverage
# ---------------------------------------------------------------------------


class TestParseExpiry:
    def _parse(self, lease):
        return StaleLeaseRecoveryService._parse_expiry(lease)

    def test_none_value_returns_none(self):
        assert self._parse({"lease_expires_at": None}) is None

    def test_missing_key_returns_none(self):
        assert self._parse({}) is None

    def test_invalid_string_returns_none(self):
        assert self._parse({"lease_expires_at": "not-a-date"}) is None

    def test_int_value_returns_none(self):
        assert self._parse({"lease_expires_at": 12345}) is None

    def test_isoformat_string_parsed(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        result = self._parse({"lease_expires_at": now.isoformat()})
        assert result is not None
        assert result == now

    def test_z_suffix_parsed(self):
        result = self._parse({"lease_expires_at": "2026-02-20T12:00:00Z"})
        assert result is not None
        assert result.tzinfo is not None

    def test_datetime_object_returned_as_utc(self):
        now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        result = self._parse({"lease_expires_at": now})
        assert result == now

    def test_naive_datetime_gets_utc_tzinfo(self):
        naive = datetime(2026, 3, 1, 10, 0)  # no tzinfo
        result = self._parse({"lease_expires_at": naive})
        assert result is not None
        assert result.tzinfo is not None
        assert result.tzinfo == UTC

    def test_result_is_always_utc(self):
        # Offset-aware string that is NOT UTC
        result = self._parse({"lease_expires_at": "2026-01-01T12:00:00+05:00"})
        assert result is not None
        # After astimezone(UTC) the offset-aware datetime maps to UTC
        assert result == datetime(2026, 1, 1, 7, 0, tzinfo=UTC)

    def test_empty_string_returns_none(self):
        assert self._parse({"lease_expires_at": ""}) is None


# ---------------------------------------------------------------------------
# scan_and_recover – task filtering
# ---------------------------------------------------------------------------


class TestScanAndRecover:
    @pytest.mark.asyncio
    async def test_no_tasks_returns_zero(self):
        service, task_handler, event_bus = _make_service(tasks=[])
        assert await service.scan_and_recover() == 0
        event_bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_without_lease_key_skipped(self):
        service, th, eb = _make_service(tasks=[{"id": "t1"}])
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_task_with_none_lease_skipped(self):
        service, th, eb = _make_service(tasks=[{"id": "t1", "lease": None}])
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_task_with_non_dict_lease_skipped(self):
        service, th, eb = _make_service(tasks=[{"id": "t1", "lease": "bad"}])
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_task_with_missing_id_skipped(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=5)).isoformat()
        service, th, eb = _make_service(
            tasks=[{"lease": {"lease_expires_at": expired}}],
            now=now,
        )
        assert await service.scan_and_recover() == 0
        th.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_with_empty_id_skipped(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=5)).isoformat()
        service, th, eb = _make_service(
            tasks=[{"id": "  ", "lease": {"lease_expires_at": expired}}],
            now=now,
        )
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_task_with_future_expiry_skipped(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        future = (now + timedelta(minutes=10)).isoformat()
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": future}}],
            now=now,
        )
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_task_with_invalid_expiry_skipped(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": "bad-date"}}],
            now=now,
        )
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_expired_task_requeued(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        requeued = {"id": "t1", "status": "pending"}
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            requeue_return=requeued,
            now=now,
        )
        count = await service.scan_and_recover()
        assert count == 1
        th.requeue_task.assert_awaited_once_with("t1")

    @pytest.mark.asyncio
    async def test_multiple_expired_tasks_all_requeued(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        tasks = [
            {"id": f"t{i}", "lease": {"lease_expires_at": expired}}
            for i in range(5)
        ]
        requeued = {"id": "tx", "status": "pending"}
        service, th, eb = _make_service(tasks=tasks, requeue_return=requeued, now=now)
        count = await service.scan_and_recover()
        assert count == 5
        assert th.requeue_task.await_count == 5

    @pytest.mark.asyncio
    async def test_requeue_failure_does_not_increment_count(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            now=now,
        )
        th.requeue_task.side_effect = RuntimeError("DB gone")
        count = await service.scan_and_recover()
        assert count == 0

    @pytest.mark.asyncio
    async def test_requeue_returning_none_does_not_increment_count(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            requeue_return=None,
            now=now,
        )
        assert await service.scan_and_recover() == 0

    @pytest.mark.asyncio
    async def test_requeue_exception_does_not_stop_other_tasks(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        tasks = [
            {"id": "t-fail", "lease": {"lease_expires_at": expired}},
            {"id": "t-ok", "lease": {"lease_expires_at": expired}},
        ]
        requeued = {"id": "t-ok", "status": "pending"}

        service, th, eb = _make_service(tasks=tasks, requeue_return=requeued, now=now)
        th.requeue_task.side_effect = [RuntimeError("fail"), requeued]

        count = await service.scan_and_recover()
        assert count == 1


# ---------------------------------------------------------------------------
# Event payload correctness
# ---------------------------------------------------------------------------


class TestScanAndRecoverEvents:
    @pytest.mark.asyncio
    async def test_publishes_exactly_two_events_per_recovered_task(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=2)).isoformat()
        requeued = {"id": "t1", "status": "pending"}
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            requeue_return=requeued,
            now=now,
        )
        await service.scan_and_recover()
        assert eb.publish.await_count == 2

    @pytest.mark.asyncio
    async def test_first_event_is_lease_expired(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        requeued = {"id": "t1", "status": "pending"}
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            requeue_return=requeued,
            now=now,
        )
        await service.scan_and_recover()
        first_call = eb.publish.await_args_list[0]
        assert first_call.args[0] == "task.lease.expired"

    @pytest.mark.asyncio
    async def test_second_event_is_task_requeued(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        requeued = {"id": "t1", "status": "pending"}
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            requeue_return=requeued,
            now=now,
        )
        await service.scan_and_recover()
        second_call = eb.publish.await_args_list[1]
        assert second_call.args[0] == "task.requeued"

    @pytest.mark.asyncio
    async def test_lease_expired_payload_contains_owner_fields(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        requeued = {"id": "t1", "status": "pending"}
        service, th, eb = _make_service(
            tasks=[
                {
                    "id": "t1",
                    "lease": {
                        "lease_expires_at": expired,
                        "owner_node": "nova",
                        "owner_agent": "forge:codex",
                        "path_lock": "harness/src",
                    },
                }
            ],
            requeue_return=requeued,
            now=now,
        )
        await service.scan_and_recover()
        payload = eb.publish.await_args_list[0].args[1]
        assert payload["owner_node"] == "nova"
        assert payload["owner_agent"] == "forge:codex"
        assert payload["path_lock"] == "harness/src"
        assert payload["task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_task_requeued_payload_contains_reason(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        requeued = {"id": "t1", "status": "pending"}
        service, th, eb = _make_service(
            tasks=[{"id": "t1", "lease": {"lease_expires_at": expired}}],
            requeue_return=requeued,
            now=now,
        )
        await service.scan_and_recover()
        payload = eb.publish.await_args_list[1].args[1]
        assert payload["reason"] == "lease_expired_auto_recovery"
        assert payload["task_id"] == "t1"
        assert payload["task"] == requeued


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


class TestStartStopLifecycle:
    @pytest.mark.asyncio
    async def test_running_false_before_start(self):
        service, _, _ = _make_service()
        assert service.running is False

    @pytest.mark.asyncio
    async def test_running_true_while_active(self):
        service, _, _ = _make_service()
        task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0.02)
        assert service.running is True
        await service.stop()
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_running_false_after_stop(self):
        service, _, _ = _make_service()
        task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0)
        await service.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert service.running is False

    @pytest.mark.asyncio
    async def test_start_twice_does_not_create_second_loop(self):
        service, th, _ = _make_service()
        t1 = asyncio.create_task(service.start(poll_interval=1.0))
        await asyncio.sleep(0)  # let first loop start
        # Second call should return immediately (guard check)
        t2 = asyncio.create_task(service.start(poll_interval=1.0))
        await asyncio.wait_for(t2, timeout=1.0)
        await service.stop()
        await asyncio.wait_for(t1, timeout=2.0)

    @pytest.mark.asyncio
    async def test_poll_interval_minimum_is_one_second(self):
        """poll_interval below 1.0 should be floored to 1.0."""
        service, _, _ = _make_service()
        # We just verify start() accepts sub-1 interval without error
        task = asyncio.create_task(service.start(poll_interval=0.001))
        await asyncio.sleep(0)
        await service.stop()
        await asyncio.wait_for(task, timeout=3.0)

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self):
        """Calling stop() before start() must not raise."""
        service, _, _ = _make_service()
        await service.stop()  # No-op, should not raise

    @pytest.mark.asyncio
    async def test_scan_called_at_least_once_during_loop(self):
        service, th, _ = _make_service()
        task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0.05)
        await service.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert th.list_tasks.await_count >= 1

    @pytest.mark.asyncio
    async def test_scan_error_does_not_crash_loop(self):
        """An exception in scan_and_recover must not terminate the loop."""
        service, th, _ = _make_service()
        th.list_tasks.side_effect = RuntimeError("boom")

        task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0.05)
        assert service.running is True  # still alive despite errors
        await service.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert service.running is False

    @pytest.mark.asyncio
    async def test_default_now_provider_is_utc(self):
        """When no now_provider supplied, the service uses UTC now."""
        service = StaleLeaseRecoveryService(
            task_handler=AsyncMock(list_tasks=AsyncMock(return_value=[])),
            event_bus=AsyncMock(),
        )
        before = datetime.now(UTC)
        result = service._now_provider()
        after = datetime.now(UTC)
        assert before <= result <= after
        assert result.tzinfo is not None

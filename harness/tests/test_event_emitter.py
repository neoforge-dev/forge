"""Tests for forge_harness/webhook_server/services/event_emitter.py

Coverage targets (70%+):
- EventEmitter.__init__: initialisation and default state
- EventEmitter.emit: builds SSEEvent, stores in ring, notifies callbacks,
  schedules async bus publish, returns the event
- EventEmitter.emit: callback exceptions are caught and logged
- EventEmitter._publish_to_bus: async loop path (create_task)
- EventEmitter._publish_to_bus: no-loop path (thread fallback)
- EventEmitter._publish_to_bus: bus error swallowed and logged
- EventEmitter.get_recent_events: unfiltered, filtered, limit, empty
- EventEmitter.subscribe / unsubscribe: add/remove callbacks
- EventEmitter.event_count / subscriber_count: introspection
- get_event_emitter: singleton creation, re-use, custom maxlen
- reset_event_emitter: destroys singleton for isolation
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from forge_harness.webhook_server.models.sse_events import SSEEvent, SSEEventType
from forge_harness.webhook_server.services.event_emitter import (
    _DEQUE_MAXLEN,
    EventEmitter,
    get_event_emitter,
    reset_event_emitter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_emitter(maxlen: int = 10) -> EventEmitter:
    """Return a fresh EventEmitter with a small ring buffer."""
    return EventEmitter(maxlen=maxlen)


def _make_bus_mock() -> MagicMock:
    """Return a mock EventBus with an async publish coroutine."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Guarantee a clean singleton before and after every test."""
    reset_event_emitter()
    yield
    reset_event_emitter()


# ===========================================================================
# EventEmitter.__init__
# ===========================================================================


class TestEventEmitterInit:
    def test_empty_ring_buffer_on_creation(self):
        emitter = _make_emitter()
        assert emitter.event_count() == 0

    def test_no_subscribers_on_creation(self):
        emitter = _make_emitter()
        assert emitter.subscriber_count() == 0

    def test_ring_buffer_is_deque(self):
        emitter = _make_emitter()
        assert isinstance(emitter._recent, deque)

    def test_ring_buffer_maxlen_applied(self):
        emitter = _make_emitter(maxlen=7)
        assert emitter._recent.maxlen == 7

    def test_default_maxlen_is_module_constant(self):
        emitter = EventEmitter()
        assert emitter._recent.maxlen == _DEQUE_MAXLEN

    def test_callbacks_list_is_empty(self):
        emitter = _make_emitter()
        assert emitter._callbacks == []

    def test_lock_is_rlock(self):
        emitter = _make_emitter()
        assert isinstance(emitter._lock, type(threading.RLock()))


# ===========================================================================
# EventEmitter.emit — core publish path
# ===========================================================================


class TestEventEmitterEmit:
    def test_emit_returns_sse_event(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            result = emitter.emit(SSEEventType.feature_updated, {"id": "f-1"})
        assert isinstance(result, SSEEvent)

    def test_emit_event_type_matches(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            result = emitter.emit(SSEEventType.session_created, {})
        assert result.event_type == SSEEventType.session_created

    def test_emit_data_matches(self):
        emitter = _make_emitter()
        payload = {"feature_id": "feat-42", "status": "done"}
        with patch.object(emitter, "_publish_to_bus"):
            result = emitter.emit(SSEEventType.evaluator_completed, payload)
        assert result.data == payload

    def test_emit_custom_source_applied(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            result = emitter.emit(SSEEventType.agent_heartbeat, {}, source="feature_tracker")
        assert result.source_service == "feature_tracker"

    def test_emit_default_source(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            result = emitter.emit(SSEEventType.agent_heartbeat, {})
        assert result.source_service == "webhook-server"

    def test_emit_stores_event_in_ring(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.feature_updated, {"x": 1})
        assert emitter.event_count() == 1

    def test_emit_multiple_events_accumulate(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            for i in range(5):
                emitter.emit(SSEEventType.task_status_changed, {"i": i})
        assert emitter.event_count() == 5

    def test_emit_ring_evicts_oldest_when_full(self):
        emitter = _make_emitter(maxlen=3)
        with patch.object(emitter, "_publish_to_bus"):
            for i in range(5):
                emitter.emit(SSEEventType.task_status_changed, {"i": i})
        # Ring is bounded to 3
        assert emitter.event_count() == 3

    def test_emit_calls_publish_to_bus(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus") as mock_pub:
            emitter.emit(SSEEventType.session_ended, {})
        mock_pub.assert_called_once()

    def test_emit_passes_event_to_publish_to_bus(self):
        emitter = _make_emitter()
        captured = []
        emitter._publish_to_bus = lambda evt: captured.append(evt)
        result = emitter.emit(SSEEventType.evaluator_failed, {"err": "x"})
        assert len(captured) == 1
        assert captured[0] is result

    def test_emit_invokes_registered_callback(self):
        emitter = _make_emitter()
        received: list[SSEEvent] = []
        emitter.subscribe(received.append)

        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.feature_assigned, {"agent": "kimi"})

        assert len(received) == 1
        assert received[0].event_type == SSEEventType.feature_assigned

    def test_emit_invokes_multiple_callbacks(self):
        emitter = _make_emitter()
        calls_a: list[SSEEvent] = []
        calls_b: list[SSEEvent] = []
        emitter.subscribe(calls_a.append)
        emitter.subscribe(calls_b.append)

        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.session_created, {})

        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_emit_callback_exception_does_not_propagate(self):
        emitter = _make_emitter()

        def bad_callback(event: SSEEvent) -> None:
            raise RuntimeError("intentional error")

        emitter.subscribe(bad_callback)
        with patch.object(emitter, "_publish_to_bus"):
            # Should not raise
            emitter.emit(SSEEventType.agent_heartbeat, {})

    def test_emit_callback_exception_logs_warning(self):
        emitter = _make_emitter()
        emitter.subscribe(lambda e: (_ for _ in ()).throw(ValueError("boom")))

        with (
            patch("forge_harness.webhook_server.services.event_emitter.logger") as mock_log,
            patch.object(emitter, "_publish_to_bus"),
        ):
            emitter.emit(SSEEventType.task_slo_warning, {})

        mock_log.warning.assert_called()

    def test_emit_later_callback_still_called_after_earlier_raises(self):
        emitter = _make_emitter()
        second_calls: list[SSEEvent] = []

        def bad_cb(e: SSEEvent) -> None:
            raise RuntimeError("first fails")

        emitter.subscribe(bad_cb)
        emitter.subscribe(second_calls.append)

        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.task_slo_breached, {})

        # Second callback must still be reached
        assert len(second_calls) == 1

    def test_emit_debug_log_called(self):
        emitter = _make_emitter()
        with (
            patch("forge_harness.webhook_server.services.event_emitter.logger") as mock_log,
            patch.object(emitter, "_publish_to_bus"),
        ):
            emitter.emit(SSEEventType.feature_updated, {})
        mock_log.debug.assert_called()


# ===========================================================================
# EventEmitter._publish_to_bus
# ===========================================================================


class TestPublishToBus:
    # _publish_to_bus imports get_event_bus *inside* the function body:
    #   from forge_harness.webhook_server.services.event_bus import get_event_bus
    # Python resolves that import at call time from the source module, so we
    # must patch it on the event_bus module, not on event_emitter.
    _BUS_TARGET = "forge_harness.webhook_server.services.event_bus.get_event_bus"

    @pytest.mark.asyncio
    async def test_publish_uses_create_task_when_loop_running(self):
        """Inside an async context, _publish_to_bus uses loop.create_task."""
        emitter = _make_emitter()
        bus = _make_bus_mock()

        event = SSEEvent(event_type=SSEEventType.feature_updated, data={"k": "v"})
        with patch(self._BUS_TARGET, return_value=bus):
            emitter._publish_to_bus(event)
            # Yield control so the created task can execute
            await asyncio.sleep(0)

        bus.publish.assert_awaited_once_with(
            event_type="feature.updated",
            data={"k": "v"},
            source="webhook-server",
        )

    def test_publish_uses_thread_when_no_loop(self):
        """Outside an async context, _publish_to_bus spawns a daemon thread."""
        emitter = _make_emitter()
        bus = _make_bus_mock()
        event = SSEEvent(event_type=SSEEventType.session_created, data={})

        threads_started: list[threading.Thread] = []
        real_start = threading.Thread.start

        def capture_start(self_thread: threading.Thread) -> None:
            threads_started.append(self_thread)
            real_start(self_thread)

        with (
            patch(self._BUS_TARGET, return_value=bus),
            patch.object(threading.Thread, "start", capture_start),
        ):
            emitter._publish_to_bus(event)

        # Wait for the thread so the test doesn't leave dangling resources
        for t in threads_started:
            t.join(timeout=2.0)

        assert len(threads_started) == 1
        assert threads_started[0].daemon is True

    def test_publish_bus_error_swallowed_and_logged(self):
        """If get_event_bus() raises, _publish_to_bus catches and logs it."""
        emitter = _make_emitter()
        event = SSEEvent(event_type=SSEEventType.evaluator_failed, data={})

        with (
            patch(self._BUS_TARGET, side_effect=RuntimeError("bus unavailable")),
            patch("forge_harness.webhook_server.services.event_emitter.logger") as mock_log,
        ):
            emitter._publish_to_bus(event)  # must not raise

        mock_log.warning.assert_called()

    def test_thread_fallback_is_daemon(self):
        """The fallback thread carries daemon=True so tests don't hang."""
        emitter = _make_emitter()
        bus = _make_bus_mock()
        event = SSEEvent(event_type=SSEEventType.agent_heartbeat, data={})

        created_threads: list[threading.Thread] = []
        original_init = threading.Thread.__init__

        def spy_init(self_t, **kwargs):
            original_init(self_t, **kwargs)
            created_threads.append(self_t)

        with (
            patch(self._BUS_TARGET, return_value=bus),
            patch.object(threading.Thread, "__init__", spy_init),
            patch.object(threading.Thread, "start"),
        ):
            emitter._publish_to_bus(event)

        assert len(created_threads) == 1
        assert created_threads[0].daemon is True


# ===========================================================================
# EventEmitter.get_recent_events
# ===========================================================================


class TestGetRecentEvents:
    def _load(self, emitter: EventEmitter, types_and_data: list[tuple]) -> None:
        for et, d in types_and_data:
            with patch.object(emitter, "_publish_to_bus"):
                emitter.emit(et, d)

    def test_returns_empty_list_when_no_events(self):
        emitter = _make_emitter()
        assert emitter.get_recent_events() == []

    def test_returns_all_events_up_to_default_limit(self):
        emitter = _make_emitter(maxlen=50)
        self._load(
            emitter,
            [(SSEEventType.feature_updated, {"i": i}) for i in range(5)],
        )
        result = emitter.get_recent_events()
        assert len(result) == 5

    def test_limit_respected(self):
        emitter = _make_emitter(maxlen=50)
        self._load(
            emitter,
            [(SSEEventType.session_created, {"i": i}) for i in range(15)],
        )
        result = emitter.get_recent_events(limit=5)
        assert len(result) == 5

    def test_returns_newest_when_limited(self):
        """With limit=2, we expect the last 2 emitted events."""
        emitter = _make_emitter(maxlen=50)
        self._load(
            emitter,
            [(SSEEventType.task_status_changed, {"seq": i}) for i in range(5)],
        )
        result = emitter.get_recent_events(limit=2)
        # Last two seqs are 3 and 4
        seqs = [e.data["seq"] for e in result]
        assert seqs == [3, 4]

    def test_filter_by_event_type(self):
        emitter = _make_emitter(maxlen=50)
        self._load(
            emitter,
            [
                (SSEEventType.feature_updated, {"x": 1}),
                (SSEEventType.session_created, {"x": 2}),
                (SSEEventType.feature_updated, {"x": 3}),
            ],
        )
        result = emitter.get_recent_events(event_type="feature.updated")
        assert len(result) == 2
        assert all(e.event_type == SSEEventType.feature_updated for e in result)

    def test_filter_returns_empty_when_no_match(self):
        emitter = _make_emitter(maxlen=50)
        self._load(emitter, [(SSEEventType.session_ended, {})])
        result = emitter.get_recent_events(event_type="evaluator.completed")
        assert result == []

    def test_limit_zero_returns_empty_list(self):
        emitter = _make_emitter(maxlen=50)
        self._load(emitter, [(SSEEventType.feature_updated, {})])
        result = emitter.get_recent_events(limit=0)
        assert result == []

    def test_limit_larger_than_buffer_returns_all(self):
        emitter = _make_emitter(maxlen=50)
        self._load(
            emitter,
            [(SSEEventType.evaluator_completed, {"i": i}) for i in range(3)],
        )
        result = emitter.get_recent_events(limit=100)
        assert len(result) == 3

    def test_filter_and_limit_combined(self):
        emitter = _make_emitter(maxlen=50)
        self._load(
            emitter,
            [(SSEEventType.feature_updated, {"i": i}) for i in range(8)],
        )
        result = emitter.get_recent_events(limit=3, event_type="feature.updated")
        assert len(result) == 3
        # Should be the last 3
        assert [e.data["i"] for e in result] == [5, 6, 7]

    def test_returns_list_not_deque(self):
        emitter = _make_emitter()
        result = emitter.get_recent_events()
        assert isinstance(result, list)

    def test_returned_list_is_copy(self):
        """Mutating the returned list must not affect the internal ring."""
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.agent_heartbeat, {})
        result = emitter.get_recent_events()
        result.clear()
        assert emitter.event_count() == 1


# ===========================================================================
# EventEmitter.subscribe / unsubscribe
# ===========================================================================


class TestSubscribeUnsubscribe:
    def test_subscribe_registers_callback(self):
        emitter = _make_emitter()
        cb = MagicMock()
        emitter.subscribe(cb)
        assert emitter.subscriber_count() == 1

    def test_subscribe_multiple_callbacks(self):
        emitter = _make_emitter()
        emitter.subscribe(MagicMock())
        emitter.subscribe(MagicMock())
        emitter.subscribe(MagicMock())
        assert emitter.subscriber_count() == 3

    def test_unsubscribe_removes_callback(self):
        emitter = _make_emitter()
        cb = MagicMock()
        emitter.subscribe(cb)
        emitter.unsubscribe(cb)
        assert emitter.subscriber_count() == 0

    def test_unsubscribe_unknown_callback_is_noop(self):
        emitter = _make_emitter()
        # Should not raise
        emitter.unsubscribe(MagicMock())
        assert emitter.subscriber_count() == 0

    def test_unsubscribe_only_removes_specified_callback(self):
        emitter = _make_emitter()
        cb_a = MagicMock()
        cb_b = MagicMock()
        emitter.subscribe(cb_a)
        emitter.subscribe(cb_b)
        emitter.unsubscribe(cb_a)
        assert emitter.subscriber_count() == 1

    def test_unsubscribe_then_emit_does_not_call_removed_callback(self):
        emitter = _make_emitter()
        cb = MagicMock()
        emitter.subscribe(cb)
        emitter.unsubscribe(cb)
        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.feature_updated, {})
        cb.assert_not_called()


# ===========================================================================
# EventEmitter.event_count / subscriber_count
# ===========================================================================


class TestIntrospection:
    def test_event_count_zero_initially(self):
        assert _make_emitter().event_count() == 0

    def test_event_count_increments_on_emit(self):
        emitter = _make_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.session_created, {})
        assert emitter.event_count() == 1

    def test_event_count_bounded_by_maxlen(self):
        emitter = _make_emitter(maxlen=2)
        with patch.object(emitter, "_publish_to_bus"):
            for _ in range(5):
                emitter.emit(SSEEventType.task_status_changed, {})
        assert emitter.event_count() == 2

    def test_subscriber_count_zero_initially(self):
        assert _make_emitter().subscriber_count() == 0

    def test_subscriber_count_after_subscribe(self):
        emitter = _make_emitter()
        emitter.subscribe(MagicMock())
        emitter.subscribe(MagicMock())
        assert emitter.subscriber_count() == 2

    def test_subscriber_count_after_unsubscribe(self):
        emitter = _make_emitter()
        cb = MagicMock()
        emitter.subscribe(cb)
        emitter.unsubscribe(cb)
        assert emitter.subscriber_count() == 0


# ===========================================================================
# get_event_emitter — singleton factory
# ===========================================================================


class TestGetEventEmitter:
    def test_returns_event_emitter_instance(self):
        emitter = get_event_emitter()
        assert isinstance(emitter, EventEmitter)

    def test_returns_same_instance_on_repeated_calls(self):
        first = get_event_emitter()
        second = get_event_emitter()
        assert first is second

    def test_first_call_creates_instance_and_logs(self):
        with patch("forge_harness.webhook_server.services.event_emitter.logger") as mock_log:
            get_event_emitter()
        mock_log.info.assert_called_once()

    def test_custom_maxlen_applied_on_first_call(self):
        emitter = get_event_emitter(maxlen=42)
        assert emitter._recent.maxlen == 42

    def test_maxlen_ignored_on_subsequent_calls(self):
        """Second call with different maxlen must NOT recreate the emitter."""
        first = get_event_emitter(maxlen=10)
        second = get_event_emitter(maxlen=999)
        # Same object — original maxlen preserved
        assert second is first
        assert second._recent.maxlen == 10

    def test_state_persists_across_calls(self):
        emitter = get_event_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.agent_heartbeat, {})
        again = get_event_emitter()
        assert again.event_count() == 1


# ===========================================================================
# reset_event_emitter
# ===========================================================================


class TestResetEventEmitter:
    def test_reset_allows_new_singleton(self):
        first = get_event_emitter()
        reset_event_emitter()
        second = get_event_emitter()
        assert first is not second

    def test_reset_clears_ring_buffer(self):
        emitter = get_event_emitter()
        with patch.object(emitter, "_publish_to_bus"):
            emitter.emit(SSEEventType.feature_updated, {})
        reset_event_emitter()
        fresh = get_event_emitter()
        assert fresh.event_count() == 0

    def test_reset_clears_callbacks(self):
        emitter = get_event_emitter()
        emitter.subscribe(MagicMock())
        reset_event_emitter()
        fresh = get_event_emitter()
        assert fresh.subscriber_count() == 0

    def test_reset_when_no_instance_does_not_raise(self):
        # Already reset by autouse fixture — calling again is a no-op
        reset_event_emitter()  # must not raise

    def test_reset_returns_none(self):
        result = reset_event_emitter()
        assert result is None


# ===========================================================================
# Thread-safety smoke tests
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_emits_do_not_corrupt_ring(self):
        """Multiple threads emitting concurrently should not lose events or crash."""
        emitter = _make_emitter(maxlen=200)

        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                for i in range(10):
                    with patch.object(emitter, "_publish_to_bus"):
                        emitter.emit(SSEEventType.task_status_changed, {"t": idx, "i": i})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert emitter.event_count() == 50

    def test_concurrent_subscribe_unsubscribe_safe(self):
        """Rapidly subscribing and unsubscribing from multiple threads must not raise."""
        emitter = _make_emitter()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                cb = MagicMock()
                emitter.subscribe(cb)
                emitter.unsubscribe(cb)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

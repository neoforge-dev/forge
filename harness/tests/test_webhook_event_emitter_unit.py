"""Unit tests for forge_harness.webhook_server.services.event_emitter (73 lines, 0% target).

Comprehensive coverage of:
- EventEmitter construction: maxlen, deque type, RLock, empty initial state
- emit(): return type, field mapping, ring-buffer storage, overflow eviction,
  _publish_to_bus delegation, synchronous callback invocation and error isolation
- _publish_to_bus(): running-loop path (create_task), no-loop fallback (daemon
  thread), exception swallowing, correct bus.publish argument forwarding
- get_recent_events(): unfiltered, filtered, limit slicing, empty buffer,
  limit=0 edge-case, copy isolation
- subscribe() / unsubscribe(): callback registration, idempotent remove
- event_count() / subscriber_count(): accurate introspection
- get_event_emitter(): singleton semantics, custom maxlen, state persistence
- reset_event_emitter(): isolation, idempotence

All external I/O (event bus, asyncio loop, threading.Thread) is mocked.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque

# Using built-in list type, no import needed
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
# Module-level helpers
# ---------------------------------------------------------------------------

_HB = SSEEventType.agent_heartbeat
_FU = SSEEventType.feature_updated
_SC = SSEEventType.session_created
_SE = SSEEventType.session_ended
_EC = SSEEventType.evaluator_completed
_EF = SSEEventType.evaluator_failed
_TS = SSEEventType.task_status_changed
_SW = SSEEventType.task_slo_warning
_SB = SSEEventType.task_slo_breached
_FA = SSEEventType.feature_assigned

# Patch target for get_event_bus (imported at call-time inside _publish_to_bus)
_BUS_TARGET = "forge_harness.webhook_server.services.event_bus.get_event_bus"
# Patch target for the module-level logger
_LOGGER_TARGET = "forge_harness.webhook_server.services.event_emitter.logger"


def _fresh(maxlen: int = 50) -> EventEmitter:
    """Return a brand-new EventEmitter without touching the singleton."""
    return EventEmitter(maxlen=maxlen)


def _mock_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _emit_silent(em: EventEmitter, event_type: SSEEventType, data: dict) -> SSEEvent:
    """Emit an event with _publish_to_bus suppressed."""
    with patch.object(em, "_publish_to_bus"):
        return em.emit(event_type, data)


# ---------------------------------------------------------------------------
# Autouse fixture: guarantee singleton isolation for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_singleton():
    reset_event_emitter()
    yield
    reset_event_emitter()


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """EventEmitter.__init__ produces the correct initial state."""

    def test_default_maxlen_equals_module_constant(self):
        em = EventEmitter()
        assert em._recent.maxlen == _DEQUE_MAXLEN

    def test_custom_maxlen_is_applied(self):
        em = _fresh(maxlen=17)
        assert em._recent.maxlen == 17

    def test_recent_is_a_deque_instance(self):
        em = _fresh()
        assert isinstance(em._recent, deque)

    def test_recent_buffer_starts_empty(self):
        em = _fresh()
        assert len(em._recent) == 0

    def test_callbacks_list_starts_empty(self):
        em = _fresh()
        assert em._callbacks == []

    def test_lock_is_reentrant(self):
        """RLock allows re-acquisition from the same thread without deadlock."""
        em = _fresh()
        with em._lock:
            # Acquire again from same thread — RLock allows this
            acquired = em._lock.acquire(blocking=False)
            assert acquired, "Lock should be reentrant (RLock)"
            em._lock.release()


# ===========================================================================
# 2. emit() — return value and field mapping
# ===========================================================================


class TestEmitReturnValue:
    """emit() constructs and returns the correct SSEEvent."""

    def test_returns_sse_event_instance(self):
        em = _fresh()
        result = _emit_silent(em, _FU, {"id": "f-1"})
        assert isinstance(result, SSEEvent)

    def test_event_type_matches_argument(self):
        em = _fresh()
        result = _emit_silent(em, _SC, {})
        assert result.event_type == _SC

    def test_data_dict_matches_argument(self):
        payload = {"feature_id": "x-99", "status": "pending"}
        em = _fresh()
        result = _emit_silent(em, _EC, payload)
        assert result.data == payload

    def test_default_source_is_webhook_server(self):
        em = _fresh()
        result = _emit_silent(em, _HB, {})
        assert result.source_service == "webhook-server"

    def test_custom_source_is_propagated(self):
        em = _fresh()
        result = _emit_silent(em, _HB, {}, )
        with patch.object(em, "_publish_to_bus"):
            result2 = em.emit(_HB, {}, source="my-tracker")
        assert result2.source_service == "my-tracker"

    def test_returned_event_has_timestamp(self):
        em = _fresh()
        result = _emit_silent(em, _FU, {})
        assert isinstance(result.timestamp, str)
        assert len(result.timestamp) > 0


# ===========================================================================
# 3. emit() — ring buffer behaviour
# ===========================================================================


class TestEmitRingBuffer:
    """emit() correctly stores events in the bounded ring buffer."""

    def test_single_emit_increments_buffer(self):
        em = _fresh()
        _emit_silent(em, _HB, {})
        assert em.event_count() == 1

    def test_emitted_event_is_retrievable(self):
        em = _fresh()
        evt = _emit_silent(em, _TS, {"seq": 7})
        events = em.get_recent_events(limit=1)
        assert events[0] is evt

    def test_multiple_emits_accumulate_in_order(self):
        em = _fresh()
        evts = [_emit_silent(em, _TS, {"n": i}) for i in range(4)]
        stored = list(em._recent)
        assert stored == evts

    def test_overflow_evicts_oldest_entries(self):
        em = _fresh(maxlen=3)
        for i in range(5):
            _emit_silent(em, _TS, {"i": i})
        assert em.event_count() == 3
        remaining = [e.data["i"] for e in em._recent]
        assert remaining == [2, 3, 4]

    def test_emit_delegates_to_publish_to_bus_once(self):
        em = _fresh()
        with patch.object(em, "_publish_to_bus") as mock_pub:
            em.emit(_HB, {})
        mock_pub.assert_called_once()

    def test_emit_passes_returned_event_to_publish_to_bus(self):
        em = _fresh()
        captured: list[SSEEvent] = []
        em._publish_to_bus = captured.append
        result = em.emit(_FU, {"k": "v"})
        assert len(captured) == 1
        assert captured[0] is result


# ===========================================================================
# 4. emit() — synchronous callback dispatch
# ===========================================================================


class TestEmitCallbackDispatch:
    """emit() calls registered synchronous callbacks with the emitted event."""

    def test_single_callback_receives_event(self):
        em = _fresh()
        received: list[SSEEvent] = []
        em.subscribe(received.append)
        evt = _emit_silent(em, _FA, {"agent": "kimi"})
        assert received == [evt]

    def test_two_callbacks_both_invoked(self):
        em = _fresh()
        calls_a: list[SSEEvent] = []
        calls_b: list[SSEEvent] = []
        em.subscribe(calls_a.append)
        em.subscribe(calls_b.append)
        _emit_silent(em, _SC, {})
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_callback_exception_does_not_propagate(self):
        em = _fresh()
        em.subscribe(lambda e: 1 / 0)
        _emit_silent(em, _HB, {})  # must not raise

    def test_later_callback_still_called_after_earlier_raises(self):
        em = _fresh()
        second: list[SSEEvent] = []
        em.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("fail")))
        em.subscribe(second.append)
        evt = _emit_silent(em, _SB, {})
        assert second == [evt]

    def test_callback_exception_triggers_warning_log(self):
        em = _fresh()
        em.subscribe(lambda e: (_ for _ in ()).throw(ValueError("boom")))
        with (
            patch(_LOGGER_TARGET) as mock_log,
            patch.object(em, "_publish_to_bus"),
        ):
            em.emit(_SW, {})
        mock_log.warning.assert_called()

    def test_no_callbacks_emits_without_error(self):
        em = _fresh()
        _emit_silent(em, _EC, {})  # must not raise

    def test_emit_logs_debug_message(self):
        em = _fresh()
        with (
            patch(_LOGGER_TARGET) as mock_log,
            patch.object(em, "_publish_to_bus"),
        ):
            em.emit(_FU, {})
        mock_log.debug.assert_called()


# ===========================================================================
# 5. _publish_to_bus() — async loop path
# ===========================================================================


class TestPublishToBusLoopPath:
    """When an event loop is running, _publish_to_bus uses loop.create_task."""

    def test_create_task_is_called_when_loop_running(self):
        em = _fresh()
        event = SSEEvent(event_type=_FU, data={"x": 1})
        # Use plain MagicMock for publish so no unawaited coroutine warning is
        # raised — we only care that create_task is called, not that the
        # coroutine is actually awaited (create_task is itself mocked).
        mock_bus = MagicMock()
        mock_bus.publish = MagicMock(return_value=MagicMock())
        mock_loop = MagicMock()

        with (
            patch(_BUS_TARGET, return_value=mock_bus),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            em._publish_to_bus(event)

        mock_loop.create_task.assert_called_once()

    def test_bus_publish_called_with_correct_event_type(self):
        em = _fresh()
        event = SSEEvent(event_type=_SE, data={}, source_service="svc-z")
        mock_bus = MagicMock()
        mock_coro = MagicMock()
        mock_bus.publish = MagicMock(return_value=mock_coro)
        mock_loop = MagicMock()

        with (
            patch(_BUS_TARGET, return_value=mock_bus),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            em._publish_to_bus(event)

        mock_bus.publish.assert_called_once_with(
            event_type="session.ended",
            data=event.data,
            source=event.source_service,
        )

    def test_create_task_receives_coroutine_from_bus_publish(self):
        em = _fresh()
        event = SSEEvent(event_type=_EC, data={"r": 1})
        mock_coro = MagicMock()
        mock_bus = MagicMock()
        mock_bus.publish = MagicMock(return_value=mock_coro)
        mock_loop = MagicMock()

        with (
            patch(_BUS_TARGET, return_value=mock_bus),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            em._publish_to_bus(event)

        mock_loop.create_task.assert_called_once_with(mock_coro)

    @pytest.mark.asyncio
    async def test_async_context_actually_publishes(self):
        """Inside a real event loop, bus.publish coroutine is awaited."""
        em = _fresh()
        bus = _mock_bus()
        event = SSEEvent(event_type=_HB, data={"ping": 1})

        with patch(_BUS_TARGET, return_value=bus):
            em._publish_to_bus(event)
            await asyncio.sleep(0)  # let the scheduled task run

        bus.publish.assert_awaited_once_with(
            event_type="agent.heartbeat",
            data={"ping": 1},
            source="webhook-server",
        )


# ===========================================================================
# 6. _publish_to_bus() — no-loop fallback path
# ===========================================================================


class TestPublishToBusThreadFallback:
    """When no event loop is running, _publish_to_bus falls back to a daemon thread."""

    def test_daemon_thread_is_spawned(self):
        em = _fresh()
        # Use plain MagicMock — thread.start() is suppressed anyway, so the
        # publish coroutine is never created and no unawaited-coroutine warning
        # can fire.
        bus = MagicMock()
        bus.publish = MagicMock(return_value=MagicMock())
        event = SSEEvent(event_type=_SC, data={})
        started: list[threading.Thread] = []

        def capture_start(self_t: threading.Thread) -> None:
            started.append(self_t)

        with (
            patch(_BUS_TARGET, return_value=bus),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
            patch.object(threading.Thread, "start", capture_start),
        ):
            em._publish_to_bus(event)

        assert len(started) == 1
        assert started[0].daemon is True

    def test_thread_is_marked_daemon_true(self):
        em = _fresh()
        bus = MagicMock()
        bus.publish = MagicMock(return_value=MagicMock())
        event = SSEEvent(event_type=_EF, data={})
        created: list[threading.Thread] = []
        original_init = threading.Thread.__init__

        def spy_init(self_t: threading.Thread, **kwargs) -> None:
            original_init(self_t, **kwargs)
            created.append(self_t)

        with (
            patch(_BUS_TARGET, return_value=bus),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
            patch.object(threading.Thread, "__init__", spy_init),
            patch.object(threading.Thread, "start"),
        ):
            em._publish_to_bus(event)

        assert created and created[0].daemon is True


# ===========================================================================
# 7. _publish_to_bus() — error swallowing
# ===========================================================================


class TestPublishToBusErrorHandling:
    """_publish_to_bus swallows all exceptions and logs a warning."""

    def test_get_event_bus_exception_is_swallowed(self):
        em = _fresh()
        event = SSEEvent(event_type=_HB, data={})
        with patch(_BUS_TARGET, side_effect=Exception("bus offline")):
            em._publish_to_bus(event)  # must not raise

    def test_get_event_bus_exception_is_logged(self):
        em = _fresh()
        event = SSEEvent(event_type=_FU, data={})
        with (
            patch(_BUS_TARGET, side_effect=RuntimeError("unavailable")),
            patch(_LOGGER_TARGET) as mock_log,
        ):
            em._publish_to_bus(event)
        mock_log.warning.assert_called()

    def test_arbitrary_exception_during_loop_path_is_swallowed(self):
        em = _fresh()
        event = SSEEvent(event_type=_TS, data={})
        mock_loop = MagicMock()
        mock_loop.create_task.side_effect = RuntimeError("task creation failed")
        # Plain MagicMock avoids unawaited-coroutine warnings when create_task
        # raises before the coroutine can be scheduled.
        mock_bus = MagicMock()
        mock_bus.publish = MagicMock(return_value=MagicMock())
        with (
            patch(_BUS_TARGET, return_value=mock_bus),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            em._publish_to_bus(event)  # must not raise


# ===========================================================================
# 8. get_recent_events()
# ===========================================================================


class TestGetRecentEvents:
    """get_recent_events() returns the correct slice from the ring buffer."""

    def test_empty_buffer_returns_empty_list(self):
        em = _fresh()
        assert em.get_recent_events() == []

    def test_returns_list_type(self):
        em = _fresh()
        _emit_silent(em, _HB, {})
        result = em.get_recent_events()
        assert isinstance(result, list)

    def test_all_events_returned_when_under_limit(self):
        em = _fresh(maxlen=50)
        for _ in range(4):
            _emit_silent(em, _FU, {})
        assert len(em.get_recent_events(limit=10)) == 4

    def test_limit_returns_newest_subset(self):
        em = _fresh(maxlen=50)
        evts = [_emit_silent(em, _TS, {"n": i}) for i in range(6)]
        result = em.get_recent_events(limit=3)
        assert result == evts[-3:]

    def test_limit_zero_returns_empty(self):
        em = _fresh()
        _emit_silent(em, _HB, {})
        assert em.get_recent_events(limit=0) == []

    def test_filter_by_event_type_string(self):
        em = _fresh(maxlen=50)
        _emit_silent(em, _HB, {"n": 1})
        _emit_silent(em, _FU, {"n": 2})
        _emit_silent(em, _HB, {"n": 3})
        result = em.get_recent_events(event_type="agent.heartbeat")
        assert len(result) == 2
        assert all(e.event_type == _HB for e in result)

    def test_filter_nonexistent_type_returns_empty(self):
        em = _fresh()
        _emit_silent(em, _HB, {})
        assert em.get_recent_events(event_type="does.not.exist") == []

    def test_none_event_type_returns_all(self):
        em = _fresh(maxlen=50)
        _emit_silent(em, _HB, {})
        _emit_silent(em, _FU, {})
        assert len(em.get_recent_events(event_type=None)) == 2

    def test_filter_and_limit_combined(self):
        em = _fresh(maxlen=50)
        for i in range(7):
            _emit_silent(em, _FU, {"i": i})
        _emit_silent(em, _HB, {"x": 0})  # different type, should be filtered out
        result = em.get_recent_events(limit=3, event_type="feature.updated")
        assert len(result) == 3
        assert [e.data["i"] for e in result] == [4, 5, 6]

    def test_returned_list_is_independent_copy(self):
        """Mutating the returned list must not affect the internal ring buffer."""
        em = _fresh()
        _emit_silent(em, _SC, {})
        result = em.get_recent_events()
        result.clear()
        assert em.event_count() == 1

    def test_limit_larger_than_buffer_returns_all(self):
        em = _fresh(maxlen=50)
        for _ in range(3):
            _emit_silent(em, _EC, {})
        assert len(em.get_recent_events(limit=100)) == 3


# ===========================================================================
# 9. subscribe() / unsubscribe()
# ===========================================================================


class TestSubscribeUnsubscribe:
    """subscribe() and unsubscribe() manage the callback list correctly."""

    def test_subscribe_adds_callback(self):
        em = _fresh()
        cb = MagicMock()
        em.subscribe(cb)
        assert cb in em._callbacks

    def test_subscriber_count_increases(self):
        em = _fresh()
        em.subscribe(MagicMock())
        em.subscribe(MagicMock())
        assert em.subscriber_count() == 2

    def test_unsubscribe_removes_callback(self):
        em = _fresh()
        cb = MagicMock()
        em.subscribe(cb)
        em.unsubscribe(cb)
        assert cb not in em._callbacks

    def test_unsubscribe_unknown_is_noop(self):
        em = _fresh()
        em.unsubscribe(MagicMock())  # must not raise
        assert em.subscriber_count() == 0

    def test_unsubscribe_only_removes_specified(self):
        em = _fresh()
        cb_a = MagicMock()
        cb_b = MagicMock()
        em.subscribe(cb_a)
        em.subscribe(cb_b)
        em.unsubscribe(cb_a)
        assert cb_b in em._callbacks
        assert cb_a not in em._callbacks

    def test_unsubscribed_callback_not_called_on_emit(self):
        em = _fresh()
        cb = MagicMock()
        em.subscribe(cb)
        em.unsubscribe(cb)
        _emit_silent(em, _HB, {})
        cb.assert_not_called()


# ===========================================================================
# 10. event_count() / subscriber_count()
# ===========================================================================


class TestIntrospection:
    """Introspection methods return accurate counts."""

    def test_event_count_zero_on_fresh_emitter(self):
        assert _fresh().event_count() == 0

    def test_event_count_increments_per_emit(self):
        em = _fresh()
        _emit_silent(em, _FU, {})
        _emit_silent(em, _SC, {})
        assert em.event_count() == 2

    def test_event_count_bounded_by_maxlen(self):
        em = _fresh(maxlen=3)
        for _ in range(7):
            _emit_silent(em, _TS, {})
        assert em.event_count() == 3

    def test_subscriber_count_zero_on_fresh_emitter(self):
        assert _fresh().subscriber_count() == 0

    def test_subscriber_count_after_subscribe(self):
        em = _fresh()
        em.subscribe(MagicMock())
        assert em.subscriber_count() == 1

    def test_subscriber_count_decrements_after_unsubscribe(self):
        em = _fresh()
        cb = MagicMock()
        em.subscribe(cb)
        em.unsubscribe(cb)
        assert em.subscriber_count() == 0


# ===========================================================================
# 11. get_event_emitter() — singleton semantics
# ===========================================================================


class TestGetEventEmitter:
    """get_event_emitter() returns and preserves a module-level singleton."""

    def test_returns_event_emitter_instance(self):
        em = get_event_emitter()
        assert isinstance(em, EventEmitter)

    def test_two_calls_return_same_object(self):
        assert get_event_emitter() is get_event_emitter()

    def test_custom_maxlen_applied_on_first_call(self):
        em = get_event_emitter(maxlen=33)
        assert em._recent.maxlen == 33

    def test_maxlen_argument_ignored_after_first_call(self):
        first = get_event_emitter(maxlen=33)
        second = get_event_emitter(maxlen=500)
        assert second is first
        assert second._recent.maxlen == 33

    def test_singleton_state_persists_between_calls(self):
        em = get_event_emitter()
        _emit_silent(em, _HB, {})
        assert get_event_emitter().event_count() == 1

    def test_singleton_creation_logs_info(self):
        with patch(_LOGGER_TARGET) as mock_log:
            get_event_emitter()
        mock_log.info.assert_called_once()


# ===========================================================================
# 12. reset_event_emitter()
# ===========================================================================


class TestResetEventEmitter:
    """reset_event_emitter() destroys the singleton for test isolation."""

    def test_reset_returns_none(self):
        assert reset_event_emitter() is None

    def test_reset_creates_fresh_singleton_on_next_call(self):
        first = get_event_emitter()
        reset_event_emitter()
        second = get_event_emitter()
        assert second is not first

    def test_reset_clears_ring_buffer(self):
        em = get_event_emitter()
        _emit_silent(em, _FU, {})
        reset_event_emitter()
        assert get_event_emitter().event_count() == 0

    def test_reset_clears_subscribers(self):
        em = get_event_emitter()
        em.subscribe(MagicMock())
        reset_event_emitter()
        assert get_event_emitter().subscriber_count() == 0

    def test_double_reset_does_not_raise(self):
        reset_event_emitter()
        reset_event_emitter()  # must not raise

    def test_reset_without_prior_creation_does_not_raise(self):
        # autouse fixture already resets; this is a belt-and-suspenders call
        reset_event_emitter()  # must not raise


# ===========================================================================
# 13. Thread-safety smoke tests
# ===========================================================================


class TestThreadSafety:
    """Concurrent access must not corrupt internal state or raise exceptions."""

    def test_concurrent_emits_do_not_corrupt_count(self):
        em = _fresh(maxlen=200)
        # Suppress bus dispatch before threads start — avoids per-thread patching
        em._publish_to_bus = lambda evt: None  # type: ignore[method-assign]
        errors: list[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for seq in range(10):
                    em.emit(_TS, {"w": worker_id, "s": seq})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Unexpected thread errors: {errors}"
        assert em.event_count() == 50  # 5 workers * 10 emits each

    def test_concurrent_subscribe_unsubscribe_no_exception(self):
        em = _fresh()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                cb = MagicMock()
                em.subscribe(cb)
                em.unsubscribe(cb)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []

    def test_concurrent_reads_while_emitting(self):
        em = _fresh(maxlen=100)
        # Replace _publish_to_bus with a no-op before spawning threads so that
        # patch.object context managers are not needed inside thread bodies
        # (context managers across thread boundaries are unreliable).
        em._publish_to_bus = lambda evt: None  # type: ignore[method-assign]
        errors: list[Exception] = []

        def emit_worker() -> None:
            try:
                for _ in range(15):
                    em.emit(_HB, {})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def read_worker() -> None:
            try:
                for _ in range(15):
                    em.get_recent_events(limit=5)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=emit_worker) for _ in range(3)]
        threads += [threading.Thread(target=read_worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []


# ===========================================================================
# 14. SSEEventType enum coverage — all defined types can be emitted
# ===========================================================================


class TestAllEventTypesEmittable:
    """Every SSEEventType value should round-trip through emit cleanly."""

    @pytest.mark.parametrize(
        "event_type",
        [
            SSEEventType.feature_updated,
            SSEEventType.feature_assigned,
            SSEEventType.evaluator_completed,
            SSEEventType.evaluator_failed,
            SSEEventType.session_created,
            SSEEventType.session_ended,
            SSEEventType.task_status_changed,
            SSEEventType.agent_heartbeat,
            SSEEventType.task_slo_warning,
            SSEEventType.task_slo_breached,
        ],
    )
    def test_emit_and_retrieve(self, event_type: SSEEventType):
        em = _fresh(maxlen=50)
        evt = _emit_silent(em, event_type, {"type_check": True})
        assert evt.event_type == event_type
        result = em.get_recent_events(event_type=event_type.value)
        assert len(result) == 1
        assert result[0] is evt

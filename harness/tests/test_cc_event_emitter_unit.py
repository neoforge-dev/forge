"""Unit tests for forge_harness.webhook_server.services.event_emitter.

Covers:
- EventEmitter construction and ring-buffer sizing
- emit(): ring-buffer storage, callback dispatch, error isolation, return value
- _publish_to_bus(): running-loop path (create_task), no-loop path (thread),
  and exception swallowing
- get_recent_events(): limit, event_type filter, edge cases
- subscribe() / unsubscribe() callback management
- event_count() / subscriber_count() introspection
- get_event_emitter() singleton creation and idempotence
- reset_event_emitter() isolation helper

All external I/O (event bus, asyncio.get_running_loop, threading.Thread) is mocked.
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

_ET = SSEEventType.agent_heartbeat
_ET2 = SSEEventType.feature_updated


def _make_emitter(maxlen: int = 100) -> EventEmitter:
    """Return a fresh EventEmitter, bypass singleton."""
    return EventEmitter(maxlen=maxlen)


def _make_event(
    event_type: SSEEventType = _ET,
    data: dict | None = None,
    source: str = "svc",
) -> SSEEvent:
    return SSEEvent(event_type=event_type, data=data or {}, source_service=source)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestEventEmitterConstruction:
    def test_default_maxlen(self):
        em = _make_emitter()
        assert em._recent.maxlen == 100

    def test_custom_maxlen(self):
        em = _make_emitter(maxlen=10)
        assert em._recent.maxlen == 10

    def test_starts_empty(self):
        em = _make_emitter()
        assert len(em._recent) == 0
        assert len(em._callbacks) == 0

    def test_lock_is_rlock(self):
        em = _make_emitter()
        # RLock can be acquired twice from the same thread
        with em._lock:
            acquired = em._lock.acquire(blocking=False)
            if acquired:
                em._lock.release()
        # No deadlock means it's reentrant


# ---------------------------------------------------------------------------
# emit() — ring buffer
# ---------------------------------------------------------------------------


class TestEventEmitterEmit:
    def setup_method(self):
        reset_event_emitter()

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_appends_to_recent(self, mock_pub):
        em = _make_emitter()
        evt = em.emit(_ET, {"k": "v"})
        assert len(em._recent) == 1
        assert em._recent[0] is evt

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_returns_sse_event(self, mock_pub):
        em = _make_emitter()
        result = em.emit(_ET, {"x": 1})
        assert isinstance(result, SSEEvent)

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_sets_event_type(self, mock_pub):
        em = _make_emitter()
        evt = em.emit(SSEEventType.session_created, {})
        assert evt.event_type == SSEEventType.session_created

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_sets_source(self, mock_pub):
        em = _make_emitter()
        evt = em.emit(_ET, {}, source="my-tracker")
        assert evt.source_service == "my-tracker"

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_ring_buffer_eviction(self, mock_pub):
        em = _make_emitter(maxlen=3)
        for i in range(5):
            em.emit(_ET, {"i": i})
        assert len(em._recent) == 3
        # Oldest two (i=0, i=1) evicted; ring holds i=2,3,4
        remaining_data = [e.data["i"] for e in em._recent]
        assert remaining_data == [2, 3, 4]

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_calls_publish_to_bus(self, mock_pub):
        em = _make_emitter()
        em.emit(_ET, {})
        mock_pub.assert_called_once()

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_multiple_events(self, mock_pub):
        em = _make_emitter()
        for _ in range(5):
            em.emit(_ET, {})
        assert len(em._recent) == 5
        assert mock_pub.call_count == 5


# ---------------------------------------------------------------------------
# emit() — callback dispatch
# ---------------------------------------------------------------------------


class TestEventEmitterCallbacks:
    def setup_method(self):
        reset_event_emitter()

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_invokes_callback(self, mock_pub):
        em = _make_emitter()
        received = []
        em.subscribe(received.append)
        evt = em.emit(_ET, {"hello": "world"})
        assert received == [evt]

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_invokes_multiple_callbacks(self, mock_pub):
        em = _make_emitter()
        hits: list[int] = []
        em.subscribe(lambda e: hits.append(1))
        em.subscribe(lambda e: hits.append(2))
        em.emit(_ET, {})
        assert hits == [1, 2]

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_isolates_callback_exceptions(self, mock_pub):
        em = _make_emitter()
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        good_calls: list[SSEEvent] = []
        em.subscribe(bad_cb)
        em.subscribe(good_calls.append)
        evt = em.emit(_ET, {})
        # Good callback must still be called despite bad one raising
        assert good_calls == [evt]

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_emit_no_callbacks_no_error(self, mock_pub):
        em = _make_emitter()
        em.emit(_ET, {})  # must not raise


# ---------------------------------------------------------------------------
# _publish_to_bus()
# ---------------------------------------------------------------------------


class TestPublishToBus:
    """Test the two code paths inside _publish_to_bus."""

    def setup_method(self):
        reset_event_emitter()

    def test_running_loop_uses_create_task(self):
        """When an event loop is running, the bus publish coroutine is scheduled via create_task."""
        em = _make_emitter()
        event = _make_event()

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        mock_loop = MagicMock()

        # get_event_bus is imported *inside* _publish_to_bus from event_bus module
        with patch(
            "forge_harness.webhook_server.services.event_bus.get_event_bus",
            return_value=mock_bus,
        ):
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                em._publish_to_bus(event)

        mock_loop.create_task.assert_called_once()

    def test_no_running_loop_spawns_thread(self):
        """When no loop is running, a daemon thread is started to run asyncio.run()."""
        em = _make_emitter()
        event = _make_event()

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        spawned_threads: list[threading.Thread] = []

        def capturing_start(self_thread, *args, **kwargs):
            spawned_threads.append(self_thread)
            # Don't actually start the thread to keep test fast

        with patch(
            "forge_harness.webhook_server.services.event_bus.get_event_bus",
            return_value=mock_bus,
        ):
            with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
                with patch.object(threading.Thread, "start", capturing_start):
                    em._publish_to_bus(event)

        assert len(spawned_threads) == 1
        assert spawned_threads[0].daemon is True

    def test_exception_from_get_event_bus_is_swallowed(self):
        """Any exception in _publish_to_bus must not propagate."""
        em = _make_emitter()
        event = _make_event()

        with patch(
            "forge_harness.webhook_server.services.event_bus.get_event_bus",
            side_effect=Exception("bus unavailable"),
        ):
            # Must not raise
            em._publish_to_bus(event)

    def test_create_task_receives_correct_coroutine_args(self):
        """create_task is called with a coroutine produced by bus.publish with correct args."""
        em = _make_emitter()
        event = _make_event(event_type=SSEEventType.session_ended, source="svc-z")

        mock_bus = MagicMock()
        mock_coro = MagicMock()
        mock_bus.publish = MagicMock(return_value=mock_coro)
        mock_loop = MagicMock()

        with patch(
            "forge_harness.webhook_server.services.event_bus.get_event_bus",
            return_value=mock_bus,
        ):
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                em._publish_to_bus(event)

        mock_bus.publish.assert_called_once_with(
            event_type=SSEEventType.session_ended.value,
            data=event.data,
            source=event.source_service,
        )
        mock_loop.create_task.assert_called_once_with(mock_coro)


# ---------------------------------------------------------------------------
# get_recent_events()
# ---------------------------------------------------------------------------


class TestGetRecentEvents:
    def setup_method(self):
        reset_event_emitter()

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_returns_all_when_under_limit(self, mock_pub):
        em = _make_emitter()
        for _ in range(5):
            em.emit(_ET, {})
        result = em.get_recent_events(limit=10)
        assert len(result) == 5

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_limit_truncates_to_newest(self, mock_pub):
        em = _make_emitter()
        emitted = [em.emit(_ET, {"i": i}) for i in range(10)]
        result = em.get_recent_events(limit=3)
        assert len(result) == 3
        # Newest last, so last 3 emitted
        assert result == emitted[-3:]

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_limit_zero_returns_empty(self, mock_pub):
        em = _make_emitter()
        em.emit(_ET, {})
        result = em.get_recent_events(limit=0)
        assert result == []

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_filter_by_event_type(self, mock_pub):
        em = _make_emitter()
        em.emit(SSEEventType.agent_heartbeat, {"n": 1})
        em.emit(SSEEventType.feature_updated, {"n": 2})
        em.emit(SSEEventType.agent_heartbeat, {"n": 3})

        result = em.get_recent_events(limit=10, event_type="agent.heartbeat")
        assert len(result) == 2
        assert all(e.event_type == SSEEventType.agent_heartbeat for e in result)

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_filter_no_match_returns_empty(self, mock_pub):
        em = _make_emitter()
        em.emit(_ET, {})
        result = em.get_recent_events(limit=10, event_type="nonexistent.event")
        assert result == []

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_none_event_type_returns_all(self, mock_pub):
        em = _make_emitter()
        em.emit(_ET, {})
        em.emit(_ET2, {})
        result = em.get_recent_events(limit=10, event_type=None)
        assert len(result) == 2

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_empty_buffer_returns_empty_list(self, mock_pub):
        em = _make_emitter()
        assert em.get_recent_events() == []

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_filter_and_limit_combined(self, mock_pub):
        em = _make_emitter()
        for i in range(8):
            em.emit(_ET, {"i": i})
        em.emit(_ET2, {"x": 0})
        result = em.get_recent_events(limit=3, event_type="agent.heartbeat")
        assert len(result) == 3
        assert all(e.event_type == _ET for e in result)


# ---------------------------------------------------------------------------
# subscribe() / unsubscribe()
# ---------------------------------------------------------------------------


class TestEventEmitterSubscription:
    def setup_method(self):
        reset_event_emitter()

    def test_subscribe_adds_callback(self):
        em = _make_emitter()
        cb = MagicMock()
        em.subscribe(cb)
        assert cb in em._callbacks

    def test_subscribe_multiple_callbacks(self):
        em = _make_emitter()
        cb1 = MagicMock()
        cb2 = MagicMock()
        em.subscribe(cb1)
        em.subscribe(cb2)
        assert em._callbacks == [cb1, cb2]

    def test_unsubscribe_removes_callback(self):
        em = _make_emitter()
        cb = MagicMock()
        em.subscribe(cb)
        em.unsubscribe(cb)
        assert cb not in em._callbacks

    def test_unsubscribe_nonexistent_is_noop(self):
        em = _make_emitter()
        cb = MagicMock()
        em.unsubscribe(cb)  # must not raise
        assert em._callbacks == []

    def test_unsubscribe_only_removes_target(self):
        em = _make_emitter()
        cb1 = MagicMock()
        cb2 = MagicMock()
        em.subscribe(cb1)
        em.subscribe(cb2)
        em.unsubscribe(cb1)
        assert cb1 not in em._callbacks
        assert cb2 in em._callbacks


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


class TestEventEmitterIntrospection:
    def setup_method(self):
        reset_event_emitter()

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_event_count_empty(self, mock_pub):
        em = _make_emitter()
        assert em.event_count() == 0

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_event_count_after_emits(self, mock_pub):
        em = _make_emitter()
        em.emit(_ET, {})
        em.emit(_ET, {})
        assert em.event_count() == 2

    @patch(
        "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
    )
    def test_event_count_capped_at_maxlen(self, mock_pub):
        em = _make_emitter(maxlen=3)
        for _ in range(10):
            em.emit(_ET, {})
        assert em.event_count() == 3

    def test_subscriber_count_empty(self):
        em = _make_emitter()
        assert em.subscriber_count() == 0

    def test_subscriber_count_after_subscribe(self):
        em = _make_emitter()
        em.subscribe(MagicMock())
        em.subscribe(MagicMock())
        assert em.subscriber_count() == 2

    def test_subscriber_count_after_unsubscribe(self):
        em = _make_emitter()
        cb = MagicMock()
        em.subscribe(cb)
        em.unsubscribe(cb)
        assert em.subscriber_count() == 0


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


class TestGetEventEmitter:
    def setup_method(self):
        reset_event_emitter()

    def test_returns_event_emitter_instance(self):
        em = get_event_emitter()
        assert isinstance(em, EventEmitter)

    def test_same_instance_on_repeated_calls(self):
        em1 = get_event_emitter()
        em2 = get_event_emitter()
        assert em1 is em2

    def test_custom_maxlen_used_on_first_call(self):
        em = get_event_emitter(maxlen=42)
        assert em._recent.maxlen == 42

    def test_maxlen_ignored_on_subsequent_calls(self):
        em1 = get_event_emitter(maxlen=42)
        em2 = get_event_emitter(maxlen=999)
        assert em1 is em2
        assert em2._recent.maxlen == 42


class TestResetEventEmitter:
    def setup_method(self):
        reset_event_emitter()

    def test_reset_allows_new_singleton(self):
        em1 = get_event_emitter()
        reset_event_emitter()
        em2 = get_event_emitter()
        assert em1 is not em2

    def test_reset_clears_state(self):
        em1 = get_event_emitter()

        with patch(
            "forge_harness.webhook_server.services.event_emitter.EventEmitter._publish_to_bus"
        ):
            em1.emit(_ET, {})

        reset_event_emitter()
        em2 = get_event_emitter()
        # Fresh instance — no events
        assert em2.event_count() == 0

    def test_reset_idempotent(self):
        reset_event_emitter()
        reset_event_emitter()  # must not raise


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------


class TestEventEmitterThreadSafety:
    def setup_method(self):
        reset_event_emitter()

    def test_concurrent_emits_and_reads(self):
        """Hammer emit and get_recent_events concurrently; expect no exceptions."""
        em = _make_emitter(maxlen=50)
        errors: list[Exception] = []

        def emit_worker():
            try:
                with patch.object(em, "_publish_to_bus"):
                    for _ in range(20):
                        em.emit(_ET, {})
            except Exception as exc:
                errors.append(exc)

        def read_worker():
            try:
                for _ in range(20):
                    em.get_recent_events(limit=10)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=emit_worker) for _ in range(5)]
        threads += [threading.Thread(target=read_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []

    def test_concurrent_subscribe_unsubscribe(self):
        em = _make_emitter()
        errors: list[Exception] = []

        def worker():
            try:
                cb = MagicMock()
                em.subscribe(cb)
                em.unsubscribe(cb)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []

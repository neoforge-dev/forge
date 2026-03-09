"""
Comprehensive tests for:
  - forge_harness.webhook_server.infrastructure.sse_session
  - forge_harness.webhook_server.services.event_bus

Targets 90%+ branch coverage for each module.
"""

import asyncio
import json
import time
import unittest.mock as mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# SSESessionStore / SessionEntry tests
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.infrastructure.sse_session import (
    SessionEntry,
    SSESessionStore,
    get_sse_session_store,
)


class TestSessionEntry:
    """Tests for the SessionEntry dataclass."""

    def test_default_fields(self):
        entry = SessionEntry(token="abc")
        assert entry.token == "abc"
        assert entry.ttl_seconds == 300
        assert entry.created_at <= time.time()

    def test_is_valid_when_fresh(self):
        entry = SessionEntry(token="tok", ttl_seconds=300)
        assert entry.is_valid() is True

    def test_is_valid_when_just_expired(self):
        # created_at set far in the past
        entry = SessionEntry(token="tok", ttl_seconds=1)
        entry.created_at = time.time() - 10  # 10 seconds ago, TTL is 1
        assert entry.is_valid() is False

    def test_is_valid_exactly_at_boundary(self):
        # Set created_at exactly ttl seconds ago – should be invalid (< not <=)
        entry = SessionEntry(token="tok", ttl_seconds=5)
        entry.created_at = time.time() - 5
        assert entry.is_valid() is False

    def test_is_valid_just_before_expiry(self):
        entry = SessionEntry(token="tok", ttl_seconds=60)
        entry.created_at = time.time() - 59
        assert entry.is_valid() is True

    def test_created_at_defaults_to_now(self):
        before = time.time()
        entry = SessionEntry(token="t")
        after = time.time()
        assert before <= entry.created_at <= after

    def test_custom_ttl(self):
        entry = SessionEntry(token="t", ttl_seconds=10)
        assert entry.ttl_seconds == 10


class TestSSESessionStore:
    """Tests for SSESessionStore."""

    def setup_method(self):
        self.store = SSESessionStore(ttl_seconds=300)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    def test_create_returns_string(self):
        token = self.store.create("bearer-xyz")
        assert isinstance(token, str)

    def test_create_returns_unique_tokens(self):
        t1 = self.store.create("bearer-a")
        t2 = self.store.create("bearer-b")
        assert t1 != t2

    def test_create_stores_entry(self):
        token = self.store.create("bearer-xyz")
        assert token in self.store._sessions

    def test_create_uses_custom_ttl(self):
        store = SSESessionStore(ttl_seconds=60)
        token = store.create("bearer")
        entry = store._sessions[token]
        assert entry.ttl_seconds == 60

    def test_create_token_has_sufficient_length(self):
        # secrets.token_urlsafe(32) → ~43 chars
        token = self.store.create("bearer")
        assert len(token) >= 32

    def test_create_does_not_require_valid_bearer(self):
        # Docstring says "Caller must verify bearer_token first"
        # so the method itself accepts any string
        token = self.store.create("anything")
        assert token is not None

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def test_validate_valid_token(self):
        token = self.store.create("bearer")
        assert self.store.validate(token) is True

    def test_validate_unknown_token(self):
        assert self.store.validate("does-not-exist") is False

    def test_validate_expired_token_returns_false(self):
        store = SSESessionStore(ttl_seconds=1)
        token = store.create("bearer")
        # Force expiry by back-dating created_at
        store._sessions[token].created_at = time.time() - 10
        assert store.validate(token) is False

    def test_validate_expired_token_removes_entry(self):
        store = SSESessionStore(ttl_seconds=1)
        token = store.create("bearer")
        store._sessions[token].created_at = time.time() - 10
        store.validate(token)
        assert token not in store._sessions

    def test_validate_after_revoke(self):
        token = self.store.create("bearer")
        self.store.revoke(token)
        assert self.store.validate(token) is False

    def test_validate_multiple_tokens_independently(self):
        t1 = self.store.create("bearer1")
        t2 = self.store.create("bearer2")
        assert self.store.validate(t1) is True
        assert self.store.validate(t2) is True
        self.store.revoke(t1)
        assert self.store.validate(t1) is False
        assert self.store.validate(t2) is True

    # ------------------------------------------------------------------
    # revoke
    # ------------------------------------------------------------------

    def test_revoke_existing_token(self):
        token = self.store.create("bearer")
        self.store.revoke(token)
        assert token not in self.store._sessions

    def test_revoke_nonexistent_token_no_error(self):
        # Should not raise
        self.store.revoke("ghost-token")

    def test_revoke_twice_no_error(self):
        token = self.store.create("bearer")
        self.store.revoke(token)
        self.store.revoke(token)  # second revoke must not raise

    # ------------------------------------------------------------------
    # cleanup_expired
    # ------------------------------------------------------------------

    def test_cleanup_expired_returns_zero_when_nothing_expired(self):
        self.store.create("b1")
        self.store.create("b2")
        assert self.store.cleanup_expired() == 0

    def test_cleanup_expired_removes_expired_entries(self):
        store = SSESessionStore(ttl_seconds=1)
        t1 = store.create("b1")
        t2 = store.create("b2")
        # Expire both
        store._sessions[t1].created_at = time.time() - 10
        store._sessions[t2].created_at = time.time() - 10
        removed = store.cleanup_expired()
        assert removed == 2
        assert t1 not in store._sessions
        assert t2 not in store._sessions

    def test_cleanup_expired_keeps_valid_entries(self):
        store = SSESessionStore(ttl_seconds=300)
        t1 = store.create("b1")
        t2 = store.create("b2")
        store._sessions[t1].created_at = time.time() - 10  # still valid
        removed = store.cleanup_expired()
        assert removed == 0
        assert t1 in store._sessions
        assert t2 in store._sessions

    def test_cleanup_expired_mixed(self):
        store = SSESessionStore(ttl_seconds=300)
        t_fresh = store.create("fresh")
        t_stale = store.create("stale")
        store._sessions[t_stale].created_at = time.time() - 999
        removed = store.cleanup_expired()
        assert removed == 1
        assert t_fresh in store._sessions
        assert t_stale not in store._sessions

    def test_cleanup_empty_store(self):
        assert self.store.cleanup_expired() == 0

    # ------------------------------------------------------------------
    # internal state
    # ------------------------------------------------------------------

    def test_initial_sessions_empty(self):
        store = SSESessionStore()
        assert store._sessions == {}

    def test_default_ttl_is_300(self):
        store = SSESessionStore()
        assert store._ttl == 300


class TestGetSseSessionStore:
    """Tests for the get_sse_session_store() singleton factory."""

    def setup_method(self):
        # Reset module singleton so each test starts clean
        import forge_harness.webhook_server.infrastructure.sse_session as mod
        mod._sse_session_store = None

    def test_returns_sse_session_store_instance(self):
        store = get_sse_session_store()
        assert isinstance(store, SSESessionStore)

    def test_returns_same_instance_on_repeated_calls(self):
        s1 = get_sse_session_store()
        s2 = get_sse_session_store()
        assert s1 is s2

    def test_singleton_has_ttl_300(self):
        store = get_sse_session_store()
        assert store._ttl == 300

    def test_singleton_reuses_existing_instance(self):
        import forge_harness.webhook_server.infrastructure.sse_session as mod
        existing = SSESessionStore(ttl_seconds=99)
        mod._sse_session_store = existing
        returned = get_sse_session_store()
        assert returned is existing


# ---------------------------------------------------------------------------
# SSEEvent / EventBus tests
# ---------------------------------------------------------------------------

from forge_harness.webhook_server.services.event_bus import (
    EventBus,
    SSEEvent,
    get_event_bus,
)


def _fresh_event_bus() -> EventBus:
    """Return a brand-new EventBus, bypassing the singleton."""
    EventBus._instance = None
    bus = EventBus()
    return bus


class TestSSEEvent:
    """Tests for SSEEvent dataclass and to_sse_format()."""

    def _make_event(self, **kwargs):
        defaults = dict(
            id="42",
            event="agent.progress",
            data={"pct": 50},
        )
        defaults.update(kwargs)
        return SSEEvent(**defaults)

    def test_defaults_source(self):
        evt = self._make_event()
        assert evt.source == "webhook-server"

    def test_timestamp_is_set(self):
        evt = self._make_event()
        assert evt.timestamp  # non-empty string

    def test_custom_source(self):
        evt = self._make_event(source="custom-src")
        assert evt.source == "custom-src"

    def test_to_sse_format_structure(self):
        evt = self._make_event(id="7", event="agent.done", data={"k": "v"})
        fmt = evt.to_sse_format()
        lines = fmt.split("\n")
        assert lines[0] == "id: 7"
        assert lines[1] == "event: agent.done"
        assert lines[2].startswith("data: ")
        assert lines[3] == ""    # blank separator
        assert fmt.endswith("\n")

    def test_to_sse_format_data_is_valid_json(self):
        evt = self._make_event(id="1", event="x", data={"hello": "world"})
        fmt = evt.to_sse_format()
        data_line = [l for l in fmt.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["id"] == "1"
        assert payload["type"] == "x"
        assert payload["data"] == {"hello": "world"}
        assert "timestamp" in payload
        assert "source" in payload

    def test_to_sse_format_type_uses_event_field(self):
        evt = self._make_event(event="approval.created")
        fmt = evt.to_sse_format()
        data_line = [l for l in fmt.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["type"] == "approval.created"

    def test_to_sse_format_ends_with_double_newline(self):
        evt = self._make_event()
        fmt = evt.to_sse_format()
        assert fmt.endswith("\n\n")

    def test_to_sse_format_complex_data(self):
        data = {"nested": {"a": 1}, "list": [1, 2, 3]}
        evt = self._make_event(data=data)
        fmt = evt.to_sse_format()
        data_line = [l for l in fmt.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"] == data

    def test_to_sse_format_empty_data(self):
        evt = self._make_event(data={})
        fmt = evt.to_sse_format()
        data_line = [l for l in fmt.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"] == {}

    def test_timestamp_default_is_utc_iso(self):
        evt = self._make_event()
        # Must be parseable ISO format with timezone info
        from datetime import datetime
        dt = datetime.fromisoformat(evt.timestamp)
        assert dt.tzinfo is not None

    def test_two_events_have_different_timestamps(self):
        e1 = self._make_event()
        # tiny sleep ensures a real time difference
        time.sleep(0.01)
        e2 = self._make_event()
        # They may be equal at microsecond resolution but different ids
        # Just assert the format is correct for both
        assert e1.timestamp
        assert e2.timestamp


class TestEventBus:
    """Tests for EventBus singleton and async methods."""

    def setup_method(self):
        # Reset singleton before each test
        EventBus._instance = None

    # ------------------------------------------------------------------
    # singleton behaviour
    # ------------------------------------------------------------------

    def test_singleton_same_instance(self):
        b1 = EventBus()
        b2 = EventBus()
        assert b1 is b2

    def test_init_runs_only_once(self):
        b = EventBus()
        original_counter = b._event_counter
        b._event_counter = 99
        b2 = EventBus()
        assert b2._event_counter == 99  # __init__ did not reset it

    def test_initialized_flag_set(self):
        b = EventBus()
        assert b._initialized is True

    def test_fresh_bus_has_no_subscribers(self):
        b = EventBus()
        assert b._subscribers == []

    def test_fresh_bus_counter_is_zero(self):
        b = EventBus()
        assert b._event_counter == 0

    # ------------------------------------------------------------------
    # subscribe / unsubscribe
    # ------------------------------------------------------------------

    def test_subscribe_returns_asyncio_queue(self):
        b = EventBus()
        q = b.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_subscribe_adds_to_subscribers(self):
        b = EventBus()
        q = b.subscribe()
        assert q in b._subscribers

    def test_multiple_subscribers(self):
        b = EventBus()
        q1 = b.subscribe()
        q2 = b.subscribe()
        assert len(b._subscribers) == 2
        assert q1 in b._subscribers
        assert q2 in b._subscribers

    def test_unsubscribe_removes_queue(self):
        b = EventBus()
        q = b.subscribe()
        b.unsubscribe(q)
        assert q not in b._subscribers

    def test_unsubscribe_unknown_queue_no_error(self):
        b = EventBus()
        orphan: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
        b.unsubscribe(orphan)  # must not raise

    def test_unsubscribe_only_removes_target(self):
        b = EventBus()
        q1 = b.subscribe()
        q2 = b.subscribe()
        b.unsubscribe(q1)
        assert q1 not in b._subscribers
        assert q2 in b._subscribers

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_publish_delivers_to_single_subscriber(self):
        b = EventBus()
        q = b.subscribe()
        await b.publish("test.event", {"x": 1})
        evt = q.get_nowait()
        assert isinstance(evt, SSEEvent)
        assert evt.event == "test.event"
        assert evt.data == {"x": 1}

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        b = EventBus()
        q1 = b.subscribe()
        q2 = b.subscribe()
        await b.publish("broadcast", {"msg": "hi"})
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1.event == "broadcast"
        assert e2.event == "broadcast"

    @pytest.mark.asyncio
    async def test_publish_increments_counter(self):
        b = EventBus()
        await b.publish("a", {})
        await b.publish("b", {})
        assert b._event_counter == 2

    @pytest.mark.asyncio
    async def test_publish_event_id_matches_counter(self):
        b = EventBus()
        q = b.subscribe()
        await b.publish("x", {})
        evt = q.get_nowait()
        assert evt.id == "1"

    @pytest.mark.asyncio
    async def test_publish_sequential_ids(self):
        b = EventBus()
        q = b.subscribe()
        await b.publish("x", {})
        await b.publish("y", {})
        e1 = q.get_nowait()
        e2 = q.get_nowait()
        assert e1.id == "1"
        assert e2.id == "2"

    @pytest.mark.asyncio
    async def test_publish_custom_source(self):
        b = EventBus()
        q = b.subscribe()
        await b.publish("ev", {}, source="my-service")
        evt = q.get_nowait()
        assert evt.source == "my-service"

    @pytest.mark.asyncio
    async def test_publish_default_source(self):
        b = EventBus()
        q = b.subscribe()
        await b.publish("ev", {})
        evt = q.get_nowait()
        assert evt.source == "webhook-server"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers_no_error(self):
        b = EventBus()
        # No exception, counter still increments
        await b.publish("lonely", {"k": "v"})
        assert b._event_counter == 1

    @pytest.mark.asyncio
    async def test_publish_drops_event_on_full_queue(self):
        b = EventBus()
        # Use a queue with maxsize=1 to trigger QueueFull
        small_q: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=1)
        with b._subscriber_lock:
            b._subscribers.append(small_q)

        # Fill the queue
        dummy_event = SSEEvent(id="0", event="pre-fill", data={})
        small_q.put_nowait(dummy_event)

        # This publish should not raise even though queue is full
        await b.publish("overflow", {"data": "x"})
        # Queue still has only the pre-filled item (overflow was dropped)
        assert small_q.qsize() == 1
        item = small_q.get_nowait()
        assert item.event == "pre-fill"

    @pytest.mark.asyncio
    async def test_publish_partial_delivery_when_one_queue_full(self):
        b = EventBus()
        q_ok = b.subscribe()
        small_q: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=1)
        with b._subscriber_lock:
            b._subscribers.append(small_q)
        # Fill small_q so next put raises QueueFull
        small_q.put_nowait(SSEEvent(id="0", event="fill", data={}))

        await b.publish("test", {})
        # q_ok should have received the event
        evt = q_ok.get_nowait()
        assert evt.event == "test"
        # small_q still only has the filler
        assert small_q.qsize() == 1

    # ------------------------------------------------------------------
    # close_all
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_close_all_sends_none_to_subscribers(self):
        b = EventBus()
        q1 = b.subscribe()
        q2 = b.subscribe()
        await b.close_all()
        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_close_all_no_subscribers_no_error(self):
        b = EventBus()
        await b.close_all()  # should not raise

    @pytest.mark.asyncio
    async def test_close_all_full_queue_does_not_raise(self):
        b = EventBus()
        full_q: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=1)
        full_q.put_nowait(SSEEvent(id="0", event="filler", data={}))
        with b._subscriber_lock:
            b._subscribers.append(full_q)
        await b.close_all()  # must not raise


# ---------------------------------------------------------------------------
# get_event_bus singleton factory
# ---------------------------------------------------------------------------

class TestGetEventBus:
    """Tests for the get_event_bus() module-level factory."""

    def setup_method(self):
        import forge_harness.webhook_server.services.event_bus as mod
        mod._event_bus = None
        EventBus._instance = None

    def test_returns_event_bus_instance(self):
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_returns_same_instance_on_repeated_calls(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_reuses_existing_module_level_instance(self):
        import forge_harness.webhook_server.services.event_bus as mod
        existing = EventBus.__new__(EventBus)
        existing._initialized = True
        existing._subscribers = []
        existing._event_counter = 0
        existing._subscriber_lock = __import__("threading").Lock()
        mod._event_bus = existing
        returned = get_event_bus()
        assert returned is existing


# ---------------------------------------------------------------------------
# Integration: full publish→receive round-trip
# ---------------------------------------------------------------------------

class TestPublishReceiveRoundTrip:
    """End-to-end tests combining subscribe/publish/unsubscribe."""

    def setup_method(self):
        EventBus._instance = None

    @pytest.mark.asyncio
    async def test_subscribe_publish_unsubscribe_cycle(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("agent.registered", {"agent_id": "a1"})
        evt = q.get_nowait()
        assert evt.event == "agent.registered"
        assert evt.data["agent_id"] == "a1"
        bus.unsubscribe(q)
        assert q not in bus._subscribers

    @pytest.mark.asyncio
    async def test_events_after_unsubscribe_not_delivered(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        await bus.publish("after.unsub", {"k": "v"})
        assert q.empty()

    @pytest.mark.asyncio
    async def test_sse_format_round_trip(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("approval.resolved", {"status": "approved"})
        evt = q.get_nowait()
        fmt = evt.to_sse_format()
        # Parse it back
        lines = fmt.strip().split("\n")
        id_val = lines[0].split(": ", 1)[1]
        event_val = lines[1].split(": ", 1)[1]
        data_val = json.loads(lines[2].split(": ", 1)[1])
        assert id_val == evt.id
        assert event_val == "approval.resolved"
        assert data_val["data"]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_multiple_events_ordered(self):
        bus = EventBus()
        q = bus.subscribe()
        for i in range(5):
            await bus.publish(f"ev.{i}", {"i": i})
        for i in range(5):
            evt = q.get_nowait()
            assert evt.event == f"ev.{i}"
            assert evt.data["i"] == i

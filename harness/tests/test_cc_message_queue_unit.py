"""
Unit tests for forge_harness.webhook_server.services.message_queue

Covers:
- MessageStatus enum
- QueuedMessage dataclass (to_dict, is_expired)
- MessageQueue.enqueue
- MessageQueue.get_pending (with and without mark_delivered)
- MessageQueue.acknowledge (success, not found, wrong agent, wrong status)
- MessageQueue.get_status
- MessageQueue.get_conversation
- MessageQueue.get_stats
- MessageQueue._cleanup_expired
- get_message_queue singleton factory
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.services.message_queue import (
    MessageQueue,
    MessageStatus,
    QueuedMessage,
    get_message_queue,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_queue(default_ttl: int = 3600) -> MessageQueue:
    """Return a fresh MessageQueue instance."""
    return MessageQueue(default_ttl_seconds=default_ttl)


def _enqueue_simple(
    queue: MessageQueue,
    sender: str = "agent:alpha",
    recipient: str = "agent:beta",
    content: str = "hello",
    **kwargs,
) -> QueuedMessage:
    return queue.enqueue(sender, recipient, content, **kwargs)


# ---------------------------------------------------------------------------
# MessageStatus enum
# ---------------------------------------------------------------------------


class TestMessageStatus:
    """Tests for the MessageStatus enum."""

    def test_all_values_present(self):
        values = {s.value for s in MessageStatus}
        assert values == {"pending", "delivered", "acknowledged", "failed", "expired"}

    def test_pending_is_default_status(self):
        msg = QueuedMessage(
            id="test-id",
            sender_id="a",
            recipient_id="b",
            type="instruction",
            content="hi",
        )
        assert msg.status == MessageStatus.PENDING


# ---------------------------------------------------------------------------
# QueuedMessage dataclass
# ---------------------------------------------------------------------------


class TestQueuedMessage:
    """Tests for QueuedMessage dataclass and its methods."""

    def _make_msg(self, **overrides) -> QueuedMessage:
        defaults = dict(
            id="msg-001",
            sender_id="forge:alpha",
            recipient_id="forge:beta",
            type="notification",
            content="payload",
        )
        defaults.update(overrides)
        return QueuedMessage(**defaults)

    # ---- to_dict ----

    def test_to_dict_basic_fields(self):
        msg = self._make_msg()
        d = msg.to_dict()

        assert d["id"] == "msg-001"
        assert d["sender_id"] == "forge:alpha"
        assert d["recipient_id"] == "forge:beta"
        assert d["type"] == "notification"
        assert d["content"] == "payload"
        assert d["priority"] == 1
        assert d["status"] == "pending"
        assert d["retry_count"] == 0

    def test_to_dict_none_timestamps(self):
        msg = self._make_msg()
        d = msg.to_dict()

        assert d["delivered_at"] is None
        assert d["acknowledged_at"] is None

    def test_to_dict_optional_timestamps_populated(self):
        now = datetime.now(UTC)
        msg = self._make_msg(
            delivered_at=now,
            acknowledged_at=now,
            expires_at=now,
        )
        d = msg.to_dict()

        assert d["delivered_at"] == now.isoformat()
        assert d["acknowledged_at"] == now.isoformat()
        assert d["expires_at"] == now.isoformat()

    def test_to_dict_metadata_is_included(self):
        msg = self._make_msg(metadata={"key": "value", "count": 3})
        d = msg.to_dict()
        assert d["metadata"] == {"key": "value", "count": 3}

    def test_to_dict_status_enum_serialised_as_string(self):
        msg = self._make_msg(status=MessageStatus.DELIVERED)
        d = msg.to_dict()
        assert d["status"] == "delivered"

    # ---- is_expired ----

    def test_is_expired_no_expiry(self):
        msg = self._make_msg(expires_at=None)
        assert msg.is_expired() is False

    def test_is_expired_future_expiry(self):
        msg = self._make_msg(expires_at=datetime.now(UTC) + timedelta(hours=1))
        assert msg.is_expired() is False

    def test_is_expired_past_expiry(self):
        msg = self._make_msg(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert msg.is_expired() is True


# ---------------------------------------------------------------------------
# MessageQueue.enqueue
# ---------------------------------------------------------------------------


class TestMessageQueueEnqueue:
    """Tests for MessageQueue.enqueue()."""

    def test_enqueue_returns_queued_message(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert isinstance(msg, QueuedMessage)

    def test_enqueue_assigns_unique_ids(self):
        q = _make_queue()
        ids = {_enqueue_simple(q).id for _ in range(10)}
        assert len(ids) == 10

    def test_enqueue_default_type_is_instruction(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert msg.type == "instruction"

    def test_enqueue_custom_type(self):
        q = _make_queue()
        msg = _enqueue_simple(q, msg_type="handoff")
        assert msg.type == "handoff"

    def test_enqueue_default_priority_is_1(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert msg.priority == 1

    def test_enqueue_custom_priority(self):
        q = _make_queue()
        msg = _enqueue_simple(q, priority=3)
        assert msg.priority == 3

    def test_enqueue_sets_expiry_using_default_ttl(self):
        q = _make_queue(default_ttl=60)
        before = datetime.now(UTC)
        msg = _enqueue_simple(q)
        after = datetime.now(UTC)

        assert msg.expires_at is not None
        expected_min = before + timedelta(seconds=59)
        expected_max = after + timedelta(seconds=61)
        assert expected_min <= msg.expires_at <= expected_max

    def test_enqueue_uses_explicit_ttl_over_default(self):
        q = _make_queue(default_ttl=3600)
        before = datetime.now(UTC)
        msg = _enqueue_simple(q, ttl_seconds=10)
        after = datetime.now(UTC)

        expected_min = before + timedelta(seconds=9)
        expected_max = after + timedelta(seconds=11)
        assert expected_min <= msg.expires_at <= expected_max

    def test_enqueue_metadata_passed_through(self):
        q = _make_queue()
        msg = _enqueue_simple(q, metadata={"task": "T-42"})
        assert msg.metadata == {"task": "T-42"}

    def test_enqueue_metadata_defaults_to_empty_dict(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert msg.metadata == {}

    def test_enqueue_status_is_pending(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert msg.status == MessageStatus.PENDING

    def test_enqueue_message_stored_in_queue(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert q.get_status(msg.id) is msg

    def test_enqueue_creates_inbox_for_recipient(self):
        q = _make_queue()
        _enqueue_simple(q, recipient="agent:new")
        pending = q.get_pending("agent:new", mark_delivered=False)
        assert len(pending) == 1


# ---------------------------------------------------------------------------
# MessageQueue.get_pending
# ---------------------------------------------------------------------------


class TestMessageQueueGetPending:
    """Tests for MessageQueue.get_pending()."""

    def test_get_pending_returns_only_pending_messages(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:x")
        # Manually mark as acknowledged to exclude it
        q.acknowledge(msg.id, "agent:x")
        extra = _enqueue_simple(q, recipient="agent:x")

        pending = q.get_pending("agent:x", mark_delivered=False)
        assert len(pending) == 1
        assert pending[0].id == extra.id

    def test_get_pending_marks_delivered_by_default(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:y")
        q.get_pending("agent:y")
        assert msg.status == MessageStatus.DELIVERED

    def test_get_pending_no_mark_delivered_leaves_status_pending(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:y")
        q.get_pending("agent:y", mark_delivered=False)
        assert msg.status == MessageStatus.PENDING

    def test_get_pending_sorted_by_priority_descending(self):
        q = _make_queue()
        _enqueue_simple(q, recipient="agent:z", priority=1)
        _enqueue_simple(q, recipient="agent:z", priority=3)
        _enqueue_simple(q, recipient="agent:z", priority=2)

        msgs = q.get_pending("agent:z", mark_delivered=False)
        priorities = [m.priority for m in msgs]
        assert priorities == sorted(priorities, reverse=True)

    def test_get_pending_empty_for_unknown_agent(self):
        q = _make_queue()
        result = q.get_pending("nobody", mark_delivered=False)
        assert result == []

    def test_get_pending_triggers_expiry_cleanup(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:e", ttl_seconds=0)
        # Let the TTL expire
        time.sleep(0.01)
        pending = q.get_pending("agent:e", mark_delivered=False)
        assert len(pending) == 0
        assert msg.status == MessageStatus.EXPIRED


# ---------------------------------------------------------------------------
# MessageQueue.acknowledge
# ---------------------------------------------------------------------------


class TestMessageQueueAcknowledge:
    """Tests for MessageQueue.acknowledge()."""

    def test_acknowledge_success_from_pending(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:r")
        result = q.acknowledge(msg.id, "agent:r")

        assert result is msg
        assert msg.status == MessageStatus.ACKNOWLEDGED
        assert msg.acknowledged_at is not None

    def test_acknowledge_success_from_delivered(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:r")
        q.get_pending("agent:r")  # marks delivered
        result = q.acknowledge(msg.id, "agent:r")

        assert result is msg
        assert msg.status == MessageStatus.ACKNOWLEDGED

    def test_acknowledge_returns_none_for_unknown_message(self):
        q = _make_queue()
        result = q.acknowledge("nonexistent-id", "some-agent")
        assert result is None

    def test_acknowledge_returns_none_wrong_agent(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:correct")
        result = q.acknowledge(msg.id, "agent:wrong")
        assert result is None
        assert msg.status == MessageStatus.PENDING  # unchanged

    def test_acknowledge_returns_none_when_already_acknowledged(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:r")
        q.acknowledge(msg.id, "agent:r")
        result = q.acknowledge(msg.id, "agent:r")
        assert result is None

    def test_acknowledge_sets_timestamp(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="agent:r")
        before = datetime.now(UTC)
        q.acknowledge(msg.id, "agent:r")
        after = datetime.now(UTC)

        assert before <= msg.acknowledged_at <= after


# ---------------------------------------------------------------------------
# MessageQueue.get_status
# ---------------------------------------------------------------------------


class TestMessageQueueGetStatus:
    """Tests for MessageQueue.get_status()."""

    def test_get_status_returns_message(self):
        q = _make_queue()
        msg = _enqueue_simple(q)
        assert q.get_status(msg.id) is msg

    def test_get_status_returns_none_for_unknown_id(self):
        q = _make_queue()
        assert q.get_status("does-not-exist") is None


# ---------------------------------------------------------------------------
# MessageQueue.get_conversation
# ---------------------------------------------------------------------------


class TestMessageQueueGetConversation:
    """Tests for MessageQueue.get_conversation()."""

    def test_get_conversation_returns_messages_both_directions(self):
        q = _make_queue()
        m1 = _enqueue_simple(q, sender="a1", recipient="a2")
        m2 = _enqueue_simple(q, sender="a2", recipient="a1")

        conv = q.get_conversation("a1", "a2")
        ids = {m.id for m in conv}
        assert m1.id in ids
        assert m2.id in ids

    def test_get_conversation_excludes_unrelated_messages(self):
        q = _make_queue()
        _enqueue_simple(q, sender="a1", recipient="a2")
        _enqueue_simple(q, sender="a3", recipient="a4")

        conv = q.get_conversation("a1", "a2")
        assert all(
            (m.sender_id in ("a1", "a2") and m.recipient_id in ("a1", "a2"))
            for m in conv
        )

    def test_get_conversation_sorted_newest_first(self):
        q = _make_queue()
        _enqueue_simple(q, sender="a1", recipient="a2", content="first")
        _enqueue_simple(q, sender="a1", recipient="a2", content="second")

        conv = q.get_conversation("a1", "a2")
        assert conv[0].created_at >= conv[1].created_at

    def test_get_conversation_respects_limit(self):
        q = _make_queue()
        for i in range(10):
            _enqueue_simple(q, sender="a1", recipient="a2", content=str(i))

        conv = q.get_conversation("a1", "a2", limit=3)
        assert len(conv) == 3

    def test_get_conversation_empty_for_no_messages(self):
        q = _make_queue()
        assert q.get_conversation("nobody", "else") == []


# ---------------------------------------------------------------------------
# MessageQueue.get_stats
# ---------------------------------------------------------------------------


class TestMessageQueueGetStats:
    """Tests for MessageQueue.get_stats()."""

    def test_stats_empty_queue(self):
        q = _make_queue()
        stats = q.get_stats()

        assert stats["total_messages"] == 0
        assert stats["total_inboxes"] == 0
        assert stats["by_status"] == {}

    def test_stats_counts_messages(self):
        q = _make_queue()
        _enqueue_simple(q, recipient="a1")
        _enqueue_simple(q, recipient="a1")
        _enqueue_simple(q, recipient="a2")

        stats = q.get_stats()
        assert stats["total_messages"] == 3
        assert stats["total_inboxes"] == 2
        assert stats["by_status"]["pending"] == 3

    def test_stats_reflects_status_changes(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="a1")
        q.acknowledge(msg.id, "a1")

        stats = q.get_stats()
        assert stats["by_status"].get("acknowledged") == 1
        assert stats["by_status"].get("pending", 0) == 0


# ---------------------------------------------------------------------------
# MessageQueue._cleanup_expired (internal, tested indirectly & directly)
# ---------------------------------------------------------------------------


class TestMessageQueueCleanupExpired:
    """Tests for internal expiry cleanup logic."""

    def test_cleanup_marks_expired_messages(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="a1", ttl_seconds=0)
        time.sleep(0.01)
        # Trigger via get_pending which calls _cleanup_expired internally
        q.get_pending("a1", mark_delivered=False)
        assert msg.status == MessageStatus.EXPIRED

    def test_cleanup_does_not_expire_non_pending_messages(self):
        q = _make_queue()
        msg = _enqueue_simple(q, recipient="a1", ttl_seconds=0)
        # Manually deliver before cleanup
        msg.status = MessageStatus.DELIVERED
        time.sleep(0.01)
        q.get_pending("a1", mark_delivered=False)
        # Delivered messages should NOT be touched by cleanup
        assert msg.status == MessageStatus.DELIVERED


# ---------------------------------------------------------------------------
# get_message_queue singleton factory
# ---------------------------------------------------------------------------


class TestGetMessageQueueSingleton:
    """Tests for the global message queue singleton factory."""

    def test_get_message_queue_returns_instance(self):
        import forge_harness.webhook_server.services.message_queue as mq_mod

        original = mq_mod._message_queue
        try:
            mq_mod._message_queue = None
            instance = get_message_queue()
            assert isinstance(instance, MessageQueue)
        finally:
            mq_mod._message_queue = original

    def test_get_message_queue_returns_same_instance(self):
        import forge_harness.webhook_server.services.message_queue as mq_mod

        original = mq_mod._message_queue
        try:
            mq_mod._message_queue = None
            a = get_message_queue()
            b = get_message_queue()
            assert a is b
        finally:
            mq_mod._message_queue = original

    def test_get_message_queue_reuses_existing_instance(self):
        import forge_harness.webhook_server.services.message_queue as mq_mod

        sentinel = MagicMock(spec=MessageQueue)
        original = mq_mod._message_queue
        try:
            mq_mod._message_queue = sentinel
            result = get_message_queue()
            assert result is sentinel
        finally:
            mq_mod._message_queue = original

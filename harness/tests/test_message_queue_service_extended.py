"""Extended tests for MessageQueue service.

Covers QueuedMessage edge cases, priority ordering details, expiry behaviour,
thread-safety patterns, conversation windowing, and stats consistency.
"""

import threading
import time
from datetime import UTC, datetime, timedelta

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


def _make_queue(ttl: int = 3600) -> MessageQueue:
    return MessageQueue(default_ttl_seconds=ttl)


# ---------------------------------------------------------------------------
# QueuedMessage – extra field/method coverage
# ---------------------------------------------------------------------------


class TestQueuedMessageExtended:
    def test_default_metadata_is_empty_dict(self):
        msg = QueuedMessage(id="m", sender_id="a", recipient_id="b", type="t", content="c")
        assert msg.metadata == {}

    def test_default_priority_is_one(self):
        msg = QueuedMessage(id="m", sender_id="a", recipient_id="b", type="t", content="c")
        assert msg.priority == 1

    def test_default_retry_count_is_zero(self):
        msg = QueuedMessage(id="m", sender_id="a", recipient_id="b", type="t", content="c")
        assert msg.retry_count == 0

    def test_default_status_is_pending(self):
        msg = QueuedMessage(id="m", sender_id="a", recipient_id="b", type="t", content="c")
        assert msg.status == MessageStatus.PENDING

    def test_to_dict_status_is_string(self):
        msg = QueuedMessage(id="m", sender_id="a", recipient_id="b", type="t", content="c")
        result = msg.to_dict()
        assert isinstance(result["status"], str)
        assert result["status"] == "pending"

    def test_to_dict_expires_at_none_when_not_set(self):
        msg = QueuedMessage(
            id="m", sender_id="a", recipient_id="b", type="t", content="c", expires_at=None
        )
        assert msg.to_dict()["expires_at"] is None

    def test_to_dict_expires_at_iso_string_when_set(self):
        expiry = datetime.now(UTC) + timedelta(hours=2)
        msg = QueuedMessage(
            id="m", sender_id="a", recipient_id="b", type="t", content="c", expires_at=expiry
        )
        result = msg.to_dict()
        assert isinstance(result["expires_at"], str)
        assert result["expires_at"] == expiry.isoformat()

    def test_to_dict_metadata_round_trips(self):
        meta = {"task_id": "xyz", "tags": ["a", "b"]}
        msg = QueuedMessage(
            id="m", sender_id="a", recipient_id="b", type="t", content="c", metadata=meta
        )
        assert msg.to_dict()["metadata"] == meta

    def test_to_dict_retry_count_included(self):
        msg = QueuedMessage(
            id="m", sender_id="a", recipient_id="b", type="t", content="c", retry_count=5
        )
        assert msg.to_dict()["retry_count"] == 5

    def test_is_expired_exactly_at_boundary(self):
        """A message that expires right now should be expired."""
        past = datetime.now(UTC) - timedelta(microseconds=1)
        msg = QueuedMessage(
            id="m", sender_id="a", recipient_id="b", type="t", content="c", expires_at=past
        )
        assert msg.is_expired() is True

    def test_is_expired_far_future(self):
        far_future = datetime.now(UTC) + timedelta(days=365)
        msg = QueuedMessage(
            id="m", sender_id="a", recipient_id="b", type="t", content="c", expires_at=far_future
        )
        assert msg.is_expired() is False


# ---------------------------------------------------------------------------
# MessageQueue – enqueue edge cases
# ---------------------------------------------------------------------------


class TestMessageQueueEnqueue:
    def test_enqueue_uses_default_ttl_when_none(self):
        q = _make_queue(ttl=120)
        msg = q.enqueue("a", "b", "hi", ttl_seconds=None)
        # expires_at should be ~120s from now
        delta = (msg.expires_at - datetime.now(UTC)).total_seconds()
        assert 110 <= delta <= 130

    def test_enqueue_custom_ttl_overrides_default(self):
        q = _make_queue(ttl=3600)
        msg = q.enqueue("a", "b", "hi", ttl_seconds=60)
        delta = (msg.expires_at - datetime.now(UTC)).total_seconds()
        assert 50 <= delta <= 70

    def test_enqueue_zero_ttl_immediately_expired(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "hi", ttl_seconds=0)
        # expires_at is at or before now
        assert msg.expires_at <= datetime.now(UTC)

    def test_enqueue_generates_unique_ids(self):
        q = _make_queue()
        ids = {q.enqueue("a", "b", "x").id for _ in range(20)}
        assert len(ids) == 20

    def test_enqueue_message_in_global_messages_dict(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        assert msg.id in q._messages

    def test_enqueue_creates_inbox_for_new_recipient(self):
        q = _make_queue()
        q.enqueue("a", "new-agent", "hello")
        assert "new-agent" in q._agent_inbox

    def test_enqueue_appends_to_existing_inbox(self):
        q = _make_queue()
        q.enqueue("a", "b", "first")
        q.enqueue("a", "b", "second")
        assert len(q._agent_inbox["b"]) == 2

    def test_enqueue_empty_metadata_default(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        assert msg.metadata == {}

    def test_enqueue_metadata_passed_through(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test", metadata={"ref": "123"})
        assert msg.metadata == {"ref": "123"}

    def test_enqueue_default_msg_type_is_instruction(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        assert msg.type == "instruction"


# ---------------------------------------------------------------------------
# MessageQueue – get_pending edge cases
# ---------------------------------------------------------------------------


class TestMessageQueueGetPending:
    def test_get_pending_only_returns_pending_status(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "hi")
        # Manually force delivered status
        msg.status = MessageStatus.DELIVERED
        pending = q.get_pending("b")
        assert pending == []

    def test_get_pending_excludes_expired_messages(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "hi")
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        # Trigger cleanup
        pending = q.get_pending("b")
        assert pending == []
        assert msg.status == MessageStatus.EXPIRED

    def test_get_pending_same_priority_sorted_by_creation_time(self):
        q = _make_queue()
        m1 = q.enqueue("a", "b", "first", priority=1)
        m2 = q.enqueue("a", "b", "second", priority=1)
        pending = q.get_pending("b", mark_delivered=False)
        assert pending[0].id == m1.id
        assert pending[1].id == m2.id

    def test_get_pending_highest_priority_first(self):
        q = _make_queue()
        q.enqueue("a", "b", "low", priority=0)
        q.enqueue("a", "b", "high", priority=3)
        pending = q.get_pending("b", mark_delivered=False)
        assert pending[0].priority == 3
        assert pending[-1].priority == 0

    def test_get_pending_marks_delivered_at_timestamp(self):
        q = _make_queue()
        before = datetime.now(UTC)
        q.enqueue("a", "b", "test")
        pending = q.get_pending("b")
        assert pending[0].delivered_at >= before

    def test_get_pending_idempotent_when_mark_delivered_false(self):
        q = _make_queue()
        q.enqueue("a", "b", "hi")
        first = q.get_pending("b", mark_delivered=False)
        second = q.get_pending("b", mark_delivered=False)
        assert len(first) == 1
        assert len(second) == 1

    def test_get_pending_unknown_agent_returns_empty(self):
        q = _make_queue()
        assert q.get_pending("ghost-agent") == []


# ---------------------------------------------------------------------------
# MessageQueue – acknowledge edge cases
# ---------------------------------------------------------------------------


class TestMessageQueueAcknowledge:
    def test_acknowledge_sets_acknowledged_at(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        before = datetime.now(UTC)
        q.acknowledge(msg.id, "b")
        assert msg.acknowledged_at >= before

    def test_acknowledge_delivered_message_succeeds(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        q.get_pending("b")  # now DELIVERED
        result = q.acknowledge(msg.id, "b")
        assert result is not None
        assert result.status == MessageStatus.ACKNOWLEDGED

    def test_acknowledge_wrong_agent_leaves_status_unchanged(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        q.acknowledge(msg.id, "not-b")
        assert msg.status == MessageStatus.PENDING

    def test_acknowledge_expired_message_returns_none(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        msg.status = MessageStatus.EXPIRED
        result = q.acknowledge(msg.id, "b")
        assert result is None

    def test_acknowledge_failed_message_returns_none(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        msg.status = MessageStatus.FAILED
        result = q.acknowledge(msg.id, "b")
        assert result is None

    def test_acknowledge_returns_same_message_object(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        result = q.acknowledge(msg.id, "b")
        assert result is msg


# ---------------------------------------------------------------------------
# MessageQueue – get_conversation
# ---------------------------------------------------------------------------


class TestMessageQueueConversation:
    def test_conversation_bidirectional(self):
        q = _make_queue()
        q.enqueue("a", "b", "hi")
        q.enqueue("b", "a", "hello back")
        conv = q.get_conversation("a", "b")
        assert len(conv) == 2

    def test_conversation_excludes_unrelated_messages(self):
        q = _make_queue()
        q.enqueue("a", "b", "relevant")
        q.enqueue("x", "y", "irrelevant")
        conv = q.get_conversation("a", "b")
        assert len(conv) == 1
        assert conv[0].content == "relevant"

    def test_conversation_newest_first(self):
        q = _make_queue()
        q.enqueue("a", "b", "older")
        time.sleep(0.01)
        q.enqueue("a", "b", "newer")
        conv = q.get_conversation("a", "b")
        assert conv[0].content == "newer"

    def test_conversation_limit_enforced(self):
        q = _make_queue()
        for i in range(15):
            q.enqueue("a", "b", f"msg{i}")
        conv = q.get_conversation("a", "b", limit=7)
        assert len(conv) == 7

    def test_conversation_both_directions_included(self):
        q = _make_queue()
        q.enqueue("a", "b", "from a")
        q.enqueue("b", "a", "from b")
        conv_ab = q.get_conversation("a", "b")
        conv_ba = q.get_conversation("b", "a")
        # Both views should see both messages
        assert len(conv_ab) == 2
        assert len(conv_ba) == 2


# ---------------------------------------------------------------------------
# MessageQueue – get_stats
# ---------------------------------------------------------------------------


class TestMessageQueueStats:
    def test_stats_counts_all_statuses(self):
        q = _make_queue()
        m1 = q.enqueue("a", "b", "p1")  # pending
        m2 = q.enqueue("a", "c", "d1")
        q.get_pending("c")  # delivered
        m3 = q.enqueue("a", "d", "a1")
        q.acknowledge(m3.id, "d")  # acknowledged

        stats = q.get_stats()
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["delivered"] == 1
        assert stats["by_status"]["acknowledged"] == 1

    def test_stats_total_messages(self):
        q = _make_queue()
        for _ in range(7):
            q.enqueue("a", "b", "x")
        assert q.get_stats()["total_messages"] == 7

    def test_stats_total_inboxes(self):
        q = _make_queue()
        q.enqueue("a", "b", "x")
        q.enqueue("a", "c", "y")
        q.enqueue("a", "d", "z")
        assert q.get_stats()["total_inboxes"] == 3

    def test_stats_empty_by_status_when_no_messages(self):
        q = _make_queue()
        assert q.get_stats()["by_status"] == {}


# ---------------------------------------------------------------------------
# MessageQueue – cleanup_expired internal behaviour
# ---------------------------------------------------------------------------


class TestMessageQueueCleanup:
    def test_cleanup_does_not_expire_delivered_messages(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        msg.status = MessageStatus.DELIVERED
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        q.get_pending("b")  # triggers cleanup but message is already DELIVERED
        # Only PENDING messages get expired by cleanup
        assert msg.status == MessageStatus.DELIVERED

    def test_cleanup_returns_count_of_expired(self):
        q = _make_queue()
        msg = q.enqueue("a", "b", "test")
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        with q._lock:
            count = q._cleanup_expired()
        assert count == 1

    def test_cleanup_no_op_when_all_valid(self):
        q = _make_queue()
        q.enqueue("a", "b", "still valid")
        with q._lock:
            count = q._cleanup_expired()
        assert count == 0


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------


class TestMessageQueueThreadSafety:
    def test_concurrent_enqueue_no_id_collision(self):
        q = _make_queue()
        ids = []
        lock = threading.Lock()

        def enqueue_many():
            for _ in range(50):
                msg = q.enqueue("sender", "recipient", "data")
                with lock:
                    ids.append(msg.id)

        threads = [threading.Thread(target=enqueue_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 200
        assert len(set(ids)) == 200  # all unique


# ---------------------------------------------------------------------------
# get_message_queue factory
# ---------------------------------------------------------------------------


class TestGetMessageQueueExtended:
    def test_returns_message_queue_instance(self):
        import forge_harness.webhook_server.services.message_queue as mq_module

        mq_module._message_queue = None
        result = get_message_queue()
        assert isinstance(result, MessageQueue)
        mq_module._message_queue = None

    def test_successive_calls_return_same_object(self):
        import forge_harness.webhook_server.services.message_queue as mq_module

        mq_module._message_queue = None
        a = get_message_queue()
        b = get_message_queue()
        assert a is b
        mq_module._message_queue = None

    def test_existing_instance_not_replaced(self):
        import forge_harness.webhook_server.services.message_queue as mq_module

        custom = MessageQueue(default_ttl_seconds=999)
        mq_module._message_queue = custom
        result = get_message_queue()
        assert result is custom
        mq_module._message_queue = None

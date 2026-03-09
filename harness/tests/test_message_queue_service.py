"""Tests for MessageQueue service.

Comprehensive tests targeting 100% statement coverage for:
  forge_harness/webhook_server/services/message_queue.py

Coverage targets:
  - MessageStatus enum
  - QueuedMessage dataclass (to_dict, is_expired)
  - MessageQueue.__init__, enqueue, get_pending, acknowledge,
    get_status, get_conversation, get_stats, _cleanup_expired
  - get_message_queue factory / singleton
"""

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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


def _make_msg(
    msg_id: str = "msg-001",
    sender: str = "agent-a",
    recipient: str = "agent-b",
    msg_type: str = "instruction",
    content: str = "hello",
    priority: int = 1,
    status: MessageStatus = MessageStatus.PENDING,
    expires_at: datetime | None = None,
    metadata: dict | None = None,
) -> QueuedMessage:
    """Build a QueuedMessage for testing."""
    return QueuedMessage(
        id=msg_id,
        sender_id=sender,
        recipient_id=recipient,
        type=msg_type,
        content=content,
        priority=priority,
        status=status,
        expires_at=expires_at,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# MessageStatus
# ---------------------------------------------------------------------------


class TestMessageStatus:
    """Tests for the MessageStatus enum."""

    def test_all_values(self):
        assert MessageStatus.PENDING.value == "pending"
        assert MessageStatus.DELIVERED.value == "delivered"
        assert MessageStatus.ACKNOWLEDGED.value == "acknowledged"
        assert MessageStatus.FAILED.value == "failed"
        assert MessageStatus.EXPIRED.value == "expired"

    def test_all_members_present(self):
        members = {s.name for s in MessageStatus}
        assert members == {"PENDING", "DELIVERED", "ACKNOWLEDGED", "FAILED", "EXPIRED"}


# ---------------------------------------------------------------------------
# QueuedMessage – construction and defaults
# ---------------------------------------------------------------------------


class TestQueuedMessageDefaults:
    def test_required_fields_stored(self):
        msg = _make_msg()
        assert msg.id == "msg-001"
        assert msg.sender_id == "agent-a"
        assert msg.recipient_id == "agent-b"
        assert msg.type == "instruction"
        assert msg.content == "hello"

    def test_default_priority_and_status(self):
        msg = _make_msg()
        assert msg.priority == 1
        assert msg.status == MessageStatus.PENDING

    def test_default_timestamps_none(self):
        msg = _make_msg()
        assert msg.delivered_at is None
        assert msg.acknowledged_at is None
        assert msg.expires_at is None

    def test_default_retry_count_zero(self):
        assert _make_msg().retry_count == 0

    def test_default_metadata_empty_dict(self):
        assert _make_msg().metadata == {}

    def test_created_at_is_utc_aware(self):
        msg = _make_msg()
        assert msg.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# QueuedMessage.to_dict
# ---------------------------------------------------------------------------


class TestQueuedMessageToDict:
    def test_basic_scalar_fields(self):
        msg = _make_msg(metadata={"k": "v"})
        d = msg.to_dict()
        assert d["id"] == "msg-001"
        assert d["sender_id"] == "agent-a"
        assert d["recipient_id"] == "agent-b"
        assert d["type"] == "instruction"
        assert d["content"] == "hello"
        assert d["priority"] == 1
        assert d["status"] == "pending"
        assert d["retry_count"] == 0
        assert d["metadata"] == {"k": "v"}

    def test_null_optional_timestamps(self):
        d = _make_msg().to_dict()
        assert d["delivered_at"] is None
        assert d["acknowledged_at"] is None
        assert d["expires_at"] is None

    def test_all_timestamps_formatted(self):
        now = datetime.now(UTC)
        msg = _make_msg(expires_at=now)
        msg.delivered_at = now
        msg.acknowledged_at = now
        d = msg.to_dict()
        assert d["delivered_at"] == now.isoformat()
        assert d["acknowledged_at"] == now.isoformat()
        assert d["expires_at"] == now.isoformat()
        assert d["created_at"] == msg.created_at.isoformat()

    def test_status_serialised_as_string(self):
        msg = _make_msg(status=MessageStatus.ACKNOWLEDGED)
        assert msg.to_dict()["status"] == "acknowledged"

    def test_metadata_preserved(self):
        msg = _make_msg(metadata={"x": 1, "y": [2, 3]})
        assert msg.to_dict()["metadata"] == {"x": 1, "y": [2, 3]}


# ---------------------------------------------------------------------------
# QueuedMessage.is_expired
# ---------------------------------------------------------------------------


class TestQueuedMessageIsExpired:
    def test_no_expiry_is_false(self):
        assert _make_msg(expires_at=None).is_expired() is False

    def test_future_expiry_is_false(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert _make_msg(expires_at=future).is_expired() is False

    def test_past_expiry_is_true(self):
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert _make_msg(expires_at=past).is_expired() is True


# ---------------------------------------------------------------------------
# MessageQueue.__init__
# ---------------------------------------------------------------------------


class TestMessageQueueInit:
    def test_default_ttl(self):
        q = MessageQueue()
        assert q._default_ttl == 3600

    def test_custom_ttl(self):
        q = MessageQueue(default_ttl_seconds=120)
        assert q._default_ttl == 120

    def test_empty_state(self):
        q = MessageQueue()
        assert len(q._messages) == 0
        assert len(q._agent_inbox) == 0


# ---------------------------------------------------------------------------
# MessageQueue.enqueue
# ---------------------------------------------------------------------------


class TestMessageQueueEnqueue:
    @pytest.fixture
    def q(self):
        return MessageQueue(default_ttl_seconds=3600)

    def test_returns_queued_message(self, q):
        msg = q.enqueue("sender", "recipient", "hello")
        assert isinstance(msg, QueuedMessage)
        assert msg.sender_id == "sender"
        assert msg.recipient_id == "recipient"
        assert msg.content == "hello"

    def test_default_type_and_priority(self, q):
        msg = q.enqueue("a", "b", "c")
        assert msg.type == "instruction"
        assert msg.priority == 1
        assert msg.status == MessageStatus.PENDING

    def test_custom_type_and_priority(self, q):
        msg = q.enqueue("a", "b", "body", msg_type="handoff", priority=3)
        assert msg.type == "handoff"
        assert msg.priority == 3

    def test_ttl_none_uses_default(self):
        q = MessageQueue(default_ttl_seconds=60)
        before = datetime.now(UTC)
        msg = q.enqueue("a", "b", "c")
        after = datetime.now(UTC)
        assert msg.expires_at is not None
        assert before + timedelta(seconds=60) <= msg.expires_at <= after + timedelta(seconds=60)

    def test_custom_ttl_overrides_default(self, q):
        before = datetime.now(UTC)
        msg = q.enqueue("a", "b", "c", ttl_seconds=10)
        after = datetime.now(UTC)
        assert before + timedelta(seconds=10) <= msg.expires_at <= after + timedelta(seconds=10)

    def test_none_metadata_becomes_empty_dict(self, q):
        msg = q.enqueue("a", "b", "c", metadata=None)
        assert msg.metadata == {}

    def test_metadata_stored(self, q):
        msg = q.enqueue("a", "b", "c", metadata={"x": 1})
        assert msg.metadata == {"x": 1}

    def test_message_stored_in_messages_dict(self, q):
        msg = q.enqueue("a", "b", "c")
        assert msg.id in q._messages
        assert q._messages[msg.id] is msg

    def test_message_added_to_recipient_inbox(self, q):
        msg = q.enqueue("a", "recipient", "c")
        assert "recipient" in q._agent_inbox
        assert msg.id in q._agent_inbox["recipient"]

    def test_multiple_messages_same_inbox(self, q):
        m1 = q.enqueue("a", "b", "m1")
        m2 = q.enqueue("c", "b", "m2")
        assert m1.id in q._agent_inbox["b"]
        assert m2.id in q._agent_inbox["b"]

    def test_different_recipients_separate_inboxes(self, q):
        q.enqueue("a", "r1", "m1")
        q.enqueue("a", "r2", "m2")
        assert "r1" in q._agent_inbox
        assert "r2" in q._agent_inbox
        assert len(q._agent_inbox["r1"]) == 1
        assert len(q._agent_inbox["r2"]) == 1

    def test_unique_ids_generated(self, q):
        ids = {q.enqueue("a", "b", "c").id for _ in range(20)}
        assert len(ids) == 20

    def test_enqueue_logs_info(self, q):
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            q.enqueue("a", "b", "hello")
            mock_log.info.assert_called_once()


# ---------------------------------------------------------------------------
# MessageQueue.get_pending
# ---------------------------------------------------------------------------


class TestMessageQueueGetPending:
    @pytest.fixture
    def q(self):
        return MessageQueue()

    def test_empty_inbox_returns_empty_list(self, q):
        assert q.get_pending("nobody") == []

    def test_returns_pending_messages(self, q):
        q.enqueue("a", "b", "m1")
        q.enqueue("c", "b", "m2")
        result = q.get_pending("b")
        assert len(result) == 2

    def test_mark_delivered_true_sets_status(self, q):
        msg = q.enqueue("a", "b", "hello")
        result = q.get_pending("b", mark_delivered=True)
        assert result[0].status == MessageStatus.DELIVERED
        assert result[0].delivered_at is not None

    def test_mark_delivered_false_leaves_pending(self, q):
        msg = q.enqueue("a", "b", "hello")
        result = q.get_pending("b", mark_delivered=False)
        assert result[0].status == MessageStatus.PENDING
        assert result[0].delivered_at is None

    def test_second_call_empty_after_delivery(self, q):
        q.enqueue("a", "b", "hello")
        q.get_pending("b", mark_delivered=True)
        assert q.get_pending("b") == []

    def test_excludes_already_delivered_messages(self, q):
        msg = q.enqueue("a", "b", "hello")
        msg.status = MessageStatus.DELIVERED
        assert q.get_pending("b") == []

    def test_excludes_acknowledged_messages(self, q):
        msg = q.enqueue("a", "b", "hello")
        msg.status = MessageStatus.ACKNOWLEDGED
        assert q.get_pending("b") == []

    def test_sorted_by_priority_descending(self, q):
        q.enqueue("a", "b", "low", priority=0)
        q.enqueue("a", "b", "normal", priority=1)
        q.enqueue("a", "b", "urgent", priority=3)
        q.enqueue("a", "b", "high", priority=2)
        result = q.get_pending("b", mark_delivered=False)
        priorities = [m.priority for m in result]
        assert priorities == sorted(priorities, reverse=True)

    def test_same_priority_sorted_by_created_at_ascending(self, q):
        m1 = q.enqueue("a", "b", "first", priority=1)
        m2 = q.enqueue("a", "b", "second", priority=1)
        m3 = q.enqueue("a", "b", "third", priority=1)
        result = q.get_pending("b", mark_delivered=False)
        assert [m.id for m in result] == [m1.id, m2.id, m3.id]

    def test_triggers_cleanup_of_expired_messages(self, q):
        msg = q.enqueue("a", "b", "old")
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        result = q.get_pending("b")
        assert result == []
        assert msg.status == MessageStatus.EXPIRED

    def test_only_returns_messages_for_requested_agent(self, q):
        q.enqueue("a", "b", "for b")
        q.enqueue("a", "c", "for c")
        result = q.get_pending("b")
        assert len(result) == 1
        assert result[0].recipient_id == "b"


# ---------------------------------------------------------------------------
# MessageQueue.acknowledge
# ---------------------------------------------------------------------------


class TestMessageQueueAcknowledge:
    @pytest.fixture
    def q(self):
        return MessageQueue()

    def test_acknowledge_delivered_message_succeeds(self, q):
        msg = q.enqueue("a", "b", "hello")
        q.get_pending("b", mark_delivered=True)
        result = q.acknowledge(msg.id, "b")
        assert result is not None
        assert result.status == MessageStatus.ACKNOWLEDGED
        assert result.acknowledged_at is not None

    def test_acknowledge_pending_message_succeeds(self, q):
        """acknowledge works on PENDING (not yet delivered) messages."""
        msg = q.enqueue("a", "b", "hello")
        result = q.acknowledge(msg.id, "b")
        assert result is not None
        assert result.status == MessageStatus.ACKNOWLEDGED

    def test_acknowledge_unknown_id_returns_none(self, q):
        result = q.acknowledge("does-not-exist", "b")
        assert result is None

    def test_acknowledge_wrong_agent_returns_none(self, q):
        msg = q.enqueue("a", "b", "hello")
        result = q.acknowledge(msg.id, "wrong-agent")
        assert result is None
        assert msg.status == MessageStatus.PENDING  # unchanged

    def test_acknowledge_already_acknowledged_returns_none(self, q):
        msg = q.enqueue("a", "b", "hello")
        q.acknowledge(msg.id, "b")
        result = q.acknowledge(msg.id, "b")
        assert result is None

    def test_acknowledge_expired_returns_none(self, q):
        msg = q.enqueue("a", "b", "hello")
        msg.status = MessageStatus.EXPIRED
        result = q.acknowledge(msg.id, "b")
        assert result is None

    def test_acknowledge_failed_returns_none(self, q):
        msg = q.enqueue("a", "b", "hello")
        msg.status = MessageStatus.FAILED
        result = q.acknowledge(msg.id, "b")
        assert result is None

    def test_acknowledge_logs_warning_on_missing_message(self, q):
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            q.acknowledge("bad-id", "agent")
            mock_log.warning.assert_called_once()

    def test_acknowledge_logs_warning_on_wrong_agent(self, q):
        msg = q.enqueue("a", "b", "hi")
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            q.acknowledge(msg.id, "wrong-agent")
            mock_log.warning.assert_called_once()

    def test_acknowledge_logs_warning_on_bad_status(self, q):
        msg = q.enqueue("a", "b", "hi")
        msg.status = MessageStatus.EXPIRED
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            q.acknowledge(msg.id, "b")
            mock_log.warning.assert_called_once()

    def test_acknowledge_logs_info_on_success(self, q):
        msg = q.enqueue("a", "b", "hi")
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            q.acknowledge(msg.id, "b")
            mock_log.info.assert_called_once()


# ---------------------------------------------------------------------------
# MessageQueue.get_status
# ---------------------------------------------------------------------------


class TestMessageQueueGetStatus:
    @pytest.fixture
    def q(self):
        return MessageQueue()

    def test_returns_existing_message(self, q):
        msg = q.enqueue("a", "b", "hello")
        found = q.get_status(msg.id)
        assert found is msg

    def test_returns_none_for_missing_id(self, q):
        assert q.get_status("does-not-exist") is None


# ---------------------------------------------------------------------------
# MessageQueue.get_conversation
# ---------------------------------------------------------------------------


class TestMessageQueueGetConversation:
    @pytest.fixture
    def q(self):
        return MessageQueue()

    def test_returns_messages_both_directions(self, q):
        m1 = q.enqueue("a", "b", "hi from a")
        m2 = q.enqueue("b", "a", "hi from b")
        q.enqueue("a", "c", "unrelated")
        conv = q.get_conversation("a", "b")
        ids = {m.id for m in conv}
        assert m1.id in ids
        assert m2.id in ids
        assert len(conv) == 2

    def test_sorted_newest_first(self, q):
        q.enqueue("a", "b", "first")
        q.enqueue("a", "b", "second")
        q.enqueue("b", "a", "third")
        conv = q.get_conversation("a", "b")
        for i in range(len(conv) - 1):
            assert conv[i].created_at >= conv[i + 1].created_at

    def test_limit_respected(self, q):
        for i in range(10):
            q.enqueue("a", "b", f"msg-{i}")
        conv = q.get_conversation("a", "b", limit=3)
        assert len(conv) == 3

    def test_default_limit_fifty(self, q):
        for i in range(60):
            q.enqueue("a", "b", f"msg-{i}")
        conv = q.get_conversation("a", "b")
        assert len(conv) == 50

    def test_empty_when_no_messages(self, q):
        assert q.get_conversation("a", "b") == []

    def test_excludes_messages_outside_pair(self, q):
        q.enqueue("a", "c", "not related")
        q.enqueue("d", "b", "also not related")
        assert q.get_conversation("a", "b") == []


# ---------------------------------------------------------------------------
# MessageQueue.get_stats
# ---------------------------------------------------------------------------


class TestMessageQueueGetStats:
    @pytest.fixture
    def q(self):
        return MessageQueue()

    def test_empty_queue_stats(self, q):
        stats = q.get_stats()
        assert stats["total_messages"] == 0
        assert stats["total_inboxes"] == 0
        assert stats["by_status"] == {}

    def test_total_messages_count(self, q):
        q.enqueue("a", "b", "m1")
        q.enqueue("a", "b", "m2")
        assert q.get_stats()["total_messages"] == 2

    def test_total_inboxes_count(self, q):
        q.enqueue("a", "b", "m1")
        q.enqueue("a", "c", "m2")
        assert q.get_stats()["total_inboxes"] == 2

    def test_by_status_counts(self, q):
        q.enqueue("a", "b", "pending-msg")
        msg2 = q.enqueue("a", "b", "delivered-msg")
        msg2.status = MessageStatus.DELIVERED
        stats = q.get_stats()
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["delivered"] == 1

    def test_by_status_all_variants(self, q):
        for status in MessageStatus:
            msg = q.enqueue("a", "b", f"msg-{status.value}")
            msg.status = status
        stats = q.get_stats()
        for status in MessageStatus:
            assert stats["by_status"][status.value] == 1


# ---------------------------------------------------------------------------
# MessageQueue._cleanup_expired (internal, tested through public API and directly)
# ---------------------------------------------------------------------------


class TestCleanupExpired:
    @pytest.fixture
    def q(self):
        return MessageQueue()

    def test_marks_expired_pending_messages(self, q):
        msg = q.enqueue("a", "b", "old")
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with q._lock:
            count = q._cleanup_expired()
        assert count == 1
        assert msg.status == MessageStatus.EXPIRED

    def test_ignores_non_pending_expired_messages(self, q):
        msg = q.enqueue("a", "b", "delivered")
        msg.status = MessageStatus.DELIVERED
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with q._lock:
            count = q._cleanup_expired()
        assert count == 0
        assert msg.status == MessageStatus.DELIVERED

    def test_returns_zero_when_nothing_expired(self, q):
        q.enqueue("a", "b", "fresh")
        with q._lock:
            count = q._cleanup_expired()
        assert count == 0

    def test_cleans_multiple_expired(self, q):
        msgs = [q.enqueue("a", "b", f"m{i}") for i in range(5)]
        for m in msgs:
            m.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with q._lock:
            count = q._cleanup_expired()
        assert count == 5
        for m in msgs:
            assert m.status == MessageStatus.EXPIRED

    def test_mixed_expired_and_valid(self, q):
        expired = q.enqueue("a", "b", "expired")
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        valid = q.enqueue("a", "b", "valid")  # expires_at set to future by enqueue
        with q._lock:
            count = q._cleanup_expired()
        assert count == 1
        assert expired.status == MessageStatus.EXPIRED
        assert valid.status == MessageStatus.PENDING

    def test_logs_info_when_messages_expired(self, q):
        msg = q.enqueue("a", "b", "old")
        msg.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            with q._lock:
                q._cleanup_expired()
            mock_log.info.assert_called()

    def test_no_log_when_nothing_to_clean(self, q):
        q.enqueue("a", "b", "fresh")
        with patch("forge_harness.webhook_server.services.message_queue.logger") as mock_log:
            with q._lock:
                q._cleanup_expired()
            mock_log.info.assert_not_called()


# ---------------------------------------------------------------------------
# get_message_queue factory / singleton
# ---------------------------------------------------------------------------


class TestGetMessageQueue:
    def setup_method(self):
        """Reset global singleton before each test."""
        import forge_harness.webhook_server.services.message_queue as mq_module

        mq_module._message_queue = None

    def teardown_method(self):
        """Clean up global singleton after each test."""
        import forge_harness.webhook_server.services.message_queue as mq_module

        mq_module._message_queue = None

    def test_returns_message_queue_instance(self):
        queue = get_message_queue()
        assert isinstance(queue, MessageQueue)

    def test_returns_same_instance_on_repeated_calls(self):
        q1 = get_message_queue()
        q2 = get_message_queue()
        assert q1 is q2

    def test_returns_existing_instance_when_set(self):
        import forge_harness.webhook_server.services.message_queue as mq_module

        existing = MessageQueue(default_ttl_seconds=999)
        mq_module._message_queue = existing
        result = get_message_queue()
        assert result is existing


# ---------------------------------------------------------------------------
# Thread-safety smoke tests
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_enqueue(self):
        """No data corruption when multiple threads enqueue simultaneously."""
        q = MessageQueue()
        errors: list[Exception] = []

        def worker():
            try:
                for i in range(50):
                    q.enqueue("sender", "recipient", f"msg-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(q._messages) == 200

    def test_concurrent_readers_and_writers(self):
        """Mixed get_pending / enqueue do not corrupt state."""
        q = MessageQueue()
        errors: list[Exception] = []

        def writer():
            try:
                for i in range(20):
                    q.enqueue("sender", "recipient", f"m{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(20):
                    q.get_pending("recipient")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(2)] + [
            threading.Thread(target=reader) for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

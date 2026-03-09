"""Tests for the MessageQueue with acknowledgment tracking.

Tests the inter-agent messaging system including:
- Message queueing with unique IDs
- Delivery tracking
- Acknowledgment protocol
- Message expiration
- Priority ordering
- Thread safety
- Global queue singleton
"""

import uuid
from datetime import UTC, datetime, timedelta
from threading import Thread
from time import sleep

import pytest

from forge_harness.webhook_server import (
    MessageQueue,
    MessageStatus,
    QueuedMessage,
)
from forge_harness.webhook_server.services.message_queue import get_message_queue


class TestMessageQueue:
    """Test suite for MessageQueue."""

    def test_enqueue_creates_message_with_id(self):
        """Enqueue should create a message with a unique ID."""
        queue = MessageQueue()

        msg = queue.enqueue(
            sender_id="agent-a",
            recipient_id="agent-b",
            content="Hello",
            msg_type="instruction",
        )

        assert msg.id is not None
        assert len(msg.id) == 36  # UUID format
        assert msg.sender_id == "agent-a"
        assert msg.recipient_id == "agent-b"
        assert msg.content == "Hello"
        assert msg.type == "instruction"
        assert msg.status == MessageStatus.PENDING

    def test_enqueue_sets_expiration(self):
        """Enqueue should set message expiration based on TTL."""
        queue = MessageQueue(default_ttl_seconds=3600)

        msg = queue.enqueue(
            sender_id="agent-a",
            recipient_id="agent-b",
            content="Test",
        )

        assert msg.expires_at is not None
        assert msg.expires_at > datetime.now(UTC)

    def test_enqueue_unique_ids(self):
        """Enqueue should generate unique IDs for each message."""
        queue = MessageQueue()
        msg1 = queue.enqueue("agent1", "agent2", "Message 1")
        msg2 = queue.enqueue("agent1", "agent2", "Message 2")
        msg3 = queue.enqueue("agent1", "agent2", "Message 3")

        assert msg1.id != msg2.id
        assert msg2.id != msg3.id
        assert msg1.id != msg3.id

    def test_enqueue_with_custom_ttl(self):
        """Enqueue should respect custom TTL over default."""
        queue = MessageQueue(default_ttl_seconds=3600)
        msg = queue.enqueue(
            sender_id="agent1",
            recipient_id="agent2",
            content="Custom TTL",
            ttl_seconds=1800,
        )

        # Check expiry is closer to 30min than 60min
        now = datetime.now(UTC)
        time_diff = (msg.expires_at - now).total_seconds()
        assert 1700 < time_diff < 1900  # ~30 minutes

    def test_enqueue_with_metadata(self):
        """Enqueue should store custom metadata."""
        queue = MessageQueue()
        metadata = {"task_id": "123", "urgent": True, "count": 42}
        msg = queue.enqueue(
            sender_id="agent1",
            recipient_id="agent2",
            content="Test",
            metadata=metadata,
        )

        assert msg.metadata == metadata

    def test_enqueue_multiple_agents_inbox_separation(self):
        """Enqueue should maintain separate inboxes for different agents."""
        queue = MessageQueue()
        queue.enqueue("agent1", "agent2", "To agent2")
        queue.enqueue("agent1", "agent3", "To agent3")
        queue.enqueue("agent2", "agent3", "Also to agent3")

        stats = queue.get_stats()
        assert stats["total_messages"] == 3
        assert stats["total_inboxes"] == 2  # agent2 and agent3

    def test_get_pending_returns_messages_for_agent(self):
        """get_pending should return only pending messages for the specified agent."""
        queue = MessageQueue()

        # Send messages to different agents
        queue.enqueue(sender_id="a", recipient_id="agent-1", content="msg1")
        queue.enqueue(sender_id="a", recipient_id="agent-2", content="msg2")
        queue.enqueue(sender_id="a", recipient_id="agent-1", content="msg3")

        pending = queue.get_pending("agent-1", mark_delivered=False)

        assert len(pending) == 2
        assert all(m.recipient_id == "agent-1" for m in pending)

    def test_get_pending_marks_as_delivered(self):
        """get_pending with mark_delivered=True should update message status."""
        queue = MessageQueue()

        msg = queue.enqueue(sender_id="a", recipient_id="agent-1", content="test")
        assert msg.status == MessageStatus.PENDING

        pending = queue.get_pending("agent-1", mark_delivered=True)

        assert len(pending) == 1
        assert pending[0].status == MessageStatus.DELIVERED
        assert pending[0].delivered_at is not None

    def test_get_pending_without_mark_delivered(self):
        """get_pending with mark_delivered=False should not change status."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")

        pending = queue.get_pending("agent2", mark_delivered=False)

        assert len(pending) == 1
        assert pending[0].status == MessageStatus.PENDING
        assert pending[0].delivered_at is None

    def test_get_pending_sorted_by_priority(self):
        """get_pending should return messages sorted by priority (highest first)."""
        queue = MessageQueue()

        queue.enqueue(sender_id="a", recipient_id="b", content="low", priority=0)
        queue.enqueue(sender_id="a", recipient_id="b", content="urgent", priority=3)
        queue.enqueue(sender_id="a", recipient_id="b", content="normal", priority=1)
        queue.enqueue(sender_id="a", recipient_id="b", content="high", priority=2)

        pending = queue.get_pending("b", mark_delivered=False)

        assert [m.content for m in pending] == ["urgent", "high", "normal", "low"]

    def test_get_pending_same_priority_chronological(self):
        """get_pending should order same-priority messages chronologically."""
        queue = MessageQueue()
        msg1 = queue.enqueue("agent1", "agent2", "First", priority=1)
        sleep(0.01)
        msg2 = queue.enqueue("agent1", "agent2", "Second", priority=1)
        sleep(0.01)
        msg3 = queue.enqueue("agent1", "agent2", "Third", priority=1)

        pending = queue.get_pending("agent2", mark_delivered=False)

        assert len(pending) == 3
        assert pending[0].id == msg1.id  # Oldest first
        assert pending[1].id == msg2.id
        assert pending[2].id == msg3.id

    def test_get_pending_no_messages(self):
        """get_pending should return empty list when no messages."""
        queue = MessageQueue()
        pending = queue.get_pending("agent1")

        assert pending == []

    def test_get_pending_only_returns_pending_status(self):
        """get_pending should only return messages with PENDING status."""
        queue = MessageQueue()
        queue.enqueue("agent1", "agent2", "Message 1")
        queue.enqueue("agent1", "agent2", "Message 2")

        # First call marks as delivered
        first = queue.get_pending("agent2")
        assert len(first) == 2

        # Second call should return empty since all are delivered
        second = queue.get_pending("agent2")
        assert len(second) == 0

    def test_get_pending_cleans_expired_messages(self):
        """get_pending should clean up expired messages."""
        queue = MessageQueue()
        msg1 = queue.enqueue("agent1", "agent2", "Expires soon", ttl_seconds=0)
        msg2 = queue.enqueue("agent1", "agent2", "Valid", ttl_seconds=3600)

        sleep(0.1)  # Let msg1 expire

        pending = queue.get_pending("agent2")

        # Only msg2 should be delivered
        assert len(pending) == 1
        assert pending[0].id == msg2.id

        # Check msg1 is expired
        status = queue.get_status(msg1.id)
        assert status.status == MessageStatus.EXPIRED

    def test_acknowledge_updates_status(self):
        """acknowledge should update message status and set acknowledged_at."""
        queue = MessageQueue()

        msg = queue.enqueue(sender_id="a", recipient_id="agent-1", content="test")

        acked = queue.acknowledge(msg.id, "agent-1")

        assert acked is not None
        assert acked.status == MessageStatus.ACKNOWLEDGED
        assert acked.acknowledged_at is not None

    def test_acknowledge_from_delivered_status(self):
        """acknowledge should work on messages in DELIVERED status."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")
        queue.get_pending("agent2")  # Mark as delivered

        result = queue.acknowledge(msg.id, "agent2")

        assert result is not None
        assert result.status == MessageStatus.ACKNOWLEDGED

    def test_acknowledge_requires_correct_recipient(self):
        """acknowledge should fail if called by wrong agent."""
        queue = MessageQueue()

        msg = queue.enqueue(sender_id="a", recipient_id="agent-1", content="test")

        acked = queue.acknowledge(msg.id, "agent-2")  # Wrong agent

        assert acked is None

    def test_acknowledge_fails_for_unknown_message(self):
        """acknowledge should fail for unknown message ID."""
        queue = MessageQueue()

        acked = queue.acknowledge("nonexistent-id", "agent-1")

        assert acked is None

    def test_acknowledge_already_acknowledged(self):
        """acknowledge should fail on already acknowledged message."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")
        queue.acknowledge(msg.id, "agent2")

        result = queue.acknowledge(msg.id, "agent2")

        assert result is None

    def test_acknowledge_failed_message(self):
        """acknowledge should fail on FAILED messages."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")
        msg.status = MessageStatus.FAILED

        result = queue.acknowledge(msg.id, "agent2")

        assert result is None

    def test_acknowledge_expired_message(self):
        """acknowledge should fail on EXPIRED messages."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")
        msg.status = MessageStatus.EXPIRED

        result = queue.acknowledge(msg.id, "agent2")

        assert result is None

    def test_get_status_returns_message(self):
        """get_status should return the message with current status."""
        queue = MessageQueue()

        msg = queue.enqueue(sender_id="a", recipient_id="b", content="test")

        status = queue.get_status(msg.id)

        assert status is not None
        assert status.id == msg.id
        assert status.status == MessageStatus.PENDING

    def test_get_status_returns_none_for_unknown(self):
        """get_status should return None for unknown message ID."""
        queue = MessageQueue()

        status = queue.get_status("nonexistent-id")

        assert status is None

    def test_get_status_reflects_updates(self):
        """get_status should reflect message status updates."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")

        status1 = queue.get_status(msg.id)
        assert status1.status == MessageStatus.PENDING

        queue.get_pending("agent2")

        status2 = queue.get_status(msg.id)
        assert status2.status == MessageStatus.DELIVERED

        queue.acknowledge(msg.id, "agent2")

        status3 = queue.get_status(msg.id)
        assert status3.status == MessageStatus.ACKNOWLEDGED

    def test_get_conversation_returns_messages_between_agents(self):
        """get_conversation should return messages between two agents."""
        queue = MessageQueue()

        # Messages between agent-1 and agent-2
        queue.enqueue(sender_id="agent-1", recipient_id="agent-2", content="hi")
        queue.enqueue(sender_id="agent-2", recipient_id="agent-1", content="hello")
        queue.enqueue(sender_id="agent-1", recipient_id="agent-2", content="how are you")

        # Message to different agent (should not be included)
        queue.enqueue(sender_id="agent-1", recipient_id="agent-3", content="other")

        conversation = queue.get_conversation("agent-1", "agent-2")

        assert len(conversation) == 3
        assert all(
            (m.sender_id in ("agent-1", "agent-2") and m.recipient_id in ("agent-1", "agent-2"))
            for m in conversation
        )

    def test_get_conversation_order(self):
        """get_conversation should return messages newest first."""
        queue = MessageQueue()
        msg1 = queue.enqueue("agent1", "agent2", "First")
        sleep(0.01)
        msg2 = queue.enqueue("agent2", "agent1", "Second")
        sleep(0.01)
        msg3 = queue.enqueue("agent1", "agent2", "Third")

        conversation = queue.get_conversation("agent1", "agent2")

        assert len(conversation) == 3
        assert conversation[0].id == msg3.id  # Newest first
        assert conversation[1].id == msg2.id
        assert conversation[2].id == msg1.id

    def test_get_conversation_limit(self):
        """get_conversation should respect limit parameter."""
        queue = MessageQueue()
        for i in range(10):
            queue.enqueue("agent1", "agent2", f"Message {i}")

        conversation = queue.get_conversation("agent1", "agent2", limit=5)

        assert len(conversation) == 5

    def test_get_conversation_no_messages(self):
        """get_conversation should return empty list when no messages."""
        queue = MessageQueue()
        conversation = queue.get_conversation("agent1", "agent2")

        assert conversation == []

    def test_get_conversation_excludes_other_agents(self):
        """get_conversation should exclude messages with other agents."""
        queue = MessageQueue()
        msg1 = queue.enqueue("agent1", "agent2", "A to B")
        msg2 = queue.enqueue("agent1", "agent3", "A to C")
        msg3 = queue.enqueue("agent2", "agent3", "B to C")
        msg4 = queue.enqueue("agent2", "agent1", "B to A")

        conversation = queue.get_conversation("agent1", "agent2")

        assert len(conversation) == 2
        message_ids = [m.id for m in conversation]
        assert msg1.id in message_ids
        assert msg4.id in message_ids
        assert msg2.id not in message_ids
        assert msg3.id not in message_ids

    def test_get_stats_returns_counts(self):
        """get_stats should return message queue statistics."""
        queue = MessageQueue()

        queue.enqueue(sender_id="a", recipient_id="b", content="1")
        queue.enqueue(sender_id="a", recipient_id="b", content="2")
        msg = queue.enqueue(sender_id="a", recipient_id="c", content="3")
        queue.acknowledge(msg.id, "c")

        stats = queue.get_stats()

        assert stats["total_messages"] == 3
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["acknowledged"] == 1

    def test_get_stats_empty_queue(self):
        """get_stats should return zero counts for empty queue."""
        queue = MessageQueue()
        stats = queue.get_stats()

        assert stats["total_messages"] == 0
        assert stats["total_inboxes"] == 0
        assert stats["by_status"] == {}

    def test_get_stats_multiple_statuses(self):
        """get_stats should count messages by status correctly."""
        queue = MessageQueue()
        msg1 = queue.enqueue("agent1", "agent2", "Message 1")
        msg2 = queue.enqueue("agent1", "agent2", "Message 2")
        msg3 = queue.enqueue("agent1", "agent3", "Message 3")

        # msg1, msg2: delivered
        queue.get_pending("agent2")
        # msg3: acknowledged
        queue.get_pending("agent3")
        queue.acknowledge(msg3.id, "agent3")

        stats = queue.get_stats()

        assert stats["total_messages"] == 3
        assert stats["by_status"]["delivered"] == 2
        assert stats["by_status"]["acknowledged"] == 1

    def test_message_to_dict(self):
        """QueuedMessage.to_dict should return serializable dict."""
        queue = MessageQueue()

        msg = queue.enqueue(
            sender_id="agent-a",
            recipient_id="agent-b",
            content="Test message",
            msg_type="instruction",
            priority=2,
            metadata={"key": "value"},
        )

        d = msg.to_dict()

        assert d["id"] == msg.id
        assert d["sender_id"] == "agent-a"
        assert d["recipient_id"] == "agent-b"
        assert d["content"] == "Test message"
        assert d["type"] == "instruction"
        assert d["priority"] == 2
        assert d["status"] == "pending"
        assert d["metadata"] == {"key": "value"}
        assert "created_at" in d

    def test_thread_safety_concurrent_enqueue(self):
        """MessageQueue should be thread-safe for concurrent enqueue."""
        queue = MessageQueue()
        messages = []

        def enqueue_messages():
            for i in range(10):
                msg = queue.enqueue("agent1", "agent2", f"Message {i}")
                messages.append(msg)

        threads = [Thread(target=enqueue_messages) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = queue.get_stats()
        assert stats["total_messages"] == 50  # 5 threads * 10 messages

    def test_thread_safety_concurrent_get_pending(self):
        """MessageQueue should be thread-safe for concurrent get_pending."""
        queue = MessageQueue()
        for i in range(20):
            queue.enqueue("agent1", "agent2", f"Message {i}")

        results = []

        def get_messages():
            pending = queue.get_pending("agent2")
            results.append(len(pending))

        threads = [Thread(target=get_messages) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # First thread gets all 20, others get 0
        assert 20 in results
        assert sum(results) == 20

    def test_thread_safety_concurrent_acknowledge(self):
        """MessageQueue should be thread-safe for concurrent acknowledge."""
        queue = MessageQueue()
        msg = queue.enqueue("agent1", "agent2", "Test")

        results = []

        def acknowledge_message():
            result = queue.acknowledge(msg.id, "agent2")
            results.append(result is not None)

        threads = [Thread(target=acknowledge_message) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one thread should succeed
        assert sum(results) == 1


class TestQueuedMessage:
    """Test suite for QueuedMessage dataclass."""

    def test_message_creation_defaults(self):
        """QueuedMessage should have correct default values."""
        msg = QueuedMessage(
            id="test-id",
            sender_id="agent1",
            recipient_id="agent2",
            type="instruction",
            content="Test",
        )

        assert msg.priority == 1
        assert msg.status == MessageStatus.PENDING
        assert msg.created_at is not None
        assert msg.delivered_at is None
        assert msg.acknowledged_at is None
        assert msg.expires_at is None
        assert msg.metadata == {}
        assert msg.retry_count == 0

    def test_message_creation_all_fields(self):
        """QueuedMessage should accept all field values."""
        created = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)
        delivered = datetime(2026, 2, 16, 12, 1, 0, tzinfo=UTC)
        acknowledged = datetime(2026, 2, 16, 12, 2, 0, tzinfo=UTC)
        expires = datetime(2026, 2, 16, 13, 0, 0, tzinfo=UTC)
        metadata = {"key": "value", "count": 42}

        msg = QueuedMessage(
            id="test-id",
            sender_id="system",
            recipient_id="agent3",
            type="notification",
            content="Important",
            priority=3,
            status=MessageStatus.ACKNOWLEDGED,
            created_at=created,
            delivered_at=delivered,
            acknowledged_at=acknowledged,
            expires_at=expires,
            metadata=metadata,
            retry_count=2,
        )

        assert msg.priority == 3
        assert msg.status == MessageStatus.ACKNOWLEDGED
        assert msg.created_at == created
        assert msg.delivered_at == delivered
        assert msg.acknowledged_at == acknowledged
        assert msg.expires_at == expires
        assert msg.metadata == metadata
        assert msg.retry_count == 2

    def test_to_dict_with_all_timestamps(self):
        """to_dict should serialize all timestamp fields."""
        created = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)
        delivered = datetime(2026, 2, 16, 12, 1, 0, tzinfo=UTC)
        acknowledged = datetime(2026, 2, 16, 12, 2, 0, tzinfo=UTC)
        expires = datetime(2026, 2, 16, 13, 0, 0, tzinfo=UTC)

        msg = QueuedMessage(
            id="test-id",
            sender_id="agent1",
            recipient_id="agent2",
            type="instruction",
            content="Test",
            created_at=created,
            delivered_at=delivered,
            acknowledged_at=acknowledged,
            expires_at=expires,
        )

        d = msg.to_dict()

        assert d["created_at"] == created.isoformat()
        assert d["delivered_at"] == delivered.isoformat()
        assert d["acknowledged_at"] == acknowledged.isoformat()
        assert d["expires_at"] == expires.isoformat()

    def test_to_dict_with_none_timestamps(self):
        """to_dict should handle None timestamps."""
        msg = QueuedMessage(
            id="test-id",
            sender_id="agent1",
            recipient_id="agent2",
            type="instruction",
            content="Test",
        )

        d = msg.to_dict()

        assert d["delivered_at"] is None
        assert d["acknowledged_at"] is None
        assert d["expires_at"] is None

    def test_is_expired_returns_false_when_no_expiration(self):
        """is_expired should return False when expires_at is None."""
        msg = QueuedMessage(
            id="test",
            sender_id="a",
            recipient_id="b",
            type="instruction",
            content="test",
            expires_at=None,
        )

        assert msg.is_expired() is False

    def test_is_expired_returns_false_when_not_expired(self):
        """is_expired should return False when expires_at is in the future."""
        msg = QueuedMessage(
            id="test",
            sender_id="a",
            recipient_id="b",
            type="instruction",
            content="test",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        assert msg.is_expired() is False

    def test_is_expired_returns_true_when_expired(self):
        """is_expired should return True when expires_at is in the past."""
        msg = QueuedMessage(
            id="test",
            sender_id="a",
            recipient_id="b",
            type="instruction",
            content="test",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        assert msg.is_expired() is True

    def test_is_expired_boundary(self):
        """is_expired should handle boundary case at expiry time."""
        # Set expiry to a few milliseconds in the future
        msg = QueuedMessage(
            id="test",
            sender_id="a",
            recipient_id="b",
            type="instruction",
            content="test",
            expires_at=datetime.now(UTC) + timedelta(milliseconds=50),
        )

        assert msg.is_expired() is False

        # Wait and check again
        sleep(0.1)
        assert msg.is_expired() is True


class TestGetMessageQueue:
    """Test suite for global message queue singleton."""

    def test_get_message_queue_returns_instance(self):
        """get_message_queue should return MessageQueue instance."""
        queue = get_message_queue()
        assert queue is not None
        assert isinstance(queue, MessageQueue)

    def test_get_message_queue_returns_same_instance(self):
        """get_message_queue should return same instance on multiple calls."""
        queue1 = get_message_queue()
        queue2 = get_message_queue()

        assert queue1 is queue2

    def test_global_queue_persists_data(self):
        """Global queue should persist data across get_message_queue calls."""
        queue1 = get_message_queue()
        msg = queue1.enqueue("agent1", "agent2", "Test persistence")

        queue2 = get_message_queue()
        status = queue2.get_status(msg.id)

        assert status is not None
        assert status.id == msg.id


class TestMessageStatusEnum:
    """Test suite for MessageStatus enum."""

    def test_message_status_values(self):
        """MessageStatus should have correct string values."""
        assert MessageStatus.PENDING.value == "pending"
        assert MessageStatus.DELIVERED.value == "delivered"
        assert MessageStatus.ACKNOWLEDGED.value == "acknowledged"
        assert MessageStatus.FAILED.value == "failed"
        assert MessageStatus.EXPIRED.value == "expired"

    def test_message_status_comparison(self):
        """MessageStatus enum values should be comparable."""
        assert MessageStatus.PENDING == MessageStatus.PENDING
        assert MessageStatus.PENDING != MessageStatus.DELIVERED
        assert MessageStatus.DELIVERED != MessageStatus.ACKNOWLEDGED

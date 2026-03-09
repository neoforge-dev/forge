"""
Message Queue Service

Provides reliable inter-agent messaging with acknowledgment tracking.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class MessageStatus(Enum):
    """Status of a queued message."""

    PENDING = "pending"  # Message sent, waiting for delivery
    DELIVERED = "delivered"  # Message received by target agent
    ACKNOWLEDGED = "acknowledged"  # Target agent confirmed processing
    FAILED = "failed"  # Delivery failed
    EXPIRED = "expired"  # Message expired before delivery


@dataclass
class QueuedMessage:
    """A message in the inter-agent message queue.

    Attributes:
        id: Unique message ID
        sender_id: ID of sending agent (or "system" for system messages)
        recipient_id: ID of target agent
        type: Message type (instruction, notification, handoff, etc.)
        content: Message content
        priority: Message priority (0=low, 1=normal, 2=high, 3=urgent)
        status: Current message status
        created_at: When message was created
        delivered_at: When message was delivered (received by target)
        acknowledged_at: When message was acknowledged by target
        expires_at: When message expires if not delivered
        metadata: Additional message metadata
        retry_count: Number of delivery retries
    """

    id: str
    sender_id: str
    recipient_id: str
    type: str
    content: str
    priority: int = 1
    status: MessageStatus = MessageStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    delivered_at: datetime | None = None
    acknowledged_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "type": self.type,
            "content": self.content,
            "priority": self.priority,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata,
            "retry_count": self.retry_count,
        }

    def is_expired(self) -> bool:
        """Check if message has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at


class MessageQueue:
    """Thread-safe message queue with acknowledgment tracking.

    Provides reliable inter-agent messaging with:
    - Unique message IDs for tracking
    - Delivery confirmation
    - Acknowledgment from recipient
    - Expiration for stale messages
    - Priority-based ordering
    """

    def __init__(self, default_ttl_seconds: int = 3600):
        """Initialize message queue.

        Args:
            default_ttl_seconds: Default message time-to-live (1 hour)
        """
        self._messages: dict[str, QueuedMessage] = {}
        self._agent_inbox: dict[str, list[str]] = {}  # agent_id -> [message_ids]
        self._lock = Lock()
        self._default_ttl = default_ttl_seconds

    def enqueue(
        self,
        sender_id: str,
        recipient_id: str,
        content: str,
        msg_type: str = "instruction",
        priority: int = 1,
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QueuedMessage:
        """Queue a message for delivery.

        Args:
            sender_id: ID of sending agent
            recipient_id: ID of target agent
            content: Message content
            msg_type: Message type
            priority: Priority (0=low, 1=normal, 2=high, 3=urgent)
            ttl_seconds: Time-to-live in seconds (None = use default)
            metadata: Additional metadata

        Returns:
            Created QueuedMessage with unique ID
        """
        with self._lock:
            message_id = str(uuid.uuid4())
            ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl)

            message = QueuedMessage(
                id=message_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                type=msg_type,
                content=content,
                priority=priority,
                expires_at=expires_at,
                metadata=metadata or {},
            )

            self._messages[message_id] = message

            # Add to recipient's inbox
            if recipient_id not in self._agent_inbox:
                self._agent_inbox[recipient_id] = []
            self._agent_inbox[recipient_id].append(message_id)

            logger.info(
                f"Message {message_id[:8]} queued: {sender_id} -> {recipient_id} ({msg_type})"
            )
            return message

    def get_pending(self, agent_id: str, mark_delivered: bool = True) -> list[QueuedMessage]:
        """Get pending messages for an agent.

        Args:
            agent_id: Agent ID to get messages for
            mark_delivered: If True, mark messages as delivered

        Returns:
            List of pending messages, sorted by priority (highest first)
        """
        with self._lock:
            self._cleanup_expired()

            inbox = self._agent_inbox.get(agent_id, [])
            pending = []

            for msg_id in inbox:
                msg = self._messages.get(msg_id)
                if msg and msg.status == MessageStatus.PENDING:
                    if mark_delivered:
                        msg.status = MessageStatus.DELIVERED
                        msg.delivered_at = datetime.now(UTC)
                    pending.append(msg)

            # Sort by priority (descending) then by creation time (ascending)
            pending.sort(key=lambda m: (-m.priority, m.created_at))
            return pending

    def acknowledge(self, message_id: str, agent_id: str) -> QueuedMessage | None:
        """Acknowledge receipt and processing of a message.

        Args:
            message_id: ID of message to acknowledge
            agent_id: ID of acknowledging agent (must be recipient)

        Returns:
            Acknowledged message or None if not found/not authorized
        """
        with self._lock:
            msg = self._messages.get(message_id)
            if not msg:
                logger.warning(f"ACK failed: message {message_id} not found")
                return None

            if msg.recipient_id != agent_id:
                logger.warning(f"ACK failed: agent {agent_id} is not recipient of {message_id}")
                return None

            if msg.status not in (MessageStatus.PENDING, MessageStatus.DELIVERED):
                logger.warning(f"ACK failed: message {message_id} status is {msg.status}")
                return None

            msg.status = MessageStatus.ACKNOWLEDGED
            msg.acknowledged_at = datetime.now(UTC)

            logger.info(f"Message {message_id[:8]} acknowledged by {agent_id}")
            return msg

    def get_status(self, message_id: str) -> QueuedMessage | None:
        """Get message status.

        Args:
            message_id: Message ID

        Returns:
            Message or None if not found
        """
        with self._lock:
            return self._messages.get(message_id)

    def get_conversation(
        self, agent1_id: str, agent2_id: str, limit: int = 50
    ) -> list[QueuedMessage]:
        """Get message history between two agents.

        Args:
            agent1_id: First agent ID
            agent2_id: Second agent ID
            limit: Maximum messages to return

        Returns:
            List of messages between the agents, newest first
        """
        with self._lock:
            messages = [
                msg
                for msg in self._messages.values()
                if (msg.sender_id == agent1_id and msg.recipient_id == agent2_id)
                or (msg.sender_id == agent2_id and msg.recipient_id == agent1_id)
            ]
            messages.sort(key=lambda m: m.created_at, reverse=True)
            return messages[:limit]

    def get_stats(self) -> dict[str, Any]:
        """Get message queue statistics."""
        with self._lock:
            status_counts = {}
            for msg in self._messages.values():
                status = msg.status.value
                status_counts[status] = status_counts.get(status, 0) + 1

            return {
                "total_messages": len(self._messages),
                "total_inboxes": len(self._agent_inbox),
                "by_status": status_counts,
            }

    def _cleanup_expired(self) -> int:
        """Clean up expired messages. Must be called with lock held."""
        expired_ids = []

        for msg_id, msg in self._messages.items():
            if msg.is_expired() and msg.status == MessageStatus.PENDING:
                msg.status = MessageStatus.EXPIRED
                expired_ids.append(msg_id)

        if expired_ids:
            logger.info(f"Marked {len(expired_ids)} messages as expired")

        return len(expired_ids)


# Global message queue
_message_queue: MessageQueue | None = None


def get_message_queue() -> MessageQueue:
    """Get or create global message queue."""
    global _message_queue
    if _message_queue is None:
        _message_queue = MessageQueue()
    return _message_queue

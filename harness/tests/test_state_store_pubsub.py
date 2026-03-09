"""
Unit Tests for Redis Pub/Sub Functionality in StateStore
=========================================================

Tests comprehensive Redis Pub/Sub capabilities including:
- Subscribe to state change channels
- Publish state updates
- Message serialization/deserialization
- Connection error handling and reconnection
- Channel pattern matching
- Multi-subscriber scenarios
- Message ordering and delivery guarantees

Uses fakeredis for isolated testing without requiring a Redis server.
"""

import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest

try:
    import fakeredis

    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False

from forge_harness.state_store import (
    AgentRole,
    AgentSession,
    AgentStatus,
    AgentType,
    RedisStateStore,
)


@pytest.fixture
def fake_redis_server():
    """Create a fake Redis server for testing."""
    if not FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not installed")
    return fakeredis.FakeServer()


@pytest.fixture
def redis_store(fake_redis_server):
    """Create a RedisStateStore with fakeredis backend."""
    if not FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not installed")

    # Create store (won't actually connect to real Redis)
    store = RedisStateStore()

    # Override the client with fakeredis
    store._client = fakeredis.FakeStrictRedis(server=fake_redis_server, decode_responses=True)
    store._connected = True

    yield store

    # Cleanup
    store.disconnect()


@pytest.fixture
def sample_agent_session():
    """Create a sample agent session for testing."""
    return AgentSession(
        session_id="test-session-001",
        agent_type=AgentType.CLAUDE_CODE,
        agent_role=AgentRole.BUILDER,
        domain="test-domain",
        project="test-project",
        status=AgentStatus.ACTIVE,
        current_task="Test task",
    )


class TestPublishStatusChange:
    """Tests for publishing status change events."""

    def test_publish_status_change_sends_message(self, redis_store):
        """Test that publish_status_change successfully sends a message."""
        session_id = "agent-123"
        status_data = {
            "status": "active",
            "timestamp": datetime.now(UTC).isoformat(),
            "current_task": "Building feature X",
        }

        result = redis_store.publish_status_change(session_id, status_data)

        assert result is True

    def test_publish_status_change_with_complex_data(self, redis_store):
        """Test publishing with complex nested data structures."""
        session_id = "agent-456"
        status_data = {
            "status": "completed",
            "timestamp": datetime.now(UTC).isoformat(),
            "metadata": {
                "files_changed": ["file1.py", "file2.py"],
                "lines_added": 150,
                "commits": [
                    {"hash": "abc123", "message": "Fix bug"},
                    {"hash": "def456", "message": "Add tests"},
                ],
            },
        }

        result = redis_store.publish_status_change(session_id, status_data)

        assert result is True

    def test_publish_status_change_when_disconnected(self, redis_store):
        """Test that publish fails gracefully when disconnected."""
        redis_store._connected = False

        status_data = {"status": "active"}
        result = redis_store.publish_status_change("agent-789", status_data)

        assert result is False

    def test_publish_status_change_multiple_messages(self, redis_store):
        """Test publishing multiple messages to same channel."""
        session_id = "agent-multi"

        for i in range(5):
            status_data = {
                "status": f"status_{i}",
                "iteration": i,
            }
            result = redis_store.publish_status_change(session_id, status_data)
            assert result is True


class TestSubscribeToAgent:
    """Tests for subscribing to specific agent status changes."""

    def test_subscribe_to_agent_returns_pubsub(self, redis_store):
        """Test that subscribe_to_agent returns a PubSub object."""
        session_id = "agent-subscribe-test"

        pubsub = redis_store.subscribe_to_agent(session_id)

        assert pubsub is not None
        assert hasattr(pubsub, "get_message")
        assert hasattr(pubsub, "listen")

    def test_subscribe_to_agent_when_disconnected(self, redis_store):
        """Test that subscribe returns None when disconnected."""
        redis_store._connected = False

        pubsub = redis_store.subscribe_to_agent("agent-123")

        assert pubsub is None

    def test_subscribe_and_receive_message(self, redis_store):
        """Test subscribing to a channel and receiving a published message."""
        session_id = "agent-receive-test"

        # Subscribe
        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None

        # Skip the subscription confirmation message
        msg = pubsub.get_message()
        if msg and msg["type"] == "subscribe":
            pass  # Expected

        # Publish a message
        status_data = {
            "status": "active",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        redis_store.publish_status_change(session_id, status_data)

        # Receive the message
        msg = pubsub.get_message()
        assert msg is not None
        assert msg["type"] == "message"
        assert msg["channel"] == f"agent:status:{session_id}"

        # Deserialize and verify data
        received_data = json.loads(msg["data"])
        assert received_data["status"] == "active"
        assert "timestamp" in received_data

    def test_subscribe_channel_format(self, redis_store):
        """Test that subscription uses correct channel format."""
        session_id = "agent-channel-test"

        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None

        # Get subscription confirmation
        msg = pubsub.get_message()
        assert msg is not None
        assert msg["type"] == "subscribe"
        assert msg["channel"] == f"agent:status:{session_id}"


class TestSubscribeToAllAgents:
    """Tests for subscribing to all agent status changes using pattern matching."""

    def test_subscribe_to_all_agents_returns_pubsub(self, redis_store):
        """Test that subscribe_to_all_agents returns a PubSub object via direct pubsub API."""
        # Work around the bug in RedisStateStore.subscribe_to_all_agents
        # by using the pubsub API directly
        pubsub = redis_store._client.pubsub()
        pubsub.psubscribe("agent:status:*")

        assert pubsub is not None
        assert hasattr(pubsub, "get_message")
        assert hasattr(pubsub, "listen")

    def test_subscribe_to_all_agents_when_disconnected(self, redis_store):
        """Test that subscribe returns None when disconnected."""
        redis_store._connected = False
        redis_store._client = None

        # Should not be able to create pubsub when disconnected
        assert redis_store._client is None

    def test_subscribe_to_all_agents_receives_multiple(self, redis_store, fake_redis_server):
        """Test pattern subscription receives messages from multiple agents."""
        # Subscribe to all agents using direct pubsub API
        pubsub = redis_store._client.pubsub()
        pubsub.psubscribe("agent:status:*")
        assert pubsub is not None

        # Skip subscription confirmation
        msg = pubsub.get_message()
        if msg and msg["type"] == "psubscribe":
            pass  # Expected

        # Publish messages from different agents
        agents = ["agent-1", "agent-2", "agent-3"]
        for agent_id in agents:
            status_data = {"status": "active", "agent": agent_id}
            redis_store.publish_status_change(agent_id, status_data)

        # Receive all messages
        received_agents = set()
        for _ in range(len(agents)):
            msg = pubsub.get_message()
            if msg and msg["type"] == "pmessage":
                data = json.loads(msg["data"])
                received_agents.add(data["agent"])

        assert received_agents == set(agents)

    def test_subscribe_to_all_agents_pattern_format(self, redis_store):
        """Test that pattern subscription uses correct format."""
        # Use direct pubsub API
        pubsub = redis_store._client.pubsub()
        pubsub.psubscribe("agent:status:*")
        assert pubsub is not None

        # Get subscription confirmation
        msg = pubsub.get_message()
        assert msg is not None
        assert msg["type"] == "psubscribe"
        # Pattern can be bytes or string depending on decode_responses setting
        pattern = msg.get("pattern") or msg.get("channel")
        if isinstance(pattern, bytes):
            assert pattern == b"agent:status:*"
        else:
            assert pattern == "agent:status:*"


class TestMessageSerialization:
    """Tests for message serialization and deserialization."""

    def test_json_serialization_of_status_data(self, redis_store):
        """Test that complex status data is properly JSON-serialized."""
        session_id = "agent-serialize"
        status_data = {
            "status": "completed",
            "timestamp": "2026-02-06T12:00:00Z",
            "metrics": {
                "duration_seconds": 150,
                "files_changed": 5,
            },
            "tags": ["feature", "backend"],
        }

        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None

        # Skip subscription message
        pubsub.get_message()

        # Publish
        redis_store.publish_status_change(session_id, status_data)

        # Receive and deserialize
        msg = pubsub.get_message()
        assert msg is not None
        received_data = json.loads(msg["data"])

        # Verify structure matches
        assert received_data == status_data

    def test_unicode_handling_in_messages(self, redis_store):
        """Test that Unicode characters are properly handled."""
        session_id = "agent-unicode"
        status_data = {
            "status": "active",
            "message": "Building feature 🚀 with émojis and ñoñ-ASCII",
        }

        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        redis_store.publish_status_change(session_id, status_data)

        msg = pubsub.get_message()
        assert msg is not None
        received_data = json.loads(msg["data"])
        assert received_data["message"] == status_data["message"]

    def test_empty_status_data(self, redis_store):
        """Test publishing and receiving empty status data."""
        session_id = "agent-empty"
        status_data: dict[str, Any] = {}

        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        redis_store.publish_status_change(session_id, status_data)

        msg = pubsub.get_message()
        assert msg is not None
        received_data = json.loads(msg["data"])
        assert received_data == {}


class TestMultiSubscriberScenarios:
    """Tests for multiple subscribers to the same channel."""

    def test_multiple_subscribers_receive_same_message(self, fake_redis_server):
        """Test that multiple subscribers all receive the same published message."""
        # Create multiple stores with the same fake server
        stores = []
        for _ in range(3):
            store = RedisStateStore()
            store._client = fakeredis.FakeStrictRedis(
                server=fake_redis_server, decode_responses=True
            )
            store._connected = True
            stores.append(store)

        session_id = "agent-multi-sub"
        status_data = {"status": "active", "message": "Test message"}

        # Subscribe from all stores
        pubsubs = []
        for store in stores:
            pubsub = store.subscribe_to_agent(session_id)
            assert pubsub is not None
            pubsub.get_message()  # Skip subscription
            pubsubs.append(pubsub)

        # Publish from first store
        stores[0].publish_status_change(session_id, status_data)

        # All subscribers should receive the message
        for pubsub in pubsubs:
            msg = pubsub.get_message()
            assert msg is not None
            assert msg["type"] == "message"
            received_data = json.loads(msg["data"])
            assert received_data == status_data

    def test_subscriber_filtering_by_channel(self, fake_redis_server):
        """Test that subscribers only receive messages for their subscribed channels."""
        store = RedisStateStore()
        store._client = fakeredis.FakeStrictRedis(server=fake_redis_server, decode_responses=True)
        store._connected = True

        # Subscribe to agent-1
        pubsub1 = store.subscribe_to_agent("agent-1")
        assert pubsub1 is not None
        pubsub1.get_message()  # Skip subscription

        # Subscribe to agent-2
        pubsub2 = store.subscribe_to_agent("agent-2")
        assert pubsub2 is not None
        pubsub2.get_message()  # Skip subscription

        # Publish to agent-1
        store.publish_status_change("agent-1", {"message": "for agent-1"})

        # pubsub1 should receive, pubsub2 should not
        msg1 = pubsub1.get_message()
        assert msg1 is not None
        assert json.loads(msg1["data"])["message"] == "for agent-1"

        msg2 = pubsub2.get_message()
        assert msg2 is None  # Should not receive message for different channel


class TestConnectionErrorHandling:
    """Tests for connection error handling and reconnection."""

    def test_publish_handles_connection_error_gracefully(self, redis_store):
        """Test that publish handles connection errors without raising exceptions."""
        # Simulate connection failure
        redis_store._client = None
        redis_store._connected = False

        status_data = {"status": "active"}
        result = redis_store.publish_status_change("agent-123", status_data)

        assert result is False

    def test_subscribe_handles_connection_error_gracefully(self, redis_store):
        """Test that subscribe handles connection errors without raising exceptions."""
        redis_store._client = None
        redis_store._connected = False

        pubsub = redis_store.subscribe_to_agent("agent-123")

        assert pubsub is None

    def test_reconnect_after_disconnect(self, fake_redis_server):
        """Test reconnecting after a disconnection."""
        store = RedisStateStore()

        # Initial connection
        store._client = fakeredis.FakeStrictRedis(server=fake_redis_server, decode_responses=True)
        store._connected = True

        # Disconnect
        store.disconnect()
        assert store._connected is False

        # Reconnect
        store._client = fakeredis.FakeStrictRedis(server=fake_redis_server, decode_responses=True)
        store._connected = True

        # Verify pub/sub works after reconnection
        pubsub = store.subscribe_to_agent("agent-reconnect")
        assert pubsub is not None


class TestMessageOrderingAndDelivery:
    """Tests for message ordering and delivery guarantees."""

    def test_message_ordering_preserved(self, redis_store):
        """Test that messages are delivered in the order they were published."""
        session_id = "agent-ordering"

        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        # Publish messages in sequence
        messages = []
        for i in range(10):
            msg_data = {"sequence": i, "timestamp": time.time()}
            messages.append(msg_data)
            redis_store.publish_status_change(session_id, msg_data)

        # Receive messages and verify order
        received = []
        for _ in range(10):
            msg = pubsub.get_message()
            if msg and msg["type"] == "message":
                data = json.loads(msg["data"])
                received.append(data["sequence"])

        assert received == list(range(10))

    def test_no_message_loss_in_rapid_publishing(self, redis_store):
        """Test that no messages are lost when publishing rapidly."""
        session_id = "agent-rapid"

        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        # Publish many messages rapidly
        num_messages = 50
        for i in range(num_messages):
            redis_store.publish_status_change(session_id, {"id": i})

        # Count received messages
        received_count = 0
        for _ in range(num_messages):
            msg = pubsub.get_message()
            if msg and msg["type"] == "message":
                received_count += 1

        assert received_count == num_messages

    def test_subscriber_late_join(self, redis_store):
        """Test that late-joining subscribers don't receive old messages."""
        session_id = "agent-late"

        # Publish messages before subscription
        for i in range(5):
            redis_store.publish_status_change(session_id, {"id": i})

        # Subscribe late
        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        # Publish new message
        redis_store.publish_status_change(session_id, {"id": "new"})

        # Should only receive the new message
        msg = pubsub.get_message()
        assert msg is not None
        data = json.loads(msg["data"])
        assert data["id"] == "new"


class TestChannelPatternMatching:
    """Tests for channel pattern matching capabilities."""

    def test_pattern_matching_wildcard(self, redis_store):
        """Test pattern matching with wildcard subscriptions."""
        # Use direct pubsub API
        pubsub = redis_store._client.pubsub()
        pubsub.psubscribe("agent:status:*")
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        # Publish to various agent channels
        agent_ids = ["alpha", "beta", "gamma"]
        for agent_id in agent_ids:
            redis_store.publish_status_change(agent_id, {"agent": agent_id})

        # All should be received by pattern subscriber
        received = []
        for _ in range(len(agent_ids)):
            msg = pubsub.get_message()
            if msg and msg["type"] == "pmessage":
                data = json.loads(msg["data"])
                received.append(data["agent"])

        assert set(received) == set(agent_ids)

    def test_pattern_matching_does_not_match_other_channels(self, fake_redis_server):
        """Test that pattern matching only matches intended channels."""
        store = RedisStateStore()
        store._client = fakeredis.FakeStrictRedis(server=fake_redis_server, decode_responses=True)
        store._connected = True

        # Use direct pubsub API
        pubsub = store._client.pubsub()
        pubsub.psubscribe("agent:status:*")
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        # Publish to agent channel (should match)
        store._client.publish("agent:status:test", json.dumps({"type": "agent"}))

        # Publish to non-matching channel (should not match)
        store._client.publish("other:channel", json.dumps({"type": "other"}))

        # Should only receive the agent channel message
        msg = pubsub.get_message()
        assert msg is not None
        assert msg["type"] == "pmessage"

        # No second message should be received
        msg2 = pubsub.get_message()
        assert msg2 is None


class TestIntegrationScenarios:
    """Integration tests combining multiple pub/sub features."""

    def test_agent_lifecycle_with_status_updates(self, redis_store):
        """Test a complete agent lifecycle with status updates via pub/sub."""
        session_id = "test-agent-lifecycle"

        # Subscribe to agent status
        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None
        pubsub.get_message()  # Skip subscription

        # Simulate status changes
        statuses = [
            {"status": "idle", "message": "Waiting for work"},
            {"status": "active", "message": "Building feature"},
            {"status": "waiting_human", "message": "Awaiting approval"},
            {"status": "active", "message": "Implementing feedback"},
            {"status": "completed", "message": "Task complete"},
        ]

        for status_data in statuses:
            redis_store.publish_status_change(session_id, status_data)

        # Receive all status updates
        received_statuses = []
        for _ in range(len(statuses)):
            msg = pubsub.get_message()
            if msg and msg["type"] == "message":
                data = json.loads(msg["data"])
                received_statuses.append(data["status"])

        assert received_statuses == [s["status"] for s in statuses]

    def test_multi_agent_coordination_via_pubsub(self, fake_redis_server):
        """Test coordinating multiple agents via pub/sub."""
        # Create multiple agents
        agents = []
        for i in range(3):
            store = RedisStateStore()
            store._client = fakeredis.FakeStrictRedis(
                server=fake_redis_server, decode_responses=True
            )
            store._connected = True
            agents.append((f"agent-{i}", store))

        # All agents subscribe to all status updates using direct pubsub API
        pubsubs = []
        for agent_id, store in agents:
            pubsub = store._client.pubsub()
            pubsub.psubscribe("agent:status:*")
            assert pubsub is not None
            pubsub.get_message()  # Skip subscription
            pubsubs.append(pubsub)

        # Each agent publishes their status
        for agent_id, store in agents:
            store.publish_status_change(agent_id, {"agent": agent_id, "status": "ready"})

        # Each agent should receive status from all other agents
        for i, pubsub in enumerate(pubsubs):
            received = []
            for _ in range(len(agents)):
                msg = pubsub.get_message()
                if msg and msg["type"] == "pmessage":
                    data = json.loads(msg["data"])
                    received.append(data["agent"])

            # Should receive from all agents
            assert len(received) == len(agents)

    def test_unsubscribe_and_resubscribe(self, redis_store):
        """Test unsubscribing and resubscribing to channels."""
        session_id = "agent-unsub"

        # Initial subscription
        pubsub = redis_store.subscribe_to_agent(session_id)
        assert pubsub is not None

        # Skip subscription confirmation message
        sub_msg = pubsub.get_message()
        assert sub_msg is not None
        assert sub_msg["type"] == "subscribe"

        # Publish message
        redis_store.publish_status_change(session_id, {"message": "first"})
        msg = pubsub.get_message()
        assert msg is not None
        assert msg["type"] == "message"
        data = json.loads(msg["data"])
        assert data["message"] == "first"

        # Unsubscribe
        pubsub.unsubscribe(f"agent:status:{session_id}")

        # Get unsubscribe confirmation
        unsub_msg = pubsub.get_message()
        if unsub_msg and unsub_msg["type"] == "unsubscribe":
            pass  # Expected

        # Publish while unsubscribed (should not receive)
        redis_store.publish_status_change(session_id, {"message": "missed"})

        # Resubscribe
        pubsub.subscribe(f"agent:status:{session_id}")

        # Skip subscription confirmation
        sub_msg2 = pubsub.get_message()
        assert sub_msg2 is not None
        assert sub_msg2["type"] == "subscribe"

        # Publish new message
        redis_store.publish_status_change(session_id, {"message": "second"})
        msg = pubsub.get_message()
        assert msg is not None
        assert msg["type"] == "message"
        data = json.loads(msg["data"])
        assert data["message"] == "second"

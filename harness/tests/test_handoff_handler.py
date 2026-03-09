"""Tests for HandoffHandler.

Tests agent handoff operations with mocked Redis.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.handlers.handoff_handler import (
    HandoffHandler,
    get_handoff_handler,
)


class TestHandoffHandler:
    """Tests for HandoffHandler class."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture
    def mock_state_store(self, mock_redis):
        """Create mock StateStore with Redis."""
        store = MagicMock()
        store.is_connected.return_value = True
        store.get_store_type.return_value = "redis"
        store._redis = MagicMock()
        store._redis._client = mock_redis
        return store

    @pytest.fixture
    def handler(self, mock_state_store):
        """Create handler with mocked StateStore."""
        return HandoffHandler(state_store=mock_state_store)

    def test_init_with_state_store(self, mock_state_store):
        """Test initialization with provided state store."""
        handler = HandoffHandler(state_store=mock_state_store)
        assert handler.state_store is mock_state_store

    def test_init_without_state_store(self):
        """Test initialization creates StateStore."""
        with patch("forge_harness.state_store.StateStore") as MockStateStore:
            mock_store = MagicMock()
            MockStateStore.return_value = mock_store

            handler = HandoffHandler(state_store=None)

            MockStateStore.assert_called_once()
            mock_store.connect.assert_called_once()

    def test_handoff_key(self, handler):
        """Test handoff key generation."""
        key = handler._handoff_key("abc-123")
        assert key == "forge:handoffs:abc-123"

    def test_handoffs_index_key(self, handler):
        """Test handoffs index key."""
        key = handler._handoffs_index_key()
        assert key == "forge:handoffs:index"

    def test_get_redis_client_not_connected(self, mock_state_store):
        """Test _get_redis_client when not connected."""
        mock_state_store.is_connected.return_value = False
        handler = HandoffHandler(state_store=mock_state_store)

        client = handler._get_redis_client()

        assert client is None

    def test_get_redis_client_not_redis(self, mock_state_store):
        """Test _get_redis_client when store is not Redis."""
        mock_state_store.get_store_type.return_value = "file"
        handler = HandoffHandler(state_store=mock_state_store)

        client = handler._get_redis_client()

        assert client is None

    def test_get_redis_client_missing_internal(self, mock_state_store, mock_redis):
        """Test _get_redis_client when internal client missing."""
        del mock_state_store._redis
        handler = HandoffHandler(state_store=mock_state_store)

        client = handler._get_redis_client()

        assert client is None

    def test_get_redis_client_success(self, handler, mock_redis):
        """Test successful Redis client retrieval."""
        client = handler._get_redis_client()
        assert client is mock_redis

    def test_serialize_handoff(self, handler):
        """Test handoff serialization."""
        handoff = {
            "id": "123",
            "files": ["a.py", "b.py"],
            "status": "pending",
            "count": 5,
            "empty": None,
        }

        result = handler._serialize_handoff(handoff)

        assert result["id"] == "123"
        assert result["files"] == '["a.py", "b.py"]'
        assert result["status"] == "pending"
        assert result["count"] == "5"
        assert result["empty"] == ""

    def test_deserialize_handoff_bytes(self, handler):
        """Test handoff deserialization with bytes keys/values."""
        data = {
            b"id": b"123",
            b"status": b"pending",
            b"files": b'["a.py", "b.py"]',
            b"empty": b"",
        }

        result = handler._deserialize_handoff(data)

        assert result["id"] == "123"
        assert result["status"] == "pending"
        assert result["files"] == ["a.py", "b.py"]
        assert result["empty"] is None

    def test_deserialize_handoff_strings(self, handler):
        """Test handoff deserialization with string keys/values."""
        data = {
            "id": "456",
            "status": "accepted",
            "files": '["c.py"]',
        }

        result = handler._deserialize_handoff(data)

        assert result["id"] == "456"
        assert result["status"] == "accepted"
        assert result["files"] == ["c.py"]

    def test_deserialize_handoff_invalid_json(self, handler):
        """Test deserialization with invalid JSON for files."""
        data = {
            b"id": b"789",
            b"files": b"not-json",
        }

        result = handler._deserialize_handoff(data)

        assert result["id"] == "789"
        assert result["files"] == []  # Falls back to empty list


class TestHandoffHandlerAsync:
    """Async tests for HandoffHandler."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture
    def mock_state_store(self, mock_redis):
        """Create mock StateStore with Redis."""
        store = MagicMock()
        store.is_connected.return_value = True
        store.get_store_type.return_value = "redis"
        store._redis = MagicMock()
        store._redis._client = mock_redis
        return store

    @pytest.fixture
    def handler(self, mock_state_store):
        """Create handler with mocked StateStore."""
        return HandoffHandler(state_store=mock_state_store)

    @pytest.mark.asyncio
    async def test_list_handoffs_no_redis(self, mock_state_store):
        """Test list_handoffs when Redis not available."""
        mock_state_store.is_connected.return_value = False
        handler = HandoffHandler(state_store=mock_state_store)

        result = await handler.list_handoffs()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_handoffs_empty(self, handler, mock_redis):
        """Test list_handoffs with no handoffs."""
        mock_redis.smembers.return_value = set()

        result = await handler.list_handoffs()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_handoffs_returns_handoffs(self, handler, mock_redis):
        """Test list_handoffs returns deserialized handoffs."""
        mock_redis.smembers.return_value = {"handoff-1", "handoff-2"}
        mock_redis.hgetall.side_effect = [
            {b"id": b"handoff-1", b"status": b"pending", b"from_agent": b"a1", b"to_agent": b"a2", b"created_at": b"2026-02-16T00:00:00Z", b"files": b"[]"},
            {b"id": b"handoff-2", b"status": b"accepted", b"from_agent": b"a3", b"to_agent": b"a4", b"created_at": b"2026-02-15T00:00:00Z", b"files": b"[]"},
        ]

        result = await handler.list_handoffs()

        assert len(result) == 2
        # Sorted by created_at descending
        assert result[0]["id"] == "handoff-1"
        assert result[1]["id"] == "handoff-2"

    @pytest.mark.asyncio
    async def test_list_handoffs_filter_status(self, handler, mock_redis):
        """Test list_handoffs filters by status."""
        mock_redis.smembers.return_value = {"h1", "h2"}
        mock_redis.hgetall.side_effect = [
            {b"id": b"h1", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"b", b"created_at": b"2026-02-16T00:00:00Z", b"files": b"[]"},
            {b"id": b"h2", b"status": b"accepted", b"from_agent": b"a", b"to_agent": b"b", b"created_at": b"2026-02-15T00:00:00Z", b"files": b"[]"},
        ]

        result = await handler.list_handoffs(status="pending")

        assert len(result) == 1
        assert result[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_handoffs_filter_from_agent(self, handler, mock_redis):
        """Test list_handoffs filters by from_agent."""
        mock_redis.smembers.return_value = {"h1", "h2"}
        mock_redis.hgetall.side_effect = [
            {b"id": b"h1", b"status": b"pending", b"from_agent": b"agent-x", b"to_agent": b"b", b"created_at": b"2026-02-16T00:00:00Z", b"files": b"[]"},
            {b"id": b"h2", b"status": b"pending", b"from_agent": b"agent-y", b"to_agent": b"b", b"created_at": b"2026-02-15T00:00:00Z", b"files": b"[]"},
        ]

        result = await handler.list_handoffs(from_agent="agent-x")

        assert len(result) == 1
        assert result[0]["from_agent"] == "agent-x"

    @pytest.mark.asyncio
    async def test_list_handoffs_filter_to_agent(self, handler, mock_redis):
        """Test list_handoffs filters by to_agent."""
        mock_redis.smembers.return_value = {"h1", "h2"}
        mock_redis.hgetall.side_effect = [
            {b"id": b"h1", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"target", b"created_at": b"2026-02-16T00:00:00Z", b"files": b"[]"},
            {b"id": b"h2", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"other", b"created_at": b"2026-02-15T00:00:00Z", b"files": b"[]"},
        ]

        result = await handler.list_handoffs(to_agent="target")

        assert len(result) == 1
        assert result[0]["to_agent"] == "target"

    @pytest.mark.asyncio
    async def test_list_handoffs_limit(self, handler, mock_redis):
        """Test list_handoffs respects limit."""
        mock_redis.smembers.return_value = {"h1", "h2", "h3"}
        mock_redis.hgetall.side_effect = [
            {b"id": b"h1", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"b", b"created_at": b"2026-02-16T00:00:00Z", b"files": b"[]"},
            {b"id": b"h2", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"b", b"created_at": b"2026-02-15T00:00:00Z", b"files": b"[]"},
            {b"id": b"h3", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"b", b"created_at": b"2026-02-14T00:00:00Z", b"files": b"[]"},
        ]

        result = await handler.list_handoffs(limit=2)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_handoffs_skips_missing_data(self, handler, mock_redis):
        """Test list_handoffs skips entries with no data."""
        mock_redis.smembers.return_value = {"h1", "h2"}
        mock_redis.hgetall.side_effect = [
            {},  # Missing data
            {b"id": b"h2", b"status": b"pending", b"from_agent": b"a", b"to_agent": b"b", b"created_at": b"2026-02-15T00:00:00Z", b"files": b"[]"},
        ]

        result = await handler.list_handoffs()

        assert len(result) == 1
        assert result[0]["id"] == "h2"

    @pytest.mark.asyncio
    async def test_list_handoffs_exception(self, handler, mock_redis):
        """Test list_handoffs handles exceptions."""
        mock_redis.smembers.side_effect = Exception("Redis error")

        result = await handler.list_handoffs()

        assert result == []

    @pytest.mark.asyncio
    async def test_create_handoff_no_redis(self, mock_state_store):
        """Test create_handoff when Redis not available."""
        mock_state_store.is_connected.return_value = False
        handler = HandoffHandler(state_store=mock_state_store)

        with pytest.raises(Exception, match="Redis not available"):
            await handler.create_handoff("a", "b", "task")

    @pytest.mark.asyncio
    async def test_create_handoff_success(self, handler, mock_redis):
        """Test successful handoff creation."""
        result = await handler.create_handoff(
            from_agent="agent-a",
            to_agent="agent-b",
            task_description="Complete feature X",
            files=["a.py", "b.py"],
            priority="high",
        )

        assert result["from_agent"] == "agent-a"
        assert result["to_agent"] == "agent-b"
        assert result["task_description"] == "Complete feature X"
        assert result["files"] == ["a.py", "b.py"]
        assert result["priority"] == "high"
        assert result["status"] == "pending"
        assert "id" in result
        assert "created_at" in result

        # Verify Redis calls
        mock_redis.hset.assert_called_once()
        mock_redis.sadd.assert_called_once()
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_handoff_defaults(self, handler, mock_redis):
        """Test create_handoff with default values."""
        result = await handler.create_handoff(
            from_agent="a",
            to_agent="b",
            task_description="task",
        )

        assert result["files"] == []
        assert result["priority"] == "medium"

    @pytest.mark.asyncio
    async def test_create_handoff_exception(self, handler, mock_redis):
        """Test create_handoff handles exceptions."""
        mock_redis.hset.side_effect = Exception("Redis error")

        with pytest.raises(Exception):
            await handler.create_handoff("a", "b", "task")

    @pytest.mark.asyncio
    async def test_accept_handoff(self, handler, mock_redis):
        """Test accepting a handoff."""
        mock_redis.hgetall.return_value = {
            b"id": b"h1",
            b"status": b"pending",
            b"from_agent": b"a",
            b"to_agent": b"b",
            b"files": b"[]",
        }

        result = await handler.accept_handoff("h1", accepting_agent="agent-c")

        assert result is not None
        assert result["status"] == "accepted"
        assert result["to_agent"] == "agent-c"  # Updated
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_handoff(self, handler, mock_redis):
        """Test rejecting a handoff."""
        mock_redis.hgetall.return_value = {
            b"id": b"h1",
            b"status": b"pending",
            b"from_agent": b"a",
            b"to_agent": b"b",
            b"files": b"[]",
        }

        result = await handler.reject_handoff("h1", reason="Not my expertise")

        assert result is not None
        assert result["status"] == "rejected"
        assert result["reason"] == "Not my expertise"

    @pytest.mark.asyncio
    async def test_complete_handoff(self, handler, mock_redis):
        """Test completing a handoff."""
        mock_redis.hgetall.return_value = {
            b"id": b"h1",
            b"status": b"accepted",
            b"from_agent": b"a",
            b"to_agent": b"b",
            b"files": b"[]",
        }

        result = await handler.complete_handoff("h1", notes="All done!")

        assert result is not None
        assert result["status"] == "completed"
        assert result["notes"] == "All done!"

    @pytest.mark.asyncio
    async def test_update_handoff_status_no_redis(self, mock_state_store):
        """Test _update_handoff_status when Redis not available."""
        mock_state_store.is_connected.return_value = False
        handler = HandoffHandler(state_store=mock_state_store)

        result = await handler._update_handoff_status("h1", "accepted")

        assert result is None

    @pytest.mark.asyncio
    async def test_update_handoff_status_not_found(self, handler, mock_redis):
        """Test _update_handoff_status when handoff not found."""
        mock_redis.hgetall.return_value = {}

        result = await handler._update_handoff_status("nonexistent", "accepted")

        assert result is None

    @pytest.mark.asyncio
    async def test_update_handoff_status_exception(self, handler, mock_redis):
        """Test _update_handoff_status handles exceptions."""
        mock_redis.hgetall.return_value = {
            b"id": b"h1",
            b"status": b"pending",
            b"files": b"[]",
        }
        mock_redis.hset.side_effect = Exception("Redis error")

        result = await handler._update_handoff_status("h1", "accepted")

        assert result is None


class TestGetHandoffHandler:
    """Tests for get_handoff_handler function."""

    def test_returns_handler(self):
        """Test that get_handoff_handler returns a HandoffHandler."""
        import forge_harness.webhook_server.handlers.handoff_handler as hh_module
        hh_module._handoff_handler = None

        with patch.object(HandoffHandler, "_ensure_state_store"):
            handler = get_handoff_handler()

        assert isinstance(handler, HandoffHandler)

    def test_returns_same_instance(self):
        """Test that get_handoff_handler returns singleton."""
        import forge_harness.webhook_server.handlers.handoff_handler as hh_module
        hh_module._handoff_handler = None

        with patch.object(HandoffHandler, "_ensure_state_store"):
            handler1 = get_handoff_handler()
            handler2 = get_handoff_handler()

        assert handler1 is handler2

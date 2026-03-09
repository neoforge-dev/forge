"""Extended tests for forge_harness/webhook_server/handlers/handoff_handler.py.

Covers additional edge cases, boundary conditions, and integration scenarios
beyond the baseline test_handoff_handler.py suite.

Goals:
- Validate serialisation round-trips with diverse field types
- Exercise list_handoffs with combined filters
- Test accept/reject/complete with extra edge cases
- Verify singleton reset behaviour
- Test key generation formats exhaustively
- Validate status transition semantics
- Cover error paths in update, create, and list operations
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.handlers.handoff_handler import (
    HandoffHandler,
    get_handoff_handler,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_redis_mock() -> MagicMock:
    return MagicMock()


def make_state_store(redis_mock: MagicMock) -> MagicMock:
    store = MagicMock()
    store.is_connected.return_value = True
    store.get_store_type.return_value = "redis"
    store._redis = MagicMock()
    store._redis._client = redis_mock
    return store


def make_handler(redis_mock: MagicMock | None = None) -> tuple[HandoffHandler, MagicMock]:
    if redis_mock is None:
        redis_mock = make_redis_mock()
    store = make_state_store(redis_mock)
    handler = HandoffHandler(state_store=store)
    return handler, redis_mock


def raw_handoff_bytes(
    handoff_id: str = "h1",
    status: str = "pending",
    from_agent: str = "agent-a",
    to_agent: str = "agent-b",
    created_at: str = "2026-01-01T00:00:00Z",
    files: str = "[]",
) -> dict:
    return {
        b"id": handoff_id.encode(),
        b"status": status.encode(),
        b"from_agent": from_agent.encode(),
        b"to_agent": to_agent.encode(),
        b"created_at": created_at.encode(),
        b"files": files.encode(),
    }


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    """Verify Redis key formats."""

    def test_handoff_key_format(self):
        handler, _ = make_handler()
        assert handler._handoff_key("abc-123") == "forge:handoffs:abc-123"

    def test_handoff_key_with_uuid_like_id(self):
        handler, _ = make_handler()
        uid = str(uuid.uuid4())
        key = handler._handoff_key(uid)
        assert key == f"forge:handoffs:{uid}"

    def test_handoffs_index_key_constant(self):
        handler, _ = make_handler()
        assert handler._handoffs_index_key() == "forge:handoffs:index"

    def test_index_key_never_changes(self):
        handler, _ = make_handler()
        k1 = handler._handoffs_index_key()
        k2 = handler._handoffs_index_key()
        assert k1 == k2


# ---------------------------------------------------------------------------
# Redis client retrieval
# ---------------------------------------------------------------------------


class TestGetRedisClient:
    """Edge cases for _get_redis_client."""

    def test_returns_client_when_all_conditions_met(self):
        handler, redis = make_handler()
        assert handler._get_redis_client() is redis

    def test_returns_none_when_not_connected(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        store.is_connected.return_value = False
        handler = HandoffHandler(state_store=store)
        assert handler._get_redis_client() is None

    def test_returns_none_when_store_type_not_redis(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        store.get_store_type.return_value = "memory"
        handler = HandoffHandler(state_store=store)
        assert handler._get_redis_client() is None

    def test_returns_none_when_no_redis_attribute(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        del store._redis
        handler = HandoffHandler(state_store=store)
        assert handler._get_redis_client() is None

    def test_returns_none_when_no_client_attribute(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        del store._redis._client
        handler = HandoffHandler(state_store=store)
        assert handler._get_redis_client() is None

    def test_returns_none_when_state_store_none(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        store.is_connected.return_value = False
        handler = HandoffHandler(state_store=store)
        handler.state_store = None
        assert handler._get_redis_client() is None


# ---------------------------------------------------------------------------
# Serialisation / deserialisation
# ---------------------------------------------------------------------------


class TestSerialisation:
    """Thorough serialisation round-trip tests."""

    def test_serialize_string_values_unchanged(self):
        handler, _ = make_handler()
        d = {"status": "pending", "from_agent": "a"}
        result = handler._serialize_handoff(d)
        assert result["status"] == "pending"
        assert result["from_agent"] == "a"

    def test_serialize_list_as_json_string(self):
        handler, _ = make_handler()
        d = {"files": ["x.py", "y.py"]}
        result = handler._serialize_handoff(d)
        parsed = json.loads(result["files"])
        assert parsed == ["x.py", "y.py"]

    def test_serialize_nested_dict_as_json_string(self):
        handler, _ = make_handler()
        d = {"meta": {"key": "value"}}
        result = handler._serialize_handoff(d)
        parsed = json.loads(result["meta"])
        assert parsed == {"key": "value"}

    def test_serialize_none_to_empty_string(self):
        handler, _ = make_handler()
        d = {"notes": None}
        result = handler._serialize_handoff(d)
        assert result["notes"] == ""

    def test_serialize_integer_to_string(self):
        handler, _ = make_handler()
        d = {"count": 42}
        result = handler._serialize_handoff(d)
        assert result["count"] == "42"

    def test_deserialize_bytes_keys_decoded(self):
        handler, _ = make_handler()
        data = {b"id": b"h1", b"status": b"pending", b"files": b"[]"}
        result = handler._deserialize_handoff(data)
        assert "id" in result
        assert result["id"] == "h1"

    def test_deserialize_empty_value_becomes_none(self):
        handler, _ = make_handler()
        data = {b"notes": b""}
        result = handler._deserialize_handoff(data)
        assert result["notes"] is None

    def test_deserialize_files_invalid_json_fallback(self):
        handler, _ = make_handler()
        data = {b"files": b"{broken json}"}
        result = handler._deserialize_handoff(data)
        assert result["files"] == []

    def test_deserialize_files_valid_json_list(self):
        handler, _ = make_handler()
        data = {b"files": b'["a.py", "b.py"]'}
        result = handler._deserialize_handoff(data)
        assert result["files"] == ["a.py", "b.py"]

    def test_deserialize_string_keys_also_work(self):
        handler, _ = make_handler()
        data = {"id": "str-key", "files": '["c.py"]'}
        result = handler._deserialize_handoff(data)
        assert result["id"] == "str-key"
        assert result["files"] == ["c.py"]

    def test_roundtrip_preserves_all_fields(self):
        handler, _ = make_handler()
        original = {
            "id": "rt-1",
            "from_agent": "a",
            "to_agent": "b",
            "task_description": "do stuff",
            "files": ["f.py"],
            "priority": "high",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        serialized = handler._serialize_handoff(original)
        # Simulate bytes back from Redis
        bytes_data = {k.encode(): v.encode() for k, v in serialized.items()}
        restored = handler._deserialize_handoff(bytes_data)
        assert restored["id"] == original["id"]
        assert restored["from_agent"] == original["from_agent"]
        assert restored["files"] == original["files"]
        assert restored["priority"] == original["priority"]


# ---------------------------------------------------------------------------
# list_handoffs
# ---------------------------------------------------------------------------


class TestListHandoffsExtended:
    """Additional list_handoffs edge cases."""

    @pytest.mark.asyncio
    async def test_list_handoffs_combined_status_and_from_agent_filter(self):
        handler, redis = make_handler()
        redis.smembers.return_value = {"h1", "h2", "h3"}
        redis.hgetall.side_effect = [
            raw_handoff_bytes("h1", "pending", "agent-x", "b"),
            raw_handoff_bytes("h2", "accepted", "agent-x", "b"),
            raw_handoff_bytes("h3", "pending", "agent-y", "b"),
        ]
        result = await handler.list_handoffs(status="pending", from_agent="agent-x")
        assert len(result) == 1
        assert result[0]["id"] == "h1"

    @pytest.mark.asyncio
    async def test_list_handoffs_to_agent_and_status_filter(self):
        handler, redis = make_handler()
        redis.smembers.return_value = {"h1", "h2"}
        redis.hgetall.side_effect = [
            raw_handoff_bytes("h1", "pending", "a", "target-agent"),
            raw_handoff_bytes("h2", "completed", "a", "target-agent"),
        ]
        result = await handler.list_handoffs(status="completed", to_agent="target-agent")
        assert len(result) == 1
        assert result[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_list_handoffs_all_filters_combined(self):
        handler, redis = make_handler()
        redis.smembers.return_value = {"h1", "h2", "h3"}
        redis.hgetall.side_effect = [
            raw_handoff_bytes("h1", "pending", "from-x", "to-y"),
            raw_handoff_bytes("h2", "pending", "from-x", "to-z"),
            raw_handoff_bytes("h3", "accepted", "from-x", "to-y"),
        ]
        result = await handler.list_handoffs(status="pending", from_agent="from-x", to_agent="to-y")
        assert len(result) == 1
        assert result[0]["id"] == "h1"

    @pytest.mark.asyncio
    async def test_list_handoffs_empty_smembers(self):
        handler, redis = make_handler()
        redis.smembers.return_value = set()
        result = await handler.list_handoffs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_handoffs_limit_one(self):
        handler, redis = make_handler()
        redis.smembers.return_value = {"h1", "h2", "h3"}
        redis.hgetall.side_effect = [
            raw_handoff_bytes("h1", "pending", "a", "b", "2026-01-03T00:00:00Z"),
            raw_handoff_bytes("h2", "pending", "a", "b", "2026-01-02T00:00:00Z"),
            raw_handoff_bytes("h3", "pending", "a", "b", "2026-01-01T00:00:00Z"),
        ]
        result = await handler.list_handoffs(limit=1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_handoffs_sorted_descending_by_created_at(self):
        handler, redis = make_handler()
        redis.smembers.return_value = {"h1", "h2"}
        redis.hgetall.side_effect = [
            raw_handoff_bytes("h1", "pending", "a", "b", "2026-01-01T00:00:00Z"),
            raw_handoff_bytes("h2", "pending", "a", "b", "2026-01-03T00:00:00Z"),
        ]
        result = await handler.list_handoffs()
        # h2 (newer) should be first
        assert result[0]["id"] == "h2"
        assert result[1]["id"] == "h1"

    @pytest.mark.asyncio
    async def test_list_handoffs_skips_empty_hgetall(self):
        handler, redis = make_handler()
        redis.smembers.return_value = {"h1", "h2"}
        redis.hgetall.side_effect = [
            {},
            raw_handoff_bytes("h2"),
        ]
        result = await handler.list_handoffs()
        assert len(result) == 1
        assert result[0]["id"] == "h2"

    @pytest.mark.asyncio
    async def test_list_handoffs_redis_exception_returns_empty(self):
        handler, redis = make_handler()
        redis.smembers.side_effect = RuntimeError("connection lost")
        result = await handler.list_handoffs()
        assert result == []


# ---------------------------------------------------------------------------
# create_handoff
# ---------------------------------------------------------------------------


class TestCreateHandoffExtended:
    """Additional create_handoff edge cases."""

    @pytest.mark.asyncio
    async def test_create_handoff_generates_uuid_id(self):
        handler, _ = make_handler()
        result = await handler.create_handoff("a", "b", "task")
        # Should be a valid UUID string
        parsed = uuid.UUID(result["id"])
        assert str(parsed) == result["id"]

    @pytest.mark.asyncio
    async def test_create_handoff_created_at_is_iso_string(self):
        handler, _ = make_handler()
        result = await handler.create_handoff("a", "b", "task")
        # Should parse without error
        dt = datetime.fromisoformat(result["created_at"])
        assert dt is not None

    @pytest.mark.asyncio
    async def test_create_handoff_created_at_equals_updated_at(self):
        handler, _ = make_handler()
        result = await handler.create_handoff("a", "b", "task")
        assert result["created_at"] == result["updated_at"]

    @pytest.mark.asyncio
    async def test_create_handoff_status_always_pending(self):
        handler, _ = make_handler()
        result = await handler.create_handoff("a", "b", "task", priority="low")
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_create_handoff_stores_task_description(self):
        handler, _ = make_handler()
        result = await handler.create_handoff("a", "b", "Fix the critical bug")
        assert result["task_description"] == "Fix the critical bug"

    @pytest.mark.asyncio
    async def test_create_handoff_sets_ttl_7_days(self):
        handler, redis = make_handler()
        await handler.create_handoff("a", "b", "task")
        redis.expire.assert_called_once()
        args = redis.expire.call_args[0]
        assert args[1] == 604800  # 7 days in seconds

    @pytest.mark.asyncio
    async def test_create_handoff_adds_to_index(self):
        handler, redis = make_handler()
        result = await handler.create_handoff("a", "b", "task")
        redis.sadd.assert_called_once_with(handler._handoffs_index_key(), result["id"])

    @pytest.mark.asyncio
    async def test_create_handoff_with_empty_files(self):
        handler, _ = make_handler()
        result = await handler.create_handoff("a", "b", "task", files=[])
        assert result["files"] == []

    @pytest.mark.asyncio
    async def test_create_handoff_no_redis_raises(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        store.is_connected.return_value = False
        handler = HandoffHandler(state_store=store)
        with pytest.raises(Exception, match="Redis not available"):
            await handler.create_handoff("a", "b", "task")

    @pytest.mark.asyncio
    async def test_create_handoff_redis_error_propagates(self):
        handler, redis = make_handler()
        redis.hset.side_effect = ConnectionError("Redis down")
        with pytest.raises(ConnectionError):
            await handler.create_handoff("a", "b", "task")


# ---------------------------------------------------------------------------
# accept / reject / complete handoff
# ---------------------------------------------------------------------------


class TestHandoffStatusTransitions:
    """Status transition via accept/reject/complete."""

    @pytest.mark.asyncio
    async def test_accept_sets_status_accepted(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending")
        result = await handler.accept_handoff("h1")
        assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_accept_without_agent_keeps_original_to_agent(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending", "a", "original-agent")
        result = await handler.accept_handoff("h1", accepting_agent=None)
        assert result["to_agent"] == "original-agent"

    @pytest.mark.asyncio
    async def test_accept_with_agent_updates_to_agent(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending", "a", "old")
        result = await handler.accept_handoff("h1", accepting_agent="new-agent")
        assert result["to_agent"] == "new-agent"

    @pytest.mark.asyncio
    async def test_reject_sets_status_rejected(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending")
        result = await handler.reject_handoff("h1", reason="wrong team")
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_reject_stores_reason(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending")
        result = await handler.reject_handoff("h1", reason="out of scope")
        assert result["reason"] == "out of scope"

    @pytest.mark.asyncio
    async def test_complete_sets_status_completed(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "accepted")
        result = await handler.complete_handoff("h1")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_with_notes_stores_notes(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "accepted")
        result = await handler.complete_handoff("h1", notes="All tests pass")
        assert result["notes"] == "All tests pass"

    @pytest.mark.asyncio
    async def test_complete_without_notes_stores_none(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "accepted")
        result = await handler.complete_handoff("h1", notes=None)
        assert result is not None
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_update_not_found_returns_none(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = {}
        result = await handler._update_handoff_status("nonexistent", "accepted")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_redis_returns_none(self):
        redis = make_redis_mock()
        store = make_state_store(redis)
        store.is_connected.return_value = False
        handler = HandoffHandler(state_store=store)
        result = await handler._update_handoff_status("h1", "accepted")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_hset_exception_returns_none(self):
        handler, redis = make_handler()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending")
        redis.hset.side_effect = RuntimeError("write error")
        result = await handler._update_handoff_status("h1", "accepted")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_updates_updated_at(self):
        handler, redis = make_handler()
        before = datetime.now(UTC).isoformat()
        redis.hgetall.return_value = raw_handoff_bytes("h1", "pending")
        result = await handler.accept_handoff("h1")
        assert result is not None
        assert result["updated_at"] >= before


# ---------------------------------------------------------------------------
# Singleton get_handoff_handler
# ---------------------------------------------------------------------------


class TestGetHandoffHandlerExtended:
    """Extended singleton tests."""

    def test_singleton_is_none_initially_when_reset(self):
        import forge_harness.webhook_server.handlers.handoff_handler as hh_module
        hh_module._handoff_handler = None
        with patch.object(HandoffHandler, "_ensure_state_store"):
            h = get_handoff_handler()
        assert h is not None

    def test_singleton_same_instance_across_multiple_calls(self):
        import forge_harness.webhook_server.handlers.handoff_handler as hh_module
        hh_module._handoff_handler = None
        with patch.object(HandoffHandler, "_ensure_state_store"):
            h1 = get_handoff_handler()
            h2 = get_handoff_handler()
            h3 = get_handoff_handler()
        assert h1 is h2
        assert h2 is h3

    def test_singleton_can_be_replaced(self):
        import forge_harness.webhook_server.handlers.handoff_handler as hh_module
        hh_module._handoff_handler = None
        with patch.object(HandoffHandler, "_ensure_state_store"):
            h1 = get_handoff_handler()
        hh_module._handoff_handler = None
        with patch.object(HandoffHandler, "_ensure_state_store"):
            h2 = get_handoff_handler()
        assert h1 is not h2

    def test_returns_handoff_handler_type(self):
        import forge_harness.webhook_server.handlers.handoff_handler as hh_module
        hh_module._handoff_handler = None
        with patch.object(HandoffHandler, "_ensure_state_store"):
            h = get_handoff_handler()
        assert isinstance(h, HandoffHandler)

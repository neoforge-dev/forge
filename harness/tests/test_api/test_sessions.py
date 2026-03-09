"""Tests for /api/sessions endpoints.

Tests cover session tracking and handoff functionality:
- GET /api/handoffs - List all handoffs
- POST /api/handoffs - Create new handoff
- POST /api/handoffs/{handoff_id}/accept - Accept handoff
- POST /api/handoffs/{handoff_id}/reject - Reject handoff
- POST /api/handoffs/{handoff_id}/complete - Complete handoff

Note: /api/sessions endpoints are covered via handoffs API for session continuity.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestHandoffsAPI:
    """Test class for /api/handoffs endpoints."""

    @pytest.fixture
    def mock_state_store(self):
        """Create a mock state store."""
        store = Mock()
        store.is_connected = Mock(return_value=True)
        store._redis = Mock()
        store._redis._client = Mock()
        store._redis._client.keys = Mock(return_value=[])
        store._redis._client.hgetall = Mock(return_value={})
        store._redis._client.hset = Mock()
        return store

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = AsyncMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def sample_handoff(self):
        """Create a sample handoff."""
        return {
            "id": "handoff-001",
            "from_agent": "agent-001",
            "to_agent": "agent-002",
            "task_description": "Continue implementing feature",
            "files": ["feature.py", "tests.py"],
            "priority": "high",
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "accepted_at": None,
            "completed_at": None,
            "rejection_reason": None,
            "completion_notes": None,
        }

    async def test_list_handoffs_empty(self, mock_state_store):
        """Test listing handoffs when empty."""
        from forge_harness.webhook_server.api.handoffs import list_handoffs

        with patch('forge_harness.webhook_server.api.handoffs.get_state_store') as mock_store:
            mock_store.return_value = mock_state_store

            result = await list_handoffs()

            assert result.status_code == 200
            assert 'success' in result.body.decode()
            assert 'handoffs' in result.body.decode()
            assert 'count' in result.body.decode()

    async def test_list_handoffs_with_data(self, mock_state_store, sample_handoff):
        """Test listing handoffs with active handoffs."""
        # Mock Redis to return a handoff
        mock_state_store._redis._client.keys = Mock(return_value=[b"forge:handoffs:handoff-001"])
        mock_state_store._redis._client.hgetall = Mock(return_value={
            b"id": b"handoff-001",
            b"from_agent": b"agent-001",
            b"to_agent": b"agent-002",
            b"task_description": b"Continue implementing feature",
            b"files": b'["feature.py", "tests.py"]',
            b"priority": b"high",
            b"status": b"pending",
            b"created_at": b"2024-01-01T00:00:00Z",
            b"accepted_at": b"",
            b"completed_at": b"",
            b"rejection_reason": b"",
            b"completion_notes": b"",
        })

        from forge_harness.webhook_server.api.handoffs import list_handoffs

        with patch('forge_harness.webhook_server.api.handoffs.get_state_store') as mock_store:
            mock_store.return_value = mock_state_store

            result = await list_handoffs()

            assert result.status_code == 200
            assert 'success' in result.body.decode()

    async def test_list_handoffs_filter_by_status(self, mock_state_store, sample_handoff):
        """Test listing handoffs filtered by status."""
        mock_state_store._redis._client.keys = Mock(return_value=[b"forge:handoffs:handoff-001"])
        mock_state_store._redis._client.hgetall = Mock(return_value={
            b"id": b"handoff-001",
            b"from_agent": b"agent-001",
            b"to_agent": b"agent-002",
            b"task_description": b"Continue implementing feature",
            b"files": b'[]',
            b"priority": b"high",
            b"status": b"pending",
            b"created_at": b"2024-01-01T00:00:00Z",
            b"accepted_at": b"",
            b"completed_at": b"",
            b"rejection_reason": b"",
            b"completion_notes": b"",
        })

        from forge_harness.webhook_server.api.handoffs import list_handoffs

        with patch('forge_harness.webhook_server.api.handoffs.get_state_store') as mock_store:
            mock_store.return_value = mock_state_store

            result = await list_handoffs(status="pending")

            assert result.status_code == 200

    async def test_create_handoff(self, mock_event_bus):
        """Test creating a new handoff."""
        from forge_harness.webhook_server.api.handoffs import HandoffRequest, create_handoff

        body = HandoffRequest(
            from_agent="agent-001",
            to_agent="agent-002",
            task_description="Continue feature implementation",
            files=["feature.py"],
            priority="high"
        )

        mock_handler = AsyncMock()
        mock_handler.create_handoff = AsyncMock(return_value={
            "id": "handoff-new",
            "from_agent": "agent-001",
            "to_agent": "agent-002",
            "task_description": "Continue feature implementation",
            "files": ["feature.py"],
            "priority": "high",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z",
        })

        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "127.0.0.1", "User-Agent": "test"}
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler), \
             patch('forge_harness.webhook_server.api.handoffs.get_event_bus', new_callable=AsyncMock, return_value=mock_event_bus), \
             patch('forge_harness.webhook_server.api.handoffs.get_audit_logger') as mock_audit:
            mock_audit.return_value = AsyncMock()

            result = await create_handoff(body, mock_request)

            assert result.status_code == 200
            assert 'success' in result.body.decode()

    async def test_create_handoff_storage_error(self):
        """Test creating handoff when storage is not connected."""
        from forge_harness.webhook_server.api.handoffs import HandoffRequest, create_handoff

        body = HandoffRequest(
            from_agent="agent-001",
            to_agent="agent-002",
            task_description="Test"
        )

        mock_handler = AsyncMock()
        mock_handler.create_handoff = AsyncMock(side_effect=Exception("Redis not available"))

        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "127.0.0.1", "User-Agent": "test"}
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler):
            result = await create_handoff(body, mock_request)

            # Should return error response (not raise HTTPException)
            assert 'CREATE_FAILED' in result.body.decode()

    async def test_accept_handoff(self, mock_event_bus):
        """Test accepting a handoff."""
        from forge_harness.webhook_server.api.handoffs import HandoffAcceptRequest, accept_handoff

        request = HandoffAcceptRequest(accepting_agent="agent-002")

        mock_handler = AsyncMock()
        mock_handler.accept_handoff = AsyncMock(return_value={
            "id": "handoff-001",
            "from_agent": "agent-001",
            "to_agent": "agent-002",
            "status": "accepted",
        })

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler), \
             patch('forge_harness.webhook_server.api.handoffs.get_event_bus', new_callable=AsyncMock, return_value=mock_event_bus):

            result = await accept_handoff("handoff-001", request)

            assert result.status_code == 200
            assert 'success' in result.body.decode()

    async def test_accept_handoff_not_found(self, mock_state_store):
        """Test accepting non-existent handoff."""
        mock_state_store._redis._client.hgetall = Mock(return_value={})

        from fastapi import HTTPException

        from forge_harness.webhook_server.api.handoffs import HandoffAcceptRequest, accept_handoff

        request = HandoffAcceptRequest()

        with patch('forge_harness.webhook_server.api.handoffs.get_state_store') as mock_store:
            mock_store.return_value = mock_state_store

            with pytest.raises(HTTPException) as exc_info:
                await accept_handoff("non-existent", request)
            assert exc_info.value.status_code == 404

    async def test_accept_handoff_wrong_status(self):
        """Test accepting handoff with wrong status."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.handoffs import HandoffAcceptRequest, accept_handoff

        request = HandoffAcceptRequest()

        mock_handler = AsyncMock()
        mock_handler.accept_handoff = AsyncMock(
            side_effect=ValueError("Cannot transition from 'completed' to 'accepted'")
        )

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler):
            with pytest.raises(HTTPException) as exc_info:
                await accept_handoff("handoff-001", request)
            assert exc_info.value.status_code == 400

    async def test_reject_handoff(self, mock_event_bus):
        """Test rejecting a handoff."""
        from forge_harness.webhook_server.api.handoffs import HandoffRejectRequest, reject_handoff

        request = HandoffRejectRequest(reason="Cannot handle this task")

        mock_handler = AsyncMock()
        mock_handler.reject_handoff = AsyncMock(return_value={
            "id": "handoff-001",
            "from_agent": "agent-001",
            "to_agent": "agent-002",
            "status": "rejected",
            "reason": "Cannot handle this task",
        })

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler), \
             patch('forge_harness.webhook_server.api.handoffs.get_event_bus', new_callable=AsyncMock, return_value=mock_event_bus):

            result = await reject_handoff("handoff-001", request)

            assert result.status_code == 200
            assert 'success' in result.body.decode()

    async def test_reject_handoff_not_pending(self):
        """Test rejecting handoff that is not in pending/accepted status."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.handoffs import HandoffRejectRequest, reject_handoff

        request = HandoffRejectRequest(reason="Test")

        mock_handler = AsyncMock()
        mock_handler.reject_handoff = AsyncMock(
            side_effect=ValueError("Cannot transition from 'completed' to 'rejected'")
        )

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler):
            with pytest.raises(HTTPException) as exc_info:
                await reject_handoff("handoff-001", request)
            assert exc_info.value.status_code == 400

    async def test_complete_handoff(self, mock_event_bus):
        """Test completing a handoff."""
        from forge_harness.webhook_server.api.handoffs import (
            HandoffCompleteRequest,
            complete_handoff,
        )

        request = HandoffCompleteRequest(notes="Completed successfully")

        mock_handler = AsyncMock()
        mock_handler.complete_handoff = AsyncMock(return_value={
            "id": "handoff-001",
            "from_agent": "agent-001",
            "to_agent": "agent-002",
            "status": "completed",
            "notes": "Completed successfully",
        })

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler), \
             patch('forge_harness.webhook_server.api.handoffs.get_event_bus', new_callable=AsyncMock, return_value=mock_event_bus):

            result = await complete_handoff("handoff-001", request)

            assert result.status_code == 200
            assert 'success' in result.body.decode()

    async def test_complete_handoff_wrong_status(self):
        """Test completing handoff with wrong status."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.handoffs import (
            HandoffCompleteRequest,
            complete_handoff,
        )

        request = HandoffCompleteRequest()

        mock_handler = AsyncMock()
        mock_handler.complete_handoff = AsyncMock(
            side_effect=ValueError("Cannot transition from 'pending' to 'completed'")
        )

        with patch('forge_harness.webhook_server.api.handoffs.get_handoff_handler', new_callable=AsyncMock, return_value=mock_handler):
            with pytest.raises(HTTPException) as exc_info:
                await complete_handoff("handoff-001", request)
            assert exc_info.value.status_code == 400


class TestHandoffsAPIModels:
    """Test Pydantic models for handoffs API."""

    def test_handoff_request(self):
        """Test HandoffRequest model."""
        from forge_harness.webhook_server.api.handoffs import HandoffRequest

        request = HandoffRequest(
            from_agent="agent-001",
            to_agent="agent-002",
            task_description="Continue implementation",
            files=["file1.py", "file2.py"],
            priority="high"
        )

        assert request.from_agent == "agent-001"
        assert request.to_agent == "agent-002"
        assert request.task_description == "Continue implementation"
        assert len(request.files) == 2
        assert request.priority == "high"

    def test_handoff_request_defaults(self):
        """Test HandoffRequest default values."""
        from forge_harness.webhook_server.api.handoffs import HandoffRequest

        request = HandoffRequest(
            from_agent="agent-001",
            to_agent="agent-002",
            task_description="Test"
        )

        assert request.files == []
        assert request.priority == "medium"

    def test_handoff_accept_request(self):
        """Test HandoffAcceptRequest model."""
        from forge_harness.webhook_server.api.handoffs import HandoffAcceptRequest

        # Default
        request = HandoffAcceptRequest()
        assert request.accepting_agent is None

        # With agent
        request = HandoffAcceptRequest(accepting_agent="agent-002")
        assert request.accepting_agent == "agent-002"

    def test_handoff_reject_request(self):
        """Test HandoffRejectRequest model."""
        from forge_harness.webhook_server.api.handoffs import HandoffRejectRequest

        request = HandoffRejectRequest(reason="Cannot handle this task")

        assert request.reason == "Cannot handle this task"

    def test_handoff_complete_request(self):
        """Test HandoffCompleteRequest model."""
        from forge_harness.webhook_server.api.handoffs import HandoffCompleteRequest

        # Default
        request = HandoffCompleteRequest()
        assert request.notes is None

        # With notes
        request = HandoffCompleteRequest(notes="Completed successfully")
        assert request.notes == "Completed successfully"


class TestHandoffsAPIHelpers:
    """Test helper functions for handoffs API."""

    def test_get_handoff_key(self):
        """Test _get_handoff_key helper."""
        from forge_harness.webhook_server.api.handoffs import _get_handoff_key

        key = _get_handoff_key("handoff-123")

        assert key == "forge:handoffs:handoff-123"

    def test_serialize_handoff(self):
        """Test _serialize_handoff helper."""
        from forge_harness.webhook_server.api.handoffs import _serialize_handoff

        handoff = {
            "id": "test",
            "files": ["file1.py", "file2.py"],
            "priority": "high",
        }

        result = _serialize_handoff(handoff)

        assert result["id"] == "test"
        assert result["files"] == '["file1.py", "file2.py"]'
        assert result["priority"] == "high"

    def test_deserialize_handoff(self):
        """Test _deserialize_handoff helper."""
        from forge_harness.webhook_server.api.handoffs import _deserialize_handoff

        data = {
            b"id": b"test",
            b"files": b'["file1.py", "file2.py"]',
            b"priority": b"high",
            b"status": b"pending",
        }

        result = _deserialize_handoff(data)

        assert result["id"] == "test"
        assert result["files"] == ["file1.py", "file2.py"]
        assert result["priority"] == "high"
        assert result["status"] == "pending"

    def test_deserialize_handoff_empty(self):
        """Test _deserialize_handoff with empty data."""
        from forge_harness.webhook_server.api.handoffs import _deserialize_handoff

        data = {}

        result = _deserialize_handoff(data)

        assert result == {}

    def test_api_response(self):
        """Test api_response helper."""
        from forge_harness.webhook_server.api.handoffs import api_response

        # Success response
        result = api_response({"key": "value"})

        assert result["success"] is True
        assert result["data"] == {"key": "value"}
        assert result["error"] is None
        assert "timestamp" in result

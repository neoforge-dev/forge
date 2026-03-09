"""Comprehensive tests for forge_harness/webhook_server/api/messages.py

Covers all endpoints:
- POST /api/agents/{agent_id}/messages/send
- GET  /api/agents/{agent_id}/messages/pending
- POST /api/agents/{agent_id}/messages/{message_id}/ack
- GET  /api/messages/{message_id}/status
- GET  /api/messages/queue/stats
- GET  /api/agents/{agent1_id}/messages/conversation/{agent2_id}

All external dependencies (agent_registry, message_queue, event_bus, send_to_tmux)
are mocked. Auth is bypassed via app.state.auth_config.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.webhook_server.api.messages import (
    AckResponse,
    AgentMessageRequestV2,
    ConversationResponse,
    MessageRequest,
    MessageResponse,
    QueueStats,
    StatsResponse,
    StatusResponse,
    api_response,
    router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_queued_message(
    msg_id: str = "msg-abc123",
    sender_id: str = "system",
    recipient_id: str = "agent-001",
    msg_type: str = "instruction",
    content: str = "Hello agent",
    priority: int = 1,
    status: str = "pending",
    acknowledged_at: datetime | None = None,
) -> Mock:
    """Return a mock QueuedMessage."""
    msg = Mock()
    msg.id = msg_id
    msg.sender_id = sender_id
    msg.recipient_id = recipient_id
    msg.type = msg_type
    msg.content = content
    msg.priority = priority
    msg.status = Mock()
    msg.status.value = status
    msg.created_at = datetime.now(UTC)
    msg.delivered_at = None
    msg.acknowledged_at = acknowledged_at
    msg.expires_at = None
    msg.metadata = {}
    msg.retry_count = 0
    msg.to_dict = Mock(
        return_value={
            "id": msg_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "type": msg_type,
            "content": content,
            "priority": priority,
            "status": status,
            "created_at": datetime.now(UTC).isoformat(),
            "delivered_at": None,
            "acknowledged_at": acknowledged_at.isoformat() if acknowledged_at else None,
            "expires_at": None,
            "metadata": {},
            "retry_count": 0,
        }
    )
    return msg


def _make_agent(
    agent_id: str = "agent-001",
    tmux_session: str | None = None,
) -> Mock:
    """Return a minimal mock AgentSession."""
    agent = Mock()
    agent.id = agent_id
    agent.tmux_session = tmux_session
    return agent


_SENTINEL = object()  # sentinel to distinguish "not passed" from None/{}


def _make_queue(
    pending: list | None = None,
    ack_result: Mock | None = None,
    status_result: Mock | None = None,
    stats: dict | None = _SENTINEL,  # type: ignore[assignment]
    conversation: list | None = None,
) -> Mock:
    """Return a mock MessageQueue."""
    _default_stats = {
        "pending_messages": 5,
        "delivered_messages": 10,
        "acknowledged_messages": 3,
        "processing_rate": 1.5,
        "average_wait_time": 2.0,
        "total_processed": 13,
        "queue_age_seconds": 120,
    }
    queue = Mock()
    queue.get_pending = Mock(return_value=pending or [])
    queue.acknowledge = Mock(return_value=ack_result)
    queue.get_status = Mock(return_value=status_result)
    queue.get_stats = Mock(
        return_value=_default_stats if stats is _SENTINEL else stats  # type: ignore[comparison-overlap]
    )
    queue.get_conversation = Mock(return_value=conversation or [])
    return queue


def _make_registry(
    agent: Mock | None = None,
    message_id: str | None = "msg-xyz",
) -> Mock:
    """Return a mock AgentRegistry."""
    registry = Mock()
    registry.send_message = Mock(return_value=(agent, message_id))
    return registry


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    return bus


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with the messages router and auth bypassed."""
    app = FastAPI()

    # Bypass auth: set require_auth=False on app state
    auth_cfg = Mock()
    auth_cfg.require_auth = False
    auth_cfg.allow_localhost = False
    app.state.auth_config = auth_cfg

    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Context manager to patch all three dep functions in the messages module
# ---------------------------------------------------------------------------


@contextmanager
def _patch_deps(
    registry: Mock | None = None,
    queue: Mock | None = None,
    event_bus: Mock | None = None,
):
    """Context manager that patches get_agent_registry, get_message_queue, get_event_bus.

    Yields (registry, queue, event_bus) mocks.
    """
    reg = registry or _make_registry()
    q = queue or _make_queue()
    bus = event_bus or _make_event_bus()

    with patch(
        "forge_harness.webhook_server.api.messages.get_agent_registry",
        return_value=reg,
    ), patch(
        "forge_harness.webhook_server.api.messages.get_message_queue",
        return_value=q,
    ), patch(
        "forge_harness.webhook_server.api.messages.get_event_bus",
        return_value=bus,
    ):
        yield reg, q, bus


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestMessageRequest:
    def test_minimal_required_fields(self):
        req = MessageRequest(type="instruction", content="do something")
        assert req.type == "instruction"
        assert req.content == "do something"
        assert req.sender_id == "system"
        assert req.priority == 1
        assert req.metadata == {}

    def test_full_fields(self):
        req = MessageRequest(
            type="notification",
            content="hello",
            sender_id="agent-99",
            priority=3,
            metadata={"key": "value"},
        )
        assert req.sender_id == "agent-99"
        assert req.priority == 3
        assert req.metadata == {"key": "value"}

    def test_priority_bounds_valid(self):
        MessageRequest(type="t", content="c", priority=0)
        MessageRequest(type="t", content="c", priority=3)

    def test_priority_below_zero_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MessageRequest(type="t", content="c", priority=-1)

    def test_priority_above_three_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MessageRequest(type="t", content="c", priority=4)

    def test_missing_type_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MessageRequest(content="hello")  # type missing

    def test_missing_content_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MessageRequest(type="instruction")  # content missing


class TestAgentMessageRequestV2:
    def test_defaults(self):
        req = AgentMessageRequestV2(type="handoff", content="data payload")
        assert req.sender_id == "system"
        assert req.priority == 1
        assert req.metadata == {}

    def test_all_fields(self):
        req = AgentMessageRequestV2(
            type="urgent",
            content="do it now",
            sender_id="orchestrator",
            priority=3,
            metadata={"trace": "abc"},
        )
        assert req.sender_id == "orchestrator"
        assert req.priority == 3

    def test_priority_out_of_range_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentMessageRequestV2(type="t", content="c", priority=5)

    def test_priority_negative_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentMessageRequestV2(type="t", content="c", priority=-1)


class TestMessageResponseModel:
    def test_construction(self):
        resp = MessageResponse(messages=[{"id": "m1"}], count=1)
        assert resp.count == 1
        assert resp.messages[0]["id"] == "m1"

    def test_empty_messages(self):
        resp = MessageResponse(messages=[], count=0)
        assert resp.count == 0
        assert resp.messages == []


class TestAckResponseModel:
    def test_construction(self):
        resp = AckResponse(acknowledged=True, message={"id": "m1"})
        assert resp.acknowledged is True

    def test_not_acknowledged(self):
        resp = AckResponse(acknowledged=False, message={})
        assert resp.acknowledged is False


class TestStatusResponseModel:
    def test_construction(self):
        resp = StatusResponse(
            status="pending",
            sender_id="s1",
            recipient_id="r1",
            created_at="2026-01-01T00:00:00Z",
            delivered_at=None,
            acknowledged_at=None,
        )
        assert resp.status == "pending"
        assert resp.delivered_at is None

    def test_with_timestamps(self):
        resp = StatusResponse(
            status="acknowledged",
            sender_id="s1",
            recipient_id="r1",
            created_at="2026-01-01T00:00:00Z",
            delivered_at="2026-01-01T00:01:00Z",
            acknowledged_at="2026-01-01T00:02:00Z",
        )
        assert resp.delivered_at == "2026-01-01T00:01:00Z"
        assert resp.acknowledged_at == "2026-01-01T00:02:00Z"


class TestStatsResponseModel:
    def test_construction(self):
        resp = StatsResponse(
            total_pending=5,
            total_delivered=10,
            total_acknowledged=3,
        )
        assert resp.total_pending == 5
        assert resp.total_delivered == 10
        assert resp.total_acknowledged == 3


class TestConversationResponseModel:
    def test_construction(self):
        resp = ConversationResponse(messages=[], count=0)
        assert resp.count == 0

    def test_with_messages(self):
        resp = ConversationResponse(messages=[{"id": "m1"}, {"id": "m2"}], count=2)
        assert resp.count == 2


class TestQueueStatsModel:
    def test_construction(self):
        qs = QueueStats(
            pending_messages=2,
            delivered_messages=8,
            acknowledged_messages=6,
            processing_rate=1.2,
            average_wait_time=0.5,
        )
        assert qs.processing_rate == 1.2
        assert qs.average_wait_time == 0.5


# ---------------------------------------------------------------------------
# api_response helper tests
# ---------------------------------------------------------------------------


class TestApiResponse:
    def test_success_response(self):
        result = api_response(data={"key": "val"})
        assert result["success"] is True
        assert result["data"] == {"key": "val"}
        assert result["error"] is None
        assert "timestamp" in result

    def test_error_response_with_code_and_message(self):
        result = api_response(error_code="NOT_FOUND", error_message="Thing not found")
        assert result["success"] is False
        assert result["data"] is None
        assert result["error"]["code"] == "NOT_FOUND"
        assert result["error"]["message"] == "Thing not found"

    def test_error_response_code_only(self):
        result = api_response(error_code="BOOM")
        assert result["success"] is False
        assert result["error"]["message"] == "An error occurred"

    def test_error_response_message_only(self):
        result = api_response(error_message="Something broke")
        assert result["success"] is False
        assert result["error"]["code"] == "UNKNOWN_ERROR"

    def test_success_response_no_data(self):
        result = api_response()
        assert result["success"] is True
        assert result["data"] is None

    def test_timestamp_is_iso_format(self):
        result = api_response(data={})
        ts = result["timestamp"]
        # Should parse without error
        datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_timestamp_is_utc(self):
        result = api_response(data={"x": 1})
        ts = result["timestamp"]
        assert "+00:00" in ts or ts.endswith("Z")

    def test_error_takes_priority_over_data(self):
        result = api_response(data={"x": 1}, error_code="ERR")
        assert result["success"] is False
        assert result["data"] is None


# ---------------------------------------------------------------------------
# Module-level factory functions
# ---------------------------------------------------------------------------


class TestGetMessageQueue:
    def test_returns_queue_from_service(self):
        from forge_harness.webhook_server.api.messages import get_message_queue

        mock_q = _make_queue()
        with patch(
            "forge_harness.webhook_server.services.message_queue.get_message_queue",
            return_value=mock_q,
        ):
            result = get_message_queue()
            assert result is mock_q


class TestGetAgentRegistry:
    def test_returns_registry_from_service(self):
        from forge_harness.webhook_server.api.messages import get_agent_registry

        mock_reg = _make_registry()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_agent_registry",
            return_value=mock_reg,
        ):
            result = get_agent_registry()
            assert result is mock_reg


class TestGetEventBus:
    def test_returns_bus_from_service(self):
        from forge_harness.webhook_server.api.messages import get_event_bus

        mock_bus = _make_event_bus()
        with patch(
            "forge_harness.webhook_server.services.event_bus.get_event_bus",
            return_value=mock_bus,
        ):
            result = get_event_bus()
            assert result is mock_bus


# ---------------------------------------------------------------------------
# Endpoint tests: send_agent_message_v2
# ---------------------------------------------------------------------------


class TestSendAgentMessageV2:
    """POST /api/agents/{agent_id}/messages/send"""

    def test_send_success_no_tmux(self):
        """Send to agent without tmux session — status=pending."""
        agent = _make_agent(agent_id="agent-001", tmux_session=None)
        reg = _make_registry(agent=agent, message_id="msg-001")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, queue=_make_queue(), event_bus=bus):
            client = TestClient(app)
            resp = client.post(
                "/api/agents/agent-001/messages/send",
                json={"type": "instruction", "content": "run tests"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["message_id"] == "msg-001"
        assert data["recipient_id"] == "agent-001"
        assert data["status"] == "pending"
        assert data["acknowledged_at"] is None

    def test_send_success_with_tmux(self):
        """Send to agent with tmux session — status=delivered."""
        agent = _make_agent(agent_id="agent-002", tmux_session="forge:worker")
        reg = _make_registry(agent=agent, message_id="msg-002")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, queue=_make_queue(), event_bus=bus):
            with patch(
                "forge_harness.fleet.verification.send_to_tmux",
                new=AsyncMock(),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/api/agents/agent-002/messages/send",
                    json={"type": "instruction", "content": "deploy now"},
                )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "delivered"

    def test_send_agent_not_found_returns_404(self):
        """When registry returns (None, None), endpoint raises 404."""
        reg = _make_registry(agent=None, message_id=None)
        reg.send_message = Mock(return_value=(None, None))

        app = _build_app()
        with _patch_deps(registry=reg, queue=_make_queue(), event_bus=_make_event_bus()):
            client = TestClient(app)
            resp = client.post(
                "/api/agents/unknown-agent/messages/send",
                json={"type": "task", "content": "hello"},
            )

        assert resp.status_code == 404
        assert "Agent not found" in resp.json()["detail"]

    def test_send_emits_sse_event(self):
        """Event bus publish is called once after a successful send."""
        agent = _make_agent(agent_id="agent-003", tmux_session=None)
        reg = _make_registry(agent=agent, message_id="msg-003")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, queue=_make_queue(), event_bus=bus) as (_, __, b):
            client = TestClient(app)
            client.post(
                "/api/agents/agent-003/messages/send",
                json={"type": "notify", "content": "hey"},
            )

        b.publish.assert_called_once()
        call_args = b.publish.call_args
        assert call_args[0][0] == "message.sent"

    def test_send_sse_event_payload(self):
        """SSE event payload contains expected keys."""
        agent = _make_agent(agent_id="agent-010")
        reg = _make_registry(agent=agent, message_id="msg-010")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, event_bus=bus) as (_, __, b):
            client = TestClient(app)
            client.post(
                "/api/agents/agent-010/messages/send",
                json={"type": "t", "content": "c", "sender_id": "sender-x"},
            )

        payload = b.publish.call_args[0][1]
        assert payload["message_id"] == "msg-010"
        assert payload["recipient_id"] == "agent-010"
        assert payload["sender_id"] == "sender-x"
        assert payload["type"] == "t"

    def test_send_with_custom_sender_and_priority(self):
        """Sender ID and priority in body are forwarded correctly."""
        agent = _make_agent(agent_id="agent-004")
        reg = _make_registry(agent=agent, message_id="msg-004")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, queue=_make_queue(), event_bus=bus) as (r, _, __):
            client = TestClient(app)
            client.post(
                "/api/agents/agent-004/messages/send",
                json={
                    "type": "handoff",
                    "content": "take over",
                    "sender_id": "orchestrator",
                    "priority": 3,
                },
            )

        r.send_message.assert_called_once()
        kwargs = r.send_message.call_args[1]
        assert kwargs["sender_id"] == "orchestrator"

    def test_send_tmux_failure_does_not_fail_request(self):
        """If tmux delivery raises, the HTTP response still succeeds (warning only)."""
        agent = _make_agent(agent_id="agent-005", tmux_session="forge:broken")
        reg = _make_registry(agent=agent, message_id="msg-005")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, queue=_make_queue(), event_bus=bus):
            with patch(
                "forge_harness.fleet.verification.send_to_tmux",
                new=AsyncMock(side_effect=RuntimeError("tmux session gone")),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/api/agents/agent-005/messages/send",
                    json={"type": "task", "content": "do it"},
                )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_send_missing_required_fields_returns_422(self):
        """Missing type and content produce 422 Unprocessable Entity."""
        app = _build_app()
        with _patch_deps():
            client = TestClient(app)
            resp = client.post("/api/agents/a/messages/send", json={})

        assert resp.status_code == 422

    def test_send_invalid_priority_returns_422(self):
        """Priority outside [0-3] produces 422."""
        app = _build_app()
        with _patch_deps():
            client = TestClient(app)
            resp = client.post(
                "/api/agents/a/messages/send",
                json={"type": "t", "content": "c", "priority": 99},
            )

        assert resp.status_code == 422

    def test_send_response_includes_sender_id(self):
        """Response data.sender_id reflects the request's sender_id."""
        agent = _make_agent(agent_id="agent-006")
        reg = _make_registry(agent=agent, message_id="msg-006")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, event_bus=bus):
            client = TestClient(app)
            resp = client.post(
                "/api/agents/agent-006/messages/send",
                json={"type": "t", "content": "c", "sender_id": "alpha"},
            )

        assert resp.json()["data"]["sender_id"] == "alpha"

    def test_send_metadata_forwarded(self):
        """Metadata dict in the request body is forwarded to the registry."""
        agent = _make_agent(agent_id="agent-007")
        reg = _make_registry(agent=agent, message_id="msg-007")

        app = _build_app()
        with _patch_deps(registry=reg) as (r, _, __):
            client = TestClient(app)
            client.post(
                "/api/agents/agent-007/messages/send",
                json={"type": "t", "content": "c", "metadata": {"trace_id": "xyz"}},
            )

        call_kwargs = r.send_message.call_args[1]
        assert call_kwargs["message"]["metadata"] == {"trace_id": "xyz"}

    def test_send_use_queue_true(self):
        """Registry.send_message is always called with use_queue=True."""
        agent = _make_agent()
        reg = _make_registry(agent=agent, message_id="m")

        app = _build_app()
        with _patch_deps(registry=reg) as (r, _, __):
            client = TestClient(app)
            client.post(
                "/api/agents/x/messages/send",
                json={"type": "t", "content": "c"},
            )

        assert r.send_message.call_args[1]["use_queue"] is True

    def test_send_default_sender_id_is_system(self):
        """When no sender_id provided, defaults to 'system'."""
        agent = _make_agent()
        reg = _make_registry(agent=agent, message_id="m")

        app = _build_app()
        with _patch_deps(registry=reg) as (r, _, __):
            client = TestClient(app)
            client.post(
                "/api/agents/x/messages/send",
                json={"type": "t", "content": "c"},
            )

        assert r.send_message.call_args[1]["sender_id"] == "system"

    def test_send_registry_called_with_correct_agent_id(self):
        """Registry.send_message receives the agent_id from the URL path."""
        agent = _make_agent(agent_id="target-agent")
        reg = _make_registry(agent=agent, message_id="m")

        app = _build_app()
        with _patch_deps(registry=reg) as (r, _, __):
            client = TestClient(app)
            client.post(
                "/api/agents/target-agent/messages/send",
                json={"type": "t", "content": "c"},
            )

        assert r.send_message.call_args[1]["agent_id"] == "target-agent"


# ---------------------------------------------------------------------------
# Endpoint tests: get_pending_messages
# ---------------------------------------------------------------------------


class TestGetPendingMessages:
    """GET /api/agents/{agent_id}/messages/pending"""

    def test_returns_pending_messages(self):
        msgs = [
            _make_queued_message("m1", content="first"),
            _make_queued_message("m2", content="second"),
        ]
        queue = _make_queue(pending=msgs)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/agent-001/messages/pending")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["count"] == 2
        assert len(body["data"]["messages"]) == 2

    def test_mark_delivered_default_true(self):
        """By default mark_delivered=True is passed to the queue."""
        queue = _make_queue(pending=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/agents/agent-001/messages/pending")

        q.get_pending.assert_called_once_with("agent-001", mark_delivered=True)

    def test_mark_delivered_false(self):
        """?mark_delivered=false is forwarded."""
        queue = _make_queue(pending=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/agents/agent-001/messages/pending?mark_delivered=false")

        q.get_pending.assert_called_once_with("agent-001", mark_delivered=False)

    def test_empty_inbox(self):
        queue = _make_queue(pending=[])
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/no-messages/messages/pending")

        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0

    def test_messages_serialised_via_to_dict(self):
        """Each message in the response comes from msg.to_dict()."""
        msg = _make_queued_message("m1", content="payload data")
        queue = _make_queue(pending=[msg])
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/agent-001/messages/pending")

        returned_msgs = resp.json()["data"]["messages"]
        assert returned_msgs[0]["content"] == "payload data"
        msg.to_dict.assert_called_once()

    def test_agent_id_forwarded_to_queue(self):
        """The agent_id from the URL path is passed to get_pending."""
        queue = _make_queue(pending=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/agents/my-special-agent/messages/pending")

        q.get_pending.assert_called_once_with("my-special-agent", mark_delivered=True)


# ---------------------------------------------------------------------------
# Endpoint tests: acknowledge_message
# ---------------------------------------------------------------------------


class TestAcknowledgeMessage:
    """POST /api/agents/{agent_id}/messages/{message_id}/ack"""

    def test_successful_ack(self):
        acked_msg = _make_queued_message(
            "msg-001",
            status="acknowledged",
            acknowledged_at=datetime.now(UTC),
        )
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus):
            client = TestClient(app)
            resp = client.post("/api/agents/agent-001/messages/msg-001/ack")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["acknowledged"] is True

    def test_ack_message_not_found_returns_404(self):
        queue = _make_queue(ack_result=None)
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.post("/api/agents/agent-001/messages/nonexistent/ack")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_ack_emits_sse_event(self):
        acked_msg = _make_queued_message(
            "msg-002",
            sender_id="sender-1",
            acknowledged_at=datetime.now(UTC),
        )
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus) as (_, __, b):
            client = TestClient(app)
            client.post("/api/agents/agent-001/messages/msg-002/ack")

        b.publish.assert_called_once()
        event_type = b.publish.call_args[0][0]
        assert event_type == "message.acknowledged"

    def test_ack_event_payload_contains_message_id(self):
        acked_msg = _make_queued_message("msg-003", acknowledged_at=datetime.now(UTC))
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus) as (_, __, b):
            client = TestClient(app)
            client.post("/api/agents/agent-001/messages/msg-003/ack")

        payload = b.publish.call_args[0][1]
        assert payload["message_id"] == "msg-003"
        assert payload["recipient_id"] == "agent-001"

    def test_ack_response_contains_message_dict(self):
        acked_msg = _make_queued_message("msg-004", content="original content")
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus):
            client = TestClient(app)
            resp = client.post("/api/agents/agent-001/messages/msg-004/ack")

        message = resp.json()["data"]["message"]
        assert message["content"] == "original content"
        acked_msg.to_dict.assert_called_once()

    def test_ack_with_null_acknowledged_at(self):
        """When acknowledged_at is None, the event payload handles it gracefully."""
        acked_msg = _make_queued_message("msg-005", acknowledged_at=None)
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus) as (_, __, b):
            client = TestClient(app)
            resp = client.post("/api/agents/agent-001/messages/msg-005/ack")

        assert resp.status_code == 200
        payload = b.publish.call_args[0][1]
        assert payload["acknowledged_at"] is None

    def test_ack_queue_called_with_correct_args(self):
        acked_msg = _make_queued_message("msg-006")
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus) as (_, q, __):
            client = TestClient(app)
            client.post("/api/agents/my-agent/messages/msg-006/ack")

        q.acknowledge.assert_called_once_with("msg-006", "my-agent")

    def test_ack_event_sender_id_in_payload(self):
        acked_msg = _make_queued_message("msg-007", sender_id="source-agent")
        acked_msg.acknowledged_at = None
        queue = _make_queue(ack_result=acked_msg)
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(queue=queue, event_bus=bus) as (_, __, b):
            client = TestClient(app)
            client.post("/api/agents/recipient/messages/msg-007/ack")

        payload = b.publish.call_args[0][1]
        assert payload["sender_id"] == "source-agent"


# ---------------------------------------------------------------------------
# Endpoint tests: get_message_status
# ---------------------------------------------------------------------------


class TestGetMessageStatus:
    """GET /api/messages/{message_id}/status

    Note: the source has a bug on line 291 — it calls api_response(content=...)
    instead of api_response(data=...).  When a message IS found the endpoint
    raises a TypeError (500).  We test the observable behaviour rather than
    masking the defect, so:
      - found    → 500 (server error due to source bug)
      - not-found → 404 (explicit HTTPException before the buggy line)
    """

    def test_found_message_raises_server_error(self):
        """Source bug: api_response(content=...) is invalid → 500."""
        msg = _make_queued_message("msg-status-01", status="delivered")
        queue = _make_queue(status_result=msg)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/messages/msg-status-01/status")

        assert resp.status_code == 500

    def test_message_not_found_returns_404(self):
        queue = _make_queue(status_result=None)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/messages/does-not-exist/status")

        assert resp.status_code == 404
        assert "Message not found" in resp.json()["detail"]

    def test_queue_called_with_message_id_when_not_found(self):
        """Even for missing messages the queue.get_status is called correctly."""
        queue = _make_queue(status_result=None)

        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/messages/msg-77/status")

        q.get_status.assert_called_once_with("msg-77")

    def test_not_found_detail_text(self):
        queue = _make_queue(status_result=None)
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/messages/ghost/status")

        assert resp.json()["detail"] == "Message not found"


# ---------------------------------------------------------------------------
# Endpoint tests: get_message_queue_stats
# ---------------------------------------------------------------------------


class TestGetMessageQueueStats:
    """GET /api/messages/queue/stats"""

    def test_returns_stats(self):
        stats_data = {
            "pending_messages": 7,
            "delivered_messages": 20,
            "acknowledged_messages": 15,
            "processing_rate": 2.5,
            "average_wait_time": 1.1,
            "total_processed": 35,
            "queue_age_seconds": 3600,
        }
        queue = _make_queue(stats=stats_data)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/messages/queue/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["pending_messages"] == 7
        assert data["delivered_messages"] == 20
        assert data["acknowledged_messages"] == 15
        assert data["processing_rate"] == 2.5
        assert data["average_wait_time"] == 1.1
        assert data["total_processed"] == 35
        assert data["queue_age_seconds"] == 3600

    def test_missing_keys_default_to_zero(self):
        """If queue stats omit keys, defaults of 0/0.0 are used."""
        queue = _make_queue(stats={})

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/messages/queue/stats")

        data = resp.json()["data"]
        assert data["pending_messages"] == 0
        assert data["delivered_messages"] == 0
        assert data["acknowledged_messages"] == 0
        assert data["processing_rate"] == 0.0
        assert data["average_wait_time"] == 0.0
        assert data["total_processed"] == 0
        assert data["queue_age_seconds"] == 0

    def test_queue_get_stats_called(self):
        queue = _make_queue()
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/messages/queue/stats")

        q.get_stats.assert_called_once()

    def test_processing_rate_and_wait_time_extracted(self):
        """processing_rate and average_wait_time are extracted from nested stats."""
        queue = _make_queue(
            stats={
                "processing_rate": 3.14,
                "average_wait_time": 9.9,
            }
        )
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/messages/queue/stats")

        data = resp.json()["data"]
        assert data["processing_rate"] == 3.14
        assert data["average_wait_time"] == 9.9


# ---------------------------------------------------------------------------
# Endpoint tests: get_agent_conversation
# ---------------------------------------------------------------------------


class TestGetAgentConversation:
    """GET /api/agents/{agent1_id}/messages/conversation/{agent2_id}"""

    def test_returns_conversation(self):
        msgs = [
            _make_queued_message("c1", sender_id="a1", recipient_id="a2"),
            _make_queued_message("c2", sender_id="a2", recipient_id="a1"),
        ]
        queue = _make_queue(conversation=msgs)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/a1/messages/conversation/a2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["count"] == 2
        assert len(body["data"]["messages"]) == 2

    def test_empty_conversation(self):
        queue = _make_queue(conversation=[])
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/a1/messages/conversation/a2")

        assert resp.json()["data"]["count"] == 0

    def test_default_limit_is_50(self):
        queue = _make_queue(conversation=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/agents/a1/messages/conversation/a2")

        q.get_conversation.assert_called_once_with("a1", "a2", limit=50)

    def test_custom_limit(self):
        queue = _make_queue(conversation=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/agents/a1/messages/conversation/a2?limit=10")

        q.get_conversation.assert_called_once_with("a1", "a2", limit=10)

    def test_limit_max_1000_exceeded_returns_422(self):
        """limit > 1000 is rejected by FastAPI Query validation."""
        queue = _make_queue(conversation=[])
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/a1/messages/conversation/a2?limit=9999")

        assert resp.status_code == 422

    def test_messages_use_to_dict(self):
        msg = _make_queued_message("c1", content="convo message")
        queue = _make_queue(conversation=[msg])

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/a1/messages/conversation/a2")

        returned = resp.json()["data"]["messages"]
        assert returned[0]["content"] == "convo message"
        msg.to_dict.assert_called_once()

    def test_different_agent_pair(self):
        """Conversation endpoint passes the correct agent IDs."""
        queue = _make_queue(conversation=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            client.get("/api/agents/forge-orch/messages/conversation/kimi-worker")

        q.get_conversation.assert_called_once_with("forge-orch", "kimi-worker", limit=50)

    def test_conversation_count_matches_message_list(self):
        msgs = [_make_queued_message(f"m{i}") for i in range(5)]
        queue = _make_queue(conversation=msgs)
        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/a1/messages/conversation/a2")

        data = resp.json()["data"]
        assert data["count"] == 5
        assert len(data["messages"]) == 5


# ---------------------------------------------------------------------------
# Auth tests: verify that the auth dependency is wired
# ---------------------------------------------------------------------------


class TestAuthEnforcement:
    """Ensure that endpoints respect the auth dependency.

    When auth is NOT bypassed (no app.state.auth_config), all endpoints
    must return 401.
    """

    def _strict_app(self) -> FastAPI:
        """Build an app where auth is not bypassed."""
        app = FastAPI()
        # No auth_config set on state → triggers all-methods-fail → 401
        app.include_router(router)
        return app

    def test_send_without_auth_401(self):
        app = self._strict_app()
        with _patch_deps():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/agents/a/messages/send",
                json={"type": "t", "content": "c"},
            )

        assert resp.status_code == 401

    def test_pending_without_auth_401(self):
        app = self._strict_app()
        with _patch_deps():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/agents/a/messages/pending")

        assert resp.status_code == 401

    def test_ack_without_auth_401(self):
        app = self._strict_app()
        with _patch_deps():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/agents/a/messages/msg-x/ack")

        assert resp.status_code == 401

    def test_status_without_auth_401(self):
        app = self._strict_app()
        with _patch_deps():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/messages/msg-x/status")

        assert resp.status_code == 401

    def test_stats_without_auth_401(self):
        app = self._strict_app()
        with _patch_deps():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/messages/queue/stats")

        assert resp.status_code == 401

    def test_conversation_without_auth_401(self):
        app = self._strict_app()
        with _patch_deps():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/agents/a1/messages/conversation/a2")

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Edge-case / integration tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_send_with_empty_metadata(self):
        agent = _make_agent()
        reg = _make_registry(agent=agent, message_id="m-empty-meta")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, event_bus=bus):
            client = TestClient(app)
            resp = client.post(
                "/api/agents/a/messages/send",
                json={"type": "t", "content": "c", "metadata": {}},
            )

        assert resp.status_code == 200

    def test_pending_with_high_priority_messages(self):
        """High-priority messages in the response are handled correctly."""
        msgs = [
            _make_queued_message("m1", priority=3, content="urgent"),
            _make_queued_message("m2", priority=1, content="normal"),
        ]
        queue = _make_queue(pending=msgs)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.get("/api/agents/a/messages/pending")

        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 2

    def test_conversation_limit_zero(self):
        """limit=0 is valid (no ge constraint) and is forwarded correctly."""
        queue = _make_queue(conversation=[])
        app = _build_app()
        with _patch_deps(queue=queue) as (_, q, __):
            client = TestClient(app)
            resp = client.get("/api/agents/a1/messages/conversation/a2?limit=0")

        assert resp.status_code == 200
        q.get_conversation.assert_called_once_with("a1", "a2", limit=0)

    def test_ack_wrong_agent_returns_404(self):
        """If queue.acknowledge returns None (wrong agent), endpoint returns 404."""
        queue = _make_queue(ack_result=None)
        queue.acknowledge = Mock(return_value=None)

        app = _build_app()
        with _patch_deps(queue=queue):
            client = TestClient(app)
            resp = client.post("/api/agents/wrong-agent/messages/msg-001/ack")

        assert resp.status_code == 404

    def test_send_tmux_import_error_caught(self):
        """ImportError from send_to_tmux call is caught gracefully."""
        agent = _make_agent(tmux_session="forge:session")
        reg = _make_registry(agent=agent, message_id="m")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, event_bus=bus):
            with patch(
                "forge_harness.fleet.verification.send_to_tmux",
                new=AsyncMock(side_effect=ImportError("no module")),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/api/agents/x/messages/send",
                    json={"type": "t", "content": "c"},
                )

        assert resp.status_code == 200

    def test_send_priority_boundary_low(self):
        """Priority=0 (lowest) is accepted."""
        agent = _make_agent()
        reg = _make_registry(agent=agent, message_id="m")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, event_bus=bus):
            client = TestClient(app)
            resp = client.post(
                "/api/agents/x/messages/send",
                json={"type": "t", "content": "c", "priority": 0},
            )

        assert resp.status_code == 200

    def test_send_priority_boundary_high(self):
        """Priority=3 (urgent) is accepted."""
        agent = _make_agent()
        reg = _make_registry(agent=agent, message_id="m")
        bus = _make_event_bus()

        app = _build_app()
        with _patch_deps(registry=reg, event_bus=bus):
            client = TestClient(app)
            resp = client.post(
                "/api/agents/x/messages/send",
                json={"type": "t", "content": "c", "priority": 3},
            )

        assert resp.status_code == 200

    def test_stats_response_is_success(self):
        app = _build_app()
        with _patch_deps():
            client = TestClient(app)
            resp = client.get("/api/messages/queue/stats")

        assert resp.json()["success"] is True

    def test_stats_response_error_field_is_none(self):
        app = _build_app()
        with _patch_deps():
            client = TestClient(app)
            resp = client.get("/api/messages/queue/stats")

        assert resp.json()["error"] is None

    def test_pending_response_error_field_is_none(self):
        app = _build_app()
        with _patch_deps():
            client = TestClient(app)
            resp = client.get("/api/agents/a/messages/pending")

        assert resp.json()["error"] is None

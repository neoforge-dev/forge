"""Message Queue API Endpoints

Agent-to-agent messaging API extracted from webhook_server_main.py.
Provides comprehensive message management with tracking, delivery, and conversation history.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from forge_harness.webhook_server.core.dependencies import verify_auth

router = APIRouter()


class MessageRequest(BaseModel):
    """Message request for sending messages."""

    type: str = Field(..., description="Message type")
    content: str = Field(..., description="Message content")
    sender_id: str = Field(default="system", description="ID of sending agent")
    priority: int = Field(
        default=1, ge=0, le=3, description="Message priority (0=low, 1=normal, 2=high, 3=urgent)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional message metadata"
    )


class AgentMessageRequestV2(BaseModel):
    """Enhanced message request with sender support (v2 endpoint)."""

    type: str = Field(..., description="Message type")
    content: str = Field(..., description="Message content")
    sender_id: str = Field(default="system", description="ID of sending agent")
    priority: int = Field(
        default=1, ge=0, le=3, description="Message priority (0=low, 1=normal, 2=high, 3=urgent)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional message metadata"
    )


class MessageResponse(BaseModel):
    """Message response data."""

    messages: list[dict[str, Any]] = Field(description="List of messages")
    count: int = Field(description="Number of messages returned")


class AckResponse(BaseModel):
    """Message acknowledgment response."""

    acknowledged: bool = Field(..., description="Whether message was acknowledged")
    message: dict[str, Any] = Field(..., description="Acknowledged message details")


class StatusResponse(BaseModel):
    """Message status response."""

    status: str = Field(..., description="Message status")
    sender_id: str = Field(..., description="Message sender ID")
    recipient_id: str = Field(..., description="Message recipient ID")
    created_at: str = Field(..., description="Message creation timestamp")
    delivered_at: str | None = Field(None, description="Message delivery timestamp")
    acknowledged_at: str | None = Field(None, description="Message acknowledgment timestamp")


class StatsResponse(BaseModel):
    """Message queue statistics."""

    total_pending: int = Field(..., description="Total pending messages")
    total_delivered: int = Field(..., description="Total delivered messages")
    total_acknowledged: int = Field(..., description="Total acknowledged messages")


class ConversationResponse(BaseModel):
    """Conversation between two agents."""

    messages: list[dict[str, Any]] = Field(description="Messages in conversation")
    count: int = Field(description="Number of messages in conversation")


class QueueStats(BaseModel):
    """Queue statistics for monitoring."""

    pending_messages: int = Field(..., description="Number of pending messages")
    delivered_messages: int = Field(..., description="Number of delivered messages")
    acknowledged_messages: int = Field(..., description="Number of acknowledged messages")
    processing_rate: float = Field(..., description="Messages processed per second")
    average_wait_time: float = Field(..., description="Average wait time in seconds")


def api_response(
    data: Any = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    """Create standardized API response."""
    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def get_message_queue():
    """Get message queue singleton."""
    from forge_harness.webhook_server.services.message_queue import (
        get_message_queue as _get_message_queue,
    )

    return _get_message_queue()


def get_agent_registry():
    """Get global agent registry."""
    from forge_harness.webhook_server.services.agent_registry import (
        get_agent_registry as _get_agent_registry,
    )

    return _get_agent_registry()


def get_event_bus():
    """Get global event bus."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus as _get_event_bus

    return _get_event_bus()


# Message Queue Endpoints


@router.post("/api/agents/{agent_id}/messages/send")
async def send_agent_message_v2(
    agent_id: str,
    body: AgentMessageRequestV2,
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Send message to an agent with full tracking (v2 endpoint).

    Returns message_id for tracking delivery and acknowledgment.
    """
    message = {
        "type": body.type,
        "content": body.content,
        "priority": body.priority,
        "metadata": body.metadata,
    }

    agent_registry = get_agent_registry()
    event_bus = get_event_bus()

    agent, message_id = agent_registry.send_message(
        agent_id=agent_id,
        message=message,
        sender_id=body.sender_id,
        use_queue=True,
    )

    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Send to tmux if agent has tmux_session (DispatchClient pattern: literal + Enter)
    tmux_target = getattr(agent, "tmux_session", None)
    if tmux_target:
        try:
            from forge_harness.fleet.verification import send_to_tmux

            await send_to_tmux(tmux_target, body.content)
        except Exception as e:
            # Log warning but don't fail the request
            from forge_harness.logging_config import get_logger

            logger = get_logger(__name__)
            logger.warning(f"V2 message sent but tmux delivery failed for {agent_id}: {e}")

    # Emit SSE event for message
    await event_bus.publish(
        "message.sent",
        {
            "message_id": message_id,
            "recipient_id": agent_id,
            "sender_id": body.sender_id,
            "type": body.type,
            "priority": body.priority,
        },
    )

    return api_response(
        data={
            "message_id": message_id,
            "recipient_id": agent_id,
            "sender_id": body.sender_id,
            "status": "delivered" if tmux_target else "pending",
            "acknowledged_at": None,
        }
    )


@router.get("/api/agents/{agent_id}/messages/pending")
async def get_pending_messages(
    agent_id: str,
    mark_delivered: bool = True,
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Get pending messages for an agent.

    Args:
        agent_id: Agent ID to get messages for
        mark_delivered: If true, mark messages as delivered (default: true)
    """
    queue = get_message_queue()
    messages = queue.get_pending(agent_id, mark_delivered=mark_delivered)

    return api_response(
        data={
            "messages": [m.to_dict() for m in messages],
            "count": len(messages),
        }
    )


@router.post("/api/agents/{agent_id}/messages/{message_id}/ack")
async def acknowledge_message(
    agent_id: str,
    message_id: str,
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Acknowledge receipt and processing of a message.

    Must be called by the recipient agent.
    """
    queue = get_message_queue()
    message = queue.acknowledge(message_id, agent_id)

    if message is None:
        raise HTTPException(
            status_code=404, detail="Message not found or agent not authorized to acknowledge"
        )

    # Emit SSE event for acknowledgment
    event_bus = get_event_bus()

    await event_bus.publish(
        "message.acknowledged",
        {
            "message_id": message_id,
            "recipient_id": agent_id,
            "sender_id": message.sender_id,
            "acknowledged_at": message.acknowledged_at.isoformat()
            if message.acknowledged_at
            else None,
        },
    )

    return api_response(
        data={
            "acknowledged": True,
            "message": message.to_dict(),
        }
    )


@router.get("/api/messages/{message_id}/status")
async def get_message_status(
    message_id: str,
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Get status of a message by ID."""
    queue = get_message_queue()
    message = queue.get_status(message_id)

    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    return api_response(content=message.to_dict())


@router.get("/api/messages/queue/stats")
async def get_message_queue_stats(
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Get message queue statistics."""
    queue = get_message_queue()
    stats = queue.get_stats()

    # Calculate additional metrics
    processing_rate = stats.get("processing_rate", 0.0)
    average_wait_time = stats.get("average_wait_time", 0.0)

    return api_response(
        data={
            "pending_messages": stats.get("pending_messages", 0),
            "delivered_messages": stats.get("delivered_messages", 0),
            "acknowledged_messages": stats.get("acknowledged_messages", 0),
            "processing_rate": processing_rate,
            "average_wait_time": average_wait_time,
            "total_processed": stats.get("total_processed", 0),
            "queue_age_seconds": stats.get("queue_age_seconds", 0),
        }
    )


@router.get("/api/agents/{agent1_id}/messages/conversation/{agent2_id}")
async def get_agent_conversation(
    agent1_id: str,
    agent2_id: str,
    limit: int = Query(default=50, le=1000, description="Maximum number of messages to return"),
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Get message history between two agents."""
    queue = get_message_queue()
    messages = queue.get_conversation(agent1_id, agent2_id, limit=limit)

    return api_response(
        data={
            "messages": [m.to_dict() for m in messages],
            "count": len(messages),
        }
    )


# NOTE: POST /api/agents/{agent_id}/message is defined in agents.py (canonical).
# Removed duplicate from here (CP-1002/D-1). The agents.py version uses DispatchClient
# for tmux delivery. Use POST /api/agents/{agent_id}/messages/send for queue-based messaging.

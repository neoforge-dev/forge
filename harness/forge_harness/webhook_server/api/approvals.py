"""Approval Queue API Endpoints

REST API endpoints for approval queue management.
Extracted from webhook_server_main.py for better modularity.
"""

from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.services.audit import get_audit_logger

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Request Models
# =============================================================================

class ApproveRequest(BaseModel):
    """Request body for approval."""

    approver: str = Field(
        ..., min_length=1, max_length=200, description="Person or system approving the request"
    )
    comment: str | None = Field(
        None, max_length=2000, description="Optional comment explaining the approval"
    )
    auto_resume: bool = True

    @field_validator("approver")
    @classmethod
    def validate_approver(cls, v: str) -> str:
        """Validate approver is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("approver cannot be empty or whitespace")
        return v.strip()


class RejectRequest(BaseModel):
    """Request body for rejection."""

    rejector: str = Field(
        ..., min_length=1, max_length=200, description="Person or system rejecting the request"
    )
    reason: str | None = Field(None, max_length=2000, description="Reason for rejection")

    @field_validator("rejector")
    @classmethod
    def validate_rejector(cls, v: str) -> str:
        """Validate rejector is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("rejector cannot be empty or whitespace")
        return v.strip()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str | None) -> str | None:
        """Validate reason is provided and not empty for rejections."""
        if v is not None and v.strip():
            return v.strip()
        return v


class BatchApproveRequest(BaseModel):
    """Request body for batch approval."""

    request_ids: list[str]
    approver: str
    comment: str | None = None


class BulkApproveRequest(BaseModel):
    """Request body for bulk approval."""

    ids: list[str]
    notes: str | None = None


class BulkRejectRequest(BaseModel):
    """Request body for bulk rejection."""

    ids: list[str]
    reason: str


# =============================================================================
# Helper Functions
# =============================================================================

def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
    """Create a standardized API response."""
    from datetime import UTC, datetime
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


async def get_approval_handler():
    """Get or create the approval handler."""
    from forge_harness.webhook_server.handlers.approval_handler import get_approval_handler
    return get_approval_handler()


async def get_webhook_handler():
    """Get or create the webhook handler."""
    from forge_harness.webhook_server.handlers.webhook_handler import WebhookHandler
    # Note: This might not be the same instance as in create_app but for logic it should be fine
    return WebhookHandler(notification_harness=None)


async def get_event_bus():
    """Get the event bus singleton."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus as _get_event_bus
    return _get_event_bus()


def _extract_request_context(request: Request | None = None) -> tuple[str | None, str | None]:
    """Extract IP address and user agent from FastAPI Request."""
    if request is None:
        return None, None
    # Get client IP (check common headers first)
    ip_address = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "").strip()
        or request.client.host if request.client else None
    )
    user_agent = request.headers.get("User-Agent")
    return ip_address, user_agent


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/api/approvals")
async def list_approvals(
    domain: str | None = Query(None, description="Filter by domain"),
    project: str | None = Query(None, description="Filter by project"),
    priority: str | None = Query(None, description="Filter by priority (low, medium, high, critical)"),
    tier: str | None = Query(None, description="Filter by tier (watch, phone, desktop)"),
    status: str | None = Query(None, description="Filter by status (pending, approved, rejected)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
    _=Depends(require_permission("approvals:read")),
):
    """List approval requests with optional filtering."""
    handler = await get_approval_handler()
    approvals = await handler.list_pending(
        domain=domain,
        project=project,
        priority=priority,
        tier=tier,
        status=status,
        limit=limit,
    )
    return JSONResponse(
        content=api_response(
            {
                "approvals": approvals,
                "count": len(approvals),
            }
        )
    )


@router.get("/api/approvals/count")
async def get_approval_count(
    domain: str | None = Query(None, description="Filter by domain"),
    project: str | None = Query(None, description="Filter by project"),
    priority: str | None = Query(None, description="Filter by priority"),
    tier: str | None = Query(None, description="Filter by tier"),
    _=Depends(require_permission("approvals:read")),
):
    """Get count of pending approvals."""
    handler = await get_approval_handler()
    count = await handler.count_pending(
        domain=domain,
        project=project,
        priority=priority,
        tier=tier,
    )
    return JSONResponse(content=api_response({"count": count}))


@router.post("/api/approvals/batch-approve")
async def batch_approve_requests(
    body: BatchApproveRequest,
    _=Depends(require_permission("approvals:write")),
):
    """Batch approve multiple low-risk (watch tier) requests.

    Only requests with tier='watch' can be batch approved.
    Higher tier requests require individual approval.
    """
    handler = await get_approval_handler()
    result = await handler.batch_approve(
        request_ids=body.request_ids,
        approver=body.approver,
        comment=body.comment,
    )

    # Emit SSE events for approved requests
    event_bus = await get_event_bus()
    for request_id in result.get("approved", []):
        await event_bus.publish(
            "approval.resolved",
            {
                "request_id": request_id,
                "action": "approved",
                "approver": body.approver,
                "comment": body.comment or "Batch approved",
                "batch": True,
            },
        )
    # Audit log (one event for batch)
    approved_ids = result.get("approved", [])
    if approved_ids:
        audit_logger = get_audit_logger()
        await audit_logger.log(
            action="approval_batch_approve",
            actor={"id": body.approver, "type": "human"},
            target={"id": ",".join(approved_ids), "type": "approval_batch"},
            context={"request_ids": approved_ids, "comment": body.comment},
            source="webhook_api",
        )

    return JSONResponse(content=api_response(result))


@router.get("/api/approvals/stats")
async def get_approval_stats(
    domain: str | None = Query(None, description="Filter by domain"),
    _=Depends(require_permission("approvals:read")),
):
    """Get approval queue statistics."""
    handler = await get_approval_handler()
    stats = await handler.get_stats(domain=domain)
    if "error" in stats:
        stats = {
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "expired": 0,
            "total": 0,
            "oldest_pending_age_hours": None,
            "error": stats.get("error"),
        }
    return JSONResponse(content=stats)


@router.get("/api/approvals/{request_id}")
async def get_approval(
    request_id: str,
    _=Depends(require_permission("approvals:read")),
):
    """Get approval request details."""
    handler = await get_approval_handler()
    request = await handler.get_request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return JSONResponse(content=request)


@router.post("/api/approvals/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: ApproveRequest,
    request: Request,
    _=Depends(require_permission("approvals:write")),
):
    """Approve a request."""
    handler = await get_approval_handler()

    # Validate request_id format
    if not request_id or not request_id.strip():
        raise HTTPException(status_code=400, detail="request_id cannot be empty")

    result = await handler.approve_request(
        request_id=request_id.strip(),
        approver=body.approver,
        comment=body.comment,
        auto_resume=body.auto_resume,
    )
    if not result.get("success"):
        error_msg = result.get("error", "Unknown error")
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    # Emit SSE event
    event_bus = await get_event_bus()
    await event_bus.publish(
        "approval.resolved",
        {
            "request_id": request_id,
            "action": "approved",
            "approver": body.approver,
            "comment": body.comment,
        },
    )
    # Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_approval_decision(
        approval_id=request_id,
        decision="approved",
        actor={"id": body.approver, "type": "human"},
        context={"comment": body.comment},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return JSONResponse(content=result)


@router.post("/api/approvals/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: RejectRequest,
    request: Request,
    _=Depends(require_permission("approvals:write")),
):
    """Reject a request."""
    handler = await get_approval_handler()

    # Validate request_id format
    if not request_id or not request_id.strip():
        raise HTTPException(status_code=400, detail="request_id cannot be empty")

    # Validate reason is provided for rejections
    if not body.reason or not body.reason.strip():
        logger.warning(f"Rejection of {request_id} by {body.rejector} has no reason provided")

    result = await handler.reject_request(
        request_id=request_id.strip(),
        rejector=body.rejector,
        reason=body.reason,
    )
    if not result.get("success"):
        error_msg = result.get("error", "Unknown error")
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    # Emit SSE event
    event_bus = await get_event_bus()
    await event_bus.publish(
        "approval.resolved",
        {
            "request_id": request_id,
            "action": "rejected",
            "rejector": body.rejector,
            "reason": body.reason,
        },
    )
    # Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_approval_decision(
        approval_id=request_id,
        decision="rejected",
        actor={"id": body.rejector, "type": "human"},
        context={"reason": body.reason},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return JSONResponse(content=result)


@router.post("/api/approvals/bulk/approve")
async def bulk_approve_requests(
    body: BulkApproveRequest,
    _=Depends(require_permission("approvals:write")),
):
    """Bulk approve multiple approval requests."""
    handler = await get_approval_handler()
    success_count = 0
    failed_count = 0
    errors = []

    for request_id in body.ids:
        try:
            result = await handler.approve_request(
                request_id=request_id,
                approver="command-center",
                comment=body.notes,
                auto_resume=True,
            )
            if result.get("success"):
                success_count += 1
                # Emit SSE event
                event_bus = await get_event_bus()
                await event_bus.publish(
                    "approval.resolved",
                    {
                        "request_id": request_id,
                        "action": "approved",
                        "approver": "command-center",
                        "comment": body.notes,
                        "bulk": True,
                    },
                )
            else:
                failed_count += 1
                errors.append({"id": request_id, "error": result.get("error", "Unknown error")})
        except Exception as e:
            failed_count += 1
            errors.append({"id": request_id, "error": str(e)})

    # Audit log (one event for bulk approve)
    if success_count > 0:
        audit_logger = get_audit_logger()
        await audit_logger.log(
            action="approval_bulk_approve",
            actor={"id": "command-center", "type": "system"},
            target={"id": ",".join(body.ids), "type": "approval_bulk"},
            context={"approved_count": success_count, "request_ids": body.ids, "notes": body.notes},
            source="webhook_api",
        )

    return JSONResponse(
        content=api_response(
            {
                "success": success_count,
                "failed": failed_count,
                "errors": errors,
            }
        )
    )


@router.post("/api/approvals/bulk/reject")
async def bulk_reject_requests(
    body: BulkRejectRequest,
    _=Depends(require_permission("approvals:write")),
):
    """Bulk reject multiple approval requests."""
    handler = await get_approval_handler()
    success_count = 0
    failed_count = 0
    errors = []

    for request_id in body.ids:
        try:
            result = await handler.reject_request(
                request_id=request_id,
                rejector="command-center",
                reason=body.reason,
            )
            if result.get("success"):
                success_count += 1
                # Emit SSE event
                event_bus = await get_event_bus()
                await event_bus.publish(
                    "approval.resolved",
                    {
                        "request_id": request_id,
                        "action": "rejected",
                        "rejector": "command-center",
                        "reason": body.reason,
                        "bulk": True,
                    },
                )
            else:
                failed_count += 1
                errors.append({"id": request_id, "error": result.get("error", "Unknown error")})
        except Exception as e:
            failed_count += 1
            errors.append({"id": request_id, "error": str(e)})

    # Audit log (one event for bulk reject)
    if success_count > 0:
        audit_logger = get_audit_logger()
        await audit_logger.log(
            action="approval_bulk_reject",
            actor={"id": "command-center", "type": "system"},
            target={"id": ",".join(body.ids), "type": "approval_bulk"},
            context={"rejected_count": success_count, "request_ids": body.ids, "reason": body.reason},
            source="webhook_api",
        )

    return JSONResponse(
        content=api_response(
            {
                "success": success_count,
                "failed": failed_count,
                "errors": errors,
            }
        )
    )


@router.post("/api/webhooks/slack/approvals")
async def slack_approval_webhook(
    request: Request,
    x_slack_signature: str | None = Header(None),
    x_slack_request_timestamp: str | None = Header(None),
):
    """Handle Slack interactive message webhooks for approvals.

    This endpoint handles Slack button clicks for approve/reject actions
    and maps them to the approval queue.
    """
    handler = await get_webhook_handler()
    approval_handler = await get_approval_handler()

    body = await request.body()

    # Verify signature if secret is configured
    if handler.slack_signing_secret and x_slack_signature and x_slack_request_timestamp:
        if not handler.verify_slack_signature(
            body, x_slack_signature, x_slack_request_timestamp
        ):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        form_data = parse_qs(body.decode("utf-8"))
        if "payload" in form_data:
            payload = json.loads(form_data["payload"][0])
        else:
            raise HTTPException(status_code=400, detail="Invalid payload")

    # Parse Slack payload for approval actions
    payload_type = payload.get("type", "")
    if payload_type != "block_actions":
        return JSONResponse(content={"status": "ignored", "message": "Not a block action"})

    actions = payload.get("actions", [])
    if not actions:
        return JSONResponse(content={"status": "ignored", "message": "No actions"})

    action = actions[0]
    action_id = action.get("action_id", "")

    # Expected action_id format: "approval_{request_id}_{action}"
    parts = action_id.split("_")
    if len(parts) < 3 or parts[0] != "approval":
        return JSONResponse(content={"status": "ignored", "message": "Not an approval action"})

    request_id = parts[1]
    action_type = parts[2]

    # Get responder info
    user = payload.get("user", {})
    responder = user.get("username", user.get("name", user.get("id", "slack-user")))

    # Process the action
    if action_type == "approve":
        await approval_handler.approve_request(
            request_id=request_id,
            approver=responder,
            comment="Approved via Slack",
            auto_resume=True,
        )
    elif action_type == "reject":
        await approval_handler.reject_request(
            request_id=request_id,
            rejector=responder,
            reason="Rejected via Slack",
        )
    else:
        return JSONResponse(
            content={"status": "ignored", "message": f"Unknown action: {action_type}"}
        )

    # Return Slack response
    status = "approved" if action_type == "approve" else "rejected"
    return JSONResponse(
        content={
            "response_type": "in_channel",
            "replace_original": True,
            "text": f"Request {request_id} {status} by {responder}",
        }
    )

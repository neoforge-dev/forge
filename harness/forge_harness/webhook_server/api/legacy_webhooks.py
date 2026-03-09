"""Legacy Webhook endpoints — extracted from webhook_server_main.py.

Handles /api/webhooks/slack and /api/webhooks/github inline routes.
"""

import json

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.handlers.webhook_handler import WebhookHandler

logger = get_logger(__name__)

router = APIRouter(tags=["legacy-webhooks"])


def _get_handler(request: Request) -> WebhookHandler:
    """Get webhook handler from app.state or create default."""
    handler = getattr(request.app.state, "webhook_handler", None)
    if handler is None:
        handler = WebhookHandler(notification_harness=None)
    return handler


@router.post("/api/webhooks/slack")
async def slack_webhook(
    request: Request,
    x_slack_signature: str | None = Header(None),
    x_slack_request_timestamp: str | None = Header(None),
):
    """Handle Slack interactive message webhooks."""
    _handler = _get_handler(request)
    body = await request.body()

    # Verify signature - required when secret is configured
    if _handler.slack_signing_secret:
        if not x_slack_signature or not x_slack_request_timestamp:
            raise HTTPException(
                status_code=401,
                detail="Missing signature headers (X-Slack-Signature, X-Slack-Request-Timestamp)",
            )
        if not _handler.verify_slack_signature(
            body, x_slack_signature, x_slack_request_timestamp
        ):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload (Slack sends as form data or JSON)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        from urllib.parse import parse_qs

        form_data = parse_qs(body.decode("utf-8"))
        if "payload" in form_data:
            payload = json.loads(form_data["payload"][0])
        else:
            raise HTTPException(status_code=400, detail="Invalid payload")

    response = await _handler.handle_slack(
        payload, x_slack_signature, x_slack_request_timestamp
    )
    return JSONResponse(
        content={
            "status": response.status,
            "notification_id": response.notification_id,
            "message": response.message,
        }
    )


@router.post("/api/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str = Header(default="ping"),
):
    """Handle GitHub webhook events."""
    _handler = _get_handler(request)
    body = await request.body()

    # Verify signature - required when secret is configured
    if _handler.github_webhook_secret:
        if not x_hub_signature_256:
            raise HTTPException(
                status_code=401, detail="Missing signature header (X-Hub-Signature-256)"
            )
        if not _handler.verify_github_signature(body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Handle ping events
    if x_github_event == "ping":
        return JSONResponse(content={"status": "pong"})

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    response = await _handler.handle_github(payload, x_hub_signature_256, x_github_event)
    return JSONResponse(
        content={
            "status": response.status,
            "notification_id": response.notification_id,
            "message": response.message,
        }
    )

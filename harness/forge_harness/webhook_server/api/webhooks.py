"""Webhook API Endpoints.

Only non-canonical test/debug routes live here.
Canonical Slack/GitHub webhook handlers live in webhook_server_main.py.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/api/webhooks/test")
async def test_webhook(request: Request) -> dict[str, Any]:
    """Test endpoint for webhook debugging.

    Returns:
        dict: Echo of received payload
    """
    try:
        payload = await request.json()
        return {
            "status": "ok",
            "received": payload,
            "timestamp": datetime.now(UTC).isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now(UTC).isoformat()
        }

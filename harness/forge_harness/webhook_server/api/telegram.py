"""Telegram Gateway API — receives Telegram Bot API webhook updates.

Route:
    POST /api/gateway/telegram
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.telegram_bot import TelegramBotHandler

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Singleton handler (created lazily on first request)
# ---------------------------------------------------------------------------

_telegram_handler: TelegramBotHandler | None = None


def get_telegram_handler() -> TelegramBotHandler:
    """Return the module-level singleton TelegramBotHandler.

    Creates it from environment variables on the first call.
    Subsequent calls reuse the same instance so the HTTP client
    and authorized-chats cache are shared across requests.
    """
    global _telegram_handler
    if _telegram_handler is None:
        _telegram_handler = TelegramBotHandler.from_env()
    return _telegram_handler


def _reset_handler() -> None:
    """Reset the singleton — used in tests to inject a fresh mock."""
    global _telegram_handler
    _telegram_handler = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/api/gateway/telegram")
async def telegram_webhook(request: Request) -> JSONResponse:
    """Receive a Telegram Bot API webhook update.

    Telegram delivers POST requests here whenever the bot receives a
    message, callback query, or other event.  The endpoint:

    1. Verifies the ``X-Telegram-Bot-Api-Secret-Token`` header (if a
       webhook secret is configured).
    2. Delegates the update body to ``TelegramBotHandler.handle_update``.
    3. Returns the handler result as JSON.

    Returns:
        200 — update handled successfully.
        401 — webhook secret verification failed.
        503 — bot token / allowed-users not configured.
        500 — unexpected error during handling.

    Telegram ignores the response body; any 2xx is considered success.
    We return meaningful JSON for observability and testing.
    """
    handler = get_telegram_handler()

    # Guard: bot must be configured (token + allowed users)
    if not handler.is_configured():
        logger.warning("Telegram webhook received but bot is not configured")
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "bot_not_configured",
                "detail": (
                    "Telegram bot is not configured. "
                    "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS."
                ),
            },
        )

    # Verify webhook secret when one is set
    headers: dict[str, str] = dict(request.headers)
    if not handler.verify_webhook(headers):
        logger.warning(
            "Telegram webhook secret mismatch — rejecting request "
            "(X-Telegram-Bot-Api-Secret-Token invalid)"
        )
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "error": "unauthorized",
                "detail": "Invalid or missing X-Telegram-Bot-Api-Secret-Token.",
            },
        )

    # Parse request body
    try:
        update_body: dict[str, Any] = await request.json()
    except Exception as exc:
        logger.error("Failed to parse Telegram update body: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "invalid_json",
                "detail": "Request body must be valid JSON.",
            },
        )

    # Dispatch to handler
    try:
        result = await handler.handle_update(update_body)
        logger.debug("Telegram update handled: %s", result)
        return JSONResponse(content=result)
    except Exception as exc:
        logger.error("Unhandled error in telegram_webhook: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "internal_error",
                "detail": "An unexpected error occurred while handling the update.",
            },
        )

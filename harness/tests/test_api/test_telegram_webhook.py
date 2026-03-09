"""Tests for POST /api/gateway/telegram endpoint.

Tests cover:
- Webhook update with valid message → returns handler result
- Webhook update with callback_query → returns handler result
- Unauthorized user → returns unauthorized response from handler
- Missing bot token → returns 503
- Webhook secret verification (valid/invalid/missing)
- Command routing (/start, /help, /status)
- Malformed JSON body → returns 400
- Unexpected handler exception → returns 500
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.webhook_server.api.telegram import (
    _reset_handler,
    get_telegram_handler,
    router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app containing only the telegram router."""
    app = FastAPI()
    app.include_router(router)
    return app


def _make_handler(
    *,
    configured: bool = True,
    verify_ok: bool = True,
    handle_result: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a pre-configured mock TelegramBotHandler."""
    handler = MagicMock()
    handler.is_configured.return_value = configured
    handler.verify_webhook.return_value = verify_ok
    handler.handle_update = AsyncMock(
        return_value=handle_result if handle_result is not None else {"ok": True, "handled": True}
    )
    return handler


def _text_message_update(
    chat_id: int = 123456789,
    user_id: int = 123456789,
    text: str = "hello",
) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": user_id, "first_name": "Alice", "username": "alice"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1700000000,
            "text": text,
        },
    }


def _callback_query_update(
    chat_id: int = 123456789,
    callback_data: str = "approve:req-001",
) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cq-001",
            "from": {"id": chat_id, "first_name": "Alice"},
            "message": {
                "message_id": 10,
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": callback_data,
        },
    }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestTelegramWebhook:
    """Tests for POST /api/gateway/telegram."""

    # -- fixtures --

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset the module-level handler singleton before each test."""
        _reset_handler()
        yield
        _reset_handler()

    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.fixture
    def client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    # -- helpers --

    def _post(
        self,
        client: TestClient,
        body: dict[str, Any],
        *,
        secret: str | None = None,
    ):
        headers: dict[str, str] = {}
        if secret is not None:
            headers["X-Telegram-Bot-Api-Secret-Token"] = secret
        return client.post(
            "/api/gateway/telegram",
            content=json.dumps(body),
            headers={"Content-Type": "application/json", **headers},
        )

    # -------------------------------------------------------------------------
    # 1. Bot not configured → 503
    # -------------------------------------------------------------------------

    def test_bot_not_configured_returns_503(self, client):
        """When TelegramBotHandler is not configured, the endpoint returns 503."""
        handler = _make_handler(configured=False)
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, _text_message_update())

        assert response.status_code == 503
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "bot_not_configured"

    # -------------------------------------------------------------------------
    # 2. Webhook secret verification
    # -------------------------------------------------------------------------

    def test_valid_secret_passes_verification(self, client):
        """A request with the correct webhook secret is accepted."""
        handler = _make_handler(verify_ok=True)
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(
                client, _text_message_update(), secret="correct-secret"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        # Verify the header was forwarded to the handler
        handler.verify_webhook.assert_called_once()
        call_headers = handler.verify_webhook.call_args[0][0]
        assert call_headers.get("x-telegram-bot-api-secret-token") == "correct-secret"

    def test_invalid_secret_returns_401(self, client):
        """A request with an incorrect webhook secret is rejected with 401."""
        handler = _make_handler(verify_ok=False)
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(
                client, _text_message_update(), secret="wrong-secret"
            )

        assert response.status_code == 401
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "unauthorized"

    def test_missing_secret_header_when_secret_required_returns_401(self, client):
        """When webhook secret is required but header is absent, return 401."""
        handler = _make_handler(verify_ok=False)
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            # No secret kwarg → no header sent
            response = self._post(client, _text_message_update())

        assert response.status_code == 401
        data = response.json()
        assert data["ok"] is False

    def test_no_secret_configured_accepts_any_request(self, client):
        """When no webhook secret is set, all requests pass verification."""
        # verify_webhook returns True (no secret configured)
        handler = _make_handler(verify_ok=True)
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, _text_message_update())

        assert response.status_code == 200

    # -------------------------------------------------------------------------
    # 3. Regular text message → handler result forwarded
    # -------------------------------------------------------------------------

    def test_text_message_returns_handler_result(self, client):
        """A plain text message update is delegated to handle_update."""
        expected = {"ok": True, "handled": True, "type": "text"}
        handler = _make_handler(handle_result=expected)
        update = _text_message_update(text="hello world")

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json() == expected
        handler.handle_update.assert_awaited_once_with(update)

    # -------------------------------------------------------------------------
    # 4. Callback query → handler result forwarded
    # -------------------------------------------------------------------------

    def test_callback_query_returns_handler_result(self, client):
        """An approve/reject callback query is delegated to handle_update."""
        expected = {"ok": True, "action": "approve", "request_id": "req-001"}
        handler = _make_handler(handle_result=expected)
        update = _callback_query_update(callback_data="approve:req-001")

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json() == expected
        handler.handle_update.assert_awaited_once_with(update)

    def test_reject_callback_query_returns_handler_result(self, client):
        """A reject callback query is delegated to handle_update."""
        expected = {"ok": True, "action": "reject", "request_id": "req-042"}
        handler = _make_handler(handle_result=expected)
        update = _callback_query_update(callback_data="reject:req-042")

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json() == expected

    # -------------------------------------------------------------------------
    # 5. Unauthorized user — handler returns the "unauthorized" dict
    # -------------------------------------------------------------------------

    def test_unauthorized_user_returns_handler_response(self, client):
        """When a user is not in allowed_users, handle_update returns unauthorized dict."""
        unauthorized_result = {"ok": False, "error": "unauthorized"}
        handler = _make_handler(handle_result=unauthorized_result)

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, _text_message_update(chat_id=999999))

        # The endpoint itself returns 200; the handler payload carries the error
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "unauthorized"

    # -------------------------------------------------------------------------
    # 6. Command routing (/start, /help, /status)
    # -------------------------------------------------------------------------

    def test_start_command_handled(self, client):
        """A /start command is dispatched through handle_update."""
        expected = {"ok": True, "command": "start", "handled": True}
        handler = _make_handler(handle_result=expected)
        update = _text_message_update(text="/start")

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json() == expected
        handler.handle_update.assert_awaited_once_with(update)

    def test_help_command_handled(self, client):
        """A /help command is dispatched through handle_update."""
        expected = {"ok": True, "command": "help", "handled": True}
        handler = _make_handler(handle_result=expected)
        update = _text_message_update(text="/help")

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json() == expected

    def test_status_command_handled(self, client):
        """A /status command is dispatched through handle_update."""
        expected = {"ok": True, "command": "status", "handled": True}
        handler = _make_handler(handle_result=expected)
        update = _text_message_update(text="/status")

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json() == expected

    # -------------------------------------------------------------------------
    # 7. Error paths
    # -------------------------------------------------------------------------

    def test_malformed_json_returns_400(self, client):
        """A non-JSON request body is rejected with 400."""
        handler = _make_handler()
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = client.post(
                "/api/gateway/telegram",
                content=b"not-valid-json",
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 400
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "invalid_json"

    def test_handler_exception_returns_500(self, client):
        """When handle_update raises, the endpoint returns 500."""
        handler = _make_handler()
        handler.handle_update = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, _text_message_update())

        assert response.status_code == 500
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "internal_error"

    # -------------------------------------------------------------------------
    # 8. Singleton behaviour
    # -------------------------------------------------------------------------

    def test_get_telegram_handler_returns_singleton(self):
        """get_telegram_handler() must return the same instance on repeated calls."""
        with patch(
            "forge_harness.webhook_server.api.telegram.TelegramBotHandler.from_env"
        ) as mock_from_env:
            mock_instance = MagicMock()
            mock_from_env.return_value = mock_instance

            h1 = get_telegram_handler()
            h2 = get_telegram_handler()

        assert h1 is h2
        # from_env should have been called exactly once
        mock_from_env.assert_called_once()

    def test_reset_handler_clears_singleton(self):
        """_reset_handler() forces a new instance to be created next call."""
        with patch(
            "forge_harness.webhook_server.api.telegram.TelegramBotHandler.from_env"
        ) as mock_from_env:
            mock_from_env.return_value = MagicMock()
            get_telegram_handler()
            _reset_handler()
            get_telegram_handler()

        assert mock_from_env.call_count == 2

    # -------------------------------------------------------------------------
    # 9. Endpoint exists and returns JSON content-type
    # -------------------------------------------------------------------------

    def test_response_content_type_is_json(self, client):
        """Successful response must carry application/json Content-Type."""
        handler = _make_handler()
        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, _text_message_update())

        assert "application/json" in response.headers.get("content-type", "")

    def test_update_with_no_message_or_callback_returns_ok(self, client):
        """An update with neither message nor callback_query is handled gracefully."""
        expected = {"ok": True, "handled": False}
        handler = _make_handler(handle_result=expected)
        update = {"update_id": 99}  # Minimal update with no known field

        with patch(
            "forge_harness.webhook_server.api.telegram.get_telegram_handler",
            return_value=handler,
        ):
            response = self._post(client, update)

        assert response.status_code == 200
        assert response.json()["ok"] is True

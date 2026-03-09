"""Tests for telegram_bot.py - Telegram Bot Handler.

Tests cover:
- TelegramUser dataclass
- TelegramCommand dataclass
- TelegramMessage dataclass
- TelegramBotHandler initialization
- Configuration checks
- HTTP client management
- Webhook verification
- send_message method
- send_notification method
- send_approval_request method
- _format_notification_text method
- _get_emoji_for_type method
- handle_update method
- Command handlers (/start, /help, /status)
- Callback handling
- broadcast method
- get_bot_info method
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from forge_harness.telegram_bot import (
    TelegramBotHandler,
    TelegramCommand,
    TelegramMessage,
    TelegramUser,
)

# =============================================================================
# Data Class Tests
# =============================================================================


class TestTelegramUser:
    """Tests for TelegramUser dataclass."""

    def test_creation_minimal(self) -> None:
        """Should create with minimal fields."""
        user = TelegramUser(user_id="12345")

        assert user.user_id == "12345"
        assert user.username is None
        assert user.first_name is None
        assert user.role == "readonly"
        assert user.notifications_enabled is True

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        user = TelegramUser(
            user_id="12345",
            username="testuser",
            first_name="Test",
            role="admin",
            notifications_enabled=False,
        )

        assert user.username == "testuser"
        assert user.first_name == "Test"
        assert user.role == "admin"
        assert user.notifications_enabled is False


class TestTelegramCommand:
    """Tests for TelegramCommand dataclass."""

    def test_creation(self) -> None:
        """Should create command."""
        cmd = TelegramCommand(
            name="start",
            args=["arg1"],
            raw_text="/start arg1",
            chat_id="12345",
            user_id="67890",
            message_id=1,
            username="testuser",
            first_name="Test",
        )

        assert cmd.name == "start"
        assert cmd.args == ["arg1"]
        assert cmd.raw_text == "/start arg1"
        assert cmd.chat_id == "12345"
        assert cmd.user_id == "67890"
        assert cmd.message_id == 1
        assert cmd.username == "testuser"
        assert cmd.first_name == "Test"


class TestTelegramMessage:
    """Tests for TelegramMessage dataclass."""

    def test_creation_defaults(self) -> None:
        """Should create with defaults."""
        msg = TelegramMessage(text="Hello", chat_id="12345")

        assert msg.text == "Hello"
        assert msg.chat_id == "12345"
        assert msg.parse_mode == "HTML"
        assert msg.disable_notification is False
        assert msg.reply_markup is None

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        markup = {"inline_keyboard": []}
        msg = TelegramMessage(
            text="Hello",
            chat_id="12345",
            parse_mode="Markdown",
            disable_notification=True,
            reply_markup=markup,
        )

        assert msg.parse_mode == "Markdown"
        assert msg.disable_notification is True
        assert msg.reply_markup is markup


# =============================================================================
# TelegramBotHandler Initialization Tests
# =============================================================================


class TestTelegramBotHandlerInit:
    """Tests for TelegramBotHandler initialization."""

    def test_init_defaults(self) -> None:
        """Should initialize with defaults."""
        handler = TelegramBotHandler()

        assert handler.bot_token is None
        assert handler.allowed_users == set()
        assert handler.webhook_secret is None
        assert handler._http_client is None
        assert handler._authorized_chats == {}
        assert handler.TELEGRAM_API_BASE == "https://api.telegram.org/bot"

    def test_init_custom(self) -> None:
        """Should initialize with custom values."""
        handler = TelegramBotHandler(
            bot_token="test-token",
            allowed_users={"12345", "67890"},
            webhook_secret="secret123",
        )

        assert handler.bot_token == "test-token"
        assert handler.allowed_users == {"12345", "67890"}
        assert handler.webhook_secret == "secret123"

    def test_from_env(self) -> None:
        """Should create from environment variables."""
        env = {
            "TELEGRAM_BOT_TOKEN": "env-token",
            "TELEGRAM_ALLOWED_USERS": "12345, 67890, 11111",
            "TELEGRAM_WEBHOOK_SECRET": "env-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            handler = TelegramBotHandler.from_env()

            assert handler.bot_token == "env-token"
            assert handler.allowed_users == {"12345", "67890", "11111"}
            assert handler.webhook_secret == "env-secret"

    def test_from_env_empty_users(self) -> None:
        """Should handle empty allowed users."""
        env = {"TELEGRAM_BOT_TOKEN": "token"}

        with patch.dict(os.environ, env, clear=True):
            handler = TelegramBotHandler.from_env()

            assert handler.allowed_users == set()


# =============================================================================
# Configuration Tests
# =============================================================================


class TestConfiguration:
    """Tests for configuration methods."""

    def test_is_configured_true(self) -> None:
        """Should return True when configured."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        assert handler.is_configured() is True

    def test_is_configured_no_token(self) -> None:
        """Should return False without token."""
        handler = TelegramBotHandler(allowed_users={"12345"})

        assert handler.is_configured() is False

    def test_is_configured_no_users(self) -> None:
        """Should return False without users."""
        handler = TelegramBotHandler(bot_token="token")

        assert handler.is_configured() is False

    def test_is_authorized_true(self) -> None:
        """Should return True for authorized chat."""
        handler = TelegramBotHandler(allowed_users={"12345"})

        assert handler.is_authorized("12345") is True

    def test_is_authorized_false(self) -> None:
        """Should return False for unauthorized chat."""
        handler = TelegramBotHandler(allowed_users={"12345"})

        assert handler.is_authorized("99999") is False


# =============================================================================
# HTTP Client Tests
# =============================================================================


class TestHTTPClient:
    """Tests for HTTP client management."""

    @pytest.mark.asyncio
    async def test_get_client_creates_new(self) -> None:
        """Should create new client when none exists."""
        handler = TelegramBotHandler()

        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = await handler._get_client()

            assert client is mock_client
            assert handler._http_client is mock_client

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing(self) -> None:
        """Should reuse existing client."""
        handler = TelegramBotHandler()
        existing = AsyncMock()
        handler._http_client = existing

        client = await handler._get_client()

        assert client is existing

    @pytest.mark.asyncio
    async def test_close_client(self) -> None:
        """Should close HTTP client."""
        handler = TelegramBotHandler()
        mock_client = AsyncMock()
        handler._http_client = mock_client

        await handler.close()

        mock_client.aclose.assert_called_once()
        assert handler._http_client is None

    @pytest.mark.asyncio
    async def test_close_no_client(self) -> None:
        """Should handle no client gracefully."""
        handler = TelegramBotHandler()

        await handler.close()  # Should not raise


# =============================================================================
# Webhook Verification Tests
# =============================================================================


class TestWebhookVerification:
    """Tests for webhook verification."""

    def test_verify_webhook_no_secret(self) -> None:
        """Should return True when no secret configured."""
        handler = TelegramBotHandler()

        assert handler.verify_webhook({}) is True

    def test_verify_webhook_valid(self) -> None:
        """Should return True for valid secret."""
        handler = TelegramBotHandler(webhook_secret="secret123")

        headers = {"X-Telegram-Bot-Api-Secret-Token": "secret123"}

        assert handler.verify_webhook(headers) is True

    def test_verify_webhook_invalid(self) -> None:
        """Should return False for invalid secret."""
        handler = TelegramBotHandler(webhook_secret="secret123")

        headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong"}

        assert handler.verify_webhook(headers) is False

    def test_verify_webhook_missing_header(self) -> None:
        """Should return False when header missing."""
        handler = TelegramBotHandler(webhook_secret="secret123")

        assert handler.verify_webhook({}) is False


# =============================================================================
# send_message Tests
# =============================================================================


class TestSendMessage:
    """Tests for send_message method."""

    @pytest.mark.asyncio
    async def test_send_no_token(self) -> None:
        """Should raise ValueError without token."""
        handler = TelegramBotHandler(allowed_users={"12345"})

        with pytest.raises(ValueError, match="token not configured"):
            await handler.send_message("12345", "Hello")

    @pytest.mark.asyncio
    async def test_send_unauthorized(self) -> None:
        """Should raise ValueError for unauthorized chat."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        with pytest.raises(ValueError, match="not authorized"):
            await handler.send_message("99999", "Hello")

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        """Should send message successfully."""
        handler = TelegramBotHandler(
            bot_token="test-token",
            allowed_users={"12345"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 1}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(handler, "_get_client", return_value=mock_client):
            result = await handler.send_message("12345", "Hello")

            assert result["message_id"] == 1
            mock_client.post.assert_called_once()

            # Verify URL
            call_args = mock_client.post.call_args
            assert "test-token" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_with_reply_markup(self) -> None:
        """Should send with reply markup."""
        handler = TelegramBotHandler(
            bot_token="test-token",
            allowed_users={"12345"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 1}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        markup = {"inline_keyboard": []}

        with patch.object(handler, "_get_client", return_value=mock_client):
            await handler.send_message("12345", "Hello", reply_markup=markup)

            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["json"]["reply_markup"] == markup

    @pytest.mark.asyncio
    async def test_send_api_error(self) -> None:
        """Should raise ValueError on API error."""
        handler = TelegramBotHandler(
            bot_token="test-token",
            allowed_users={"12345"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "description": "Bad Request"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(handler, "_get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="API error"):
                await handler.send_message("12345", "Hello")


# =============================================================================
# send_notification Tests
# =============================================================================


class TestSendNotification:
    """Tests for send_notification method."""

    @pytest.mark.asyncio
    async def test_send_notification_watch(self) -> None:
        """Should send watch tier notification."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        mock_result = {"message_id": 1}

        with patch.object(handler, "send_message", return_value=mock_result) as mock_send:
            result = await handler.send_notification(
                chat_id="12345",
                title="Test",
                message="Test message",
                tier="watch",
            )

            assert result is mock_result
            mock_send.assert_called_once()

            # Verify text is formatted for watch
            call_kwargs = mock_send.call_args[1]
            assert "<b>" in call_kwargs["text"]
            assert call_kwargs["reply_markup"] is None

    @pytest.mark.asyncio
    async def test_send_notification_phone(self) -> None:
        """Should send phone tier notification."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        mock_result = {"message_id": 1}

        with patch.object(handler, "send_message", return_value=mock_result) as mock_send:
            result = await handler.send_notification(
                chat_id="12345",
                title="Test",
                message="Test message",
                tier="phone",
                metadata={"domain": "test-domain", "project": "test-project"},
                action_url="http://example.com",
            )

            assert result is mock_result
            mock_send.assert_called_once()

            # Verify markup includes action button
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["reply_markup"] is not None
            assert "inline_keyboard" in call_kwargs["reply_markup"]

    @pytest.mark.asyncio
    async def test_send_notification_desktop(self) -> None:
        """Should send desktop tier notification."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        mock_result = {"message_id": 1}

        with patch.object(handler, "send_message", return_value=mock_result) as mock_send:
            result = await handler.send_notification(
                chat_id="12345",
                title="Test",
                message="Test message",
                tier="desktop",
                metadata={"type": "info"},
            )

            assert result is mock_result


# =============================================================================
# send_approval_request Tests
# =============================================================================


class TestSendApprovalRequest:
    """Tests for send_approval_request method."""

    @pytest.mark.asyncio
    async def test_send_approval_watch(self) -> None:
        """Should send watch tier approval request."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        mock_result = {"message_id": 1}

        with patch.object(handler, "send_message", return_value=mock_result) as mock_send:
            result = await handler.send_approval_request(
                chat_id="12345",
                request_id="req-123",
                title="Approve this",
                description="Please approve",
                tier="watch",
            )

            assert result is mock_result

            # Verify minimal keyboard for watch
            call_kwargs = mock_send.call_args[1]
            markup = call_kwargs["reply_markup"]
            assert len(markup["inline_keyboard"]) == 1
            assert len(markup["inline_keyboard"][0]) == 2  # Approve + Reject

    @pytest.mark.asyncio
    async def test_send_approval_phone(self) -> None:
        """Should send phone tier approval request."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        mock_result = {"message_id": 1}

        with patch.object(handler, "send_message", return_value=mock_result) as mock_send:
            result = await handler.send_approval_request(
                chat_id="12345",
                request_id="req-123",
                title="Approve this",
                description="Please approve",
                tier="phone",
            )

            assert result is mock_result

            # Verify full keyboard for phone
            call_kwargs = mock_send.call_args[1]
            markup = call_kwargs["reply_markup"]
            assert len(markup["inline_keyboard"]) == 2  # 2 rows


# =============================================================================
# _format_notification_text Tests
# =============================================================================


class TestFormatNotificationText:
    """Tests for _format_notification_text method."""

    def test_format_watch(self) -> None:
        """Should format watch tier text."""
        handler = TelegramBotHandler()

        text = handler._format_notification_text(
            title="Test Title",
            message="Test message",
            tier="watch",
        )

        assert "<b>Test Title</b>" in text
        assert len(text) < 100

    def test_format_phone(self) -> None:
        """Should format phone tier text."""
        handler = TelegramBotHandler()

        text = handler._format_notification_text(
            title="Test Title",
            message="Test message",
            tier="phone",
            metadata={"domain": "test-domain", "project": "test-project"},
        )

        assert "<b>Test Title</b>" in text
        assert "test-domain" in text
        assert "test-project" in text
        assert "Test message" in text

    def test_format_desktop(self) -> None:
        """Should format desktop tier text."""
        handler = TelegramBotHandler()

        text = handler._format_notification_text(
            title="Test Title",
            message="Test message",
            tier="desktop",
            metadata={
                "domain": "test-domain",
                "project": "test-project",
                "feature_id": "feat-123",
                "risk_score": 0.5,
            },
            action_url="http://example.com",
        )

        assert "<b>Test Title</b>" in text
        assert "test-domain" in text
        assert "test-project" in text
        assert "feat-123" in text
        assert "Risk:" in text
        assert "50%" in text
        assert "Open Dashboard" in text

    def test_format_desktop_low_risk(self) -> None:
        """Should show green emoji for low risk."""
        handler = TelegramBotHandler()

        text = handler._format_notification_text(
            title="Test",
            message="Test",
            tier="desktop",
            metadata={"risk_score": 0.2},
        )

        assert "🟢" in text

    def test_format_desktop_high_risk(self) -> None:
        """Should show red emoji for high risk."""
        handler = TelegramBotHandler()

        text = handler._format_notification_text(
            title="Test",
            message="Test",
            tier="desktop",
            metadata={"risk_score": 0.8},
        )

        assert "🔴" in text


# =============================================================================
# _get_emoji_for_type Tests
# =============================================================================


class TestGetEmojiForType:
    """Tests for _get_emoji_for_type method."""

    def test_emoji_approval(self) -> None:
        """Should return approval emoji."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("approval") == "🔘"

    def test_emoji_task_complete(self) -> None:
        """Should return task complete emoji."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("task_complete") == "✅"

    def test_emoji_error(self) -> None:
        """Should return error emoji."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("error") == "❌"

    def test_emoji_warning(self) -> None:
        """Should return warning emoji."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("warning") == "⚠️"

    def test_emoji_info(self) -> None:
        """Should return info emoji."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("info") == "ℹ️"

    def test_emoji_summary(self) -> None:
        """Should return summary emoji."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("summary") == "📊"

    def test_emoji_default(self) -> None:
        """Should return default emoji for unknown type."""
        handler = TelegramBotHandler()
        assert handler._get_emoji_for_type("unknown") == "ℹ️"


# =============================================================================
# handle_update Tests
# =============================================================================


class TestHandleUpdate:
    """Tests for handle_update method."""

    @pytest.mark.asyncio
    async def test_handle_callback_query(self) -> None:
        """Should handle callback query and route to approval handler."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        update = {
            "callback_query": {
                "data": "approve:req-123",
                "from": {"id": 67890},
                "message": {"chat": {"id": 12345}},
            }
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "req-123", "status": "approved"}
        )

        with patch.object(handler, "send_message", return_value={}):
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler.handle_update(update)

                assert result["ok"] is True
                assert result["action"] == "approve"

    @pytest.mark.asyncio
    async def test_handle_message_command(self) -> None:
        """Should handle command message."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        update = {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {"id": 67890, "username": "test"},
            }
        }

        with patch.object(handler, "send_message", return_value={}):
            result = await handler.handle_update(update)

            assert result["ok"] is True
            assert result["command"] == "start"

    @pytest.mark.asyncio
    async def test_handle_message_text(self) -> None:
        """Should handle plain text message."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        update = {
            "message": {
                "text": "Hello",
                "chat": {"id": 12345},
                "from": {"id": 67890},
            }
        }

        with patch.object(handler, "send_message", return_value={}):
            result = await handler.handle_update(update)

            assert result["ok"] is True
            assert result["type"] == "text"

    @pytest.mark.asyncio
    async def test_handle_unauthorized(self) -> None:
        """Should reject unauthorized chat."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        update = {
            "message": {
                "text": "/start",
                "chat": {"id": 99999},
                "from": {"id": 67890},
            }
        }

        with patch.object(handler, "send_message", return_value={}):
            result = await handler.handle_update(update)

            assert result["ok"] is False
            assert result["error"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_handle_empty_update(self) -> None:
        """Should handle empty update."""
        handler = TelegramBotHandler()

        result = await handler.handle_update({})

        assert result["ok"] is True
        assert result["handled"] is False


# =============================================================================
# Command Handler Tests
# =============================================================================


class TestCommandHandlers:
    """Tests for command handlers."""

    @pytest.mark.asyncio
    async def test_cmd_start(self) -> None:
        """Should handle /start command."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        message = {
            "text": "/start",
            "chat": {"id": 12345},
            "from": {"id": 67890, "first_name": "Test"},
        }

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_start("12345", message)

            assert result["ok"] is True
            assert result["command"] == "start"

            # Verify welcome message
            call_args = mock_send.call_args[1]
            assert "Hello" in call_args["text"]
            assert "Test" in call_args["text"]

    @pytest.mark.asyncio
    async def test_cmd_help(self) -> None:
        """Should handle /help command."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_help("12345")

            assert result["ok"] is True
            assert result["command"] == "help"

            # Verify help text
            call_args = mock_send.call_args[1]
            assert "/start" in call_args["text"]
            assert "/status" in call_args["text"]

    @pytest.mark.asyncio
    async def test_cmd_status_no_args(self) -> None:
        """Should handle /status without args."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_status("12345", [])

            assert result["ok"] is True
            assert result["command"] == "status"

            # Verify fleet status message
            call_args = mock_send.call_args[1]
            assert "Fleet Status" in call_args["text"]

    @pytest.mark.asyncio
    async def test_cmd_status_with_args(self) -> None:
        """Should handle /status with agent name."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_status("12345", ["agent-1"])

            assert result["ok"] is True

            # Verify agent-specific message
            call_args = mock_send.call_args[1]
            assert "agent-1" in call_args["text"]

    @pytest.mark.asyncio
    async def test_handle_help_command(self) -> None:
        """Should handle /help command via _handle_command."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        message = {
            "text": "/help",
            "chat": {"id": 12345},
            "from": {"id": 67890},
        }

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._handle_command(message)

            assert result["ok"] is True
            assert result["command"] == "help"
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_status_command(self) -> None:
        """Should handle /status command via _handle_command."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        message = {
            "text": "/status",
            "chat": {"id": 12345},
            "from": {"id": 67890},
        }

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._handle_command(message)

            assert result["ok"] is True
            assert result["command"] == "status"
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self) -> None:
        """Should handle unknown command."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        message = {
            "text": "/unknown",
            "chat": {"id": 12345},
        }

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._handle_command(message)

            assert result["ok"] is True
            assert result["handled"] is False

            # Verify unknown command message
            call_args = mock_send.call_args[1]
            assert "Unknown command" in call_args["text"]


# =============================================================================
# Callback Handler Tests
# =============================================================================


class TestCallbackHandler:
    """Tests for callback handling."""

    @pytest.mark.asyncio
    async def test_callback_approve(self) -> None:
        """Should handle approve callback and process via approval handler."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "approve:req-123",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "req-123", "status": "approved"}
        )

        with patch.object(handler, "send_message", return_value={}):
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler._handle_callback(callback)

                assert result["ok"] is True
                assert result["action"] == "approve"
                assert result["request_id"] == "req-123"
                assert result["result"] == "processed"
                mock_approval_handler.approve_request.assert_called_once_with(
                    request_id="req-123",
                    approver="67890",
                )

    @pytest.mark.asyncio
    async def test_callback_reject(self) -> None:
        """Should handle reject callback and process via approval handler."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "reject:req-123",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-123", "status": "rejected"}
        )

        with patch.object(handler, "send_message", return_value={}):
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler._handle_callback(callback)

                assert result["ok"] is True
                assert result["action"] == "reject"
                assert result["request_id"] == "req-123"
                assert result["result"] == "processed"
                mock_approval_handler.reject_request.assert_called_once_with(
                    request_id="req-123",
                    rejector="67890",
                )

    @pytest.mark.asyncio
    async def test_callback_details_found(self) -> None:
        """Should handle details callback and display approval info."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "details:req-123",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.get_request = AsyncMock(
            return_value={
                "request_id": "req-123",
                "title": "Deploy to production",
                "description": "Deploy version 1.0.0",
                "status": "pending",
                "priority": "high",
                "created_at": "2026-03-01T10:00:00",
            }
        )

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler._handle_callback(callback)

                assert result["ok"] is True
                assert result["action"] == "details"
                assert result["request_id"] == "req-123"
                mock_approval_handler.get_request.assert_called_once_with("req-123")

                # Verify details were included in the message
                call_kwargs = mock_send.call_args[1]
                assert "Deploy to production" in call_kwargs["text"]
                assert "req-123" in call_kwargs["text"]
                # Pending status should include action buttons
                assert call_kwargs["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_callback_details_not_found(self) -> None:
        """Should handle details callback when approval not found."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "details:req-missing",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.get_request = AsyncMock(return_value=None)

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler._handle_callback(callback)

                assert result["ok"] is True
                assert result["action"] == "details"
                call_kwargs = mock_send.call_args[1]
                assert "not found" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_callback_details_resolved_no_buttons(self) -> None:
        """Should not show action buttons for already-resolved approvals."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "details:req-456",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.get_request = AsyncMock(
            return_value={
                "request_id": "req-456",
                "title": "Old request",
                "description": "Already done",
                "status": "approved",
                "priority": "low",
                "created_at": "2026-02-28T08:00:00",
            }
        )

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler._handle_callback(callback)

                assert result["ok"] is True
                call_kwargs = mock_send.call_args[1]
                # Approved status should NOT include action buttons
                assert call_kwargs["reply_markup"] is None

    @pytest.mark.asyncio
    async def test_callback_unauthorized(self) -> None:
        """Should reject callback from unauthorized chat."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "approve:req-123",
            "from": {"id": 99999},
            "message": {"chat": {"id": 99999}},
        }

        result = await handler._handle_callback(callback)

        assert result["ok"] is False
        assert result["error"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_callback_invalid(self) -> None:
        """Should handle invalid callback data (no colon separator)."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "invalid",
            "message": {"chat": {"id": 12345}},
        }

        result = await handler._handle_callback(callback)

        assert result["ok"] is True
        assert result["handled"] is False

    @pytest.mark.asyncio
    async def test_callback_handler_error(self) -> None:
        """Should handle errors from approval handler gracefully."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "approve:req-error",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_approval_handler = AsyncMock()
        mock_approval_handler.approve_request = AsyncMock(
            side_effect=RuntimeError("Queue unavailable")
        )

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                result = await handler._handle_callback(callback)

                assert result["ok"] is True
                assert result["action"] == "approve"
                assert "error" in result
                # Error message should be sent to user
                call_kwargs = mock_send.call_args[1]
                assert "Error processing callback" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_callback_rotate(self) -> None:
        """rotate:<agent> callback triggers forge fleet swap and sends success message."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "rotate:glm",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        mock_approval_handler = AsyncMock()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                    result = await handler._handle_callback(callback)

        assert result["ok"] is True
        assert result["action"] == "rotate"
        assert result["agent_id"] == "glm"
        assert "error" not in result

        # Verify forge fleet swap was called with the correct agent
        mock_exec.assert_called_once_with(
            "forge", "fleet", "swap", "glm",
            stdout=__import__("asyncio").subprocess.PIPE,
            stderr=__import__("asyncio").subprocess.PIPE,
        )

        # Verify success message was sent
        texts = [call[1]["text"] for call in mock_send.call_args_list]
        assert any("glm" in t and "rotat" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_callback_rotate_failure(self) -> None:
        """rotate:<agent> callback sends an error message when subprocess fails."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
        )

        callback = {
            "data": "rotate:kimi",
            "from": {"id": 67890},
            "message": {"chat": {"id": 12345}},
        }

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"agent not found"))

        mock_approval_handler = AsyncMock()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=mock_approval_handler,
            ):
                with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                    result = await handler._handle_callback(callback)

        assert result["ok"] is True
        assert result["action"] == "rotate"
        assert result["agent_id"] == "kimi"

        # Verify failure message was sent containing the error text from stderr
        texts = [call[1]["text"] for call in mock_send.call_args_list]
        assert any("agent not found" in t for t in texts)


# =============================================================================
# broadcast Tests
# =============================================================================


class TestBroadcast:
    """Tests for broadcast method."""

    @pytest.mark.asyncio
    async def test_broadcast_no_users(self) -> None:
        """Should return empty list when no users."""
        handler = TelegramBotHandler(bot_token="token")

        results = await handler.broadcast("Hello")

        assert results == []

    @pytest.mark.asyncio
    async def test_broadcast_success(self) -> None:
        """Should broadcast to all users."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345", "67890"},
        )

        mock_result = {"message_id": 1}

        with patch.object(handler, "send_notification", return_value=mock_result):
            results = await handler.broadcast("Hello")

            assert len(results) == 2
            assert all(r["success"] for r in results)

    @pytest.mark.asyncio
    async def test_broadcast_partial_failure(self) -> None:
        """Should handle partial failures."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345", "67890"},
        )

        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            chat_id = kwargs.get("chat_id")
            if chat_id == "12345":
                return {"message_id": 1}
            raise ValueError("Failed")

        with patch.object(handler, "send_notification", side_effect=mock_send):
            results = await handler.broadcast("Hello")

            assert len(results) == 2
            # Find success and failure results
            success_results = [r for r in results if r["success"]]
            failure_results = [r for r in results if not r["success"]]
            assert len(success_results) == 1
            assert len(failure_results) == 1
            assert "error" in failure_results[0]


# =============================================================================
# get_bot_info Tests
# =============================================================================


class TestGetBotInfo:
    """Tests for get_bot_info method."""

    def test_get_info_configured(self) -> None:
        """Should return info for configured bot."""
        handler = TelegramBotHandler(
            bot_token="token",
            allowed_users={"12345"},
            webhook_secret="secret",
        )
        handler._authorized_chats["12345"] = TelegramUser(user_id="12345")

        info = handler.get_bot_info()

        assert info["configured"] is True
        assert info["has_token"] is True
        assert info["allowed_users_count"] == 1
        assert info["has_webhook_secret"] is True
        assert info["authorized_chats"] == 1

    def test_get_info_not_configured(self) -> None:
        """Should return info for unconfigured bot."""
        handler = TelegramBotHandler()

        info = handler.get_bot_info()

        assert info["configured"] is False
        assert info["has_token"] is False
        assert info["allowed_users_count"] == 0
        assert info["has_webhook_secret"] is False
        assert info["authorized_chats"] == 0


# =============================================================================
# Fleet Command Tests (/spawn, /stop, /nudge, /rotate)
# =============================================================================


def _make_handler() -> TelegramBotHandler:
    """Return a pre-configured handler for fleet command tests."""
    return TelegramBotHandler(
        bot_token="test-token",
        allowed_users={"12345"},
    )


def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    """Build a mock asyncio.Process that communicates() correctly."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestCmdSpawn:
    """Tests for /spawn command."""

    @pytest.mark.asyncio
    async def test_spawn_no_args_returns_usage(self) -> None:
        """Should send usage when no arguments given."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_spawn("12345", [])

        assert result["ok"] is True
        assert result["command"] == "spawn"
        assert result["handled"] is True
        call_kwargs = mock_send.call_args[1]
        assert "Usage" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_only_one_arg_returns_usage(self) -> None:
        """Should send usage when only one argument given."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_spawn("12345", ["sati"])

        assert result["ok"] is True
        assert result["handled"] is True
        call_kwargs = mock_send.call_args[1]
        assert "Usage" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_invalid_node_returns_error(self) -> None:
        """Should send error message for unknown node."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_spawn("12345", ["badnode", "opencode"])

        assert result["ok"] is True
        assert result["command"] == "spawn"
        assert result["error"] == "invalid_node"
        call_kwargs = mock_send.call_args[1]
        assert "badnode" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_success(self) -> None:
        """Should call forge fleet spawn with correct args on success."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                result = await handler._cmd_spawn("12345", ["sati", "opencode"])

        assert result["ok"] is True
        assert result["command"] == "spawn"
        assert result["node"] == "sati"
        assert result["agent"] == "opencode"
        assert result["returncode"] == 0

        # Verify subprocess called with the right command
        mock_exec.assert_called_once_with(
            "forge", "fleet", "spawn", "opencode", "--node", "sati",
            stdout=__import__("asyncio").subprocess.PIPE,
            stderr=__import__("asyncio").subprocess.PIPE,
        )

        # Last send_message should confirm success
        last_call_kwargs = mock_send.call_args[1]
        assert "opencode" in last_call_kwargs["text"]
        assert "sati" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_subprocess_failure(self) -> None:
        """Should report error when subprocess returns non-zero exit code."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=1, stderr=b"agent already running")

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._cmd_spawn("12345", ["sati", "opencode"])

        assert result["returncode"] == 1
        last_call_kwargs = mock_send.call_args[1]
        assert "agent already running" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_timeout(self) -> None:
        """Should report timeout when subprocess exceeds 30 s."""
        handler = _make_handler()

        async def slow_proc(*args, **kwargs):  # noqa: ARG001
            mock = AsyncMock()
            mock.communicate = AsyncMock(side_effect=TimeoutError)
            return mock

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", side_effect=slow_proc):
                result = await handler._cmd_spawn("12345", ["sati", "opencode"])

        assert result["error"] == "timeout"
        last_call_kwargs = mock_send.call_args[1]
        assert "timed out" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_unexpected_exception(self) -> None:
        """Should send error message on unexpected exception."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=RuntimeError("exec failed"),
            ):
                result = await handler._cmd_spawn("12345", ["sati", "opencode"])

        assert "error" in result
        last_call_kwargs = mock_send.call_args[1]
        assert "exec failed" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_spawn_via_handle_command(self) -> None:
        """Should route /spawn through _handle_command dispatcher."""
        handler = _make_handler()

        message = {
            "text": "/spawn sati opencode",
            "chat": {"id": 12345},
            "from": {"id": 67890},
        }
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._handle_command(message)

        assert result["ok"] is True
        assert result["command"] == "spawn"


class TestCmdStop:
    """Tests for /stop command."""

    @pytest.mark.asyncio
    async def test_stop_no_args_returns_usage(self) -> None:
        """Should send usage when no arguments given."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_stop("12345", [])

        assert result["ok"] is True
        assert result["command"] == "stop"
        assert result["handled"] is True
        call_kwargs = mock_send.call_args[1]
        assert "Usage" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_stop_success(self) -> None:
        """Should call forge fleet stop with correct agent name."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                result = await handler._cmd_stop("12345", ["glm"])

        assert result["ok"] is True
        assert result["command"] == "stop"
        assert result["agent"] == "glm"
        assert result["returncode"] == 0

        mock_exec.assert_called_once_with(
            "forge", "fleet", "stop", "glm",
            stdout=__import__("asyncio").subprocess.PIPE,
            stderr=__import__("asyncio").subprocess.PIPE,
        )

        last_call_kwargs = mock_send.call_args[1]
        assert "glm" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_stop_subprocess_failure(self) -> None:
        """Should report error when subprocess returns non-zero exit code."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=1, stderr=b"agent not found")

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._cmd_stop("12345", ["glm"])

        assert result["returncode"] == 1
        last_call_kwargs = mock_send.call_args[1]
        assert "agent not found" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_stop_timeout(self) -> None:
        """Should report timeout when subprocess exceeds 30 s."""
        handler = _make_handler()

        async def slow_proc(*args, **kwargs):  # noqa: ARG001
            mock = AsyncMock()
            mock.communicate = AsyncMock(side_effect=TimeoutError)
            return mock

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", side_effect=slow_proc):
                result = await handler._cmd_stop("12345", ["glm"])

        assert result["error"] == "timeout"
        last_call_kwargs = mock_send.call_args[1]
        assert "timed out" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_stop_unexpected_exception(self) -> None:
        """Should send error message on unexpected exception."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("exec unavailable"),
            ):
                result = await handler._cmd_stop("12345", ["glm"])

        assert "error" in result
        last_call_kwargs = mock_send.call_args[1]
        assert "exec unavailable" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_stop_via_handle_command(self) -> None:
        """Should route /stop through _handle_command dispatcher."""
        handler = _make_handler()

        message = {
            "text": "/stop glm",
            "chat": {"id": 12345},
            "from": {"id": 67890},
        }
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._handle_command(message)

        assert result["ok"] is True
        assert result["command"] == "stop"


class TestCmdNudge:
    """Tests for /nudge command."""

    @pytest.mark.asyncio
    async def test_nudge_no_args_returns_usage(self) -> None:
        """Should send usage when no arguments given."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_nudge("12345", [])

        assert result["ok"] is True
        assert result["command"] == "nudge"
        assert result["handled"] is True
        call_kwargs = mock_send.call_args[1]
        assert "Usage" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_nudge_success_calls_tmux(self) -> None:
        """Should call tmux send-keys with correct target and Enter."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                result = await handler._cmd_nudge("12345", ["glm"])

        assert result["ok"] is True
        assert result["command"] == "nudge"
        assert result["agent"] == "glm"
        assert result["returncode"] == 0

        mock_exec.assert_called_once_with(
            "tmux", "send-keys", "-t", "forge:glm", "Enter",
            stdout=__import__("asyncio").subprocess.PIPE,
            stderr=__import__("asyncio").subprocess.PIPE,
        )

        last_call_kwargs = mock_send.call_args[1]
        assert "glm" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_nudge_subprocess_failure(self) -> None:
        """Should report error when tmux returns non-zero exit code."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=1, stderr=b"no session named forge")

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._cmd_nudge("12345", ["glm"])

        assert result["returncode"] == 1
        last_call_kwargs = mock_send.call_args[1]
        assert "no session named forge" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_nudge_timeout(self) -> None:
        """Should report timeout when subprocess exceeds 30 s."""
        handler = _make_handler()

        async def slow_proc(*args, **kwargs):  # noqa: ARG001
            mock = AsyncMock()
            mock.communicate = AsyncMock(side_effect=TimeoutError)
            return mock

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", side_effect=slow_proc):
                result = await handler._cmd_nudge("12345", ["glm"])

        assert result["error"] == "timeout"
        last_call_kwargs = mock_send.call_args[1]
        assert "timed out" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_nudge_unexpected_exception(self) -> None:
        """Should send error message on unexpected exception."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError("tmux not found"),
            ):
                result = await handler._cmd_nudge("12345", ["glm"])

        assert "error" in result
        last_call_kwargs = mock_send.call_args[1]
        assert "tmux not found" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_nudge_via_handle_command(self) -> None:
        """Should route /nudge through _handle_command dispatcher."""
        handler = _make_handler()

        message = {
            "text": "/nudge glm",
            "chat": {"id": 12345},
            "from": {"id": 67890},
        }
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._handle_command(message)

        assert result["ok"] is True
        assert result["command"] == "nudge"


class TestCmdRotate:
    """Tests for /rotate command."""

    @pytest.mark.asyncio
    async def test_rotate_no_args_returns_usage(self) -> None:
        """Should send usage when no arguments given."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            result = await handler._cmd_rotate("12345", [])

        assert result["ok"] is True
        assert result["command"] == "rotate"
        assert result["handled"] is True
        call_kwargs = mock_send.call_args[1]
        assert "Usage" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rotate_success(self) -> None:
        """Should call forge fleet swap with correct agent name."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                result = await handler._cmd_rotate("12345", ["is.lead"])

        assert result["ok"] is True
        assert result["command"] == "rotate"
        assert result["agent"] == "is.lead"
        assert result["returncode"] == 0

        mock_exec.assert_called_once_with(
            "forge", "fleet", "swap", "is.lead",
            stdout=__import__("asyncio").subprocess.PIPE,
            stderr=__import__("asyncio").subprocess.PIPE,
        )

        last_call_kwargs = mock_send.call_args[1]
        assert "is.lead" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rotate_subprocess_failure(self) -> None:
        """Should report error when subprocess returns non-zero exit code."""
        handler = _make_handler()
        mock_proc = _mock_proc(returncode=1, stderr=b"agent not registered")

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._cmd_rotate("12345", ["is.lead"])

        assert result["returncode"] == 1
        last_call_kwargs = mock_send.call_args[1]
        assert "agent not registered" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rotate_timeout(self) -> None:
        """Should report timeout when subprocess exceeds 30 s."""
        handler = _make_handler()

        async def slow_proc(*args, **kwargs):  # noqa: ARG001
            mock = AsyncMock()
            mock.communicate = AsyncMock(side_effect=TimeoutError)
            return mock

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch("asyncio.create_subprocess_exec", side_effect=slow_proc):
                result = await handler._cmd_rotate("12345", ["is.lead"])

        assert result["error"] == "timeout"
        last_call_kwargs = mock_send.call_args[1]
        assert "timed out" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rotate_unexpected_exception(self) -> None:
        """Should send error message on unexpected exception."""
        handler = _make_handler()

        with patch.object(handler, "send_message", return_value={}) as mock_send:
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=PermissionError("permission denied"),
            ):
                result = await handler._cmd_rotate("12345", ["is.lead"])

        assert "error" in result
        last_call_kwargs = mock_send.call_args[1]
        assert "permission denied" in last_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rotate_via_handle_command(self) -> None:
        """Should route /rotate through _handle_command dispatcher."""
        handler = _make_handler()

        message = {
            "text": "/rotate is.lead",
            "chat": {"id": 12345},
            "from": {"id": 67890},
        }
        mock_proc = _mock_proc(returncode=0)

        with patch.object(handler, "send_message", return_value={}):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await handler._handle_command(message)

        assert result["ok"] is True
        assert result["command"] == "rotate"

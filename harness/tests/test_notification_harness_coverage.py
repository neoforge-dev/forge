"""
Coverage Tests for Notification Harness Module
=============================================

Tests for forge_harness/notification_harness.py to improve coverage.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.notification_harness import (
    EmailConfig,
    HumanResponse,
    NotificationChannel,
    NotificationConfig,
    NotificationHarness,
    NotificationResult,
    NotificationUrgency,
    create_notification_harness,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def notification_harness() -> NotificationHarness:
    """Create a NotificationHarness instance with Slack configured."""
    return NotificationHarness(
        slack_webhook_url="https://hooks.slack.com/test",
    )


@pytest.fixture
def email_harness() -> NotificationHarness:
    """Create a NotificationHarness with email configured."""
    return NotificationHarness(
        email_config=EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="test",
            password="password",
            from_address="test@example.com",
        ),
    )


# =============================================================================
# Tests for NotificationConfig
# =============================================================================


class TestNotificationConfig:
    """Tests for NotificationConfig."""

    def test_basic_config(self) -> None:
        """Test basic notification config."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )
        assert config.channels == [NotificationChannel.SLACK]
        assert config.message == "Test message"
        assert config.urgency == NotificationUrgency.NORMAL
        assert config.action_required is False


# =============================================================================
# Tests for NotificationResult
# =============================================================================


class TestNotificationResult:
    """Tests for NotificationResult."""

    def test_success_result(self) -> None:
        """Test successful notification result."""
        result = NotificationResult(
            channel=NotificationChannel.SLACK,
            success=True,
            message_id="msg_123",
        )
        assert result.success is True
        assert result.message_id == "msg_123"
        assert result.error is None

    def test_failure_result(self) -> None:
        """Test failed notification result."""
        result = NotificationResult(
            channel=NotificationChannel.SLACK,
            success=False,
            error="Network error",
        )
        assert result.success is False
        assert result.error == "Network error"


# =============================================================================
# Tests for HumanResponse
# =============================================================================


class TestHumanResponse:
    """Tests for HumanResponse."""

    def test_human_response(self) -> None:
        """Test human response creation."""
        response = HumanResponse(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
            message="Looks good!",
            responder="@user",
        )
        assert response.notification_id == "notif_123"
        assert response.response_type == "approved"


# =============================================================================
# Tests for has_channel
# =============================================================================


class TestHasChannel:
    """Tests for has_channel method."""

    def test_slack_configured(self, notification_harness: NotificationHarness) -> None:
        """Test Slack channel detection."""
        assert notification_harness.has_channel(NotificationChannel.SLACK) is True
        assert notification_harness.has_channel(NotificationChannel.EMAIL) is False

    def test_email_configured(self, email_harness: NotificationHarness) -> None:
        """Test email channel detection."""
        assert email_harness.has_channel(NotificationChannel.EMAIL) is True
        assert email_harness.has_channel(NotificationChannel.SLACK) is False

    def test_github_not_configured(self, notification_harness: NotificationHarness) -> None:
        """Test GitHub not configured."""
        assert notification_harness.has_channel(NotificationChannel.GITHUB_ISSUE) is False

    def test_telegram_configured(self) -> None:
        """Test Telegram channel detection."""
        harness = NotificationHarness(
            telegram_bot_token="token",
            telegram_allowed_users={"123"},
        )
        assert harness.has_channel(NotificationChannel.TELEGRAM) is True


# =============================================================================
# Tests for notify method
# =============================================================================


class TestNotify:
    """Tests for notify method."""

    @pytest.mark.asyncio
    async def test_notify_slack_success(self, notification_harness: NotificationHarness) -> None:
        """Test successful Slack notification."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )
        with patch.object(
            notification_harness, "_send_slack", new_callable=AsyncMock
        ) as mock_send:
            mock_send.return_value = NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id="msg_123",
            )
            results = await notification_harness.notify(config)
            assert len(results) == 1
            assert results[0].success is True

    @pytest.mark.asyncio
    async def test_notify_channel_not_configured(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test notification to unconfigured channel."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test",
        )
        results = await notification_harness.notify(config)
        assert results[0].success is False
        assert "not configured" in results[0].error

    @pytest.mark.asyncio
    async def test_notify_multiple_channels(
        self, email_harness: NotificationHarness
    ) -> None:
        """Test notification to multiple channels."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL],
            message="Test",
        )
        # Slack not configured, email configured
        with patch.object(email_harness, "_send_email", new_callable=AsyncMock) as mock_email:
            mock_email.return_value = NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=True,
                message_id="msg_123",
            )
            results = await email_harness.notify(config)
            assert len(results) == 2
            # First should fail (no slack), second should succeed


# =============================================================================
# Tests for _build_slack_blocks
# =============================================================================


class TestBuildSlackBlocks:
    """Tests for _build_slack_blocks method."""

    def test_basic_blocks(self, notification_harness: NotificationHarness) -> None:
        """Test basic Slack blocks."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
            title="Test Title",
        )
        blocks = notification_harness._build_slack_blocks(config)
        assert len(blocks) >= 2
        assert blocks[0]["type"] == "header"

    def test_blocks_with_mentions(self, notification_harness: NotificationHarness) -> None:
        """Test Slack blocks with user mentions."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
            title="Test Title",  # Add title to ensure blocks[1] exists
            mention_users=["user1", "user2"],
        )
        blocks = notification_harness._build_slack_blocks(config)
        # Should have section with mentions
        section_block = next((b for b in blocks if b.get("type") == "section"), None)
        assert section_block is not None
        text_content = section_block["text"]["text"]
        assert "user1" in text_content

    def test_blocks_with_action_url(self, notification_harness: NotificationHarness) -> None:
        """Test Slack blocks with action URL."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
            action_required=True,
            action_url="https://example.com/action",
        )
        blocks = notification_harness._build_slack_blocks(config)
        # Should have action button
        action_block = next(b for b in blocks if b.get("type") == "actions")
        assert action_block is not None

    def test_blocks_with_metadata(self, notification_harness: NotificationHarness) -> None:
        """Test Slack blocks with metadata."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
            metadata={"key1": "value1", "key2": "value2"},
        )
        blocks = notification_harness._build_slack_blocks(config)
        # Should have context block with metadata
        context_block = next((b for b in blocks if b.get("type") == "context"), None)
        assert context_block is not None


# =============================================================================
# Tests for urgency emoji
# =============================================================================


class TestUrgencyEmoji:
    """Tests for urgency emoji method."""

    def test_all_urgency_levels(self, notification_harness: NotificationHarness) -> None:
        """Test emoji for all urgency levels."""
        for urgency in NotificationUrgency:
            emoji = notification_harness._urgency_emoji(urgency)
            assert isinstance(emoji, str)


# =============================================================================
# Tests for response tracking
# =============================================================================


class TestResponseTracking:
    """Tests for response tracking methods."""

    def test_record_response(self, notification_harness: NotificationHarness) -> None:
        """Test recording a response."""
        response = notification_harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
            responder="@user",
        )
        assert response.response_type == "approved"

    def test_get_response(self, notification_harness: NotificationHarness) -> None:
        """Test getting recorded response."""
        notification_harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
        )
        response = notification_harness.get_response("notif_123")
        assert response is not None
        assert response.response_type == "approved"

    def test_get_response_nonexistent(self, notification_harness: NotificationHarness) -> None:
        """Test getting nonexistent response."""
        response = notification_harness.get_response("nonexistent")
        assert response is None

    def test_clear_pending(self, notification_harness: NotificationHarness) -> None:
        """Test clearing pending response."""
        notification_harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
        )
        notification_harness.clear_pending("notif_123")
        assert notification_harness.get_response("notif_123") is None


# =============================================================================
# Tests for await_response
# =============================================================================


class TestAwaitResponse:
    """Tests for await_response method."""

    @pytest.mark.asyncio
    async def test_await_response_timeout(self, notification_harness: NotificationHarness) -> None:
        """Test awaiting response that never comes."""
        result = await notification_harness.await_response(
            notification_id="notif_123",
            timeout=timedelta(seconds=1),
            poll_interval=0.1,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_await_response_received(self, notification_harness: NotificationHarness) -> None:
        """Test awaiting response that arrives."""
        notification_harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
        )
        result = await notification_harness.await_response(
            notification_id="notif_123",
            timeout=timedelta(seconds=1),
            poll_interval=0.1,
        )
        assert result is not None
        assert result.response_type == "approved"


# =============================================================================
# Tests for tier-aware approval notifications
# =============================================================================


class TestApprovalNotifications:
    """Tests for tier-aware approval notifications."""

    @pytest.mark.asyncio
    async def test_send_approval_notification_watch(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test WATCH tier approval notification."""
        with patch.object(notification_harness, "_get_client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.post = AsyncMock(return_value=mock_response)

            result = await notification_harness.send_approval_notification(
                request_id="req_123",
                title="Test Approval",
                description="Test description",
                tier="watch",
                risk_score=0.2,
            )
            # Should fail due to mock, but tests the flow

    def test_build_approval_slack_blocks_watch(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test building WATCH tier approval blocks."""
        blocks = notification_harness._build_approval_slack_blocks(
            request_id="req_123",
            title="Test Approval",
            description="Test description",
            tier="watch",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.2,
            context={},
            dashboard_url=None,
        )
        # WATCH tier should be minimal
        assert len(blocks) > 0

    def test_build_approval_slack_blocks_phone(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test building PHONE tier approval blocks."""
        blocks = notification_harness._build_approval_slack_blocks(
            request_id="req_123",
            title="Test Approval",
            description="Test description",
            tier="phone",
            domain="test-domain",
            project="test-project",
            feature_id="FEAT-001",
            approval_type="content",
            risk_score=0.4,
            context={},
            dashboard_url=None,
        )
        assert len(blocks) > 0

    def test_build_approval_slack_blocks_desktop(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test building DESKTOP tier approval blocks."""
        blocks = notification_harness._build_approval_slack_blocks(
            request_id="req_123",
            title="Test Approval",
            description="Test description",
            tier="desktop",
            domain="test-domain",
            project="test-project",
            feature_id="FEAT-001",
            approval_type="deploy",
            risk_score=0.7,
            context={"key": "value"},
            dashboard_url="https://example.com/dashboard",
        )
        assert len(blocks) > 0


# =============================================================================
# Tests for emoji and risk bar
# =============================================================================


class TestEmojiAndRiskBar:
    """Tests for emoji and risk bar methods."""

    def test_approval_emoji_by_type(self, notification_harness: NotificationHarness) -> None:
        """Test approval emoji by type."""
        emoji = notification_harness._approval_emoji("deploy", 0.2)
        assert emoji == "🚀"

        emoji = notification_harness._approval_emoji("security", 0.2)
        assert emoji == "🔐"

        emoji = notification_harness._approval_emoji("content", 0.2)
        assert emoji == "📝"

    def test_approval_emoji_by_risk(self, notification_harness: NotificationHarness) -> None:
        """Test approval emoji by risk level."""
        emoji = notification_harness._approval_emoji("content", 0.8)
        assert emoji == "🚨"

        emoji = notification_harness._approval_emoji("content", 0.4)
        assert emoji == "⚠️"

    def test_risk_bar(self, notification_harness: NotificationHarness) -> None:
        """Test risk bar generation."""
        bar = notification_harness._risk_bar(0.5)
        assert "50%" in bar
        assert "🟥" in bar
        assert "⬜" in bar

        bar = notification_harness._risk_bar(1.0)
        assert "100%" in bar


# =============================================================================
# Tests for email methods
# =============================================================================


class TestEmailMethods:
    """Tests for email-related methods."""

    def test_build_email_html(self, email_harness: NotificationHarness) -> None:
        """Test building HTML email."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message",
            title="Test Title",
            urgency=NotificationUrgency.HIGH,
            action_url="https://example.com",
            action_required=True,
        )
        html = email_harness._build_email_html(config)
        assert "Test Title" in html
        assert "Test message" in html

    def test_build_email_html_with_metadata(
        self, email_harness: NotificationHarness
    ) -> None:
        """Test building HTML email with metadata."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test",
            metadata={"key1": "value1"},
        )
        html = email_harness._build_email_html(config)
        assert "key1" in html


# =============================================================================
# Tests for GitHub and Notion
# =============================================================================


class TestGitHubNotionMethods:
    """Tests for GitHub and Notion methods."""

    def test_build_github_comment_body(self, notification_harness: NotificationHarness) -> None:
        """Test building GitHub comment body."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test message",
            title="Test Title",
            urgency=NotificationUrgency.HIGH,
        )
        body = notification_harness._build_github_comment_body(config)
        assert "Test Title" in body
        assert "Test message" in body
        assert "Warning" in body

    def test_build_notion_comment_blocks(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test building Notion comment blocks."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test message",
            title="Test Title",
            urgency=NotificationUrgency.CRITICAL,
        )
        blocks = notification_harness._build_notion_comment_blocks(config)
        assert len(blocks) > 0
        assert blocks[0]["type"] == "divider"

    def test_notion_urgency_color(self, notification_harness: NotificationHarness) -> None:
        """Test Notion urgency color mapping."""
        color = notification_harness._notion_urgency_color(NotificationUrgency.LOW)
        assert color == "gray_background"

        color = notification_harness._notion_urgency_color(NotificationUrgency.CRITICAL)
        assert color == "red_background"


# =============================================================================
# Tests for Telegram
# =============================================================================


class TestTelegramMethods:
    """Tests for Telegram-related methods."""

    def test_urgency_to_tier(self, notification_harness: NotificationHarness) -> None:
        """Test urgency to tier conversion."""
        tier = notification_harness._urgency_to_tier(NotificationUrgency.LOW)
        assert tier == "watch"

        tier = notification_harness._urgency_to_tier(NotificationUrgency.CRITICAL)
        assert tier == "desktop"


# =============================================================================
# Tests for factory function
# =============================================================================


class TestFactoryFunction:
    """Tests for create_notification_harness factory."""

    @patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"})
    def test_create_from_env(self) -> None:
        """Test creating from environment variables."""
        harness = create_notification_harness()
        assert harness.slack_webhook_url == "https://hooks.slack.com/test"

    @patch.dict(
        "os.environ",
        {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
            "GITHUB_TOKEN": "token",
            "NOTION_API_TOKEN": "token",
        },
    )
    def test_create_multiple_channels(self) -> None:
        """Test creating with multiple channels."""
        harness = create_notification_harness()
        assert harness.has_channel(NotificationChannel.SLACK)
        assert harness.has_channel(NotificationChannel.GITHUB_ISSUE)
        assert harness.has_channel(NotificationChannel.NOTION_COMMENT)


# =============================================================================
# Tests for HTTP client
# =============================================================================


class TestHTTPClient:
    """Tests for HTTP client management."""

    @pytest.mark.asyncio
    async def test_get_client(self, notification_harness: NotificationHarness) -> None:
        """Test getting HTTP client."""
        client = await notification_harness._get_client()
        assert client is not None

    @pytest.mark.asyncio
    async def test_close(self, notification_harness: NotificationHarness) -> None:
        """Test closing HTTP client."""
        await notification_harness._get_client()
        await notification_harness.close()
        # After close, should create new client
        client = await notification_harness._get_client()
        assert client is not None


# =============================================================================
# Edge case tests
# =============================================================================


class TestEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_notify_exception_handling(
        self, notification_harness: NotificationHarness
    ) -> None:
        """Test that exceptions are handled gracefully."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )
        with patch.object(
            notification_harness, "_send_slack", side_effect=Exception("Test error")
        ):
            results = await notification_harness.notify(config)
            assert results[0].success is False
            assert "Test error" in results[0].error

    def test_notification_config_no_channels(self) -> None:
        """Test notification config with no channels."""
        config = NotificationConfig(
            channels=[],
            message="Test",
        )
        assert config.channels == []

    def test_build_slack_blocks_no_title(self, notification_harness: NotificationHarness) -> None:
        """Test building blocks without title."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )
        blocks = notification_harness._build_slack_blocks(config)
        # Should not have header block
        assert not any(b.get("type") == "header" for b in blocks)

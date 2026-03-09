"""
Pure unit tests for forge_harness.notification_harness

Covers:
- NotificationHarness initialization
- has_channel() checks
- _build_slack_blocks() formatting
- _build_email_html() formatting
- _build_github_comment_body() formatting
- _build_notion_comment_blocks() formatting
- _urgency_emoji() mapping
- _notion_urgency_color() mapping
- record_response() and get_response()
- clear_pending()
"""

from __future__ import annotations

from datetime import UTC, datetime
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
)


class TestNotificationHarnessInit:
    """Tests for NotificationHarness initialization."""

    def test_init_default(self):
        """Test default initialization."""
        harness = NotificationHarness()
        assert harness.slack_webhook_url is None
        assert harness.email_config is None
        assert harness.github_token is None
        assert harness.notion_token is None
        assert harness.telegram_bot_token is None
        assert harness.telegram_allowed_users == set()
        assert harness._pending_responses == {}

    def test_init_with_all_params(self):
        """Test initialization with all parameters."""
        email = EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="test",
            password="pass",
            from_address="test@example.com",
        )

        harness = NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/test",
            email_config=email,
            github_token="gh_token",
            notion_token="notion_token",
            telegram_bot_token="tg_token",
            telegram_allowed_users={"user1", "user2"},
        )

        assert harness.slack_webhook_url == "https://hooks.slack.com/test"
        assert harness.email_config is email
        assert harness.github_token == "gh_token"
        assert harness.notion_token == "notion_token"
        assert harness.telegram_bot_token == "tg_token"
        assert harness.telegram_allowed_users == {"user1", "user2"}


class TestHasChannel:
    """Tests for has_channel method."""

    def test_has_slack_configured(self):
        """Test Slack detection when configured."""
        harness = NotificationHarness(slack_webhook_url="https://hooks.slack.com/test")
        assert harness.has_channel(NotificationChannel.SLACK) is True

    def test_has_slack_not_configured(self):
        """Test Slack detection when not configured."""
        harness = NotificationHarness()
        assert harness.has_channel(NotificationChannel.SLACK) is False

    def test_has_email_configured(self):
        """Test email detection when configured."""
        email = EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="test",
            password="pass",
            from_address="test@example.com",
        )
        harness = NotificationHarness(email_config=email)
        assert harness.has_channel(NotificationChannel.EMAIL) is True

    def test_has_email_not_configured(self):
        """Test email detection when not configured."""
        harness = NotificationHarness()
        assert harness.has_channel(NotificationChannel.EMAIL) is False

    def test_has_github_configured(self):
        """Test GitHub detection when configured."""
        harness = NotificationHarness(github_token="gh_token")
        assert harness.has_channel(NotificationChannel.GITHUB_ISSUE) is True

    def test_has_github_not_configured(self):
        """Test GitHub detection when not configured."""
        harness = NotificationHarness()
        assert harness.has_channel(NotificationChannel.GITHUB_ISSUE) is False

    def test_has_notion_configured(self):
        """Test Notion detection when configured."""
        harness = NotificationHarness(notion_token="notion_token")
        assert harness.has_channel(NotificationChannel.NOTION_COMMENT) is True

    def test_has_notion_not_configured(self):
        """Test Notion detection when not configured."""
        harness = NotificationHarness()
        assert harness.has_channel(NotificationChannel.NOTION_COMMENT) is False

    def test_has_telegram_configured(self):
        """Test Telegram detection when configured."""
        harness = NotificationHarness(
            telegram_bot_token="tg_token",
            telegram_allowed_users={"user1"},
        )
        assert harness.has_channel(NotificationChannel.TELEGRAM) is True

    def test_has_telegram_not_configured(self):
        """Test Telegram detection when not configured."""
        harness = NotificationHarness()
        assert harness.has_channel(NotificationChannel.TELEGRAM) is False

    def test_has_telegram_no_users(self):
        """Test Telegram detection when no allowed users."""
        harness = NotificationHarness(telegram_bot_token="tg_token")
        assert harness.has_channel(NotificationChannel.TELEGRAM) is False


class TestBuildSlackBlocks:
    """Tests for _build_slack_blocks method."""

    def test_build_with_title(self):
        """Test blocks with title."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
            title="Test Title",
        )

        blocks = harness._build_slack_blocks(config)

        assert len(blocks) >= 2
        assert blocks[0]["type"] == "header"
        assert "Test Title" in blocks[0]["text"]["text"]

    def test_build_without_title(self):
        """Test blocks without title."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )

        blocks = harness._build_slack_blocks(config)

        assert len(blocks) >= 1
        assert all(b["type"] != "header" for b in blocks)

    def test_build_with_mentions(self):
        """Test blocks with user mentions."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
            mention_users=["@user1", "@user2"],
        )

        blocks = harness._build_slack_blocks(config)

        section_block = [b for b in blocks if b["type"] == "section"][0]
        assert "@user1" in section_block["text"]["text"]
        assert "@user2" in section_block["text"]["text"]

    def test_build_with_action_url(self):
        """Test blocks with action URL."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
            action_url="https://example.com/action",
            action_required=True,
        )

        blocks = harness._build_slack_blocks(config)

        actions_blocks = [b for b in blocks if b["type"] == "actions"]
        assert len(actions_blocks) == 1

    def test_build_with_metadata(self):
        """Test blocks with metadata."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
            metadata={"key1": "value1", "key2": "value2"},
        )

        blocks = harness._build_slack_blocks(config)

        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) == 1


class TestUrgencyEmoji:
    """Tests for _urgency_emoji method."""

    def test_low_urgency(self):
        """Test low urgency emoji."""
        harness = NotificationHarness()
        emoji = harness._urgency_emoji(NotificationUrgency.LOW)
        assert emoji == ""

    def test_normal_urgency(self):
        """Test normal urgency emoji."""
        harness = NotificationHarness()
        emoji = harness._urgency_emoji(NotificationUrgency.NORMAL)
        assert emoji == ""

    def test_high_urgency(self):
        """Test high urgency emoji."""
        harness = NotificationHarness()
        emoji = harness._urgency_emoji(NotificationUrgency.HIGH)
        assert emoji == ""

    def test_critical_urgency(self):
        """Test critical urgency emoji."""
        harness = NotificationHarness()
        emoji = harness._urgency_emoji(NotificationUrgency.CRITICAL)
        assert emoji == ""


class TestBuildEmailHtml:
    """Tests for _build_email_html method."""

    def test_build_basic_email(self):
        """Test basic email HTML generation."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message body",
            title="Test Title",
        )

        html = harness._build_email_html(config)

        assert "Test Title" in html
        assert "Test message body" in html
        assert "<!DOCTYPE html>" in html

    def test_build_with_action_url(self):
        """Test email with action button."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message",
            action_url="https://example.com/action",
            action_required=True,
        )

        html = harness._build_email_html(config)

        assert "https://example.com/action" in html
        assert "Take Action" in html

    def test_build_with_metadata(self):
        """Test email with metadata."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message",
            metadata={"Status": "Pending", "Priority": "High"},
        )

        html = harness._build_email_html(config)

        assert "Status" in html
        assert "Pending" in html

    def test_urgency_colors(self):
        """Test different urgency colors."""
        harness = NotificationHarness()

        for urgency, expected_color in [
            (NotificationUrgency.LOW, "#6b7280"),
            (NotificationUrgency.NORMAL, "#2563eb"),
            (NotificationUrgency.HIGH, "#f59e0b"),
            (NotificationUrgency.CRITICAL, "#dc2626"),
        ]:
            config = NotificationConfig(
                channels=[NotificationChannel.EMAIL],
                message="Test",
                title="Urgency Test",
                urgency=urgency,
            )
            html = harness._build_email_html(config)
            assert expected_color in html


class TestBuildGithubCommentBody:
    """Tests for _build_github_comment_body method."""

    def test_build_basic_comment(self):
        """Test basic GitHub comment."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test message",
            title="Test Title",
        )

        body = harness._build_github_comment_body(config)

        assert "## Test Title" in body
        assert "Test message" in body
        assert "Sent by FORGE Harness" in body

    def test_build_with_action_url(self):
        """Test GitHub comment with action link."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test message",
            action_url="https://example.com/action",
            action_required=True,
        )

        body = harness._build_github_comment_body(config)

        assert "[Take Action]" in body
        assert "https://example.com/action" in body

    def test_build_with_metadata(self):
        """Test GitHub comment with metadata."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test message",
            metadata={"Status": "Pending", "repo": "test", "issue_number": 1},
        )

        body = harness._build_github_comment_body(config)

        assert "Status" in body
        assert "Pending" in body
        assert "repo" not in body  # Filtered out
        assert "issue_number" not in body  # Filtered out


class TestBuildNotionCommentBlocks:
    """Tests for _build_notion_comment_blocks method."""

    def test_build_basic_blocks(self):
        """Test basic Notion blocks."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test message",
            title="Test Title",
        )

        blocks = harness._build_notion_comment_blocks(config)

        assert len(blocks) >= 3
        assert blocks[0]["type"] == "divider"
        assert blocks[1]["type"] == "callout"
        assert blocks[2]["type"] == "paragraph"

    def test_build_without_title(self):
        """Test Notion blocks without title."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test message",
        )

        blocks = harness._build_notion_comment_blocks(config)

        assert blocks[1]["callout"]["rich_text"][0]["text"]["content"] == "FORGE Notification"


class TestNotionUrgencyColor:
    """Tests for _notion_urgency_color method."""

    def test_low_urgency(self):
        """Test low urgency color."""
        harness = NotificationHarness()
        color = harness._notion_urgency_color(NotificationUrgency.LOW)
        assert color == "gray_background"

    def test_normal_urgency(self):
        """Test normal urgency color."""
        harness = NotificationHarness()
        color = harness._notion_urgency_color(NotificationUrgency.NORMAL)
        assert color == "blue_background"

    def test_high_urgency(self):
        """Test high urgency color."""
        harness = NotificationHarness()
        color = harness._notion_urgency_color(NotificationUrgency.HIGH)
        assert color == "yellow_background"

    def test_critical_urgency(self):
        """Test critical urgency color."""
        harness = NotificationHarness()
        color = harness._notion_urgency_color(NotificationUrgency.CRITICAL)
        assert color == "red_background"


class TestRecordResponse:
    """Tests for record_response method."""

    def test_record_response(self):
        """Test recording a response."""
        harness = NotificationHarness()

        response = harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
            message="Looks good",
            responder="@user",
        )

        assert isinstance(response, HumanResponse)
        assert response.notification_id == "notif_123"
        assert response.channel == NotificationChannel.SLACK
        assert response.response_type == "approved"
        assert response.message == "Looks good"
        assert response.responder == "@user"
        assert harness._pending_responses["notif_123"] is response

    def test_get_response_existing(self):
        """Test getting an existing response."""
        harness = NotificationHarness()
        harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
        )

        response = harness.get_response("notif_123")

        assert response is not None
        assert response.response_type == "approved"

    def test_get_response_missing(self):
        """Test getting a non-existent response."""
        harness = NotificationHarness()

        response = harness.get_response("notif_missing")

        assert response is None


class TestClearPending:
    """Tests for clear_pending method."""

    def test_clear_existing(self):
        """Test clearing an existing pending response."""
        harness = NotificationHarness()
        harness.record_response(
            notification_id="notif_123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
        )

        harness.clear_pending("notif_123")

        assert "notif_123" not in harness._pending_responses

    def test_clear_nonexistent(self):
        """Test clearing a non-existent pending response."""
        harness = NotificationHarness()

        # Should not raise
        harness.clear_pending("notif_missing")


class TestNotificationConfig:
    """Tests for NotificationConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        assert config.title is None
        assert config.metadata == {}
        assert config.urgency == NotificationUrgency.NORMAL
        assert config.action_required is False
        assert config.action_url is None
        assert config.mention_users == []

    def test_custom_values(self):
        """Test custom configuration values."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL],
            message="Test",
            title="Title",
            metadata={"key": "value"},
            urgency=NotificationUrgency.HIGH,
            action_required=True,
            action_url="https://example.com",
            mention_users=["@user1"],
        )

        assert config.title == "Title"
        assert config.metadata == {"key": "value"}
        assert config.urgency == NotificationUrgency.HIGH
        assert config.action_required is True
        assert config.action_url == "https://example.com"
        assert config.mention_users == ["@user1"]


class TestNotificationResult:
    """Tests for NotificationResult dataclass."""

    def test_default_timestamp(self):
        """Test default timestamp is set."""
        result = NotificationResult(
            channel=NotificationChannel.SLACK,
            success=True,
        )

        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo == UTC

    def test_success_result(self):
        """Test successful result."""
        result = NotificationResult(
            channel=NotificationChannel.SLACK,
            success=True,
            message_id="msg_123",
        )

        assert result.success is True
        assert result.message_id == "msg_123"
        assert result.error is None

    def test_failure_result(self):
        """Test failed result."""
        result = NotificationResult(
            channel=NotificationChannel.SLACK,
            success=False,
            error="Network error",
        )

        assert result.success is False
        assert result.error == "Network error"
        assert result.message_id is None


class TestEmailConfig:
    """Tests for EmailConfig dataclass."""

    def test_default_from_name(self):
        """Test default from name."""
        config = EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="test",
            password="pass",
            from_address="test@example.com",
        )

        assert config.from_name == "FORGE Harness"
        assert config.use_tls is True

    def test_custom_from_name(self):
        """Test custom from name."""
        config = EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="test",
            password="pass",
            from_address="test@example.com",
            from_name="Custom Name",
            use_tls=False,
        )

        assert config.from_name == "Custom Name"
        assert config.use_tls is False

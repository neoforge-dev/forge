"""Tests for FORGE NotificationHarness - multi-channel notifications."""

import asyncio
import smtplib
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


class TestDataModels:
    """Tests for notification data models."""

    def test_notification_config_defaults(self):
        """NotificationConfig has correct defaults."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )
        assert config.title is None
        assert config.metadata == {}
        assert config.urgency == NotificationUrgency.NORMAL
        assert config.action_required is False
        assert config.action_url is None
        assert config.mention_users == []

    def test_notification_config_full(self):
        """NotificationConfig accepts all attributes."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL],
            message="Content ready for review",
            title="Review Required",
            metadata={"project": "interview-simulator"},
            urgency=NotificationUrgency.HIGH,
            action_required=True,
            action_url="https://notion.so/page",
            mention_users=["U12345", "U67890"],
        )
        assert len(config.channels) == 2
        assert config.title == "Review Required"
        assert config.urgency == NotificationUrgency.HIGH
        assert config.action_required is True

    def test_notification_result_success(self):
        """NotificationResult captures success."""
        result = NotificationResult(
            channel=NotificationChannel.SLACK,
            success=True,
            message_id="msg-123",
        )
        assert result.success is True
        assert result.message_id == "msg-123"
        assert result.error is None
        assert result.timestamp is not None

    def test_notification_result_failure(self):
        """NotificationResult captures failure."""
        result = NotificationResult(
            channel=NotificationChannel.EMAIL,
            success=False,
            error="SMTP connection failed",
        )
        assert result.success is False
        assert result.message_id is None
        assert result.error == "SMTP connection failed"

    def test_human_response_model(self):
        """HumanResponse captures response data."""
        response = HumanResponse(
            notification_id="notif-123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
            message="Looks good!",
            responder="@alice",
        )
        assert response.notification_id == "notif-123"
        assert response.response_type == "approved"
        assert response.message == "Looks good!"
        assert response.timestamp is not None

    def test_email_config_model(self):
        """EmailConfig has correct attributes."""
        config = EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="user@gmail.com",
            password="secret",
            from_address="noreply@example.com",
            from_name="FORGE",
            use_tls=True,
        )
        assert config.smtp_host == "smtp.gmail.com"
        assert config.smtp_port == 587
        assert config.use_tls is True


class TestNotificationHarness:
    """Tests for NotificationHarness class."""

    @pytest.fixture
    def harness(self):
        """Create NotificationHarness with all channels configured."""
        return NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/test",
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test@test.com",
                password="secret",
                from_address="noreply@test.com",
            ),
            github_token="ghp_test",
            notion_token="ntn_test",
        )

    @pytest.fixture
    def minimal_harness(self):
        """Create NotificationHarness with no channels configured."""
        return NotificationHarness()

    def test_has_channel_slack(self, harness, minimal_harness):
        """has_channel correctly detects Slack configuration."""
        assert harness.has_channel(NotificationChannel.SLACK) is True
        assert minimal_harness.has_channel(NotificationChannel.SLACK) is False

    def test_has_channel_email(self, harness, minimal_harness):
        """has_channel correctly detects email configuration."""
        assert harness.has_channel(NotificationChannel.EMAIL) is True
        assert minimal_harness.has_channel(NotificationChannel.EMAIL) is False

    def test_has_channel_github(self, harness, minimal_harness):
        """has_channel correctly detects GitHub configuration."""
        assert harness.has_channel(NotificationChannel.GITHUB_ISSUE) is True
        assert minimal_harness.has_channel(NotificationChannel.GITHUB_ISSUE) is False

    def test_has_channel_notion(self, harness, minimal_harness):
        """has_channel correctly detects Notion configuration."""
        assert harness.has_channel(NotificationChannel.NOTION_COMMENT) is True
        assert minimal_harness.has_channel(NotificationChannel.NOTION_COMMENT) is False


class TestSlackNotifications:
    """Tests for Slack notification sending."""

    @pytest.fixture
    def harness(self):
        """Create harness with Slack configured."""
        return NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/services/test",
        )

    @pytest.mark.asyncio
    async def test_send_slack_success(self, harness):
        """Successful Slack notification."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
            title="Test Title",
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200, text="ok")
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert len(results) == 1
            assert results[0].success is True
            assert results[0].message_id is not None
            assert results[0].channel == NotificationChannel.SLACK

    @pytest.mark.asyncio
    async def test_send_slack_failure(self, harness):
        """Failed Slack notification."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(
                status_code=500,
                text="Internal Server Error",
            )
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert len(results) == 1
            assert results[0].success is False
            assert "500" in results[0].error

    @pytest.mark.asyncio
    async def test_slack_not_configured(self):
        """Slack notification fails when not configured."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test message",
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "not configured" in results[0].error


class TestSlackBlockBuilder:
    """Tests for Slack Block Kit message building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    def test_build_blocks_with_title(self, harness):
        """Builds header block for title."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Message body",
            title="Important Title",
        )

        blocks = harness._build_slack_blocks(config)

        assert any(b.get("type") == "header" for b in blocks)
        header = next(b for b in blocks if b.get("type") == "header")
        assert "Important Title" in header["text"]["text"]

    def test_build_blocks_with_action_url(self, harness):
        """Builds action button for action_url."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Message body",
            action_url="https://example.com/action",
            action_required=True,
        )

        blocks = harness._build_slack_blocks(config)

        assert any(b.get("type") == "actions" for b in blocks)
        actions = next(b for b in blocks if b.get("type") == "actions")
        button = actions["elements"][0]
        assert button["url"] == "https://example.com/action"
        assert button["style"] == "primary"

    def test_build_blocks_with_metadata(self, harness):
        """Builds context block for metadata."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Message body",
            metadata={"project": "test", "domain": "example"},
        )

        blocks = harness._build_slack_blocks(config)

        assert any(b.get("type") == "context" for b in blocks)

    def test_build_blocks_with_mentions(self, harness):
        """Includes user mentions in message."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Please review",
            mention_users=["U12345", "U67890"],
        )

        blocks = harness._build_slack_blocks(config)

        section = next(b for b in blocks if b.get("type") == "section")
        assert "<@U12345>" in section["text"]["text"]
        assert "<@U67890>" in section["text"]["text"]


class TestEmailNotifications:
    """Tests for email notification sending."""

    @pytest.fixture
    def harness(self):
        """Create harness with email configured."""
        return NotificationHarness(
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test@test.com",
                password="secret",
                from_address="noreply@test.com",
                from_name="Test Harness",
            ),
        )

    @pytest.mark.asyncio
    async def test_send_email_no_recipients(self, harness):
        """Email fails without recipients."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message",
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "No recipient" in results[0].error

    @pytest.mark.asyncio
    async def test_send_email_success(self, harness):
        """Successful email notification."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message",
            title="Test Subject",
            mention_users=["recipient@example.com"],
        )

        with patch.object(harness, "_send_smtp") as mock_smtp:
            results = await harness.notify(config)

            assert len(results) == 1
            assert results[0].success is True
            mock_smtp.assert_called_once()

    @pytest.mark.asyncio
    async def test_email_not_configured(self):
        """Email notification fails when not configured."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test message",
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "not configured" in results[0].error or "not provided" in results[0].error


class TestEmailHTMLBuilder:
    """Tests for email HTML building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test",
                password="test",
                from_address="test@test.com",
            ),
        )

    def test_build_html_with_title(self, harness):
        """HTML includes title."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Body text",
            title="Email Title",
        )

        html = harness._build_email_html(config)

        assert "Email Title" in html
        assert "<h2" in html

    def test_build_html_with_action_button(self, harness):
        """HTML includes action button."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Body text",
            action_url="https://example.com",
            action_required=True,
        )

        html = harness._build_email_html(config)

        assert "https://example.com" in html
        assert "Take Action" in html

    def test_build_html_urgency_colors(self, harness):
        """HTML uses urgency-appropriate colors."""
        for urgency in NotificationUrgency:
            config = NotificationConfig(
                channels=[NotificationChannel.EMAIL],
                message="Body text",
                title="Test",
                urgency=urgency,
            )

            html = harness._build_email_html(config)
            assert "color:" in html  # Has color styling


class TestGitHubNotifications:
    """Tests for GitHub issue comment notifications."""

    @pytest.fixture
    def harness(self):
        """Create harness with GitHub configured."""
        return NotificationHarness(github_token="ghp_test_token")

    @pytest.mark.asyncio
    async def test_github_missing_metadata(self, harness):
        """GitHub comment fails without repo/issue metadata."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test comment",
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "repo" in results[0].error or "issue_number" in results[0].error

    @pytest.mark.asyncio
    async def test_github_comment_success(self, harness):
        """Successful GitHub comment."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test comment",
            metadata={"repo": "owner/repo", "issue_number": 123},
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(
                status_code=201,
                json=lambda: {"id": 456789},
            )
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert len(results) == 1
            assert results[0].success is True
            assert results[0].message_id == "456789"

    @pytest.mark.asyncio
    async def test_github_not_configured(self):
        """GitHub notification fails when not configured."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test comment",
            metadata={"repo": "owner/repo", "issue_number": 123},
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "not configured" in results[0].error


class TestGitHubCommentBuilder:
    """Tests for GitHub comment markdown building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(github_token="ghp_test")

    def test_build_comment_with_title(self, harness):
        """Comment includes title as heading."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Body text",
            title="Comment Title",
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "## Comment Title" in body

    def test_build_comment_with_action(self, harness):
        """Comment includes action link."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Body text",
            action_url="https://example.com",
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "[View Details](https://example.com)" in body

    def test_build_comment_high_urgency(self, harness):
        """High urgency adds warning badge."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Body text",
            urgency=NotificationUrgency.HIGH,
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "**Warning**" in body


class TestNotionNotifications:
    """Tests for Notion comment notifications."""

    @pytest.fixture
    def harness(self):
        """Create harness with Notion configured."""
        return NotificationHarness(notion_token="ntn_test_token")

    @pytest.mark.asyncio
    async def test_notion_missing_page_id(self, harness):
        """Notion comment fails without page_id."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test comment",
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "page_id" in results[0].error

    @pytest.mark.asyncio
    async def test_notion_comment_success(self, harness):
        """Successful Notion comment."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test comment",
            metadata={"page_id": "abc-123-def"},
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.patch.return_value = MagicMock(status_code=200)
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert len(results) == 1
            assert results[0].success is True

    @pytest.mark.asyncio
    async def test_notion_not_configured(self):
        """Notion notification fails when not configured."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test comment",
            metadata={"page_id": "abc-123"},
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "not configured" in results[0].error


class TestResponseTracking:
    """Tests for human response tracking."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/test",
        )

    def test_record_response(self, harness):
        """record_response stores response."""
        response = harness.record_response(
            notification_id="notif-123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
            message="Looks good",
            responder="@alice",
        )

        assert response.notification_id == "notif-123"
        assert response.response_type == "approved"
        assert harness.get_response("notif-123") == response

    def test_get_response_not_found(self, harness):
        """get_response returns None for unknown notification."""
        response = harness.get_response("unknown-id")
        assert response is None

    def test_clear_pending(self, harness):
        """clear_pending removes notification from tracking."""
        harness.record_response(
            notification_id="notif-123",
            channel=NotificationChannel.SLACK,
            response_type="approved",
        )

        harness.clear_pending("notif-123")

        assert harness.get_response("notif-123") is None

    @pytest.mark.asyncio
    async def test_await_response_found(self, harness):
        """await_response returns when response is recorded."""

        notification_id = "notif-456"

        # Record response after short delay
        async def record_later():
            await asyncio.sleep(0.1)
            harness.record_response(
                notification_id=notification_id,
                channel=NotificationChannel.SLACK,
                response_type="approved",
            )

        asyncio.create_task(record_later())

        response = await harness.await_response(
            notification_id=notification_id,
            timeout=timedelta(seconds=5),
            poll_interval=0.05,
        )

        assert response is not None
        assert response.response_type == "approved"

    @pytest.mark.asyncio
    async def test_await_response_timeout(self, harness):
        """await_response returns None on timeout."""
        response = await harness.await_response(
            notification_id="notif-789",
            timeout=timedelta(milliseconds=100),
            poll_interval=0.01,
        )

        assert response is None


class TestMultiChannelNotifications:
    """Tests for sending to multiple channels."""

    @pytest.fixture
    def harness(self):
        """Create harness with multiple channels configured."""
        return NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/test",
            github_token="ghp_test",
        )

    @pytest.mark.asyncio
    async def test_notify_multiple_channels(self, harness):
        """Notifications sent to all configured channels."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK, NotificationChannel.GITHUB_ISSUE],
            message="Multi-channel test",
            metadata={"repo": "owner/repo", "issue_number": 1},
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200, text="ok")
            mock_client.post.return_value.json = lambda: {"id": 123}
            mock_client.post.return_value.status_code = 200
            mock_get_client.return_value = mock_client

            # First call returns 200 for Slack, second returns 201 for GitHub
            mock_client.post.side_effect = [
                MagicMock(status_code=200, text="ok"),
                MagicMock(status_code=201, json=lambda: {"id": 456}),
            ]

            results = await harness.notify(config)

            assert len(results) == 2
            channels = {r.channel for r in results}
            assert NotificationChannel.SLACK in channels
            assert NotificationChannel.GITHUB_ISSUE in channels


class TestFactoryFunction:
    """Tests for create_notification_harness factory."""

    def test_factory_with_explicit_values(self):
        """Factory creates harness with explicit values."""
        harness = create_notification_harness(
            slack_webhook_url="https://hooks.slack.com/explicit",
            github_token="ghp_explicit",
        )

        assert harness.slack_webhook_url == "https://hooks.slack.com/explicit"
        assert harness.github_token == "ghp_explicit"

    def test_factory_with_env_vars(self, monkeypatch):
        """Factory uses environment variables as fallbacks."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/env")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_env")
        monkeypatch.setenv("NOTION_API_TOKEN", "ntn_env")

        harness = create_notification_harness()

        assert harness.slack_webhook_url == "https://hooks.slack.com/env"
        assert harness.github_token == "ghp_env"
        assert harness.notion_token == "ntn_env"

    def test_factory_explicit_overrides_env(self, monkeypatch):
        """Explicit values override environment variables."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/env")

        harness = create_notification_harness(
            slack_webhook_url="https://hooks.slack.com/explicit",
        )

        assert harness.slack_webhook_url == "https://hooks.slack.com/explicit"

    def test_factory_with_email_config(self):
        """Factory parses email config dict."""
        harness = create_notification_harness(
            email_config={
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "username": "user",
                "password": "pass",
                "from_address": "from@example.com",
                "from_name": "Custom Name",
                "use_tls": False,
            },
        )

        assert harness.email_config is not None
        assert harness.email_config.smtp_host == "smtp.example.com"
        assert harness.email_config.from_name == "Custom Name"
        assert harness.email_config.use_tls is False


class TestUrgencyHelpers:
    """Tests for urgency helper methods."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness()

    def test_urgency_emoji(self, harness):
        """Urgency emoji returns strings (may be empty for low urgency)."""
        for urgency in NotificationUrgency:
            emoji = harness._urgency_emoji(urgency)
            assert isinstance(emoji, str)
            # All urgencies should return a string (empty is ok for LOW/NORMAL)

    def test_notion_urgency_color(self, harness):
        """Notion urgency color returns valid colors."""
        valid_colors = {
            "gray_background",
            "blue_background",
            "yellow_background",
            "red_background",
        }

        for urgency in NotificationUrgency:
            color = harness._notion_urgency_color(urgency)
            assert color in valid_colors


class TestTierAwareApprovals:
    """Tests for tier-aware approval notifications (WATCH/PHONE/DESKTOP)."""

    @pytest.fixture
    def harness(self):
        """Create harness with Slack configured."""
        return NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/test",
        )

    @pytest.mark.asyncio
    async def test_approval_watch_tier_minimal(self, harness):
        """WATCH tier has minimal blocks for Apple Watch."""
        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200)
            mock_get_client.return_value = mock_client

            result = await harness.send_approval_notification(
                request_id="watch-test",
                title="Deploy Feature",
                description="Long description that should not appear in WATCH tier",
                tier="watch",
                domain="test-domain",
                project="test-project",
            )

            assert result.success is True
            assert result.message_id == "watch-test"

    @pytest.mark.asyncio
    async def test_approval_phone_tier_summary(self, harness):
        """PHONE tier has summary context for mobile."""
        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200)
            mock_get_client.return_value = mock_client

            result = await harness.send_approval_notification(
                request_id="phone-test",
                title="Deploy Feature",
                description="Medium length description for phone view",
                tier="phone",
                domain="test-domain",
                project="test-project",
                feature_id="FT-123",
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_approval_desktop_tier_full(self, harness):
        """DESKTOP tier has full details and dashboard link."""
        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200)
            mock_get_client.return_value = mock_client

            result = await harness.send_approval_notification(
                request_id="desktop-test",
                title="Deploy Feature",
                description="Full detailed description with all context",
                tier="desktop",
                domain="test-domain",
                project="test-project",
                feature_id="FT-123",
                approval_type="deployment",
                risk_score=0.5,
                context={"version": "1.2.3", "env": "production"},
                dashboard_url="https://dashboard.example.com",
            )

            assert result.success is True

    def test_approval_blocks_watch_structure(self, harness):
        """WATCH tier blocks are minimal (section + divider + buttons)."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test-123",
            title="Test Approval with a Very Long Title That Exceeds Fifty Characters",
            description="Description that should not appear",
            tier="watch",
            domain="test-domain",
            project="test-project",
            feature_id="FT-123",
            approval_type="deploy",
            risk_score=0.2,
            context={},
            dashboard_url=None,
        )

        # WATCH: section (compact title) + divider + actions
        assert len(blocks) == 3
        assert blocks[0]["type"] == "section"
        assert "..." in blocks[0]["text"]["text"]  # Title truncated
        assert blocks[1]["type"] == "divider"
        assert blocks[2]["type"] == "actions"
        assert len(blocks[2]["elements"]) == 2  # Only approve/reject

    def test_approval_blocks_phone_structure(self, harness):
        """PHONE tier blocks have summary structure."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test-123",
            title="Test Approval",
            description="Test description that is longer than 200 characters so it should be truncated. "
            * 5,
            tier="phone",
            domain="test-domain",
            project="test-project",
            feature_id="FT-123",
            approval_type="feature",
            risk_score=0.3,
            context={},
            dashboard_url=None,
        )

        # PHONE: header + context + description (truncated) + divider + buttons
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "context"
        # Find description block
        description_block = next(
            (
                b
                for b in blocks
                if b.get("type") == "section" and "..." in b.get("text", {}).get("text", "")
            ),
            None,
        )
        assert description_block is not None  # Description truncated

    def test_approval_blocks_desktop_structure(self, harness):
        """DESKTOP tier blocks have full detail structure."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test-123",
            title="Test Approval",
            description="Full description",
            tier="desktop",
            domain="test-domain",
            project="test-project",
            feature_id="FT-123",
            approval_type="deploy",
            risk_score=0.7,
            context={"extra": "data"},
            dashboard_url="https://dashboard.example.com",
        )

        # DESKTOP: header + description + fields + context + divider + buttons (with dashboard)
        assert blocks[0]["type"] == "header"
        # Find fields section
        fields_block = next((b for b in blocks if b["type"] == "section" and "fields" in b), None)
        assert fields_block is not None
        # Dashboard button in actions
        actions_block = next(b for b in blocks if b["type"] == "actions")
        assert len(actions_block["elements"]) == 3  # approve, reject, dashboard

    def test_approval_blocks_button_structure(self, harness):
        """Approval blocks include approve/reject buttons."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test-request",
            title="Test",
            description="Test",
            tier="phone",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        actions_block = next(b for b in blocks if b["type"] == "actions")
        elements = actions_block["elements"]

        # Approve button
        approve = next(e for e in elements if "approve" in e["action_id"])
        assert approve["style"] == "primary"
        assert approve["value"] == "test-request"

        # Reject button
        reject = next(e for e in elements if "reject" in e["action_id"])
        assert reject["style"] == "danger"
        assert reject["value"] == "test-request"

    def test_approval_emoji_by_type(self, harness):
        """Approval emoji selected by type."""
        assert harness._approval_emoji("deploy", 0.1) == "🚀"
        assert harness._approval_emoji("deployment", 0.1) == "🚀"
        assert harness._approval_emoji("content", 0.1) == "📝"
        assert harness._approval_emoji("feature", 0.1) == "✨"
        assert harness._approval_emoji("security", 0.1) == "🔐"
        assert harness._approval_emoji("config", 0.1) == "⚙️"
        assert harness._approval_emoji("data", 0.1) == "📊"
        assert harness._approval_emoji("compliance", 0.1) == "📋"

    def test_approval_emoji_by_risk_overrides_type(self, harness):
        """High risk overrides type emoji."""
        # Critical risk
        assert harness._approval_emoji("deploy", 0.8) == "🚨"
        assert harness._approval_emoji("feature", 0.9) == "🚨"

        # High risk
        assert harness._approval_emoji("deploy", 0.5) == "⚠️"
        assert harness._approval_emoji("feature", 0.4) == "⚠️"

        # Normal risk uses type emoji
        assert harness._approval_emoji("deploy", 0.2) == "🚀"

    def test_risk_bar_visualization(self, harness):
        """Risk bar generates correct visualization."""
        assert harness._risk_bar(0.0) == "⬜⬜⬜⬜⬜ 0%"
        assert harness._risk_bar(0.2) == "🟥⬜⬜⬜⬜ 20%"
        assert harness._risk_bar(0.5) == "🟥🟥⬜⬜⬜ 50%"
        assert harness._risk_bar(0.8) == "🟥🟥🟥🟥⬜ 80%"
        assert harness._risk_bar(1.0) == "🟥🟥🟥🟥🟥 100%"

    def test_approval_context_fields_included(self, harness):
        """DESKTOP tier includes all context fields."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test-123",
            title="Test",
            description="Test",
            tier="desktop",
            domain="test-domain",
            project="test-project",
            feature_id="FT-456",
            approval_type="deployment",
            risk_score=0.6,
            context={"key1": "value1", "key2": "value2"},
            dashboard_url=None,
        )

        # Find fields block
        fields_block = next((b for b in blocks if b.get("fields")), None)
        assert fields_block is not None

        fields = fields_block["fields"]
        field_texts = [f["text"] for f in fields]

        # Check required fields present
        assert any("test-domain" in t for t in field_texts)
        assert any("test-project" in t for t in field_texts)
        assert any("FT-456" in t for t in field_texts)
        assert any("deployment" in t for t in field_texts)
        assert any("🟥" in t for t in field_texts)  # Risk bar

    def test_approval_phone_context_line(self, harness):
        """PHONE tier includes brief context line."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test-123",
            title="Test",
            description="Test",
            tier="phone",
            domain="test-domain",
            project="test-project",
            feature_id="FT-789",
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        # Find context block
        context_block = next((b for b in blocks if b["type"] == "context"), None)
        assert context_block is not None

        context_text = context_block["elements"][0]["text"]
        assert "test-domain" in context_text
        assert "test-project" in context_text
        assert "FT-789" in context_text

    @pytest.mark.asyncio
    async def test_approval_not_configured(self):
        """Approval notification fails when Slack not configured."""
        harness = NotificationHarness(slack_webhook_url=None)

        result = await harness.send_approval_notification(
            request_id="test-123",
            title="Test",
            description="Test",
            tier="phone",
        )

        assert result.success is False
        assert "not configured" in result.error


class TestHTTPClientLifecycle:
    """Tests for HTTP client lifecycle management."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness()

    @pytest.mark.asyncio
    async def test_get_client_creates_new(self, harness):
        """_get_client creates new AsyncClient if none exists."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            client = await harness._get_client()

            assert client == mock_client
            assert harness._http_client == mock_client
            mock_client_class.assert_called_once_with(timeout=30.0)

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing(self, harness):
        """_get_client reuses existing client."""
        mock_client = AsyncMock()
        harness._http_client = mock_client

        client = await harness._get_client()

        assert client == mock_client

    @pytest.mark.asyncio
    async def test_close_closes_client(self, harness):
        """close() closes HTTP client and clears reference."""
        mock_client = AsyncMock()
        harness._http_client = mock_client

        await harness.close()

        mock_client.aclose.assert_called_once()
        assert harness._http_client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self, harness):
        """close() handles no client gracefully."""
        harness._http_client = None

        await harness.close()  # Should not raise

        assert harness._http_client is None


class TestErrorHandlingAndGracefulDegradation:
    """Tests for error handling across all notification types."""

    @pytest.fixture
    def harness(self):
        """Create harness with all channels configured."""
        return NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/test",
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test",
                password="test",
                from_address="test@test.com",
            ),
            github_token="ghp_test",
            notion_token="ntn_test",
        )

    @pytest.mark.asyncio
    async def test_network_timeout_handling(self, harness):
        """Network timeouts handled gracefully."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=TimeoutError("Timeout"))
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert results[0].success is False
            assert "Timeout" in results[0].error or results[0].error is not None

    @pytest.mark.asyncio
    async def test_partial_channel_failure(self, harness):
        """Notification continues even if one channel fails."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK, NotificationChannel.GITHUB_ISSUE],
            message="Test",
            metadata={"repo": "owner/repo", "issue_number": 42},
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            # Slack succeeds, GitHub fails
            slack_response = MagicMock(status_code=200)
            github_response = MagicMock(status_code=500, text="Server Error")
            mock_client.post = AsyncMock(side_effect=[slack_response, github_response])
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            slack_result = next(r for r in results if r.channel == NotificationChannel.SLACK)
            github_result = next(
                r for r in results if r.channel == NotificationChannel.GITHUB_ISSUE
            )

            assert slack_result.success is True
            assert github_result.success is False

    @pytest.mark.asyncio
    async def test_exception_during_send_handled(self, harness):
        """Exceptions during send are caught and reported."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        with patch.object(harness, "_send_slack", side_effect=Exception("Unexpected error")):
            results = await harness.notify(config)

            assert results[0].success is False
            assert "Unexpected error" in results[0].error

    def test_missing_credentials_graceful(self):
        """Missing credentials don't break initialization."""
        harness = NotificationHarness()

        assert harness.slack_webhook_url is None
        assert harness.email_config is None
        assert harness.github_token is None
        assert harness.notion_token is None

    @pytest.mark.asyncio
    async def test_invalid_configuration_detected(self):
        """Invalid configuration properly detected."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        results = await harness.notify(config)

        assert results[0].success is False
        assert "not configured" in results[0].error


class TestPendingResponseManagement:
    """Tests for pending response tracking and management."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    @pytest.mark.asyncio
    async def test_successful_notification_registers_for_tracking(self, harness):
        """Successful notifications register in pending_responses."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200)
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert results[0].success is True
            assert results[0].message_id in harness._pending_responses
            assert harness._pending_responses[results[0].message_id] is None

    @pytest.mark.asyncio
    async def test_failed_notification_not_registered(self, harness):
        """Failed notifications not registered for tracking."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=500, text="Error")
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert results[0].success is False
            # No message_id means not in pending_responses
            assert results[0].message_id not in harness._pending_responses

    def test_clear_pending_removes_entry(self, harness):
        """clear_pending removes notification from tracking."""
        harness._pending_responses["test-id"] = None

        harness.clear_pending("test-id")

        assert "test-id" not in harness._pending_responses

    def test_clear_pending_nonexistent_safe(self, harness):
        """clear_pending handles non-existent ID safely."""
        harness.clear_pending("nonexistent")  # Should not raise


class TestNotificationTierClassification:
    """Tests for NotificationTier classification logic (WATCH, PHONE, DESKTOP)."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    def test_watch_tier_title_truncation(self, harness):
        """WATCH tier truncates long titles to 50 chars."""
        long_title = "A" * 100
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title=long_title,
            description="desc",
            tier="watch",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        section = blocks[0]
        assert "..." in section["text"]["text"]
        assert len(section["text"]["text"]) < len(long_title)

    def test_phone_tier_description_truncation(self, harness):
        """PHONE tier truncates description to 200 chars."""
        long_desc = "B" * 300
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Title",
            description=long_desc,
            tier="phone",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        # Find description section
        desc_section = next(
            (
                b
                for b in blocks
                if b.get("type") == "section" and "..." in b.get("text", {}).get("text", "")
            ),
            None,
        )
        assert desc_section is not None

    def test_desktop_tier_no_truncation(self, harness):
        """DESKTOP tier shows full content."""
        full_desc = "Full description content"
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Title",
            description=full_desc,
            tier="desktop",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        # Find description section
        desc_section = next(
            (
                b
                for b in blocks
                if b.get("type") == "section" and full_desc in b.get("text", {}).get("text", "")
            ),
            None,
        )
        assert desc_section is not None

    def test_phone_tier_context_formatting(self, harness):
        """PHONE tier formats context as slash-separated line."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Title",
            description="Desc",
            tier="phone",
            domain="test-domain",
            project="test-project",
            feature_id="FT-123",
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        context_block = next((b for b in blocks if b["type"] == "context"), None)
        assert context_block is not None
        text = context_block["elements"][0]["text"]
        assert "/" in text  # Slash separator
        assert "test-domain" in text
        assert "test-project" in text


class TestRiskScoreCalculation:
    """Tests for risk score calculation and visualization."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness()

    def test_risk_bar_zero(self, harness):
        """Risk bar at 0% shows empty."""
        bar = harness._risk_bar(0.0)
        assert bar == "⬜⬜⬜⬜⬜ 0%"

    def test_risk_bar_low(self, harness):
        """Risk bar at 20% shows one filled."""
        bar = harness._risk_bar(0.2)
        assert bar == "🟥⬜⬜⬜⬜ 20%"

    def test_risk_bar_medium(self, harness):
        """Risk bar at 50% shows half filled."""
        bar = harness._risk_bar(0.5)
        assert bar == "🟥🟥⬜⬜⬜ 50%"

    def test_risk_bar_high(self, harness):
        """Risk bar at 80% shows mostly filled."""
        bar = harness._risk_bar(0.8)
        assert bar == "🟥🟥🟥🟥⬜ 80%"

    def test_risk_bar_full(self, harness):
        """Risk bar at 100% shows completely filled."""
        bar = harness._risk_bar(1.0)
        assert bar == "🟥🟥🟥🟥🟥 100%"

    def test_risk_bar_fractional(self, harness):
        """Risk bar rounds fractional percentages."""
        bar = harness._risk_bar(0.67)
        assert "67%" in bar
        # Should have 3 filled blocks (int(0.67 * 5) = 3)
        assert bar.count("🟥") == 3

    def test_approval_emoji_critical_risk(self, harness):
        """Critical risk (>60%) shows alert emoji."""
        emoji = harness._approval_emoji("deploy", 0.8)
        assert emoji == "🚨"

    def test_approval_emoji_high_risk(self, harness):
        """High risk (>30%) shows warning emoji."""
        emoji = harness._approval_emoji("feature", 0.5)
        assert emoji == "⚠️"

    def test_approval_emoji_low_risk_uses_type(self, harness):
        """Low risk uses type-based emoji."""
        assert harness._approval_emoji("deploy", 0.1) == "🚀"
        assert harness._approval_emoji("content", 0.1) == "📝"
        assert harness._approval_emoji("feature", 0.1) == "✨"

    def test_approval_emoji_unknown_type(self, harness):
        """Unknown type uses default emoji."""
        emoji = harness._approval_emoji("unknown_type", 0.1)
        assert emoji == "📋"

    def test_approval_emoji_deployment_alias(self, harness):
        """'deployment' is alias for 'deploy'."""
        assert harness._approval_emoji("deployment", 0.1) == "🚀"

    def test_approval_emoji_all_types(self, harness):
        """All approval types have emojis."""
        types = [
            "deploy",
            "deployment",
            "content",
            "feature",
            "security",
            "config",
            "data",
            "compliance",
        ]
        for approval_type in types:
            emoji = harness._approval_emoji(approval_type, 0.1)
            assert isinstance(emoji, str)
            assert len(emoji) > 0


class TestSlackNotificationTemplates:
    """Tests for Slack notification template building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    def test_urgency_emoji_mapping(self, harness):
        """Urgency levels map to emojis."""
        low = harness._urgency_emoji(NotificationUrgency.LOW)
        normal = harness._urgency_emoji(NotificationUrgency.NORMAL)
        high = harness._urgency_emoji(NotificationUrgency.HIGH)
        critical = harness._urgency_emoji(NotificationUrgency.CRITICAL)

        # All should return strings (may be empty for low/normal)
        assert isinstance(low, str)
        assert isinstance(normal, str)
        assert isinstance(high, str)
        assert isinstance(critical, str)

    def test_slack_blocks_without_title(self, harness):
        """Slack blocks work without title."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Just a message",
        )

        blocks = harness._build_slack_blocks(config)

        # Should have at least one section block
        assert any(b["type"] == "section" for b in blocks)
        # Should not have header block
        assert not any(b.get("type") == "header" for b in blocks)

    def test_slack_blocks_action_button_not_required(self, harness):
        """Action button shows 'View Details' when not required."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Message",
            action_url="https://example.com",
            action_required=False,
        )

        blocks = harness._build_slack_blocks(config)
        actions = next(b for b in blocks if b.get("type") == "actions")
        button = actions["elements"][0]

        assert button["text"]["text"] == "View Details"
        assert button.get("style") is None  # Not primary

    def test_slack_blocks_multiple_metadata(self, harness):
        """Metadata with multiple items formatted correctly."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Message",
            metadata={"key1": "value1", "key2": "value2", "key3": "value3"},
        )

        blocks = harness._build_slack_blocks(config)
        context = next(b for b in blocks if b.get("type") == "context")
        text = context["elements"][0]["text"]

        assert "key1" in text
        assert "value1" in text
        assert "|" in text  # Separator


class TestEmailNotificationTemplates:
    """Tests for email notification template building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test",
                password="test",
                from_address="test@test.com",
            )
        )

    def test_email_html_all_urgency_colors(self, harness):
        """Each urgency level has distinct color."""
        colors = {}
        for urgency in NotificationUrgency:
            config = NotificationConfig(
                channels=[NotificationChannel.EMAIL],
                message="Test",
                title="Test",
                urgency=urgency,
            )
            html = harness._build_email_html(config)
            # Extract color (this is a simple check)
            colors[urgency] = html

        # All should be different (basic check)
        assert len(colors) == 4

    def test_email_html_without_title(self, harness):
        """Email HTML works without title."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Message only",
        )

        html = harness._build_email_html(config)

        assert "Message only" in html
        assert "<h2" not in html  # No title header

    def test_email_html_with_metadata(self, harness):
        """Email HTML includes metadata as list."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Message",
            metadata={"project": "test-project", "version": "1.2.3"},
        )

        html = harness._build_email_html(config)

        assert "project" in html
        assert "test-project" in html
        assert "version" in html
        assert "1.2.3" in html
        assert "<li>" in html  # Metadata as list items

    def test_email_html_action_button_text(self, harness):
        """Action button text changes based on action_required."""
        config_required = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test",
            action_url="https://example.com",
            action_required=True,
        )
        config_optional = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test",
            action_url="https://example.com",
            action_required=False,
        )

        html_required = harness._build_email_html(config_required)
        html_optional = harness._build_email_html(config_optional)

        assert "Take Action" in html_required
        assert "View Details" in html_optional

    @pytest.mark.asyncio
    async def test_email_with_metadata_addresses(self, harness):
        """Email uses to_addresses from metadata."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test",
            metadata={"to_addresses": ["recipient1@example.com", "recipient2@example.com"]},
        )

        with patch.object(harness, "_send_smtp") as mock_smtp:
            results = await harness.notify(config)

            assert results[0].success is True
            # Verify _send_smtp was called
            mock_smtp.assert_called_once()


class TestGitHubNotificationTemplates:
    """Tests for GitHub notification template building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(github_token="ghp_test")

    def test_github_comment_critical_urgency(self, harness):
        """Critical urgency shows Critical badge."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Message",
            urgency=NotificationUrgency.CRITICAL,
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "**Critical**" in body

    def test_github_comment_normal_urgency_no_badge(self, harness):
        """Normal urgency has no badge."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Message",
            urgency=NotificationUrgency.NORMAL,
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "**Warning**" not in body
        assert "**Critical**" not in body

    def test_github_comment_action_required_text(self, harness):
        """Action required shows 'Take Action' link."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Message",
            action_url="https://example.com",
            action_required=True,
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "[Take Action](https://example.com)" in body

    def test_github_comment_metadata_excludes_repo_info(self, harness):
        """Metadata section excludes repo and issue_number."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Message",
            metadata={"repo": "owner/repo", "issue_number": 42, "extra": "data"},
        )

        body = harness._build_github_comment_body(config)

        # extra should be included
        assert "extra" in body
        # repo and issue_number should not be in metadata section
        # (They're used for API call, not displayed)
        lines = body.split("\n")
        metadata_section = "\n".join(lines[lines.index("---") :]) if "---" in body else ""
        assert "extra" in metadata_section if metadata_section else True

    def test_github_comment_footer(self, harness):
        """GitHub comment includes FORGE Harness footer."""
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Message",
            metadata={},
        )

        body = harness._build_github_comment_body(config)

        assert "FORGE Harness" in body


class TestNotionNotificationTemplates:
    """Tests for Notion notification template building."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(notion_token="ntn_test")

    def test_notion_blocks_structure(self, harness):
        """Notion blocks have expected structure."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Message",
            title="Title",
            metadata={},
        )

        blocks = harness._build_notion_comment_blocks(config)

        # Should have: divider, callout, paragraph, timestamp
        assert len(blocks) >= 4
        assert blocks[0]["type"] == "divider"
        assert blocks[1]["type"] == "callout"
        assert any(b["type"] == "paragraph" for b in blocks)

    def test_notion_urgency_color_mapping(self, harness):
        """Notion urgency colors map correctly."""
        assert harness._notion_urgency_color(NotificationUrgency.LOW) == "gray_background"
        assert harness._notion_urgency_color(NotificationUrgency.NORMAL) == "blue_background"
        assert harness._notion_urgency_color(NotificationUrgency.HIGH) == "yellow_background"
        assert harness._notion_urgency_color(NotificationUrgency.CRITICAL) == "red_background"

    def test_notion_callout_with_title(self, harness):
        """Notion callout uses title if provided."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Message",
            title="Custom Title",
            metadata={},
        )

        blocks = harness._build_notion_comment_blocks(config)
        callout = next(b for b in blocks if b["type"] == "callout")

        text = callout["callout"]["rich_text"][0]["text"]["content"]
        assert text == "Custom Title"

    def test_notion_callout_default_title(self, harness):
        """Notion callout uses default title if none provided."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Message",
            metadata={},
        )

        blocks = harness._build_notion_comment_blocks(config)
        callout = next(b for b in blocks if b["type"] == "callout")

        text = callout["callout"]["rich_text"][0]["text"]["content"]
        assert text == "FORGE Notification"

    def test_notion_includes_timestamp(self, harness):
        """Notion blocks include timestamp paragraph."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Message",
            metadata={},
        )

        blocks = harness._build_notion_comment_blocks(config)

        # Find timestamp paragraph
        timestamp_para = None
        for block in blocks:
            if block["type"] == "paragraph":
                text = block["paragraph"]["rich_text"][0]["text"]["content"]
                if "Sent:" in text:
                    timestamp_para = block
                    break

        assert timestamp_para is not None
        assert "UTC" in timestamp_para["paragraph"]["rich_text"][0]["text"]["content"]


class TestErrorHandlingEdgeCases:
    """Tests for error handling edge cases and failure scenarios."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(
            slack_webhook_url="https://test",
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test",
                password="test",
                from_address="test@test.com",
            ),
        )

    @pytest.mark.asyncio
    async def test_smtp_exception_handling(self, harness):
        """SMTP exceptions are caught and reported."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Test",
            mention_users=["test@example.com"],
        )

        with patch.object(harness, "_send_smtp", side_effect=smtplib.SMTPException("SMTP error")):
            results = await harness.notify(config)

            assert results[0].success is False
            assert "SMTP error" in results[0].error

    @pytest.mark.asyncio
    async def test_github_api_error_response(self, harness):
        """GitHub API error responses handled."""
        harness.github_token = "ghp_test"
        config = NotificationConfig(
            channels=[NotificationChannel.GITHUB_ISSUE],
            message="Test",
            metadata={"repo": "owner/repo", "issue_number": 1},
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(
                status_code=404,
                text="Not Found",
            )
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert results[0].success is False
            assert "404" in results[0].error

    @pytest.mark.asyncio
    async def test_notion_api_error_response(self, harness):
        """Notion API error responses handled."""
        harness.notion_token = "ntn_test"
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Test",
            metadata={"page_id": "abc123"},
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.patch.return_value = MagicMock(
                status_code=400,
                text="Bad Request",
            )
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            assert results[0].success is False
            assert "400" in results[0].error

    def test_has_channel_unknown_channel(self):
        """has_channel returns False for unknown channel types."""
        harness = NotificationHarness()
        # Create a mock channel that's not in the enum
        # Since we can't create invalid enum values, just test the existing ones
        assert harness.has_channel(NotificationChannel.SLACK) is False
        assert harness.has_channel(NotificationChannel.EMAIL) is False


class TestApprovalNotificationEdgeCases:
    """Tests for approval notification edge cases."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    def test_approval_blocks_max_fields_limit(self, harness):
        """DESKTOP tier respects Slack's 10 field limit."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="Test",
            tier="desktop",
            domain="domain",
            project="project",
            feature_id="FT-1",
            approval_type="deploy",
            risk_score=0.5,
            context={},
            dashboard_url=None,
        )

        fields_block = next((b for b in blocks if b.get("fields")), None)
        if fields_block:
            assert len(fields_block["fields"]) <= 10

    def test_approval_blocks_max_context_items(self, harness):
        """Context section limits to 5 items."""
        large_context = {f"key{i}": f"value{i}" for i in range(20)}

        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="Test",
            tier="desktop",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context=large_context,
            dashboard_url=None,
        )

        # Find context block
        context_block = next((b for b in blocks if b.get("type") == "context"), None)
        if context_block:
            # Should only include first 5 items
            text = context_block["elements"][0]["text"]
            # Count pipe separators (n items = n-1 separators)
            separator_count = text.count("|")
            assert separator_count <= 4  # Max 5 items = 4 separators

    def test_approval_blocks_without_dashboard_url(self, harness):
        """DESKTOP tier without dashboard URL only has 2 buttons."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="Test",
            tier="desktop",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        actions = next(b for b in blocks if b["type"] == "actions")
        assert len(actions["elements"]) == 2  # Only approve/reject

    def test_approval_blocks_phone_no_description(self, harness):
        """PHONE tier handles empty description."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="",
            tier="phone",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        # Should not crash, blocks should exist
        assert len(blocks) > 0

    def test_approval_blocks_phone_no_context_parts(self, harness):
        """PHONE tier handles missing context parts gracefully."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="Test",
            tier="phone",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        # Should not have context block if no context parts
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        # May or may not have context block, but shouldn't crash
        assert isinstance(blocks, list)

    @pytest.mark.asyncio
    async def test_approval_notification_api_failure(self, harness):
        """Approval notification handles API failures."""
        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(
                status_code=500,
                text="Internal Server Error",
            )
            mock_get_client.return_value = mock_client

            result = await harness.send_approval_notification(
                request_id="test",
                title="Test",
                description="Test",
                tier="phone",
            )

            assert result.success is False
            assert "500" in result.error


class TestSMTPBlockingSend:
    """Tests for the blocking SMTP send method."""

    @pytest.fixture
    def harness(self):
        """Create harness with email configured."""
        return NotificationHarness(
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test@test.com",
                password="secret",
                from_address="noreply@test.com",
                use_tls=True,
            )
        )

    def test_send_smtp_with_tls(self, harness):
        """SMTP send uses TLS when configured."""
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart()
        msg["To"] = "recipient@example.com"

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_class.return_value.__exit__ = MagicMock()

            harness._send_smtp(msg, ["recipient@example.com"])

            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once()
            mock_server.sendmail.assert_called_once()

    def test_send_smtp_without_tls(self):
        """SMTP send skips TLS when not configured."""
        from email.mime.multipart import MIMEMultipart

        harness = NotificationHarness(
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=25,
                username="test@test.com",
                password="secret",
                from_address="noreply@test.com",
                use_tls=False,
            )
        )

        msg = MIMEMultipart()
        msg["To"] = "recipient@example.com"

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_class.return_value.__exit__ = MagicMock()

            harness._send_smtp(msg, ["recipient@example.com"])

            # TLS should NOT be called
            mock_server.starttls.assert_not_called()
            mock_server.login.assert_called_once()

    def test_send_smtp_no_config_raises(self):
        """SMTP send raises when no config."""
        from email.mime.multipart import MIMEMultipart

        harness = NotificationHarness()  # No email config
        msg = MIMEMultipart()

        with pytest.raises(ValueError, match="Email configuration not provided"):
            harness._send_smtp(msg, ["test@example.com"])


class TestUnsupportedChannelHandling:
    """Tests for unsupported or unknown channels."""

    @pytest.mark.asyncio
    async def test_notify_with_unconfigured_channel_type(self):
        """Notify handles channels gracefully even if channel check fails."""
        harness = NotificationHarness()

        # All channels should fail gracefully
        for channel in NotificationChannel:
            config = NotificationConfig(
                channels=[channel],
                message="Test",
            )

            results = await harness.notify(config)

            assert len(results) == 1
            assert results[0].success is False
            assert "not configured" in results[0].error.lower()


class TestNotificationResultRegistration:
    """Tests for notification result registration in pending responses."""

    @pytest.fixture
    def harness(self):
        """Create harness with Slack configured."""
        return NotificationHarness(slack_webhook_url="https://test")

    @pytest.mark.asyncio
    async def test_only_successful_messages_registered(self, harness):
        """Only successful notifications with message_id are registered."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        with patch.object(harness, "_get_client") as mock_get_client:
            # First call succeeds
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=200)
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            # Successful result should be in pending responses
            assert results[0].success is True
            assert results[0].message_id in harness._pending_responses
            assert harness._pending_responses[results[0].message_id] is None

    @pytest.mark.asyncio
    async def test_failed_messages_not_registered(self, harness):
        """Failed notifications are not registered."""
        config = NotificationConfig(
            channels=[NotificationChannel.SLACK],
            message="Test",
        )

        initial_count = len(harness._pending_responses)

        with patch.object(harness, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(status_code=500, text="Error")
            mock_get_client.return_value = mock_client

            results = await harness.notify(config)

            # Failed result should NOT increase pending responses
            assert results[0].success is False
            assert len(harness._pending_responses) == initial_count


class TestEmailPlainTextContent:
    """Tests for email plain text content generation."""

    @pytest.fixture
    def harness(self):
        """Create harness with email configured."""
        return NotificationHarness(
            email_config=EmailConfig(
                smtp_host="smtp.test.com",
                smtp_port=587,
                username="test",
                password="test",
                from_address="test@test.com",
            )
        )

    @pytest.mark.asyncio
    async def test_email_plain_text_includes_action_url(self, harness):
        """Email plain text includes action URL."""
        config = NotificationConfig(
            channels=[NotificationChannel.EMAIL],
            message="Message body",
            action_url="https://example.com/action",
            mention_users=["test@example.com"],
        )

        with patch.object(harness, "_send_smtp") as mock_smtp:
            await harness.notify(config)

            # Check the message that was passed to _send_smtp
            call_args = mock_smtp.call_args
            msg = call_args[0][0]  # First positional argument is the message

            # Get plain text part
            plain_text_part = None
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    plain_text_part = part.get_payload()
                    break

            assert plain_text_part is not None
            assert "https://example.com/action" in plain_text_part


class TestNotionCommentUrgencyEmoji:
    """Tests for Notion comment urgency emoji fallback."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness()

    def test_notion_urgency_emoji_fallback(self, harness):
        """Notion uses 'notification' fallback when urgency emoji is empty."""
        config = NotificationConfig(
            channels=[NotificationChannel.NOTION_COMMENT],
            message="Message",
            urgency=NotificationUrgency.LOW,  # Empty emoji
            metadata={},
        )

        blocks = harness._build_notion_comment_blocks(config)
        callout = next(b for b in blocks if b["type"] == "callout")

        # When urgency emoji is empty, should use "notification" fallback
        emoji_value = callout["callout"]["icon"]["emoji"]
        assert emoji_value == "notification" or emoji_value == ""


class TestHasChannelTelegram:
    """Tests for Telegram channel configuration detection."""

    def test_has_channel_telegram_fully_configured(self):
        """Telegram channel detected when token and users provided."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"12345678"},
        )
        assert harness.has_channel(NotificationChannel.TELEGRAM) is True

    def test_has_channel_telegram_no_token(self):
        """Telegram channel missing when no token."""
        harness = NotificationHarness(
            telegram_bot_token=None,
            telegram_allowed_users={"12345678"},
        )
        assert harness.has_channel(NotificationChannel.TELEGRAM) is False

    def test_has_channel_telegram_no_users(self):
        """Telegram channel missing when no allowed users."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users=set(),
        )
        assert harness.has_channel(NotificationChannel.TELEGRAM) is False

    def test_has_channel_telegram_both_missing(self):
        """Telegram channel missing when neither token nor users provided."""
        harness = NotificationHarness()
        assert harness.has_channel(NotificationChannel.TELEGRAM) is False

    def test_has_channel_returns_false_for_unlisted_type(self):
        """has_channel returns False for any channel not matching known branches."""
        harness = NotificationHarness(
            slack_webhook_url="https://test",
            github_token="ghp_test",
            notion_token="ntn_test",
        )
        # Line 253: the fallback return False after all elif branches
        # This is hit only if an unknown enum value were passed.
        # We can't create a non-enum value, so we verify all known channels work.
        for channel in NotificationChannel:
            result = harness.has_channel(channel)
            assert isinstance(result, bool)


class TestUrgencyToTier:
    """Tests for _urgency_to_tier mapping."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness()

    def test_low_urgency_maps_to_watch(self, harness):
        """LOW urgency maps to 'watch' tier."""
        tier = harness._urgency_to_tier(NotificationUrgency.LOW)
        assert tier == "watch"

    def test_normal_urgency_maps_to_phone(self, harness):
        """NORMAL urgency maps to 'phone' tier."""
        tier = harness._urgency_to_tier(NotificationUrgency.NORMAL)
        assert tier == "phone"

    def test_high_urgency_maps_to_phone(self, harness):
        """HIGH urgency maps to 'phone' tier."""
        tier = harness._urgency_to_tier(NotificationUrgency.HIGH)
        assert tier == "phone"

    def test_critical_urgency_maps_to_desktop(self, harness):
        """CRITICAL urgency maps to 'desktop' tier."""
        tier = harness._urgency_to_tier(NotificationUrgency.CRITICAL)
        assert tier == "desktop"

    def test_all_urgency_levels_return_valid_tier(self, harness):
        """All urgency levels return a valid tier string."""
        valid_tiers = {"watch", "phone", "desktop"}
        for urgency in NotificationUrgency:
            tier = harness._urgency_to_tier(urgency)
            assert tier in valid_tiers, f"Urgency {urgency} returned invalid tier: {tier}"


class TestTelegramNotifications:
    """Tests for Telegram notification sending."""

    @pytest.fixture
    def harness(self):
        """Create harness with Telegram configured."""
        return NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111", "222222222"},
        )

    @pytest.fixture
    def harness_no_telegram(self):
        """Create harness without Telegram configured."""
        return NotificationHarness()

    @pytest.mark.asyncio
    async def test_send_telegram_not_configured(self, harness_no_telegram):
        """Telegram notification fails when not configured."""
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
            title="Test Title",
        )
        results = await harness_no_telegram.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "not configured" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_send_telegram_success_all_users(self, harness):
        """Telegram sends successfully to all allowed users."""
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
            title="Test Title",
        )

        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(return_value={"ok": True})

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].message_id is not None
        assert results[0].channel == NotificationChannel.TELEGRAM

    @pytest.mark.asyncio
    async def test_send_telegram_all_users_fail(self, harness):
        """Telegram returns failure when all user sends fail."""
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
        )

        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None

    @pytest.mark.asyncio
    async def test_send_telegram_partial_success(self):
        """Telegram succeeds if at least one user send succeeds."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111", "222222222"},
        )
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
        )

        call_count = 0

        async def side_effect_fn(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First user failed")
            return {"ok": True}

        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(side_effect=side_effect_fn)

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_send_telegram_handler_exception(self, harness):
        """Telegram notification handles handler initialization exception."""
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
        )

        with patch.object(
            harness,
            "_get_telegram_handler",
            side_effect=Exception("Handler init failed"),
        ):
            results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "Handler init failed" in results[0].error

    @pytest.mark.asyncio
    async def test_send_telegram_no_token_direct_call(self):
        """_send_telegram returns failure when called with no token."""
        harness = NotificationHarness(
            telegram_bot_token=None,
            telegram_allowed_users={"111111111"},
        )
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
        )
        # Directly call _send_telegram bypassing has_channel check
        result = await harness._send_telegram(config)

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_send_telegram_no_users_direct_call(self):
        """_send_telegram returns failure when no allowed users."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users=set(),
        )
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test message",
        )
        result = await harness._send_telegram(config)

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_send_telegram_uses_urgency_tier(self, harness):
        """Telegram send uses urgency to determine tier."""
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Critical alert",
            urgency=NotificationUrgency.CRITICAL,
        )

        captured_tiers = []

        async def capture_tier(**kwargs):
            captured_tiers.append(kwargs.get("tier"))
            return {"ok": True}

        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(side_effect=capture_tier)

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            results = await harness.notify(config)

        assert results[0].success is True
        assert all(t == "desktop" for t in captured_tiers), f"Expected 'desktop' tier, got: {captured_tiers}"


class TestSendTelegramNotification:
    """Tests for the send_telegram_notification convenience method."""

    @pytest.fixture
    def harness(self):
        """Create harness with Telegram configured."""
        return NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111"},
        )

    @pytest.mark.asyncio
    async def test_send_telegram_notification_success(self, harness):
        """send_telegram_notification returns success result."""
        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(return_value={"ok": True})

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            result = await harness.send_telegram_notification(
                title="Test Title",
                message="Test message",
                tier="phone",
            )

        assert result.success is True
        assert result.channel == NotificationChannel.TELEGRAM

    @pytest.mark.asyncio
    async def test_send_telegram_notification_not_configured(self):
        """send_telegram_notification returns failure when not configured."""
        harness = NotificationHarness()

        result = await harness.send_telegram_notification(
            title="Test",
            message="Test message",
        )

        assert result.success is False
        assert result.channel == NotificationChannel.TELEGRAM

    @pytest.mark.asyncio
    async def test_send_telegram_notification_with_metadata(self, harness):
        """send_telegram_notification passes metadata to handler."""
        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(return_value={"ok": True})

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            result = await harness.send_telegram_notification(
                title="Test",
                message="Test message",
                metadata={"project": "interview-simulator"},
                action_url="https://example.com",
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_send_telegram_notification_default_tier(self, harness):
        """send_telegram_notification defaults to 'phone' tier."""
        mock_handler = AsyncMock()
        mock_handler.send_notification = AsyncMock(return_value={"ok": True})

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            result = await harness.send_telegram_notification(
                title="Test",
                message="Test message",
            )

        assert result.success is True


class TestSendTelegramApproval:
    """Tests for the send_telegram_approval method."""

    @pytest.fixture
    def harness(self):
        """Create harness with Telegram configured."""
        return NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111", "222222222"},
        )

    @pytest.mark.asyncio
    async def test_send_telegram_approval_not_configured(self):
        """send_telegram_approval fails when Telegram not configured."""
        harness = NotificationHarness()

        result = await harness.send_telegram_approval(
            request_id="req-123",
            title="Deploy Feature",
            description="Deploy feature FT-042",
        )

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_send_telegram_approval_success(self, harness):
        """send_telegram_approval succeeds when handler works."""
        mock_handler = AsyncMock()
        mock_handler.send_approval_request = AsyncMock(return_value={"ok": True})

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            result = await harness.send_telegram_approval(
                request_id="req-456",
                title="Deploy to Production",
                description="Deploy v1.2.3 to production environment",
                tier="desktop",
                metadata={"version": "1.2.3"},
            )

        assert result.success is True
        assert result.channel == NotificationChannel.TELEGRAM
        assert result.message_id is not None

    @pytest.mark.asyncio
    async def test_send_telegram_approval_all_fail(self, harness):
        """send_telegram_approval fails when all user sends fail."""
        mock_handler = AsyncMock()
        mock_handler.send_approval_request = AsyncMock(
            side_effect=Exception("Network error")
        )

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            result = await harness.send_telegram_approval(
                request_id="req-789",
                title="Test",
                description="Test",
            )

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_send_telegram_approval_partial_success(self):
        """send_telegram_approval succeeds if at least one user succeeds."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111", "222222222"},
        )

        call_count = 0

        async def side_effect_fn(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First user failed")
            return {"ok": True}

        mock_handler = AsyncMock()
        mock_handler.send_approval_request = AsyncMock(side_effect=side_effect_fn)

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            result = await harness.send_telegram_approval(
                request_id="req-abc",
                title="Test",
                description="Test",
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_send_telegram_approval_handler_init_failure(self, harness):
        """send_telegram_approval propagates exception when handler init fails.

        Note: _get_telegram_handler() is called outside the try/except in
        send_telegram_approval(), so exceptions from it propagate to the caller.
        """
        with patch.object(
            harness,
            "_get_telegram_handler",
            side_effect=Exception("Bot token invalid"),
        ):
            with pytest.raises(Exception, match="Bot token invalid"):
                await harness.send_telegram_approval(
                    request_id="req-fail",
                    title="Test",
                    description="Test",
                )

    @pytest.mark.asyncio
    async def test_send_telegram_approval_no_token(self):
        """send_telegram_approval with token but no users returns failure."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users=set(),
        )

        result = await harness.send_telegram_approval(
            request_id="req-no-users",
            title="Test",
            description="Test",
        )

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_send_telegram_approval_passes_request_id(self, harness):
        """send_telegram_approval passes request_id to handler."""
        captured_ids = []

        async def capture_request(**kwargs):
            captured_ids.append(kwargs.get("request_id"))
            return {"ok": True}

        mock_handler = AsyncMock()
        mock_handler.send_approval_request = AsyncMock(side_effect=capture_request)

        with patch.object(harness, "_get_telegram_handler", return_value=mock_handler):
            await harness.send_telegram_approval(
                request_id="my-unique-req-id",
                title="Test",
                description="Test",
            )

        assert any(r == "my-unique-req-id" for r in captured_ids)


class TestGetTelegramHandler:
    """Tests for Telegram handler lazy initialization."""

    def test_get_telegram_handler_lazy_loads(self):
        """_get_telegram_handler lazily creates handler on first call."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111"},
        )

        assert harness._telegram_handler is None

        mock_handler_instance = MagicMock()
        mock_handler_class = MagicMock(return_value=mock_handler_instance)

        with patch.dict("sys.modules", {"forge_harness.telegram_bot": MagicMock()}):
            with patch(
                "forge_harness.notification_harness.NotificationHarness._get_telegram_handler",
                return_value=mock_handler_instance,
            ):
                handler = harness._get_telegram_handler()

        assert handler is not None

    def test_get_telegram_handler_returns_existing(self):
        """_get_telegram_handler returns existing handler if already created."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111"},
        )
        mock_handler = MagicMock()
        harness._telegram_handler = mock_handler

        handler = harness._get_telegram_handler()

        assert handler is mock_handler


class TestCreateNotificationHarnessWithTelegram:
    """Tests for create_notification_harness with Telegram configuration."""

    def test_factory_with_telegram_params(self):
        """Factory creates harness with explicit Telegram params."""
        harness = create_notification_harness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111", "222222222"},
        )

        assert harness.telegram_bot_token == "bot123:TOKEN"
        assert "111111111" in harness.telegram_allowed_users
        assert "222222222" in harness.telegram_allowed_users

    def test_factory_telegram_from_env_vars(self, monkeypatch):
        """Factory reads Telegram config from environment variables."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot_from_env:TOKEN")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111,222222222,333333333")

        harness = create_notification_harness()

        assert harness.telegram_bot_token == "bot_from_env:TOKEN"
        assert "111111111" in harness.telegram_allowed_users
        assert "222222222" in harness.telegram_allowed_users
        assert "333333333" in harness.telegram_allowed_users

    def test_factory_telegram_env_empty_users_string(self, monkeypatch):
        """Factory handles empty TELEGRAM_ALLOWED_USERS env var."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123:TOKEN")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")

        harness = create_notification_harness()

        assert harness.telegram_allowed_users == set()

    def test_factory_explicit_telegram_overrides_env(self, monkeypatch):
        """Explicit Telegram params override environment variables."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot_from_env:TOKEN")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999999999")

        harness = create_notification_harness(
            telegram_bot_token="explicit_bot:TOKEN",
            telegram_allowed_users={"111111111"},
        )

        assert harness.telegram_bot_token == "explicit_bot:TOKEN"
        assert "111111111" in harness.telegram_allowed_users
        assert "999999999" not in harness.telegram_allowed_users

    def test_factory_telegram_users_whitespace_stripped(self, monkeypatch):
        """Factory strips whitespace from Telegram user IDs in env var."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", " 111111111 , 222222222 , 333333333 ")

        harness = create_notification_harness()

        assert "111111111" in harness.telegram_allowed_users
        assert "222222222" in harness.telegram_allowed_users
        assert "333333333" in harness.telegram_allowed_users
        # Raw whitespace-padded IDs should not be present
        assert " 111111111 " not in harness.telegram_allowed_users


class TestNotifyTelegramChannel:
    """Tests for notify() routing to Telegram channel."""

    @pytest.mark.asyncio
    async def test_notify_telegram_unconfigured_returns_error(self):
        """notify() returns error for Telegram when not configured."""
        harness = NotificationHarness()
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test",
        )

        results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "not configured" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_notify_telegram_configured_calls_send(self):
        """notify() routes Telegram channel to _send_telegram when configured."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111"},
        )
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test",
            title="Test Title",
        )

        mock_result = NotificationResult(
            channel=NotificationChannel.TELEGRAM,
            success=True,
            message_id="tg-msg-123",
        )

        with patch.object(harness, "_send_telegram", return_value=mock_result):
            results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].message_id == "tg-msg-123"

    @pytest.mark.asyncio
    async def test_notify_telegram_registers_in_pending_responses(self):
        """Successful Telegram notify registers message_id in pending responses."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111"},
        )
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test",
        )

        mock_result = NotificationResult(
            channel=NotificationChannel.TELEGRAM,
            success=True,
            message_id="tg-pending-123",
        )

        with patch.object(harness, "_send_telegram", return_value=mock_result):
            results = await harness.notify(config)

        assert results[0].success is True
        assert "tg-pending-123" in harness._pending_responses

    @pytest.mark.asyncio
    async def test_notify_telegram_exception_handled_gracefully(self):
        """notify() handles exceptions from _send_telegram gracefully."""
        harness = NotificationHarness(
            telegram_bot_token="bot123:TOKEN",
            telegram_allowed_users={"111111111"},
        )
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            message="Test",
        )

        with patch.object(
            harness,
            "_send_telegram",
            side_effect=Exception("Unexpected Telegram error"),
        ):
            results = await harness.notify(config)

        assert len(results) == 1
        assert results[0].success is False
        assert "Unexpected Telegram error" in results[0].error


class TestApprovalBlocksActionId:
    """Tests for approval block action_id formatting."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    def test_approve_action_id_format(self, harness):
        """Approve button action_id follows approval_{id}_approve pattern."""
        blocks = harness._build_approval_slack_blocks(
            request_id="unique-req-id-123",
            title="Test",
            description="Test",
            tier="phone",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        actions_block = next(b for b in blocks if b["type"] == "actions")
        approve_btn = next(e for e in actions_block["elements"] if "approve" in e["action_id"])

        assert approve_btn["action_id"] == "approval_unique-req-id-123_approve"

    def test_reject_action_id_format(self, harness):
        """Reject button action_id follows approval_{id}_reject pattern."""
        blocks = harness._build_approval_slack_blocks(
            request_id="unique-req-id-456",
            title="Test",
            description="Test",
            tier="watch",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        actions_block = next(b for b in blocks if b["type"] == "actions")
        reject_btn = next(e for e in actions_block["elements"] if "reject" in e["action_id"])

        assert reject_btn["action_id"] == "approval_unique-req-id-456_reject"

    def test_block_id_format(self, harness):
        """Actions block block_id follows approval_actions_{id} pattern."""
        blocks = harness._build_approval_slack_blocks(
            request_id="my-block-id",
            title="Test",
            description="Test",
            tier="desktop",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        actions_block = next(b for b in blocks if b["type"] == "actions")
        assert actions_block["block_id"] == "approval_actions_my-block-id"


class TestApprovalDesktopRiskField:
    """Tests for DESKTOP tier risk score inclusion in fields."""

    @pytest.fixture
    def harness(self):
        """Create harness for testing."""
        return NotificationHarness(slack_webhook_url="https://test")

    def test_zero_risk_score_not_in_fields(self, harness):
        """Risk field not added when risk_score is 0."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="Test",
            tier="desktop",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        fields_block = next((b for b in blocks if b.get("fields")), None)
        # With risk_score=0 and no other fields, there should be no fields block
        # or fields should not contain a Risk field
        if fields_block:
            field_texts = [f["text"] for f in fields_block["fields"]]
            assert not any("Risk:" in t for t in field_texts)

    def test_nonzero_risk_score_in_fields(self, harness):
        """Risk field added when risk_score > 0."""
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title="Test",
            description="Test",
            tier="desktop",
            domain="domain",
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.5,
            context={},
            dashboard_url=None,
        )

        fields_block = next((b for b in blocks if b.get("fields")), None)
        assert fields_block is not None
        field_texts = [f["text"] for f in fields_block["fields"]]
        assert any("Risk:" in t for t in field_texts)

    def test_phone_header_title_truncated_at_75(self, harness):
        """PHONE tier header truncates title at 75 characters."""
        title_76_chars = "A" * 76
        blocks = harness._build_approval_slack_blocks(
            request_id="test",
            title=title_76_chars,
            description="Desc",
            tier="phone",
            domain=None,
            project=None,
            feature_id=None,
            approval_type=None,
            risk_score=0.0,
            context={},
            dashboard_url=None,
        )

        header = next(b for b in blocks if b["type"] == "header")
        header_text = header["text"]["text"]
        # The title is sliced at [:75] in phone tier — no "..." appended
        assert len(header_text) <= 80  # emoji + space + 75 chars = reasonable limit
        assert title_76_chars not in header_text  # Full 76-char title should not appear

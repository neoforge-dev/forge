"""
Notification Harness for FORGE Workflows
========================================

Multi-channel notifications for workflow events and human-in-the-loop interactions.

Supports:
- Slack (via Incoming Webhooks)
- Email (via SMTP or SendGrid)
- GitHub Issue comments
- Notion comments

Usage:
    from forge_harness.notification_harness import (
        NotificationHarness,
        NotificationConfig,
        NotificationChannel,
        create_notification_harness,
    )

    # Create harness
    harness = create_notification_harness(
        slack_webhook_url="https://hooks.slack.com/services/...",
        email_config={"smtp_host": "smtp.gmail.com", "smtp_port": 587, ...},
    )

    # Send notification
    results = await harness.notify(
        NotificationConfig(
            channels=[NotificationChannel.SLACK],
            title="Content Ready",
            message="Your content is ready for review",
            action_required=True,
            action_url="https://notion.so/...",
        )
    )
"""

import asyncio
import os
import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any

import httpx

from .logging_config import get_logger

logger = get_logger(__name__)


class NotificationChannel(Enum):
    """Supported notification channels."""

    SLACK = "slack"
    EMAIL = "email"
    GITHUB_ISSUE = "github_issue"
    NOTION_COMMENT = "notion_comment"
    TELEGRAM = "telegram"


class NotificationUrgency(Enum):
    """Urgency levels for notifications."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class NotificationConfig:
    """Configuration for a notification.

    Attributes:
        channels: List of channels to send notification to
        message: Main notification message body
        title: Optional notification title/subject
        metadata: Additional metadata to include
        urgency: Urgency level (affects formatting/priority)
        action_required: Whether user action is required
        action_url: URL for the action (if applicable)
        mention_users: List of user IDs/emails to mention
    """

    channels: list[NotificationChannel]
    message: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    urgency: NotificationUrgency = NotificationUrgency.NORMAL
    action_required: bool = False
    action_url: str | None = None
    mention_users: list[str] = field(default_factory=list)


@dataclass
class NotificationResult:
    """Result of sending a notification.

    Attributes:
        channel: Channel the notification was sent to
        success: Whether sending succeeded
        message_id: Unique identifier for the message (if available)
        error: Error message if sending failed
        timestamp: When the notification was sent
    """

    channel: NotificationChannel
    success: bool
    message_id: str | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class HumanResponse:
    """Response from a human via notification channel.

    Attributes:
        notification_id: ID of the original notification
        channel: Channel the response came from
        response_type: Type of response (approved, rejected, comment)
        message: Optional message content
        responder: ID/name of the responder
        timestamp: When the response was received
    """

    notification_id: str
    channel: NotificationChannel
    response_type: str  # "approved", "rejected", "comment", "acknowledged"
    message: str | None = None
    responder: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class EmailConfig:
    """SMTP email configuration.

    Attributes:
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        username: SMTP username
        password: SMTP password
        from_address: Sender email address
        from_name: Sender display name
        use_tls: Whether to use TLS
    """

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_address: str
    from_name: str = "FORGE Harness"
    use_tls: bool = True


class NotificationHarness:
    """
    Send notifications and collect human responses.

    Supports multiple channels: Slack, email, GitHub Issues, Notion comments.

    Usage:
        harness = NotificationHarness(
            slack_webhook_url="https://hooks.slack.com/services/...",
        )

        # Send notification
        results = await harness.notify(
            NotificationConfig(
                channels=[NotificationChannel.SLACK],
                message="Content ready for review",
                action_required=True,
            )
        )

        # Record response (typically via webhook callback)
        harness.record_response(
            notification_id=results[0].message_id,
            channel=NotificationChannel.SLACK,
            response_type="approved",
            responder="@user",
        )
    """

    def __init__(
        self,
        slack_webhook_url: str | None = None,
        email_config: EmailConfig | None = None,
        github_token: str | None = None,
        notion_token: str | None = None,
        telegram_bot_token: str | None = None,
        telegram_allowed_users: set[str] | None = None,
    ):
        """
        Initialize NotificationHarness.

        Args:
            slack_webhook_url: Slack Incoming Webhook URL
            email_config: SMTP email configuration
            github_token: GitHub API token for issue comments
            notion_token: Notion API token for comments
            telegram_bot_token: Telegram bot token from @BotFather
            telegram_allowed_users: Set of allowed Telegram chat IDs
        """
        self.slack_webhook_url = slack_webhook_url
        self.email_config = email_config
        self.github_token = github_token
        self.notion_token = notion_token
        self.telegram_bot_token = telegram_bot_token
        self.telegram_allowed_users = telegram_allowed_users or set()

        # Store pending responses
        self._pending_responses: dict[str, HumanResponse | None] = {}

        # HTTP client for API calls
        self._http_client: httpx.AsyncClient | None = None

        # Telegram bot handler (lazy loaded)
        self._telegram_handler: Any | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def has_channel(self, channel: NotificationChannel) -> bool:
        """Check if a channel is configured."""
        if channel == NotificationChannel.SLACK:
            return self.slack_webhook_url is not None
        elif channel == NotificationChannel.EMAIL:
            return self.email_config is not None
        elif channel == NotificationChannel.GITHUB_ISSUE:
            return self.github_token is not None
        elif channel == NotificationChannel.NOTION_COMMENT:
            return self.notion_token is not None
        elif channel == NotificationChannel.TELEGRAM:
            return self.telegram_bot_token is not None and bool(self.telegram_allowed_users)
        return False

    async def notify(
        self,
        config: NotificationConfig,
    ) -> list[NotificationResult]:
        """
        Send notification to configured channels.

        Args:
            config: Notification configuration

        Returns:
            List of NotificationResult for each channel
        """
        results = []

        for channel in config.channels:
            if not self.has_channel(channel):
                result = NotificationResult(
                    channel=channel,
                    success=False,
                    error=f"Channel not configured: {channel.value}",
                )
            else:
                try:
                    if channel == NotificationChannel.SLACK:
                        result = await self._send_slack(config)
                    elif channel == NotificationChannel.EMAIL:
                        result = await self._send_email(config)
                    elif channel == NotificationChannel.GITHUB_ISSUE:
                        result = await self._send_github_comment(config)
                    elif channel == NotificationChannel.NOTION_COMMENT:
                        result = await self._send_notion_comment(config)
                    elif channel == NotificationChannel.TELEGRAM:
                        result = await self._send_telegram(config)
                    else:
                        result = NotificationResult(
                            channel=channel,
                            success=False,
                            error=f"Unsupported channel: {channel.value}",
                        )
                except Exception as e:
                    logger.error(f"Failed to send notification via {channel.value}: {e}")
                    result = NotificationResult(
                        channel=channel,
                        success=False,
                        error=str(e),
                    )

            results.append(result)

            # Register message for response tracking
            if result.success and result.message_id:
                self._pending_responses[result.message_id] = None

        return results

    async def _send_slack(self, config: NotificationConfig) -> NotificationResult:
        """Send Slack notification via Incoming Webhook."""
        if not self.slack_webhook_url:
            return NotificationResult(
                channel=NotificationChannel.SLACK,
                success=False,
                error="Slack webhook URL not configured",
            )

        message_id = str(uuid.uuid4())

        # Build Slack Block Kit message
        blocks = self._build_slack_blocks(config)

        payload = {
            "blocks": blocks,
            "text": config.message,  # Fallback text
        }

        client = await self._get_client()
        response = await client.post(
            self.slack_webhook_url,
            json=payload,
        )

        if response.status_code == 200:
            logger.info(f"Slack notification sent: {message_id}")
            return NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id=message_id,
            )
        else:
            error = f"Slack API error: {response.status_code} - {response.text}"
            logger.error(error)
            return NotificationResult(
                channel=NotificationChannel.SLACK,
                success=False,
                error=error,
            )

    def _build_slack_blocks(self, config: NotificationConfig) -> list[dict]:
        """Build Slack Block Kit blocks for notification."""
        blocks = []

        # Header block with title
        if config.title:
            emoji = self._urgency_emoji(config.urgency)
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} {config.title}",
                        "emoji": True,
                    },
                }
            )

        # Message body
        message_text = config.message
        if config.mention_users:
            mentions = " ".join(f"<@{user}>" for user in config.mention_users)
            message_text = f"{mentions}\n\n{message_text}"

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text,
                },
            }
        )

        # Action button if URL provided
        if config.action_url:
            button_text = "Take Action" if config.action_required else "View Details"
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": button_text,
                                "emoji": True,
                            },
                            "url": config.action_url,
                            "style": "primary" if config.action_required else None,
                        },
                    ],
                }
            )

        # Context with metadata
        if config.metadata:
            context_items = [f"*{k}:* {v}" for k, v in config.metadata.items()]
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": " | ".join(context_items),
                        },
                    ],
                }
            )

        return blocks

    def _urgency_emoji(self, urgency: NotificationUrgency) -> str:
        """Get emoji for urgency level."""
        return {
            NotificationUrgency.LOW: "",
            NotificationUrgency.NORMAL: "",
            NotificationUrgency.HIGH: "",
            NotificationUrgency.CRITICAL: "",
        }.get(urgency, "")

    async def _send_email(self, config: NotificationConfig) -> NotificationResult:
        """Send email notification via SMTP."""
        if not self.email_config:
            return NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=False,
                error="Email configuration not provided",
            )

        message_id = str(uuid.uuid4())

        # Build email
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.email_config.from_name} <{self.email_config.from_address}>"
        msg["Subject"] = config.title or "FORGE Notification"
        msg["Message-ID"] = f"<{message_id}@forge-harness>"

        # To addresses from mention_users or metadata
        to_addresses = config.mention_users or config.metadata.get("to_addresses", [])
        if not to_addresses:
            return NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=False,
                error="No recipient email addresses provided",
            )

        msg["To"] = ", ".join(to_addresses)

        # Plain text version
        text_content = config.message
        if config.action_url:
            text_content += f"\n\nAction URL: {config.action_url}"

        # HTML version
        html_content = self._build_email_html(config)

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Send via SMTP (run in executor to not block)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._send_smtp,
                msg,
                to_addresses,
            )
            logger.info(f"Email notification sent: {message_id}")
            return NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=True,
                message_id=message_id,
            )
        except Exception as e:
            error = f"SMTP error: {e}"
            logger.error(error)
            return NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=False,
                error=error,
            )

    def _send_smtp(self, msg: MIMEMultipart, to_addresses: list[str]) -> None:
        """Send email via SMTP (blocking)."""
        if not self.email_config:
            raise ValueError("Email configuration not provided")

        with smtplib.SMTP(self.email_config.smtp_host, self.email_config.smtp_port) as server:
            if self.email_config.use_tls:
                server.starttls()
            server.login(self.email_config.username, self.email_config.password)
            server.sendmail(
                self.email_config.from_address,
                to_addresses,
                msg.as_string(),
            )

    def _build_email_html(self, config: NotificationConfig) -> str:
        """Build HTML email body."""
        urgency_color = {
            NotificationUrgency.LOW: "#6b7280",
            NotificationUrgency.NORMAL: "#2563eb",
            NotificationUrgency.HIGH: "#f59e0b",
            NotificationUrgency.CRITICAL: "#dc2626",
        }.get(config.urgency, "#2563eb")

        action_button = ""
        if config.action_url:
            button_text = "Take Action" if config.action_required else "View Details"
            action_button = f'''
            <p style="margin-top: 20px;">
                <a href="{config.action_url}"
                   style="background-color: {urgency_color}; color: white;
                          padding: 10px 20px; text-decoration: none;
                          border-radius: 5px; display: inline-block;">
                    {button_text}
                </a>
            </p>
            '''

        title_html = ""
        if config.title:
            title_html = (
                f'<h2 style="color: {urgency_color}; margin-bottom: 10px;">{config.title}</h2>'
            )

        metadata_html = ""
        if config.metadata:
            items = "".join(
                f"<li><strong>{k}:</strong> {v}</li>" for k, v in config.metadata.items()
            )
            metadata_html = f'<ul style="margin-top: 15px; color: #6b7280;">{items}</ul>'

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                     max-width: 600px; margin: 0 auto; padding: 20px;">
            {title_html}
            <p style="color: #374151; line-height: 1.6;">{config.message}</p>
            {action_button}
            {metadata_html}
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #e5e7eb;">
            <p style="color: #9ca3af; font-size: 12px;">
                Sent by FORGE Harness
            </p>
        </body>
        </html>
        """

    async def _send_github_comment(self, config: NotificationConfig) -> NotificationResult:
        """Post comment on GitHub issue/PR."""
        if not self.github_token:
            return NotificationResult(
                channel=NotificationChannel.GITHUB_ISSUE,
                success=False,
                error="GitHub token not configured",
            )

        # Extract issue info from metadata
        repo = config.metadata.get("repo")
        issue_number = config.metadata.get("issue_number")

        if not repo or not issue_number:
            return NotificationResult(
                channel=NotificationChannel.GITHUB_ISSUE,
                success=False,
                error="Missing 'repo' or 'issue_number' in metadata",
            )

        message_id = str(uuid.uuid4())

        # Build comment body
        body = self._build_github_comment_body(config)

        client = await self._get_client()
        response = await client.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
            headers={
                "Authorization": f"Bearer {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"body": body},
        )

        if response.status_code == 201:
            comment_data = response.json()
            logger.info(f"GitHub comment posted: {comment_data.get('id')}")
            return NotificationResult(
                channel=NotificationChannel.GITHUB_ISSUE,
                success=True,
                message_id=str(comment_data.get("id", message_id)),
            )
        else:
            error = f"GitHub API error: {response.status_code} - {response.text}"
            logger.error(error)
            return NotificationResult(
                channel=NotificationChannel.GITHUB_ISSUE,
                success=False,
                error=error,
            )

    def _build_github_comment_body(self, config: NotificationConfig) -> str:
        """Build GitHub comment markdown body."""
        urgency_badge = {
            NotificationUrgency.LOW: "",
            NotificationUrgency.NORMAL: "",
            NotificationUrgency.HIGH: "> **Warning**",
            NotificationUrgency.CRITICAL: "> **Critical**",
        }.get(config.urgency, "")

        parts = []

        if config.title:
            parts.append(f"## {config.title}")

        if urgency_badge:
            parts.append(urgency_badge)

        parts.append(config.message)

        if config.action_url:
            action_text = "Take Action" if config.action_required else "View Details"
            parts.append(f"\n[{action_text}]({config.action_url})")

        if config.metadata:
            metadata_lines = [
                f"- **{k}**: {v}"
                for k, v in config.metadata.items()
                if k not in ("repo", "issue_number")
            ]
            if metadata_lines:
                parts.append("\n---\n" + "\n".join(metadata_lines))

        parts.append("\n---\n*Sent by FORGE Harness*")

        return "\n\n".join(parts)

    async def _send_notion_comment(self, config: NotificationConfig) -> NotificationResult:
        """Post comment on Notion page."""
        if not self.notion_token:
            return NotificationResult(
                channel=NotificationChannel.NOTION_COMMENT,
                success=False,
                error="Notion token not configured",
            )

        # Extract page ID from metadata
        page_id = config.metadata.get("page_id")
        if not page_id:
            return NotificationResult(
                channel=NotificationChannel.NOTION_COMMENT,
                success=False,
                error="Missing 'page_id' in metadata",
            )

        message_id = str(uuid.uuid4())

        # Build comment as discussion thread
        # Note: Notion API doesn't have direct comment support yet,
        # so we add a block to the page instead
        blocks = self._build_notion_comment_blocks(config)

        client = await self._get_client()
        response = await client.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers={
                "Authorization": f"Bearer {self.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={"children": blocks},
        )

        if response.status_code == 200:
            logger.info(f"Notion comment added: {message_id}")
            return NotificationResult(
                channel=NotificationChannel.NOTION_COMMENT,
                success=True,
                message_id=message_id,
            )
        else:
            error = f"Notion API error: {response.status_code} - {response.text}"
            logger.error(error)
            return NotificationResult(
                channel=NotificationChannel.NOTION_COMMENT,
                success=False,
                error=error,
            )

    def _build_notion_comment_blocks(self, config: NotificationConfig) -> list[dict]:
        """Build Notion blocks for comment."""
        blocks = []

        # Divider
        blocks.append({"type": "divider", "divider": {}})

        # Title/header callout
        emoji = self._urgency_emoji(config.urgency) or "notification"
        callout_text = config.title or "FORGE Notification"
        blocks.append(
            {
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": emoji},
                    "rich_text": [{"type": "text", "text": {"content": callout_text}}],
                    "color": self._notion_urgency_color(config.urgency),
                },
            }
        )

        # Message paragraph
        blocks.append(
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": config.message}}],
                },
            }
        )

        # Timestamp
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        blocks.append(
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": f"Sent: {timestamp}"},
                            "annotations": {"italic": True, "color": "gray"},
                        },
                    ],
                },
            }
        )

        return blocks

    def _notion_urgency_color(self, urgency: NotificationUrgency) -> str:
        """Get Notion color for urgency level."""
        return {
            NotificationUrgency.LOW: "gray_background",
            NotificationUrgency.NORMAL: "blue_background",
            NotificationUrgency.HIGH: "yellow_background",
            NotificationUrgency.CRITICAL: "red_background",
        }.get(urgency, "blue_background")

    def record_response(
        self,
        notification_id: str,
        channel: NotificationChannel,
        response_type: str,
        message: str | None = None,
        responder: str | None = None,
    ) -> HumanResponse:
        """
        Record a human response to a notification.

        Args:
            notification_id: ID of the original notification
            channel: Channel the response came from
            response_type: Type of response (approved, rejected, comment)
            message: Optional message content
            responder: ID/name of the responder

        Returns:
            HumanResponse object
        """
        response = HumanResponse(
            notification_id=notification_id,
            channel=channel,
            response_type=response_type,
            message=message,
            responder=responder,
        )
        self._pending_responses[notification_id] = response
        logger.info(f"Recorded response for {notification_id}: {response_type}")
        return response

    def get_response(self, notification_id: str) -> HumanResponse | None:
        """Get recorded response for a notification."""
        return self._pending_responses.get(notification_id)

    async def await_response(
        self,
        notification_id: str,
        timeout: timedelta,
        poll_interval: float = 5.0,
    ) -> HumanResponse | None:
        """
        Wait for human response with timeout.

        Note: This polls the internal response store. In a real implementation,
        you would integrate with Slack interactivity, webhooks, or other
        callback mechanisms to capture responses.

        Args:
            notification_id: ID of the notification to wait for
            timeout: Maximum time to wait
            poll_interval: Seconds between polls

        Returns:
            HumanResponse if received, None if timeout
        """
        deadline = datetime.now(UTC) + timeout
        logger.info(f"Waiting for response to {notification_id} (timeout: {timeout})")

        while datetime.now(UTC) < deadline:
            response = self._pending_responses.get(notification_id)
            if response is not None:
                logger.info(f"Response received for {notification_id}: {response.response_type}")
                return response
            await asyncio.sleep(poll_interval)

        logger.warning(f"Timeout waiting for response to {notification_id}")
        return None

    def clear_pending(self, notification_id: str) -> None:
        """Remove a notification from pending responses."""
        self._pending_responses.pop(notification_id, None)

    # =========================================================================
    # Tier-Aware Approval Notifications
    # =========================================================================

    async def send_approval_notification(
        self,
        request_id: str,
        title: str,
        description: str,
        tier: str,  # "watch", "phone", "desktop"
        domain: str | None = None,
        project: str | None = None,
        feature_id: str | None = None,
        approval_type: str | None = None,
        risk_score: float = 0.0,
        context: dict | None = None,
        dashboard_url: str | None = None,
    ) -> NotificationResult:
        """
        Send a tier-aware approval notification to Slack with approve/reject buttons.

        Tier formats:
        - WATCH: Minimal - title + approve/reject buttons only (Apple Watch friendly)
        - PHONE: Summary - title, brief context, buttons (mobile friendly)
        - DESKTOP: Full - title, full context, buttons, dashboard link

        Args:
            request_id: Unique approval request ID (used in button action_id)
            title: Approval title/summary
            description: Full description (shown based on tier)
            tier: "watch", "phone", or "desktop"
            domain: Domain name for context
            project: Project name for context
            feature_id: Feature ID for context
            approval_type: Type of approval (deploy, content, feature, etc.)
            risk_score: Risk score 0-1 for context
            context: Additional context dict
            dashboard_url: URL to full dashboard view

        Returns:
            NotificationResult with success status
        """
        if not self.slack_webhook_url:
            return NotificationResult(
                channel=NotificationChannel.SLACK,
                success=False,
                error="Slack webhook URL not configured",
            )

        # Build tier-appropriate blocks
        blocks = self._build_approval_slack_blocks(
            request_id=request_id,
            title=title,
            description=description,
            tier=tier,
            domain=domain,
            project=project,
            feature_id=feature_id,
            approval_type=approval_type,
            risk_score=risk_score,
            context=context or {},
            dashboard_url=dashboard_url,
        )

        payload = {
            "blocks": blocks,
            "text": f"Approval needed: {title}",  # Fallback text for notifications
        }

        client = await self._get_client()
        response = await client.post(
            self.slack_webhook_url,
            json=payload,
        )

        if response.status_code == 200:
            logger.info(f"Approval notification sent: {request_id} (tier={tier})")
            return NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id=request_id,
            )
        else:
            error = f"Slack API error: {response.status_code} - {response.text}"
            logger.error(error)
            return NotificationResult(
                channel=NotificationChannel.SLACK,
                success=False,
                error=error,
            )

    def _build_approval_slack_blocks(
        self,
        request_id: str,
        title: str,
        description: str,
        tier: str,
        domain: str | None,
        project: str | None,
        feature_id: str | None,
        approval_type: str | None,
        risk_score: float,
        context: dict,
        dashboard_url: str | None,
    ) -> list[dict]:
        """
        Build Slack Block Kit blocks for approval notification based on tier.

        WATCH tier: Minimal for Apple Watch
        - Header with emoji
        - Single approve/reject button row

        PHONE tier: Summary for mobile
        - Header with emoji
        - Brief context section
        - Approve/reject buttons

        DESKTOP tier: Full detail
        - Header with emoji
        - Full description
        - Context fields
        - Approve/reject buttons
        - Dashboard link button
        """
        blocks = []

        # Determine emoji based on approval type and risk
        emoji = self._approval_emoji(approval_type, risk_score)

        # WATCH tier: Ultra-minimal
        if tier == "watch":
            # Compact header - just emoji and short title (truncated)
            short_title = title[:50] + "..." if len(title) > 50 else title
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{emoji} *{short_title}*",
                    },
                }
            )

        # PHONE tier: Summary context
        elif tier == "phone":
            # Header
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} {title[:75]}",
                        "emoji": True,
                    },
                }
            )

            # Brief context line
            context_parts = []
            if domain:
                context_parts.append(f"*{domain}*")
            if project:
                context_parts.append(project)
            if feature_id:
                context_parts.append(f"`{feature_id}`")

            if context_parts:
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": " / ".join(context_parts),
                            }
                        ],
                    }
                )

            # Truncated description
            if description:
                short_desc = description[:200] + "..." if len(description) > 200 else description
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": short_desc,
                        },
                    }
                )

        # DESKTOP tier: Full details
        else:  # desktop
            # Header
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} {title}",
                        "emoji": True,
                    },
                }
            )

            # Full description
            if description:
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": description,
                        },
                    }
                )

            # Context fields (two columns)
            fields = []
            if domain:
                fields.append({"type": "mrkdwn", "text": f"*Domain:*\n{domain}"})
            if project:
                fields.append({"type": "mrkdwn", "text": f"*Project:*\n{project}"})
            if feature_id:
                fields.append({"type": "mrkdwn", "text": f"*Feature:*\n{feature_id}"})
            if approval_type:
                fields.append({"type": "mrkdwn", "text": f"*Type:*\n{approval_type}"})
            if risk_score > 0:
                risk_bar = self._risk_bar(risk_score)
                fields.append({"type": "mrkdwn", "text": f"*Risk:*\n{risk_bar}"})

            if fields:
                blocks.append(
                    {
                        "type": "section",
                        "fields": fields[:10],  # Slack limit
                    }
                )

            # Additional context
            if context:
                context_items = [f"*{k}:* {v}" for k, v in list(context.items())[:5]]
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": " | ".join(context_items),
                            }
                        ],
                    }
                )

        # Divider before buttons
        blocks.append({"type": "divider"})

        # Approve/Reject buttons (all tiers)
        action_elements = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                "style": "primary",
                "action_id": f"approval_{request_id}_approve",
                "value": request_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                "style": "danger",
                "action_id": f"approval_{request_id}_reject",
                "value": request_id,
            },
        ]

        # Add dashboard link for desktop tier
        if tier == "desktop" and dashboard_url:
            action_elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Details", "emoji": True},
                    "url": dashboard_url,
                }
            )

        blocks.append(
            {
                "type": "actions",
                "block_id": f"approval_actions_{request_id}",
                "elements": action_elements,
            }
        )

        return blocks

    def _approval_emoji(self, approval_type: str | None, risk_score: float) -> str:
        """Get emoji for approval type and risk level."""
        # High risk always gets warning
        if risk_score > 0.6:
            return "🚨"
        if risk_score > 0.3:
            return "⚠️"

        # Type-based emoji
        type_emojis = {
            "deploy": "🚀",
            "deployment": "🚀",
            "content": "📝",
            "feature": "✨",
            "security": "🔐",
            "config": "⚙️",
            "data": "📊",
            "compliance": "📋",
        }
        return type_emojis.get(approval_type or "", "📋")

    def _risk_bar(self, risk_score: float) -> str:
        """Generate visual risk bar."""
        filled = int(risk_score * 5)
        empty = 5 - filled
        bar = "🟥" * filled + "⬜" * empty
        percent = int(risk_score * 100)
        return f"{bar} {percent}%"

    # =====================================================================
    # Telegram Notifications (Phase 1)
    # =====================================================================

    def _get_telegram_handler(self) -> Any:
        """Get or create Telegram bot handler."""
        if self._telegram_handler is None:
            from .telegram_bot import TelegramBotHandler

            self._telegram_handler = TelegramBotHandler(
                bot_token=self.telegram_bot_token,
                allowed_users=self.telegram_allowed_users,
            )
        return self._telegram_handler

    async def _send_telegram(self, config: NotificationConfig) -> NotificationResult:
        """Send notification via Telegram."""
        if not self.telegram_bot_token or not self.telegram_allowed_users:
            return NotificationResult(
                channel=NotificationChannel.TELEGRAM,
                success=False,
                error="Telegram not configured (missing token or allowed users)",
            )

        handler = self._get_telegram_handler()
        message_id = str(uuid.uuid4())

        try:
            # Determine tier from urgency
            tier = self._urgency_to_tier(config.urgency)

            # Send to all allowed users
            results = []
            for chat_id in self.telegram_allowed_users:
                try:
                    result = await handler.send_notification(
                        chat_id=chat_id,
                        title=config.title or "FORGE Notification",
                        message=config.message,
                        tier=tier,
                        metadata=config.metadata,
                        action_url=config.action_url,
                    )
                    results.append({"chat_id": chat_id, "success": True, "result": result})
                except Exception as e:
                    logger.error(f"Failed to send Telegram to {chat_id}: {e}")
                    results.append({"chat_id": chat_id, "success": False, "error": str(e)})

            # Check if any succeeded
            success_count = sum(1 for r in results if r["success"])
            if success_count > 0:
                logger.info(f"Telegram notification sent to {success_count} users: {message_id}")
                return NotificationResult(
                    channel=NotificationChannel.TELEGRAM,
                    success=True,
                    message_id=message_id,
                )
            else:
                error = f"All Telegram sends failed: {results}"
                logger.error(error)
                return NotificationResult(
                    channel=NotificationChannel.TELEGRAM,
                    success=False,
                    error=error,
                )

        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return NotificationResult(
                channel=NotificationChannel.TELEGRAM,
                success=False,
                error=str(e),
            )

    def _urgency_to_tier(self, urgency: NotificationUrgency) -> str:
        """Convert notification urgency to device tier."""
        return {
            NotificationUrgency.LOW: "watch",
            NotificationUrgency.NORMAL: "phone",
            NotificationUrgency.HIGH: "phone",
            NotificationUrgency.CRITICAL: "desktop",
        }.get(urgency, "phone")

    async def send_telegram_notification(
        self,
        title: str,
        message: str,
        tier: str = "phone",
        metadata: dict | None = None,
        action_url: str | None = None,
    ) -> NotificationResult:
        """
        Send a notification directly to Telegram.

        Args:
            title: Notification title
            message: Message body
            tier: Device tier (watch, phone, desktop)
            metadata: Additional context
            action_url: Optional action URL

        Returns:
            NotificationResult
        """
        config = NotificationConfig(
            channels=[NotificationChannel.TELEGRAM],
            title=title,
            message=message,
            metadata=metadata or {},
            action_url=action_url,
        )
        results = await self.notify(config)
        return results[0] if results else NotificationResult(
            channel=NotificationChannel.TELEGRAM,
            success=False,
            error="No results returned",
        )

    async def send_telegram_approval(
        self,
        request_id: str,
        title: str,
        description: str,
        tier: str = "phone",
        metadata: dict | None = None,
    ) -> NotificationResult:
        """
        Send approval request via Telegram with inline buttons.

        Args:
            request_id: Unique approval request ID
            title: Approval title
            description: Approval description
            tier: Device tier
            metadata: Additional context

        Returns:
            NotificationResult
        """
        if not self.telegram_bot_token or not self.telegram_allowed_users:
            return NotificationResult(
                channel=NotificationChannel.TELEGRAM,
                success=False,
                error="Telegram not configured",
            )

        handler = self._get_telegram_handler()
        message_id = str(uuid.uuid4())

        try:
            # Send to all allowed users
            results = []
            for chat_id in self.telegram_allowed_users:
                try:
                    result = await handler.send_approval_request(
                        chat_id=chat_id,
                        request_id=request_id,
                        title=title,
                        description=description,
                        tier=tier,
                        metadata=metadata,
                    )
                    results.append({"chat_id": chat_id, "success": True, "result": result})
                except Exception as e:
                    logger.error(f"Failed to send approval to {chat_id}: {e}")
                    results.append({"chat_id": chat_id, "success": False, "error": str(e)})

            success_count = sum(1 for r in results if r["success"])
            if success_count > 0:
                logger.info(f"Telegram approval sent to {success_count} users: {request_id}")
                return NotificationResult(
                    channel=NotificationChannel.TELEGRAM,
                    success=True,
                    message_id=message_id,
                )
            else:
                return NotificationResult(
                    channel=NotificationChannel.TELEGRAM,
                    success=False,
                    error=f"All sends failed: {results}",
                )

        except Exception as e:
            logger.error(f"Failed to send Telegram approval: {e}")
            return NotificationResult(
                channel=NotificationChannel.TELEGRAM,
                success=False,
                error=str(e),
            )


def create_notification_harness(
    slack_webhook_url: str | None = None,
    email_config: dict | None = None,
    github_token: str | None = None,
    notion_token: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_allowed_users: set[str] | None = None,
) -> NotificationHarness:
    """
    Factory function to create NotificationHarness.

    Environment variables are used as fallbacks:
    - SLACK_WEBHOOK_URL
    - GITHUB_TOKEN
    - NOTION_API_TOKEN
    - TELEGRAM_BOT_TOKEN
    - TELEGRAM_ALLOWED_USERS (comma-separated chat IDs)

    Args:
        slack_webhook_url: Slack Incoming Webhook URL
        email_config: Dict with SMTP configuration
        github_token: GitHub API token
        notion_token: Notion API token
        telegram_bot_token: Telegram bot token from @BotFather
        telegram_allowed_users: Set of allowed Telegram chat IDs

    Returns:
        Configured NotificationHarness instance
    """
    # Use environment variables as fallbacks
    slack_url = slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    gh_token = github_token or os.environ.get("GITHUB_TOKEN")
    notion = notion_token or os.environ.get("NOTION_API_TOKEN")
    telegram_token = telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")

    # Parse Telegram allowed users from env or parameter
    telegram_users = telegram_allowed_users
    if telegram_users is None:
        users_str = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
        telegram_users = set(
            user_id.strip()
            for user_id in users_str.split(",")
            if user_id.strip()
        )

    # Parse email config if provided
    email_cfg = None
    if email_config:
        email_cfg = EmailConfig(
            smtp_host=email_config["smtp_host"],
            smtp_port=email_config["smtp_port"],
            username=email_config["username"],
            password=email_config["password"],
            from_address=email_config["from_address"],
            from_name=email_config.get("from_name", "FORGE Harness"),
            use_tls=email_config.get("use_tls", True),
        )

    return NotificationHarness(
        slack_webhook_url=slack_url,
        email_config=email_cfg,
        github_token=gh_token,
        notion_token=notion,
        telegram_bot_token=telegram_token,
        telegram_allowed_users=telegram_users,
    )

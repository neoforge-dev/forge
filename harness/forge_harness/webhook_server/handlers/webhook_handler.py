"""Webhook Handler

Handles incoming webhooks from Slack and GitHub and resolves human gates.
Connects webhook events to NotificationHarness.record_response().
"""

import hashlib
import hmac
import os
import re
from datetime import UTC, datetime
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.models import WebhookPayload, WebhookResponse

logger = get_logger(__name__)


class WebhookHandler:
    """Handles incoming webhooks and resolves human gates.

    Connects webhook events to NotificationHarness.record_response().
    """

    def __init__(
        self,
        notification_harness: Any,
        slack_signing_secret: str | None = None,
        github_webhook_secret: str | None = None,
    ):
        """Initialize webhook handler.

        Args:
            notification_harness: NotificationHarness instance for recording responses
            slack_signing_secret: Slack signing secret for verification
            github_webhook_secret: GitHub webhook secret for verification
        """
        self.notification = notification_harness
        self.slack_signing_secret = slack_signing_secret or os.environ.get("SLACK_SIGNING_SECRET")
        self.github_webhook_secret = github_webhook_secret or os.environ.get(
            "GITHUB_WEBHOOK_SECRET"
        )

    async def handle_slack(
        self, payload: dict[str, Any], signature: str | None, timestamp: str | None
    ) -> WebhookResponse:
        """Handle Slack interactive message webhook.

        Args:
            payload: Slack payload (parsed from form data or JSON)
            signature: X-Slack-Signature header
            timestamp: X-Slack-Request-Timestamp header

        Returns:
            WebhookResponse
        """
        # Parse the payload
        parsed = self.parse_slack_payload(payload)

        # Record the response in notification harness
        if self.notification is not None:
            try:
                self.notification.record_response(
                    notification_id=parsed.notification_id,
                    response_type=parsed.response_type,
                    responder=parsed.responder,
                    message=parsed.message,
                )
            except Exception as e:
                logger.warning(f"Failed to record Slack response: {e}")
                return WebhookResponse(
                    status="error",
                    notification_id=parsed.notification_id,
                    message=str(e),
                )

        return WebhookResponse(
            status="received",
            notification_id=parsed.notification_id,
            message=f"Response '{parsed.response_type}' recorded",
        )

    async def handle_github(
        self, payload: dict[str, Any], signature: str | None, event_type: str
    ) -> WebhookResponse:
        """Handle GitHub webhook.

        Args:
            payload: GitHub webhook payload
            signature: X-Hub-Signature-256 header
            event_type: X-GitHub-Event header

        Returns:
            WebhookResponse
        """
        # Parse the payload
        parsed = self.parse_github_payload(payload, event_type)

        # Record the response in notification harness
        if self.notification is not None:
            try:
                self.notification.record_response(
                    notification_id=parsed.notification_id,
                    response_type=parsed.response_type,
                    responder=parsed.responder,
                    message=parsed.message,
                )
            except Exception as e:
                logger.warning(f"Failed to record GitHub response: {e}")
                return WebhookResponse(
                    status="error",
                    notification_id=parsed.notification_id,
                    message=str(e),
                )

        return WebhookResponse(
            status="received",
            notification_id=parsed.notification_id,
            message=f"Response '{parsed.response_type}' recorded",
        )

    def verify_slack_signature(self, body: bytes, signature: str, timestamp: str) -> bool:
        """Verify Slack request signature.

        Args:
            body: Raw request body
            signature: X-Slack-Signature header
            timestamp: X-Slack-Request-Timestamp header

        Returns:
            True if signature is valid
        """
        if not self.slack_signing_secret:
            logger.warning("Slack signing secret not configured - verification disabled")
            # Return False in production to enforce security
            if os.environ.get("FORGE_ENV", "development").lower() == "production":
                logger.error("SECURITY: Slack webhooks disabled - no signing secret")
                return False
            return True

        # Validate timestamp to prevent replay attacks (within 5 minutes)
        try:
            request_time = int(timestamp)
            current_time = int(datetime.now(UTC).timestamp())
            if abs(current_time - request_time) > 300:  # 5 minutes
                logger.warning(f"Slack signature timestamp too old: {current_time - request_time}s")
                return False
        except (ValueError, TypeError):
            logger.warning("Invalid Slack timestamp format")
            return False

        # Build the signature base string
        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"

        # Compute HMAC-SHA256
        computed_sig = (
            "v0="
            + hmac.new(
                self.slack_signing_secret.encode("utf-8"),
                sig_basestring.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )

        # Compare signatures
        return hmac.compare_digest(computed_sig, signature)

    def verify_github_signature(self, body: bytes, signature: str) -> bool:
        """Verify GitHub webhook signature.

        Args:
            body: Raw request body
            signature: X-Hub-Signature-256 header

        Returns:
            True if signature is valid
        """
        if not self.github_webhook_secret:
            logger.warning("GitHub webhook secret not configured - verification disabled")
            # Return False in production to enforce security
            if os.environ.get("FORGE_ENV", "development").lower() == "production":
                logger.error("SECURITY: GitHub webhooks disabled - no webhook secret")
                return False
            return True

        if not signature or not signature.startswith("sha256="):
            return False

        # Compute HMAC-SHA256
        computed_sig = (
            "sha256="
            + hmac.new(
                self.github_webhook_secret.encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
        )

        # Compare signatures
        return hmac.compare_digest(computed_sig, signature)

    def parse_slack_payload(self, payload: dict[str, Any]) -> WebhookPayload:
        """Parse Slack interactive message payload.

        Args:
            payload: Slack payload

        Returns:
            Parsed WebhookPayload
        """
        # Determine the payload type
        payload_type = payload.get("type", "")

        # Extract user info
        user = payload.get("user", {})
        responder = user.get("username", user.get("name", user.get("id", "unknown")))

        # Initialize defaults
        notification_id = ""
        response_type = "unknown"
        message = None

        if payload_type == "block_actions":
            # Handle button clicks
            actions = payload.get("actions", [])
            if actions:
                action = actions[0]
                action_id = action.get("action_id", "")
                value = action.get("value", "")

                # Parse action_id format: "action_{notification_id}_{response}"
                parts = action_id.split("_")
                if len(parts) >= 3:
                    notification_id = parts[1]
                    response_type = parts[2]
                elif len(parts) == 2:
                    notification_id = parts[1]
                    response_type = value or "action"
                else:
                    notification_id = action_id
                    response_type = value or "action"

        elif payload_type == "view_submission":
            # Handle modal submissions
            callback_id = payload.get("view", {}).get("callback_id", "")
            parts = callback_id.split("_")
            if len(parts) >= 2:
                notification_id = parts[1]
            response_type = "submitted"

            # Extract message from input values
            values = payload.get("view", {}).get("state", {}).get("values", {})
            for block in values.values():
                for input_val in block.values():
                    if "value" in input_val:
                        message = input_val["value"]
                        break

        return WebhookPayload(
            source="slack",
            event_type=payload_type,
            notification_id=notification_id,
            response_type=response_type,
            responder=responder,
            message=message,
            raw_payload=payload,
        )

    def parse_github_payload(self, payload: dict[str, Any], event_type: str) -> WebhookPayload:
        """Parse GitHub webhook payload.

        Args:
            payload: GitHub payload
            event_type: GitHub event type

        Returns:
            Parsed WebhookPayload
        """
        # Extract user info
        sender = payload.get("sender", {})
        responder = sender.get("login", "unknown")

        # Initialize defaults
        notification_id = ""
        response_type = "comment"
        message = None

        if event_type == "issue_comment":
            # Handle issue comments
            comment = payload.get("comment", {})
            comment_body = comment.get("body", "")
            message = comment_body

            # Look for notification_id in issue body
            issue = payload.get("issue", {})
            issue_body = issue.get("body", "")

            # Pattern: <!-- notification_id: xxx -->
            match = re.search(r"<!--\s*notification_id:\s*([^\s]+)\s*-->", issue_body)
            if match:
                notification_id = match.group(1)

            # Determine response type from comment content
            lower_body = comment_body.lower()
            if any(
                word in lower_body
                for word in ["lgtm", "approved", "+1", "approve", ":+1:", ":shipit:"]
            ):
                response_type = "approved"
            elif any(
                word in lower_body
                for word in ["rejected", "-1", "changes requested", "reject", ":-1:"]
            ):
                response_type = "rejected"

        elif event_type == "pull_request_review":
            # Handle PR reviews
            review = payload.get("review", {})
            review_state = review.get("state", "").lower()
            message = review.get("body", "")

            # Look for notification_id in PR body
            pr = payload.get("pull_request", {})
            pr_body = pr.get("body", "")

            # Pattern: <!-- notification_id: xxx -->
            match = re.search(r"<!--\s*notification_id:\s*([^\s]+)\s*-->", pr_body)
            if match:
                notification_id = match.group(1)

            # Map review state to response type
            if review_state == "approved":
                response_type = "approved"
            elif review_state in ("changes_requested", "request_changes"):
                response_type = "rejected"
            else:
                response_type = "comment"

        return WebhookPayload(
            source="github",
            event_type=event_type,
            notification_id=notification_id,
            response_type=response_type,
            responder=responder,
            message=message,
            raw_payload=payload,
        )


# Singleton instance
_webhook_handler: WebhookHandler | None = None


def get_webhook_handler() -> WebhookHandler:
    """Get or create the webhook handler singleton."""
    global _webhook_handler
    if _webhook_handler is None:
        _webhook_handler = WebhookHandler(notification_harness=None)
    return _webhook_handler

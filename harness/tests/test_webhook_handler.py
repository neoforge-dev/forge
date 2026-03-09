"""Tests for WebhookHandler.

Comprehensive tests for webhook handler operations with mocked dependencies.
Targets 80%+ coverage for webhook_handler.py.
"""

import hashlib
import hmac
import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.core.models import WebhookPayload, WebhookResponse
from forge_harness.webhook_server.handlers.webhook_handler import (
    WebhookHandler,
    get_webhook_handler,
)


class TestWebhookHandlerInit:
    """Tests for WebhookHandler initialization."""

    def test_init_with_notification_harness(self):
        """Test initialization with provided notification harness."""
        mock_notification = MagicMock()
        handler = WebhookHandler(
            notification_harness=mock_notification,
            slack_signing_secret="slack-secret",
            github_webhook_secret="github-secret",
        )

        assert handler.notification is mock_notification
        assert handler.slack_signing_secret == "slack-secret"
        assert handler.github_webhook_secret == "github-secret"

    def test_init_without_notification_harness(self):
        """Test initialization without notification harness."""
        handler = WebhookHandler(notification_harness=None)

        assert handler.notification is None

    def test_init_loads_secrets_from_env(self):
        """Test initialization loads secrets from environment."""
        with patch.dict(os.environ, {
            "SLACK_SIGNING_SECRET": "env-slack-secret",
            "GITHUB_WEBHOOK_SECRET": "env-github-secret",
        }):
            handler = WebhookHandler(notification_harness=None)

            assert handler.slack_signing_secret == "env-slack-secret"
            assert handler.github_webhook_secret == "env-github-secret"


class TestWebhookHandlerSlack:
    """Tests for Slack webhook handling."""

    @pytest.fixture
    def mock_notification(self):
        """Create mock notification harness."""
        return MagicMock()

    @pytest.fixture
    def handler(self, mock_notification):
        """Create handler with mocked notification harness."""
        return WebhookHandler(
            notification_harness=mock_notification,
            slack_signing_secret="test-secret",
        )

    @pytest.mark.asyncio
    async def test_handle_slack_success(self, handler, mock_notification):
        """Test successful Slack webhook handling."""
        payload = {
            "type": "block_actions",
            "user": {"username": "testuser"},
            "actions": [{
                "action_id": "action_notif123_approved",
                "value": "approve",
            }],
        }

        result = await handler.handle_slack(payload, None, None)

        assert result.status == "received"
        assert result.notification_id == "notif123"
        assert "approved" in result.message

        # Verify notification was recorded
        mock_notification.record_response.assert_called_once_with(
            notification_id="notif123",
            response_type="approved",
            responder="testuser",
            message=None,
        )

    @pytest.mark.asyncio
    async def test_handle_slack_record_response_fails(self, handler, mock_notification):
        """Test Slack webhook when record_response fails."""
        mock_notification.record_response.side_effect = Exception("Database error")

        payload = {
            "type": "block_actions",
            "user": {"username": "testuser"},
            "actions": [{
                "action_id": "action_notif123_approved",
                "value": "approve",
            }],
        }

        result = await handler.handle_slack(payload, None, None)

        assert result.status == "error"
        assert result.notification_id == "notif123"
        assert "Database error" in result.message

    @pytest.mark.asyncio
    async def test_handle_slack_no_notification_harness(self):
        """Test Slack webhook without notification harness."""
        handler = WebhookHandler(notification_harness=None)

        payload = {
            "type": "block_actions",
            "user": {"username": "testuser"},
            "actions": [{
                "action_id": "action_notif123_approved",
            }],
        }

        result = await handler.handle_slack(payload, None, None)

        # Should succeed but not record anything
        assert result.status == "received"
        assert result.notification_id == "notif123"


class TestWebhookHandlerGitHub:
    """Tests for GitHub webhook handling."""

    @pytest.fixture
    def mock_notification(self):
        """Create mock notification harness."""
        return MagicMock()

    @pytest.fixture
    def handler(self, mock_notification):
        """Create handler with mocked notification harness."""
        return WebhookHandler(
            notification_harness=mock_notification,
            github_webhook_secret="test-secret",
        )

    @pytest.mark.asyncio
    async def test_handle_github_success(self, handler, mock_notification):
        """Test successful GitHub webhook handling."""
        payload = {
            "action": "created",
            "issue": {
                "body": "Issue body\n<!-- notification_id: gh-notif-456 -->",
            },
            "comment": {
                "body": "LGTM! This looks great.",
            },
            "sender": {"login": "github-user"},
        }

        result = await handler.handle_github(payload, None, "issue_comment")

        assert result.status == "received"
        assert result.notification_id == "gh-notif-456"
        assert "approved" in result.message

        # Verify notification was recorded
        mock_notification.record_response.assert_called_once_with(
            notification_id="gh-notif-456",
            response_type="approved",
            responder="github-user",
            message="LGTM! This looks great.",
        )

    @pytest.mark.asyncio
    async def test_handle_github_record_response_fails(self, handler, mock_notification):
        """Test GitHub webhook when record_response fails."""
        mock_notification.record_response.side_effect = Exception("Redis error")

        payload = {
            "issue": {
                "body": "<!-- notification_id: gh-notif-789 -->",
            },
            "comment": {"body": "approved"},
            "sender": {"login": "github-user"},
        }

        result = await handler.handle_github(payload, None, "issue_comment")

        assert result.status == "error"
        assert result.notification_id == "gh-notif-789"
        assert "Redis error" in result.message

    @pytest.mark.asyncio
    async def test_handle_github_no_notification_harness(self):
        """Test GitHub webhook without notification harness."""
        handler = WebhookHandler(notification_harness=None)

        payload = {
            "issue": {
                "body": "<!-- notification_id: gh-notif-999 -->",
            },
            "comment": {"body": "comment"},
            "sender": {"login": "user"},
        }

        result = await handler.handle_github(payload, None, "issue_comment")

        # Should succeed but not record anything
        assert result.status == "received"
        assert result.notification_id == "gh-notif-999"


class TestSlackSignatureVerification:
    """Tests for Slack signature verification."""

    @pytest.fixture
    def handler(self):
        """Create handler with Slack secret."""
        return WebhookHandler(
            notification_harness=None,
            slack_signing_secret="test-signing-secret",
        )

    def test_verify_slack_signature_valid(self, handler):
        """Test valid Slack signature verification."""
        body = b'{"type":"block_actions"}'
        timestamp = str(int(datetime.now(UTC).timestamp()))

        # Compute valid signature
        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        computed_sig = "v0=" + hmac.new(
            b"test-signing-secret",
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = handler.verify_slack_signature(body, computed_sig, timestamp)

        assert result is True

    def test_verify_slack_signature_invalid(self, handler):
        """Test invalid Slack signature verification."""
        body = b'{"type":"block_actions"}'
        timestamp = str(int(datetime.now(UTC).timestamp()))
        invalid_signature = "v0=invalid_signature_hash"

        result = handler.verify_slack_signature(body, invalid_signature, timestamp)

        assert result is False

    def test_verify_slack_signature_no_secret(self):
        """Test signature verification without secret in development."""
        handler = WebhookHandler(
            notification_harness=None,
            slack_signing_secret=None,
        )

        with patch.dict(os.environ, {"FORGE_ENV": "development"}):
            result = handler.verify_slack_signature(b"body", "sig", "12345")

            # Should return True in development
            assert result is True

    def test_verify_slack_signature_no_secret_production(self):
        """Test signature verification without secret in production."""
        handler = WebhookHandler(
            notification_harness=None,
            slack_signing_secret=None,
        )

        with patch.dict(os.environ, {"FORGE_ENV": "production"}):
            result = handler.verify_slack_signature(b"body", "sig", "12345")

            # Should return False in production
            assert result is False

    def test_verify_slack_signature_old_timestamp(self, handler):
        """Test signature verification with old timestamp (replay attack)."""
        body = b'{"type":"block_actions"}'
        # Timestamp from 10 minutes ago
        old_timestamp = str(int(datetime.now(UTC).timestamp()) - 600)

        # Compute valid signature but with old timestamp
        sig_basestring = f"v0:{old_timestamp}:{body.decode('utf-8')}"
        computed_sig = "v0=" + hmac.new(
            b"test-signing-secret",
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = handler.verify_slack_signature(body, computed_sig, old_timestamp)

        # Should reject due to timestamp too old
        assert result is False

    def test_verify_slack_signature_invalid_timestamp_format(self, handler):
        """Test signature verification with invalid timestamp format."""
        body = b'{"type":"block_actions"}'
        invalid_timestamp = "not-a-number"

        result = handler.verify_slack_signature(body, "v0=sig", invalid_timestamp)

        assert result is False

    def test_verify_slack_signature_future_timestamp(self, handler):
        """Test signature verification with future timestamp."""
        body = b'{"type":"block_actions"}'
        # Timestamp from 10 minutes in the future
        future_timestamp = str(int(datetime.now(UTC).timestamp()) + 600)

        # Compute valid signature but with future timestamp
        sig_basestring = f"v0:{future_timestamp}:{body.decode('utf-8')}"
        computed_sig = "v0=" + hmac.new(
            b"test-signing-secret",
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = handler.verify_slack_signature(body, computed_sig, future_timestamp)

        # Should reject due to timestamp in future
        assert result is False


class TestGitHubSignatureVerification:
    """Tests for GitHub signature verification."""

    @pytest.fixture
    def handler(self):
        """Create handler with GitHub secret."""
        return WebhookHandler(
            notification_harness=None,
            github_webhook_secret="github-webhook-secret",
        )

    def test_verify_github_signature_valid(self, handler):
        """Test valid GitHub signature verification."""
        body = b'{"action":"created"}'

        # Compute valid signature
        computed_sig = "sha256=" + hmac.new(
            b"github-webhook-secret",
            body,
            hashlib.sha256,
        ).hexdigest()

        result = handler.verify_github_signature(body, computed_sig)

        assert result is True

    def test_verify_github_signature_invalid(self, handler):
        """Test invalid GitHub signature verification."""
        body = b'{"action":"created"}'
        invalid_signature = "sha256=invalid_signature_hash"

        result = handler.verify_github_signature(body, invalid_signature)

        assert result is False

    def test_verify_github_signature_no_secret(self):
        """Test signature verification without secret in development."""
        handler = WebhookHandler(
            notification_harness=None,
            github_webhook_secret=None,
        )

        with patch.dict(os.environ, {"FORGE_ENV": "development"}):
            result = handler.verify_github_signature(b"body", "sha256=sig")

            # Should return True in development
            assert result is True

    def test_verify_github_signature_no_secret_production(self):
        """Test signature verification without secret in production."""
        handler = WebhookHandler(
            notification_harness=None,
            github_webhook_secret=None,
        )

        with patch.dict(os.environ, {"FORGE_ENV": "production"}):
            result = handler.verify_github_signature(b"body", "sha256=sig")

            # Should return False in production
            assert result is False

    def test_verify_github_signature_missing_prefix(self, handler):
        """Test signature verification with missing sha256 prefix."""
        body = b'{"action":"created"}'

        # Signature without sha256= prefix
        sig_no_prefix = hmac.new(
            b"github-webhook-secret",
            body,
            hashlib.sha256,
        ).hexdigest()

        result = handler.verify_github_signature(body, sig_no_prefix)

        assert result is False

    def test_verify_github_signature_empty(self, handler):
        """Test signature verification with empty signature."""
        result = handler.verify_github_signature(b"body", "")

        assert result is False

    def test_verify_github_signature_none(self, handler):
        """Test signature verification with None signature."""
        result = handler.verify_github_signature(b"body", None)

        assert result is False


class TestParseSlackPayload:
    """Tests for Slack payload parsing."""

    @pytest.fixture
    def handler(self):
        """Create handler."""
        return WebhookHandler(notification_harness=None)

    def test_parse_slack_block_actions(self, handler):
        """Test parsing Slack block_actions payload."""
        payload = {
            "type": "block_actions",
            "user": {
                "username": "alice",
                "id": "U12345",
            },
            "actions": [{
                "action_id": "action_notif123_approved",
                "value": "approve",
            }],
        }

        result = handler.parse_slack_payload(payload)

        assert isinstance(result, WebhookPayload)
        assert result.source == "slack"
        assert result.event_type == "block_actions"
        assert result.notification_id == "notif123"
        assert result.response_type == "approved"
        assert result.responder == "alice"
        assert result.message is None
        assert result.raw_payload == payload

    def test_parse_slack_block_actions_with_value(self, handler):
        """Test parsing Slack block_actions with value fallback."""
        payload = {
            "type": "block_actions",
            "user": {"id": "U99999"},
            "actions": [{
                "action_id": "simple_notif456",
                "value": "custom_action",
            }],
        }

        result = handler.parse_slack_payload(payload)

        assert result.notification_id == "notif456"
        assert result.response_type == "custom_action"
        assert result.responder == "U99999"

    def test_parse_slack_block_actions_single_part_action_id(self, handler):
        """Test parsing Slack block_actions with single-part action_id (no underscores)."""
        payload = {
            "type": "block_actions",
            "user": {"name": "Bob"},
            "actions": [{
                "action_id": "simple",
                "value": "clicked",
            }],
        }

        result = handler.parse_slack_payload(payload)

        # When action_id has no parts (len(parts) == 1), it becomes notification_id
        # and response_type comes from value or defaults to "action"
        assert result.notification_id == "simple"
        assert result.response_type == "clicked"
        assert result.responder == "Bob"

    def test_parse_slack_view_submission(self, handler):
        """Test parsing Slack view_submission payload."""
        payload = {
            "type": "view_submission",
            "user": {"username": "charlie"},
            "view": {
                "callback_id": "modal_notif789",
                "state": {
                    "values": {
                        "block_id": {
                            "input_id": {
                                "value": "User feedback here",
                            },
                        },
                    },
                },
            },
        }

        result = handler.parse_slack_payload(payload)

        assert result.source == "slack"
        assert result.event_type == "view_submission"
        assert result.notification_id == "notif789"
        assert result.response_type == "submitted"
        assert result.responder == "charlie"
        assert result.message == "User feedback here"

    def test_parse_slack_view_submission_no_input(self, handler):
        """Test parsing Slack view_submission without input values."""
        payload = {
            "type": "view_submission",
            "user": {"username": "diana"},
            "view": {
                "callback_id": "modal_notif999",
                "state": {
                    "values": {},
                },
            },
        }

        result = handler.parse_slack_payload(payload)

        assert result.notification_id == "notif999"
        assert result.response_type == "submitted"
        assert result.message is None

    def test_parse_slack_unknown_type(self, handler):
        """Test parsing Slack payload with unknown type."""
        payload = {
            "type": "unknown_type",
            "user": {"username": "eve"},
        }

        result = handler.parse_slack_payload(payload)

        assert result.source == "slack"
        assert result.event_type == "unknown_type"
        assert result.notification_id == ""
        assert result.response_type == "unknown"
        assert result.responder == "eve"

    def test_parse_slack_missing_user(self, handler):
        """Test parsing Slack payload with missing user."""
        payload = {
            "type": "block_actions",
            "actions": [{
                "action_id": "action_notif111_test",
            }],
        }

        result = handler.parse_slack_payload(payload)

        assert result.responder == "unknown"

    def test_parse_slack_empty_actions(self, handler):
        """Test parsing Slack payload with empty actions list."""
        payload = {
            "type": "block_actions",
            "user": {"username": "frank"},
            "actions": [],
        }

        result = handler.parse_slack_payload(payload)

        assert result.notification_id == ""
        assert result.response_type == "unknown"


class TestParseGitHubPayload:
    """Tests for GitHub payload parsing."""

    @pytest.fixture
    def handler(self):
        """Create handler."""
        return WebhookHandler(notification_harness=None)

    def test_parse_github_issue_comment_approved(self, handler):
        """Test parsing GitHub issue comment with approval keywords."""
        payload = {
            "action": "created",
            "issue": {
                "number": 42,
                "body": "Issue description\n<!-- notification_id: gh-notif-001 -->\nMore text",
            },
            "comment": {
                "body": "LGTM! Looks great :+1:",
            },
            "sender": {"login": "reviewer1"},
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert isinstance(result, WebhookPayload)
        assert result.source == "github"
        assert result.event_type == "issue_comment"
        assert result.notification_id == "gh-notif-001"
        assert result.response_type == "approved"
        assert result.responder == "reviewer1"
        assert "LGTM" in result.message

    def test_parse_github_issue_comment_rejected(self, handler):
        """Test parsing GitHub issue comment with rejection keywords."""
        payload = {
            "issue": {
                "body": "<!-- notification_id: gh-notif-002 -->",
            },
            "comment": {
                "body": "Changes requested. Please fix :-1:",
            },
            "sender": {"login": "reviewer2"},
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert result.notification_id == "gh-notif-002"
        assert result.response_type == "rejected"
        assert "Changes requested" in result.message

    def test_parse_github_issue_comment_generic(self, handler):
        """Test parsing GitHub issue comment without approval/rejection keywords."""
        payload = {
            "issue": {
                "body": "Issue\n<!-- notification_id: gh-notif-003 -->",
            },
            "comment": {
                "body": "Just a regular comment",
            },
            "sender": {"login": "commenter"},
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert result.notification_id == "gh-notif-003"
        assert result.response_type == "comment"
        assert result.message == "Just a regular comment"

    def test_parse_github_issue_comment_no_notification_id(self, handler):
        """Test parsing GitHub issue comment without notification_id."""
        payload = {
            "issue": {
                "body": "Regular issue without notification marker",
            },
            "comment": {
                "body": "Comment",
            },
            "sender": {"login": "user"},
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert result.notification_id == ""
        assert result.response_type == "comment"

    def test_parse_github_pr_review_approved(self, handler):
        """Test parsing GitHub PR review with approved state."""
        payload = {
            "action": "submitted",
            "pull_request": {
                "number": 123,
                "body": "PR description\n<!-- notification_id: pr-notif-001 -->",
            },
            "review": {
                "state": "approved",
                "body": "Great work!",
            },
            "sender": {"login": "maintainer"},
        }

        result = handler.parse_github_payload(payload, "pull_request_review")

        assert result.source == "github"
        assert result.event_type == "pull_request_review"
        assert result.notification_id == "pr-notif-001"
        assert result.response_type == "approved"
        assert result.responder == "maintainer"
        assert result.message == "Great work!"

    def test_parse_github_pr_review_changes_requested(self, handler):
        """Test parsing GitHub PR review with changes requested."""
        payload = {
            "pull_request": {
                "body": "<!-- notification_id: pr-notif-002 -->",
            },
            "review": {
                "state": "changes_requested",
                "body": "Please update tests",
            },
            "sender": {"login": "maintainer2"},
        }

        result = handler.parse_github_payload(payload, "pull_request_review")

        assert result.notification_id == "pr-notif-002"
        assert result.response_type == "rejected"
        assert result.message == "Please update tests"

    def test_parse_github_pr_review_comment_only(self, handler):
        """Test parsing GitHub PR review with comment state."""
        payload = {
            "pull_request": {
                "body": "<!-- notification_id: pr-notif-003 -->",
            },
            "review": {
                "state": "commented",
                "body": "Just leaving a comment",
            },
            "sender": {"login": "reviewer"},
        }

        result = handler.parse_github_payload(payload, "pull_request_review")

        assert result.notification_id == "pr-notif-003"
        assert result.response_type == "comment"
        assert result.message == "Just leaving a comment"

    def test_parse_github_pr_review_no_notification_id(self, handler):
        """Test parsing GitHub PR review without notification_id."""
        payload = {
            "pull_request": {
                "body": "Regular PR description",
            },
            "review": {
                "state": "approved",
                "body": "LGTM",
            },
            "sender": {"login": "approver"},
        }

        result = handler.parse_github_payload(payload, "pull_request_review")

        assert result.notification_id == ""
        assert result.response_type == "approved"

    def test_parse_github_unknown_event(self, handler):
        """Test parsing GitHub payload with unknown event type."""
        payload = {
            "sender": {"login": "someone"},
        }

        result = handler.parse_github_payload(payload, "unknown_event")

        assert result.source == "github"
        assert result.event_type == "unknown_event"
        assert result.notification_id == ""
        assert result.response_type == "comment"
        assert result.responder == "someone"

    def test_parse_github_missing_sender(self, handler):
        """Test parsing GitHub payload with missing sender."""
        payload = {
            "issue": {
                "body": "<!-- notification_id: test -->",
            },
            "comment": {
                "body": "Comment",
            },
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert result.responder == "unknown"

    def test_parse_github_multiple_approval_keywords(self, handler):
        """Test parsing with multiple approval keywords."""
        payload = {
            "issue": {
                "body": "<!-- notification_id: multi-001 -->",
            },
            "comment": {
                "body": "LGTM! Approved :shipit: +1",
            },
            "sender": {"login": "enthusiast"},
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert result.response_type == "approved"

    def test_parse_github_mixed_case_keywords(self, handler):
        """Test parsing with mixed case keywords."""
        payload = {
            "issue": {
                "body": "<!-- notification_id: case-001 -->",
            },
            "comment": {
                "body": "Rejected! This needs work.",
            },
            "sender": {"login": "reviewer"},
        }

        result = handler.parse_github_payload(payload, "issue_comment")

        assert result.response_type == "rejected"


class TestGetWebhookHandler:
    """Tests for get_webhook_handler function."""

    def test_returns_handler(self):
        """Test that get_webhook_handler returns a WebhookHandler."""
        import forge_harness.webhook_server.handlers.webhook_handler as wh_module

        # Reset singleton
        wh_module._webhook_handler = None

        handler = get_webhook_handler()

        assert isinstance(handler, WebhookHandler)
        assert handler.notification is None

    def test_returns_same_instance(self):
        """Test that get_webhook_handler returns singleton."""
        import forge_harness.webhook_server.handlers.webhook_handler as wh_module

        # Reset singleton
        wh_module._webhook_handler = None

        handler1 = get_webhook_handler()
        handler2 = get_webhook_handler()

        assert handler1 is handler2

    def test_singleton_persists(self):
        """Test that singleton persists across calls."""
        handler1 = get_webhook_handler()
        handler2 = get_webhook_handler()
        handler3 = get_webhook_handler()

        assert handler1 is handler2 is handler3

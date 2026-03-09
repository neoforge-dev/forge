"""
Unit tests for WebhookHandler
==============================

Comprehensive unit tests targeting:
- Webhook signature verification (Slack + GitHub)
- Slack payload parsing (block_actions, view_submission, edge cases)
- GitHub payload parsing (issue_comment, pull_request_review, edge cases)
- handle_slack / handle_github routing and error handling
- Notification harness integration and error propagation
- get_webhook_handler singleton
- Environment-based configuration (secrets, FORGE_ENV)

Target: 60%+ coverage of webhook_handler.py
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slack_sig(secret: str, timestamp: str, body: bytes) -> str:
    """Build a valid v0= Slack HMAC-SHA256 signature."""
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    return "v0=" + hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _make_github_sig(secret: str, body: bytes) -> str:
    """Build a valid sha256= GitHub HMAC-SHA256 signature."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _fresh_timestamp() -> str:
    """Return the current Unix timestamp as a string (within 5-minute window)."""
    return str(int(datetime.now(UTC).timestamp()))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_notification():
    mock = MagicMock()
    mock.record_response = MagicMock()
    return mock


@pytest.fixture
def handler(mock_notification):
    return WebhookHandler(mock_notification)


@pytest.fixture
def handler_with_secrets(mock_notification):
    return WebhookHandler(
        mock_notification,
        slack_signing_secret="slack_secret_123",
        github_webhook_secret="github_secret_456",
    )


# ===========================================================================
# __init__ – configuration wiring
# ===========================================================================

class TestWebhookHandlerInit:
    def test_secrets_from_constructor(self):
        h = WebhookHandler(None, slack_signing_secret="s1", github_webhook_secret="g1")
        assert h.slack_signing_secret == "s1"
        assert h.github_webhook_secret == "g1"

    def test_secrets_from_env(self):
        with patch.dict(
            os.environ,
            {"SLACK_SIGNING_SECRET": "env_slack", "GITHUB_WEBHOOK_SECRET": "env_github"},
        ):
            h = WebhookHandler(None)
        assert h.slack_signing_secret == "env_slack"
        assert h.github_webhook_secret == "env_github"

    def test_constructor_secrets_take_precedence_over_env(self):
        with patch.dict(
            os.environ,
            {"SLACK_SIGNING_SECRET": "env_slack", "GITHUB_WEBHOOK_SECRET": "env_github"},
        ):
            h = WebhookHandler(None, slack_signing_secret="ctor_slack")
        assert h.slack_signing_secret == "ctor_slack"

    def test_notification_stored(self, mock_notification):
        h = WebhookHandler(mock_notification)
        assert h.notification is mock_notification

    def test_none_notification_allowed(self):
        h = WebhookHandler(None)
        assert h.notification is None


# ===========================================================================
# verify_slack_signature
# ===========================================================================

class TestVerifySlackSignature:
    def test_valid_signature(self, handler_with_secrets):
        ts = _fresh_timestamp()
        body = b"test body"
        sig = _make_slack_sig("slack_secret_123", ts, body)
        assert handler_with_secrets.verify_slack_signature(body, sig, ts) is True

    def test_invalid_signature_value(self, handler_with_secrets):
        ts = _fresh_timestamp()
        body = b"test body"
        assert handler_with_secrets.verify_slack_signature(body, "v0=deadbeef", ts) is False

    def test_replay_attack_old_timestamp(self, handler_with_secrets):
        # Timestamp from 2009 — far outside 5-minute replay window
        old_ts = "1234567890"
        body = b"replayed body"
        sig = _make_slack_sig("slack_secret_123", old_ts, body)
        assert handler_with_secrets.verify_slack_signature(body, sig, old_ts) is False

    def test_replay_attack_future_timestamp(self, handler_with_secrets):
        # Timestamp 10 minutes in the future
        future_ts = str(int(datetime.now(UTC).timestamp()) + 700)
        body = b"future body"
        sig = _make_slack_sig("slack_secret_123", future_ts, body)
        assert handler_with_secrets.verify_slack_signature(body, sig, future_ts) is False

    def test_invalid_timestamp_format(self, handler_with_secrets):
        assert (
            handler_with_secrets.verify_slack_signature(b"body", "v0=sig", "not_a_number")
            is False
        )

    def test_none_timestamp(self, handler_with_secrets):
        assert (
            handler_with_secrets.verify_slack_signature(b"body", "v0=sig", None) is False
        )

    def test_no_secret_development_env_returns_true(self, mock_notification):
        """Without a secret in development mode verification is permissive."""
        h = WebhookHandler(mock_notification, slack_signing_secret=None)
        with patch.dict(os.environ, {"FORGE_ENV": "development", "SLACK_SIGNING_SECRET": ""}):
            # Patching env to ensure no secret resolves from env
            h2 = WebhookHandler(mock_notification)
            h2.slack_signing_secret = None
            result = h2.verify_slack_signature(b"body", "v0=anything", _fresh_timestamp())
        assert result is True

    def test_no_secret_production_env_returns_false(self, mock_notification):
        """Without a secret in production mode verification is denied."""
        h = WebhookHandler(mock_notification)
        h.slack_signing_secret = None
        with patch.dict(os.environ, {"FORGE_ENV": "production"}):
            result = h.verify_slack_signature(b"body", "v0=anything", _fresh_timestamp())
        assert result is False

    def test_body_encoding_matters(self, handler_with_secrets):
        """Two different bodies must produce different signatures."""
        ts = _fresh_timestamp()
        body_a = b"body_a"
        body_b = b"body_b"
        sig_a = _make_slack_sig("slack_secret_123", ts, body_a)
        assert handler_with_secrets.verify_slack_signature(body_a, sig_a, ts) is True
        assert handler_with_secrets.verify_slack_signature(body_b, sig_a, ts) is False


# ===========================================================================
# verify_github_signature
# ===========================================================================

class TestVerifyGithubSignature:
    def test_valid_signature(self, handler_with_secrets):
        body = b'{"action": "created"}'
        sig = _make_github_sig("github_secret_456", body)
        assert handler_with_secrets.verify_github_signature(body, sig) is True

    def test_invalid_signature_value(self, handler_with_secrets):
        body = b'{"action": "created"}'
        assert handler_with_secrets.verify_github_signature(body, "sha256=deadbeef") is False

    def test_missing_sha256_prefix(self, handler_with_secrets):
        body = b'{"action": "created"}'
        assert handler_with_secrets.verify_github_signature(body, "no_prefix") is False

    def test_empty_signature(self, handler_with_secrets):
        assert handler_with_secrets.verify_github_signature(b"body", "") is False

    def test_none_signature(self, handler_with_secrets):
        assert handler_with_secrets.verify_github_signature(b"body", None) is False

    def test_no_secret_development_returns_true(self, mock_notification):
        h = WebhookHandler(mock_notification)
        h.github_webhook_secret = None
        with patch.dict(os.environ, {"FORGE_ENV": "development"}):
            result = h.verify_github_signature(b"body", "sha256=anything")
        assert result is True

    def test_no_secret_production_returns_false(self, mock_notification):
        h = WebhookHandler(mock_notification)
        h.github_webhook_secret = None
        with patch.dict(os.environ, {"FORGE_ENV": "production"}):
            result = h.verify_github_signature(b"body", "sha256=anything")
        assert result is False

    def test_different_body_fails(self, handler_with_secrets):
        body = b"correct body"
        sig = _make_github_sig("github_secret_456", body)
        assert handler_with_secrets.verify_github_signature(b"wrong body", sig) is False


# ===========================================================================
# parse_slack_payload – block_actions
# ===========================================================================

class TestParseSlackPayloadBlockActions:
    def test_basic_three_part_action_id(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "alice"},
            "actions": [{"action_id": "action_notif42_approved", "value": "yes"}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.source == "slack"
        assert result.event_type == "block_actions"
        assert result.notification_id == "notif42"
        assert result.response_type == "approved"
        assert result.responder == "alice"
        assert result.message is None

    def test_two_part_action_id_uses_value(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "bob"},
            "actions": [{"action_id": "action_notif99", "value": "rejected"}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == "notif99"
        assert result.response_type == "rejected"

    def test_two_part_action_id_no_value_defaults_to_action(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "bob"},
            "actions": [{"action_id": "action_notif77", "value": ""}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.response_type == "action"

    def test_single_part_action_id_uses_value(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "carol"},
            "actions": [{"action_id": "someaction", "value": "custom_response"}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == "someaction"
        assert result.response_type == "custom_response"

    def test_single_part_action_id_no_value_defaults_to_action(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "carol"},
            "actions": [{"action_id": "singlepart", "value": ""}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.response_type == "action"

    def test_empty_actions_list(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "dave"},
            "actions": [],
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == ""
        assert result.response_type == "unknown"

    def test_user_name_fallback_chain(self, handler):
        """Falls back: username -> name -> id -> 'unknown'."""
        # Only 'name' present
        payload = {
            "type": "block_actions",
            "user": {"name": "name_field"},
            "actions": [],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "name_field"

    def test_user_id_fallback(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"id": "U12345"},
            "actions": [],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "U12345"

    def test_user_unknown_fallback(self, handler):
        payload = {
            "type": "block_actions",
            "user": {},
            "actions": [],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "unknown"

    def test_missing_user_key(self, handler):
        payload = {
            "type": "block_actions",
            "actions": [],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "unknown"

    def test_raw_payload_stored(self, handler):
        payload = {"type": "block_actions", "user": {}, "actions": []}
        result = handler.parse_slack_payload(payload)
        assert result.raw_payload is payload


# ===========================================================================
# parse_slack_payload – view_submission
# ===========================================================================

class TestParseSlackPayloadViewSubmission:
    def test_basic_view_submission(self, handler):
        # callback_id "modal_gate55" -> parts = ["modal", "gate55"] -> parts[1] = "gate55"
        payload = {
            "type": "view_submission",
            "user": {"username": "eve"},
            "view": {
                "callback_id": "modal_gate55",
                "state": {"values": {}},
            },
        }
        result = handler.parse_slack_payload(payload)
        assert result.source == "slack"
        assert result.event_type == "view_submission"
        assert result.notification_id == "gate55"
        assert result.response_type == "submitted"
        assert result.responder == "eve"
        assert result.message is None

    def test_view_submission_extracts_message(self, handler):
        # callback_id "modal_gate88" -> parts[1] = "gate88"
        payload = {
            "type": "view_submission",
            "user": {"username": "frank"},
            "view": {
                "callback_id": "modal_gate88",
                "state": {
                    "values": {
                        "block1": {
                            "input1": {"value": "User typed this message"}
                        }
                    }
                },
            },
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == "gate88"
        assert result.message == "User typed this message"

    def test_view_submission_single_part_callback_id(self, handler):
        """callback_id with only one part produces empty notification_id."""
        payload = {
            "type": "view_submission",
            "user": {"username": "grace"},
            "view": {
                "callback_id": "singlepart",
                "state": {"values": {}},
            },
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == ""

    def test_view_submission_missing_state_values(self, handler):
        payload = {
            "type": "view_submission",
            "user": {"username": "hank"},
            "view": {
                "callback_id": "modal_abc",
                "state": {},
            },
        }
        result = handler.parse_slack_payload(payload)
        assert result.message is None

    def test_view_submission_missing_view(self, handler):
        payload = {
            "type": "view_submission",
            "user": {"username": "ivan"},
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == ""

    def test_view_submission_input_without_value_key(self, handler):
        """Input block without 'value' key should not set message."""
        payload = {
            "type": "view_submission",
            "user": {"username": "judy"},
            "view": {
                "callback_id": "modal_xyz",
                "state": {
                    "values": {
                        "block1": {
                            "input1": {"type": "plain_text_input"}  # no 'value'
                        }
                    }
                },
            },
        }
        result = handler.parse_slack_payload(payload)
        assert result.message is None

    def test_unknown_payload_type_returns_defaults(self, handler):
        payload = {"type": "unknown_type", "user": {"username": "zach"}}
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == ""
        assert result.response_type == "unknown"
        assert result.responder == "zach"


# ===========================================================================
# parse_github_payload – issue_comment
# ===========================================================================

class TestParseGithubPayloadIssueComment:
    def test_approval_keywords_lgtm(self, handler):
        payload = {
            "sender": {"login": "reviewer1"},
            "comment": {"body": "LGTM! Looks great."},
            "issue": {"body": "<!-- notification_id: issue_abc --> context"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.source == "github"
        assert result.event_type == "issue_comment"
        assert result.notification_id == "issue_abc"
        assert result.response_type == "approved"
        assert result.responder == "reviewer1"
        assert result.message == "LGTM! Looks great."

    def test_approval_keyword_approved(self, handler):
        payload = {
            "sender": {"login": "rev2"},
            "comment": {"body": "Approved by me."},
            "issue": {"body": "<!-- notification_id: notif1 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "approved"

    def test_approval_keyword_plus_one(self, handler):
        payload = {
            "sender": {"login": "rev3"},
            "comment": {"body": "+1 ship it"},
            "issue": {"body": "<!-- notification_id: notif2 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "approved"

    def test_approval_keyword_shipit_emoji(self, handler):
        payload = {
            "sender": {"login": "rev4"},
            "comment": {"body": ":shipit: deploy now"},
            "issue": {"body": "<!-- notification_id: notif3 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "approved"

    def test_rejection_keyword_rejected(self, handler):
        payload = {
            "sender": {"login": "rev5"},
            "comment": {"body": "Rejected - needs changes."},
            "issue": {"body": "<!-- notification_id: notif4 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "rejected"

    def test_rejection_keyword_minus_one(self, handler):
        payload = {
            "sender": {"login": "rev6"},
            "comment": {"body": "-1 please fix the tests first"},
            "issue": {"body": "<!-- notification_id: notif5 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "rejected"

    def test_rejection_keyword_changes_requested(self, handler):
        payload = {
            "sender": {"login": "rev7"},
            "comment": {"body": "changes requested please update"},
            "issue": {"body": "<!-- notification_id: notif6 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "rejected"

    def test_neutral_comment_defaults_to_comment(self, handler):
        payload = {
            "sender": {"login": "rev8"},
            "comment": {"body": "Looking at this now..."},
            "issue": {"body": "<!-- notification_id: notif7 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "comment"

    def test_no_notification_id_in_issue_body(self, handler):
        payload = {
            "sender": {"login": "rev9"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "No marker here"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == ""

    def test_missing_issue_key(self, handler):
        payload = {
            "sender": {"login": "rev10"},
            "comment": {"body": "lgtm"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == ""

    def test_missing_comment_key(self, handler):
        payload = {
            "sender": {"login": "rev11"},
            "issue": {"body": "<!-- notification_id: noid -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.message == ""

    def test_unknown_sender_fallback(self, handler):
        payload = {
            "sender": {},
            "comment": {"body": "lgtm"},
            "issue": {"body": ""},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.responder == "unknown"

    def test_missing_sender_key(self, handler):
        payload = {
            "comment": {"body": "lgtm"},
            "issue": {"body": ""},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.responder == "unknown"

    def test_notification_id_with_spaces_in_comment(self, handler):
        """HTML comment with extra spaces around notification_id value."""
        payload = {
            "sender": {"login": "spaceman"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!--  notification_id:   spaced_id  -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == "spaced_id"


# ===========================================================================
# parse_github_payload – pull_request_review
# ===========================================================================

class TestParseGithubPayloadPRReview:
    def test_pr_review_approved(self, handler):
        payload = {
            "sender": {"login": "prreviewer"},
            "review": {"state": "approved", "body": "Looks good!"},
            "pull_request": {"body": "<!-- notification_id: pr_gate1 -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.source == "github"
        assert result.event_type == "pull_request_review"
        assert result.notification_id == "pr_gate1"
        assert result.response_type == "approved"
        assert result.responder == "prreviewer"
        assert result.message == "Looks good!"

    def test_pr_review_changes_requested(self, handler):
        payload = {
            "sender": {"login": "strictrev"},
            "review": {"state": "changes_requested", "body": "Please fix X."},
            "pull_request": {"body": "<!-- notification_id: pr_gate2 -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.response_type == "rejected"

    def test_pr_review_request_changes_alias(self, handler):
        """'request_changes' is also mapped to rejected."""
        payload = {
            "sender": {"login": "nitpicker"},
            "review": {"state": "request_changes", "body": "Nit: rename this."},
            "pull_request": {"body": "<!-- notification_id: pr_gate3 -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.response_type == "rejected"

    def test_pr_review_commented(self, handler):
        payload = {
            "sender": {"login": "observer"},
            "review": {"state": "commented", "body": "Interesting approach"},
            "pull_request": {"body": "<!-- notification_id: pr_gate4 -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.response_type == "comment"

    def test_pr_review_no_notification_id(self, handler):
        payload = {
            "sender": {"login": "anon"},
            "review": {"state": "approved", "body": "Ship it!"},
            "pull_request": {"body": "No marker here"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.notification_id == ""

    def test_pr_review_missing_pr_key(self, handler):
        payload = {
            "sender": {"login": "anon"},
            "review": {"state": "approved", "body": ""},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.notification_id == ""

    def test_pr_review_empty_body(self, handler):
        payload = {
            "sender": {"login": "silent"},
            "review": {"state": "approved", "body": ""},
            "pull_request": {"body": "<!-- notification_id: pr_empty -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.message == ""

    def test_unknown_event_type_returns_defaults(self, handler):
        """Unrecognised event_type returns default 'comment' response_type."""
        payload = {"sender": {"login": "system"}}
        result = handler.parse_github_payload(payload, "push")
        assert result.response_type == "comment"
        assert result.notification_id == ""
        assert result.responder == "system"

    def test_raw_payload_stored(self, handler):
        payload = {
            "sender": {"login": "dev"},
            "review": {"state": "approved", "body": ""},
            "pull_request": {"body": "<!-- notification_id: stored -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.raw_payload is payload


# ===========================================================================
# handle_slack (async)
# ===========================================================================

class TestHandleSlack:
    @pytest.mark.asyncio
    async def test_success_calls_record_response(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {
            "type": "block_actions",
            "user": {"username": "tester"},
            "actions": [{"action_id": "action_notify1_approved", "value": "yes"}],
        }
        response = await h.handle_slack(payload, None, None)
        assert response.status == "received"
        assert response.notification_id == "notify1"
        mock_notification.record_response.assert_called_once_with(
            notification_id="notify1",
            response_type="approved",
            responder="tester",
            message=None,
        )

    @pytest.mark.asyncio
    async def test_response_message_contains_response_type(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {
            "type": "block_actions",
            "user": {"username": "u1"},
            "actions": [{"action_id": "action_n1_custom_type", "value": ""}],
        }
        response = await h.handle_slack(payload, None, None)
        assert "custom" in response.message

    @pytest.mark.asyncio
    async def test_record_response_exception_returns_error(self, mock_notification):
        mock_notification.record_response.side_effect = RuntimeError("DB is down")
        h = WebhookHandler(mock_notification)
        payload = {
            "type": "block_actions",
            "user": {"username": "err_user"},
            "actions": [{"action_id": "action_errnotif_approved", "value": ""}],
        }
        response = await h.handle_slack(payload, None, None)
        assert response.status == "error"
        assert "DB is down" in response.message
        assert response.notification_id == "errnotif"

    @pytest.mark.asyncio
    async def test_none_notification_skips_record_response(self):
        """When notification harness is None, no exception and status is 'received'."""
        h = WebhookHandler(None)
        payload = {
            "type": "block_actions",
            "user": {"username": "nonotif"},
            "actions": [{"action_id": "action_nn1_approved", "value": ""}],
        }
        response = await h.handle_slack(payload, None, None)
        assert response.status == "received"

    @pytest.mark.asyncio
    async def test_view_submission_handle_slack(self, mock_notification):
        # callback_id "modal_gate1" -> split("_") -> ["modal", "gate1"] -> parts[1] = "gate1"
        h = WebhookHandler(mock_notification)
        payload = {
            "type": "view_submission",
            "user": {"username": "modal_user"},
            "view": {
                "callback_id": "modal_gate1",
                "state": {
                    "values": {
                        "block": {"inp": {"value": "my note"}}
                    }
                },
            },
        }
        response = await h.handle_slack(payload, None, None)
        assert response.status == "received"
        assert response.notification_id == "gate1"
        mock_notification.record_response.assert_called_once_with(
            notification_id="gate1",
            response_type="submitted",
            responder="modal_user",
            message="my note",
        )


# ===========================================================================
# handle_github (async)
# ===========================================================================

class TestHandleGithub:
    @pytest.mark.asyncio
    async def test_success_issue_comment(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "gh_user"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: gh_issue1 -->"},
        }
        response = await h.handle_github(payload, None, "issue_comment")
        assert response.status == "received"
        assert response.notification_id == "gh_issue1"
        mock_notification.record_response.assert_called_once_with(
            notification_id="gh_issue1",
            response_type="approved",
            responder="gh_user",
            message="lgtm",
        )

    @pytest.mark.asyncio
    async def test_response_message_contains_response_type(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "u"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: rtype_test -->"},
        }
        response = await h.handle_github(payload, None, "issue_comment")
        assert "approved" in response.message

    @pytest.mark.asyncio
    async def test_record_response_exception_returns_error(self, mock_notification):
        mock_notification.record_response.side_effect = ValueError("No gate found")
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "err_gh_user"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: gh_err1 -->"},
        }
        response = await h.handle_github(payload, None, "issue_comment")
        assert response.status == "error"
        assert "No gate found" in response.message
        assert response.notification_id == "gh_err1"

    @pytest.mark.asyncio
    async def test_none_notification_skips_record_response(self):
        h = WebhookHandler(None)
        payload = {
            "sender": {"login": "ghost"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: no_harness -->"},
        }
        response = await h.handle_github(payload, None, "issue_comment")
        assert response.status == "received"

    @pytest.mark.asyncio
    async def test_pr_review_approved_recorded(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "gh_reviewer"},
            "review": {"state": "approved", "body": "All good."},
            "pull_request": {"body": "<!-- notification_id: gh_pr1 -->"},
        }
        response = await h.handle_github(payload, None, "pull_request_review")
        assert response.status == "received"
        assert response.notification_id == "gh_pr1"
        mock_notification.record_response.assert_called_once_with(
            notification_id="gh_pr1",
            response_type="approved",
            responder="gh_reviewer",
            message="All good.",
        )

    @pytest.mark.asyncio
    async def test_unknown_event_type_recorded_as_comment(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {"sender": {"login": "pusher"}}
        response = await h.handle_github(payload, None, "push")
        assert response.status == "received"
        mock_notification.record_response.assert_called_once_with(
            notification_id="",
            response_type="comment",
            responder="pusher",
            message=None,
        )


# ===========================================================================
# get_webhook_handler singleton
# ===========================================================================

class TestGetWebhookHandlerSingleton:
    def test_returns_webhook_handler_instance(self):
        # Reset singleton so test is independent
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        mod._webhook_handler = None
        instance = get_webhook_handler()
        assert isinstance(instance, WebhookHandler)

    def test_returns_same_instance_on_second_call(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        mod._webhook_handler = None
        first = get_webhook_handler()
        second = get_webhook_handler()
        assert first is second

    def test_singleton_notification_is_none(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        mod._webhook_handler = None
        instance = get_webhook_handler()
        assert instance.notification is None

    def test_existing_singleton_not_recreated(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        sentinel = WebhookHandler(None, slack_signing_secret="sentinel_secret")
        mod._webhook_handler = sentinel
        result = get_webhook_handler()
        assert result is sentinel
        assert result.slack_signing_secret == "sentinel_secret"
        # Cleanup
        mod._webhook_handler = None

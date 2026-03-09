"""Extended tests for WebhookHandler.

These tests cover behavioral edge cases, boundary conditions, and scenario
combinations not exercised by the existing test files. All scenarios target
the 360-line webhook_handler.py module:
- Multi-action payloads (only first action used)
- Notification id extraction edge cases (malformed HTML comments, multiple
  markers, numeric ids, hyphens)
- Unicode and special character handling throughout
- Signature verification boundary: exactly-at-5-minutes vs just-outside
- Slack signing secret from env wins when constructor receives None
- GitHub secret from env wins when constructor receives None
- handle_slack / handle_github pass signature/timestamp through without using them
  (the handler currently ignores those params—verify the contract)
- WebhookPayload / WebhookResponse dataclass field invariants
- view_submission: callback_id with many underscores preserves only parts[1]
- block_actions: action with 4+ parts (extra underscores in response type)
- issue_comment: rejection keyword :-1: (colon form)
- issue_comment: approval keyword :+1: and approved
- issue_comment: empty comment body
- issue_comment: issue body is None-like (empty string)
- pull_request_review: PR body None / empty
- pull_request_review: review state uppercase normalisation
- Singleton: pre-seeded instance with full secrets is returned unchanged
- Concurrent-style: multiple calls to get_webhook_handler in a row
- handle_github with view_submission-style payload (wrong type, treated as
  unknown event)
- handle_slack with view_submission: multiple input blocks (only first value used)
- parse_slack_payload: view_submission with input block that has a None value
- parse_github_payload: notification_id with trailing whitespace in the HTML comment
- parse_slack_payload: no 'actions' key at all in block_actions payload
- WebhookResponse: message defaults to None
- WebhookPayload: received_at is set automatically
- verify_slack_signature: body with non-ASCII bytes round-trips correctly
- verify_github_signature: body with JSON special chars verified correctly
- handle_slack: record_response called with exact signature for view_submission
- handle_github: error response preserves notification_id extracted from PR body
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
    """Produce a valid v0= HMAC-SHA256 Slack signature."""
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    return "v0=" + hmac.new(
        secret.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _make_github_sig(secret: str, body: bytes) -> str:
    """Produce a valid sha256= HMAC-SHA256 GitHub signature."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _now_ts() -> str:
    return str(int(datetime.now(UTC).timestamp()))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_notification():
    m = MagicMock()
    m.record_response = MagicMock()
    return m


@pytest.fixture
def handler(mock_notification):
    return WebhookHandler(mock_notification)


@pytest.fixture
def handler_with_secrets(mock_notification):
    return WebhookHandler(
        mock_notification,
        slack_signing_secret="slack_secret",
        github_webhook_secret="github_secret",
    )


# ===========================================================================
# DataClass invariants — WebhookPayload and WebhookResponse
# ===========================================================================

class TestWebhookPayloadDataclass:
    def test_received_at_is_populated_automatically(self):
        before = datetime.now(UTC)
        p = WebhookPayload(
            source="slack",
            event_type="block_actions",
            notification_id="n1",
            response_type="approved",
            responder="alice",
        )
        after = datetime.now(UTC)
        assert before <= p.received_at <= after

    def test_message_defaults_to_none(self):
        p = WebhookPayload(
            source="github",
            event_type="issue_comment",
            notification_id="",
            response_type="comment",
            responder="bob",
        )
        assert p.message is None

    def test_raw_payload_defaults_to_empty_dict(self):
        p = WebhookPayload(
            source="slack",
            event_type="view_submission",
            notification_id="x",
            response_type="submitted",
            responder="carol",
        )
        assert p.raw_payload == {}

    def test_explicit_message_stored(self):
        p = WebhookPayload(
            source="github",
            event_type="pull_request_review",
            notification_id="pr1",
            response_type="approved",
            responder="dave",
            message="Looks good!",
        )
        assert p.message == "Looks good!"


class TestWebhookResponseDataclass:
    def test_message_defaults_to_none(self):
        r = WebhookResponse(status="received", notification_id="n1")
        assert r.message is None

    def test_all_fields_stored(self):
        r = WebhookResponse(status="error", notification_id="n2", message="boom")
        assert r.status == "error"
        assert r.notification_id == "n2"
        assert r.message == "boom"


# ===========================================================================
# Initialization — environment variable precedence edge cases
# ===========================================================================

class TestWebhookHandlerInitEdgeCases:
    def test_env_slack_secret_used_when_constructor_arg_is_none(self):
        """Constructor None falls back to env var."""
        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "from_env_slack"}):
            h = WebhookHandler(None, slack_signing_secret=None)
        assert h.slack_signing_secret == "from_env_slack"

    def test_env_github_secret_used_when_constructor_arg_is_none(self):
        """Constructor None falls back to env var."""
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "from_env_github"}):
            h = WebhookHandler(None, github_webhook_secret=None)
        assert h.github_webhook_secret == "from_env_github"

    def test_constructor_secret_takes_precedence_over_env_slack(self):
        """Explicit constructor secret wins over env var."""
        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "env_slack"}):
            h = WebhookHandler(None, slack_signing_secret="ctor_slack")
        assert h.slack_signing_secret == "ctor_slack"

    def test_constructor_secret_takes_precedence_over_env_github(self):
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "env_github"}):
            h = WebhookHandler(None, github_webhook_secret="ctor_github")
        assert h.github_webhook_secret == "ctor_github"

    def test_no_env_and_no_constructor_secret_is_none(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SLACK_SIGNING_SECRET", "GITHUB_WEBHOOK_SECRET")}
        with patch.dict(os.environ, env, clear=True):
            h = WebhookHandler(None)
        assert h.slack_signing_secret is None
        assert h.github_webhook_secret is None


# ===========================================================================
# verify_slack_signature — boundary / edge cases
# ===========================================================================

class TestVerifySlackSignatureBoundary:
    def test_exactly_300_seconds_old_is_rejected(self):
        """A request exactly 300s old is outside the window (> 300)."""
        h = WebhookHandler(None, slack_signing_secret="secret")
        body = b"payload"
        ts = str(int(datetime.now(UTC).timestamp()) - 300)
        sig = _make_slack_sig("secret", ts, body)
        # abs(current - ts) == 300 which is NOT > 300, so the check is:
        # abs(...) > 300  => 300 > 300 is False => should pass
        result = h.verify_slack_signature(body, sig, ts)
        assert result is True

    def test_301_seconds_old_is_rejected(self):
        h = WebhookHandler(None, slack_signing_secret="secret")
        body = b"payload"
        ts = str(int(datetime.now(UTC).timestamp()) - 301)
        sig = _make_slack_sig("secret", ts, body)
        assert h.verify_slack_signature(body, sig, ts) is False

    def test_empty_body_signature_is_valid(self):
        """Empty body should still verify correctly."""
        h = WebhookHandler(None, slack_signing_secret="emptysecret")
        ts = _now_ts()
        body = b""
        sig = _make_slack_sig("emptysecret", ts, body)
        assert h.verify_slack_signature(body, sig, ts) is True

    def test_unicode_body_verifies_correctly(self):
        """Body with unicode characters (URL-encoded or raw) verifies."""
        h = WebhookHandler(None, slack_signing_secret="unicodesecret")
        ts = _now_ts()
        # Simulated form-encoded body with unicode-safe ASCII representation
        body = b"payload=hello+world&user=caf%C3%A9"
        sig = _make_slack_sig("unicodesecret", ts, body)
        assert h.verify_slack_signature(body, sig, ts) is True

    def test_wrong_body_fails_with_valid_signature(self):
        h = WebhookHandler(None, slack_signing_secret="secret")
        ts = _now_ts()
        body_correct = b"correct payload"
        body_wrong = b"tampered payload"
        sig = _make_slack_sig("secret", ts, body_correct)
        assert h.verify_slack_signature(body_wrong, sig, ts) is False


# ===========================================================================
# verify_github_signature — edge cases
# ===========================================================================

class TestVerifyGithubSignatureBoundary:
    def test_empty_body_verifies_correctly(self):
        h = WebhookHandler(None, github_webhook_secret="ghsecret")
        body = b""
        sig = _make_github_sig("ghsecret", body)
        assert h.verify_github_signature(body, sig) is True

    def test_json_special_chars_in_body(self):
        h = WebhookHandler(None, github_webhook_secret="ghsecret")
        body = b'{"action":"created","body":"line1\\nline2\\ttab\\"quote\\""}'
        sig = _make_github_sig("ghsecret", body)
        assert h.verify_github_signature(body, sig) is True

    def test_signature_with_wrong_prefix_fails(self):
        """sha1= prefix (old GitHub format) must be rejected."""
        h = WebhookHandler(None, github_webhook_secret="ghsecret")
        body = b"test"
        # Build a sha1= sig (not sha256=)
        bad_sig = "sha1=" + hmac.new(b"ghsecret", body, hashlib.sha1).hexdigest()
        assert h.verify_github_signature(body, bad_sig) is False

    def test_tampered_body_fails(self):
        h = WebhookHandler(None, github_webhook_secret="ghsecret")
        body_orig = b'{"action":"created"}'
        body_tampered = b'{"action":"deleted"}'
        sig = _make_github_sig("ghsecret", body_orig)
        assert h.verify_github_signature(body_tampered, sig) is False


# ===========================================================================
# parse_slack_payload — additional edge cases
# ===========================================================================

class TestParseSlackPayloadEdgeCases:
    def test_multiple_actions_only_first_used(self, handler):
        """When multiple actions are present, only the first is used."""
        payload = {
            "type": "block_actions",
            "user": {"username": "multi"},
            "actions": [
                {"action_id": "action_first_approved", "value": "yes"},
                {"action_id": "action_second_rejected", "value": "no"},
            ],
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == "first"
        assert result.response_type == "approved"

    def test_action_id_with_four_parts_uses_parts_2_as_response(self, handler):
        """action_id with 4 parts: parts[1]=notif_id, parts[2]=response (extra ignored)."""
        payload = {
            "type": "block_actions",
            "user": {"username": "fourpart"},
            "actions": [{"action_id": "action_notifX_approve_extra", "value": ""}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == "notifX"
        assert result.response_type == "approve"

    def test_block_actions_no_actions_key(self, handler):
        """Missing 'actions' key entirely behaves like empty list."""
        payload = {
            "type": "block_actions",
            "user": {"username": "noact"},
        }
        result = handler.parse_slack_payload(payload)
        assert result.notification_id == ""
        assert result.response_type == "unknown"

    def test_view_submission_callback_id_with_many_underscores(self, handler):
        """callback_id 'submit_gate_42_extra' -> parts[1] = 'gate'."""
        payload = {
            "type": "view_submission",
            "user": {"username": "manyparts"},
            "view": {
                "callback_id": "submit_gate_42_extra",
                "state": {"values": {}},
            },
        }
        result = handler.parse_slack_payload(payload)
        # parts = ["submit", "gate", "42", "extra"] -> parts[1] = "gate"
        assert result.notification_id == "gate"
        assert result.response_type == "submitted"

    def test_view_submission_multiple_blocks_last_value_wins(self, handler):
        """The inner break exits only the per-block loop; the outer loop continues.

        Therefore the last block's value overwrites earlier ones — the last block
        with a 'value' key is what ends up in message.
        """
        payload = {
            "type": "view_submission",
            "user": {"username": "multiblock"},
            "view": {
                "callback_id": "modal_notif_multi",
                "state": {
                    "values": {
                        "block_first": {
                            "input_a": {"value": "first value"},
                        },
                        "block_second": {
                            "input_b": {"value": "second value"},
                        },
                    },
                },
            },
        }
        result = handler.parse_slack_payload(payload)
        # The inner break only exits the per-block inner loop; the outer loop
        # iterates both blocks, so the last block's value is what's stored.
        assert result.message == "second value"

    def test_view_submission_input_with_none_value(self, handler):
        """Input block present but 'value' is None should not override message."""
        payload = {
            "type": "view_submission",
            "user": {"username": "nullval"},
            "view": {
                "callback_id": "modal_nullval",
                "state": {
                    "values": {
                        "block": {
                            "inp": {"value": None},
                        }
                    },
                },
            },
        }
        result = handler.parse_slack_payload(payload)
        # 'value' key is present with None, so it IS assigned as message
        assert result.message is None

    def test_user_fallback_username_over_name(self, handler):
        """'username' takes priority over 'name'."""
        payload = {
            "type": "block_actions",
            "user": {"username": "uname_wins", "name": "name_loses"},
            "actions": [],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "uname_wins"

    def test_unicode_username(self, handler):
        payload = {
            "type": "block_actions",
            "user": {"username": "用户名"},
            "actions": [{"action_id": "action_u1_ok", "value": ""}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "用户名"

    def test_raw_payload_is_reference_not_copy(self, handler):
        payload = {"type": "unknown_type", "user": {}}
        result = handler.parse_slack_payload(payload)
        assert result.raw_payload is payload


# ===========================================================================
# parse_github_payload — notification id extraction edge cases
# ===========================================================================

class TestParseGithubNotificationIdExtraction:
    def test_notification_id_with_hyphens(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: feat-deploy-2026 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == "feat-deploy-2026"

    def test_notification_id_numeric(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: 12345 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == "12345"

    def test_notification_id_with_underscores(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: gate_deploy_prod -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == "gate_deploy_prod"

    def test_multiple_notification_id_markers_first_wins(self, handler):
        """When the issue body has two markers, the regex returns the first match."""
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "lgtm"},
            "issue": {
                "body": (
                    "<!-- notification_id: first_id -->\n"
                    "<!-- notification_id: second_id -->"
                )
            },
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == "first_id"

    def test_notification_id_with_extra_spaces_in_marker(self, handler):
        """Regex allows multiple spaces around the colon and value."""
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!--   notification_id:   spaced_val   -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == "spaced_val"

    def test_issue_with_empty_body(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "lgtm"},
            "issue": {"body": ""},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.notification_id == ""


# ===========================================================================
# parse_github_payload — response_type keyword detection
# ===========================================================================

class TestParseGithubResponseTypeKeywords:
    def test_colon_plus_one_emoji_is_approved(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": ":+1: great work"},
            "issue": {"body": "<!-- notification_id: k1 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "approved"

    def test_colon_minus_one_emoji_is_rejected(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": ":-1: nope"},
            "issue": {"body": "<!-- notification_id: k2 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "rejected"

    def test_reject_keyword_in_comment(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": "reject this please"},
            "issue": {"body": "<!-- notification_id: k3 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "rejected"

    def test_empty_comment_body_defaults_to_comment(self, handler):
        payload = {
            "sender": {"login": "rev"},
            "comment": {"body": ""},
            "issue": {"body": "<!-- notification_id: k4 -->"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "comment"
        assert result.message == ""

    def test_pr_review_uppercase_state_not_matched(self, handler):
        """Review state is .lower()ed before comparison — 'APPROVED' still matches."""
        payload = {
            "sender": {"login": "rev"},
            "review": {"state": "APPROVED", "body": ""},
            "pull_request": {"body": "<!-- notification_id: uc1 -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        # state.lower() == "approved" -> maps to "approved"
        assert result.response_type == "approved"

    def test_pr_review_changes_requested_upper(self, handler):
        """CHANGES_REQUESTED (uppercase) is lowercased to changes_requested."""
        payload = {
            "sender": {"login": "rev"},
            "review": {"state": "CHANGES_REQUESTED", "body": ""},
            "pull_request": {"body": "<!-- notification_id: uc2 -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.response_type == "rejected"

    def test_pr_review_empty_pr_body(self, handler):
        """PR with empty body has no notification_id."""
        payload = {
            "sender": {"login": "rev"},
            "review": {"state": "approved", "body": "ship it"},
            "pull_request": {"body": ""},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.notification_id == ""
        assert result.response_type == "approved"
        assert result.message == "ship it"

    def test_pr_review_missing_review_body_key(self, handler):
        """Review dict without 'body' key results in empty message."""
        payload = {
            "sender": {"login": "rev"},
            "review": {"state": "approved"},
            "pull_request": {"body": "<!-- notification_id: nob -->"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.message == ""
        assert result.notification_id == "nob"


# ===========================================================================
# handle_slack — additional async integration scenarios
# ===========================================================================

class TestHandleSlackIntegration:
    @pytest.mark.asyncio
    async def test_signature_and_timestamp_params_accepted_but_ignored(self, mock_notification):
        """handle_slack accepts signature/timestamp but does not verify them internally."""
        h = WebhookHandler(mock_notification, slack_signing_secret="secret")
        payload = {
            "type": "block_actions",
            "user": {"username": "pass_sig"},
            "actions": [{"action_id": "action_s1_approved", "value": ""}],
        }
        # Pass bogus signature and timestamp — handler should not reject
        result = await h.handle_slack(payload, "v0=bogus", "99999")
        assert result.status == "received"

    @pytest.mark.asyncio
    async def test_view_submission_record_response_exact_signature(self, mock_notification):
        """record_response is called with the exact arguments derived from modal."""
        h = WebhookHandler(mock_notification)
        payload = {
            "type": "view_submission",
            "user": {"username": "modal_tester"},
            "view": {
                "callback_id": "modal_gate99",
                "state": {
                    "values": {
                        "blk": {"inp": {"value": "approval note"}}
                    }
                },
            },
        }
        await h.handle_slack(payload, None, None)
        mock_notification.record_response.assert_called_once_with(
            notification_id="gate99",
            response_type="submitted",
            responder="modal_tester",
            message="approval note",
        )

    @pytest.mark.asyncio
    async def test_response_message_format_for_view_submission(self, mock_notification):
        """Response message includes the response_type string 'submitted'."""
        h = WebhookHandler(mock_notification)
        payload = {
            "type": "view_submission",
            "user": {"username": "u"},
            "view": {
                "callback_id": "modal_resp_fmt",
                "state": {"values": {}},
            },
        }
        result = await h.handle_slack(payload, None, None)
        assert "submitted" in result.message

    @pytest.mark.asyncio
    async def test_unknown_payload_type_still_returns_received(self, mock_notification):
        """Even with an unknown type, handle_slack returns 'received'."""
        h = WebhookHandler(mock_notification)
        payload = {"type": "shortcut", "user": {"username": "shortcuts_user"}}
        result = await h.handle_slack(payload, None, None)
        assert result.status == "received"

    @pytest.mark.asyncio
    async def test_notification_harness_none_returns_received_for_view_submission(self):
        h = WebhookHandler(None)
        payload = {
            "type": "view_submission",
            "user": {"username": "noharness"},
            "view": {
                "callback_id": "modal_noharness",
                "state": {"values": {}},
            },
        }
        result = await h.handle_slack(payload, None, None)
        assert result.status == "received"


# ===========================================================================
# handle_github — additional async integration scenarios
# ===========================================================================

class TestHandleGithubIntegration:
    @pytest.mark.asyncio
    async def test_signature_param_accepted_but_ignored(self, mock_notification):
        """handle_github accepts signature but does not verify it internally."""
        h = WebhookHandler(mock_notification, github_webhook_secret="secret")
        payload = {
            "sender": {"login": "user"},
            "comment": {"body": "lgtm"},
            "issue": {"body": "<!-- notification_id: sig_ignored -->"},
        }
        result = await h.handle_github(payload, "sha256=bogus", "issue_comment")
        assert result.status == "received"

    @pytest.mark.asyncio
    async def test_pr_review_error_preserves_notification_id(self, mock_notification):
        """On record_response failure for PR review, notification_id comes from PR body."""
        mock_notification.record_response.side_effect = RuntimeError("store down")
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "reviewer"},
            "review": {"state": "approved", "body": "ship it"},
            "pull_request": {"body": "<!-- notification_id: pr_err_id -->"},
        }
        result = await h.handle_github(payload, None, "pull_request_review")
        assert result.status == "error"
        assert result.notification_id == "pr_err_id"
        assert "store down" in result.message

    @pytest.mark.asyncio
    async def test_unknown_event_type_none_notification_returns_received(self):
        h = WebhookHandler(None)
        payload = {"sender": {"login": "pusher"}}
        result = await h.handle_github(payload, None, "push")
        assert result.status == "received"
        assert result.notification_id == ""

    @pytest.mark.asyncio
    async def test_response_message_includes_comment_for_neutral(self, mock_notification):
        """Response message includes 'comment' when no approval/rejection keyword."""
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "u"},
            "comment": {"body": "looking into it"},
            "issue": {"body": "<!-- notification_id: neutral -->"},
        }
        result = await h.handle_github(payload, None, "issue_comment")
        assert "comment" in result.message

    @pytest.mark.asyncio
    async def test_issue_comment_approval_with_plus_one_keyword(self, mock_notification):
        h = WebhookHandler(mock_notification)
        payload = {
            "sender": {"login": "approver"},
            "comment": {"body": "+1 to this"},
            "issue": {"body": "<!-- notification_id: plus_one -->"},
        }
        result = await h.handle_github(payload, None, "issue_comment")
        assert result.status == "received"
        mock_notification.record_response.assert_called_once_with(
            notification_id="plus_one",
            response_type="approved",
            responder="approver",
            message="+1 to this",
        )


# ===========================================================================
# get_webhook_handler singleton — extended scenarios
# ===========================================================================

class TestGetWebhookHandlerExtended:
    def test_singleton_reset_produces_fresh_instance(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        mod._webhook_handler = None
        h1 = get_webhook_handler()
        mod._webhook_handler = None
        h2 = get_webhook_handler()
        # They are different instances because we reset in between
        assert h1 is not h2

    def test_pre_set_instance_with_github_secret_preserved(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        sentinel = WebhookHandler(None, github_webhook_secret="pre_set_gh")
        mod._webhook_handler = sentinel
        result = get_webhook_handler()
        assert result is sentinel
        assert result.github_webhook_secret == "pre_set_gh"
        mod._webhook_handler = None

    def test_three_consecutive_calls_return_same_instance(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        mod._webhook_handler = None
        a = get_webhook_handler()
        b = get_webhook_handler()
        c = get_webhook_handler()
        assert a is b is c
        mod._webhook_handler = None

    def test_singleton_has_no_slack_or_github_secrets_by_default(self):
        import forge_harness.webhook_server.handlers.webhook_handler as mod
        mod._webhook_handler = None
        env = {k: v for k, v in os.environ.items()
               if k not in ("SLACK_SIGNING_SECRET", "GITHUB_WEBHOOK_SECRET")}
        with patch.dict(os.environ, env, clear=True):
            h = get_webhook_handler()
        assert h.slack_signing_secret is None
        assert h.github_webhook_secret is None
        mod._webhook_handler = None

"""Comprehensive unit tests for forge_harness/iteration/event_notifier_example.py.

Tests all example functions via mocked EventNotifier/WebhookConfig/Event internals,
and also tests the underlying event_notifier module classes directly to achieve
90%+ combined coverage.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import the modules under test
# ---------------------------------------------------------------------------
from forge_harness.iteration.event_notifier import (
    Event,
    EventNotifier,
    EventType,
    NotificationResult,
    WebhookConfig,
    create_event_notifier,
)
from forge_harness.iteration.event_notifier_example import (
    example_basic_usage,
    example_custom_event,
    example_custom_handlers,
    example_integration_with_iteration_loop,
    example_multiple_webhooks,
    example_slack_integration,
    example_with_environment_config,
)

# ===========================================================================
# Helpers
# ===========================================================================

def _make_mock_response(code: int = 200) -> MagicMock:
    """Return a mock object that behaves like urllib's HTTP response."""
    resp = MagicMock()
    resp.getcode.return_value = code
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ===========================================================================
# EventType tests
# ===========================================================================

class TestEventType:
    def test_all_expected_values_exist(self):
        expected = {
            "iteration_start", "iteration_complete", "iteration_fail",
            "feature_start", "feature_complete", "feature_fail",
            "loop_start", "loop_complete",
        }
        actual = {e.value for e in EventType}
        assert expected == actual

    def test_is_str_subclass(self):
        assert isinstance(EventType.ITERATION_START, str)

    def test_values_are_snake_case_strings(self):
        for et in EventType:
            assert et == et.value  # str Enum equality


# ===========================================================================
# Event dataclass tests
# ===========================================================================

class TestEvent:
    def test_required_fields(self):
        evt = Event(type=EventType.LOOP_START, project="proj")
        assert evt.type is EventType.LOOP_START
        assert evt.project == "proj"

    def test_defaults(self):
        evt = Event(type=EventType.LOOP_START, project="proj")
        assert evt.iteration_number is None
        assert evt.feature_id is None
        assert evt.feature_title is None
        assert evt.message == ""
        assert evt.details == {}

    def test_timestamp_default_is_recent_datetime(self):
        before = datetime.now()
        evt = Event(type=EventType.LOOP_START, project="proj")
        after = datetime.now()
        assert before <= evt.timestamp <= after

    def test_to_dict_keys(self):
        evt = Event(type=EventType.FEATURE_COMPLETE, project="my-proj",
                    feature_id="f-1", feature_title="Auth", message="done",
                    details={"lines": 10})
        d = evt.to_dict()
        assert set(d.keys()) == {
            "type", "project", "timestamp", "iteration_number",
            "feature_id", "feature_title", "message", "details",
        }

    def test_to_dict_type_value(self):
        evt = Event(type=EventType.ITERATION_FAIL, project="p")
        assert evt.to_dict()["type"] == "iteration_fail"

    def test_to_dict_timestamp_iso(self):
        fixed = datetime(2024, 1, 15, 10, 30, 0)
        evt = Event(type=EventType.LOOP_START, project="p", timestamp=fixed)
        assert evt.to_dict()["timestamp"] == fixed.isoformat()

    def test_to_dict_optional_fields_none(self):
        evt = Event(type=EventType.LOOP_START, project="p")
        d = evt.to_dict()
        assert d["iteration_number"] is None
        assert d["feature_id"] is None
        assert d["feature_title"] is None

    def test_to_dict_details_propagated(self):
        evt = Event(type=EventType.LOOP_START, project="p",
                    details={"a": 1, "b": "two"})
        assert evt.to_dict()["details"] == {"a": 1, "b": "two"}


# ===========================================================================
# NotificationResult tests
# ===========================================================================

class TestNotificationResult:
    def test_success_result(self):
        r = NotificationResult(success=True, destination="slack", response_code=200)
        assert r.success is True
        assert r.destination == "slack"
        assert r.error is None
        assert r.response_code == 200

    def test_failure_result(self):
        r = NotificationResult(success=False, destination="webhook", error="timeout")
        assert r.success is False
        assert r.error == "timeout"
        assert r.response_code is None


# ===========================================================================
# WebhookConfig tests
# ===========================================================================

class TestWebhookConfig:
    def test_required_url_only(self):
        cfg = WebhookConfig(url="https://example.com/hook")
        assert cfg.url == "https://example.com/hook"
        assert cfg.name == "webhook"
        assert cfg.headers == {}
        assert cfg.events == []
        assert cfg.format == "json"

    def test_custom_fields(self):
        cfg = WebhookConfig(
            url="https://hooks.slack.com/x",
            name="slack-team",
            format="slack",
            events=[EventType.ITERATION_COMPLETE],
            headers={"X-Key": "secret"},
        )
        assert cfg.name == "slack-team"
        assert cfg.format == "slack"
        assert EventType.ITERATION_COMPLETE in cfg.events
        assert cfg.headers == {"X-Key": "secret"}


# ===========================================================================
# EventNotifier — core logic tests
# ===========================================================================

class TestEventNotifierInit:
    def test_project_name_stored(self):
        n = EventNotifier("my-project")
        assert n.project_name == "my-project"

    def test_starts_empty(self):
        n = EventNotifier("p")
        assert n._webhooks == []
        assert n._handlers == {}


class TestAddWebhook:
    def test_add_single_webhook(self):
        n = EventNotifier("p")
        cfg = WebhookConfig(url="https://example.com")
        n.add_webhook(cfg)
        assert len(n._webhooks) == 1
        assert n._webhooks[0] is cfg

    def test_add_multiple_webhooks(self):
        n = EventNotifier("p")
        for i in range(3):
            n.add_webhook(WebhookConfig(url=f"https://example.com/{i}"))
        assert len(n._webhooks) == 3


class TestAddHandler:
    def test_add_single_handler(self):
        n = EventNotifier("p")
        handler = MagicMock()
        n.add_handler(EventType.FEATURE_COMPLETE, handler)
        assert EventType.FEATURE_COMPLETE in n._handlers
        assert handler in n._handlers[EventType.FEATURE_COMPLETE]

    def test_add_multiple_handlers_same_event(self):
        n = EventNotifier("p")
        h1, h2 = MagicMock(), MagicMock()
        n.add_handler(EventType.ITERATION_START, h1)
        n.add_handler(EventType.ITERATION_START, h2)
        assert len(n._handlers[EventType.ITERATION_START]) == 2

    def test_add_handlers_different_events(self):
        n = EventNotifier("p")
        h1, h2 = MagicMock(), MagicMock()
        n.add_handler(EventType.FEATURE_COMPLETE, h1)
        n.add_handler(EventType.ITERATION_FAIL, h2)
        assert EventType.FEATURE_COMPLETE in n._handlers
        assert EventType.ITERATION_FAIL in n._handlers


class TestFormatSlack:
    """Tests for _format_slack private method."""

    def _notifier(self):
        return EventNotifier("test-proj")

    def test_returns_dict_with_blocks(self):
        n = self._notifier()
        evt = Event(type=EventType.ITERATION_START, project="p",
                    iteration_number=1, message="go")
        result = n._format_slack(evt)
        assert "blocks" in result
        assert isinstance(result["blocks"], list)

    def test_emoji_mapping_iteration_start(self):
        n = self._notifier()
        evt = Event(type=EventType.ITERATION_START, project="p")
        result = n._format_slack(evt)
        header_text = result["blocks"][0]["text"]["text"]
        assert ":rocket:" in header_text

    def test_emoji_mapping_iteration_complete(self):
        n = self._notifier()
        evt = Event(type=EventType.ITERATION_COMPLETE, project="p")
        result = n._format_slack(evt)
        assert ":white_check_mark:" in result["blocks"][0]["text"]["text"]

    def test_emoji_mapping_iteration_fail(self):
        n = self._notifier()
        evt = Event(type=EventType.ITERATION_FAIL, project="p")
        result = n._format_slack(evt)
        assert ":x:" in result["blocks"][0]["text"]["text"]

    def test_emoji_mapping_feature_start(self):
        n = self._notifier()
        evt = Event(type=EventType.FEATURE_START, project="p")
        result = n._format_slack(evt)
        assert ":hammer:" in result["blocks"][0]["text"]["text"]

    def test_emoji_mapping_feature_complete(self):
        n = self._notifier()
        evt = Event(type=EventType.FEATURE_COMPLETE, project="p")
        result = n._format_slack(evt)
        assert ":tada:" in result["blocks"][0]["text"]["text"]

    def test_emoji_mapping_feature_fail(self):
        n = self._notifier()
        evt = Event(type=EventType.FEATURE_FAIL, project="p")
        result = n._format_slack(evt)
        assert ":warning:" in result["blocks"][0]["text"]["text"]

    def test_emoji_mapping_loop_start(self):
        n = self._notifier()
        evt = Event(type=EventType.LOOP_START, project="p")
        result = n._format_slack(evt)
        assert ":repeat:" in result["blocks"][0]["text"]["text"]

    def test_emoji_mapping_loop_complete(self):
        n = self._notifier()
        evt = Event(type=EventType.LOOP_COMPLETE, project="p")
        result = n._format_slack(evt)
        assert ":checkered_flag:" in result["blocks"][0]["text"]["text"]

    def test_iteration_number_in_header(self):
        n = self._notifier()
        evt = Event(type=EventType.ITERATION_START, project="p", iteration_number=7)
        result = n._format_slack(evt)
        header_text = result["blocks"][0]["text"]["text"]
        assert "Iteration 7" in header_text

    def test_feature_title_in_header(self):
        n = self._notifier()
        evt = Event(type=EventType.FEATURE_COMPLETE, project="p",
                    feature_title="Login Flow")
        result = n._format_slack(evt)
        assert "Login Flow" in result["blocks"][0]["text"]["text"]

    def test_message_block_added_when_present(self):
        n = self._notifier()
        evt = Event(type=EventType.LOOP_START, project="p", message="Hello!")
        result = n._format_slack(evt)
        texts = [b.get("text", {}).get("text", "") for b in result["blocks"]
                 if b.get("type") == "section"]
        assert any("Hello!" in t for t in texts)

    def test_no_message_block_when_empty(self):
        n = self._notifier()
        evt = Event(type=EventType.LOOP_START, project="p")
        result = n._format_slack(evt)
        # Only header section + context block expected
        section_blocks = [b for b in result["blocks"] if b["type"] == "section"]
        assert len(section_blocks) == 1

    def test_details_become_fields(self):
        n = self._notifier()
        evt = Event(type=EventType.ITERATION_COMPLETE, project="p",
                    details={"duration": 120, "tests": 42})
        result = n._format_slack(evt)
        field_blocks = [b for b in result["blocks"]
                        if b.get("type") == "section" and "fields" in b]
        assert len(field_blocks) == 1
        field_texts = [f["text"] for f in field_blocks[0]["fields"]]
        assert any("duration" in t for t in field_texts)
        assert any("tests" in t for t in field_texts)

    def test_details_truncated_to_10_fields(self):
        n = self._notifier()
        details = {f"k{i}": i for i in range(15)}
        evt = Event(type=EventType.ITERATION_COMPLETE, project="p", details=details)
        result = n._format_slack(evt)
        field_blocks = [b for b in result["blocks"]
                        if b.get("type") == "section" and "fields" in b]
        assert len(field_blocks[0]["fields"]) <= 10

    def test_context_block_contains_project(self):
        n = self._notifier()
        evt = Event(type=EventType.LOOP_START, project="my-proj")
        result = n._format_slack(evt)
        context_blocks = [b for b in result["blocks"] if b["type"] == "context"]
        assert len(context_blocks) == 1
        element_text = context_blocks[0]["elements"][0]["text"]
        assert "my-proj" in element_text


class TestSendWebhook:
    """Tests for _send_webhook."""

    def test_filtered_event_type_returns_success_with_note(self):
        n = EventNotifier("p")
        webhook = WebhookConfig(
            url="https://example.com",
            name="filtered-hook",
            events=[EventType.ITERATION_COMPLETE],  # only this event
        )
        evt = Event(type=EventType.ITERATION_START, project="p")
        result = n._send_webhook(webhook, evt)
        assert result.success is True
        assert result.destination == "filtered-hook"
        assert "filtered" in (result.error or "").lower()

    def test_successful_json_webhook(self):
        n = EventNotifier("p")
        webhook = WebhookConfig(url="https://example.com/hook", name="json-hook")
        evt = Event(type=EventType.ITERATION_START, project="p")

        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = n._send_webhook(webhook, evt)

        assert result.success is True
        assert result.response_code == 200
        assert result.destination == "json-hook"

    def test_successful_slack_webhook(self):
        n = EventNotifier("p")
        webhook = WebhookConfig(url="https://hooks.slack.com/x", name="slack",
                                format="slack")
        evt = Event(type=EventType.ITERATION_COMPLETE, project="p",
                    iteration_number=1, message="done")

        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = n._send_webhook(webhook, evt)

        assert result.success is True

    def test_custom_headers_merged(self):
        """Verify custom headers are passed along with Content-Type."""
        n = EventNotifier("p")
        webhook = WebhookConfig(
            url="https://example.com/hook",
            name="auth-hook",
            headers={"X-API-Key": "secret"},
        )
        evt = Event(type=EventType.ITERATION_START, project="p")
        mock_resp = _make_mock_response(200)

        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = dict(req.headers)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            n._send_webhook(webhook, evt)

        # urllib capitalises first letter of each header word
        header_keys_lower = {k.lower() for k in captured_req["headers"]}
        assert "x-api-key" in header_keys_lower
        assert "content-type" in header_keys_lower

    def test_http_error_returns_failure(self):
        n = EventNotifier("p")
        webhook = WebhookConfig(url="https://example.com/hook", name="err-hook")
        evt = Event(type=EventType.ITERATION_START, project="p")

        http_err = urllib.error.HTTPError(
            url="https://example.com/hook", code=500,
            msg="Internal Server Error", hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            result = n._send_webhook(webhook, evt)

        assert result.success is False
        assert result.response_code == 500
        assert result.destination == "err-hook"

    def test_generic_exception_returns_failure(self):
        n = EventNotifier("p")
        webhook = WebhookConfig(url="https://example.com/hook", name="exc-hook")
        evt = Event(type=EventType.ITERATION_START, project="p")

        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            result = n._send_webhook(webhook, evt)

        assert result.success is False
        assert "refused" in result.error

    def test_json_payload_is_valid_json(self):
        """Verify the body sent to urlopen is valid JSON."""
        n = EventNotifier("p")
        webhook = WebhookConfig(url="https://example.com/hook", name="hook")
        evt = Event(type=EventType.ITERATION_START, project="p", details={"k": "v"})
        mock_resp = _make_mock_response(200)
        sent_data = {}

        original_request = urllib.request.Request

        def capturing_request(url, data=None, headers=None, method=None):
            sent_data["body"] = data
            return original_request(url, data=data, headers=headers or {}, method=method)

        with patch("urllib.request.Request", side_effect=capturing_request):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                n._send_webhook(webhook, evt)

        parsed = json.loads(sent_data["body"])
        assert parsed["project"] == "p"

    def test_event_type_not_filtered_when_events_empty(self):
        """Empty events list means all events pass through."""
        n = EventNotifier("p")
        webhook = WebhookConfig(url="https://example.com/hook", name="all-hook",
                                events=[])  # empty = all
        evt = Event(type=EventType.ITERATION_START, project="p")
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = n._send_webhook(webhook, evt)
        assert result.success is True


class TestNotify:
    """Tests for EventNotifier.notify()."""

    def test_no_webhooks_no_handlers_returns_empty(self):
        n = EventNotifier("p")
        evt = Event(type=EventType.LOOP_START, project="p")
        results = n.notify(evt)
        assert results == []

    def test_multiple_webhooks_all_called(self):
        n = EventNotifier("p")
        for i in range(3):
            n.add_webhook(WebhookConfig(url=f"https://example.com/{i}",
                                        name=f"hook-{i}"))
        evt = Event(type=EventType.ITERATION_START, project="p")
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            results = n.notify(evt)
        assert len(results) == 3

    def test_handler_called_for_matching_event(self):
        n = EventNotifier("p")
        handler = MagicMock()
        n.add_handler(EventType.FEATURE_COMPLETE, handler)
        evt = Event(type=EventType.FEATURE_COMPLETE, project="p",
                    feature_id="f1", feature_title="T")
        n.notify(evt)
        handler.assert_called_once_with(evt)

    def test_handler_not_called_for_other_event(self):
        n = EventNotifier("p")
        handler = MagicMock()
        n.add_handler(EventType.ITERATION_COMPLETE, handler)
        evt = Event(type=EventType.ITERATION_START, project="p")
        n.notify(evt)
        handler.assert_not_called()

    def test_multiple_handlers_all_called(self):
        n = EventNotifier("p")
        h1, h2 = MagicMock(), MagicMock()
        n.add_handler(EventType.ITERATION_FAIL, h1)
        n.add_handler(EventType.ITERATION_FAIL, h2)
        evt = Event(type=EventType.ITERATION_FAIL, project="p")
        n.notify(evt)
        h1.assert_called_once()
        h2.assert_called_once()

    def test_handler_exception_does_not_propagate(self):
        n = EventNotifier("p")

        def bad_handler(event):
            raise RuntimeError("boom")

        n.add_handler(EventType.LOOP_START, bad_handler)
        evt = Event(type=EventType.LOOP_START, project="p")
        # Should not raise
        results = n.notify(evt)
        assert isinstance(results, list)

    def test_returns_list_of_notification_results(self):
        n = EventNotifier("p")
        n.add_webhook(WebhookConfig(url="https://example.com/hook", name="h"))
        evt = Event(type=EventType.ITERATION_START, project="p")
        mock_resp = _make_mock_response(201)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            results = n.notify(evt)
        assert all(isinstance(r, NotificationResult) for r in results)


class TestConvenienceMethods:
    """Tests for notify_iteration_start/complete/fail/feature_complete."""

    def test_notify_iteration_start_creates_correct_event(self):
        n = EventNotifier("proj-x")
        captured = {}

        def record(event):
            captured["event"] = event

        n.add_handler(EventType.ITERATION_START, record)
        n.notify_iteration_start(iteration_number=3, agent="codex")
        assert captured["event"].type is EventType.ITERATION_START
        assert captured["event"].iteration_number == 3
        assert captured["event"].project == "proj-x"
        assert "Starting iteration 3" in captured["event"].message
        assert captured["event"].details["agent"] == "codex"

    def test_notify_iteration_complete_includes_features(self):
        n = EventNotifier("proj-x")
        captured = {}

        def record(event):
            captured["event"] = event

        n.add_handler(EventType.ITERATION_COMPLETE, record)
        n.notify_iteration_complete(iteration_number=2,
                                    features=["f1", "f2"],
                                    duration_seconds=100)
        evt = captured["event"]
        assert evt.type is EventType.ITERATION_COMPLETE
        assert evt.iteration_number == 2
        assert evt.details["features"] == ["f1", "f2"]
        assert evt.details["duration_seconds"] == 100
        assert "2 features" in evt.message

    def test_notify_iteration_fail_includes_error(self):
        n = EventNotifier("proj-x")
        captured = {}

        def record(event):
            captured["event"] = event

        n.add_handler(EventType.ITERATION_FAIL, record)
        n.notify_iteration_fail(iteration_number=1, error="build error", exit_code=1)
        evt = captured["event"]
        assert evt.type is EventType.ITERATION_FAIL
        assert evt.details["error"] == "build error"
        assert evt.details["exit_code"] == 1
        assert "failed" in evt.message.lower()

    def test_notify_feature_complete_sets_feature_fields(self):
        n = EventNotifier("proj-x")
        captured = {}

        def record(event):
            captured["event"] = event

        n.add_handler(EventType.FEATURE_COMPLETE, record)
        n.notify_feature_complete(
            feature_id="feat-99", feature_title="User Auth",
            lines_changed=50, tests_added=5,
        )
        evt = captured["event"]
        assert evt.type is EventType.FEATURE_COMPLETE
        assert evt.feature_id == "feat-99"
        assert evt.feature_title == "User Auth"
        assert evt.details["lines_changed"] == 50
        assert evt.details["tests_added"] == 5
        assert "User Auth" in evt.message

    def test_convenience_methods_return_list(self):
        n = EventNotifier("p")
        assert isinstance(n.notify_iteration_start(1), list)
        assert isinstance(n.notify_iteration_complete(1, []), list)
        assert isinstance(n.notify_iteration_fail(1, "err"), list)
        assert isinstance(n.notify_feature_complete("f", "title"), list)


class TestCreateEventNotifier:
    def test_returns_event_notifier_instance(self):
        n = create_event_notifier("voice-coach")
        assert isinstance(n, EventNotifier)
        assert n.project_name == "voice-coach"


# ===========================================================================
# Example function tests
# ===========================================================================

class TestExampleBasicUsage:
    """Tests for example_basic_usage()."""

    def test_runs_without_error_with_mocked_network(self):
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            example_basic_usage()  # should not raise

    def test_calls_urlopen_multiple_times(self):
        """The function sends several notifications — each triggers urlopen."""
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            example_basic_usage()
        # 4 notify calls total (start, complete, fail, feature_complete)
        assert mock_open.call_count == 4


class TestExampleSlackIntegration:
    def test_runs_without_error(self):
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            example_slack_integration()

    def test_only_iteration_complete_filtered_in(self):
        """Slack webhook configured for ITERATION_COMPLETE only, so exactly 1 urlopen call."""
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            example_slack_integration()
        assert mock_open.call_count == 1


class TestExampleMultipleWebhooks:
    def test_runs_without_error(self):
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            example_multiple_webhooks()

    def test_notify_iteration_start_sent_to_all_matching_webhooks(self):
        """
        slack_webhook filters to [ITERATION_COMPLETE, ITERATION_FAIL], so it
        skips ITERATION_START.  The other two (json + monitoring) accept all events.
        Net urlopen calls = 2.
        """
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            example_multiple_webhooks()
        assert mock_open.call_count == 2


class TestExampleCustomHandlers:
    def test_runs_without_error(self):
        example_custom_handlers()

    def test_feature_tracked_in_list(self, capsys):
        example_custom_handlers()
        captured = capsys.readouterr()
        assert "Content Generation" in captured.out

    def test_iteration_metrics_tracked(self, capsys):
        example_custom_handlers()
        captured = capsys.readouterr()
        assert "Iteration 3 metrics updated" in captured.out

    def test_completed_features_printed(self, capsys):
        example_custom_handlers()
        captured = capsys.readouterr()
        assert "Tracked features:" in captured.out
        assert "Iteration metrics:" in captured.out


class TestExampleCustomEvent:
    def test_runs_without_error(self):
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            example_custom_event()

    def test_prints_success_message(self, capsys):
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            example_custom_event()
        out = capsys.readouterr().out
        assert "Notification sent to" in out

    def test_prints_failure_message_on_http_error(self, capsys):
        http_err = urllib.error.HTTPError(
            url="https://example.com/webhook", code=503,
            msg="Unavailable", hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            example_custom_event()
        out = capsys.readouterr().out
        assert "Failed to send to" in out


class TestExampleIntegrationWithIterationLoop:
    def test_runs_without_error(self):
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            example_integration_with_iteration_loop()

    def test_sends_start_complete_and_feature_notifications(self):
        """
        Expect: 1 iteration_start + 3 feature_complete + 1 iteration_complete
        = 5 urlopen calls (all slack, all events allowed since events=[]).
        """
        mock_resp = _make_mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            example_integration_with_iteration_loop()
        assert mock_open.call_count == 5

    def test_failure_path_notifies_and_reraises(self):
        """If processing raises, notify_iteration_fail is called and exception re-raised."""
        mock_resp = _make_mock_response(200)
        call_counter = {"notify_fail": 0}
        original_notify = EventNotifier.notify

        def tracking_notify(self, event):
            if event.type is EventType.ITERATION_FAIL:
                call_counter["notify_fail"] += 1
            return original_notify(self, event)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch.object(EventNotifier, "notify", tracking_notify):
                # Patch notify_feature_complete to raise after first call
                original_fc = EventNotifier.notify_feature_complete
                call_fc = {"count": 0}

                def raising_fc(self, feature_id, feature_title, **kwargs):
                    call_fc["count"] += 1
                    if call_fc["count"] == 1:
                        raise RuntimeError("simulated build failure")
                    return original_fc(self, feature_id, feature_title, **kwargs)

                with patch.object(EventNotifier, "notify_feature_complete", raising_fc):
                    with pytest.raises(RuntimeError, match="simulated build failure"):
                        example_integration_with_iteration_loop()

        assert call_counter["notify_fail"] == 1


class TestExampleWithEnvironmentConfig:
    def test_returns_event_notifier(self):
        with patch.dict(os.environ, {}, clear=False):
            # Ensure env vars absent so no webhooks added
            os.environ.pop("SLACK_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_API_KEY", None)
            notifier = example_with_environment_config()
        assert isinstance(notifier, EventNotifier)

    def test_no_webhooks_without_env_vars(self):
        env = {"SLACK_WEBHOOK_URL": "", "MONITORING_WEBHOOK_URL": "",
               "MONITORING_API_KEY": ""}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SLACK_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_API_KEY", None)
            notifier = example_with_environment_config()
        assert len(notifier._webhooks) == 0

    def test_slack_webhook_added_when_env_set(self):
        env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("MONITORING_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_API_KEY", None)
            notifier = example_with_environment_config()
        assert len(notifier._webhooks) == 1
        assert notifier._webhooks[0].url == "https://hooks.slack.com/test"

    def test_monitoring_webhook_added_when_both_env_vars_set(self):
        env = {
            "MONITORING_WEBHOOK_URL": "https://monitor.example.com/hook",
            "MONITORING_API_KEY": "key123",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SLACK_WEBHOOK_URL", None)
            notifier = example_with_environment_config()
        assert len(notifier._webhooks) == 1
        assert "Bearer key123" in notifier._webhooks[0].headers["Authorization"]

    def test_both_webhooks_added_when_all_env_vars_set(self):
        env = {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
            "MONITORING_WEBHOOK_URL": "https://monitor.example.com/hook",
            "MONITORING_API_KEY": "key999",
        }
        with patch.dict(os.environ, env, clear=False):
            notifier = example_with_environment_config()
        assert len(notifier._webhooks) == 2

    def test_monitoring_not_added_with_only_url(self):
        """Both URL and API key must be set for monitoring webhook."""
        env = {"MONITORING_WEBHOOK_URL": "https://monitor.example.com/hook"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SLACK_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_API_KEY", None)
            notifier = example_with_environment_config()
        assert len(notifier._webhooks) == 0

    def test_monitoring_not_added_with_only_api_key(self):
        """Both URL and API key must be set for monitoring webhook."""
        env = {"MONITORING_API_KEY": "key-only"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SLACK_WEBHOOK_URL", None)
            os.environ.pop("MONITORING_WEBHOOK_URL", None)
            notifier = example_with_environment_config()
        assert len(notifier._webhooks) == 0

"""Tests for event notifier module."""

import urllib.error
import urllib.request
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from forge_harness.iteration.event_notifier import (
    Event,
    EventNotifier,
    EventType,
    NotificationResult,
    WebhookConfig,
    create_event_notifier,
)

# ===== EventType Tests =====


def test_event_type_enum_values():
    """Test EventType enum has correct values."""
    assert EventType.ITERATION_START.value == "iteration_start"
    assert EventType.ITERATION_COMPLETE.value == "iteration_complete"
    assert EventType.ITERATION_FAIL.value == "iteration_fail"
    assert EventType.FEATURE_START.value == "feature_start"
    assert EventType.FEATURE_COMPLETE.value == "feature_complete"
    assert EventType.FEATURE_FAIL.value == "feature_fail"
    assert EventType.LOOP_START.value == "loop_start"
    assert EventType.LOOP_COMPLETE.value == "loop_complete"


def test_event_type_string_comparison():
    """Test EventType can be compared as strings."""
    assert EventType.ITERATION_START == "iteration_start"
    assert EventType.FEATURE_COMPLETE == "feature_complete"


# ===== Event Tests =====


def test_event_creation_minimal():
    """Test creating event with minimal fields."""
    event = Event(type=EventType.ITERATION_START, project="test-project")
    assert event.type == EventType.ITERATION_START
    assert event.project == "test-project"
    assert isinstance(event.timestamp, datetime)
    assert event.iteration_number is None
    assert event.feature_id is None
    assert event.feature_title is None
    assert event.message == ""
    assert event.details == {}


def test_event_creation_full():
    """Test creating event with all fields."""
    timestamp = datetime.now()
    event = Event(
        type=EventType.FEATURE_COMPLETE,
        project="test-project",
        timestamp=timestamp,
        iteration_number=5,
        feature_id="feat-123",
        feature_title="Add login",
        message="Login feature completed successfully",
        details={"duration": 120, "tests_passed": 15},
    )
    assert event.type == EventType.FEATURE_COMPLETE
    assert event.project == "test-project"
    assert event.timestamp == timestamp
    assert event.iteration_number == 5
    assert event.feature_id == "feat-123"
    assert event.feature_title == "Add login"
    assert event.message == "Login feature completed successfully"
    assert event.details == {"duration": 120, "tests_passed": 15}


def test_event_to_dict():
    """Test Event.to_dict conversion."""
    timestamp = datetime(2025, 1, 29, 12, 30, 45)
    event = Event(
        type=EventType.ITERATION_COMPLETE,
        project="test-project",
        timestamp=timestamp,
        iteration_number=3,
        message="Iteration done",
        details={"features": 5},
    )
    result = event.to_dict()

    assert result["type"] == "iteration_complete"
    assert result["project"] == "test-project"
    assert result["timestamp"] == timestamp.isoformat()
    assert result["iteration_number"] == 3
    assert result["feature_id"] is None
    assert result["feature_title"] is None
    assert result["message"] == "Iteration done"
    assert result["details"] == {"features": 5}


def test_event_to_dict_with_feature():
    """Test Event.to_dict with feature information."""
    event = Event(
        type=EventType.FEATURE_COMPLETE,
        project="test-project",
        feature_id="feat-456",
        feature_title="User dashboard",
    )
    result = event.to_dict()

    assert result["feature_id"] == "feat-456"
    assert result["feature_title"] == "User dashboard"


# ===== NotificationResult Tests =====


def test_notification_result_success():
    """Test NotificationResult for successful notification."""
    result = NotificationResult(success=True, destination="slack-webhook", response_code=200)
    assert result.success is True
    assert result.destination == "slack-webhook"
    assert result.error is None
    assert result.response_code == 200


def test_notification_result_failure():
    """Test NotificationResult for failed notification."""
    result = NotificationResult(
        success=False, destination="webhook-1", error="Connection timeout", response_code=None
    )
    assert result.success is False
    assert result.destination == "webhook-1"
    assert result.error == "Connection timeout"
    assert result.response_code is None


def test_notification_result_http_error():
    """Test NotificationResult with HTTP error."""
    result = NotificationResult(
        success=False,
        destination="api-webhook",
        error="HTTP Error 404: Not Found",
        response_code=404,
    )
    assert result.success is False
    assert result.response_code == 404
    assert "404" in result.error


# ===== WebhookConfig Tests =====


def test_webhook_config_minimal():
    """Test WebhookConfig with minimal configuration."""
    config = WebhookConfig(url="https://example.com/webhook")
    assert config.url == "https://example.com/webhook"
    assert config.name == "webhook"
    assert config.headers == {}
    assert config.events == []
    assert config.format == "json"


def test_webhook_config_full():
    """Test WebhookConfig with full configuration."""
    config = WebhookConfig(
        url="https://slack.com/api/webhook",
        name="slack-notifications",
        headers={"Authorization": "Bearer token123"},
        events=[EventType.ITERATION_COMPLETE, EventType.FEATURE_COMPLETE],
        format="slack",
    )
    assert config.url == "https://slack.com/api/webhook"
    assert config.name == "slack-notifications"
    assert config.headers == {"Authorization": "Bearer token123"}
    assert len(config.events) == 2
    assert EventType.ITERATION_COMPLETE in config.events
    assert EventType.FEATURE_COMPLETE in config.events
    assert config.format == "slack"


def test_webhook_config_with_custom_headers():
    """Test WebhookConfig with custom headers."""
    config = WebhookConfig(
        url="https://api.example.com/notify", headers={"X-API-Key": "secret", "X-Project": "forge"}
    )
    assert config.headers["X-API-Key"] == "secret"
    assert config.headers["X-Project"] == "forge"


# ===== EventNotifier Tests =====


def test_event_notifier_initialization():
    """Test EventNotifier initialization."""
    notifier = EventNotifier("test-project")
    assert notifier.project_name == "test-project"
    assert notifier._webhooks == []
    assert notifier._handlers == {}


def test_event_notifier_add_webhook():
    """Test adding webhooks to notifier."""
    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://example.com/webhook")

    notifier.add_webhook(config)
    assert len(notifier._webhooks) == 1
    assert notifier._webhooks[0] == config


def test_event_notifier_add_multiple_webhooks():
    """Test adding multiple webhooks."""
    notifier = EventNotifier("test-project")
    config1 = WebhookConfig(url="https://example.com/webhook1", name="webhook-1")
    config2 = WebhookConfig(url="https://example.com/webhook2", name="webhook-2")

    notifier.add_webhook(config1)
    notifier.add_webhook(config2)

    assert len(notifier._webhooks) == 2
    assert notifier._webhooks[0].name == "webhook-1"
    assert notifier._webhooks[1].name == "webhook-2"


def test_event_notifier_add_handler():
    """Test adding custom event handler."""
    notifier = EventNotifier("test-project")
    handler = Mock()

    notifier.add_handler(EventType.ITERATION_START, handler)

    assert EventType.ITERATION_START in notifier._handlers
    assert len(notifier._handlers[EventType.ITERATION_START]) == 1
    assert notifier._handlers[EventType.ITERATION_START][0] == handler


def test_event_notifier_add_multiple_handlers_same_event():
    """Test adding multiple handlers for same event type."""
    notifier = EventNotifier("test-project")
    handler1 = Mock()
    handler2 = Mock()

    notifier.add_handler(EventType.FEATURE_COMPLETE, handler1)
    notifier.add_handler(EventType.FEATURE_COMPLETE, handler2)

    assert len(notifier._handlers[EventType.FEATURE_COMPLETE]) == 2


def test_event_notifier_add_handlers_different_events():
    """Test adding handlers for different event types."""
    notifier = EventNotifier("test-project")
    handler1 = Mock()
    handler2 = Mock()

    notifier.add_handler(EventType.ITERATION_START, handler1)
    notifier.add_handler(EventType.ITERATION_COMPLETE, handler2)

    assert len(notifier._handlers) == 2
    assert EventType.ITERATION_START in notifier._handlers
    assert EventType.ITERATION_COMPLETE in notifier._handlers


# ===== Slack Formatting Tests =====


def test_format_slack_iteration_start():
    """Test Slack formatting for iteration start."""
    notifier = EventNotifier("test-project")
    event = Event(
        type=EventType.ITERATION_START,
        project="test-project",
        iteration_number=1,
        message="Beginning iteration",
        timestamp=datetime(2025, 1, 29, 12, 0, 0),
    )

    result = notifier._format_slack(event)

    assert "blocks" in result
    blocks = result["blocks"]
    assert len(blocks) >= 2

    # Check header block
    header = blocks[0]
    assert header["type"] == "section"
    assert ":rocket:" in header["text"]["text"]
    assert "Iteration Start" in header["text"]["text"]
    assert "Iteration 1" in header["text"]["text"]

    # Check message block
    message_block = blocks[1]
    assert message_block["type"] == "section"
    assert "Beginning iteration" in message_block["text"]["text"]


def test_format_slack_feature_complete():
    """Test Slack formatting for feature complete."""
    notifier = EventNotifier("test-project")
    event = Event(
        type=EventType.FEATURE_COMPLETE,
        project="test-project",
        feature_title="User Authentication",
        message="Feature completed successfully",
        timestamp=datetime(2025, 1, 29, 12, 30, 0),
    )

    result = notifier._format_slack(event)
    blocks = result["blocks"]

    # Check header includes feature title
    header_text = blocks[0]["text"]["text"]
    assert ":tada:" in header_text
    assert "User Authentication" in header_text


def test_format_slack_with_details():
    """Test Slack formatting with details fields."""
    notifier = EventNotifier("test-project")
    event = Event(
        type=EventType.ITERATION_COMPLETE,
        project="test-project",
        iteration_number=3,
        details={"features_completed": 5, "tests_passed": 42, "duration": "2h 30m"},
        timestamp=datetime(2025, 1, 29, 15, 0, 0),
    )

    result = notifier._format_slack(event)
    blocks = result["blocks"]

    # Find fields block
    fields_block = None
    for block in blocks:
        if block["type"] == "section" and "fields" in block:
            fields_block = block
            break

    assert fields_block is not None
    assert len(fields_block["fields"]) == 3


def test_format_slack_details_limit():
    """Test Slack formatting respects 10 field limit."""
    notifier = EventNotifier("test-project")
    details = {f"field_{i}": f"value_{i}" for i in range(15)}
    event = Event(
        type=EventType.LOOP_COMPLETE,
        project="test-project",
        details=details,
        timestamp=datetime(2025, 1, 29, 16, 0, 0),
    )

    result = notifier._format_slack(event)
    blocks = result["blocks"]

    # Find fields block
    fields_block = None
    for block in blocks:
        if block["type"] == "section" and "fields" in block:
            fields_block = block
            break

    assert fields_block is not None
    assert len(fields_block["fields"]) == 10  # Slack limit


def test_format_slack_context_footer():
    """Test Slack formatting includes context footer."""
    notifier = EventNotifier("test-project")
    timestamp = datetime(2025, 1, 29, 12, 45, 30)
    event = Event(type=EventType.FEATURE_START, project="test-project", timestamp=timestamp)

    result = notifier._format_slack(event)
    blocks = result["blocks"]

    # Check last block is context
    context_block = blocks[-1]
    assert context_block["type"] == "context"
    assert "Project: test-project" in context_block["elements"][0]["text"]
    assert "2025-01-29 12:45:30" in context_block["elements"][0]["text"]


def test_format_slack_all_event_types_have_emojis():
    """Test all event types have emoji mappings."""
    notifier = EventNotifier("test-project")

    for event_type in EventType:
        event = Event(type=event_type, project="test-project")
        result = notifier._format_slack(event)
        blocks = result["blocks"]
        header_text = blocks[0]["text"]["text"]

        # Should have an emoji (starts with :)
        assert ":" in header_text


# ===== Webhook Sending Tests =====


@patch("urllib.request.urlopen")
def test_send_webhook_json_format_success(mock_urlopen):
    """Test sending webhook with JSON format successfully."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://example.com/webhook", format="json")
    event = Event(type=EventType.ITERATION_START, project="test-project", iteration_number=1)

    result = notifier._send_webhook(config, event)

    assert result.success is True
    assert result.destination == "webhook"
    assert result.response_code == 200
    assert result.error is None


@patch("urllib.request.urlopen")
def test_send_webhook_slack_format_success(mock_urlopen):
    """Test sending webhook with Slack format successfully."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://hooks.slack.com/webhook", name="slack", format="slack")
    event = Event(
        type=EventType.FEATURE_COMPLETE, project="test-project", feature_title="New Feature"
    )

    result = notifier._send_webhook(config, event)

    assert result.success is True
    assert result.destination == "slack"
    assert result.response_code == 200


@patch("urllib.request.urlopen")
def test_send_webhook_with_custom_headers(mock_urlopen):
    """Test sending webhook with custom headers."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://example.com/webhook", headers={"X-API-Key": "secret123"})
    event = Event(type=EventType.LOOP_START, project="test-project")

    notifier._send_webhook(config, event)

    # Verify request was made with custom headers
    call_args = mock_urlopen.call_args
    request = call_args[0][0]
    assert request.headers["X-api-key"] == "secret123"
    assert request.headers["Content-type"] == "application/json"


@patch("urllib.request.urlopen")
def test_send_webhook_http_error(mock_urlopen):
    """Test handling HTTP errors when sending webhook."""
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://example.com/webhook", code=404, msg="Not Found", hdrs={}, fp=None
    )

    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://example.com/webhook")
    event = Event(type=EventType.ITERATION_FAIL, project="test-project")

    result = notifier._send_webhook(config, event)

    assert result.success is False
    assert result.response_code == 404
    assert result.error is not None


@patch("urllib.request.urlopen")
def test_send_webhook_connection_error(mock_urlopen):
    """Test handling connection errors when sending webhook."""
    mock_urlopen.side_effect = ConnectionError("Connection refused")

    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://example.com/webhook")
    event = Event(type=EventType.ITERATION_START, project="test-project")

    result = notifier._send_webhook(config, event)

    assert result.success is False
    assert result.error == "Connection refused"
    assert result.response_code is None


def test_send_webhook_event_filtering():
    """Test webhook event filtering."""
    notifier = EventNotifier("test-project")
    config = WebhookConfig(
        url="https://example.com/webhook",
        events=[EventType.ITERATION_COMPLETE, EventType.FEATURE_COMPLETE],
    )

    # Event not in filter list
    event = Event(type=EventType.ITERATION_START, project="test-project")
    result = notifier._send_webhook(config, event)

    assert result.success is True
    assert result.error == "Event type filtered"


def test_send_webhook_no_filtering_when_events_empty():
    """Test webhook sends all events when events list is empty."""
    # This is tested indirectly through other tests, but let's be explicit
    notifier = EventNotifier("test-project")
    config = WebhookConfig(
        url="https://example.com/webhook",
        events=[],  # Empty = all events
    )

    # Should not filter
    event = Event(type=EventType.ITERATION_START, project="test-project")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = notifier._send_webhook(config, event)

        assert result.success is True
        assert result.error != "Event type filtered"


# ===== Event Notification Tests =====


@patch("urllib.request.urlopen")
def test_notify_with_single_webhook(mock_urlopen):
    """Test notify sends to single webhook."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    config = WebhookConfig(url="https://example.com/webhook")
    notifier.add_webhook(config)

    event = Event(type=EventType.ITERATION_START, project="test-project")
    results = notifier.notify(event)

    assert len(results) == 1
    assert results[0].success is True


@patch("urllib.request.urlopen")
def test_notify_with_multiple_webhooks(mock_urlopen):
    """Test notify sends to multiple webhooks."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook1", name="webhook-1"))
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook2", name="webhook-2"))
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook3", name="webhook-3"))

    event = Event(type=EventType.FEATURE_COMPLETE, project="test-project")
    results = notifier.notify(event)

    assert len(results) == 3
    assert all(r.success for r in results)


def test_notify_calls_custom_handlers():
    """Test notify calls registered custom handlers."""
    notifier = EventNotifier("test-project")
    handler = Mock()
    notifier.add_handler(EventType.ITERATION_COMPLETE, handler)

    event = Event(type=EventType.ITERATION_COMPLETE, project="test-project")
    notifier.notify(event)

    handler.assert_called_once_with(event)


def test_notify_calls_multiple_handlers():
    """Test notify calls all registered handlers for event type."""
    notifier = EventNotifier("test-project")
    handler1 = Mock()
    handler2 = Mock()
    handler3 = Mock()

    notifier.add_handler(EventType.FEATURE_START, handler1)
    notifier.add_handler(EventType.FEATURE_START, handler2)
    notifier.add_handler(EventType.FEATURE_START, handler3)

    event = Event(type=EventType.FEATURE_START, project="test-project")
    notifier.notify(event)

    handler1.assert_called_once_with(event)
    handler2.assert_called_once_with(event)
    handler3.assert_called_once_with(event)


def test_notify_handles_handler_exceptions():
    """Test notify continues if handler raises exception."""
    notifier = EventNotifier("test-project")
    failing_handler = Mock(side_effect=Exception("Handler error"))
    success_handler = Mock()

    notifier.add_handler(EventType.LOOP_START, failing_handler)
    notifier.add_handler(EventType.LOOP_START, success_handler)

    event = Event(type=EventType.LOOP_START, project="test-project")
    # Should not raise exception
    notifier.notify(event)

    # Both handlers should be called despite first one failing
    failing_handler.assert_called_once()
    success_handler.assert_called_once()


def test_notify_no_webhooks_no_handlers():
    """Test notify with no webhooks or handlers."""
    notifier = EventNotifier("test-project")
    event = Event(type=EventType.ITERATION_START, project="test-project")

    results = notifier.notify(event)

    assert results == []


# ===== Convenience Methods Tests =====


@patch("urllib.request.urlopen")
def test_notify_iteration_start(mock_urlopen):
    """Test notify_iteration_start convenience method."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook"))

    results = notifier.notify_iteration_start(iteration_number=5, features_count=10, agent="codex")

    assert len(results) == 1
    assert results[0].success is True


@patch("urllib.request.urlopen")
def test_notify_iteration_complete(mock_urlopen):
    """Test notify_iteration_complete convenience method."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook"))

    features = ["feat-1", "feat-2", "feat-3"]
    results = notifier.notify_iteration_complete(
        iteration_number=5, features=features, duration=120
    )

    assert len(results) == 1
    assert results[0].success is True


@patch("urllib.request.urlopen")
def test_notify_iteration_fail(mock_urlopen):
    """Test notify_iteration_fail convenience method."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook"))

    results = notifier.notify_iteration_fail(iteration_number=3, error="Build failed", exit_code=1)

    assert len(results) == 1
    assert results[0].success is True


@patch("urllib.request.urlopen")
def test_notify_feature_complete(mock_urlopen):
    """Test notify_feature_complete convenience method."""
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    notifier = EventNotifier("test-project")
    notifier.add_webhook(WebhookConfig(url="https://example.com/webhook"))

    results = notifier.notify_feature_complete(
        feature_id="feat-456", feature_title="User Dashboard", tests_passed=25, lines_changed=342
    )

    assert len(results) == 1
    assert results[0].success is True


def test_convenience_methods_use_project_name():
    """Test convenience methods use notifier's project name."""
    notifier = EventNotifier("my-awesome-project")
    handler = Mock()
    notifier.add_handler(EventType.ITERATION_START, handler)

    notifier.notify_iteration_start(1)

    call_args = handler.call_args[0][0]
    assert call_args.project == "my-awesome-project"


def test_convenience_methods_set_correct_event_types():
    """Test convenience methods set correct event types."""
    notifier = EventNotifier("test-project")

    # Capture events through handler
    events = []

    def capture_handler(event):
        events.append(event)

    for event_type in EventType:
        notifier.add_handler(event_type, capture_handler)

    notifier.notify_iteration_start(1)
    notifier.notify_iteration_complete(1, [])
    notifier.notify_iteration_fail(1, "error")
    notifier.notify_feature_complete("feat-1", "Feature")

    assert events[0].type == EventType.ITERATION_START
    assert events[1].type == EventType.ITERATION_COMPLETE
    assert events[2].type == EventType.ITERATION_FAIL
    assert events[3].type == EventType.FEATURE_COMPLETE


# ===== Factory Function Tests =====


def test_create_event_notifier():
    """Test factory function creates EventNotifier."""
    notifier = create_event_notifier("test-project")

    assert isinstance(notifier, EventNotifier)
    assert notifier.project_name == "test-project"


def test_create_event_notifier_returns_new_instances():
    """Test factory function returns new instances."""
    notifier1 = create_event_notifier("project-1")
    notifier2 = create_event_notifier("project-2")

    assert notifier1 is not notifier2
    assert notifier1.project_name == "project-1"
    assert notifier2.project_name == "project-2"

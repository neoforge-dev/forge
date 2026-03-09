# Event Notifier

The Event Notifier module provides a flexible system for sending notifications about iteration events in FORGE Harness.

## Features

- **Multiple Destinations**: Send notifications to multiple webhooks simultaneously
- **Slack Integration**: Built-in Slack message formatting with rich blocks
- **Event Filtering**: Configure which events each webhook should receive
- **Custom Handlers**: Add custom Python functions to handle events
- **Convenience Methods**: Simple methods for common notification scenarios
- **Error Handling**: Graceful handling of webhook failures
- **Type Safety**: Full type hints for better IDE support

## Quick Start

```python
from forge_harness.iteration.event_notifier import (
    EventNotifier,
    WebhookConfig,
    EventType,
)

# Create notifier
notifier = EventNotifier("my-project")

# Add Slack webhook
slack = WebhookConfig(
    url="https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
    name="slack",
    format="slack"
)
notifier.add_webhook(slack)

# Send notifications
notifier.notify_iteration_start(iteration_number=1)
notifier.notify_feature_complete(
    feature_id="feat-123",
    feature_title="User Authentication"
)
```

## Event Types

The following event types are supported:

- `ITERATION_START` - Iteration begins
- `ITERATION_COMPLETE` - Iteration completes successfully
- `ITERATION_FAIL` - Iteration fails
- `FEATURE_START` - Feature implementation begins
- `FEATURE_COMPLETE` - Feature implementation completes
- `FEATURE_FAIL` - Feature implementation fails
- `LOOP_START` - Autonomous loop starts
- `LOOP_COMPLETE` - Autonomous loop completes

## Webhook Configuration

### Basic JSON Webhook

```python
webhook = WebhookConfig(
    url="https://example.com/webhook",
    name="my-webhook",
    format="json"
)
```

### Slack Webhook

```python
slack_webhook = WebhookConfig(
    url="https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
    name="slack-notifications",
    format="slack"
)
```

### Filtered Events

Only send specific events to a webhook:

```python
webhook = WebhookConfig(
    url="https://example.com/webhook",
    events=[
        EventType.ITERATION_COMPLETE,
        EventType.ITERATION_FAIL
    ]
)
```

### Custom Headers

Add authentication or custom headers:

```python
webhook = WebhookConfig(
    url="https://api.example.com/notifications",
    headers={
        "Authorization": "Bearer your-token",
        "X-Project": "forge"
    }
)
```

## Convenience Methods

### Iteration Events

```python
# Start iteration
notifier.notify_iteration_start(
    iteration_number=1,
    agent="codex",
    features_count=5
)

# Complete iteration
notifier.notify_iteration_complete(
    iteration_number=1,
    features=["feat-1", "feat-2", "feat-3"],
    duration_seconds=120
)

# Fail iteration
notifier.notify_iteration_fail(
    iteration_number=1,
    error="Build failed",
    exit_code=1
)
```

### Feature Events

```python
# Complete feature
notifier.notify_feature_complete(
    feature_id="feat-123",
    feature_title="User Authentication",
    tests_passed=15,
    lines_changed=342
)
```

## Custom Handlers

Add custom Python functions to handle events:

```python
def log_to_database(event: Event):
    """Custom handler to log events to database."""
    db.insert("events", {
        "type": event.type,
        "project": event.project,
        "timestamp": event.timestamp,
        "details": event.details
    })

notifier.add_handler(EventType.FEATURE_COMPLETE, log_to_database)
```

## Custom Events

Send custom events:

```python
from forge_harness.iteration.event_notifier import Event, EventType

event = Event(
    type=EventType.LOOP_START,
    project="my-project",
    message="Starting autonomous loop",
    details={"mode": "ralph-wiggum", "max_iterations": 10}
)

results = notifier.notify(event)
```

## Slack Message Format

Slack messages include:

- **Header**: Event type with emoji, iteration/feature info
- **Message**: Custom message text
- **Details**: Key-value fields from event details (max 10)
- **Footer**: Project name and timestamp

Example Slack message:

```
:rocket: Iteration Start | Iteration 1

Starting iteration 1

features_count: 5
agent: codex

Project: my-project | 2026-01-29 12:30:45
```

## Error Handling

The notifier handles errors gracefully:

```python
results = notifier.notify_iteration_start(1)

for result in results:
    if result.success:
        print(f"✓ Sent to {result.destination}")
    else:
        print(f"✗ Failed to send to {result.destination}: {result.error}")
```

Failed webhook deliveries don't prevent other webhooks from being called.

## Integration with Iteration Loop

```python
from forge_harness.iteration.event_notifier import create_event_notifier

# Create notifier
notifier = create_event_notifier("my-project")
notifier.add_webhook(webhook_config)

# In iteration loop
try:
    notifier.notify_iteration_start(iteration_number=1)

    # Implement features...
    for feature in features:
        implement_feature(feature)
        notifier.notify_feature_complete(
            feature_id=feature.id,
            feature_title=feature.title
        )

    notifier.notify_iteration_complete(
        iteration_number=1,
        features=[f.id for f in features]
    )
except Exception as e:
    notifier.notify_iteration_fail(
        iteration_number=1,
        error=str(e)
    )
    raise
```

## Environment Configuration

Load webhook URLs from environment:

```python
import os

notifier = create_event_notifier("my-project")

slack_url = os.getenv("SLACK_WEBHOOK_URL")
if slack_url:
    notifier.add_webhook(WebhookConfig(
        url=slack_url,
        format="slack"
    ))
```

## Testing

Run the test suite:

```bash
uv run pytest tests/test_event_notifier.py -v
```

Run with coverage:

```bash
uv run pytest tests/test_event_notifier.py --cov=forge_harness.iteration.event_notifier --cov-report=term-missing
```

## Examples

See `event_notifier_example.py` for complete usage examples:

- Basic webhook usage
- Slack integration
- Multiple webhooks
- Custom handlers
- Custom events
- Integration with iteration loops
- Environment configuration

## API Reference

### Classes

- **`EventType`**: Enum of supported event types
- **`Event`**: Event data class with type, project, timestamp, and details
- **`NotificationResult`**: Result of sending a notification
- **`WebhookConfig`**: Webhook configuration with URL, headers, and filters
- **`EventNotifier`**: Main class for managing notifications

### Methods

- `EventNotifier.add_webhook(config)`: Add webhook destination
- `EventNotifier.add_handler(event_type, handler)`: Add custom handler
- `EventNotifier.notify(event)`: Send event to all destinations
- `EventNotifier.notify_iteration_start(iteration_number, **details)`: Convenience method
- `EventNotifier.notify_iteration_complete(iteration_number, features, **details)`: Convenience method
- `EventNotifier.notify_iteration_fail(iteration_number, error, **details)`: Convenience method
- `EventNotifier.notify_feature_complete(feature_id, feature_title, **details)`: Convenience method

### Factory Functions

- `create_event_notifier(project_name)`: Create new EventNotifier instance

## License

Part of FORGE Harness - see main LICENSE file.

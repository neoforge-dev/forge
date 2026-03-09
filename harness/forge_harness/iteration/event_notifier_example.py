"""Example usage of EventNotifier for FORGE Harness.

This module demonstrates how to configure and use the EventNotifier
to send notifications about iteration events to various destinations.
"""

from forge_harness.iteration.event_notifier import (
    Event,
    EventNotifier,
    EventType,
    WebhookConfig,
    create_event_notifier,
)


def example_basic_usage():
    """Basic usage with a generic webhook."""
    # Create notifier for a project
    notifier = EventNotifier("my-mvp-project")

    # Add a webhook destination
    webhook = WebhookConfig(
        url="https://example.com/api/notifications", name="main-webhook", format="json"
    )
    notifier.add_webhook(webhook)

    # Send notifications using convenience methods
    notifier.notify_iteration_start(iteration_number=1, agent="codex", features_count=5)

    # Notify when iteration completes
    notifier.notify_iteration_complete(
        iteration_number=1,
        features=["feat-1", "feat-2", "feat-3"],
        duration_seconds=120,
        tests_passed=42,
    )

    # Notify on failure
    notifier.notify_iteration_fail(
        iteration_number=1, error="Build failed: syntax error in auth.py", exit_code=1
    )

    # Notify feature completion
    notifier.notify_feature_complete(
        feature_id="feat-123",
        feature_title="User Authentication",
        lines_changed=342,
        tests_added=15,
    )


def example_slack_integration():
    """Example with Slack webhook integration."""
    notifier = create_event_notifier("voice-coach-mvp")

    # Configure Slack webhook
    slack_webhook = WebhookConfig(
        url="https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
        name="slack-dev-channel",
        format="slack",  # Use Slack-specific formatting
        events=[  # Only notify on these events
            EventType.ITERATION_COMPLETE,
            EventType.ITERATION_FAIL,
            EventType.FEATURE_COMPLETE,
        ],
    )
    notifier.add_webhook(slack_webhook)

    # Send notification
    notifier.notify_iteration_complete(
        iteration_number=5,
        features=["auth", "dashboard", "settings"],
        duration_seconds=180,
        agent="opencode",
    )


def example_multiple_webhooks():
    """Example with multiple webhook destinations."""
    notifier = create_event_notifier("campaign-intelligence")

    # Slack for team notifications
    slack_webhook = WebhookConfig(
        url="https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
        name="slack-team",
        format="slack",
        events=[EventType.ITERATION_COMPLETE, EventType.ITERATION_FAIL],
    )

    # Generic webhook for logging service
    logging_webhook = WebhookConfig(
        url="https://logging.example.com/webhooks/forge", name="logging-service", format="json"
    )

    # Webhook for monitoring dashboard
    monitoring_webhook = WebhookConfig(
        url="https://monitoring.example.com/api/events",
        name="monitoring",
        format="json",
        headers={"X-API-Key": "your-api-key"},
    )

    notifier.add_webhook(slack_webhook)
    notifier.add_webhook(logging_webhook)
    notifier.add_webhook(monitoring_webhook)

    # This will send to all three destinations
    notifier.notify_iteration_start(iteration_number=10, agent="codex")


def example_custom_handlers():
    """Example with custom event handlers."""
    notifier = create_event_notifier("brand-brain")

    # Track completed features in memory
    completed_features = []

    def track_feature_completion(event: Event):
        """Custom handler to track feature completions."""
        completed_features.append(
            {"id": event.feature_id, "title": event.feature_title, "timestamp": event.timestamp}
        )
        print(f"Feature completed: {event.feature_title}")

    # Track iteration metrics
    iteration_metrics = {}

    def track_iteration_metrics(event: Event):
        """Custom handler to track iteration metrics."""
        iteration_num = event.iteration_number
        if iteration_num not in iteration_metrics:
            iteration_metrics[iteration_num] = {}
        iteration_metrics[iteration_num].update(event.details)
        print(f"Iteration {iteration_num} metrics updated")

    # Register handlers
    notifier.add_handler(EventType.FEATURE_COMPLETE, track_feature_completion)
    notifier.add_handler(EventType.ITERATION_COMPLETE, track_iteration_metrics)

    # These will trigger the custom handlers
    notifier.notify_feature_complete(
        feature_id="feat-456", feature_title="Content Generation", complexity="high"
    )

    notifier.notify_iteration_complete(
        iteration_number=3, features=["feat-456"], duration_seconds=240
    )

    print(f"Tracked features: {completed_features}")
    print(f"Iteration metrics: {iteration_metrics}")


def example_custom_event():
    """Example sending custom events."""
    notifier = create_event_notifier("interview-simulator")

    webhook = WebhookConfig(url="https://example.com/webhook", format="json")
    notifier.add_webhook(webhook)

    # Create and send custom event
    custom_event = Event(
        type=EventType.LOOP_START,
        project="interview-simulator",
        message="Starting autonomous development loop",
        details={"mode": "ralph-wiggum", "max_iterations": 10, "agent": "codex"},
    )

    results = notifier.notify(custom_event)

    # Check results
    for result in results:
        if result.success:
            print(f"Notification sent to {result.destination}")
        else:
            print(f"Failed to send to {result.destination}: {result.error}")


def example_integration_with_iteration_loop():
    """Example integration with actual iteration loop."""
    notifier = create_event_notifier("pkm-ai")

    # Configure Slack
    slack_webhook = WebhookConfig(
        url="https://hooks.slack.com/services/YOUR/WEBHOOK/URL", name="slack", format="slack"
    )
    notifier.add_webhook(slack_webhook)

    # Simulated iteration loop
    iteration_number = 1
    features_to_implement = [
        {"id": "feat-1", "title": "Note taking"},
        {"id": "feat-2", "title": "Search functionality"},
        {"id": "feat-3", "title": "Export to PDF"},
    ]

    try:
        # Start iteration
        notifier.notify_iteration_start(
            iteration_number=iteration_number, features_count=len(features_to_implement)
        )

        # Process features
        completed = []
        for feature in features_to_implement:
            # Simulate feature implementation
            # In real code, this would be actual implementation
            notifier.notify_feature_complete(
                feature_id=feature["id"], feature_title=feature["title"], tests_passed=10
            )
            completed.append(feature["id"])

        # Complete iteration
        notifier.notify_iteration_complete(
            iteration_number=iteration_number, features=completed, duration_seconds=300
        )

    except Exception as e:
        # Notify on failure
        notifier.notify_iteration_fail(
            iteration_number=iteration_number,
            error=str(e),
            features_attempted=len(features_to_implement),
        )
        raise


def example_with_environment_config():
    """Example using environment variables for configuration."""
    import os

    notifier = create_event_notifier("ad-asset-finder")

    # Load webhook URL from environment
    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url:
        slack_webhook = WebhookConfig(url=slack_url, name="slack", format="slack")
        notifier.add_webhook(slack_webhook)

    # Load monitoring webhook
    monitoring_url = os.getenv("MONITORING_WEBHOOK_URL")
    monitoring_api_key = os.getenv("MONITORING_API_KEY")
    if monitoring_url and monitoring_api_key:
        monitoring_webhook = WebhookConfig(
            url=monitoring_url,
            name="monitoring",
            format="json",
            headers={"Authorization": f"Bearer {monitoring_api_key}"},
        )
        notifier.add_webhook(monitoring_webhook)

    return notifier


if __name__ == "__main__":
    print("=== Basic Usage ===")
    # example_basic_usage()  # Uncomment to run

    print("\n=== Slack Integration ===")
    # example_slack_integration()  # Uncomment to run

    print("\n=== Multiple Webhooks ===")
    # example_multiple_webhooks()  # Uncomment to run

    print("\n=== Custom Handlers ===")
    example_custom_handlers()

    print("\n=== Custom Event ===")
    # example_custom_event()  # Uncomment to run

    print("\n=== Integration with Iteration Loop ===")
    # example_integration_with_iteration_loop()  # Uncomment to run

    print("\n=== Environment Config ===")
    notifier = example_with_environment_config()
    print(f"Created notifier with {len(notifier._webhooks)} webhooks")

"""Example usage of the Feature Queue Manager.

This demonstrates how to use the feature queue for managing features.json.
"""

from forge_harness.iteration.feature_queue import (
    Feature,
    FeatureStatus,
    Priority,
    create_feature_queue,
)


def example_basic_usage():
    """Basic usage example."""
    print("=== Basic Usage ===\n")

    # Create a queue (uses features.json in current directory by default)
    queue = create_feature_queue()

    # Get all features
    features = queue.get_all()
    print(f"Total features: {len(features)}")

    # Get statistics
    stats = queue.get_stats()
    print("\nQueue Stats:")
    print(f"  Total: {stats.total}")
    print(f"  Pending: {stats.pending}")
    print(f"  In Progress: {stats.in_progress}")
    print(f"  Completed: {stats.completed}")
    print(f"  Blocked: {stats.blocked}")
    print(f"  By Priority: {stats.by_priority}")
    print(f"  By Epic: {stats.by_epic}")


def example_get_next_feature():
    """Example of getting the next feature to work on."""
    print("\n=== Getting Next Feature ===\n")

    queue = create_feature_queue()

    # Get the next high-priority pending feature
    next_feature = queue.get_next_feature()

    if next_feature:
        print("Next feature to work on:")
        print(f"  ID: {next_feature.id}")
        print(f"  Title: {next_feature.title}")
        print(f"  Priority: {next_feature.priority}")
        print(f"  Epic: {next_feature.epic}")

        # Start working on it
        queue.start_feature(next_feature.id, assigned_to="agent-1")
        print(f"\nStarted feature {next_feature.id}")
    else:
        print("No pending features")


def example_filtering():
    """Example of filtering features."""
    print("\n=== Filtering Features ===\n")

    queue = create_feature_queue()

    # Get high priority features
    high_priority = queue.get_by_priority(Priority.HIGH)
    print(f"High priority features: {len(high_priority)}")
    for f in high_priority:
        print(f"  - {f.id}: {f.title}")

    # Get features by epic
    epic_features = queue.get_by_epic("Epic A")
    print(f"\nFeatures in Epic A: {len(epic_features)}")
    for f in epic_features:
        print(f"  - {f.id}: {f.title}")

    # Get features by status
    in_progress = queue.get_by_status(FeatureStatus.IN_PROGRESS)
    print(f"\nFeatures in progress: {len(in_progress)}")
    for f in in_progress:
        print(f"  - {f.id}: {f.title} (assigned to: {f.assigned_to})")


def example_status_transitions():
    """Example of status transitions."""
    print("\n=== Status Transitions ===\n")

    queue = create_feature_queue()

    # Get a pending feature
    pending = queue.get_pending(limit=1)
    if not pending:
        print("No pending features to demonstrate")
        return

    feature = pending[0]
    print(f"Starting feature: {feature.id}")

    # Start the feature
    queue.start_feature(feature.id, assigned_to="agent-1")
    print(f"Status: {queue.get_by_id(feature.id).status}")

    # Complete the feature
    queue.complete_feature(feature.id)
    print(f"Status: {queue.get_by_id(feature.id).status}")

    print("\nFeature lifecycle completed!")


def example_blocking_feature():
    """Example of blocking a feature."""
    print("\n=== Blocking a Feature ===\n")

    queue = create_feature_queue()

    # Get a pending feature
    pending = queue.get_pending(limit=1)
    if not pending:
        print("No pending features to demonstrate")
        return

    feature = pending[0]
    print(f"Blocking feature: {feature.id}")

    # Block the feature with a reason
    queue.block_feature(feature.id, reason="Waiting for API endpoint")

    blocked_feature = queue.get_by_id(feature.id)
    print(f"Status: {blocked_feature.status}")
    print(f"Description now includes: {blocked_feature.description}")


def example_adding_feature():
    """Example of adding a new feature."""
    print("\n=== Adding New Feature ===\n")

    queue = create_feature_queue()

    # Create a new feature
    new_feature = Feature(
        id="new-feature-1",
        title="New Feature Title",
        description="This is a new feature added programmatically",
        priority=Priority.HIGH,
        epic="Epic B",
        acceptance_criteria=[
            "AC 1: Feature works as expected",
            "AC 2: Tests pass",
            "AC 3: Documentation updated",
        ],
        test_command="pytest tests/test_new_feature.py",
    )

    # Add it to the queue
    success = queue.add_feature(new_feature)
    if success:
        print(f"Added feature: {new_feature.id}")
        print(f"  Title: {new_feature.title}")
        print(f"  Priority: {new_feature.priority}")
        print(f"  Epic: {new_feature.epic}")
        print(f"  Acceptance Criteria: {len(new_feature.acceptance_criteria)} items")
    else:
        print("Failed to add feature (ID may already exist)")


def example_iteration_workflow():
    """Example of a typical iteration workflow."""
    print("\n=== Typical Iteration Workflow ===\n")

    queue = create_feature_queue()

    # Step 1: Get queue statistics
    stats = queue.get_stats()
    print(f"Queue Status: {stats.pending} pending, {stats.in_progress} in progress")

    # Step 2: Get the next feature to work on
    next_feature = queue.get_next_feature()
    if not next_feature:
        print("No features to work on!")
        return

    print(f"\nPicking up feature: {next_feature.title}")

    # Step 3: Start the feature
    queue.start_feature(next_feature.id, assigned_to="iteration-agent")
    print(f"Started working on: {next_feature.id}")

    # Step 4: Simulate work...
    print(f"Working on feature: {next_feature.title}")
    print(f"  Description: {next_feature.description}")
    print("  Acceptance Criteria:")
    for i, ac in enumerate(next_feature.acceptance_criteria, 1):
        print(f"    {i}. {ac}")

    if next_feature.test_command:
        print(f"  Test Command: {next_feature.test_command}")

    # Step 5: Complete the feature
    queue.complete_feature(next_feature.id)
    print(f"\nCompleted feature: {next_feature.id}")

    # Step 6: Check updated statistics
    stats = queue.get_stats()
    print(
        f"\nUpdated Queue Status: {stats.pending} pending, {stats.in_progress} in progress, {stats.completed} completed"
    )


if __name__ == "__main__":
    # Run all examples
    # Note: These examples assume a features.json file exists
    # Create a test features.json or modify the path as needed

    try:
        example_basic_usage()
        example_get_next_feature()
        example_filtering()
        example_status_transitions()
        example_blocking_feature()
        example_adding_feature()
        example_iteration_workflow()
    except FileNotFoundError:
        print("\nNote: No features.json found. Create one to run the examples.")
        print("\nExample features.json structure:")
        print("""
{
  "version": "1.0",
  "description": "Project features",
  "features": [
    {
      "id": "feature-1",
      "title": "Implement user authentication",
      "description": "Add JWT-based authentication",
      "status": "pending",
      "priority": "high",
      "epic": "Authentication",
      "acceptance_criteria": [
        "Users can log in",
        "Sessions are secure",
        "Tests pass"
      ],
      "test_command": "pytest tests/test_auth.py"
    }
  ]
}
        """)

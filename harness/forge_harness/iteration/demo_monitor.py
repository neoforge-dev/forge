"""Demonstration of Progress Monitor.

This script demonstrates the ProgressMonitor functionality:
- Registering sessions for monitoring
- Detecting completion, timeout, and stall conditions
- Collecting output and extending timeouts
"""

import asyncio
from datetime import UTC, datetime, timedelta

from forge_harness.iteration.monitor import ProgressMonitor


async def demo_basic_usage():
    """Demonstrate basic monitor usage."""
    print("=" * 80)
    print("Demo: Basic Progress Monitor Usage")
    print("=" * 80)

    monitor = ProgressMonitor(poll_interval=2, default_timeout=1)

    # Register a few sessions
    print("\n1. Registering sessions...")
    monitor.register_session("session-1", task_id="HRN-001", timeout_minutes=1)
    monitor.register_session("session-2", task_id="HRN-002", timeout_minutes=2)
    monitor.register_session("session-3", task_id="HRN-003", metadata={"domain": "voice-coach"})

    print(f"   Registered {len(monitor.sessions)} sessions")

    # Display metrics
    print("\n2. Current session metrics:")
    for session_id, metrics in monitor.get_all_metrics().items():
        print(f"   - {session_id}: {metrics.state.value} (timeout: {metrics.timeout_minutes}min)")

    # Get state for specific session
    print("\n3. Check session states:")
    for session_id in ["session-1", "session-2", "session-3"]:
        state = monitor.get_state(session_id)
        print(f"   - {session_id}: {state.value if state else 'not found'}")

    # Demonstrate extending timeout
    print("\n4. Extending timeout for session-1...")
    monitor.extend_timeout("session-1", minutes=5)
    metrics = monitor.get_metrics("session-1")
    print(f"   - Extended until: {metrics.extended_until}")

    # Cleanup
    print("\n5. Unregistering sessions...")
    for session_id in ["session-1", "session-2", "session-3"]:
        monitor.unregister_session(session_id)
    print(f"   Remaining sessions: {len(monitor.sessions)}")


async def demo_completion_detection():
    """Demonstrate completion detection."""
    print("\n" + "=" * 80)
    print("Demo: Completion Detection")
    print("=" * 80)

    monitor = ProgressMonitor()

    # Test completion markers
    completion_outputs = [
        "Running tests...\nAll tests pass\nDone.",
        "Building project...\n✓ Success\nBuild complete.",
        "Deploying...\nDeployment complete\nURL: https://app.example.com",
    ]

    print("\n1. Testing completion markers:")
    for i, output in enumerate(completion_outputs, 1):
        is_complete = monitor.detect_completion(f"session-{i}", output)
        print(f"   - Output {i}: {'✓ Completed' if is_complete else '✗ Not completed'}")
        print(f"     Preview: {output[:50]}...")

    # Test failure markers
    failure_outputs = [
        "Running tests...\nError: Test failed\nExiting.",
        "Building project...\nBUILD FAILED\nSee logs for details.",
        "Running command...\nException: ValueError\nTraceback...",
    ]

    print("\n2. Testing failure markers:")
    for i, output in enumerate(failure_outputs, 1):
        has_failure = monitor._detect_failure(output)
        print(f"   - Output {i}: {'✓ Failure detected' if has_failure else '✗ No failure'}")
        print(f"     Preview: {output[:50]}...")


async def demo_timeout_handling():
    """Demonstrate timeout handling."""
    print("\n" + "=" * 80)
    print("Demo: Timeout Handling")
    print("=" * 80)

    monitor = ProgressMonitor(default_timeout=1)  # 1 minute timeout

    print("\n1. Creating session with old activity...")
    monitor.register_session("old-session")

    # Simulate old activity
    past = datetime.now(UTC) - timedelta(minutes=2)
    monitor.sessions["old-session"].last_activity_at = past

    metrics = monitor.get_metrics("old-session")
    print(f"   - Session age: {metrics.age_minutes:.1f} minutes")
    print(f"   - Idle time: {metrics.idle_minutes:.1f} minutes")
    print(f"   - Timeout threshold: {metrics.timeout_minutes} minutes")
    print(f"   - Is timed out: {metrics.is_timeout}")

    print("\n2. Extending timeout for recovery...")
    monitor.extend_timeout("old-session", minutes=10)
    metrics = monitor.get_metrics("old-session")
    print(f"   - Extended until: {metrics.extended_until}")
    print(f"   - Is timed out now: {metrics.is_timeout}")


async def demo_stall_detection():
    """Demonstrate stall detection."""
    print("\n" + "=" * 80)
    print("Demo: Stall Detection")
    print("=" * 80)

    monitor = ProgressMonitor()

    print("\n1. Creating sessions with different idle times...")

    # Active session
    monitor.register_session("active-session")
    monitor.sessions["active-session"].last_activity_at = datetime.now(UTC)

    # Stalled session (11 minutes idle)
    monitor.register_session("stalled-session")
    past = datetime.now(UTC) - timedelta(minutes=11)
    monitor.sessions["stalled-session"].last_activity_at = past

    print("\n2. Checking for stalls:")
    for session_id in ["active-session", "stalled-session"]:
        is_stalled = monitor.detect_stall(session_id)
        metrics = monitor.get_metrics(session_id)
        print(f"   - {session_id}:")
        print(f"     Idle time: {metrics.idle_minutes:.1f} minutes")
        print(f"     Stalled: {is_stalled}")


async def demo_metrics_export():
    """Demonstrate metrics export."""
    print("\n" + "=" * 80)
    print("Demo: Metrics Export")
    print("=" * 80)

    monitor = ProgressMonitor()
    monitor.register_session(
        "export-test",
        task_id="HRN-999",
        timeout_minutes=20,
        metadata={"domain": "test", "type": "demo"},
    )

    print("\n1. Exporting session metrics to dict:")
    metrics = monitor.get_metrics("export-test")
    metrics_dict = metrics.to_dict()

    print(f"   Session ID: {metrics_dict['session_id']}")
    print(f"   Task ID: {metrics_dict['task_id']}")
    print(f"   State: {metrics_dict['state']}")
    print(f"   Timeout: {metrics_dict['timeout_minutes']} minutes")
    print(f"   Age: {metrics_dict['age_minutes']:.2f} minutes")
    print(f"   Metadata: {metrics_dict['metadata']}")


async def main():
    """Run all demonstrations."""
    print("\n" + "=" * 80)
    print("FORGE Harness - Progress Monitor Demonstration")
    print("=" * 80)

    await demo_basic_usage()
    await demo_completion_detection()
    await demo_timeout_handling()
    await demo_stall_detection()
    await demo_metrics_export()

    print("\n" + "=" * 80)
    print("Demonstration complete!")
    print("=" * 80)
    print("\nFor real usage with tmux sessions:")
    print("  python -m forge_harness.iteration.monitor --watch session-1 session-2")
    print()


if __name__ == "__main__":
    asyncio.run(main())

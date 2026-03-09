"""Example usage of Session Orchestrator.

This demonstrates how to use the SessionOrchestrator to coordinate
iteration tracking, auto-committing, and PR generation.
"""

from forge_harness.iteration.session_orchestrator import create_session_orchestrator


def example_basic_session():
    """Basic session with auto-commit enabled."""
    # Create orchestrator
    orchestrator = create_session_orchestrator(
        project_name="example-project",
        auto_commit=True,
        auto_pr=False,
    )

    # Start iteration
    iteration_num = orchestrator.start_iteration()
    print(f"Started iteration {iteration_num}")

    # Do some work...
    # (implement features, run tests, etc.)

    # Complete iteration with results
    result = orchestrator.complete_iteration(
        features=[
            "Add user authentication",
            "Implement password reset",
            "Add email verification",
        ],
        tests_added=12,
        lines_added=450,
        notes="All tests passing, ready for review",
    )

    if result.success:
        print(f"✓ Iteration {result.iteration_number} completed successfully")
        print(f"  Features: {len(result.features_completed)}")
        print(f"  Duration: {result.duration_seconds:.2f}s")

        if result.commit_result and result.commit_result.success:
            print(f"  Commit: {result.commit_result.commit_hash}")
    else:
        print(f"✗ Iteration failed: {result.error}")

    # View stats
    stats = orchestrator.get_stats()
    print("\nSession stats:")
    print(f"  Total iterations: {stats.total_iterations}")
    print(f"  Completed: {stats.completed_iterations}")
    print(f"  Failed: {stats.failed_iterations}")
    print(f"  Total features: {stats.total_features}")


def example_full_workflow():
    """Full workflow with auto-commit and auto-PR."""
    orchestrator = create_session_orchestrator(
        project_name="voice-coach",
        auto_commit=True,
        auto_pr=True,  # Enable automatic PR creation
    )

    # Configure PR settings
    orchestrator.config.pr_draft = True  # Create as draft
    orchestrator.config.base_branch = "main"
    orchestrator.config.commit_scope = "backend"

    # Start iteration
    iteration_num = orchestrator.start_iteration()
    print(f"Starting iteration {iteration_num}...")

    # Complete with commit and PR
    result = orchestrator.complete_iteration(
        features=[
            "Implement pitch analysis engine",
            "Add voice recording feature",
            "Create feedback generation system",
        ],
        tests_added=25,
        lines_added=1200,
        files_changed=[
            "backend/app/engines/pitch_analyzer.py",
            "backend/app/api/recordings.py",
            "backend/app/services/feedback.py",
        ],
        notes="Major feature release - includes comprehensive testing",
    )

    if result.success:
        print("✓ Iteration completed successfully")

        if result.commit_result:
            print(f"  Commit: {result.commit_result.commit_hash}")
            print(f"  Message: {result.commit_result.message[:80]}...")

        if result.pr_result and result.pr_result.success:
            print(f"  PR created: {result.pr_result.pr_url}")
            print(f"  PR number: #{result.pr_result.pr_number}")
    else:
        print(f"✗ Failed: {result.error}")


def example_multiple_iterations():
    """Multiple iterations in sequence."""
    orchestrator = create_session_orchestrator(
        project_name="interview-simulator",
        auto_commit=True,
    )

    # Iteration 1: Setup
    orchestrator.start_iteration()
    result1 = orchestrator.complete_iteration(
        features=["Setup project structure", "Configure database"],
        tests_added=5,
        lines_added=200,
    )
    print(f"Iteration 1: {'✓' if result1.success else '✗'}")

    # Iteration 2: Core features
    orchestrator.start_iteration()
    result2 = orchestrator.complete_iteration(
        features=["Add question bank", "Implement interview flow"],
        tests_added=15,
        lines_added=800,
    )
    print(f"Iteration 2: {'✓' if result2.success else '✗'}")

    # Iteration 3: Polish
    orchestrator.start_iteration()
    result3 = orchestrator.complete_iteration(
        features=["Add analytics", "Improve UI"],
        tests_added=10,
        lines_added=400,
    )
    print(f"Iteration 3: {'✓' if result3.success else '✗'}")

    # View overall progress
    stats = orchestrator.get_stats()
    print("\nTotal progress:")
    print(f"  Iterations: {stats.completed_iterations}/{stats.total_iterations}")
    print(f"  Features: {stats.total_features}")
    print(f"  Tests: {stats.total_tests}")
    print(f"  Lines: {stats.total_lines:,}")
    print(f"  Avg duration: {stats.avg_duration:.2f}s")


def example_failure_handling():
    """Handling failures and recovery."""
    orchestrator = create_session_orchestrator(
        project_name="example-project",
        auto_commit=True,
    )

    # Start iteration
    orchestrator.start_iteration()

    # Simulate failure
    result = orchestrator.fail_iteration(reason="Tests failed - authentication bug detected")

    print(f"✗ Iteration failed: {result.error}")

    # Start new iteration to fix
    orchestrator.start_iteration()
    result = orchestrator.complete_iteration(
        features=["Fix authentication bug"],
        tests_added=3,
        lines_added=50,
        notes="Bug fixed, all tests passing",
    )

    print("✓ Recovery iteration completed")

    # View recent iterations
    recent = orchestrator.get_recent_iterations(count=5)
    print("\nRecent iterations:")
    for record in recent:
        status_icon = "✓" if record.status == "completed" else "✗"
        print(f"  {status_icon} Iteration {record.iteration_number}: {record.status}")


if __name__ == "__main__":
    print("=== Example 1: Basic Session ===\n")
    example_basic_session()

    print("\n\n=== Example 2: Full Workflow ===\n")
    example_full_workflow()

    print("\n\n=== Example 3: Multiple Iterations ===\n")
    example_multiple_iterations()

    print("\n\n=== Example 4: Failure Handling ===\n")
    example_failure_handling()

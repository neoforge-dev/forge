#!/usr/bin/env python3
"""Demo script for the smart prioritizer.

Shows how to use the prioritizer with real features and assessment data.
"""

from pathlib import Path

from forge_harness.iteration.assess import AssessmentReport, Issue, IssueType, TestResults
from forge_harness.iteration.prioritizer import (
    PrioritizationStrategy,
    find_features_in_portfolio,
    prioritize_tasks,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


def demo_basic_prioritization():
    """Demo basic prioritization without assessment."""
    print("=" * 80)
    print("DEMO: Basic Prioritization (Balanced Strategy)")
    print("=" * 80)

    # Find features in harness directory
    harness_root = Path(__file__).parent.parent.parent
    features_file = harness_root / "features.json"

    if not features_file.exists():
        print(f"Features file not found: {features_file}")
        return

    # Prioritize with balanced strategy
    tasks = prioritize_tasks(
        features_paths=[features_file],
        strategy=PrioritizationStrategy.BALANCED,
    )

    # Show top 10 tasks
    print(f"\nFound {len(tasks)} pending tasks. Top 10:")
    print("-" * 80)

    for i, task in enumerate(tasks[:10], 1):
        print(f"\n{i}. {task.feature_id}: {task.title}")
        print(
            f"   Score: {task.score:.2f} | Impact: {task.impact}/10 | "
            f"Urgency: {task.urgency}/10 | Effort: {task.effort}/5"
        )
        print(f"   Epic: {task.epic or 'None'}")
        print(f"   Reasoning: {task.reasoning}")
        if task.blockers:
            print(f"   ⚠️  Blocked by: {', '.join(task.blockers)}")


def demo_with_failures():
    """Demo prioritization with failing tests boost."""
    print("\n" + "=" * 80)
    print("DEMO: Prioritization with Failure Boosting")
    print("=" * 80)

    harness_root = Path(__file__).parent.parent.parent
    features_file = harness_root / "features.json"

    if not features_file.exists():
        print(f"Features file not found: {features_file}")
        return

    # Create assessment report with failures
    assessment = AssessmentReport()
    assessment.test_results = TestResults(
        total_tests=50,
        passed_count=45,
        failed_count=5,
    )
    assessment.issues.append(
        Issue(
            issue_type=IssueType.FAILED_TEST,
            severity="high",
            message="5 tests failing",
        )
    )

    # Prioritize with failure boosting
    tasks = prioritize_tasks(
        features_paths=[features_file],
        assessment_report=assessment,
        strategy=PrioritizationStrategy.BALANCED,
    )

    # Show tasks that got failure boost
    boosted_tasks = [t for t in tasks if t.metadata.get("failure_boost")]

    print(f"\n{len(boosted_tasks)} tasks boosted due to failures:")
    print("-" * 80)

    for i, task in enumerate(boosted_tasks[:5], 1):
        print(f"\n{i}. {task.feature_id}: {task.title}")
        print(f"   Score: {task.score:.2f} (boosted)")
        print(f"   Reasoning: {task.reasoning}")


def demo_quick_wins():
    """Demo quick-wins strategy (high value, low effort)."""
    print("\n" + "=" * 80)
    print("DEMO: Quick Wins Strategy")
    print("=" * 80)

    harness_root = Path(__file__).parent.parent.parent
    features_file = harness_root / "features.json"

    if not features_file.exists():
        print(f"Features file not found: {features_file}")
        return

    # Prioritize with quick-wins strategy
    tasks = prioritize_tasks(
        features_paths=[features_file],
        strategy=PrioritizationStrategy.QUICK_WINS,
    )

    # Show top 5 quick wins
    print("\nTop 5 Quick Wins (high impact/effort ratio):")
    print("-" * 80)

    for i, task in enumerate(tasks[:5], 1):
        ratio = task.impact_effort_ratio
        print(f"\n{i}. {task.feature_id}: {task.title}")
        print(f"   Impact/Effort Ratio: {ratio:.2f}")
        print(f"   Impact: {task.impact}/10 | Effort: {task.effort}/5")
        print(f"   Score: {task.score:.2f}")


def demo_dependency_first():
    """Demo dependency-first strategy (clear blockers)."""
    print("\n" + "=" * 80)
    print("DEMO: Dependency-First Strategy")
    print("=" * 80)

    harness_root = Path(__file__).parent.parent.parent
    features_file = harness_root / "features.json"

    if not features_file.exists():
        print(f"Features file not found: {features_file}")
        return

    # Prioritize with dependency-first strategy
    tasks = prioritize_tasks(
        features_paths=[features_file],
        strategy=PrioritizationStrategy.DEPENDENCY_FIRST,
    )

    # Show unblocked tasks (no dependencies)
    unblocked = [t for t in tasks if not t.is_blocked]

    print(f"\n{len(unblocked)} unblocked tasks (ready to start):")
    print("-" * 80)

    for i, task in enumerate(unblocked[:5], 1):
        print(f"\n{i}. {task.feature_id}: {task.title}")
        print(f"   Score: {task.score:.2f}")
        print(f"   Dependencies: {len(task.dependencies)}")
        if task.dependencies:
            print(f"   Depends on (completed): {', '.join(task.dependencies)}")


def demo_portfolio_scan():
    """Demo scanning entire portfolio for features."""
    print("\n" + "=" * 80)
    print("DEMO: Portfolio-Wide Prioritization")
    print("=" * 80)

    # Try to find FORGE root
    current = Path.cwd()
    forge_root = None

    if current.name == "FORGE":
        forge_root = current
    else:
        for parent in current.parents:
            if parent.name == "FORGE":
                forge_root = parent
                break

    if not forge_root:
        print("FORGE root not found. Run from within FORGE directory.")
        return

    # Find all features files
    features_files = find_features_in_portfolio(forge_root)
    print(f"\nFound {len(features_files)} features.json files in portfolio")

    # Prioritize across entire portfolio
    tasks = prioritize_tasks(
        features_paths=features_files,
        strategy=PrioritizationStrategy.BALANCED,
        domain_weights={
            "voice-coach": 1.5,
            "interview-simulator": 1.3,
        },
    )

    # Show top tasks by domain
    from collections import defaultdict

    by_domain = defaultdict(list)
    for task in tasks:
        domain = task.metadata.get("domain", "unknown")
        by_domain[domain].append(task)

    print("\nTop task per domain:")
    print("-" * 80)

    for domain in sorted(by_domain.keys()):
        top_task = by_domain[domain][0]
        print(f"\n{domain}:")
        print(f"  {top_task.feature_id}: {top_task.title}")
        print(f"  Score: {top_task.score:.2f} | Weight: {top_task.domain_weight}x")


def main():
    """Run all demos."""
    try:
        demo_basic_prioritization()
        demo_with_failures()
        demo_quick_wins()
        demo_dependency_first()
        demo_portfolio_scan()

        print("\n" + "=" * 80)
        print("All demos completed!")
        print("=" * 80)

    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()

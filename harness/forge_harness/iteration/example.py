#!/usr/bin/env python3
"""
Example usage of the smart task prioritizer.

This script demonstrates how to use the prioritizer to rank tasks
from a features.json file and display the results.
"""

from pathlib import Path

from forge_harness.iteration.prioritizer import prioritize_tasks


def main():
    """Run prioritization example."""
    # Determine features.json path (relative to harness root)
    harness_root = Path(__file__).parent.parent.parent
    features_path = harness_root / "features.json"

    if not features_path.exists():
        print(f"Error: {features_path} not found")
        return

    # Load and prioritize tasks
    print("Loading and prioritizing tasks...")
    tasks = prioritize_tasks(str(features_path))

    # Display results
    print(f"\nFound {len(tasks)} tasks")
    print("=" * 80)

    # Show top 10 tasks
    print("\nTop 10 Prioritized Tasks:")
    print("-" * 80)
    for i, task in enumerate(tasks[:10], 1):
        print(f"\n{i}. [{task.task_id}] {task.name}")
        print(f"   Score: {task.score:.2f}")
        print(
            f"   Impact: {task.impact:.1f} | Urgency: {task.urgency:.1f} | Effort: {task.effort:.1f}"
        )
        print(f"   {task.reasoning}")

    # Show score distribution
    print("\n" + "=" * 80)
    print("Score Distribution:")
    print("-" * 80)

    score_ranges = {
        "Very High (10+)": [t for t in tasks if t.score >= 10],
        "High (5-10)": [t for t in tasks if 5 <= t.score < 10],
        "Medium (3-5)": [t for t in tasks if 3 <= t.score < 5],
        "Low (<3)": [t for t in tasks if t.score < 3],
    }

    for range_name, range_tasks in score_ranges.items():
        count = len(range_tasks)
        percentage = (count / len(tasks) * 100) if tasks else 0
        print(f"{range_name:20} {count:3d} tasks ({percentage:5.1f}%)")

    # Show urgent tasks (with failing tests)
    urgent = [t for t in tasks if t.urgency >= 4]
    if urgent:
        print("\n" + "=" * 80)
        print(f"⚠️  Urgent Tasks ({len(urgent)} tasks with failing tests):")
        print("-" * 80)
        for task in urgent[:5]:
            print(f"  • [{task.task_id}] {task.name} (score: {task.score:.2f})")


if __name__ == "__main__":
    main()

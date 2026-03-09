"""Demo script for ResultAggregator.

Shows how to aggregate results from multiple parallel agents.

Usage:
    python -m forge_harness.iteration.demo_aggregator
"""

from datetime import UTC, datetime, timedelta

from forge_harness.iteration.aggregator import (
    AgentResult,
    ResultAggregator,
    ResultStatus,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


def demo_successful_aggregation():
    """Demo: Aggregate results from successful agents."""
    print("\n" + "=" * 80)
    print("DEMO 1: Successful Aggregation")
    print("=" * 80 + "\n")

    aggregator = ResultAggregator()

    # Create agent results
    started = datetime.now(UTC)
    results = [
        AgentResult(
            agent_id="backend-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            findings=[
                "Implemented ResultAggregator class",
                "Added similarity matching for deduplication",
                "Added conflict detection",
            ],
            started_at=started,
            completed_at=started + timedelta(seconds=120),
        ),
        AgentResult(
            agent_id="backend-2",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            findings=[
                "Implemented ResultAggregator with deduplication",
                "Added comprehensive test coverage",
                "Generated markdown reports",
            ],
            started_at=started + timedelta(seconds=30),
            completed_at=started + timedelta(seconds=180),
        ),
    ]

    # Aggregate results
    report = aggregator.aggregate(results)

    print(f"Task: {report.task_id}")
    print(f"Success Rate: {report.success_rate:.1f}%")
    print(f"Total Duration: {report.duration_seconds:.1f}s")
    print(f"Common Findings: {len(report.common_findings)}")
    print(f"Conflicts: {len(report.conflicts)}")
    print()

    # Print markdown report
    print(report.to_markdown())


def demo_partial_failure():
    """Demo: Aggregate results with partial failures."""
    print("\n" + "=" * 80)
    print("DEMO 2: Partial Failure")
    print("=" * 80 + "\n")

    aggregator = ResultAggregator()

    started = datetime.now(UTC)
    results = [
        AgentResult(
            agent_id="backend-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            findings=["Implemented aggregator", "All tests pass"],
            started_at=started,
            completed_at=started + timedelta(seconds=120),
        ),
        AgentResult(
            agent_id="backend-2",
            task_id="HRN-005",
            status=ResultStatus.FAILURE,
            findings=["Started implementation"],
            errors=["Import error: missing difflib", "Connection timeout"],
            started_at=started + timedelta(seconds=30),
            completed_at=started + timedelta(seconds=90),
        ),
        AgentResult(
            agent_id="qa-1",
            task_id="HRN-005",
            status=ResultStatus.PARTIAL,
            findings=["Completed 15 of 20 tests", "3 tests failing"],
            started_at=started + timedelta(seconds=60),
            completed_at=started + timedelta(seconds=180),
        ),
    ]

    report = aggregator.aggregate(results)

    print(f"Task: {report.task_id}")
    print(f"Total Agents: {report.total_agents}")
    print(f"Successful: {report.successful_agents}")
    print(f"Failed: {report.failed_agents}")
    print(f"Partial: {report.partial_agents}")
    print(f"Success Rate: {report.success_rate:.1f}%")
    print(f"Conflicts Detected: {len(report.conflicts)}")
    print(f"Errors Collected: {len(report.errors)}")
    print()

    # Print markdown report
    print(report.to_markdown())


def demo_conflict_detection():
    """Demo: Detect conflicts between agent results."""
    print("\n" + "=" * 80)
    print("DEMO 3: Conflict Detection")
    print("=" * 80 + "\n")

    aggregator = ResultAggregator(conflict_detection=True)

    started = datetime.now(UTC)
    results = [
        AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            findings=["Tests pass", "Coverage 95%"],
            metadata={"test_count": 39, "coverage": 95.0, "version": "1.0.0"},
            started_at=started,
            completed_at=started + timedelta(seconds=120),
        ),
        AgentResult(
            agent_id="agent-2",
            task_id="HRN-005",
            status=ResultStatus.FAILURE,
            findings=["Tests fail", "Coverage 60%"],
            errors=["5 tests failed"],
            metadata={"test_count": 39, "coverage": 60.0, "version": "1.0.1"},
            started_at=started + timedelta(seconds=30),
            completed_at=started + timedelta(seconds=100),
        ),
    ]

    report = aggregator.aggregate(results)

    print(f"Task: {report.task_id}")
    print(f"Conflicts Detected: {len(report.conflicts)}")
    print()

    # Print conflicts
    for conflict in report.conflicts:
        print(f"Severity: {conflict.severity.value.upper()}")
        print(f"Description: {conflict.description}")
        print(f"Agents: {', '.join(conflict.agent_ids)}")
        if conflict.details:
            print(f"Details: {conflict.details}")
        print()

    # Print markdown report
    print(report.to_markdown())


def demo_deduplication():
    """Demo: Deduplication of similar findings."""
    print("\n" + "=" * 80)
    print("DEMO 4: Finding Deduplication")
    print("=" * 80 + "\n")

    # Test with different similarity thresholds
    for threshold in [0.6, 0.75, 0.9]:
        print(f"\nSimilarity Threshold: {threshold}")
        print("-" * 40)

        aggregator = ResultAggregator(similarity_threshold=threshold)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=[
                    "Fixed the API bug in authentication module",
                    "Added new test for edge case",
                ],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=[
                    "Fixed API bug in authentication",
                    "Added test for edge case scenario",
                ],
            ),
            AgentResult(
                agent_id="agent-3",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=[
                    "Repaired authentication API bug",
                    "Created edge case test",
                ],
            ),
        ]

        report = aggregator.aggregate(results)

        print(f"Common Findings: {len(report.common_findings)}")
        for finding in report.common_findings:
            print(f"  - '{finding.finding}' ({finding.count} agents)")
            print(f"    Similarity: {finding.similarity_score:.2%}")

        unique_count = sum(len(findings) for findings in report.unique_findings.values())
        print(f"Unique Findings: {unique_count}")


def demo_multiple_agents_parallel():
    """Demo: Aggregate results from many parallel agents."""
    print("\n" + "=" * 80)
    print("DEMO 5: Many Parallel Agents")
    print("=" * 80 + "\n")

    aggregator = ResultAggregator()

    started = datetime.now(UTC)

    # Simulate 10 parallel agents working on the same task
    results = []
    for i in range(10):
        status = ResultStatus.SUCCESS if i < 8 else ResultStatus.FAILURE
        agent_findings = [
            f"Agent {i + 1} completed task",
            "Implemented core functionality",
            f"Added {i + 5} tests",
        ]
        errors = [] if status == ResultStatus.SUCCESS else [f"Agent {i + 1} error"]

        results.append(
            AgentResult(
                agent_id=f"agent-{i + 1}",
                task_id="HRN-005",
                status=status,
                findings=agent_findings if status == ResultStatus.SUCCESS else [],
                errors=errors,
                started_at=started + timedelta(seconds=i * 10),
                completed_at=started + timedelta(seconds=i * 10 + 120),
            )
        )

    report = aggregator.aggregate(results)

    print(f"Task: {report.task_id}")
    print(f"Total Agents: {report.total_agents}")
    print(f"Successful: {report.successful_agents}")
    print(f"Failed: {report.failed_agents}")
    print(f"Success Rate: {report.success_rate:.1f}%")
    print(f"Common Findings: {len(report.common_findings)}")
    print()

    # Show top common findings
    print("Top Common Findings:")
    for finding in sorted(report.common_findings, key=lambda x: x.count, reverse=True)[:3]:
        print(f"  - {finding.finding}")
        print(f"    Reported by {finding.count} agents")


def main():
    """Run all demos."""
    print("\n" + "=" * 80)
    print("ResultAggregator Demo")
    print("=" * 80)

    try:
        demo_successful_aggregation()
        demo_partial_failure()
        demo_conflict_detection()
        demo_deduplication()
        demo_multiple_agents_parallel()

        print("\n" + "=" * 80)
        print("All demos completed successfully!")
        print("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Demo script for the FORGE assess module.

Shows comprehensive usage of the assessment system.
"""

import json

from forge_harness.iteration.assess import assess_status


def main():
    """Run assessment and display results."""
    print("=" * 80)
    print("FORGE Harness - Assessment Phase Demo")
    print("=" * 80)
    print()

    # Run assessment
    print("Running assessment...")
    report = assess_status()
    print(f"✓ Assessment complete in {report.duration_seconds:.2f}s")
    print()

    # Display agent status
    print("-" * 80)
    print("AGENT STATUS")
    print("-" * 80)
    if report.agents:
        for agent in report.agents:
            status_icon = "🟢" if agent.is_active else "⚫"
            print(f"{status_icon} {agent.session_name}")
            print(f"   Type: {agent.agent_type.value}")
            print(f"   Active: {agent.is_active}")
            if agent.working_directory:
                print(f"   Directory: {agent.working_directory}")
            if agent.last_activity:
                print(f"   Last Activity: {agent.last_activity.strftime('%Y-%m-%d %H:%M:%S')}")
            print()
    else:
        print("No active agents detected")
        print()

    # Display git status
    print("-" * 80)
    print("GIT STATUS")
    print("-" * 80)
    print(f"Projects scanned: {len(report.git_status.projects)}")
    print(f"Total staged: {report.git_status.total_staged}")
    print(f"Total modified: {report.git_status.total_modified}")
    print(f"Total untracked: {report.git_status.total_untracked}")
    print(f"Total conflicts: {report.git_status.total_conflicts}")
    print()

    if report.git_status.projects:
        print("Projects with changes:")
        for project in report.git_status.projects:
            if project.has_changes:
                print(f"  {project.domain}/{project.project} ({project.branch})")
                if project.staged:
                    print(f"    Staged: {len(project.staged)} files")
                if project.modified:
                    print(f"    Modified: {len(project.modified)} files")
                if project.untracked:
                    print(f"    Untracked: {len(project.untracked)} files")
                if project.conflicts:
                    print(f"    Conflicts: {len(project.conflicts)} files")
                print()

    # Display test results
    print("-" * 80)
    print("TEST RESULTS")
    print("-" * 80)
    if report.test_results.total_tests > 0:
        print(f"Total tests: {report.test_results.total_tests}")
        print(f"Passed: {report.test_results.passed_count}")
        print(f"Failed: {report.test_results.failed_count}")
        print(f"Skipped: {report.test_results.skipped_count}")
        print(f"Success rate: {report.test_results.success_rate * 100:.1f}%")
        if report.test_results.last_run:
            print(f"Last run: {report.test_results.last_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print()

        if report.test_results.failed_tests:
            print("Failed tests:")
            for test in report.test_results.failed_tests[:10]:  # Show first 10
                print(f"  - {test.test_name}")
                if test.message:
                    print(f"    {test.message[:100]}")
            if len(report.test_results.failed_tests) > 10:
                print(f"  ... and {len(report.test_results.failed_tests) - 10} more")
            print()
    else:
        print("No test results available")
        print("(Run tests or check pytest cache)")
        print()

    # Display issues
    print("-" * 80)
    print("ISSUES")
    print("-" * 80)
    if report.issues:
        # Group by severity
        by_severity = {}
        for issue in report.issues:
            if issue.severity not in by_severity:
                by_severity[issue.severity] = []
            by_severity[issue.severity].append(issue)

        for severity in ["critical", "high", "medium", "low"]:
            if severity not in by_severity:
                continue

            issues = by_severity[severity]
            severity_icon = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🔵",
            }[severity]

            print(f"{severity_icon} {severity.upper()}: {len(issues)} issues")
            for issue in issues:
                print(f"  [{issue.issue_type.value}] {issue.message}")
                if issue.location:
                    print(f"    Location: {issue.location}")
            print()
    else:
        print("✓ No issues detected")
        print()

    # Display summary
    print("-" * 80)
    print("SUMMARY")
    print("-" * 80)
    summary = report.get_summary()
    print(f"Active agents: {summary['active_agents']}/{summary['total_agents']}")
    print(f"Projects with changes: {summary['projects_with_changes']}/{summary['total_projects']}")
    print(f"Failed tests: {summary['failed_tests']}/{summary['total_tests']}")
    print(f"Critical issues: {summary['critical_issues']}/{summary['total_issues']}")
    print()

    # Export JSON
    print("-" * 80)
    print("JSON EXPORT")
    print("-" * 80)
    print("Full report available as JSON:")
    print("  report.to_json()")
    print()
    print("Sample:")
    print(json.dumps(report.get_summary(), indent=2))
    print()

    print("=" * 80)
    print(f"Assessment complete: {report.duration_seconds:.2f}s")
    print("=" * 80)


if __name__ == "__main__":
    main()

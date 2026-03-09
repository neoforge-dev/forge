"""Iteration Management for FORGE Harness.

Provides automated assessment, planning, and execution phases
for the FORGE autonomous development loop.

This module implements the core iteration cycle:
1. Assess - Gather current state (agents, git, tests, issues)
2. Plan - Decide on next actions based on assessment
3. Execute - Dispatch work to agents and tools
4. Monitor - Track progress and collect feedback

Usage:
    from forge_harness.iteration import assess_status, AssessmentReport

    # Get current portfolio state
    report = assess_status()

    # Check agent status
    print(f"Active agents: {len(report.agents)}")

    # Check test results
    print(f"Failed tests: {report.test_results.failed_count}")
"""

from forge_harness.iteration.aggregator import (
    AgentResult,
    AggregatedReport,
    ConflictSeverity,
    DuplicateFinding,
    ResultAggregator,
    ResultStatus,
)
from forge_harness.iteration.assess import (
    AgentStatus,
    AssessmentReport,
    GitStatus,
    Issue,
    ProjectStatus,
    TestResults,
    assess_status,
)
from forge_harness.iteration.dispatch import (
    DispatchResult,
    DispatchStatus,
    TmuxDispatcher,
    dispatch_task,
    dispatch_tasks,
    get_dispatch_status,
)
from forge_harness.iteration.journal import (
    EntryType,
    IterationJournal,
    JournalEntry,
)
from forge_harness.iteration.prioritizer import (
    PrioritizationStrategy,
    PrioritizedTask,
    prioritize_tasks,
)

__all__ = [
    # Assessment
    "assess_status",
    "AssessmentReport",
    "AgentStatus",
    "GitStatus",
    "TestResults",
    "Issue",
    "ProjectStatus",
    # Prioritization
    "prioritize_tasks",
    "PrioritizedTask",
    "PrioritizationStrategy",
    # Dispatch
    "dispatch_task",
    "dispatch_tasks",
    "get_dispatch_status",
    "DispatchResult",
    "DispatchStatus",
    "TmuxDispatcher",
    # Journal
    "IterationJournal",
    "JournalEntry",
    "EntryType",
    # Aggregation
    "ResultAggregator",
    "AgentResult",
    "AggregatedReport",
    "ResultStatus",
    "DuplicateFinding",
    "ConflictSeverity",
]

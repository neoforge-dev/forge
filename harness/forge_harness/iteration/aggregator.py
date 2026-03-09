"""Result Aggregator for FORGE Harness.

Combines outputs from multiple parallel agents into a single coherent report.
Handles partial failures, result deduplication, and conflict detection.

Features:
- Aggregate results from 2+ agents into unified report
- Group results by task type and outcome (success/failure/partial)
- Identify and deduplicate common findings using similarity matching
- Highlight conflicts or inconsistencies between agent results
- Generate markdown report with agent attribution

Usage:
    from forge_harness.iteration.aggregator import ResultAggregator, AgentResult

    # Create aggregator
    aggregator = ResultAggregator()

    # Prepare agent results
    results = [
        AgentResult(
            agent_id="backend-1",
            task_id="HRN-001",
            status="success",
            findings=["Fixed API bug", "Added tests"],
        ),
        AgentResult(
            agent_id="qa-1",
            task_id="HRN-001",
            status="success",
            findings=["All tests pass", "Coverage 95%"],
        ),
    ]

    # Aggregate results
    report = aggregator.aggregate(results)
    print(report.to_markdown())
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class ResultStatus(str, Enum):
    """Status of an agent result."""

    SUCCESS = "success"  # Task completed successfully
    FAILURE = "failure"  # Task failed completely
    PARTIAL = "partial"  # Task partially completed
    TIMEOUT = "timeout"  # Task timed out
    ERROR = "error"  # Task encountered error
    UNKNOWN = "unknown"  # Status unknown


class ConflictSeverity(str, Enum):
    """Severity of a conflict between results."""

    CRITICAL = "critical"  # Contradictory results
    WARNING = "warning"  # Inconsistent but not contradictory
    INFO = "info"  # Minor differences


@dataclass
class AgentResult:
    """Result from a single agent's task execution.

    Attributes:
        agent_id: Unique identifier for the agent
        task_id: Task identifier
        status: Result status
        findings: List of findings or outputs
        errors: List of errors encountered
        metadata: Additional metadata
        started_at: Task start timestamp
        completed_at: Task completion timestamp
        output: Full output text (optional)
    """

    agent_id: str
    task_id: str
    status: ResultStatus
    findings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    output: str = ""

    def __post_init__(self):
        """Convert string status to enum if needed."""
        if isinstance(self.status, str):
            self.status = ResultStatus(self.status)

    @property
    def duration_seconds(self) -> float | None:
        """Calculate task duration in seconds."""
        if self.completed_at is None:
            return None
        delta = self.completed_at - self.started_at
        return delta.total_seconds()

    @property
    def is_successful(self) -> bool:
        """Check if result is successful."""
        return self.status == ResultStatus.SUCCESS

    @property
    def has_errors(self) -> bool:
        """Check if result has errors."""
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "findings": self.findings,
            "errors": self.errors,
            "metadata": self.metadata,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class DuplicateFinding:
    """A finding that appears in multiple agent results.

    Attributes:
        finding: The deduplicated finding text
        agent_ids: List of agent IDs that reported this finding
        similarity_score: Average similarity score (0.0-1.0)
        original_texts: Original finding texts from each agent
    """

    finding: str
    agent_ids: list[str]
    similarity_score: float
    original_texts: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of agents that reported this finding."""
        return len(self.agent_ids)


@dataclass
class Conflict:
    """A conflict or inconsistency between agent results.

    Attributes:
        severity: Conflict severity level
        description: Description of the conflict
        agent_ids: Agents involved in conflict
        details: Additional conflict details
    """

    severity: ConflictSeverity
    description: str
    agent_ids: list[str]
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Convert string severity to enum if needed."""
        if isinstance(self.severity, str):
            self.severity = ConflictSeverity(self.severity)


@dataclass
class AggregatedReport:
    """Aggregated report from multiple agent results.

    Attributes:
        task_id: Common task ID
        total_agents: Total number of agents
        successful_agents: Number of successful agents
        failed_agents: Number of failed agents
        partial_agents: Number of partially successful agents
        results_by_status: Results grouped by status
        common_findings: Findings reported by multiple agents
        unique_findings: Findings unique to one agent
        conflicts: Detected conflicts or inconsistencies
        errors: All errors collected
        started_at: Earliest start time
        completed_at: Latest completion time
        metadata: Additional metadata
    """

    task_id: str
    total_agents: int
    successful_agents: int
    failed_agents: int
    partial_agents: int
    results_by_status: dict[ResultStatus, list[AgentResult]]
    common_findings: list[DuplicateFinding]
    unique_findings: dict[str, list[str]]  # agent_id -> findings
    conflicts: list[Conflict]
    errors: list[dict[str, str]]  # agent_id + error
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_agents == 0:
            return 0.0
        return (self.successful_agents / self.total_agents) * 100

    @property
    def duration_seconds(self) -> float | None:
        """Calculate total duration in seconds."""
        if self.started_at is None or self.completed_at is None:
            return None
        delta = self.completed_at - self.started_at
        return delta.total_seconds()

    def to_markdown(self) -> str:
        """Generate markdown report."""
        lines = [
            f"# Aggregated Report: {self.task_id}",
            "",
            "## Summary",
            "",
            f"- **Total Agents**: {self.total_agents}",
            f"- **Successful**: {self.successful_agents} ({self.success_rate:.1f}%)",
            f"- **Failed**: {self.failed_agents}",
            f"- **Partial**: {self.partial_agents}",
        ]

        if self.duration_seconds:
            lines.append(f"- **Duration**: {self.duration_seconds:.1f}s")

        # Results by status
        lines.extend(["", "## Results by Status", ""])
        for status, results in self.results_by_status.items():
            if results:
                lines.append(f"### {status.value.upper()}")
                lines.append("")
                for result in results:
                    duration = (
                        f" ({result.duration_seconds:.1f}s)" if result.duration_seconds else ""
                    )
                    lines.append(f"- **{result.agent_id}**{duration}")
                    if result.findings:
                        for finding in result.findings:
                            lines.append(f"  - {finding}")
                lines.append("")

        # Common findings
        if self.common_findings:
            lines.extend(["## Common Findings", ""])
            lines.append("Findings reported by multiple agents:")
            lines.append("")
            for dup in sorted(self.common_findings, key=lambda x: x.count, reverse=True):
                agents = ", ".join(dup.agent_ids)
                lines.append(f"### {dup.finding}")
                lines.append(f"**Reported by**: {agents} ({dup.count} agents)")
                lines.append(f"**Similarity**: {dup.similarity_score:.2%}")
                lines.append("")

        # Unique findings
        if self.unique_findings:
            lines.extend(["## Unique Findings", ""])
            lines.append("Findings unique to individual agents:")
            lines.append("")
            for agent_id, findings in self.unique_findings.items():
                if findings:
                    lines.append(f"### {agent_id}")
                    for finding in findings:
                        lines.append(f"- {finding}")
                    lines.append("")

        # Conflicts
        if self.conflicts:
            lines.extend(["## Conflicts & Inconsistencies", ""])
            for conflict in sorted(self.conflicts, key=lambda x: x.severity.value):
                icon = (
                    "🔴"
                    if conflict.severity == ConflictSeverity.CRITICAL
                    else "⚠️"
                    if conflict.severity == ConflictSeverity.WARNING
                    else "ℹ️"
                )
                lines.append(
                    f"### {icon} {conflict.severity.value.upper()}: {conflict.description}"
                )
                agents = ", ".join(conflict.agent_ids)
                lines.append(f"**Agents**: {agents}")
                if conflict.details:
                    lines.append("**Details**:")
                    for key, value in conflict.details.items():
                        lines.append(f"- {key}: {value}")
                lines.append("")

        # Errors
        if self.errors:
            lines.extend(["## Errors", ""])
            for error_entry in self.errors:
                agent_id = error_entry.get("agent_id", "unknown")
                error = error_entry.get("error", "")
                lines.append(f"### {agent_id}")
                lines.append("```")
                lines.append(error)
                lines.append("```")
                lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "total_agents": self.total_agents,
            "successful_agents": self.successful_agents,
            "failed_agents": self.failed_agents,
            "partial_agents": self.partial_agents,
            "success_rate": self.success_rate,
            "common_findings_count": len(self.common_findings),
            "conflicts_count": len(self.conflicts),
            "errors_count": len(self.errors),
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class ResultAggregator:
    """Aggregates results from multiple parallel agents.

    Attributes:
        similarity_threshold: Minimum similarity for deduplication (0.0-1.0)
        conflict_detection: Enable conflict detection
    """

    def __init__(
        self,
        similarity_threshold: float = 0.75,
        conflict_detection: bool = True,
    ):
        """Initialize result aggregator.

        Args:
            similarity_threshold: Minimum similarity for deduplication (default: 0.75)
            conflict_detection: Enable conflict detection (default: True)
        """
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0.0 and 1.0")

        self.similarity_threshold = similarity_threshold
        self.conflict_detection = conflict_detection
        logger.info(
            "ResultAggregator initialized",
            extra={
                "similarity_threshold": similarity_threshold,
                "conflict_detection": conflict_detection,
            },
        )

    def aggregate(self, results: list[AgentResult]) -> AggregatedReport:
        """Aggregate results from multiple agents.

        Args:
            results: List of agent results to aggregate

        Returns:
            Aggregated report with deduplicated findings and conflict detection

        Raises:
            ValueError: If results list is empty or has inconsistent task IDs
        """
        if not results:
            raise ValueError("Results list cannot be empty")

        # Validate task IDs
        task_ids = {r.task_id for r in results}
        if len(task_ids) > 1:
            raise ValueError(f"Results have inconsistent task IDs: {task_ids}")

        task_id = results[0].task_id
        logger.info(f"Aggregating {len(results)} results for task {task_id}")

        # Group results by status
        results_by_status = self._group_by_status(results)

        # Count agents by status
        successful = len(results_by_status.get(ResultStatus.SUCCESS, []))
        failed = len(results_by_status.get(ResultStatus.FAILURE, []))
        partial = len(results_by_status.get(ResultStatus.PARTIAL, []))

        # Find common and unique findings
        common_findings, unique_findings = self._deduplicate_findings(results)

        # Detect conflicts
        conflicts = []
        if self.conflict_detection:
            conflicts = self._detect_conflicts(results, results_by_status)

        # Collect errors
        errors = self._collect_errors(results)

        # Calculate time bounds
        started_at = min((r.started_at for r in results), default=None)
        completed_at = max((r.completed_at for r in results if r.completed_at), default=None)

        report = AggregatedReport(
            task_id=task_id,
            total_agents=len(results),
            successful_agents=successful,
            failed_agents=failed,
            partial_agents=partial,
            results_by_status=results_by_status,
            common_findings=common_findings,
            unique_findings=unique_findings,
            conflicts=conflicts,
            errors=errors,
            started_at=started_at,
            completed_at=completed_at,
        )

        logger.info(
            f"Aggregation complete: {report.success_rate:.1f}% success, "
            f"{len(common_findings)} common findings, {len(conflicts)} conflicts"
        )

        return report

    def _group_by_status(self, results: list[AgentResult]) -> dict[ResultStatus, list[AgentResult]]:
        """Group results by status."""
        grouped: dict[ResultStatus, list[AgentResult]] = defaultdict(list)
        for result in results:
            grouped[result.status].append(result)
        return dict(grouped)

    def _deduplicate_findings(
        self, results: list[AgentResult]
    ) -> tuple[list[DuplicateFinding], dict[str, list[str]]]:
        """Deduplicate findings using similarity matching.

        Returns:
            Tuple of (common_findings, unique_findings)
        """
        # Collect all findings with agent attribution
        all_findings: list[tuple[str, str]] = []  # (finding, agent_id)
        for result in results:
            for finding in result.findings:
                all_findings.append((finding, result.agent_id))

        if not all_findings:
            return [], {}

        # Find similar findings
        processed: set[int] = set()
        common_indices: set[int] = set()  # Track indices that are part of common findings
        common_findings: list[DuplicateFinding] = []

        for i, (finding_i, agent_i) in enumerate(all_findings):
            if i in processed:
                continue

            similar_group = [(finding_i, agent_i, 1.0)]  # Include self with 1.0 similarity
            similar_indices = [i]
            processed.add(i)

            for j, (finding_j, agent_j) in enumerate(all_findings[i + 1 :], start=i + 1):
                if j in processed:
                    continue

                similarity = self._calculate_similarity(finding_i, finding_j)
                if similarity >= self.similarity_threshold:
                    similar_group.append((finding_j, agent_j, similarity))
                    similar_indices.append(j)
                    processed.add(j)

            # If multiple agents reported similar findings, it's common
            if len(similar_group) > 1:
                # Use the longest finding as the canonical version
                canonical = max(similar_group, key=lambda x: len(x[0]))[0]
                agent_ids = [agent for _, agent, _ in similar_group]
                original_texts = [finding for finding, _, _ in similar_group]
                avg_similarity = sum(sim for _, _, sim in similar_group) / len(similar_group)

                common_findings.append(
                    DuplicateFinding(
                        finding=canonical,
                        agent_ids=agent_ids,
                        similarity_score=avg_similarity,
                        original_texts=original_texts,
                    )
                )
                # Mark these indices as part of common findings
                common_indices.update(similar_indices)

        # Collect unique findings (not in common findings)
        unique_findings: dict[str, list[str]] = defaultdict(list)

        for i, (finding, agent_id) in enumerate(all_findings):
            if i not in common_indices:
                unique_findings[agent_id].append(finding)

        return common_findings, dict(unique_findings)

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two texts using SequenceMatcher.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score between 0.0 and 1.0
        """
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

    def _detect_conflicts(
        self,
        results: list[AgentResult],
        results_by_status: dict[ResultStatus, list[AgentResult]],
    ) -> list[Conflict]:
        """Detect conflicts and inconsistencies between results.

        Args:
            results: All agent results
            results_by_status: Results grouped by status

        Returns:
            List of detected conflicts
        """
        conflicts: list[Conflict] = []

        # Check for status conflicts (some succeed, some fail)
        success_count = len(results_by_status.get(ResultStatus.SUCCESS, []))
        failure_count = len(results_by_status.get(ResultStatus.FAILURE, []))

        if success_count > 0 and failure_count > 0:
            success_agents = [r.agent_id for r in results_by_status[ResultStatus.SUCCESS]]
            failure_agents = [r.agent_id for r in results_by_status[ResultStatus.FAILURE]]

            conflicts.append(
                Conflict(
                    severity=ConflictSeverity.CRITICAL,
                    description="Contradictory results: some agents succeeded while others failed",
                    agent_ids=success_agents + failure_agents,
                    details={
                        "successful_agents": success_agents,
                        "failed_agents": failure_agents,
                    },
                )
            )

        # Check for conflicting metadata values
        metadata_conflicts = self._detect_metadata_conflicts(results)
        conflicts.extend(metadata_conflicts)

        return conflicts

    def _detect_metadata_conflicts(self, results: list[AgentResult]) -> list[Conflict]:
        """Detect conflicts in metadata values across results.

        Args:
            results: All agent results

        Returns:
            List of metadata conflicts
        """
        conflicts: list[Conflict] = []

        # Collect all metadata keys
        all_keys: set[str] = set()
        for result in results:
            all_keys.update(result.metadata.keys())

        # Check each key for conflicting values
        for key in all_keys:
            values: dict[Any, list[str]] = defaultdict(list)  # value -> agent_ids

            for result in results:
                if key in result.metadata:
                    value = result.metadata[key]
                    # Convert unhashable types to strings for grouping
                    if isinstance(value, (list, dict)):
                        value = str(value)
                    values[value].append(result.agent_id)

            # If multiple different values exist, it's a conflict
            if len(values) > 1:
                severity = ConflictSeverity.WARNING
                description = f"Inconsistent metadata value for '{key}'"
                agent_ids = [agent for agents in values.values() for agent in agents]

                conflicts.append(
                    Conflict(
                        severity=severity,
                        description=description,
                        agent_ids=agent_ids,
                        details={
                            "key": key,
                            "values": {str(k): v for k, v in values.items()},
                        },
                    )
                )

        return conflicts

    def _collect_errors(self, results: list[AgentResult]) -> list[dict[str, str]]:
        """Collect all errors from results.

        Args:
            results: All agent results

        Returns:
            List of error entries with agent attribution
        """
        errors: list[dict[str, str]] = []

        for result in results:
            for error in result.errors:
                errors.append({"agent_id": result.agent_id, "error": error})

        return errors

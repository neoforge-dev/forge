"""
FORGE Harness Result Aggregator
================================

Collects and summarizes results from multiple agent operations.

Usage:
    from forge_harness.iteration.result_aggregator import ResultAggregator, AggregatedResult
    from forge_harness.iteration.monitor import MonitorResult, SessionStatus

    aggregator = ResultAggregator()

    # Add results as they come in
    aggregator.add_result(result1)
    aggregator.add_result(result2)

    # Get aggregated summary
    aggregated = aggregator.aggregate()
    print(f"Success: {aggregated.success_count}, Failed: {aggregated.failure_count}")

    # Or aggregate a list directly
    aggregated = ResultAggregator.aggregate_all(results)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .dispatcher import DispatchResult, DispatchStatus
from .monitor import MonitorResult, SessionStatus

logger = logging.getLogger(__name__)


class ResultType(str, Enum):
    """Type of result being aggregated."""

    MONITOR = "monitor"
    DISPATCH = "dispatch"

    def __str__(self) -> str:
        return self.value


@dataclass
class ResultEntry:
    """Normalized entry for aggregation."""

    session_id: str
    result_type: ResultType
    success: bool
    status: str
    duration: float = 0.0
    error_message: str | None = None
    raw_result: MonitorResult | DispatchResult | None = None

    @classmethod
    def from_monitor_result(cls, result: MonitorResult) -> "ResultEntry":
        """Create from MonitorResult."""
        success = result.status == SessionStatus.COMPLETED
        error_msg = None
        if result.status in (SessionStatus.ERROR, SessionStatus.STALLED, SessionStatus.TIMEOUT):
            error_msg = f"Session {result.status.value}"
            if result.status == SessionStatus.TIMEOUT:
                error_msg = f"Timeout after {result.duration:.1f}s"
            elif result.status == SessionStatus.STALLED:
                error_msg = "Session stalled (no activity)"

        return cls(
            session_id=result.session_id,
            result_type=ResultType.MONITOR,
            success=success,
            status=result.status.value,
            duration=result.duration,
            error_message=error_msg,
            raw_result=result,
        )

    @classmethod
    def from_dispatch_result(cls, result: DispatchResult) -> "ResultEntry":
        """Create from DispatchResult."""
        success = result.status == DispatchStatus.COMPLETED
        error_msg = None
        if result.status == DispatchStatus.FAILED:
            error_msg = f"Dispatch to {result.agent_type} failed"

        return cls(
            session_id=result.session_id,
            result_type=ResultType.DISPATCH,
            success=success,
            status=result.status.value,
            duration=0.0,  # DispatchResult doesn't track duration
            error_message=error_msg,
            raw_result=result,
        )


@dataclass
class AggregatedResult:
    """Aggregated results from multiple operations."""

    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    stalled_count: int = 0
    pending_count: int = 0

    results: dict[str, ResultEntry] = field(default_factory=dict)  # session_id -> ResultEntry

    total_duration: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def total_count(self) -> int:
        """Total number of results."""
        return len(self.results)

    @property
    def success_rate(self) -> float:
        """Success rate as percentage (0-100)."""
        if self.total_count == 0:
            return 0.0
        return (self.success_count / self.total_count) * 100

    @property
    def all_succeeded(self) -> bool:
        """True if all results were successful."""
        return self.failure_count == 0 and self.timeout_count == 0 and self.success_count > 0

    @property
    def has_failures(self) -> bool:
        """True if any results failed."""
        return self.failure_count > 0 or self.timeout_count > 0 or self.stalled_count > 0

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = ["# Aggregated Results", ""]

        # Status line
        status = (
            "All Succeeded"
            if self.all_succeeded
            else "Has Failures"
            if self.has_failures
            else "In Progress"
        )
        lines.append(f"**Status:** {status}")
        lines.append(f"**Success Rate:** {self.success_rate:.1f}%")
        lines.append("")

        # Counts
        lines.append("## Counts")
        lines.append(f"- Total: {self.total_count}")
        lines.append(f"- Succeeded: {self.success_count}")
        lines.append(f"- Failed: {self.failure_count}")
        if self.timeout_count:
            lines.append(f"- Timed Out: {self.timeout_count}")
        if self.stalled_count:
            lines.append(f"- Stalled: {self.stalled_count}")
        if self.pending_count:
            lines.append(f"- Pending: {self.pending_count}")
        lines.append("")

        # Duration
        if self.total_duration > 0:
            lines.append(f"**Total Duration:** {self.total_duration:.1f}s")
            if self.success_count > 0:
                avg_duration = self.total_duration / self.success_count
                lines.append(f"**Avg Duration (successful):** {avg_duration:.1f}s")
            lines.append("")

        # Failures detail
        failures = [e for e in self.results.values() if not e.success]
        if failures:
            lines.append("## Failures")
            for entry in failures:
                lines.append(f"- **{entry.session_id}**: {entry.error_message or entry.status}")
            lines.append("")

        return "\n".join(lines)


class ResultAggregator:
    """Collects and aggregates results from multiple agent operations."""

    def __init__(self):
        """Initialize the aggregator."""
        self._entries: dict[str, ResultEntry] = {}
        self._started_at: datetime | None = None

    def add_result(
        self,
        result: MonitorResult | DispatchResult,
    ) -> ResultEntry:
        """Add a result to the aggregator.

        Args:
            result: MonitorResult or DispatchResult to add

        Returns:
            Normalized ResultEntry
        """
        if self._started_at is None:
            self._started_at = datetime.now()

        if isinstance(result, MonitorResult):
            entry = ResultEntry.from_monitor_result(result)
        elif isinstance(result, DispatchResult):
            entry = ResultEntry.from_dispatch_result(result)
        else:
            raise TypeError(f"Unsupported result type: {type(result)}")

        self._entries[entry.session_id] = entry
        logger.debug(f"Added result for {entry.session_id}: {entry.status}")
        return entry

    def add_results(
        self,
        results: list[MonitorResult | DispatchResult],
    ) -> list[ResultEntry]:
        """Add multiple results.

        Args:
            results: List of results to add

        Returns:
            List of normalized ResultEntries
        """
        return [self.add_result(r) for r in results]

    def remove_result(self, session_id: str) -> ResultEntry | None:
        """Remove a result by session ID.

        Args:
            session_id: Session to remove

        Returns:
            Removed entry or None
        """
        return self._entries.pop(session_id, None)

    def get_result(self, session_id: str) -> ResultEntry | None:
        """Get a specific result by session ID.

        Args:
            session_id: Session to look up

        Returns:
            ResultEntry or None
        """
        return self._entries.get(session_id)

    def clear(self) -> None:
        """Clear all results."""
        self._entries.clear()
        self._started_at = None

    def aggregate(self) -> AggregatedResult:
        """Generate aggregated results.

        Returns:
            AggregatedResult with all statistics
        """
        now = datetime.now()

        success_count = 0
        failure_count = 0
        timeout_count = 0
        stalled_count = 0
        pending_count = 0
        total_duration = 0.0

        for entry in self._entries.values():
            if entry.success:
                success_count += 1
            elif entry.status == "timeout":
                timeout_count += 1
            elif entry.status == "stalled":
                stalled_count += 1
            elif entry.status in ("pending", "running"):
                pending_count += 1
            else:
                failure_count += 1

            total_duration += entry.duration

        return AggregatedResult(
            success_count=success_count,
            failure_count=failure_count,
            timeout_count=timeout_count,
            stalled_count=stalled_count,
            pending_count=pending_count,
            results=dict(self._entries),
            total_duration=total_duration,
            started_at=self._started_at,
            completed_at=now,
        )

    def get_failures(self) -> list[ResultEntry]:
        """Get all failed results.

        Returns:
            List of failed ResultEntries
        """
        return [
            e
            for e in self._entries.values()
            if not e.success and e.status not in ("pending", "running")
        ]

    def get_successes(self) -> list[ResultEntry]:
        """Get all successful results.

        Returns:
            List of successful ResultEntries
        """
        return [e for e in self._entries.values() if e.success]

    def get_timeouts(self) -> list[ResultEntry]:
        """Get all timed out results.

        Returns:
            List of timed out ResultEntries
        """
        return [e for e in self._entries.values() if e.status == "timeout"]

    def get_pending(self) -> list[ResultEntry]:
        """Get all pending/running results.

        Returns:
            List of pending ResultEntries
        """
        return [e for e in self._entries.values() if e.status in ("pending", "running")]

    def generate_report(self, include_raw: bool = False) -> str:
        """Generate a detailed report.

        Args:
            include_raw: Include raw result data

        Returns:
            Markdown-formatted report
        """
        agg = self.aggregate()
        lines = [agg.summary]

        # Add session details
        if self._entries:
            lines.append("## Session Details")
            lines.append("")

            # Group by status
            for status_group, label in [
                (self.get_successes(), "### Succeeded"),
                (self.get_failures(), "### Failed"),
                (self.get_timeouts(), "### Timed Out"),
                (self.get_pending(), "### Pending"),
            ]:
                if status_group:
                    lines.append(label)
                    for entry in status_group:
                        lines.append(f"- **{entry.session_id}**")
                        lines.append(f"  - Type: {entry.result_type.value}")
                        lines.append(f"  - Status: {entry.status}")
                        if entry.duration > 0:
                            lines.append(f"  - Duration: {entry.duration:.1f}s")
                        if entry.error_message:
                            lines.append(f"  - Error: {entry.error_message}")
                    lines.append("")

        return "\n".join(lines)

    @classmethod
    def aggregate_all(
        cls,
        results: list[MonitorResult | DispatchResult],
    ) -> AggregatedResult:
        """Aggregate a list of results directly.

        Convenience method for one-shot aggregation.

        Args:
            results: List of results to aggregate

        Returns:
            AggregatedResult
        """
        aggregator = cls()
        aggregator.add_results(results)
        return aggregator.aggregate()

    @classmethod
    def from_monitor_results(
        cls,
        results: dict[str, MonitorResult],
    ) -> "ResultAggregator":
        """Create aggregator from monitor results dict.

        Args:
            results: Dict of session_id -> MonitorResult

        Returns:
            Populated ResultAggregator
        """
        aggregator = cls()
        for result in results.values():
            aggregator.add_result(result)
        return aggregator


# Convenience function
def aggregate_results(
    results: list[MonitorResult | DispatchResult],
) -> AggregatedResult:
    """Aggregate a list of results.

    Args:
        results: List of MonitorResult or DispatchResult

    Returns:
        AggregatedResult with statistics
    """
    return ResultAggregator.aggregate_all(results)

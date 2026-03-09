"""Tests for result aggregator module."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from forge_harness.iteration.dispatcher import AgentType, DispatchResult, DispatchStatus
from forge_harness.iteration.monitor import MonitorResult, SessionStatus
from forge_harness.iteration.result_aggregator import (
    AggregatedResult,
    ResultAggregator,
    ResultEntry,
    ResultType,
    aggregate_results,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def completed_monitor_result():
    """Create a completed MonitorResult."""
    return MonitorResult(
        session_id="forge:tech",
        status=SessionStatus.COMPLETED,
        output="Task completed successfully",
        duration=120.5,
        completion_marker="Completed",
    )


@pytest.fixture
def failed_monitor_result():
    """Create a failed MonitorResult."""
    return MonitorResult(
        session_id="forge:cursor",
        status=SessionStatus.ERROR,
        output="Error occurred",
        duration=30.0,
    )


@pytest.fixture
def timeout_monitor_result():
    """Create a timed out MonitorResult."""
    return MonitorResult(
        session_id="forge:content",
        status=SessionStatus.TIMEOUT,
        output="Still running...",
        duration=900.0,
    )


@pytest.fixture
def stalled_monitor_result():
    """Create a stalled MonitorResult."""
    return MonitorResult(
        session_id="forge:qa",
        status=SessionStatus.STALLED,
        output="No progress",
        duration=450.0,
    )


@pytest.fixture
def completed_dispatch_result():
    """Create a completed DispatchResult."""
    return DispatchResult(
        task_id="HRN-001",
        session_id="codex-hrn001",
        agent_type=AgentType.CODEX,
        started_at=datetime.now(UTC),
        status=DispatchStatus.COMPLETED,
        prompt="Implement feature",
    )


@pytest.fixture
def failed_dispatch_result():
    """Create a failed DispatchResult."""
    return DispatchResult(
        task_id="HRN-002",
        session_id="opencode-hrn002",
        agent_type=AgentType.OPENCODE,
        started_at=datetime.now(UTC),
        status=DispatchStatus.FAILED,
        prompt="Fix bug",
    )


# =============================================================================
# TestResultEntry
# =============================================================================


class TestResultEntry:
    """Tests for ResultEntry dataclass."""

    def test_from_monitor_result_completed(self, completed_monitor_result):
        """ResultEntry from completed MonitorResult."""
        entry = ResultEntry.from_monitor_result(completed_monitor_result)

        assert entry.session_id == "forge:tech"
        assert entry.result_type == ResultType.MONITOR
        assert entry.success is True
        assert entry.status == "completed"
        assert entry.duration == 120.5
        assert entry.error_message is None

    def test_from_monitor_result_error(self, failed_monitor_result):
        """ResultEntry from errored MonitorResult."""
        entry = ResultEntry.from_monitor_result(failed_monitor_result)

        assert entry.session_id == "forge:cursor"
        assert entry.success is False
        assert entry.status == "error"
        assert entry.error_message is not None

    def test_from_monitor_result_timeout(self, timeout_monitor_result):
        """ResultEntry from timed out MonitorResult."""
        entry = ResultEntry.from_monitor_result(timeout_monitor_result)

        assert entry.success is False
        assert entry.status == "timeout"
        assert "Timeout" in entry.error_message
        assert "900" in entry.error_message

    def test_from_monitor_result_stalled(self, stalled_monitor_result):
        """ResultEntry from stalled MonitorResult."""
        entry = ResultEntry.from_monitor_result(stalled_monitor_result)

        assert entry.success is False
        assert entry.status == "stalled"
        assert "stalled" in entry.error_message.lower()

    def test_from_dispatch_result_completed(self, completed_dispatch_result):
        """ResultEntry from completed DispatchResult."""
        entry = ResultEntry.from_dispatch_result(completed_dispatch_result)

        assert entry.session_id == "codex-hrn001"
        assert entry.result_type == ResultType.DISPATCH
        assert entry.success is True
        assert entry.status == "completed"

    def test_from_dispatch_result_failed(self, failed_dispatch_result):
        """ResultEntry from failed DispatchResult."""
        entry = ResultEntry.from_dispatch_result(failed_dispatch_result)

        assert entry.success is False
        assert entry.status == "failed"
        assert "failed" in entry.error_message.lower()


# =============================================================================
# TestAggregatedResult
# =============================================================================


class TestAggregatedResult:
    """Tests for AggregatedResult dataclass."""

    def test_aggregated_result_defaults(self):
        """AggregatedResult has correct defaults."""
        result = AggregatedResult()

        assert result.success_count == 0
        assert result.failure_count == 0
        assert result.total_count == 0
        assert result.success_rate == 0.0

    def test_total_count(self):
        """total_count returns correct value."""
        result = AggregatedResult(
            success_count=3,
            failure_count=2,
            results={
                "a": MagicMock(),
                "b": MagicMock(),
                "c": MagicMock(),
                "d": MagicMock(),
                "e": MagicMock(),
            },
        )

        assert result.total_count == 5

    def test_success_rate(self):
        """success_rate calculates correctly."""
        result = AggregatedResult(
            success_count=3,
            failure_count=2,
            results={
                "a": MagicMock(),
                "b": MagicMock(),
                "c": MagicMock(),
                "d": MagicMock(),
                "e": MagicMock(),
            },
        )

        assert result.success_rate == 60.0

    def test_all_succeeded_true(self):
        """all_succeeded returns True when all passed."""
        result = AggregatedResult(
            success_count=5,
            failure_count=0,
            timeout_count=0,
            results={"a": MagicMock()},
        )

        assert result.all_succeeded is True

    def test_all_succeeded_false_with_failures(self):
        """all_succeeded returns False when has failures."""
        result = AggregatedResult(
            success_count=3,
            failure_count=2,
            results={"a": MagicMock()},
        )

        assert result.all_succeeded is False

    def test_all_succeeded_false_with_timeouts(self):
        """all_succeeded returns False when has timeouts."""
        result = AggregatedResult(
            success_count=3,
            timeout_count=1,
            results={"a": MagicMock()},
        )

        assert result.all_succeeded is False

    def test_has_failures_true(self):
        """has_failures returns True when failures exist."""
        result = AggregatedResult(failure_count=1)
        assert result.has_failures is True

    def test_has_failures_with_timeout(self):
        """has_failures returns True when timeouts exist."""
        result = AggregatedResult(timeout_count=1)
        assert result.has_failures is True

    def test_has_failures_false(self):
        """has_failures returns False when no failures."""
        result = AggregatedResult(success_count=5)
        assert result.has_failures is False

    def test_summary_all_succeeded(self):
        """summary shows success message."""
        result = AggregatedResult(
            success_count=2,
            failure_count=0,
            results={"a": MagicMock(), "b": MagicMock()},
        )

        summary = result.summary
        assert "Succeeded" in summary
        assert "100.0%" in summary

    def test_summary_with_failures(self):
        """summary shows failure message."""
        entry = ResultEntry(
            session_id="forge:tech",
            result_type=ResultType.MONITOR,
            success=False,
            status="error",
            error_message="Something broke",
        )
        result = AggregatedResult(
            success_count=2,
            failure_count=1,
            results={"forge:tech": entry},
        )

        summary = result.summary
        assert "Failure" in summary


# =============================================================================
# TestResultAggregator
# =============================================================================


class TestResultAggregator:
    """Tests for ResultAggregator class."""

    @pytest.fixture
    def aggregator(self):
        """Create empty aggregator."""
        return ResultAggregator()

    def test_add_monitor_result(self, aggregator, completed_monitor_result):
        """add_result accepts MonitorResult."""
        entry = aggregator.add_result(completed_monitor_result)

        assert entry.session_id == "forge:tech"
        assert entry.success is True
        assert len(aggregator._entries) == 1

    def test_add_dispatch_result(self, aggregator, completed_dispatch_result):
        """add_result accepts DispatchResult."""
        entry = aggregator.add_result(completed_dispatch_result)

        assert entry.session_id == "codex-hrn001"
        assert entry.success is True

    def test_add_results_multiple(
        self, aggregator, completed_monitor_result, failed_monitor_result
    ):
        """add_results handles multiple results."""
        entries = aggregator.add_results([completed_monitor_result, failed_monitor_result])

        assert len(entries) == 2
        assert len(aggregator._entries) == 2

    def test_remove_result(self, aggregator, completed_monitor_result):
        """remove_result removes entry."""
        aggregator.add_result(completed_monitor_result)
        removed = aggregator.remove_result("forge:tech")

        assert removed is not None
        assert len(aggregator._entries) == 0

    def test_remove_result_not_found(self, aggregator):
        """remove_result returns None for missing."""
        removed = aggregator.remove_result("nonexistent")
        assert removed is None

    def test_get_result(self, aggregator, completed_monitor_result):
        """get_result returns correct entry."""
        aggregator.add_result(completed_monitor_result)
        entry = aggregator.get_result("forge:tech")

        assert entry is not None
        assert entry.session_id == "forge:tech"

    def test_clear(self, aggregator, completed_monitor_result):
        """clear removes all entries."""
        aggregator.add_result(completed_monitor_result)
        aggregator.clear()

        assert len(aggregator._entries) == 0

    def test_aggregate_mixed_results(
        self,
        aggregator,
        completed_monitor_result,
        failed_monitor_result,
        timeout_monitor_result,
    ):
        """aggregate handles mixed results."""
        aggregator.add_results(
            [
                completed_monitor_result,
                failed_monitor_result,
                timeout_monitor_result,
            ]
        )

        result = aggregator.aggregate()

        assert result.success_count == 1
        assert result.failure_count == 1
        assert result.timeout_count == 1
        assert result.total_count == 3

    def test_get_failures(
        self,
        aggregator,
        completed_monitor_result,
        failed_monitor_result,
    ):
        """get_failures returns only failures."""
        aggregator.add_results([completed_monitor_result, failed_monitor_result])

        failures = aggregator.get_failures()

        assert len(failures) == 1
        assert failures[0].session_id == "forge:cursor"

    def test_get_successes(
        self,
        aggregator,
        completed_monitor_result,
        failed_monitor_result,
    ):
        """get_successes returns only successes."""
        aggregator.add_results([completed_monitor_result, failed_monitor_result])

        successes = aggregator.get_successes()

        assert len(successes) == 1
        assert successes[0].session_id == "forge:tech"

    def test_get_timeouts(
        self,
        aggregator,
        completed_monitor_result,
        timeout_monitor_result,
    ):
        """get_timeouts returns only timeouts."""
        aggregator.add_results([completed_monitor_result, timeout_monitor_result])

        timeouts = aggregator.get_timeouts()

        assert len(timeouts) == 1
        assert timeouts[0].session_id == "forge:content"

    def test_generate_report(
        self,
        aggregator,
        completed_monitor_result,
        failed_monitor_result,
    ):
        """generate_report creates markdown report."""
        aggregator.add_results([completed_monitor_result, failed_monitor_result])

        report = aggregator.generate_report()

        assert "# Aggregated Results" in report
        assert "forge:tech" in report
        assert "forge:cursor" in report
        assert "Succeeded" in report
        assert "Failed" in report

    def test_aggregate_all_classmethod(
        self,
        completed_monitor_result,
        failed_monitor_result,
    ):
        """aggregate_all class method works."""
        result = ResultAggregator.aggregate_all(
            [
                completed_monitor_result,
                failed_monitor_result,
            ]
        )

        assert result.success_count == 1
        assert result.failure_count == 1

    def test_from_monitor_results(self, completed_monitor_result, failed_monitor_result):
        """from_monitor_results creates populated aggregator."""
        results_dict = {
            "forge:tech": completed_monitor_result,
            "forge:cursor": failed_monitor_result,
        }

        aggregator = ResultAggregator.from_monitor_results(results_dict)
        result = aggregator.aggregate()

        assert result.total_count == 2


# =============================================================================
# TestConvenienceFunction
# =============================================================================


class TestAggregateResultsFunction:
    """Tests for aggregate_results convenience function."""

    def test_aggregate_results_function(
        self,
        completed_monitor_result,
        failed_monitor_result,
    ):
        """aggregate_results function works."""
        result = aggregate_results(
            [
                completed_monitor_result,
                failed_monitor_result,
            ]
        )

        assert isinstance(result, AggregatedResult)
        assert result.total_count == 2


# =============================================================================
# TestTimeoutHandling
# =============================================================================


class TestTimeoutHandling:
    """Tests for timeout and partial result handling."""

    def test_partial_results(self):
        """Handles partial results gracefully."""
        results = [
            MonitorResult(
                session_id="a",
                status=SessionStatus.COMPLETED,
                duration=10.0,
            ),
            MonitorResult(
                session_id="b",
                status=SessionStatus.RUNNING,
                duration=5.0,
            ),
        ]

        agg = aggregate_results(results)

        assert agg.success_count == 1
        assert agg.pending_count == 1

    def test_mixed_result_types(self):
        """Handles mixed MonitorResult and DispatchResult."""
        results = [
            MonitorResult(
                session_id="monitor-1",
                status=SessionStatus.COMPLETED,
                duration=60.0,
            ),
            DispatchResult(
                task_id="task-1",
                session_id="dispatch-1",
                agent_type=AgentType.CODEX,
                started_at=datetime.now(UTC),
                status=DispatchStatus.COMPLETED,
                prompt="test",
            ),
        ]

        agg = aggregate_results(results)

        assert agg.success_count == 2
        assert agg.total_count == 2


# =============================================================================
# TestEdgeCases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_results(self):
        """Handles empty result list."""
        agg = aggregate_results([])

        assert agg.total_count == 0
        assert agg.success_rate == 0.0
        assert agg.all_succeeded is False

    def test_duplicate_session_ids(self):
        """Later result overwrites earlier for same session."""
        aggregator = ResultAggregator()

        aggregator.add_result(
            MonitorResult(
                session_id="forge:tech",
                status=SessionStatus.RUNNING,
                duration=10.0,
            )
        )
        aggregator.add_result(
            MonitorResult(
                session_id="forge:tech",
                status=SessionStatus.COMPLETED,
                duration=60.0,
            )
        )

        result = aggregator.aggregate()

        assert result.total_count == 1
        assert result.success_count == 1

    def test_unsupported_result_type(self):
        """Raises TypeError for unsupported types."""
        aggregator = ResultAggregator()

        with pytest.raises(TypeError):
            aggregator.add_result("not a result")

    def test_total_duration_calculation(self):
        """total_duration sums all durations."""
        results = [
            MonitorResult(session_id="a", status=SessionStatus.COMPLETED, duration=10.0),
            MonitorResult(session_id="b", status=SessionStatus.COMPLETED, duration=20.0),
            MonitorResult(session_id="c", status=SessionStatus.COMPLETED, duration=30.0),
        ]

        agg = aggregate_results(results)

        assert agg.total_duration == 60.0

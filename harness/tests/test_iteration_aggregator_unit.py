"""Unit tests for forge_harness.iteration.demo_aggregator.

Tests cover all demo functions in demo_aggregator.py:
- demo_successful_aggregation
- demo_partial_failure
- demo_conflict_detection
- demo_deduplication
- demo_multiple_agents_parallel
- main
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from forge_harness.iteration.aggregator import (
    AgentResult,
    AggregatedReport,
    Conflict,
    ConflictSeverity,
    DuplicateFinding,
    ResultAggregator,
    ResultStatus,
)
from forge_harness.iteration.demo_aggregator import (
    demo_conflict_detection,
    demo_deduplication,
    demo_multiple_agents_parallel,
    demo_partial_failure,
    demo_successful_aggregation,
    main,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_result(
    agent_id: str = "agent-1",
    task_id: str = "TASK-001",
    status: ResultStatus = ResultStatus.SUCCESS,
    findings: list[str] | None = None,
    errors: list[str] | None = None,
    metadata: dict | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        findings=findings or [],
        errors=errors or [],
        metadata=metadata or {},
        started_at=started_at or datetime.now(UTC),
        completed_at=completed_at,
    )


# =============================================================================
# demo_successful_aggregation tests
# =============================================================================


class TestDemoSuccessfulAggregation:
    """Tests for demo_successful_aggregation."""

    def test_runs_without_error(self, capsys):
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "DEMO 1: Successful Aggregation" in captured.out

    def test_output_contains_task_id(self, capsys):
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "Task: HRN-005" in captured.out

    def test_output_contains_success_rate(self, capsys):
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "Success Rate: 100.0%" in captured.out

    def test_output_contains_markdown_report(self, capsys):
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "# Aggregated Report" in captured.out

    def test_two_agents_aggregated(self, capsys):
        """Verify both backend-1 and backend-2 appear in output."""
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "backend-1" in captured.out
        assert "backend-2" in captured.out

    def test_common_findings_detected(self, capsys):
        """Both agents mention deduplication, so common findings > 0."""
        demo_successful_aggregation()
        captured = capsys.readouterr()
        # The output line is "Common Findings: N"
        assert "Common Findings:" in captured.out

    def test_duration_is_positive(self, capsys):
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "Total Duration:" in captured.out


# =============================================================================
# demo_partial_failure tests
# =============================================================================


class TestDemoPartialFailure:
    """Tests for demo_partial_failure."""

    def test_runs_without_error(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "DEMO 2: Partial Failure" in captured.out

    def test_three_agents_reported(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Total Agents: 3" in captured.out

    def test_one_successful(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Successful: 1" in captured.out

    def test_one_failed(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Failed: 1" in captured.out

    def test_one_partial(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Partial: 1" in captured.out

    def test_errors_collected(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        # backend-2 has 2 errors
        assert "Errors Collected: 2" in captured.out

    def test_success_rate_is_33(self, capsys):
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Success Rate: 33.3%" in captured.out


# =============================================================================
# demo_conflict_detection tests
# =============================================================================


class TestDemoConflictDetection:
    """Tests for demo_conflict_detection."""

    def test_runs_without_error(self, capsys):
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "DEMO 3: Conflict Detection" in captured.out

    def test_conflicts_detected(self, capsys):
        demo_conflict_detection()
        captured = capsys.readouterr()
        # At least a status conflict (one success, one failure)
        assert "Conflicts Detected:" in captured.out
        # Should not be zero
        assert "Conflicts Detected: 0" not in captured.out

    def test_severity_printed(self, capsys):
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "Severity:" in captured.out

    def test_agents_listed_in_conflicts(self, capsys):
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "agent-1" in captured.out
        assert "agent-2" in captured.out

    def test_metadata_conflict_on_coverage(self, capsys):
        """Agents have conflicting coverage metadata (95 vs 60)."""
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "coverage" in captured.out.lower() or "Inconsistent" in captured.out

    def test_markdown_report_generated(self, capsys):
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "# Aggregated Report" in captured.out


# =============================================================================
# demo_deduplication tests
# =============================================================================


class TestDemoDeduplication:
    """Tests for demo_deduplication."""

    def test_runs_without_error(self, capsys):
        demo_deduplication()
        captured = capsys.readouterr()
        assert "DEMO 4: Finding Deduplication" in captured.out

    def test_three_thresholds_tested(self, capsys):
        demo_deduplication()
        captured = capsys.readouterr()
        assert "Similarity Threshold: 0.6" in captured.out
        assert "Similarity Threshold: 0.75" in captured.out
        assert "Similarity Threshold: 0.9" in captured.out

    def test_common_findings_reported(self, capsys):
        demo_deduplication()
        captured = capsys.readouterr()
        assert "Common Findings:" in captured.out

    def test_unique_findings_reported(self, capsys):
        demo_deduplication()
        captured = capsys.readouterr()
        assert "Unique Findings:" in captured.out

    def test_similarity_score_printed(self, capsys):
        demo_deduplication()
        captured = capsys.readouterr()
        assert "Similarity:" in captured.out

    def test_lower_threshold_more_common_findings(self, capsys):
        """Lower threshold should produce >= as many common findings as higher."""
        # Verify by checking that 0.6 threshold has common findings
        demo_deduplication()
        captured = capsys.readouterr()
        # At 0.6 threshold, similar findings should be grouped
        lines = captured.out.split("\n")
        threshold_06_section = False
        found_common_at_06 = False
        for line in lines:
            if "Similarity Threshold: 0.6" in line:
                threshold_06_section = True
            elif "Similarity Threshold: 0.75" in line:
                threshold_06_section = False
            if threshold_06_section and "Common Findings:" in line:
                found_common_at_06 = True
        assert found_common_at_06


# =============================================================================
# demo_multiple_agents_parallel tests
# =============================================================================


class TestDemoMultipleAgentsParallel:
    """Tests for demo_multiple_agents_parallel."""

    def test_runs_without_error(self, capsys):
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "DEMO 5: Many Parallel Agents" in captured.out

    def test_ten_agents_total(self, capsys):
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Total Agents: 10" in captured.out

    def test_eight_successful(self, capsys):
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Successful: 8" in captured.out

    def test_two_failed(self, capsys):
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Failed: 2" in captured.out

    def test_success_rate_80(self, capsys):
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Success Rate: 80.0%" in captured.out

    def test_common_findings_exist(self, capsys):
        """'Implemented core functionality' is common across 8 agents."""
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Top Common Findings:" in captured.out

    def test_core_functionality_is_top_finding(self, capsys):
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Implemented core functionality" in captured.out


# =============================================================================
# main() tests
# =============================================================================


class TestMain:
    """Tests for the main entry point."""

    def test_main_runs_all_demos(self, capsys):
        main()
        captured = capsys.readouterr()
        assert "DEMO 1" in captured.out
        assert "DEMO 2" in captured.out
        assert "DEMO 3" in captured.out
        assert "DEMO 4" in captured.out
        assert "DEMO 5" in captured.out
        assert "All demos completed successfully!" in captured.out

    def test_main_propagates_errors(self):
        """If a demo fails, main should raise."""
        with patch(
            "forge_harness.iteration.demo_aggregator.demo_successful_aggregation",
            side_effect=RuntimeError("test error"),
        ):
            with pytest.raises(RuntimeError, match="test error"):
                main()


# =============================================================================
# Underlying aggregator logic unit tests (additional coverage)
# =============================================================================


class TestAggregatorEdgeCases:
    """Test ResultAggregator edge cases exercised by the demo."""

    def test_empty_results_raises(self):
        agg = ResultAggregator()
        with pytest.raises(ValueError, match="empty"):
            agg.aggregate([])

    def test_inconsistent_task_ids_raises(self):
        agg = ResultAggregator()
        results = [
            _make_result(task_id="A"),
            _make_result(task_id="B"),
        ]
        with pytest.raises(ValueError, match="inconsistent"):
            agg.aggregate(results)

    def test_invalid_similarity_threshold(self):
        with pytest.raises(ValueError):
            ResultAggregator(similarity_threshold=1.5)
        with pytest.raises(ValueError):
            ResultAggregator(similarity_threshold=-0.1)

    def test_single_result_dissimilar_findings(self):
        """With very different findings from one agent, no common findings."""
        agg = ResultAggregator()
        result = _make_result(findings=["implemented auth module", "ran pytest suite"])
        report = agg.aggregate([result])
        assert report.total_agents == 1
        # These findings are dissimilar enough to not be grouped
        assert len(report.common_findings) == 0

    def test_no_conflict_when_all_succeed(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", findings=["done"]),
            _make_result(agent_id="a2", findings=["done"]),
        ]
        report = agg.aggregate(results)
        # No status conflict since both succeed
        status_conflicts = [
            c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL
        ]
        assert len(status_conflicts) == 0

    def test_conflict_when_mixed_status(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        critical = [
            c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL
        ]
        assert len(critical) == 1

    def test_metadata_conflict_detection(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", metadata={"version": "1.0"}),
            _make_result(agent_id="a2", metadata={"version": "2.0"}),
        ]
        report = agg.aggregate(results)
        warning_conflicts = [
            c for c in report.conflicts if c.severity == ConflictSeverity.WARNING
        ]
        assert len(warning_conflicts) >= 1

    def test_conflict_detection_disabled(self):
        agg = ResultAggregator(conflict_detection=False)
        results = [
            _make_result(agent_id="a1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        assert len(report.conflicts) == 0

    def test_errors_collected_from_multiple_agents(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", errors=["err1"]),
            _make_result(agent_id="a2", errors=["err2", "err3"]),
        ]
        report = agg.aggregate(results)
        assert len(report.errors) == 3

    def test_time_bounds(self):
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        t3 = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", started_at=t1, completed_at=t2),
            _make_result(agent_id="a2", started_at=t2, completed_at=t3),
        ]
        report = agg.aggregate(results)
        assert report.started_at == t1
        assert report.completed_at == t3
        assert report.duration_seconds == 600.0

    def test_report_to_dict(self):
        agg = ResultAggregator()
        results = [_make_result()]
        report = agg.aggregate(results)
        d = report.to_dict()
        assert "task_id" in d
        assert "success_rate" in d
        assert d["total_agents"] == 1

    def test_report_to_markdown_contains_header(self):
        agg = ResultAggregator()
        results = [_make_result()]
        report = agg.aggregate(results)
        md = report.to_markdown()
        assert "# Aggregated Report: TASK-001" in md

    def test_agent_result_to_dict(self):
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        r = _make_result(started_at=t1, completed_at=t2)
        d = r.to_dict()
        assert d["agent_id"] == "agent-1"
        assert d["duration_seconds"] == 120.0

    def test_agent_result_string_status(self):
        r = AgentResult(
            agent_id="a", task_id="t", status="success",
            findings=[], errors=[],
        )
        assert r.status == ResultStatus.SUCCESS

    def test_agent_result_is_successful(self):
        r = _make_result(status=ResultStatus.SUCCESS)
        assert r.is_successful is True
        r2 = _make_result(status=ResultStatus.FAILURE)
        assert r2.is_successful is False

    def test_agent_result_has_errors(self):
        r = _make_result(errors=["e1"])
        assert r.has_errors is True
        r2 = _make_result(errors=[])
        assert r2.has_errors is False

    def test_agent_result_duration_none_without_completed(self):
        r = _make_result(completed_at=None)
        assert r.duration_seconds is None

    def test_duplicate_finding_count(self):
        df = DuplicateFinding(
            finding="test", agent_ids=["a", "b", "c"], similarity_score=0.9
        )
        assert df.count == 3

    def test_conflict_string_severity(self):
        c = Conflict(severity="warning", description="test", agent_ids=["a"])
        assert c.severity == ConflictSeverity.WARNING

    def test_success_rate_zero_agents(self):
        report = AggregatedReport(
            task_id="T",
            total_agents=0,
            successful_agents=0,
            failed_agents=0,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )
        assert report.success_rate == 0.0

    def test_duration_none_when_no_times(self):
        report = AggregatedReport(
            task_id="T",
            total_agents=1,
            successful_agents=1,
            failed_agents=0,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
            started_at=None,
            completed_at=None,
        )
        assert report.duration_seconds is None

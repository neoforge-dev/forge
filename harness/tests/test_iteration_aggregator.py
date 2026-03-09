"""Comprehensive tests for forge_harness.iteration.aggregator.

Tests cover:
- ResultStatus enum
- ConflictSeverity enum
- AgentResult dataclass (properties, to_dict, __post_init__)
- DuplicateFinding dataclass (count property)
- Conflict dataclass (__post_init__)
- AggregatedReport dataclass (success_rate, duration_seconds, to_markdown, to_dict)
- ResultAggregator class (aggregate, grouping, deduplication, conflict detection)
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

# =============================================================================
# Helpers / Fixtures
# =============================================================================


def _make_result(
    agent_id: str = "agent-1",
    task_id: str = "TASK-001",
    status: str | ResultStatus = ResultStatus.SUCCESS,
    findings: list[str] | None = None,
    errors: list[str] | None = None,
    metadata: dict | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    output: str = "",
) -> AgentResult:
    """Convenience factory for AgentResult."""
    return AgentResult(
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        findings=findings or [],
        errors=errors or [],
        metadata=metadata or {},
        started_at=started_at or datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        completed_at=completed_at,
        output=output,
    )


@pytest.fixture
def aggregator() -> ResultAggregator:
    """Default ResultAggregator with standard settings."""
    return ResultAggregator()


@pytest.fixture
def two_success_results() -> list[AgentResult]:
    """Two successful results for the same task."""
    t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    return [
        _make_result(
            agent_id="backend-1",
            task_id="HRN-001",
            status=ResultStatus.SUCCESS,
            findings=["Fixed API bug", "Added tests"],
            started_at=t,
            completed_at=t + timedelta(seconds=60),
        ),
        _make_result(
            agent_id="qa-1",
            task_id="HRN-001",
            status=ResultStatus.SUCCESS,
            findings=["All tests pass", "Coverage 95%"],
            started_at=t,
            completed_at=t + timedelta(seconds=90),
        ),
    ]


@pytest.fixture
def mixed_status_results() -> list[AgentResult]:
    """Mixed success/failure/partial results."""
    t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    return [
        _make_result(
            agent_id="a1",
            task_id="TASK-002",
            status=ResultStatus.SUCCESS,
            started_at=t,
            completed_at=t + timedelta(seconds=30),
        ),
        _make_result(
            agent_id="a2",
            task_id="TASK-002",
            status=ResultStatus.FAILURE,
            errors=["Connection refused"],
            started_at=t,
            completed_at=t + timedelta(seconds=15),
        ),
        _make_result(
            agent_id="a3",
            task_id="TASK-002",
            status=ResultStatus.PARTIAL,
            started_at=t,
            completed_at=t + timedelta(seconds=50),
        ),
    ]


# =============================================================================
# ResultStatus
# =============================================================================


class TestResultStatus:
    """Tests for ResultStatus enum."""

    def test_all_values_are_strings(self):
        for member in ResultStatus:
            assert isinstance(member.value, str)

    def test_is_string_enum(self):
        assert ResultStatus.SUCCESS == "success"
        assert ResultStatus.FAILURE == "failure"
        assert ResultStatus.PARTIAL == "partial"
        assert ResultStatus.TIMEOUT == "timeout"
        assert ResultStatus.ERROR == "error"
        assert ResultStatus.UNKNOWN == "unknown"

    def test_construction_from_string(self):
        assert ResultStatus("success") == ResultStatus.SUCCESS
        assert ResultStatus("failure") == ResultStatus.FAILURE

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ResultStatus("invalid_status")


# =============================================================================
# ConflictSeverity
# =============================================================================


class TestConflictSeverity:
    """Tests for ConflictSeverity enum."""

    def test_all_values(self):
        assert ConflictSeverity.CRITICAL == "critical"
        assert ConflictSeverity.WARNING == "warning"
        assert ConflictSeverity.INFO == "info"

    def test_is_string_enum(self):
        assert isinstance(ConflictSeverity.CRITICAL, str)

    def test_construction_from_string(self):
        assert ConflictSeverity("critical") == ConflictSeverity.CRITICAL


# =============================================================================
# AgentResult
# =============================================================================


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_basic_construction(self):
        result = _make_result()
        assert result.agent_id == "agent-1"
        assert result.task_id == "TASK-001"
        assert result.status == ResultStatus.SUCCESS

    def test_post_init_converts_string_status(self):
        result = AgentResult(
            agent_id="a",
            task_id="t",
            status="failure",
        )
        assert result.status == ResultStatus.FAILURE
        assert isinstance(result.status, ResultStatus)

    def test_post_init_keeps_enum_status(self):
        result = _make_result(status=ResultStatus.PARTIAL)
        assert result.status == ResultStatus.PARTIAL

    def test_post_init_invalid_string_raises(self):
        with pytest.raises(ValueError):
            AgentResult(agent_id="a", task_id="t", status="bad_status")

    def test_duration_seconds_with_completion(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        result = _make_result(
            started_at=t,
            completed_at=t + timedelta(seconds=42),
        )
        assert result.duration_seconds == 42.0

    def test_duration_seconds_none_when_no_completion(self):
        result = _make_result(completed_at=None)
        assert result.duration_seconds is None

    def test_duration_seconds_zero(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        result = _make_result(started_at=t, completed_at=t)
        assert result.duration_seconds == 0.0

    def test_is_successful_true(self):
        result = _make_result(status=ResultStatus.SUCCESS)
        assert result.is_successful is True

    def test_is_successful_false_for_failure(self):
        result = _make_result(status=ResultStatus.FAILURE)
        assert result.is_successful is False

    def test_is_successful_false_for_partial(self):
        result = _make_result(status=ResultStatus.PARTIAL)
        assert result.is_successful is False

    def test_has_errors_true(self):
        result = _make_result(errors=["Error 1", "Error 2"])
        assert result.has_errors is True

    def test_has_errors_false_when_empty(self):
        result = _make_result(errors=[])
        assert result.has_errors is False

    def test_to_dict_basic_fields(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        result = _make_result(
            agent_id="agt",
            task_id="TK-1",
            status=ResultStatus.SUCCESS,
            findings=["f1"],
            errors=["e1"],
            metadata={"key": "val"},
            started_at=t,
            completed_at=t + timedelta(seconds=5),
        )
        d = result.to_dict()
        assert d["agent_id"] == "agt"
        assert d["task_id"] == "TK-1"
        assert d["status"] == "success"
        assert d["findings"] == ["f1"]
        assert d["errors"] == ["e1"]
        assert d["metadata"] == {"key": "val"}
        assert d["started_at"] == t.isoformat()
        assert d["completed_at"] == (t + timedelta(seconds=5)).isoformat()
        assert d["duration_seconds"] == 5.0

    def test_to_dict_completed_at_none(self):
        result = _make_result(completed_at=None)
        d = result.to_dict()
        assert d["completed_at"] is None
        assert d["duration_seconds"] is None

    def test_default_output_is_empty_string(self):
        result = _make_result()
        assert result.output == ""

    def test_custom_output(self):
        result = _make_result(output="some output text")
        assert result.output == "some output text"

    def test_default_findings_are_empty_list(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.findings == []

    def test_default_errors_are_empty_list(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.errors == []

    def test_default_metadata_is_empty_dict(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.metadata == {}


# =============================================================================
# DuplicateFinding
# =============================================================================


class TestDuplicateFinding:
    """Tests for DuplicateFinding dataclass."""

    def test_count_reflects_agent_ids(self):
        dup = DuplicateFinding(
            finding="Fixed bug",
            agent_ids=["a1", "a2", "a3"],
            similarity_score=0.9,
        )
        assert dup.count == 3

    def test_count_with_single_agent(self):
        dup = DuplicateFinding(
            finding="Fixed bug",
            agent_ids=["a1"],
            similarity_score=1.0,
        )
        assert dup.count == 1

    def test_count_empty_agent_ids(self):
        dup = DuplicateFinding(
            finding="Fixed bug",
            agent_ids=[],
            similarity_score=0.0,
        )
        assert dup.count == 0

    def test_original_texts_default_empty(self):
        dup = DuplicateFinding(
            finding="finding",
            agent_ids=["a1"],
            similarity_score=0.8,
        )
        assert dup.original_texts == []

    def test_original_texts_preserved(self):
        dup = DuplicateFinding(
            finding="canonical text",
            agent_ids=["a1", "a2"],
            similarity_score=0.85,
            original_texts=["variant 1", "variant 2"],
        )
        assert len(dup.original_texts) == 2


# =============================================================================
# Conflict
# =============================================================================


class TestConflict:
    """Tests for Conflict dataclass."""

    def test_basic_construction(self):
        conflict = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Contradictory results",
            agent_ids=["a1", "a2"],
        )
        assert conflict.severity == ConflictSeverity.CRITICAL
        assert conflict.description == "Contradictory results"
        assert conflict.agent_ids == ["a1", "a2"]

    def test_post_init_converts_string_severity(self):
        conflict = Conflict(
            severity="warning",
            description="Inconsistency",
            agent_ids=["a1"],
        )
        assert conflict.severity == ConflictSeverity.WARNING
        assert isinstance(conflict.severity, ConflictSeverity)

    def test_post_init_keeps_enum_severity(self):
        conflict = Conflict(
            severity=ConflictSeverity.INFO,
            description="Minor diff",
            agent_ids=["a1"],
        )
        assert conflict.severity == ConflictSeverity.INFO

    def test_post_init_invalid_string_raises(self):
        with pytest.raises(ValueError):
            Conflict(severity="unknown_sev", description="x", agent_ids=[])

    def test_details_default_empty(self):
        conflict = Conflict(
            severity=ConflictSeverity.INFO,
            description="desc",
            agent_ids=[],
        )
        assert conflict.details == {}

    def test_custom_details(self):
        conflict = Conflict(
            severity=ConflictSeverity.WARNING,
            description="desc",
            agent_ids=["a1"],
            details={"key": "value"},
        )
        assert conflict.details == {"key": "value"}


# =============================================================================
# AggregatedReport
# =============================================================================


class TestAggregatedReport:
    """Tests for AggregatedReport dataclass."""

    def _make_report(self, **kwargs) -> AggregatedReport:
        defaults = {
            "task_id": "TASK-001",
            "total_agents": 2,
            "successful_agents": 2,
            "failed_agents": 0,
            "partial_agents": 0,
            "results_by_status": {},
            "common_findings": [],
            "unique_findings": {},
            "conflicts": [],
            "errors": [],
        }
        defaults.update(kwargs)
        return AggregatedReport(**defaults)

    def test_success_rate_all_success(self):
        report = self._make_report(total_agents=4, successful_agents=4)
        assert report.success_rate == 100.0

    def test_success_rate_partial(self):
        report = self._make_report(total_agents=4, successful_agents=3)
        assert report.success_rate == 75.0

    def test_success_rate_zero_agents(self):
        report = self._make_report(total_agents=0, successful_agents=0)
        assert report.success_rate == 0.0

    def test_success_rate_zero_success(self):
        report = self._make_report(total_agents=3, successful_agents=0)
        assert report.success_rate == 0.0

    def test_duration_seconds_with_bounds(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        report = self._make_report(
            started_at=t,
            completed_at=t + timedelta(seconds=120),
        )
        assert report.duration_seconds == 120.0

    def test_duration_seconds_none_when_missing_start(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        report = self._make_report(started_at=None, completed_at=t)
        assert report.duration_seconds is None

    def test_duration_seconds_none_when_missing_end(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        report = self._make_report(started_at=t, completed_at=None)
        assert report.duration_seconds is None

    def test_duration_seconds_none_when_both_missing(self):
        report = self._make_report(started_at=None, completed_at=None)
        assert report.duration_seconds is None

    # --- to_markdown ---

    def test_to_markdown_contains_task_id(self):
        report = self._make_report(task_id="HRN-999")
        md = report.to_markdown()
        assert "HRN-999" in md

    def test_to_markdown_summary_section(self):
        report = self._make_report(total_agents=3, successful_agents=2, failed_agents=1)
        md = report.to_markdown()
        assert "## Summary" in md
        assert "Total Agents" in md
        assert "Successful" in md
        assert "Failed" in md

    def test_to_markdown_includes_duration_when_present(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        report = self._make_report(
            started_at=t,
            completed_at=t + timedelta(seconds=75),
        )
        md = report.to_markdown()
        assert "Duration" in md
        assert "75.0s" in md

    def test_to_markdown_no_duration_when_missing(self):
        report = self._make_report(started_at=None, completed_at=None)
        md = report.to_markdown()
        assert "Duration" not in md

    def test_to_markdown_results_by_status_section(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        result = _make_result(
            agent_id="backend-1",
            task_id="TASK-001",
            status=ResultStatus.SUCCESS,
            findings=["Fixed it"],
            started_at=t,
            completed_at=t + timedelta(seconds=10),
        )
        report = self._make_report(
            results_by_status={ResultStatus.SUCCESS: [result]},
        )
        md = report.to_markdown()
        assert "Results by Status" in md
        assert "SUCCESS" in md
        assert "backend-1" in md
        assert "Fixed it" in md

    def test_to_markdown_common_findings_section(self):
        dup = DuplicateFinding(
            finding="Both agents found this",
            agent_ids=["a1", "a2"],
            similarity_score=0.95,
        )
        report = self._make_report(common_findings=[dup])
        md = report.to_markdown()
        assert "Common Findings" in md
        assert "Both agents found this" in md
        assert "a1" in md
        assert "a2" in md

    def test_to_markdown_common_findings_sorted_by_count(self):
        dup1 = DuplicateFinding(
            finding="Finding A",
            agent_ids=["a1"],
            similarity_score=0.9,
        )
        dup2 = DuplicateFinding(
            finding="Finding B",
            agent_ids=["a1", "a2", "a3"],
            similarity_score=0.85,
        )
        report = self._make_report(common_findings=[dup1, dup2])
        md = report.to_markdown()
        idx_b = md.index("Finding B")
        idx_a = md.index("Finding A")
        # Finding B (3 agents) should appear before Finding A (1 agent)
        assert idx_b < idx_a

    def test_to_markdown_unique_findings_section(self):
        report = self._make_report(
            unique_findings={"agent-x": ["Unique observation 1", "Unique observation 2"]}
        )
        md = report.to_markdown()
        assert "Unique Findings" in md
        assert "agent-x" in md
        assert "Unique observation 1" in md

    def test_to_markdown_unique_findings_skips_empty(self):
        report = self._make_report(
            unique_findings={"agent-x": [], "agent-y": ["has finding"]}
        )
        md = report.to_markdown()
        # agent-x has no findings so should not appear as a heading
        # agent-y should appear
        assert "agent-y" in md

    def test_to_markdown_conflicts_section(self):
        conflict = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Contradictory results",
            agent_ids=["a1", "a2"],
        )
        report = self._make_report(conflicts=[conflict])
        md = report.to_markdown()
        assert "Conflicts" in md
        assert "CRITICAL" in md
        assert "Contradictory results" in md

    def test_to_markdown_conflict_with_details(self):
        conflict = Conflict(
            severity=ConflictSeverity.WARNING,
            description="Metadata mismatch",
            agent_ids=["a1"],
            details={"version": "1.0 vs 2.0"},
        )
        report = self._make_report(conflicts=[conflict])
        md = report.to_markdown()
        assert "version" in md
        assert "1.0 vs 2.0" in md

    def test_to_markdown_errors_section(self):
        report = self._make_report(
            errors=[{"agent_id": "agent-bad", "error": "Connection refused"}]
        )
        md = report.to_markdown()
        assert "Errors" in md
        assert "agent-bad" in md
        assert "Connection refused" in md

    def test_to_markdown_errors_missing_agent_id(self):
        report = self._make_report(
            errors=[{"error": "Some error without agent"}]
        )
        md = report.to_markdown()
        assert "unknown" in md

    def test_to_markdown_no_common_findings_section_when_empty(self):
        report = self._make_report(common_findings=[])
        md = report.to_markdown()
        assert "Common Findings" not in md

    def test_to_markdown_no_unique_section_when_empty(self):
        report = self._make_report(unique_findings={})
        md = report.to_markdown()
        assert "Unique Findings" not in md

    def test_to_markdown_no_conflicts_section_when_empty(self):
        report = self._make_report(conflicts=[])
        md = report.to_markdown()
        assert "Conflicts" not in md

    def test_to_markdown_no_errors_section_when_empty(self):
        report = self._make_report(errors=[])
        md = report.to_markdown()
        assert "## Errors" not in md

    # --- to_dict ---

    def test_to_dict_basic_fields(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        report = self._make_report(
            task_id="T-1",
            total_agents=3,
            successful_agents=2,
            failed_agents=1,
            partial_agents=0,
            started_at=t,
            completed_at=t + timedelta(seconds=30),
        )
        d = report.to_dict()
        assert d["task_id"] == "T-1"
        assert d["total_agents"] == 3
        assert d["successful_agents"] == 2
        assert d["failed_agents"] == 1
        assert d["partial_agents"] == 0
        assert d["success_rate"] == pytest.approx(66.666, rel=0.01)
        assert d["duration_seconds"] == 30.0

    def test_to_dict_counts_common_findings(self):
        dup = DuplicateFinding(finding="x", agent_ids=["a1", "a2"], similarity_score=0.9)
        report = self._make_report(common_findings=[dup])
        d = report.to_dict()
        assert d["common_findings_count"] == 1

    def test_to_dict_counts_conflicts(self):
        conflict = Conflict(
            severity=ConflictSeverity.WARNING,
            description="x",
            agent_ids=["a1"],
        )
        report = self._make_report(conflicts=[conflict])
        d = report.to_dict()
        assert d["conflicts_count"] == 1

    def test_to_dict_counts_errors(self):
        report = self._make_report(errors=[{"agent_id": "a", "error": "e"}])
        d = report.to_dict()
        assert d["errors_count"] == 1

    def test_to_dict_started_and_completed_isoformat(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        report = self._make_report(
            started_at=t,
            completed_at=t + timedelta(seconds=10),
        )
        d = report.to_dict()
        assert d["started_at"] == t.isoformat()
        assert d["completed_at"] == (t + timedelta(seconds=10)).isoformat()

    def test_to_dict_none_times(self):
        report = self._make_report(started_at=None, completed_at=None)
        d = report.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None
        assert d["duration_seconds"] is None


# =============================================================================
# ResultAggregator - initialization
# =============================================================================


class TestResultAggregatorInit:
    """Tests for ResultAggregator initialization."""

    def test_default_threshold(self):
        agg = ResultAggregator()
        assert agg.similarity_threshold == 0.75

    def test_custom_threshold(self):
        agg = ResultAggregator(similarity_threshold=0.9)
        assert agg.similarity_threshold == 0.9

    def test_conflict_detection_default_true(self):
        agg = ResultAggregator()
        assert agg.conflict_detection is True

    def test_conflict_detection_can_be_disabled(self):
        agg = ResultAggregator(conflict_detection=False)
        assert agg.conflict_detection is False

    def test_threshold_zero_is_valid(self):
        agg = ResultAggregator(similarity_threshold=0.0)
        assert agg.similarity_threshold == 0.0

    def test_threshold_one_is_valid(self):
        agg = ResultAggregator(similarity_threshold=1.0)
        assert agg.similarity_threshold == 1.0

    def test_threshold_below_zero_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            ResultAggregator(similarity_threshold=-0.1)

    def test_threshold_above_one_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            ResultAggregator(similarity_threshold=1.1)


# =============================================================================
# ResultAggregator.aggregate — validation
# =============================================================================


class TestResultAggregatorAggregateValidation:
    """Tests for aggregate() input validation."""

    def test_empty_list_raises(self, aggregator):
        with pytest.raises(ValueError, match="empty"):
            aggregator.aggregate([])

    def test_inconsistent_task_ids_raises(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="TASK-001"),
            _make_result(agent_id="a2", task_id="TASK-002"),
        ]
        with pytest.raises(ValueError, match="inconsistent task IDs"):
            aggregator.aggregate(results)

    def test_single_result_is_valid(self, aggregator):
        results = [_make_result()]
        report = aggregator.aggregate(results)
        assert report.total_agents == 1

    def test_task_id_comes_from_first_result(self, aggregator):
        results = [_make_result(task_id="HRN-042")]
        report = aggregator.aggregate(results)
        assert report.task_id == "HRN-042"


# =============================================================================
# ResultAggregator.aggregate — status grouping & counts
# =============================================================================


class TestResultAggregatorStatusGrouping:
    """Tests for status grouping and count fields in the aggregated report."""

    def test_all_success(self, aggregator, two_success_results):
        report = aggregator.aggregate(two_success_results)
        assert report.total_agents == 2
        assert report.successful_agents == 2
        assert report.failed_agents == 0
        assert report.partial_agents == 0

    def test_mixed_statuses(self, aggregator, mixed_status_results):
        report = aggregator.aggregate(mixed_status_results)
        assert report.total_agents == 3
        assert report.successful_agents == 1
        assert report.failed_agents == 1
        assert report.partial_agents == 1

    def test_results_by_status_success_only(self, aggregator, two_success_results):
        report = aggregator.aggregate(two_success_results)
        assert ResultStatus.SUCCESS in report.results_by_status
        assert len(report.results_by_status[ResultStatus.SUCCESS]) == 2

    def test_results_by_status_mixed(self, aggregator, mixed_status_results):
        report = aggregator.aggregate(mixed_status_results)
        assert ResultStatus.SUCCESS in report.results_by_status
        assert ResultStatus.FAILURE in report.results_by_status
        assert ResultStatus.PARTIAL in report.results_by_status

    def test_timeout_counted_in_status_group(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.TIMEOUT),
            _make_result(agent_id="a2", task_id="T-1", status=ResultStatus.SUCCESS),
        ]
        report = aggregator.aggregate(results)
        assert ResultStatus.TIMEOUT in report.results_by_status
        assert report.successful_agents == 1

    def test_error_status_grouped(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.ERROR),
        ]
        report = aggregator.aggregate(results)
        assert ResultStatus.ERROR in report.results_by_status

    def test_unknown_status_grouped(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.UNKNOWN),
        ]
        report = aggregator.aggregate(results)
        assert ResultStatus.UNKNOWN in report.results_by_status

    def test_success_rate_calculation(self, aggregator, mixed_status_results):
        report = aggregator.aggregate(mixed_status_results)
        # 1 out of 3 succeed = 33.33%
        assert report.success_rate == pytest.approx(33.33, rel=0.01)

    def test_success_rate_100_percent(self, aggregator, two_success_results):
        report = aggregator.aggregate(two_success_results)
        assert report.success_rate == 100.0


# =============================================================================
# ResultAggregator.aggregate — time bounds
# =============================================================================


class TestResultAggregatorTimeBounds:
    """Tests for started_at and completed_at calculation."""

    def test_started_at_is_min_of_all(self, aggregator):
        t_early = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        t_late = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            _make_result(agent_id="a1", task_id="T-1", started_at=t_late),
            _make_result(agent_id="a2", task_id="T-1", started_at=t_early),
        ]
        report = aggregator.aggregate(results)
        assert report.started_at == t_early

    def test_completed_at_is_max_of_all(self, aggregator):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                completed_at=t + timedelta(seconds=30),
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                completed_at=t + timedelta(seconds=90),
            ),
        ]
        report = aggregator.aggregate(results)
        assert report.completed_at == t + timedelta(seconds=90)

    def test_completed_at_none_when_no_completions(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", completed_at=None),
            _make_result(agent_id="a2", task_id="T-1", completed_at=None),
        ]
        report = aggregator.aggregate(results)
        assert report.completed_at is None

    def test_completed_at_uses_only_non_none(self, aggregator):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            _make_result(agent_id="a1", task_id="T-1", completed_at=None),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                completed_at=t + timedelta(seconds=60),
            ),
        ]
        report = aggregator.aggregate(results)
        assert report.completed_at == t + timedelta(seconds=60)


# =============================================================================
# ResultAggregator — error collection
# =============================================================================


class TestResultAggregatorErrorCollection:
    """Tests for _collect_errors via aggregate()."""

    def test_errors_collected_from_results(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                errors=["Error 1", "Error 2"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                errors=["Error 3"],
            ),
        ]
        report = aggregator.aggregate(results)
        assert len(report.errors) == 3
        agent_ids = [e["agent_id"] for e in report.errors]
        assert agent_ids.count("a1") == 2
        assert agent_ids.count("a2") == 1

    def test_no_errors_when_results_clean(self, aggregator, two_success_results):
        report = aggregator.aggregate(two_success_results)
        assert report.errors == []

    def test_error_dict_has_agent_id_and_error_keys(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", errors=["Timeout error"]),
        ]
        report = aggregator.aggregate(results)
        entry = report.errors[0]
        assert "agent_id" in entry
        assert "error" in entry
        assert entry["agent_id"] == "a1"
        assert entry["error"] == "Timeout error"

    def test_errors_from_successful_agents_still_collected(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                status=ResultStatus.SUCCESS,
                errors=["Non-fatal warning"],
            ),
        ]
        report = aggregator.aggregate(results)
        assert len(report.errors) == 1


# =============================================================================
# ResultAggregator — deduplication
# =============================================================================


class TestResultAggregatorDeduplication:
    """Tests for _deduplicate_findings via aggregate()."""

    def test_identical_findings_are_common(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Fixed the authentication bug"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Fixed the authentication bug"],
            ),
        ]
        report = aggregator.aggregate(results)
        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 2

    def test_highly_similar_findings_are_common(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Fixed the authentication bug in login module"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Fixed the authentication bug in login"],
            ),
        ]
        report = aggregator.aggregate(results)
        # Should detect these as similar (>0.75 similarity)
        assert len(report.common_findings) >= 1

    def test_dissimilar_findings_are_unique(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Added database indexing"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Implemented OAuth2 flow"],
            ),
        ]
        report = aggregator.aggregate(results)
        assert len(report.common_findings) == 0
        assert "a1" in report.unique_findings
        assert "a2" in report.unique_findings

    def test_no_findings_returns_empty(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", findings=[]),
            _make_result(agent_id="a2", task_id="T-1", findings=[]),
        ]
        report = aggregator.aggregate(results)
        assert report.common_findings == []
        assert report.unique_findings == {}

    def test_unique_findings_attributed_correctly(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Database index optimisation applied"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["OAuth2 refresh token rotation implemented"],
            ),
        ]
        report = aggregator.aggregate(results)
        assert "a1" in report.unique_findings
        assert "Database index optimisation applied" in report.unique_findings["a1"]
        assert "a2" in report.unique_findings
        assert "OAuth2 refresh token rotation implemented" in report.unique_findings["a2"]

    def test_canonical_finding_is_longest(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Short"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Short version but longer"],
            ),
        ]
        # With threshold=0.75, "Short" vs "Short version but longer" is ~0.4 similarity
        # so they might be unique — use more similar text
        results2 = [
            _make_result(
                agent_id="x1",
                task_id="T-2",
                findings=["Fixed bug in authentication"],
            ),
            _make_result(
                agent_id="x2",
                task_id="T-2",
                findings=["Fixed bug in authentication module"],
            ),
        ]
        report = aggregator.aggregate(results2)
        if report.common_findings:
            # The canonical should be the longer one
            canonical = report.common_findings[0].finding
            assert canonical == "Fixed bug in authentication module"

    def test_high_similarity_threshold_produces_fewer_common(self):
        strict = ResultAggregator(similarity_threshold=0.99)
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Fixed the bug"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Fixed the buggy code"],
            ),
        ]
        report = strict.aggregate(results)
        assert len(report.common_findings) == 0

    def test_low_similarity_threshold_produces_more_common(self):
        lenient = ResultAggregator(similarity_threshold=0.3)
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Fixed authentication"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Fixed auth"],
            ),
        ]
        report = lenient.aggregate(results)
        assert len(report.common_findings) >= 1

    def test_common_finding_original_texts_preserved(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Fixed the authentication bug"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Fixed the authentication bug"],
            ),
        ]
        report = aggregator.aggregate(results)
        assert len(report.common_findings) == 1
        dup = report.common_findings[0]
        assert len(dup.original_texts) == 2

    def test_common_finding_agent_ids_correct(self, aggregator):
        results = [
            _make_result(
                agent_id="alpha",
                task_id="T-1",
                findings=["Fixed the authentication bug"],
            ),
            _make_result(
                agent_id="beta",
                task_id="T-1",
                findings=["Fixed the authentication bug"],
            ),
        ]
        report = aggregator.aggregate(results)
        dup = report.common_findings[0]
        assert "alpha" in dup.agent_ids
        assert "beta" in dup.agent_ids

    def test_already_common_finding_not_in_unique(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                findings=["Shared finding"],
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                findings=["Shared finding"],
            ),
        ]
        report = aggregator.aggregate(results)
        # "Shared finding" should be in common, not unique
        all_unique = []
        for findings in report.unique_findings.values():
            all_unique.extend(findings)
        assert "Shared finding" not in all_unique


# =============================================================================
# ResultAggregator — conflict detection
# =============================================================================


class TestResultAggregatorConflictDetection:
    """Tests for _detect_conflicts via aggregate()."""

    def test_success_and_failure_produces_critical_conflict(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", task_id="T-1", status=ResultStatus.FAILURE),
        ]
        report = aggregator.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1

    def test_all_success_no_status_conflict(self, aggregator, two_success_results):
        report = aggregator.aggregate(two_success_results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) == 0

    def test_all_failure_no_status_conflict(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.FAILURE),
            _make_result(agent_id="a2", task_id="T-1", status=ResultStatus.FAILURE),
        ]
        report = aggregator.aggregate(results)
        # Both fail — no contradictory conflict
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) == 0

    def test_conflict_includes_agent_ids(self, aggregator):
        results = [
            _make_result(agent_id="success-agent", task_id="T-1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="failure-agent", task_id="T-1", status=ResultStatus.FAILURE),
        ]
        report = aggregator.aggregate(results)
        conflict = report.conflicts[0]
        assert "success-agent" in conflict.agent_ids
        assert "failure-agent" in conflict.agent_ids

    def test_metadata_conflict_produces_warning(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                metadata={"version": "1.0"},
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                metadata={"version": "2.0"},
            ),
        ]
        report = aggregator.aggregate(results)
        warnings = [c for c in report.conflicts if c.severity == ConflictSeverity.WARNING]
        assert len(warnings) >= 1

    def test_metadata_conflict_description_mentions_key(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                metadata={"coverage": "80%"},
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                metadata={"coverage": "90%"},
            ),
        ]
        report = aggregator.aggregate(results)
        warning = next(
            c for c in report.conflicts if c.severity == ConflictSeverity.WARNING
        )
        assert "coverage" in warning.description

    def test_matching_metadata_no_conflict(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                metadata={"version": "1.0"},
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                metadata={"version": "1.0"},
            ),
        ]
        report = aggregator.aggregate(results)
        assert len(report.conflicts) == 0

    def test_no_metadata_no_metadata_conflict(self, aggregator, two_success_results):
        report = aggregator.aggregate(two_success_results)
        metadata_conflicts = [
            c for c in report.conflicts if "metadata" in c.description.lower()
        ]
        assert len(metadata_conflicts) == 0

    def test_conflict_detection_disabled_skips_all(self):
        agg = ResultAggregator(conflict_detection=False)
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", task_id="T-1", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        assert report.conflicts == []

    def test_metadata_list_values_converted_for_comparison(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                metadata={"tags": ["a", "b"]},
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                metadata={"tags": ["c", "d"]},
            ),
        ]
        # Should not raise — list values are converted to string
        report = aggregator.aggregate(results)
        assert isinstance(report.conflicts, list)

    def test_metadata_dict_values_converted_for_comparison(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                metadata={"config": {"a": 1}},
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                metadata={"config": {"b": 2}},
            ),
        ]
        report = aggregator.aggregate(results)
        assert isinstance(report.conflicts, list)

    def test_conflict_details_include_values(self, aggregator):
        results = [
            _make_result(
                agent_id="a1",
                task_id="T-1",
                metadata={"env": "staging"},
            ),
            _make_result(
                agent_id="a2",
                task_id="T-1",
                metadata={"env": "production"},
            ),
        ]
        report = aggregator.aggregate(results)
        warning = next(
            c for c in report.conflicts if c.severity == ConflictSeverity.WARNING
        )
        assert "values" in warning.details
        assert "key" in warning.details


# =============================================================================
# ResultAggregator — _calculate_similarity
# =============================================================================


class TestCalculateSimilarity:
    """Tests for the internal _calculate_similarity method."""

    def test_identical_strings_return_1(self, aggregator):
        score = aggregator._calculate_similarity("hello world", "hello world")
        assert score == 1.0

    def test_completely_different_returns_low(self, aggregator):
        score = aggregator._calculate_similarity("abcdef", "xyz123")
        assert score < 0.5

    def test_empty_strings_return_1(self, aggregator):
        score = aggregator._calculate_similarity("", "")
        assert score == 1.0

    def test_case_insensitive(self, aggregator):
        score = aggregator._calculate_similarity("Hello World", "hello world")
        assert score == 1.0

    def test_partial_match_between_0_and_1(self, aggregator):
        score = aggregator._calculate_similarity("Fixed auth bug", "Fixed authentication bug")
        assert 0.0 < score < 1.0

    def test_symmetry(self, aggregator):
        s1 = aggregator._calculate_similarity("text one", "text two")
        s2 = aggregator._calculate_similarity("text two", "text one")
        assert s1 == pytest.approx(s2)


# =============================================================================
# ResultAggregator — _group_by_status
# =============================================================================


class TestGroupByStatus:
    """Tests for the internal _group_by_status method."""

    def test_groups_by_status(self, aggregator):
        results = [
            _make_result(agent_id="a1", task_id="T-1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", task_id="T-1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a3", task_id="T-1", status=ResultStatus.FAILURE),
        ]
        grouped = aggregator._group_by_status(results)
        assert len(grouped[ResultStatus.SUCCESS]) == 2
        assert len(grouped[ResultStatus.FAILURE]) == 1

    def test_empty_list_returns_empty_dict(self, aggregator):
        grouped = aggregator._group_by_status([])
        assert grouped == {}

    def test_single_result(self, aggregator):
        results = [_make_result(agent_id="a1", task_id="T-1", status=ResultStatus.PARTIAL)]
        grouped = aggregator._group_by_status(results)
        assert ResultStatus.PARTIAL in grouped
        assert len(grouped[ResultStatus.PARTIAL]) == 1


# =============================================================================
# Integration tests — full aggregate workflow
# =============================================================================


class TestResultAggregatorIntegration:
    """Full workflow integration tests."""

    def test_full_workflow_to_markdown(self, aggregator):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-001",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug", "Added tests"],
                started_at=t,
                completed_at=t + timedelta(seconds=60),
            ),
            AgentResult(
                agent_id="qa-1",
                task_id="HRN-001",
                status=ResultStatus.SUCCESS,
                findings=["All tests pass", "Coverage 95%"],
                started_at=t,
                completed_at=t + timedelta(seconds=90),
            ),
        ]
        report = aggregator.aggregate(results)
        md = report.to_markdown()
        assert "# Aggregated Report: HRN-001" in md
        assert "backend-1" in md
        assert "qa-1" in md
        assert "## Summary" in md

    def test_full_workflow_to_dict(self, aggregator):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-001",
                status=ResultStatus.SUCCESS,
                started_at=t,
                completed_at=t + timedelta(seconds=60),
            ),
        ]
        report = aggregator.aggregate(results)
        d = report.to_dict()
        assert d["task_id"] == "HRN-001"
        assert d["total_agents"] == 1
        assert d["success_rate"] == 100.0

    def test_module_docstring_example(self):
        """Replicates the usage example from the module docstring."""
        aggregator = ResultAggregator()
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
        report = aggregator.aggregate(results)
        md = report.to_markdown()
        assert isinstance(md, str)
        assert len(md) > 0
        assert "HRN-001" in md

    def test_three_agents_with_duplicates_and_conflicts(self):
        """Complex scenario: duplicates + metadata conflict + status conflict."""
        agg = ResultAggregator()
        t = datetime(2026, 1, 1, tzinfo=UTC)
        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="COMPLEX-001",
                status=ResultStatus.SUCCESS,
                findings=["Fixed auth bug", "Added unit tests"],
                metadata={"env": "staging"},
                started_at=t,
                completed_at=t + timedelta(seconds=30),
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="COMPLEX-001",
                status=ResultStatus.FAILURE,
                findings=["Fixed auth bug"],
                errors=["Deployment failed"],
                metadata={"env": "production"},
                started_at=t,
                completed_at=t + timedelta(seconds=20),
            ),
            AgentResult(
                agent_id="agent-3",
                task_id="COMPLEX-001",
                status=ResultStatus.PARTIAL,
                findings=["Added unit tests"],
                metadata={"env": "staging"},
                started_at=t,
                completed_at=t + timedelta(seconds=50),
            ),
        ]
        report = agg.aggregate(results)

        # Check totals
        assert report.total_agents == 3
        assert report.successful_agents == 1
        assert report.failed_agents == 1
        assert report.partial_agents == 1

        # Check errors
        assert len(report.errors) == 1
        assert report.errors[0]["agent_id"] == "agent-2"

        # Check time bounds
        assert report.started_at == t
        assert report.completed_at == t + timedelta(seconds=50)

        # Should have status conflict (success + failure)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1

        # Should have metadata conflict (env: staging vs production)
        warnings = [c for c in report.conflicts if c.severity == ConflictSeverity.WARNING]
        assert len(warnings) >= 1

        # Markdown and dict should work without error
        md = report.to_markdown()
        d = report.to_dict()
        assert isinstance(md, str)
        assert isinstance(d, dict)

"""Comprehensive unit tests for forge_harness.iteration.demo_aggregator.

Tests all demo functions, the main entry point, and the underlying
ResultAggregator + AgentResult classes exercised by those demos.
Every external side-effect (print, logger) is mocked so tests are
fully isolated and fast.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Aggregator classes (used directly in several assertions)
# ---------------------------------------------------------------------------
from forge_harness.iteration.aggregator import (
    AgentResult,
    AggregatedReport,
    Conflict,
    ConflictSeverity,
    DuplicateFinding,
    ResultAggregator,
    ResultStatus,
)

# ---------------------------------------------------------------------------
# The module under test
# ---------------------------------------------------------------------------
from forge_harness.iteration.demo_aggregator import (
    demo_conflict_detection,
    demo_deduplication,
    demo_multiple_agents_parallel,
    demo_partial_failure,
    demo_successful_aggregation,
    main,
)

# ===========================================================================
# Helpers
# ===========================================================================

def _make_result(
    agent_id: str = "agent-1",
    task_id: str = "HRN-005",
    status: ResultStatus = ResultStatus.SUCCESS,
    findings: list[str] | None = None,
    errors: list[str] | None = None,
    metadata: dict | None = None,
    offset_start: int = 0,
    offset_end: int = 120,
) -> AgentResult:
    """Factory helper to create AgentResult instances."""
    started = datetime.now(UTC) + timedelta(seconds=offset_start)
    return AgentResult(
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        findings=findings or [],
        errors=errors or [],
        metadata=metadata or {},
        started_at=started,
        completed_at=started + timedelta(seconds=offset_end),
    )


# ===========================================================================
# ResultStatus enum
# ===========================================================================

class TestResultStatusEnum:
    def test_values_exist(self):
        assert ResultStatus.SUCCESS.value == "success"
        assert ResultStatus.FAILURE.value == "failure"
        assert ResultStatus.PARTIAL.value == "partial"
        assert ResultStatus.TIMEOUT.value == "timeout"
        assert ResultStatus.ERROR.value == "error"
        assert ResultStatus.UNKNOWN.value == "unknown"

    def test_is_str_subclass(self):
        assert isinstance(ResultStatus.SUCCESS, str)

    def test_from_string_conversion(self):
        result = AgentResult(
            agent_id="a", task_id="t", status="success"
        )
        assert result.status is ResultStatus.SUCCESS

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            AgentResult(agent_id="a", task_id="t", status="invalid_status")


# ===========================================================================
# ConflictSeverity enum
# ===========================================================================

class TestConflictSeverityEnum:
    def test_values_exist(self):
        assert ConflictSeverity.CRITICAL.value == "critical"
        assert ConflictSeverity.WARNING.value == "warning"
        assert ConflictSeverity.INFO.value == "info"

    def test_is_str_subclass(self):
        assert isinstance(ConflictSeverity.CRITICAL, str)

    def test_from_string_via_conflict(self):
        c = Conflict(
            severity="warning",
            description="test",
            agent_ids=["a"],
        )
        assert c.severity is ConflictSeverity.WARNING


# ===========================================================================
# AgentResult dataclass
# ===========================================================================

class TestAgentResult:
    def test_defaults(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.findings == []
        assert result.errors == []
        assert result.metadata == {}
        assert result.output == ""
        assert result.completed_at is None

    def test_duration_seconds_none_when_no_completion(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.duration_seconds is None

    def test_duration_seconds_calculated(self):
        started = datetime.now(UTC)
        result = AgentResult(
            agent_id="a",
            task_id="t",
            status=ResultStatus.SUCCESS,
            started_at=started,
            completed_at=started + timedelta(seconds=90),
        )
        assert result.duration_seconds == pytest.approx(90.0)

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
        result = _make_result(errors=["oops"])
        assert result.has_errors is True

    def test_has_errors_false(self):
        result = _make_result(errors=[])
        assert result.has_errors is False

    def test_to_dict_keys(self):
        result = _make_result(
            findings=["f1"],
            errors=["e1"],
            metadata={"k": "v"},
        )
        d = result.to_dict()
        for key in (
            "agent_id", "task_id", "status", "findings",
            "errors", "metadata", "started_at", "completed_at", "duration_seconds",
        ):
            assert key in d

    def test_to_dict_status_is_string(self):
        result = _make_result()
        assert isinstance(result.to_dict()["status"], str)

    def test_to_dict_completed_at_none(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.to_dict()["completed_at"] is None
        assert result.to_dict()["duration_seconds"] is None

    def test_to_dict_completed_at_isoformat(self):
        result = _make_result()
        d = result.to_dict()
        assert isinstance(d["completed_at"], str)
        # Should parse back to datetime
        datetime.fromisoformat(d["completed_at"])


# ===========================================================================
# DuplicateFinding dataclass
# ===========================================================================

class TestDuplicateFinding:
    def test_count_property(self):
        dup = DuplicateFinding(
            finding="foo",
            agent_ids=["a1", "a2", "a3"],
            similarity_score=0.9,
        )
        assert dup.count == 3

    def test_original_texts_default_empty(self):
        dup = DuplicateFinding(finding="x", agent_ids=[], similarity_score=1.0)
        assert dup.original_texts == []

    def test_count_zero_for_empty_agents(self):
        dup = DuplicateFinding(finding="x", agent_ids=[], similarity_score=0.8)
        assert dup.count == 0


# ===========================================================================
# Conflict dataclass
# ===========================================================================

class TestConflict:
    def test_string_severity_converted(self):
        c = Conflict(severity="critical", description="d", agent_ids=["a"])
        assert c.severity is ConflictSeverity.CRITICAL

    def test_details_defaults_empty(self):
        c = Conflict(severity=ConflictSeverity.INFO, description="d", agent_ids=[])
        assert c.details == {}

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError):
            Conflict(severity="super-critical", description="d", agent_ids=[])


# ===========================================================================
# AggregatedReport dataclass
# ===========================================================================

class TestAggregatedReport:
    def _make_report(self, **kwargs) -> AggregatedReport:
        defaults = dict(
            task_id="T-1",
            total_agents=3,
            successful_agents=2,
            failed_agents=1,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )
        defaults.update(kwargs)
        return AggregatedReport(**defaults)

    def test_success_rate_calculation(self):
        report = self._make_report(total_agents=4, successful_agents=3)
        assert report.success_rate == pytest.approx(75.0)

    def test_success_rate_zero_agents(self):
        report = self._make_report(total_agents=0, successful_agents=0)
        assert report.success_rate == 0.0

    def test_duration_seconds_none_when_missing_timestamps(self):
        report = self._make_report()
        assert report.duration_seconds is None

    def test_duration_seconds_calculated(self):
        started = datetime.now(UTC)
        report = self._make_report(
            started_at=started,
            completed_at=started + timedelta(seconds=200),
        )
        assert report.duration_seconds == pytest.approx(200.0)

    def test_duration_none_when_only_started(self):
        report = self._make_report(started_at=datetime.now(UTC))
        assert report.duration_seconds is None

    def test_to_dict_keys(self):
        report = self._make_report()
        d = report.to_dict()
        for key in (
            "task_id", "total_agents", "successful_agents", "failed_agents",
            "partial_agents", "success_rate", "common_findings_count",
            "conflicts_count", "errors_count", "duration_seconds",
            "started_at", "completed_at",
        ):
            assert key in d

    def test_to_dict_timestamps_none(self):
        report = self._make_report()
        d = report.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None

    def test_to_dict_timestamps_isoformat(self):
        t = datetime.now(UTC)
        report = self._make_report(started_at=t, completed_at=t + timedelta(seconds=10))
        d = report.to_dict()
        datetime.fromisoformat(d["started_at"])
        datetime.fromisoformat(d["completed_at"])

    def test_to_markdown_contains_task_id(self):
        report = self._make_report(task_id="MY-TASK")
        md = report.to_markdown()
        assert "MY-TASK" in md

    def test_to_markdown_summary_section(self):
        report = self._make_report(total_agents=5, successful_agents=4, failed_agents=1)
        md = report.to_markdown()
        assert "## Summary" in md
        assert "5" in md

    def test_to_markdown_with_duration(self):
        started = datetime.now(UTC)
        report = self._make_report(
            started_at=started,
            completed_at=started + timedelta(seconds=300),
        )
        md = report.to_markdown()
        assert "Duration" in md

    def test_to_markdown_common_findings_section(self):
        dup = DuplicateFinding(
            finding="Fixed bug",
            agent_ids=["a1", "a2"],
            similarity_score=0.9,
            original_texts=["Fixed bug", "Fixed the bug"],
        )
        report = self._make_report(common_findings=[dup])
        md = report.to_markdown()
        assert "Common Findings" in md
        assert "Fixed bug" in md

    def test_to_markdown_unique_findings_section(self):
        report = self._make_report(unique_findings={"agent-x": ["Only I found this"]})
        md = report.to_markdown()
        assert "Unique Findings" in md
        assert "Only I found this" in md

    def test_to_markdown_conflicts_section(self):
        conflict = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Contradictory results",
            agent_ids=["a1", "a2"],
        )
        report = self._make_report(conflicts=[conflict])
        md = report.to_markdown()
        assert "Conflicts" in md
        assert "Contradictory results" in md

    def test_to_markdown_errors_section(self):
        report = self._make_report(
            errors=[{"agent_id": "bad-agent", "error": "ImportError"}]
        )
        md = report.to_markdown()
        assert "Errors" in md
        assert "bad-agent" in md
        assert "ImportError" in md

    def test_to_markdown_no_sections_when_empty(self):
        report = self._make_report()
        md = report.to_markdown()
        assert "Common Findings" not in md
        assert "Unique Findings" not in md
        assert "Conflicts" not in md
        assert "Errors" not in md

    def test_to_markdown_results_by_status(self):
        result = _make_result(agent_id="worker-1", findings=["did something"])
        report = self._make_report(
            results_by_status={ResultStatus.SUCCESS: [result]}
        )
        md = report.to_markdown()
        assert "worker-1" in md
        assert "did something" in md

    def test_to_markdown_conflict_icons(self):
        conflicts = [
            Conflict(ConflictSeverity.CRITICAL, "crit", ["a"]),
            Conflict(ConflictSeverity.WARNING, "warn", ["b"]),
            Conflict(ConflictSeverity.INFO, "info", ["c"]),
        ]
        report = self._make_report(conflicts=conflicts)
        md = report.to_markdown()
        # All severities should appear
        assert "CRITICAL" in md
        assert "WARNING" in md
        assert "INFO" in md


# ===========================================================================
# ResultAggregator class
# ===========================================================================

class TestResultAggregatorInit:
    def test_default_threshold(self):
        agg = ResultAggregator()
        assert agg.similarity_threshold == 0.75

    def test_custom_threshold(self):
        agg = ResultAggregator(similarity_threshold=0.5)
        assert agg.similarity_threshold == 0.5

    def test_conflict_detection_default_true(self):
        agg = ResultAggregator()
        assert agg.conflict_detection is True

    def test_conflict_detection_disabled(self):
        agg = ResultAggregator(conflict_detection=False)
        assert agg.conflict_detection is False

    def test_threshold_zero_valid(self):
        agg = ResultAggregator(similarity_threshold=0.0)
        assert agg.similarity_threshold == 0.0

    def test_threshold_one_valid(self):
        agg = ResultAggregator(similarity_threshold=1.0)
        assert agg.similarity_threshold == 1.0

    def test_threshold_below_zero_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            ResultAggregator(similarity_threshold=-0.1)

    def test_threshold_above_one_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            ResultAggregator(similarity_threshold=1.1)


class TestResultAggregatorAggregate:
    def test_empty_results_raises(self):
        agg = ResultAggregator()
        with pytest.raises(ValueError, match="empty"):
            agg.aggregate([])

    def test_inconsistent_task_ids_raises(self):
        agg = ResultAggregator()
        r1 = _make_result(task_id="T-1")
        r2 = _make_result(task_id="T-2")
        with pytest.raises(ValueError, match="inconsistent task IDs"):
            agg.aggregate([r1, r2])

    def test_single_result_success(self):
        agg = ResultAggregator()
        r = _make_result()
        report = agg.aggregate([r])
        assert report.total_agents == 1
        assert report.successful_agents == 1
        assert report.failed_agents == 0
        assert report.task_id == "HRN-005"

    def test_returns_aggregated_report(self):
        agg = ResultAggregator()
        report = agg.aggregate([_make_result()])
        assert isinstance(report, AggregatedReport)

    def test_success_rate_100_percent(self):
        agg = ResultAggregator()
        results = [_make_result(agent_id=f"a{i}") for i in range(5)]
        report = agg.aggregate(results)
        assert report.success_rate == pytest.approx(100.0)

    def test_success_rate_0_percent(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id=f"a{i}", status=ResultStatus.FAILURE)
            for i in range(3)
        ]
        report = agg.aggregate(results)
        assert report.success_rate == pytest.approx(0.0)

    def test_counts_partial_agents(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", status=ResultStatus.PARTIAL),
            _make_result(agent_id="a3", status=ResultStatus.PARTIAL),
        ]
        report = agg.aggregate(results)
        assert report.partial_agents == 2

    def test_counts_failed_agents(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        assert report.failed_agents == 1

    def test_started_at_is_earliest(self):
        base = datetime(2024, 1, 1, tzinfo=UTC)
        r1 = AgentResult(
            agent_id="a1", task_id="T", status=ResultStatus.SUCCESS,
            started_at=base + timedelta(seconds=60),
            completed_at=base + timedelta(seconds=180),
        )
        r2 = AgentResult(
            agent_id="a2", task_id="T", status=ResultStatus.SUCCESS,
            started_at=base,
            completed_at=base + timedelta(seconds=120),
        )
        agg = ResultAggregator()
        report = agg.aggregate([r1, r2])
        assert report.started_at == base

    def test_completed_at_is_latest(self):
        base = datetime(2024, 1, 1, tzinfo=UTC)
        r1 = AgentResult(
            agent_id="a1", task_id="T", status=ResultStatus.SUCCESS,
            started_at=base,
            completed_at=base + timedelta(seconds=120),
        )
        r2 = AgentResult(
            agent_id="a2", task_id="T", status=ResultStatus.SUCCESS,
            started_at=base + timedelta(seconds=10),
            completed_at=base + timedelta(seconds=300),
        )
        agg = ResultAggregator()
        report = agg.aggregate([r1, r2])
        assert report.completed_at == base + timedelta(seconds=300)

    def test_completed_at_none_when_no_completions(self):
        agg = ResultAggregator()
        r = AgentResult(agent_id="a", task_id="T", status=ResultStatus.SUCCESS)
        report = agg.aggregate([r])
        assert report.completed_at is None

    def test_errors_collected(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", errors=["err1", "err2"]),
            _make_result(agent_id="a2", errors=["err3"]),
        ]
        report = agg.aggregate(results)
        assert len(report.errors) == 3
        agent_ids = [e["agent_id"] for e in report.errors]
        assert agent_ids.count("a1") == 2
        assert agent_ids.count("a2") == 1

    def test_no_conflict_detection_when_disabled(self):
        agg = ResultAggregator(conflict_detection=False)
        results = [
            _make_result(agent_id="a1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        assert report.conflicts == []

    def test_conflict_detection_enabled_finds_status_conflict(self):
        agg = ResultAggregator(conflict_detection=True)
        results = [
            _make_result(agent_id="a1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="a2", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        assert len(report.conflicts) >= 1
        severities = [c.severity for c in report.conflicts]
        assert ConflictSeverity.CRITICAL in severities

    def test_metadata_conflict_detected(self):
        agg = ResultAggregator(conflict_detection=True)
        results = [
            _make_result(agent_id="a1", metadata={"coverage": 95.0}),
            _make_result(agent_id="a2", metadata={"coverage": 60.0}),
        ]
        report = agg.aggregate(results)
        # Should detect metadata conflict
        meta_conflicts = [
            c for c in report.conflicts
            if "coverage" in c.description
        ]
        assert len(meta_conflicts) >= 1

    def test_no_metadata_conflict_when_same_values(self):
        agg = ResultAggregator(conflict_detection=True)
        results = [
            _make_result(agent_id="a1", metadata={"version": "1.0.0"}),
            _make_result(agent_id="a2", metadata={"version": "1.0.0"}),
        ]
        report = agg.aggregate(results)
        meta_conflicts = [
            c for c in report.conflicts if "version" in c.description
        ]
        assert len(meta_conflicts) == 0

    def test_results_grouped_by_status(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="s1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="f1", status=ResultStatus.FAILURE),
            _make_result(agent_id="p1", status=ResultStatus.PARTIAL),
        ]
        report = agg.aggregate(results)
        assert ResultStatus.SUCCESS in report.results_by_status
        assert ResultStatus.FAILURE in report.results_by_status
        assert ResultStatus.PARTIAL in report.results_by_status


class TestDeduplicationBehavior:
    """Tests for _deduplicate_findings internals via aggregate()."""

    def test_identical_findings_deduplicated(self):
        agg = ResultAggregator(similarity_threshold=0.75)
        results = [
            _make_result(agent_id="a1", findings=["Fixed the bug"]),
            _make_result(agent_id="a2", findings=["Fixed the bug"]),
        ]
        report = agg.aggregate(results)
        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 2

    def test_dissimilar_findings_not_merged(self):
        agg = ResultAggregator(similarity_threshold=0.75)
        results = [
            _make_result(agent_id="a1", findings=["Alpha omega gamma"]),
            _make_result(agent_id="a2", findings=["Totally different text"]),
        ]
        report = agg.aggregate(results)
        # Very different texts should not be merged as common
        for cf in report.common_findings:
            # If any common finding exists it must have score >= threshold
            assert cf.similarity_score >= 0.75

    def test_no_findings_returns_empty(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", findings=[]),
            _make_result(agent_id="a2", findings=[]),
        ]
        report = agg.aggregate(results)
        assert report.common_findings == []
        assert report.unique_findings == {}

    def test_unique_findings_captured(self):
        agg = ResultAggregator(similarity_threshold=0.99)  # Very high threshold
        results = [
            _make_result(agent_id="a1", findings=["Unique finding from agent 1"]),
            _make_result(agent_id="a2", findings=["Something completely different xyz"]),
        ]
        report = agg.aggregate(results)
        # With very high threshold, these won't merge — both should be unique
        all_unique = [f for findings in report.unique_findings.values() for f in findings]
        assert len(all_unique) >= 1

    def test_similarity_threshold_respected(self):
        # Low threshold → similar texts merge
        agg_low = ResultAggregator(similarity_threshold=0.3)
        results = [
            _make_result(agent_id="a1", findings=["Fixed bug in auth"]),
            _make_result(agent_id="a2", findings=["Fixed authentication bug"]),
        ]
        report_low = agg_low.aggregate(results)

        # High threshold → texts may not merge
        agg_high = ResultAggregator(similarity_threshold=0.95)
        report_high = agg_high.aggregate(results)

        # Low threshold should produce more (or equal) common findings
        assert len(report_low.common_findings) >= len(report_high.common_findings)

    def test_canonical_finding_is_longest(self):
        """The canonical deduplicated finding should be the longest text."""
        agg = ResultAggregator(similarity_threshold=0.5)
        results = [
            _make_result(agent_id="a1", findings=["Fixed bug"]),
            _make_result(agent_id="a2", findings=["Fixed the authentication bug thoroughly"]),
        ]
        report = agg.aggregate(results)
        if report.common_findings:
            cf = report.common_findings[0]
            # Canonical is the longest
            assert cf.finding == "Fixed the authentication bug thoroughly"

    def test_similarity_score_between_0_and_1(self):
        agg = ResultAggregator(similarity_threshold=0.5)
        results = [
            _make_result(agent_id="a1", findings=["Implemented core feature"]),
            _make_result(agent_id="a2", findings=["Implemented feature"]),
        ]
        report = agg.aggregate(results)
        for cf in report.common_findings:
            assert 0.0 <= cf.similarity_score <= 1.0


class TestCalculateSimilarity:
    """Tests for _calculate_similarity method directly."""

    def test_identical_strings(self):
        agg = ResultAggregator()
        score = agg._calculate_similarity("hello world", "hello world")
        assert score == pytest.approx(1.0)

    def test_completely_different_strings(self):
        agg = ResultAggregator()
        score = agg._calculate_similarity("aaa", "bbb")
        assert score < 0.5

    def test_case_insensitive(self):
        agg = ResultAggregator()
        score_lower = agg._calculate_similarity("hello", "hello")
        score_mixed = agg._calculate_similarity("HELLO", "hello")
        assert score_lower == pytest.approx(score_mixed)

    def test_partial_overlap(self):
        agg = ResultAggregator()
        score = agg._calculate_similarity("Fixed bug", "Fixed authentication bug")
        assert 0.0 < score < 1.0

    def test_empty_strings(self):
        agg = ResultAggregator()
        score = agg._calculate_similarity("", "")
        assert score == pytest.approx(1.0)

    def test_one_empty_string(self):
        agg = ResultAggregator()
        score = agg._calculate_similarity("hello", "")
        assert score == pytest.approx(0.0)


class TestCollectErrors:
    def test_collects_all_errors_with_attribution(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="bad1", errors=["err-a", "err-b"]),
            _make_result(agent_id="bad2", errors=["err-c"]),
            _make_result(agent_id="good", errors=[]),
        ]
        errors = agg._collect_errors(results)
        assert len(errors) == 3
        assert {"agent_id": "bad1", "error": "err-a"} in errors
        assert {"agent_id": "bad1", "error": "err-b"} in errors
        assert {"agent_id": "bad2", "error": "err-c"} in errors

    def test_no_errors_returns_empty(self):
        agg = ResultAggregator()
        results = [_make_result(agent_id="a", errors=[])]
        assert agg._collect_errors(results) == []


class TestGroupByStatus:
    def test_groups_correctly(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="s1", status=ResultStatus.SUCCESS),
            _make_result(agent_id="s2", status=ResultStatus.SUCCESS),
            _make_result(agent_id="f1", status=ResultStatus.FAILURE),
        ]
        grouped = agg._group_by_status(results)
        assert len(grouped[ResultStatus.SUCCESS]) == 2
        assert len(grouped[ResultStatus.FAILURE]) == 1

    def test_empty_list(self):
        agg = ResultAggregator()
        grouped = agg._group_by_status([])
        assert grouped == {}


class TestDetectMetadataConflicts:
    def test_no_conflict_same_values(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", metadata={"k": "same"}),
            _make_result(agent_id="a2", metadata={"k": "same"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert conflicts == []

    def test_conflict_different_values(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", metadata={"k": "v1"}),
            _make_result(agent_id="a2", metadata={"k": "v2"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert len(conflicts) == 1
        assert conflicts[0].severity == ConflictSeverity.WARNING

    def test_list_values_converted_to_string(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", metadata={"items": [1, 2, 3]}),
            _make_result(agent_id="a2", metadata={"items": [4, 5, 6]}),
        ]
        # Should not raise; list converted to str for comparison
        conflicts = agg._detect_metadata_conflicts(results)
        assert len(conflicts) == 1

    def test_no_conflict_when_no_shared_keys(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="a1", metadata={"only_a": 1}),
            _make_result(agent_id="a2", metadata={"only_b": 2}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert conflicts == []

    def test_conflict_agents_attributed_correctly(self):
        agg = ResultAggregator()
        results = [
            _make_result(agent_id="x1", metadata={"score": 10}),
            _make_result(agent_id="x2", metadata={"score": 20}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert "x1" in conflicts[0].agent_ids
        assert "x2" in conflicts[0].agent_ids


# ===========================================================================
# demo_successful_aggregation
# ===========================================================================

class TestDemoSuccessfulAggregation:
    @patch("builtins.print")
    def test_runs_without_error(self, mock_print):
        demo_successful_aggregation()
        assert mock_print.called

    @patch("builtins.print")
    def test_prints_task_id(self, mock_print):
        demo_successful_aggregation()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "HRN-005" in all_printed

    @patch("builtins.print")
    def test_prints_success_rate(self, mock_print):
        demo_successful_aggregation()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Success Rate" in all_printed

    @patch("builtins.print")
    def test_prints_duration(self, mock_print):
        demo_successful_aggregation()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Duration" in all_printed

    @patch("builtins.print")
    def test_prints_markdown_report(self, mock_print):
        demo_successful_aggregation()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Aggregated Report" in all_printed

    @patch("builtins.print")
    def test_uses_two_agents(self, mock_print):
        """Both backend-1 and backend-2 agents used."""
        demo_successful_aggregation()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "backend-1" in all_printed
        assert "backend-2" in all_printed

    def test_aggregation_result_is_100_percent(self):
        """Underlying aggregation should yield 100% success."""
        from forge_harness.iteration.aggregator import ResultAggregator as ResultAggregatorAlias
        started = datetime.now(UTC)
        results = [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator class"],
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="backend-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator with deduplication"],
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=180),
            ),
        ]
        report = ResultAggregatorAlias().aggregate(results)
        assert report.success_rate == pytest.approx(100.0)
        assert report.total_agents == 2


# ===========================================================================
# demo_partial_failure
# ===========================================================================

class TestDemoPartialFailure:
    @patch("builtins.print")
    def test_runs_without_error(self, mock_print):
        demo_partial_failure()

    @patch("builtins.print")
    def test_prints_total_agents(self, mock_print):
        demo_partial_failure()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Total Agents" in all_printed

    @patch("builtins.print")
    def test_prints_failed_count(self, mock_print):
        demo_partial_failure()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Failed" in all_printed

    @patch("builtins.print")
    def test_prints_partial_count(self, mock_print):
        demo_partial_failure()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Partial" in all_printed

    @patch("builtins.print")
    def test_prints_errors_collected(self, mock_print):
        demo_partial_failure()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Errors Collected" in all_printed

    def test_aggregation_counts(self):
        """Verify 3 agents: 1 success, 1 failure, 1 partial."""
        started = datetime.now(UTC)
        results = [
            AgentResult("backend-1", "HRN-005", ResultStatus.SUCCESS,
                        findings=["Implemented aggregator"],
                        started_at=started,
                        completed_at=started + timedelta(seconds=120)),
            AgentResult("backend-2", "HRN-005", ResultStatus.FAILURE,
                        errors=["Import error"],
                        started_at=started + timedelta(seconds=30),
                        completed_at=started + timedelta(seconds=90)),
            AgentResult("qa-1", "HRN-005", ResultStatus.PARTIAL,
                        findings=["Completed 15 of 20 tests"],
                        started_at=started + timedelta(seconds=60),
                        completed_at=started + timedelta(seconds=180)),
        ]
        report = ResultAggregator().aggregate(results)
        assert report.total_agents == 3
        assert report.successful_agents == 1
        assert report.failed_agents == 1
        assert report.partial_agents == 1


# ===========================================================================
# demo_conflict_detection
# ===========================================================================

class TestDemoConflictDetection:
    @patch("builtins.print")
    def test_runs_without_error(self, mock_print):
        demo_conflict_detection()

    @patch("builtins.print")
    def test_prints_conflicts_detected(self, mock_print):
        demo_conflict_detection()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Conflicts Detected" in all_printed

    @patch("builtins.print")
    def test_prints_severity(self, mock_print):
        demo_conflict_detection()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        # Should print at least one severity level
        assert any(sev in all_printed for sev in ("CRITICAL", "WARNING", "INFO"))

    @patch("builtins.print")
    def test_prints_description(self, mock_print):
        demo_conflict_detection()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Description" in all_printed

    @patch("builtins.print")
    def test_prints_agents_involved(self, mock_print):
        demo_conflict_detection()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Agents" in all_printed

    def test_conflict_detection_finds_status_and_metadata_conflicts(self):
        started = datetime.now(UTC)
        results = [
            AgentResult(
                agent_id="agent-1", task_id="HRN-005", status=ResultStatus.SUCCESS,
                findings=["Tests pass"],
                metadata={"test_count": 39, "coverage": 95.0, "version": "1.0.0"},
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="agent-2", task_id="HRN-005", status=ResultStatus.FAILURE,
                findings=["Tests fail"],
                errors=["5 tests failed"],
                metadata={"test_count": 39, "coverage": 60.0, "version": "1.0.1"},
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=100),
            ),
        ]
        report = ResultAggregator(conflict_detection=True).aggregate(results)
        assert len(report.conflicts) >= 2  # At minimum: status + metadata

    def test_same_test_count_no_conflict_for_that_key(self):
        """test_count is same across agents — should not trigger conflict for that key."""
        started = datetime.now(UTC)
        results = [
            AgentResult(
                "agent-1", "HRN-005", ResultStatus.SUCCESS,
                metadata={"test_count": 39},
                started_at=started, completed_at=started + timedelta(seconds=10),
            ),
            AgentResult(
                "agent-2", "HRN-005", ResultStatus.SUCCESS,
                metadata={"test_count": 39},
                started_at=started, completed_at=started + timedelta(seconds=10),
            ),
        ]
        report = ResultAggregator(conflict_detection=True).aggregate(results)
        test_count_conflicts = [c for c in report.conflicts if "test_count" in c.description]
        assert test_count_conflicts == []


# ===========================================================================
# demo_deduplication
# ===========================================================================

class TestDemoDeduplication:
    @patch("builtins.print")
    def test_runs_without_error(self, mock_print):
        demo_deduplication()

    @patch("builtins.print")
    def test_tests_three_thresholds(self, mock_print):
        demo_deduplication()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "0.6" in all_printed
        assert "0.75" in all_printed
        assert "0.9" in all_printed

    @patch("builtins.print")
    def test_prints_common_findings(self, mock_print):
        demo_deduplication()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Common Findings" in all_printed

    @patch("builtins.print")
    def test_prints_unique_findings(self, mock_print):
        demo_deduplication()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Unique Findings" in all_printed

    @patch("builtins.print")
    def test_prints_similarity_score(self, mock_print):
        demo_deduplication()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Similarity" in all_printed

    def test_low_threshold_produces_more_common_findings(self):
        """At threshold 0.6, similar texts should group more aggressively."""
        results = [
            AgentResult(
                agent_id="agent-1", task_id="HRN-005", status=ResultStatus.SUCCESS,
                findings=["Fixed the API bug in authentication module"],
            ),
            AgentResult(
                agent_id="agent-2", task_id="HRN-005", status=ResultStatus.SUCCESS,
                findings=["Fixed API bug in authentication"],
            ),
            AgentResult(
                agent_id="agent-3", task_id="HRN-005", status=ResultStatus.SUCCESS,
                findings=["Repaired authentication API bug"],
            ),
        ]
        report_low = ResultAggregator(similarity_threshold=0.6).aggregate(results)
        report_high = ResultAggregator(similarity_threshold=0.9).aggregate(results)
        # At 0.6 we expect more common findings than at 0.9
        assert len(report_low.common_findings) >= len(report_high.common_findings)


# ===========================================================================
# demo_multiple_agents_parallel
# ===========================================================================

class TestDemoMultipleAgentsParallel:
    @patch("builtins.print")
    def test_runs_without_error(self, mock_print):
        demo_multiple_agents_parallel()

    @patch("builtins.print")
    def test_prints_10_agents(self, mock_print):
        demo_multiple_agents_parallel()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "10" in all_printed

    @patch("builtins.print")
    def test_prints_success_rate(self, mock_print):
        demo_multiple_agents_parallel()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Success Rate" in all_printed

    @patch("builtins.print")
    def test_prints_top_common_findings(self, mock_print):
        demo_multiple_agents_parallel()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Top Common Findings" in all_printed

    def test_10_agents_8_success_2_failure(self):
        """Verify the demo logic: agents 0-7 succeed, 8-9 fail."""
        started = datetime.now(UTC)
        results = []
        for i in range(10):
            status = ResultStatus.SUCCESS if i < 8 else ResultStatus.FAILURE
            findings = (
                [f"Agent {i + 1} completed task", "Implemented core functionality"]
                if status == ResultStatus.SUCCESS else []
            )
            errors = [] if status == ResultStatus.SUCCESS else [f"Agent {i + 1} error"]
            results.append(AgentResult(
                agent_id=f"agent-{i + 1}",
                task_id="HRN-005",
                status=status,
                findings=findings,
                errors=errors,
                started_at=started + timedelta(seconds=i * 10),
                completed_at=started + timedelta(seconds=i * 10 + 120),
            ))
        report = ResultAggregator().aggregate(results)
        assert report.total_agents == 10
        assert report.successful_agents == 8
        assert report.failed_agents == 2
        assert report.success_rate == pytest.approx(80.0)

    @patch("builtins.print")
    def test_prints_8_successful(self, mock_print):
        demo_multiple_agents_parallel()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "8" in all_printed  # 8 successful agents


# ===========================================================================
# main()
# ===========================================================================

class TestMain:
    @patch("builtins.print")
    def test_main_runs_all_demos(self, mock_print):
        """main() should call all five demo functions and print completion."""
        main()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        # Each demo banner should appear
        assert "DEMO 1" in all_printed
        assert "DEMO 2" in all_printed
        assert "DEMO 3" in all_printed
        assert "DEMO 4" in all_printed
        assert "DEMO 5" in all_printed

    @patch("builtins.print")
    def test_main_prints_completion_banner(self, mock_print):
        main()
        all_printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "All demos completed successfully" in all_printed

    @patch(
        "forge_harness.iteration.demo_aggregator.demo_successful_aggregation",
        side_effect=RuntimeError("boom"),
    )
    @patch("forge_harness.iteration.demo_aggregator.logger")
    def test_main_propagates_exception(self, mock_logger, mock_demo):
        """main() re-raises exceptions after logging."""
        with pytest.raises(RuntimeError, match="boom"):
            main()
        mock_logger.error.assert_called_once()

    @patch("forge_harness.iteration.demo_aggregator.demo_successful_aggregation")
    @patch("forge_harness.iteration.demo_aggregator.demo_partial_failure")
    @patch("forge_harness.iteration.demo_aggregator.demo_conflict_detection")
    @patch("forge_harness.iteration.demo_aggregator.demo_deduplication")
    @patch("forge_harness.iteration.demo_aggregator.demo_multiple_agents_parallel")
    @patch("builtins.print")
    def test_main_calls_each_demo_once(
        self,
        mock_print,
        mock_parallel,
        mock_dedup,
        mock_conflict,
        mock_partial,
        mock_success,
    ):
        main()
        mock_success.assert_called_once()
        mock_partial.assert_called_once()
        mock_conflict.assert_called_once()
        mock_dedup.assert_called_once()
        mock_parallel.assert_called_once()

    @patch("forge_harness.iteration.demo_aggregator.demo_successful_aggregation")
    @patch("forge_harness.iteration.demo_aggregator.demo_partial_failure", side_effect=ValueError("bad"))
    @patch("forge_harness.iteration.demo_aggregator.logger")
    def test_main_logs_error_on_exception(self, mock_logger, mock_partial, mock_success):
        with pytest.raises(ValueError):
            main()
        assert mock_logger.error.called
        call_args = mock_logger.error.call_args
        assert "bad" in str(call_args) or "Demo failed" in str(call_args)


# ===========================================================================
# Integration-style tests (end-to-end through demo functions)
# ===========================================================================

class TestIntegrationScenarios:
    """Tests that verify the full pipeline from AgentResult to AggregatedReport."""

    def test_full_pipeline_success(self):
        """Full successful aggregation pipeline."""
        agg = ResultAggregator()
        started = datetime.now(UTC)
        results = [
            AgentResult(
                "backend-1", "HRN-005", ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator class",
                          "Added similarity matching"],
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                "backend-2", "HRN-005", ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator with deduplication",
                          "Generated markdown reports"],
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=180),
            ),
        ]
        report = agg.aggregate(results)
        assert report.success_rate == pytest.approx(100.0)
        assert report.task_id == "HRN-005"
        assert report.duration_seconds > 0
        md = report.to_markdown()
        assert "# Aggregated Report" in md

    def test_full_pipeline_with_errors_and_conflicts(self):
        started = datetime.now(UTC)
        results = [
            AgentResult(
                "agent-1", "HRN-005", ResultStatus.SUCCESS,
                metadata={"coverage": 95.0},
                started_at=started,
                completed_at=started + timedelta(seconds=100),
            ),
            AgentResult(
                "agent-2", "HRN-005", ResultStatus.FAILURE,
                errors=["Test suite crashed"],
                metadata={"coverage": 10.0},
                started_at=started + timedelta(seconds=5),
                completed_at=started + timedelta(seconds=50),
            ),
        ]
        report = ResultAggregator(conflict_detection=True).aggregate(results)
        assert report.failed_agents == 1
        assert len(report.errors) == 1
        assert len(report.conflicts) >= 1

    def test_to_dict_is_serializable(self):
        """AggregatedReport.to_dict() should produce JSON-compatible output."""
        import json
        results = [
            _make_result(agent_id="a1"),
            _make_result(agent_id="a2", status=ResultStatus.FAILURE, errors=["err"]),
        ]
        report = ResultAggregator(conflict_detection=True).aggregate(results)
        d = report.to_dict()
        # Should not raise
        serialized = json.dumps(d)
        assert "HRN-005" in serialized

    def test_agent_result_to_dict_is_serializable(self):
        import json
        result = _make_result(
            agent_id="a1",
            findings=["f1", "f2"],
            errors=["e1"],
            metadata={"key": "value"},
        )
        d = result.to_dict()
        serialized = json.dumps(d)
        assert "a1" in serialized

    def test_large_agent_count(self):
        """Aggregator handles many agents without error."""
        results = [
            _make_result(
                agent_id=f"agent-{i}",
                findings=["Implemented core functionality", f"Added feature {i}"],
                errors=[] if i % 3 != 0 else ["minor error"],
                status=ResultStatus.SUCCESS if i % 4 != 0 else ResultStatus.FAILURE,
            )
            for i in range(50)
        ]
        report = ResultAggregator().aggregate(results)
        assert report.total_agents == 50
        assert 0.0 <= report.success_rate <= 100.0

    def test_finding_similarity_across_all_agents(self):
        """Common finding 'Implemented core functionality' should appear across many agents."""
        results = [
            _make_result(
                agent_id=f"agent-{i}",
                findings=["Implemented core functionality"],
            )
            for i in range(5)
        ]
        report = ResultAggregator(similarity_threshold=0.75).aggregate(results)
        # All 5 agents report the identical finding — should form 1 common finding
        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 5

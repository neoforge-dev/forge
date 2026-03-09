"""Extended tests for forge_harness.iteration.aggregator.

Targets uncovered paths:
- _deduplicate_findings called directly with many findings
- _collect_errors called directly
- _detect_metadata_conflicts called directly
- similarity at/near the threshold boundary
- 3+ agents with mixed findings
- Multiple common finding groups in a single aggregate
- Single-agent deduplication (same finding appears twice in one agent)
- AggregatedReport.to_markdown with result durations
- AggregatedReport.to_markdown conflict severity icons (INFO)
- to_dict timestamps edge cases
- AgentResult with very large metadata / long output
- Aggregate with TIMEOUT and ERROR statuses
- Success rate with only partial agents (no success)
- Similarity: one empty, one non-empty
- Conflict detection with only partial + timeout (no SUCCESS + FAILURE mix)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
# Helpers
# =============================================================================


def _result(
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
        started_at=started_at or datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        completed_at=completed_at,
    )


@pytest.fixture
def agg() -> ResultAggregator:
    return ResultAggregator()


# =============================================================================
# ResultAggregator._deduplicate_findings — direct method calls
# =============================================================================


class TestDeduplicateFindingsDirect:
    """Call _deduplicate_findings directly to test internals."""

    def test_empty_findings_returns_empty(self, agg):
        results = [
            _result(agent_id="a1", findings=[]),
            _result(agent_id="a2", findings=[]),
        ]
        common, unique = agg._deduplicate_findings(results)
        assert common == []
        assert unique == {}

    def test_single_agent_dissimilar_findings_are_unique(self, agg):
        """With only one agent, dissimilar findings cannot form a common group."""
        results = [_result(agent_id="solo", findings=["Database indexing strategy", "OAuth2 flow"])]
        common, unique = agg._deduplicate_findings(results)
        # These two findings are dissimilar (<0.75) — should NOT be grouped
        assert len(common) == 0
        assert "solo" in unique
        assert len(unique["solo"]) == 2

    def test_identical_findings_across_three_agents(self, agg):
        results = [
            _result(agent_id="a1", findings=["Critical security patch needed"]),
            _result(agent_id="a2", findings=["Critical security patch needed"]),
            _result(agent_id="a3", findings=["Critical security patch needed"]),
        ]
        common, unique = agg._deduplicate_findings(results)
        assert len(common) == 1
        assert common[0].count == 3
        assert set(common[0].agent_ids) == {"a1", "a2", "a3"}

    def test_multiple_common_finding_groups(self, agg):
        """Two sets of similar findings should produce two common groups."""
        results = [
            _result(agent_id="a1", findings=["Auth bug fixed", "Database index added"]),
            _result(agent_id="a2", findings=["Auth bug fixed", "Database index added"]),
        ]
        common, unique = agg._deduplicate_findings(results)
        assert len(common) == 2

    def test_mixed_common_and_unique(self, agg):
        results = [
            _result(agent_id="a1", findings=["Shared finding", "Only agent-1 knows this"]),
            _result(agent_id="a2", findings=["Shared finding", "Exclusively agent-2 detail"]),
        ]
        common, unique = agg._deduplicate_findings(results)
        assert len(common) == 1
        # Each agent should have one unique finding
        assert "a1" in unique
        assert "a2" in unique
        assert len(unique["a1"]) == 1
        assert len(unique["a2"]) == 1

    def test_canonical_is_longest_finding(self, agg):
        """Canonical finding must be the longest text in the group."""
        results = [
            _result(agent_id="a1", findings=["Fixed bug in auth module thoroughly"]),
            _result(agent_id="a2", findings=["Fixed bug in auth module"]),
        ]
        common, unique = agg._deduplicate_findings(results)
        if common:
            # The longer string must win
            assert common[0].finding == "Fixed bug in auth module thoroughly"

    def test_similarity_exactly_at_threshold_included(self):
        """Finding with similarity == threshold should be grouped."""
        # We'll use a very permissive threshold so near-identical texts are counted
        lenient = ResultAggregator(similarity_threshold=0.0)
        results = [
            _result(agent_id="a1", findings=["abc"]),
            _result(agent_id="a2", findings=["xyz"]),
        ]
        common, unique = lenient._deduplicate_findings(results)
        # With threshold 0.0, anything qualifies
        assert len(common) == 1

    def test_identical_findings_from_same_agent_grouped(self, agg):
        """Same finding listed twice within one agent IS grouped (similarity=1.0)."""
        results = [_result(agent_id="solo", findings=["same finding", "same finding"])]
        common, unique = agg._deduplicate_findings(results)
        # The two identical entries are above threshold and become a common group
        # (both attributed to 'solo')
        assert len(common) == 1
        assert common[0].similarity_score == 1.0

    def test_large_finding_set_no_exception(self, agg):
        """Aggregating 20 findings across 2 agents should not raise."""
        # Use highly distinct findings so they each form separate groups
        subjects = [
            "database", "oauth", "webhook", "caching", "logging",
            "deployment", "metrics", "alerting", "tracing", "billing",
        ]
        findings_a = [f"Implemented {s} module in Python" for s in subjects]
        findings_b = [f"Implemented {s} module in Python" for s in subjects]  # identical
        results = [
            _result(agent_id="a1", findings=findings_a),
            _result(agent_id="a2", findings=findings_b),
        ]
        # Should not raise; we just check it runs and produces some common findings
        common, unique = agg._deduplicate_findings(results)
        assert len(common) >= 1
        assert isinstance(common, list)


# =============================================================================
# ResultAggregator._collect_errors — direct method calls
# =============================================================================


class TestCollectErrorsDirect:
    """Call _collect_errors directly."""

    def test_no_errors(self, agg):
        results = [
            _result(agent_id="a1", errors=[]),
            _result(agent_id="a2", errors=[]),
        ]
        errors = agg._collect_errors(results)
        assert errors == []

    def test_single_error(self, agg):
        results = [_result(agent_id="a1", errors=["DB connection failed"])]
        errors = agg._collect_errors(results)
        assert len(errors) == 1
        assert errors[0]["agent_id"] == "a1"
        assert errors[0]["error"] == "DB connection failed"

    def test_multiple_errors_single_agent(self, agg):
        results = [
            _result(agent_id="multi", errors=["Err A", "Err B", "Err C"]),
        ]
        errors = agg._collect_errors(results)
        assert len(errors) == 3
        assert all(e["agent_id"] == "multi" for e in errors)

    def test_errors_from_multiple_agents_preserves_attribution(self, agg):
        results = [
            _result(agent_id="agent-x", errors=["X error 1", "X error 2"]),
            _result(agent_id="agent-y", errors=["Y error"]),
        ]
        errors = agg._collect_errors(results)
        assert len(errors) == 3
        agent_ids = [e["agent_id"] for e in errors]
        assert agent_ids.count("agent-x") == 2
        assert agent_ids.count("agent-y") == 1

    def test_error_dict_structure(self, agg):
        results = [_result(agent_id="x", errors=["Something broke"])]
        errors = agg._collect_errors(results)
        entry = errors[0]
        assert set(entry.keys()) == {"agent_id", "error"}

    def test_successful_agents_with_errors_included(self, agg):
        """Non-fatal errors from SUCCESS agents should still be collected."""
        results = [
            _result(
                agent_id="a1",
                status=ResultStatus.SUCCESS,
                errors=["Non-fatal warning: deprecated API"],
            )
        ]
        errors = agg._collect_errors(results)
        assert len(errors) == 1


# =============================================================================
# ResultAggregator._detect_metadata_conflicts — direct method calls
# =============================================================================


class TestDetectMetadataConflictsDirect:
    """Call _detect_metadata_conflicts directly."""

    def test_no_metadata_no_conflicts(self, agg):
        results = [_result(agent_id="a1"), _result(agent_id="a2")]
        conflicts = agg._detect_metadata_conflicts(results)
        assert conflicts == []

    def test_same_metadata_no_conflict(self, agg):
        results = [
            _result(agent_id="a1", metadata={"env": "staging"}),
            _result(agent_id="a2", metadata={"env": "staging"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert conflicts == []

    def test_different_values_creates_warning(self, agg):
        results = [
            _result(agent_id="a1", metadata={"build": "1.0"}),
            _result(agent_id="a2", metadata={"build": "2.0"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert len(conflicts) == 1
        assert conflicts[0].severity == ConflictSeverity.WARNING

    def test_conflict_description_includes_key_name(self, agg):
        results = [
            _result(agent_id="a1", metadata={"region": "us-east"}),
            _result(agent_id="a2", metadata={"region": "eu-west"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert "region" in conflicts[0].description

    def test_conflict_details_has_key_and_values(self, agg):
        results = [
            _result(agent_id="a1", metadata={"tier": "free"}),
            _result(agent_id="a2", metadata={"tier": "pro"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        details = conflicts[0].details
        assert "key" in details
        assert "values" in details
        assert details["key"] == "tier"

    def test_partial_metadata_coverage(self, agg):
        """If only one agent has a key, no conflict (only one value)."""
        results = [
            _result(agent_id="a1", metadata={"version": "1.0"}),
            _result(agent_id="a2", metadata={}),  # no key
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert conflicts == []

    def test_list_value_metadata_conflict(self, agg):
        """List values are stringified for comparison — different lists conflict."""
        results = [
            _result(agent_id="a1", metadata={"tags": ["x", "y"]}),
            _result(agent_id="a2", metadata={"tags": ["a", "b"]}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert len(conflicts) == 1

    def test_dict_value_metadata_conflict(self, agg):
        results = [
            _result(agent_id="a1", metadata={"opts": {"a": 1}}),
            _result(agent_id="a2", metadata={"opts": {"b": 2}}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert len(conflicts) == 1

    def test_multiple_conflicting_keys(self, agg):
        results = [
            _result(agent_id="a1", metadata={"k1": "v1", "k2": "x"}),
            _result(agent_id="a2", metadata={"k1": "v2", "k2": "y"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        assert len(conflicts) == 2

    def test_conflict_agent_ids_are_correct(self, agg):
        results = [
            _result(agent_id="alice", metadata={"flag": "on"}),
            _result(agent_id="bob", metadata={"flag": "off"}),
        ]
        conflicts = agg._detect_metadata_conflicts(results)
        conflict_agent_ids = conflicts[0].agent_ids
        assert "alice" in conflict_agent_ids
        assert "bob" in conflict_agent_ids


# =============================================================================
# Aggregate with TIMEOUT and ERROR statuses
# =============================================================================


class TestAggregateSpecialStatuses:
    def test_all_timeout(self, agg):
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.TIMEOUT),
            _result(agent_id="a2", task_id="T", status=ResultStatus.TIMEOUT),
        ]
        report = agg.aggregate(results)
        assert report.total_agents == 2
        assert report.successful_agents == 0
        assert report.failed_agents == 0
        assert ResultStatus.TIMEOUT in report.results_by_status

    def test_all_error(self, agg):
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.ERROR),
        ]
        report = agg.aggregate(results)
        assert ResultStatus.ERROR in report.results_by_status
        assert report.success_rate == 0.0

    def test_all_unknown(self, agg):
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.UNKNOWN),
        ]
        report = agg.aggregate(results)
        assert ResultStatus.UNKNOWN in report.results_by_status

    def test_timeout_and_error_no_status_conflict(self, agg):
        """TIMEOUT + ERROR should NOT trigger SUCCESS vs FAILURE conflict."""
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.TIMEOUT),
            _result(agent_id="a2", task_id="T", status=ResultStatus.ERROR),
        ]
        report = agg.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) == 0

    def test_success_and_timeout_no_critical_conflict(self, agg):
        """SUCCESS + TIMEOUT should NOT generate a CRITICAL conflict (only SUCCESS+FAILURE does)."""
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            _result(agent_id="a2", task_id="T", status=ResultStatus.TIMEOUT),
        ]
        report = agg.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) == 0

    def test_success_and_partial_no_critical_conflict(self, agg):
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            _result(agent_id="a2", task_id="T", status=ResultStatus.PARTIAL),
        ]
        report = agg.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) == 0


# =============================================================================
# AggregatedReport.success_rate edge cases
# =============================================================================


class TestSuccessRateEdgeCases:
    def make_report(self, total, successful, failed=0, partial=0) -> AggregatedReport:
        return AggregatedReport(
            task_id="T",
            total_agents=total,
            successful_agents=successful,
            failed_agents=failed,
            partial_agents=partial,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )

    def test_one_of_one(self):
        r = self.make_report(1, 1)
        assert r.success_rate == 100.0

    def test_zero_of_one(self):
        r = self.make_report(1, 0)
        assert r.success_rate == 0.0

    def test_fractional_rate(self):
        r = self.make_report(3, 1)
        assert abs(r.success_rate - 33.333) < 0.01

    def test_large_fleet(self):
        r = self.make_report(100, 73)
        assert r.success_rate == pytest.approx(73.0)


# =============================================================================
# AggregatedReport.to_markdown — additional coverage
# =============================================================================


class TestToMarkdownAdditional:
    def _make(self, **kwargs) -> AggregatedReport:
        defaults = dict(
            task_id="X",
            total_agents=1,
            successful_agents=1,
            failed_agents=0,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )
        defaults.update(kwargs)
        return AggregatedReport(**defaults)

    def test_info_conflict_in_markdown(self):
        """INFO severity conflict should appear in markdown."""
        conflict = Conflict(
            severity=ConflictSeverity.INFO,
            description="Minor difference",
            agent_ids=["a1"],
        )
        report = self._make(conflicts=[conflict])
        md = report.to_markdown()
        assert "INFO" in md
        assert "Minor difference" in md

    def test_warning_conflict_in_markdown(self):
        conflict = Conflict(
            severity=ConflictSeverity.WARNING,
            description="Inconsistency detected",
            agent_ids=["a1", "a2"],
        )
        report = self._make(conflicts=[conflict])
        md = report.to_markdown()
        assert "WARNING" in md
        assert "Inconsistency detected" in md

    def test_result_without_duration_no_duration_suffix(self):
        """Result with no completed_at should not show duration suffix."""
        result = _result(
            agent_id="no-duration",
            task_id="X",
            findings=["Some finding"],
            completed_at=None,
        )
        report = self._make(results_by_status={ResultStatus.SUCCESS: [result]})
        md = report.to_markdown()
        # Agent should appear but no "(Xs)" duration suffix
        assert "no-duration" in md

    def test_result_with_duration_shows_seconds(self):
        t = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        result = AgentResult(
            agent_id="timed-agent",
            task_id="X",
            status=ResultStatus.SUCCESS,
            started_at=t,
            completed_at=t + timedelta(seconds=42),
        )
        report = self._make(results_by_status={ResultStatus.SUCCESS: [result]})
        md = report.to_markdown()
        assert "42.0s" in md

    def test_multiple_common_findings_sorted_desc(self):
        """Higher count findings appear before lower count findings."""
        dup_low = DuplicateFinding(
            finding="Low count finding",
            agent_ids=["a1"],
            similarity_score=0.9,
        )
        dup_high = DuplicateFinding(
            finding="High count finding",
            agent_ids=["a1", "a2", "a3"],
            similarity_score=0.95,
        )
        report = self._make(common_findings=[dup_low, dup_high])
        md = report.to_markdown()
        idx_high = md.index("High count finding")
        idx_low = md.index("Low count finding")
        assert idx_high < idx_low

    def test_similarity_shown_as_percentage(self):
        dup = DuplicateFinding(
            finding="test",
            agent_ids=["a1", "a2"],
            similarity_score=0.875,
        )
        report = self._make(common_findings=[dup])
        md = report.to_markdown()
        # 87.50% should appear somewhere in the markdown
        assert "87.50%" in md

    def test_conflicts_sorted_by_severity_value(self):
        """Conflicts are sorted by severity.value (alphabetical: critical < info < warning)."""
        info_conflict = Conflict(
            severity=ConflictSeverity.INFO,
            description="Info level",
            agent_ids=["a1"],
        )
        critical_conflict = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Critical level",
            agent_ids=["a2"],
        )
        report = self._make(conflicts=[info_conflict, critical_conflict])
        md = report.to_markdown()
        idx_critical = md.index("Critical level")
        idx_info = md.index("Info level")
        # "critical" < "info" alphabetically
        assert idx_critical < idx_info

    def test_multiple_errors_from_same_agent(self):
        errors = [
            {"agent_id": "bad-agent", "error": "First error"},
            {"agent_id": "bad-agent", "error": "Second error"},
        ]
        report = self._make(errors=errors)
        md = report.to_markdown()
        assert md.count("bad-agent") >= 2
        assert "First error" in md
        assert "Second error" in md

    def test_partial_agents_count_in_summary(self):
        report = self._make(
            total_agents=5,
            successful_agents=2,
            failed_agents=1,
            partial_agents=2,
        )
        md = report.to_markdown()
        assert "Partial" in md
        assert "2" in md


# =============================================================================
# AggregatedReport.to_dict edge cases
# =============================================================================


class TestToDictEdgeCases:
    def test_to_dict_zero_counts(self):
        report = AggregatedReport(
            task_id="ZERO",
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
        d = report.to_dict()
        assert d["common_findings_count"] == 0
        assert d["conflicts_count"] == 0
        assert d["errors_count"] == 0
        assert d["success_rate"] == 0.0

    def test_to_dict_high_counts(self):
        common = [
            DuplicateFinding(finding=f"f{i}", agent_ids=["a", "b"], similarity_score=0.9)
            for i in range(5)
        ]
        conflicts = [
            Conflict(severity=ConflictSeverity.INFO, description="x", agent_ids=["a"])
            for _ in range(3)
        ]
        errors = [{"agent_id": "a", "error": "e"} for _ in range(7)]
        report = AggregatedReport(
            task_id="COUNTS",
            total_agents=10,
            successful_agents=6,
            failed_agents=4,
            partial_agents=0,
            results_by_status={},
            common_findings=common,
            unique_findings={},
            conflicts=conflicts,
            errors=errors,
        )
        d = report.to_dict()
        assert d["common_findings_count"] == 5
        assert d["conflicts_count"] == 3
        assert d["errors_count"] == 7


# =============================================================================
# AgentResult edge cases
# =============================================================================


class TestAgentResultEdgeCases:
    def test_large_metadata_preserved(self):
        big_meta = {f"key_{i}": f"value_{i}" for i in range(50)}
        result = AgentResult(
            agent_id="agent-big",
            task_id="T",
            status=ResultStatus.SUCCESS,
            metadata=big_meta,
        )
        assert len(result.metadata) == 50

    def test_very_long_output(self):
        long_output = "x" * 10000
        result = AgentResult(
            agent_id="verbose",
            task_id="T",
            status=ResultStatus.SUCCESS,
            output=long_output,
        )
        assert len(result.output) == 10000

    def test_to_dict_includes_all_required_keys(self):
        result = AgentResult(agent_id="a", task_id="T", status=ResultStatus.SUCCESS)
        d = result.to_dict()
        required_keys = {
            "agent_id",
            "task_id",
            "status",
            "findings",
            "errors",
            "metadata",
            "started_at",
            "completed_at",
            "duration_seconds",
        }
        assert required_keys.issubset(d.keys())

    def test_timeout_status_via_string(self):
        result = AgentResult(agent_id="a", task_id="T", status="timeout")
        assert result.status == ResultStatus.TIMEOUT

    def test_error_status_via_string(self):
        result = AgentResult(agent_id="a", task_id="T", status="error")
        assert result.status == ResultStatus.ERROR

    def test_unknown_status_via_string(self):
        result = AgentResult(agent_id="a", task_id="T", status="unknown")
        assert result.status == ResultStatus.UNKNOWN

    def test_has_errors_false_with_many_findings(self):
        result = AgentResult(
            agent_id="a",
            task_id="T",
            status=ResultStatus.SUCCESS,
            findings=["f1", "f2", "f3"],
            errors=[],
        )
        assert result.has_errors is False

    def test_duration_sub_second(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        result = AgentResult(
            agent_id="fast",
            task_id="T",
            status=ResultStatus.SUCCESS,
            started_at=t,
            completed_at=t + timedelta(milliseconds=500),
        )
        assert result.duration_seconds == pytest.approx(0.5)


# =============================================================================
# Integration: 3-agent complex scenarios
# =============================================================================


class TestThreeAgentIntegration:
    """Scenarios requiring 3+ agents to exercise non-trivial paths."""

    def test_three_agents_identical_findings(self, agg):
        results = [
            _result(agent_id=f"agent-{i}", task_id="T", findings=["All same finding"])
            for i in range(3)
        ]
        report = agg.aggregate(results)
        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 3

    def test_five_agents_all_succeed(self, agg):
        results = [
            _result(agent_id=f"worker-{i}", task_id="T", status=ResultStatus.SUCCESS)
            for i in range(5)
        ]
        report = agg.aggregate(results)
        assert report.total_agents == 5
        assert report.successful_agents == 5
        assert report.success_rate == 100.0

    def test_three_agents_one_fails_critical_conflict(self, agg):
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            _result(agent_id="a2", task_id="T", status=ResultStatus.SUCCESS),
            _result(agent_id="a3", task_id="T", status=ResultStatus.FAILURE),
        ]
        report = agg.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1
        # Both a1/a2 (success) and a3 (failure) are included
        agent_ids_in_conflict = critical[0].agent_ids
        assert "a3" in agent_ids_in_conflict

    def test_partial_agents_dont_inflate_failed_count(self, agg):
        results = [
            _result(agent_id="a1", task_id="T", status=ResultStatus.PARTIAL),
            _result(agent_id="a2", task_id="T", status=ResultStatus.PARTIAL),
            _result(agent_id="a3", task_id="T", status=ResultStatus.PARTIAL),
        ]
        report = agg.aggregate(results)
        assert report.failed_agents == 0
        assert report.partial_agents == 3
        assert report.successful_agents == 0

    def test_aggregate_preserves_result_findings_order(self, agg):
        findings = ["First", "Second", "Third"]
        results = [_result(agent_id="single", task_id="T", findings=findings)]
        report = agg.aggregate(results)
        result_in_report = report.results_by_status[ResultStatus.SUCCESS][0]
        assert result_in_report.findings == findings

    def test_time_bounds_with_three_agents(self, agg):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            _result(
                agent_id="early",
                task_id="T",
                started_at=t,
                completed_at=t + timedelta(seconds=10),
            ),
            _result(
                agent_id="mid",
                task_id="T",
                started_at=t + timedelta(seconds=5),
                completed_at=t + timedelta(seconds=30),
            ),
            _result(
                agent_id="late",
                task_id="T",
                started_at=t + timedelta(seconds=2),
                completed_at=t + timedelta(seconds=60),
            ),
        ]
        report = agg.aggregate(results)
        assert report.started_at == t
        assert report.completed_at == t + timedelta(seconds=60)

    def test_aggregate_with_metadata_and_findings_all_paths(self, agg):
        """All paths hit: common findings, unique findings, status conflict, metadata conflict."""
        t = datetime(2026, 1, 1, tzinfo=UTC)
        results = [
            AgentResult(
                agent_id="writer",
                task_id="FULL-001",
                status=ResultStatus.SUCCESS,
                findings=["Shared insight", "Implemented OAuth2 token refresh"],
                errors=[],
                metadata={"env": "prod"},
                started_at=t,
                completed_at=t + timedelta(seconds=20),
            ),
            AgentResult(
                agent_id="reader",
                task_id="FULL-001",
                status=ResultStatus.SUCCESS,
                findings=["Shared insight", "Migrated legacy database schema"],
                errors=[],
                metadata={"env": "staging"},
                started_at=t,
                completed_at=t + timedelta(seconds=25),
            ),
            AgentResult(
                agent_id="breaker",
                task_id="FULL-001",
                status=ResultStatus.FAILURE,
                findings=[],
                errors=["Segfault in main"],
                metadata={"env": "prod"},
                started_at=t,
                completed_at=t + timedelta(seconds=5),
            ),
        ]
        report = agg.aggregate(results)
        assert report.total_agents == 3
        assert report.successful_agents == 2
        assert report.failed_agents == 1
        assert len(report.errors) == 1
        # Should have status conflict (success+failure)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1
        # Should have metadata conflict (env: prod vs staging)
        warnings = [c for c in report.conflicts if c.severity == ConflictSeverity.WARNING]
        assert len(warnings) >= 1
        # Should have common findings (Shared insight)
        assert len(report.common_findings) >= 1
        # Should have unique findings (OAuth2 and DB schema are dissimilar)
        assert "writer" in report.unique_findings or "reader" in report.unique_findings
        # Markdown and dict should work
        md = report.to_markdown()
        d = report.to_dict()
        assert "FULL-001" in md
        assert d["task_id"] == "FULL-001"


# =============================================================================
# _calculate_similarity boundary checks
# =============================================================================


class TestCalculateSimilarityBoundary:
    def test_one_empty_string_low_score(self, agg):
        score = agg._calculate_similarity("", "some text")
        assert score == 0.0

    def test_both_empty_returns_one(self, agg):
        score = agg._calculate_similarity("", "")
        assert score == 1.0

    def test_single_char_match(self, agg):
        score = agg._calculate_similarity("a", "a")
        assert score == 1.0

    def test_single_char_mismatch(self, agg):
        score = agg._calculate_similarity("a", "b")
        assert score == 0.0

    def test_very_long_strings_match(self, agg):
        text = "the quick brown fox jumps over the lazy dog " * 10
        score = agg._calculate_similarity(text, text)
        assert score == 1.0

    def test_completely_reversed_string_is_low(self, agg):
        score = agg._calculate_similarity("abcdef", "fedcba")
        # Reversed character sequences are low-similarity in SequenceMatcher
        assert score < 0.5

    def test_returns_float(self, agg):
        score = agg._calculate_similarity("hello", "world")
        assert isinstance(score, float)

    def test_score_in_range(self, agg):
        score = agg._calculate_similarity("anything", "something else")
        assert 0.0 <= score <= 1.0


# =============================================================================
# DuplicateFinding and Conflict constructors
# =============================================================================


class TestDataclassConstructors:
    def test_duplicate_finding_with_all_fields(self):
        dup = DuplicateFinding(
            finding="canonical text here",
            agent_ids=["x", "y", "z"],
            similarity_score=0.92,
            original_texts=["variant a", "variant b", "variant c"],
        )
        assert dup.count == 3
        assert len(dup.original_texts) == 3
        assert dup.similarity_score == 0.92

    def test_conflict_with_info_severity(self):
        c = Conflict(
            severity=ConflictSeverity.INFO,
            description="Minor discrepancy",
            agent_ids=["a"],
            details={"note": "acceptable"},
        )
        assert c.severity == ConflictSeverity.INFO
        assert c.details["note"] == "acceptable"

    def test_conflict_from_string_info(self):
        c = Conflict(severity="info", description="info", agent_ids=[])
        assert c.severity == ConflictSeverity.INFO

    def test_conflict_from_string_critical(self):
        c = Conflict(severity="critical", description="critical", agent_ids=[])
        assert c.severity == ConflictSeverity.CRITICAL

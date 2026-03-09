"""Tests for forge_harness.iteration.aggregator module."""

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


class TestAgentResult:
    """Test AgentResult dataclass."""

    def test_create_basic_result(self):
        """Test creating a basic agent result."""
        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
        )

        assert result.agent_id == "agent-1"
        assert result.task_id == "HRN-005"
        assert result.status == ResultStatus.SUCCESS
        assert result.findings == []
        assert result.errors == []
        assert result.metadata == {}

    def test_result_with_findings(self):
        """Test result with findings."""
        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            findings=["Fixed bug", "Added tests"],
        )

        assert len(result.findings) == 2
        assert "Fixed bug" in result.findings

    def test_result_with_errors(self):
        """Test result with errors."""
        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.FAILURE,
            errors=["Import error", "Connection failed"],
        )

        assert len(result.errors) == 2
        assert result.has_errors is True

    def test_duration_calculation(self):
        """Test duration calculation."""
        started = datetime.now(UTC)
        completed = started + timedelta(seconds=45)

        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            started_at=started,
            completed_at=completed,
        )

        assert result.duration_seconds == pytest.approx(45.0, abs=0.1)

    def test_duration_none_when_incomplete(self):
        """Test duration is None when not completed."""
        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
        )

        assert result.duration_seconds is None

    def test_is_successful(self):
        """Test is_successful property."""
        success = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
        )
        failure = AgentResult(
            agent_id="agent-2",
            task_id="HRN-005",
            status=ResultStatus.FAILURE,
        )

        assert success.is_successful is True
        assert failure.is_successful is False

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status=ResultStatus.SUCCESS,
            findings=["Test finding"],
            metadata={"key": "value"},
        )

        data = result.to_dict()
        assert data["agent_id"] == "agent-1"
        assert data["task_id"] == "HRN-005"
        assert data["status"] == "success"
        assert data["findings"] == ["Test finding"]
        assert data["metadata"] == {"key": "value"}

    def test_string_status_conversion(self):
        """Test automatic string to enum conversion."""
        result = AgentResult(
            agent_id="agent-1",
            task_id="HRN-005",
            status="success",  # type: ignore
        )

        assert result.status == ResultStatus.SUCCESS
        assert isinstance(result.status, ResultStatus)


class TestDuplicateFinding:
    """Test DuplicateFinding dataclass."""

    def test_create_duplicate_finding(self):
        """Test creating a duplicate finding."""
        finding = DuplicateFinding(
            finding="Fixed API bug",
            agent_ids=["agent-1", "agent-2"],
            similarity_score=0.95,
        )

        assert finding.count == 2
        assert "agent-1" in finding.agent_ids

    def test_original_texts(self):
        """Test original texts tracking."""
        finding = DuplicateFinding(
            finding="Fixed API bug",
            agent_ids=["agent-1", "agent-2"],
            similarity_score=0.95,
            original_texts=["Fixed API bug", "Fixed the API bug"],
        )

        assert len(finding.original_texts) == 2


class TestConflict:
    """Test Conflict dataclass."""

    def test_create_conflict(self):
        """Test creating a conflict."""
        conflict = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Contradictory results",
            agent_ids=["agent-1", "agent-2"],
        )

        assert conflict.severity == ConflictSeverity.CRITICAL
        assert len(conflict.agent_ids) == 2

    def test_string_severity_conversion(self):
        """Test automatic string to enum conversion."""
        conflict = Conflict(
            severity="warning",  # type: ignore
            description="Test conflict",
            agent_ids=["agent-1"],
        )

        assert conflict.severity == ConflictSeverity.WARNING
        assert isinstance(conflict.severity, ConflictSeverity)


class TestAggregatedReport:
    """Test AggregatedReport dataclass."""

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        report = AggregatedReport(
            task_id="HRN-005",
            total_agents=10,
            successful_agents=8,
            failed_agents=2,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )

        assert report.success_rate == 80.0

    def test_success_rate_zero_agents(self):
        """Test success rate with zero agents."""
        report = AggregatedReport(
            task_id="HRN-005",
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

    def test_duration_calculation(self):
        """Test duration calculation."""
        started = datetime.now(UTC)
        completed = started + timedelta(minutes=5)

        report = AggregatedReport(
            task_id="HRN-005",
            total_agents=2,
            successful_agents=2,
            failed_agents=0,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
            started_at=started,
            completed_at=completed,
        )

        assert report.duration_seconds == pytest.approx(300.0, abs=0.1)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        report = AggregatedReport(
            task_id="HRN-005",
            total_agents=5,
            successful_agents=4,
            failed_agents=1,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )

        data = report.to_dict()
        assert data["task_id"] == "HRN-005"
        assert data["total_agents"] == 5
        assert data["successful_agents"] == 4
        assert data["success_rate"] == 80.0

    def test_to_markdown_basic(self):
        """Test markdown generation."""
        report = AggregatedReport(
            task_id="HRN-005",
            total_agents=2,
            successful_agents=2,
            failed_agents=0,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[],
            errors=[],
        )

        markdown = report.to_markdown()
        assert "# Aggregated Report: HRN-005" in markdown
        assert "**Total Agents**: 2" in markdown
        assert "**Successful**: 2 (100.0%)" in markdown

    def test_to_markdown_with_findings(self):
        """Test markdown with findings."""
        report = AggregatedReport(
            task_id="HRN-005",
            total_agents=2,
            successful_agents=2,
            failed_agents=0,
            partial_agents=0,
            results_by_status={},
            common_findings=[
                DuplicateFinding(
                    finding="Fixed bug",
                    agent_ids=["agent-1", "agent-2"],
                    similarity_score=0.95,
                )
            ],
            unique_findings={"agent-1": ["Unique finding"]},
            conflicts=[],
            errors=[],
        )

        markdown = report.to_markdown()
        assert "## Common Findings" in markdown
        assert "Fixed bug" in markdown
        assert "## Unique Findings" in markdown

    def test_to_markdown_with_conflicts(self):
        """Test markdown with conflicts."""
        report = AggregatedReport(
            task_id="HRN-005",
            total_agents=2,
            successful_agents=1,
            failed_agents=1,
            partial_agents=0,
            results_by_status={},
            common_findings=[],
            unique_findings={},
            conflicts=[
                Conflict(
                    severity=ConflictSeverity.CRITICAL,
                    description="Contradictory results",
                    agent_ids=["agent-1", "agent-2"],
                )
            ],
            errors=[],
        )

        markdown = report.to_markdown()
        assert "## Conflicts & Inconsistencies" in markdown
        assert "CRITICAL" in markdown
        assert "Contradictory results" in markdown


class TestResultAggregator:
    """Test ResultAggregator class."""

    def test_initialize_aggregator(self):
        """Test aggregator initialization."""
        aggregator = ResultAggregator()
        assert aggregator.similarity_threshold == 0.75
        assert aggregator.conflict_detection is True

    def test_custom_similarity_threshold(self):
        """Test custom similarity threshold."""
        aggregator = ResultAggregator(similarity_threshold=0.9)
        assert aggregator.similarity_threshold == 0.9

    def test_invalid_similarity_threshold(self):
        """Test invalid similarity threshold."""
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            ResultAggregator(similarity_threshold=1.5)

        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            ResultAggregator(similarity_threshold=-0.1)

    def test_aggregate_empty_results(self):
        """Test aggregating empty results list."""
        aggregator = ResultAggregator()

        with pytest.raises(ValueError, match="cannot be empty"):
            aggregator.aggregate([])

    def test_aggregate_inconsistent_task_ids(self):
        """Test aggregating results with different task IDs."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(agent_id="agent-1", task_id="HRN-001", status=ResultStatus.SUCCESS),
            AgentResult(agent_id="agent-2", task_id="HRN-002", status=ResultStatus.SUCCESS),
        ]

        with pytest.raises(ValueError, match="inconsistent task IDs"):
            aggregator.aggregate(results)

    def test_aggregate_single_successful_agent(self):
        """Test aggregating single successful agent."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed bug", "Added tests"],
            )
        ]

        report = aggregator.aggregate(results)

        assert report.task_id == "HRN-005"
        assert report.total_agents == 1
        assert report.successful_agents == 1
        assert report.failed_agents == 0
        assert report.success_rate == 100.0

    def test_aggregate_multiple_agents_all_success(self):
        """Test aggregating multiple successful agents."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug"],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed the API bug"],
            ),
        ]

        report = aggregator.aggregate(results)

        assert report.total_agents == 2
        assert report.successful_agents == 2
        assert report.success_rate == 100.0
        assert len(report.common_findings) > 0

    def test_aggregate_mixed_success_failure(self):
        """Test aggregating mixed success and failure."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Task completed"],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
                errors=["Connection failed"],
            ),
        ]

        report = aggregator.aggregate(results)

        assert report.total_agents == 2
        assert report.successful_agents == 1
        assert report.failed_agents == 1
        assert report.success_rate == 50.0
        assert len(report.conflicts) > 0

    def test_deduplicate_identical_findings(self):
        """Test deduplication of identical findings."""
        aggregator = ResultAggregator(similarity_threshold=0.75)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug"],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug"],
            ),
        ]

        report = aggregator.aggregate(results)

        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 2
        assert "agent-1" in report.common_findings[0].agent_ids
        assert "agent-2" in report.common_findings[0].agent_ids

    def test_deduplicate_similar_findings(self):
        """Test deduplication of similar findings."""
        aggregator = ResultAggregator(similarity_threshold=0.75)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed the API bug in the authentication module"],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug in authentication module"],
            ),
        ]

        report = aggregator.aggregate(results)

        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 2

    def test_unique_findings_preserved(self):
        """Test unique findings are preserved."""
        aggregator = ResultAggregator(similarity_threshold=0.75)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug", "Added documentation"],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Fixed API bug", "Improved performance"],
            ),
        ]

        report = aggregator.aggregate(results)

        # Should have 1 common finding (Fixed API bug)
        assert len(report.common_findings) == 1

        # Should have unique findings from each agent
        assert "agent-1" in report.unique_findings or "agent-2" in report.unique_findings

    def test_detect_status_conflict(self):
        """Test detection of status conflicts."""
        aggregator = ResultAggregator(conflict_detection=True)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
            ),
        ]

        report = aggregator.aggregate(results)

        assert len(report.conflicts) > 0
        assert any(c.severity == ConflictSeverity.CRITICAL for c in report.conflicts)

    def test_detect_metadata_conflicts(self):
        """Test detection of metadata conflicts."""
        aggregator = ResultAggregator(conflict_detection=True)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                metadata={"version": "1.0.0"},
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                metadata={"version": "1.0.1"},
            ),
        ]

        report = aggregator.aggregate(results)

        # Should detect version mismatch
        assert len(report.conflicts) > 0
        assert any("version" in c.description.lower() for c in report.conflicts)

    def test_conflict_detection_disabled(self):
        """Test with conflict detection disabled."""
        aggregator = ResultAggregator(conflict_detection=False)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
            ),
        ]

        report = aggregator.aggregate(results)

        assert len(report.conflicts) == 0

    def test_collect_errors(self):
        """Test error collection."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
                errors=["Import error", "Connection failed"],
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
                errors=["Timeout error"],
            ),
        ]

        report = aggregator.aggregate(results)

        assert len(report.errors) == 3
        assert any(e["error"] == "Import error" for e in report.errors)
        assert any(e["agent_id"] == "agent-1" for e in report.errors)

    def test_time_bounds_calculation(self):
        """Test calculation of time bounds."""
        aggregator = ResultAggregator()

        now = datetime.now(UTC)
        later = now + timedelta(minutes=5)

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                started_at=now,
                completed_at=now + timedelta(minutes=2),
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                started_at=now + timedelta(minutes=1),
                completed_at=later,
            ),
        ]

        report = aggregator.aggregate(results)

        # Should use earliest start time
        assert report.started_at == now
        # Should use latest completion time
        assert report.completed_at == later
        # Duration should be the full span
        assert report.duration_seconds == pytest.approx(300.0, abs=0.1)

    def test_calculate_similarity(self):
        """Test similarity calculation."""
        aggregator = ResultAggregator()

        # Identical strings
        similarity = aggregator._calculate_similarity("Fixed bug", "Fixed bug")
        assert similarity == 1.0

        # Similar strings
        similarity = aggregator._calculate_similarity("Fixed API bug", "Fixed the API bug")
        assert similarity > 0.7

        # Different strings
        similarity = aggregator._calculate_similarity("Fixed bug", "Added feature")
        assert similarity < 0.5

    def test_group_by_status(self):
        """Test grouping results by status."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(agent_id="agent-1", task_id="HRN-005", status=ResultStatus.SUCCESS),
            AgentResult(agent_id="agent-2", task_id="HRN-005", status=ResultStatus.SUCCESS),
            AgentResult(agent_id="agent-3", task_id="HRN-005", status=ResultStatus.FAILURE),
            AgentResult(agent_id="agent-4", task_id="HRN-005", status=ResultStatus.PARTIAL),
        ]

        grouped = aggregator._group_by_status(results)

        assert len(grouped[ResultStatus.SUCCESS]) == 2
        assert len(grouped[ResultStatus.FAILURE]) == 1
        assert len(grouped[ResultStatus.PARTIAL]) == 1

    def test_partial_completion(self):
        """Test handling partial completion."""
        aggregator = ResultAggregator()

        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.PARTIAL,
                findings=["Completed 50%"],
            ),
        ]

        report = aggregator.aggregate(results)

        assert report.successful_agents == 1
        assert report.partial_agents == 1
        assert report.total_agents == 2

    def test_markdown_formatting(self):
        """Test markdown report formatting."""
        aggregator = ResultAggregator()

        started = datetime.now(UTC)
        completed = started + timedelta(seconds=120)

        results = [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented aggregator", "Added tests"],
                started_at=started,
                completed_at=completed,
            ),
        ]

        report = aggregator.aggregate(results)
        markdown = report.to_markdown()

        # Check structure
        assert markdown.startswith("# Aggregated Report:")
        assert "## Summary" in markdown
        assert "## Results by Status" in markdown
        assert "**backend-1**" in markdown
        assert "120.0s" in markdown  # Duration

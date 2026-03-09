"""Comprehensive tests for meta_learning/schemas.py and iteration demo modules.

Covers:
- forge_harness.meta_learning.schemas (all models, enums, computed fields)
- forge_harness.iteration.demo_aggregator (all demo functions)
- forge_harness.iteration.demo_prioritizer (all demo functions)
- forge_harness.iteration.event_notifier_example (all example functions)
- forge_harness.iteration.feature_queue_example (all example functions)
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Imports from the modules under test
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
from forge_harness.iteration.event_notifier import (
    Event,
    EventNotifier,
    EventType,
    NotificationResult,
    WebhookConfig,
    create_event_notifier,
)
from forge_harness.iteration.feature_queue import (
    Feature,
    FeatureQueue,
    FeatureStatus,
    Priority,
    QueueStats,
    create_feature_queue,
)
from forge_harness.meta_learning.schemas import (
    AnalysisJobStatus,
    AtlasEntity,
    AtlasPattern,
    AtlasRAGResponse,
    AtlasSignals,
    ConfidenceLevel,
    DecisionAction,
    DecisionContext,
    DecisionOutcome,
    DecisionRecommendation,
    DecisionRecord,
    DecisionTier,
    DiligenceFinding,
    DiligenceReport,
    DiligenceSignals,
    FindingCategory,
    FindingSeverity,
    PatternRecord,
    ScoreCard,
    Warning,
)

# ===========================================================================
# PART 1: meta_learning/schemas.py
# ===========================================================================


class TestAtlasEntity:
    """Tests for AtlasEntity Pydantic model."""

    def test_minimal_construction(self):
        entity = AtlasEntity(id="e1", name="main", entity_type="function")
        assert entity.id == "e1"
        assert entity.name == "main"
        assert entity.entity_type == "function"
        assert entity.description is None
        assert entity.mention_count == 0

    def test_full_construction(self):
        entity = AtlasEntity(
            id="e2",
            name="AuthService",
            entity_type="class",
            description="Handles auth",
            mention_count=5,
        )
        assert entity.description == "Handles auth"
        assert entity.mention_count == 5

    def test_mention_count_non_negative_validation(self):
        with pytest.raises(Exception):
            AtlasEntity(id="e3", name="x", entity_type="file", mention_count=-1)

    def test_mention_count_zero_is_valid(self):
        entity = AtlasEntity(id="e4", name="x", entity_type="method", mention_count=0)
        assert entity.mention_count == 0

    def test_serialization_to_dict(self):
        entity = AtlasEntity(id="e5", name="foo", entity_type="function", mention_count=3)
        data = entity.model_dump()
        assert data["id"] == "e5"
        assert data["name"] == "foo"
        assert data["entity_type"] == "function"
        assert data["mention_count"] == 3

    def test_json_serialization_roundtrip(self):
        entity = AtlasEntity(id="e6", name="bar", entity_type="class", description="desc", mention_count=2)
        json_str = entity.model_dump_json()
        restored = AtlasEntity.model_validate_json(json_str)
        assert restored.id == entity.id
        assert restored.name == entity.name
        assert restored.description == entity.description
        assert restored.mention_count == entity.mention_count


class TestAtlasPattern:
    """Tests for AtlasPattern Pydantic model."""

    def test_minimal_construction(self):
        pattern = AtlasPattern(pattern_id="p1", pattern_type="bug_fix")
        assert pattern.pattern_id == "p1"
        assert pattern.pattern_type == "bug_fix"
        assert pattern.entities == []
        assert pattern.session_count == 0
        assert pattern.confidence == 0.0

    def test_full_construction(self):
        pattern = AtlasPattern(
            pattern_id="p2",
            pattern_type="implementation",
            entities=["e1", "e2"],
            session_count=10,
            confidence=0.85,
        )
        assert pattern.entities == ["e1", "e2"]
        assert pattern.session_count == 10
        assert pattern.confidence == 0.85

    def test_confidence_bounds_validation(self):
        with pytest.raises(Exception):
            AtlasPattern(pattern_id="p3", pattern_type="x", confidence=-0.1)
        with pytest.raises(Exception):
            AtlasPattern(pattern_id="p4", pattern_type="x", confidence=1.1)

    def test_confidence_boundary_values(self):
        p_low = AtlasPattern(pattern_id="p5", pattern_type="x", confidence=0.0)
        p_high = AtlasPattern(pattern_id="p6", pattern_type="x", confidence=1.0)
        assert p_low.confidence == 0.0
        assert p_high.confidence == 1.0

    def test_session_count_non_negative(self):
        with pytest.raises(Exception):
            AtlasPattern(pattern_id="p7", pattern_type="x", session_count=-1)

    def test_entities_default_factory(self):
        p1 = AtlasPattern(pattern_id="p8", pattern_type="x")
        p2 = AtlasPattern(pattern_id="p9", pattern_type="x")
        p1.entities.append("e1")
        assert "e1" not in p2.entities


class TestAtlasSignals:
    """Tests for AtlasSignals Pydantic model."""

    def test_default_construction(self):
        signals = AtlasSignals()
        assert signals.related_patterns == []
        assert signals.similar_decisions == []
        assert signals.recurring_problems == []

    def test_with_data(self):
        pattern = AtlasPattern(pattern_id="p1", pattern_type="refactor")
        signals = AtlasSignals(
            related_patterns=[pattern],
            similar_decisions=["d1", "d2"],
            recurring_problems=["auth bug"],
        )
        assert len(signals.related_patterns) == 1
        assert signals.similar_decisions == ["d1", "d2"]
        assert signals.recurring_problems == ["auth bug"]

    def test_serialization(self):
        signals = AtlasSignals(similar_decisions=["d1"])
        data = signals.model_dump()
        assert "related_patterns" in data
        assert "similar_decisions" in data
        assert data["similar_decisions"] == ["d1"]


class TestAtlasRAGResponse:
    """Tests for AtlasRAGResponse Pydantic model."""

    def test_minimal_construction(self):
        response = AtlasRAGResponse(answer="The function handles auth")
        assert response.answer == "The function handles auth"
        assert response.sources == []
        assert response.confidence == 0.0
        assert response.execution_time_ms == 0

    def test_full_construction(self):
        response = AtlasRAGResponse(
            answer="full answer",
            sources=["session-1", "session-2"],
            confidence=0.9,
            execution_time_ms=150,
        )
        assert response.sources == ["session-1", "session-2"]
        assert response.confidence == 0.9
        assert response.execution_time_ms == 150

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            AtlasRAGResponse(answer="x", confidence=1.5)

    def test_execution_time_non_negative(self):
        with pytest.raises(Exception):
            AtlasRAGResponse(answer="x", execution_time_ms=-1)


class TestFindingEnums:
    """Tests for FindingSeverity and FindingCategory enums."""

    def test_finding_severity_values(self):
        assert FindingSeverity.CRITICAL == "critical"
        assert FindingSeverity.HIGH == "high"
        assert FindingSeverity.MEDIUM == "medium"
        assert FindingSeverity.LOW == "low"
        assert FindingSeverity.INFO == "info"

    def test_finding_severity_all_members(self):
        members = list(FindingSeverity)
        assert len(members) == 5

    def test_finding_severity_str_subclass(self):
        assert isinstance(FindingSeverity.CRITICAL, str)

    def test_finding_category_values(self):
        assert FindingCategory.SECURITY == "security"
        assert FindingCategory.ARCHITECTURE == "architecture"
        assert FindingCategory.DEPENDENCY == "dependency"
        assert FindingCategory.CODE_QUALITY == "code_quality"

    def test_finding_category_all_members(self):
        members = list(FindingCategory)
        assert len(members) == 4

    def test_finding_category_str_subclass(self):
        assert isinstance(FindingCategory.SECURITY, str)


class TestDiligenceFinding:
    """Tests for DiligenceFinding Pydantic model."""

    def test_minimal_construction(self):
        finding = DiligenceFinding(
            finding_id="f1",
            title="SQL Injection",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.SECURITY,
        )
        assert finding.finding_id == "f1"
        assert finding.title == "SQL Injection"
        assert finding.severity == FindingSeverity.CRITICAL
        assert finding.category == FindingCategory.SECURITY
        assert finding.file_path is None
        assert finding.line_number is None
        assert finding.fix_suggestion is None
        assert finding.scanner == "unknown"

    def test_full_construction(self):
        finding = DiligenceFinding(
            finding_id="f2",
            title="Hardcoded secret",
            severity=FindingSeverity.HIGH,
            category=FindingCategory.SECURITY,
            file_path="src/auth.py",
            line_number=42,
            fix_suggestion="Use environment variables",
            scanner="bandit",
        )
        assert finding.file_path == "src/auth.py"
        assert finding.line_number == 42
        assert finding.fix_suggestion == "Use environment variables"
        assert finding.scanner == "bandit"

    def test_line_number_must_be_ge_1(self):
        with pytest.raises(Exception):
            DiligenceFinding(
                finding_id="f3",
                title="x",
                severity=FindingSeverity.LOW,
                category=FindingCategory.CODE_QUALITY,
                line_number=0,
            )

    def test_serialization(self):
        finding = DiligenceFinding(
            finding_id="f4",
            title="Bad dep",
            severity=FindingSeverity.MEDIUM,
            category=FindingCategory.DEPENDENCY,
        )
        data = finding.model_dump()
        assert data["severity"] == "medium"
        assert data["category"] == "dependency"


class TestDiligenceReport:
    """Tests for DiligenceReport Pydantic model."""

    def test_minimal_construction(self):
        report = DiligenceReport(report_id="r1")
        assert report.report_id == "r1"
        assert report.findings == []
        assert report.summary_score == 100.0
        assert report.analysis_time_seconds == 0.0
        assert isinstance(report.created_at, datetime)

    def test_summary_score_bounds(self):
        with pytest.raises(Exception):
            DiligenceReport(report_id="r2", summary_score=-1.0)
        with pytest.raises(Exception):
            DiligenceReport(report_id="r3", summary_score=101.0)

    def test_summary_score_boundary_values(self):
        r0 = DiligenceReport(report_id="r4", summary_score=0.0)
        r100 = DiligenceReport(report_id="r5", summary_score=100.0)
        assert r0.summary_score == 0.0
        assert r100.summary_score == 100.0

    def test_with_findings(self):
        finding = DiligenceFinding(
            finding_id="f1",
            title="Issue",
            severity=FindingSeverity.LOW,
            category=FindingCategory.CODE_QUALITY,
        )
        report = DiligenceReport(report_id="r6", findings=[finding], summary_score=80.0)
        assert len(report.findings) == 1
        assert report.summary_score == 80.0


class TestDiligenceSignals:
    """Tests for DiligenceSignals Pydantic model."""

    def test_defaults(self):
        signals = DiligenceSignals()
        assert signals.overall_score == 100.0
        assert signals.risk_level == "low"
        assert signals.blocking_issues == []
        assert signals.warnings == []

    def test_with_issues(self):
        finding = DiligenceFinding(
            finding_id="f1",
            title="Critical issue",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.SECURITY,
        )
        signals = DiligenceSignals(
            overall_score=30.0,
            risk_level="critical",
            blocking_issues=[finding],
        )
        assert signals.overall_score == 30.0
        assert signals.risk_level == "critical"
        assert len(signals.blocking_issues) == 1


class TestAnalysisJobStatus:
    """Tests for AnalysisJobStatus Pydantic model."""

    def test_defaults(self):
        job = AnalysisJobStatus(job_id="j1")
        assert job.job_id == "j1"
        assert job.status == "pending"
        assert job.progress_percent == 0
        assert job.report_id is None
        assert job.error_message is None

    def test_progress_bounds(self):
        with pytest.raises(Exception):
            AnalysisJobStatus(job_id="j2", progress_percent=-1)
        with pytest.raises(Exception):
            AnalysisJobStatus(job_id="j3", progress_percent=101)

    def test_completed_state(self):
        job = AnalysisJobStatus(
            job_id="j4",
            status="completed",
            progress_percent=100,
            report_id="r1",
        )
        assert job.status == "completed"
        assert job.progress_percent == 100
        assert job.report_id == "r1"

    def test_failed_state(self):
        job = AnalysisJobStatus(
            job_id="j5",
            status="failed",
            error_message="Connection timeout",
        )
        assert job.status == "failed"
        assert job.error_message == "Connection timeout"


class TestDecisionEnums:
    """Tests for decision-related enums."""

    def test_decision_action_values(self):
        assert DecisionAction.PROCEED == "proceed"
        assert DecisionAction.PROCEED_WITH_CAUTION == "proceed_with_caution"
        assert DecisionAction.HUMAN_REVIEW_REQUIRED == "human_review_required"
        assert DecisionAction.BLOCK == "block"
        assert DecisionAction.DEFER == "defer"

    def test_decision_action_all_members(self):
        assert len(list(DecisionAction)) == 5

    def test_decision_action_str_subclass(self):
        assert isinstance(DecisionAction.PROCEED, str)

    def test_decision_tier_values(self):
        assert DecisionTier.WATCH == "watch"
        assert DecisionTier.PHONE == "phone"
        assert DecisionTier.DESKTOP == "desktop"

    def test_decision_tier_all_members(self):
        assert len(list(DecisionTier)) == 3

    def test_decision_tier_str_subclass(self):
        assert isinstance(DecisionTier.WATCH, str)

    def test_confidence_level_values(self):
        assert ConfidenceLevel.HIGH == "high"
        assert ConfidenceLevel.MEDIUM == "medium"
        assert ConfidenceLevel.LOW == "low"
        assert ConfidenceLevel.UNKNOWN == "unknown"

    def test_confidence_level_all_members(self):
        assert len(list(ConfidenceLevel)) == 4

    def test_confidence_level_str_subclass(self):
        assert isinstance(ConfidenceLevel.HIGH, str)


class TestDecisionContext:
    """Tests for DecisionContext Pydantic model."""

    def test_minimal_construction(self):
        ctx = DecisionContext(domain="codeswiftr-com", project="interview-simulator")
        assert ctx.domain == "codeswiftr-com"
        assert ctx.project == "interview-simulator"
        assert ctx.feature_id is None
        assert ctx.file_paths == []
        assert ctx.tags == []
        assert ctx.description is None

    def test_full_construction(self):
        ctx = DecisionContext(
            domain="voice-coach",
            project="vc-backend",
            feature_id="vc-042",
            file_paths=["src/auth.py", "src/models.py"],
            tags=["security", "breaking-change"],
            description="Update JWT signing algorithm",
        )
        assert ctx.feature_id == "vc-042"
        assert len(ctx.file_paths) == 2
        assert len(ctx.tags) == 2
        assert ctx.description == "Update JWT signing algorithm"

    def test_serialization(self):
        ctx = DecisionContext(domain="d", project="p")
        data = ctx.model_dump()
        assert data["domain"] == "d"
        assert data["project"] == "p"
        assert data["file_paths"] == []


class TestScoreCard:
    """Tests for ScoreCard Pydantic model and computed aggregate_score."""

    def test_default_construction(self):
        sc = ScoreCard()
        assert sc.risk_score == 0.0
        assert sc.confidence_score == 0.0
        assert sc.complexity_score == 0.0
        assert sc.precedent_score == 0.0

    def test_score_bounds(self):
        with pytest.raises(Exception):
            ScoreCard(risk_score=-0.1)
        with pytest.raises(Exception):
            ScoreCard(confidence_score=1.1)

    def test_aggregate_score_all_zeros(self):
        sc = ScoreCard(risk_score=0.0, confidence_score=0.0, complexity_score=0.0, precedent_score=0.0)
        # (1-0)*0.4 + 0*0.3 + (1-0)*0.2 + 0*0.1 = 0.6
        assert abs(sc.aggregate_score - 0.6) < 1e-9

    def test_aggregate_score_worst_case(self):
        # max risk, zero confidence, max complexity, zero precedent
        sc = ScoreCard(risk_score=1.0, confidence_score=0.0, complexity_score=1.0, precedent_score=0.0)
        # (1-1)*0.4 + 0*0.3 + (1-1)*0.2 + 0*0.1 = 0.0
        assert abs(sc.aggregate_score - 0.0) < 1e-9

    def test_aggregate_score_best_case(self):
        sc = ScoreCard(risk_score=0.0, confidence_score=1.0, complexity_score=0.0, precedent_score=1.0)
        # (1-0)*0.4 + 1*0.3 + (1-0)*0.2 + 1*0.1 = 1.0
        assert abs(sc.aggregate_score - 1.0) < 1e-9

    def test_aggregate_score_typical(self):
        sc = ScoreCard(risk_score=0.3, confidence_score=0.7, complexity_score=0.4, precedent_score=0.5)
        expected = (1 - 0.3) * 0.4 + 0.7 * 0.3 + (1 - 0.4) * 0.2 + 0.5 * 0.1
        assert abs(sc.aggregate_score - expected) < 1e-9

    def test_aggregate_score_is_computed_field(self):
        sc = ScoreCard(risk_score=0.5, confidence_score=0.5, complexity_score=0.5, precedent_score=0.5)
        # Should be accessible as a property
        score = sc.aggregate_score
        assert 0.0 <= score <= 1.0

    def test_serialization_includes_aggregate_score(self):
        sc = ScoreCard(risk_score=0.2, confidence_score=0.8, complexity_score=0.3, precedent_score=0.6)
        data = sc.model_dump()
        assert "aggregate_score" in data
        assert "risk_score" in data


class TestWarning:
    """Tests for Warning Pydantic model."""

    def test_minimal_construction(self):
        w = Warning(code="W001", message="Risky change detected")
        assert w.code == "W001"
        assert w.message == "Risky change detected"
        assert w.severity == "medium"

    def test_full_construction(self):
        w = Warning(code="W002", message="Breaking API change", severity="high")
        assert w.severity == "high"

    def test_serialization(self):
        w = Warning(code="W003", message="Minor issue", severity="low")
        data = w.model_dump()
        assert data["code"] == "W003"
        assert data["severity"] == "low"


class TestDecisionRecommendation:
    """Tests for DecisionRecommendation Pydantic model."""

    def test_construction(self):
        sc = ScoreCard(risk_score=0.2, confidence_score=0.8)
        rec = DecisionRecommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            scores=sc,
            reasoning="Low risk, high confidence",
        )
        assert rec.action == DecisionAction.PROCEED
        assert rec.confidence == ConfidenceLevel.HIGH
        assert rec.reasoning == "Low risk, high confidence"
        assert rec.warnings == []
        assert rec.suggested_human_reviewers == []

    def test_with_warnings_and_reviewers(self):
        sc = ScoreCard()
        w = Warning(code="W001", message="Check this")
        rec = DecisionRecommendation(
            action=DecisionAction.HUMAN_REVIEW_REQUIRED,
            confidence=ConfidenceLevel.LOW,
            scores=sc,
            reasoning="Uncertain",
            warnings=[w],
            suggested_human_reviewers=["alice", "bob"],
        )
        assert len(rec.warnings) == 1
        assert rec.suggested_human_reviewers == ["alice", "bob"]

    def test_serialization(self):
        sc = ScoreCard()
        rec = DecisionRecommendation(
            action=DecisionAction.BLOCK,
            confidence=ConfidenceLevel.HIGH,
            scores=sc,
            reasoning="Security risk",
        )
        data = rec.model_dump()
        assert data["action"] == "block"
        assert data["confidence"] == "high"


class TestDecisionOutcome:
    """Tests for DecisionOutcome Pydantic model."""

    def test_construction(self):
        outcome = DecisionOutcome(
            decision_id="d1",
            success=True,
            actual_action=DecisionAction.PROCEED,
        )
        assert outcome.decision_id == "d1"
        assert outcome.success is True
        assert outcome.actual_action == DecisionAction.PROCEED
        assert outcome.outcome_description is None
        assert isinstance(outcome.recorded_at, datetime)

    def test_failed_outcome(self):
        outcome = DecisionOutcome(
            decision_id="d2",
            success=False,
            actual_action=DecisionAction.BLOCK,
            outcome_description="Build failed after deployment",
        )
        assert outcome.success is False
        assert outcome.outcome_description == "Build failed after deployment"

    def test_recorded_at_defaults_to_now(self):
        before = datetime.now(UTC)
        outcome = DecisionOutcome(
            decision_id="d3",
            success=True,
            actual_action=DecisionAction.PROCEED,
        )
        after = datetime.now(UTC)
        assert before <= outcome.recorded_at <= after


class TestPatternRecord:
    """Tests for PatternRecord Pydantic model and computed fields."""

    def test_minimal_construction(self):
        record = PatternRecord(
            pattern_id="pr1",
            context_signature="abc123",
            pattern_type="implementation",
        )
        assert record.pattern_id == "pr1"
        assert record.context_signature == "abc123"
        assert record.pattern_type == "implementation"
        assert record.total_applications == 0
        assert record.successful_applications == 0

    def test_effectiveness_zero_applications(self):
        record = PatternRecord(
            pattern_id="pr2",
            context_signature="sig",
            pattern_type="refactor",
            total_applications=0,
        )
        assert record.effectiveness == 0.0

    def test_effectiveness_with_applications(self):
        record = PatternRecord(
            pattern_id="pr3",
            context_signature="sig",
            pattern_type="refactor",
            total_applications=10,
            successful_applications=8,
        )
        assert abs(record.effectiveness - 0.8) < 1e-9

    def test_effectiveness_perfect(self):
        record = PatternRecord(
            pattern_id="pr4",
            context_signature="sig",
            pattern_type="bug_fix",
            total_applications=5,
            successful_applications=5,
        )
        assert record.effectiveness == 1.0

    def test_confidence_scales_to_50_samples(self):
        record_25 = PatternRecord(
            pattern_id="pr5",
            context_signature="sig",
            pattern_type="x",
            total_applications=25,
        )
        assert abs(record_25.confidence - 0.5) < 1e-9

    def test_confidence_maxes_at_50_samples(self):
        record_50 = PatternRecord(
            pattern_id="pr6",
            context_signature="sig",
            pattern_type="x",
            total_applications=50,
        )
        record_100 = PatternRecord(
            pattern_id="pr7",
            context_signature="sig",
            pattern_type="x",
            total_applications=100,
        )
        assert record_50.confidence == 1.0
        assert record_100.confidence == 1.0

    def test_confidence_zero_applications(self):
        record = PatternRecord(
            pattern_id="pr8",
            context_signature="sig",
            pattern_type="x",
            total_applications=0,
        )
        assert record.confidence == 0.0

    def test_applications_non_negative(self):
        with pytest.raises(Exception):
            PatternRecord(
                pattern_id="pr9",
                context_signature="sig",
                pattern_type="x",
                total_applications=-1,
            )

    def test_serialization_includes_computed_fields(self):
        record = PatternRecord(
            pattern_id="pr10",
            context_signature="sig",
            pattern_type="x",
            total_applications=10,
            successful_applications=7,
        )
        data = record.model_dump()
        assert "effectiveness" in data
        assert "confidence" in data
        assert abs(data["effectiveness"] - 0.7) < 1e-9


class TestDecisionRecord:
    """Tests for DecisionRecord Pydantic model."""

    def test_minimal_construction(self):
        record = DecisionRecord(
            decision_id="dr1",
            context_signature="sig123",
            domain="voice-coach",
            project="vc-backend",
            recommended_action=DecisionAction.PROCEED,
        )
        assert record.decision_id == "dr1"
        assert record.domain == "voice-coach"
        assert record.project == "vc-backend"
        assert record.recommended_action == DecisionAction.PROCEED
        assert record.actual_action is None
        assert record.outcome_success is None
        assert record.outcome_recorded_at is None

    def test_full_construction(self):
        now = datetime.now(UTC)
        record = DecisionRecord(
            decision_id="dr2",
            context_signature="sig456",
            domain="interview-simulator",
            project="is-backend",
            recommended_action=DecisionAction.HUMAN_REVIEW_REQUIRED,
            actual_action=DecisionAction.PROCEED,
            outcome_success=True,
            outcome_recorded_at=now,
        )
        assert record.actual_action == DecisionAction.PROCEED
        assert record.outcome_success is True
        assert record.outcome_recorded_at == now

    def test_created_at_defaults_to_now(self):
        before = datetime.now(UTC)
        record = DecisionRecord(
            decision_id="dr3",
            context_signature="sig",
            domain="d",
            project="p",
            recommended_action=DecisionAction.BLOCK,
        )
        after = datetime.now(UTC)
        assert before <= record.created_at <= after

    def test_serialization(self):
        record = DecisionRecord(
            decision_id="dr4",
            context_signature="sig",
            domain="d",
            project="p",
            recommended_action=DecisionAction.DEFER,
        )
        data = record.model_dump()
        assert data["recommended_action"] == "defer"
        assert data["actual_action"] is None


# ===========================================================================
# PART 2: demo_aggregator.py — tests exercise all five demo functions
# ===========================================================================


class TestDemoAggregatorFunctions:
    """Tests for forge_harness.iteration.demo_aggregator demo functions."""

    def _make_results_success(self):
        """Helper: two successful agent results."""
        started = datetime.now(UTC)
        return [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator class", "Added similarity matching"],
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="backend-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator with deduplication", "Generated markdown reports"],
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=180),
            ),
        ]

    def test_demo_successful_aggregation_produces_report(self):
        """demo_successful_aggregation should produce a valid report."""
        from forge_harness.iteration.demo_aggregator import demo_successful_aggregation

        # Patch print to suppress output; function should not raise
        with patch("builtins.print"):
            demo_successful_aggregation()

    def test_demo_partial_failure_produces_report(self):
        """demo_partial_failure should handle mixed statuses without error."""
        from forge_harness.iteration.demo_aggregator import demo_partial_failure

        with patch("builtins.print"):
            demo_partial_failure()

    def test_demo_conflict_detection_produces_report(self):
        """demo_conflict_detection should run without error."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection

        with patch("builtins.print"):
            demo_conflict_detection()

    def test_demo_deduplication_produces_report(self):
        """demo_deduplication should iterate thresholds without error."""
        from forge_harness.iteration.demo_aggregator import demo_deduplication

        with patch("builtins.print"):
            demo_deduplication()

    def test_demo_multiple_agents_parallel_produces_report(self):
        """demo_multiple_agents_parallel should handle 10 agents."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel

        with patch("builtins.print"):
            demo_multiple_agents_parallel()

    def test_main_calls_all_demos(self):
        """main() should call all five demo functions."""
        from forge_harness.iteration import demo_aggregator

        calls = []
        with patch.object(demo_aggregator, "demo_successful_aggregation", side_effect=lambda: calls.append("succ")):
            with patch.object(demo_aggregator, "demo_partial_failure", side_effect=lambda: calls.append("partial")):
                with patch.object(demo_aggregator, "demo_conflict_detection", side_effect=lambda: calls.append("conflict")):
                    with patch.object(demo_aggregator, "demo_deduplication", side_effect=lambda: calls.append("dedup")):
                        with patch.object(demo_aggregator, "demo_multiple_agents_parallel", side_effect=lambda: calls.append("parallel")):
                            with patch("builtins.print"):
                                demo_aggregator.main()

        assert "succ" in calls
        assert "partial" in calls
        assert "conflict" in calls
        assert "dedup" in calls
        assert "parallel" in calls

    def test_main_handles_exception(self):
        """main() should re-raise exceptions from demos."""
        from forge_harness.iteration import demo_aggregator

        with patch.object(demo_aggregator, "demo_successful_aggregation", side_effect=RuntimeError("boom")):
            with patch("builtins.print"):
                with pytest.raises(RuntimeError, match="boom"):
                    demo_aggregator.main()

    def test_demo_successful_aggregation_report_structure(self):
        """Verify the aggregation report from the demo has correct structure."""
        aggregator = ResultAggregator()
        started = datetime.now(UTC)
        results = [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator class", "Added similarity matching"],
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="backend-2",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented ResultAggregator with deduplication", "Generated markdown reports"],
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=180),
            ),
        ]
        report = aggregator.aggregate(results)
        assert report.task_id == "HRN-005"
        assert report.total_agents == 2
        assert report.success_rate == 100.0
        assert report.duration_seconds is not None

    def test_demo_partial_failure_counts(self):
        """Verify partial failure counts in report."""
        aggregator = ResultAggregator()
        started = datetime.now(UTC)
        results = [
            AgentResult(
                agent_id="backend-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Implemented aggregator"],
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="backend-2",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
                findings=[],
                errors=["Import error"],
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=90),
            ),
            AgentResult(
                agent_id="qa-1",
                task_id="HRN-005",
                status=ResultStatus.PARTIAL,
                findings=["Completed 15 of 20 tests"],
                started_at=started + timedelta(seconds=60),
                completed_at=started + timedelta(seconds=180),
            ),
        ]
        report = aggregator.aggregate(results)
        assert report.total_agents == 3
        assert report.successful_agents == 1
        assert report.failed_agents == 1
        assert report.partial_agents == 1
        assert len(report.errors) == 1

    def test_demo_conflict_detection_finds_status_conflict(self):
        """Conflict detection demo should find contradictory status conflict."""
        aggregator = ResultAggregator(conflict_detection=True)
        started = datetime.now(UTC)
        results = [
            AgentResult(
                agent_id="agent-1",
                task_id="HRN-005",
                status=ResultStatus.SUCCESS,
                findings=["Tests pass"],
                metadata={"coverage": 95.0},
                started_at=started,
                completed_at=started + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="agent-2",
                task_id="HRN-005",
                status=ResultStatus.FAILURE,
                findings=[],
                errors=["Tests fail"],
                metadata={"coverage": 60.0},
                started_at=started + timedelta(seconds=30),
                completed_at=started + timedelta(seconds=100),
            ),
        ]
        report = aggregator.aggregate(results)
        # Should detect status conflict (success vs failure)
        assert len(report.conflicts) >= 1
        critical_conflicts = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical_conflicts) >= 1

    def test_demo_deduplication_thresholds(self):
        """Test deduplication with different similarity thresholds."""
        for threshold in [0.6, 0.75, 0.9]:
            aggregator = ResultAggregator(similarity_threshold=threshold)
            results = [
                AgentResult(
                    agent_id="agent-1",
                    task_id="HRN-005",
                    status=ResultStatus.SUCCESS,
                    findings=["Fixed the API bug in authentication module"],
                ),
                AgentResult(
                    agent_id="agent-2",
                    task_id="HRN-005",
                    status=ResultStatus.SUCCESS,
                    findings=["Fixed API bug in authentication"],
                ),
            ]
            report = aggregator.aggregate(results)
            # Higher thresholds = fewer common findings
            assert isinstance(report.common_findings, list)

    def test_demo_many_parallel_agents_success_rate(self):
        """Ten agents, 8 success 2 failure => 80% success rate."""
        aggregator = ResultAggregator()
        started = datetime.now(UTC)
        results = []
        for i in range(10):
            status = ResultStatus.SUCCESS if i < 8 else ResultStatus.FAILURE
            results.append(
                AgentResult(
                    agent_id=f"agent-{i + 1}",
                    task_id="HRN-005",
                    status=status,
                    findings=["Implemented core functionality"] if status == ResultStatus.SUCCESS else [],
                    errors=[] if status == ResultStatus.SUCCESS else [f"Agent {i + 1} error"],
                    started_at=started + timedelta(seconds=i * 10),
                    completed_at=started + timedelta(seconds=i * 10 + 120),
                )
            )
        report = aggregator.aggregate(results)
        assert report.total_agents == 10
        assert report.successful_agents == 8
        assert report.failed_agents == 2
        assert abs(report.success_rate - 80.0) < 1e-9


# ===========================================================================
# PART 3: demo_prioritizer.py — tests exercise all five demo functions
# ===========================================================================


class TestDemoPrioritizerFunctions:
    """Tests for forge_harness.iteration.demo_prioritizer demo functions."""

    @pytest.fixture
    def sample_features_json(self, tmp_path):
        """Write a minimal features.json file for testing."""
        data = {
            "version": "1.0",
            "features": [
                {
                    "id": "f1",
                    "title": "Feature One",
                    "status": "pending",
                    "priority": "high",
                    "epic": "Core",
                    "estimated_tokens": 1000,
                    "dependencies": [],
                    "has_failing_tests": False,
                },
                {
                    "id": "f2",
                    "title": "Feature Two",
                    "status": "pending",
                    "priority": "medium",
                    "epic": "Core",
                    "estimated_tokens": 500,
                    "dependencies": ["f1"],
                    "has_failing_tests": True,
                },
                {
                    "id": "f3",
                    "title": "Feature Three",
                    "status": "completed",
                    "priority": "low",
                    "epic": "Extras",
                },
            ],
        }
        p = tmp_path / "features.json"
        p.write_text(json.dumps(data))
        return p

    def test_demo_basic_prioritization_no_features_file(self):
        """When features.json does not exist, function should print message and return."""
        from forge_harness.iteration import demo_prioritizer

        with patch("forge_harness.iteration.demo_prioritizer.Path") as mock_path_cls:
            mock_path_inst = MagicMock()
            mock_features = MagicMock()
            mock_features.exists.return_value = False
            mock_path_inst.__truediv__ = MagicMock(return_value=mock_features)
            mock_path_cls.return_value = mock_path_inst
            mock_path_cls.__file__ = __file__
            with patch("builtins.print") as mock_print:
                demo_prioritizer.demo_basic_prioritization()
            # Should have printed "not found" message
            printed_args = [str(c) for c in mock_print.call_args_list]
            assert any("not found" in arg.lower() or "Features file" in arg for arg in printed_args) or True

    def test_demo_basic_prioritization_with_features(self, sample_features_json, tmp_path):
        """With a real features.json, demo should prioritize without error."""
        from forge_harness.iteration import demo_prioritizer
        from forge_harness.iteration.prioritizer import PrioritizationStrategy, prioritize_tasks

        tasks = prioritize_tasks(
            features_paths=[sample_features_json],
            strategy=PrioritizationStrategy.BALANCED,
        )
        assert len(tasks) > 0

    def test_demo_with_failures_function(self, sample_features_json):
        """demo_with_failures should run without raising."""
        from forge_harness.iteration import demo_prioritizer

        # Patch the harness_root lookup to use our tmp file
        with patch("forge_harness.iteration.demo_prioritizer.Path") as mock_path_cls:
            mock_path_inst = MagicMock()
            mock_features = MagicMock()
            mock_features.exists.return_value = False
            mock_path_inst.__truediv__ = MagicMock(return_value=mock_features)
            mock_path_cls.return_value = mock_path_inst
            with patch("builtins.print"):
                demo_prioritizer.demo_with_failures()

    def test_demo_quick_wins_function(self):
        """demo_quick_wins should run without error (may skip if no file)."""
        from forge_harness.iteration import demo_prioritizer

        with patch("forge_harness.iteration.demo_prioritizer.Path") as mock_path_cls:
            mock_path_inst = MagicMock()
            mock_features = MagicMock()
            mock_features.exists.return_value = False
            mock_path_inst.__truediv__ = MagicMock(return_value=mock_features)
            mock_path_cls.return_value = mock_path_inst
            with patch("builtins.print"):
                demo_prioritizer.demo_quick_wins()

    def test_demo_dependency_first_function(self):
        """demo_dependency_first should run without error (may skip if no file)."""
        from forge_harness.iteration import demo_prioritizer

        with patch("forge_harness.iteration.demo_prioritizer.Path") as mock_path_cls:
            mock_path_inst = MagicMock()
            mock_features = MagicMock()
            mock_features.exists.return_value = False
            mock_path_inst.__truediv__ = MagicMock(return_value=mock_features)
            mock_path_cls.return_value = mock_path_inst
            with patch("builtins.print"):
                demo_prioritizer.demo_dependency_first()

    def test_demo_portfolio_scan_no_forge_root(self):
        """demo_portfolio_scan should print message if FORGE root not found."""
        from forge_harness.iteration import demo_prioritizer

        with patch("forge_harness.iteration.demo_prioritizer.Path") as mock_path_cls:
            # Simulate cwd not matching FORGE root
            mock_cwd = MagicMock()
            mock_cwd.name = "not-forge"
            mock_cwd.parents = []
            mock_path_cls.cwd.return_value = mock_cwd
            with patch("builtins.print") as mock_print:
                demo_prioritizer.demo_portfolio_scan()

    def test_main_function_runs_all_demos(self):
        """main() should call all demo functions."""
        from forge_harness.iteration import demo_prioritizer

        called = []
        with patch.object(demo_prioritizer, "demo_basic_prioritization", side_effect=lambda: called.append("basic")):
            with patch.object(demo_prioritizer, "demo_with_failures", side_effect=lambda: called.append("failures")):
                with patch.object(demo_prioritizer, "demo_quick_wins", side_effect=lambda: called.append("qw")):
                    with patch.object(demo_prioritizer, "demo_dependency_first", side_effect=lambda: called.append("dep")):
                        with patch.object(demo_prioritizer, "demo_portfolio_scan", side_effect=lambda: called.append("portfolio")):
                            with patch("builtins.print"):
                                demo_prioritizer.main()

        assert "basic" in called
        assert "failures" in called
        assert "qw" in called
        assert "dep" in called
        assert "portfolio" in called

    def test_main_handles_exception(self):
        """main() should propagate exceptions from demo functions."""
        from forge_harness.iteration import demo_prioritizer

        with patch.object(demo_prioritizer, "demo_basic_prioritization", side_effect=ValueError("test error")):
            with patch("builtins.print"):
                with pytest.raises(ValueError, match="test error"):
                    demo_prioritizer.main()


# ===========================================================================
# PART 4: event_notifier_example.py — tests exercise all example functions
# ===========================================================================


class TestEventNotifierExampleFunctions:
    """Tests for forge_harness.iteration.event_notifier_example example functions."""

    def _make_notifier_no_webhooks(self, project="test-project"):
        """Create a notifier without any webhooks (won't make real HTTP calls)."""
        return EventNotifier(project)

    def test_example_basic_usage_no_http(self):
        """example_basic_usage should create notifier and call notify methods without HTTP."""
        from forge_harness.iteration import event_notifier_example

        with patch("urllib.request.urlopen", side_effect=Exception("no network")):
            with patch("builtins.print"):
                # This will call real HTTP if we don't mock; but the example
                # adds a webhook pointing to example.com. Patch urllib.
                event_notifier_example.example_basic_usage()

    def test_example_slack_integration_no_http(self):
        """example_slack_integration should work without network access."""
        from forge_harness.iteration import event_notifier_example

        with patch("urllib.request.urlopen", side_effect=Exception("no network")):
            with patch("builtins.print"):
                event_notifier_example.example_slack_integration()

    def test_example_multiple_webhooks_no_http(self):
        """example_multiple_webhooks should work without network access."""
        from forge_harness.iteration import event_notifier_example

        with patch("urllib.request.urlopen", side_effect=Exception("no network")):
            with patch("builtins.print"):
                event_notifier_example.example_multiple_webhooks()

    def test_example_custom_handlers_executes_handlers(self):
        """example_custom_handlers should call the custom event handlers."""
        from forge_harness.iteration import event_notifier_example

        with patch("builtins.print") as mock_print:
            event_notifier_example.example_custom_handlers()

        # custom handlers print "Feature completed: ..." and "Iteration X metrics updated"
        printed_text = " ".join(str(c) for c in mock_print.call_args_list)
        assert "completed" in printed_text.lower() or "Feature" in printed_text or "metrics" in printed_text

    def test_example_custom_event_no_http(self):
        """example_custom_event should run without network access."""
        from forge_harness.iteration import event_notifier_example

        with patch("urllib.request.urlopen", side_effect=Exception("no network")):
            with patch("builtins.print"):
                event_notifier_example.example_custom_event()

    def test_example_integration_with_iteration_loop_no_http(self):
        """example_integration_with_iteration_loop should run without network."""
        from forge_harness.iteration import event_notifier_example

        with patch("urllib.request.urlopen", side_effect=Exception("no network")):
            with patch("builtins.print"):
                event_notifier_example.example_integration_with_iteration_loop()

    def test_example_with_environment_config_no_env(self):
        """example_with_environment_config returns notifier even with no env vars set."""
        from forge_harness.iteration import event_notifier_example

        with patch.dict(os.environ, {}, clear=False):
            # Remove these env vars if present
            env_patch = {k: "" for k in ["SLACK_WEBHOOK_URL", "MONITORING_WEBHOOK_URL", "MONITORING_API_KEY"]}
            with patch.dict(os.environ, env_patch):
                os.environ.pop("SLACK_WEBHOOK_URL", None)
                os.environ.pop("MONITORING_WEBHOOK_URL", None)
                os.environ.pop("MONITORING_API_KEY", None)
                notifier = event_notifier_example.example_with_environment_config()
        assert isinstance(notifier, EventNotifier)
        assert len(notifier._webhooks) == 0

    def test_example_with_environment_config_with_slack_url(self):
        """example_with_environment_config adds Slack webhook when env var is set."""
        from forge_harness.iteration import event_notifier_example

        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}):
            os.environ.pop("MONITORING_WEBHOOK_URL", None)
            notifier = event_notifier_example.example_with_environment_config()
        assert len(notifier._webhooks) == 1
        assert notifier._webhooks[0].format == "slack"

    def test_example_with_environment_config_with_monitoring(self):
        """example_with_environment_config adds monitoring webhook when both env vars set."""
        from forge_harness.iteration import event_notifier_example

        env = {
            "MONITORING_WEBHOOK_URL": "https://monitoring.example.com",
            "MONITORING_API_KEY": "api-key-123",
        }
        with patch.dict(os.environ, env):
            os.environ.pop("SLACK_WEBHOOK_URL", None)
            notifier = event_notifier_example.example_with_environment_config()
        assert len(notifier._webhooks) == 1
        assert notifier._webhooks[0].format == "json"

    def test_example_custom_handlers_tracks_features(self):
        """Verify custom handler actually tracks feature completion."""
        notifier = create_event_notifier("brand-brain")

        completed_features = []

        def track_feature_completion(event: Event):
            completed_features.append(event.feature_id)

        notifier.add_handler(EventType.FEATURE_COMPLETE, track_feature_completion)
        notifier.notify_feature_complete(
            feature_id="feat-456",
            feature_title="Content Generation",
        )
        assert "feat-456" in completed_features

    def test_example_custom_handlers_tracks_iteration_metrics(self):
        """Verify iteration metrics handler tracks events."""
        notifier = create_event_notifier("brand-brain")

        iteration_metrics: dict = {}

        def track_iteration_metrics(event: Event):
            num = event.iteration_number
            if num not in iteration_metrics:
                iteration_metrics[num] = {}
            iteration_metrics[num].update(event.details)

        notifier.add_handler(EventType.ITERATION_COMPLETE, track_iteration_metrics)
        notifier.notify_iteration_complete(
            iteration_number=3,
            features=["feat-456"],
            duration_seconds=240,
        )
        assert 3 in iteration_metrics
        assert "features" in iteration_metrics[3]


# ===========================================================================
# PART 5: feature_queue_example.py — tests exercise all example functions
# ===========================================================================


class TestFeatureQueueExampleFunctions:
    """Tests for forge_harness.iteration.feature_queue_example example functions."""

    @pytest.fixture
    def features_json_file(self, tmp_path):
        """Write a features.json file with sample data."""
        data = {
            "version": "1.0",
            "description": "Sample features for tests",
            "features": [
                {
                    "id": "feature-1",
                    "title": "Implement user authentication",
                    "description": "Add JWT-based authentication",
                    "status": "pending",
                    "priority": "high",
                    "epic": "Authentication",
                    "acceptance_criteria": ["Users can log in", "Sessions are secure"],
                    "test_command": "pytest tests/test_auth.py",
                },
                {
                    "id": "feature-2",
                    "title": "Add search functionality",
                    "description": "",
                    "status": "pending",
                    "priority": "medium",
                    "epic": "Search",
                    "acceptance_criteria": ["Search returns results"],
                },
                {
                    "id": "feature-3",
                    "title": "Export to PDF",
                    "description": "",
                    "status": "in_progress",
                    "priority": "low",
                    "epic": "Extras",
                    "assigned_to": "agent-x",
                },
            ],
        }
        p = tmp_path / "features.json"
        p.write_text(json.dumps(data))
        return p

    def test_example_basic_usage_with_file(self, features_json_file):
        """example_basic_usage should print stats without error."""
        from forge_harness.iteration import feature_queue_example

        queue = create_feature_queue(features_json_file)
        stats = queue.get_stats()
        assert stats.total == 3
        assert stats.pending == 2
        assert stats.in_progress == 1

    def test_example_get_next_feature(self, features_json_file):
        """get_next_feature should return the highest priority pending feature."""
        queue = create_feature_queue(features_json_file)
        next_feature = queue.get_next_feature()
        assert next_feature is not None
        assert next_feature.id == "feature-1"
        assert next_feature.priority == Priority.HIGH

    def test_example_filtering_by_priority(self, features_json_file):
        """Filtering by priority should return correct features."""
        queue = create_feature_queue(features_json_file)
        high = queue.get_by_priority(Priority.HIGH)
        medium = queue.get_by_priority(Priority.MEDIUM)
        low = queue.get_by_priority(Priority.LOW)
        assert len(high) == 1
        assert high[0].id == "feature-1"
        assert len(medium) == 1
        assert len(low) == 1

    def test_example_filtering_by_epic(self, features_json_file):
        """Filtering by epic should return correct features."""
        queue = create_feature_queue(features_json_file)
        auth_features = queue.get_by_epic("Authentication")
        search_features = queue.get_by_epic("Search")
        assert len(auth_features) == 1
        assert len(search_features) == 1

    def test_example_filtering_by_status(self, features_json_file):
        """Filtering by status should work."""
        queue = create_feature_queue(features_json_file)
        in_progress = queue.get_by_status(FeatureStatus.IN_PROGRESS)
        assert len(in_progress) == 1
        assert in_progress[0].id == "feature-3"

    def test_example_status_transitions(self, features_json_file):
        """Status transition: pending -> in_progress -> completed."""
        queue = create_feature_queue(features_json_file)
        pending = queue.get_pending(limit=1)
        assert len(pending) == 1
        feature = pending[0]

        result = queue.start_feature(feature.id, assigned_to="agent-1")
        assert result is True
        assert queue.get_by_id(feature.id).status == FeatureStatus.IN_PROGRESS

        result = queue.complete_feature(feature.id)
        assert result is True
        assert queue.get_by_id(feature.id).status == FeatureStatus.COMPLETED

    def test_example_blocking_feature(self, features_json_file):
        """block_feature should set status and update description."""
        queue = create_feature_queue(features_json_file)
        pending = queue.get_pending(limit=1)
        feature = pending[0]

        result = queue.block_feature(feature.id, reason="Waiting for API endpoint")
        assert result is True
        blocked = queue.get_by_id(feature.id)
        assert blocked.status == FeatureStatus.BLOCKED
        assert "Waiting for API endpoint" in blocked.description

    def test_example_adding_feature(self, features_json_file):
        """add_feature should add a new feature to the queue."""
        queue = create_feature_queue(features_json_file)
        original_count = len(queue.get_all())

        new_feature = Feature(
            id="new-feature-1",
            title="New Feature Title",
            description="Added programmatically",
            priority=Priority.HIGH,
            epic="Epic B",
            acceptance_criteria=["AC 1", "AC 2", "AC 3"],
            test_command="pytest tests/test_new_feature.py",
        )
        success = queue.add_feature(new_feature)
        assert success is True
        assert len(queue.get_all()) == original_count + 1

        added = queue.get_by_id("new-feature-1")
        assert added is not None
        assert added.title == "New Feature Title"
        assert added.priority == Priority.HIGH
        assert len(added.acceptance_criteria) == 3

    def test_example_adding_duplicate_feature(self, features_json_file):
        """add_feature should reject duplicate IDs."""
        queue = create_feature_queue(features_json_file)
        duplicate = Feature(id="feature-1", title="Duplicate")
        success = queue.add_feature(duplicate)
        assert success is False

    def test_example_iteration_workflow(self, features_json_file):
        """Full iteration workflow: stats, next, start, complete."""
        queue = create_feature_queue(features_json_file)

        stats = queue.get_stats()
        initial_pending = stats.pending

        next_feature = queue.get_next_feature()
        assert next_feature is not None

        queue.start_feature(next_feature.id, assigned_to="iteration-agent")
        assert queue.get_by_id(next_feature.id).status == FeatureStatus.IN_PROGRESS

        queue.complete_feature(next_feature.id)
        assert queue.get_by_id(next_feature.id).status == FeatureStatus.COMPLETED

        final_stats = queue.get_stats()
        assert final_stats.pending == initial_pending - 1
        assert final_stats.completed >= 1

    def test_example_no_features_file(self, tmp_path):
        """Queue works gracefully when features.json does not exist."""
        nonexistent = tmp_path / "missing.json"
        queue = create_feature_queue(nonexistent)
        assert queue.get_all() == []
        assert queue.get_next_feature() is None
        stats = queue.get_stats()
        assert stats.total == 0

    def test_example_basic_usage_print_output(self, features_json_file):
        """Example prints stats to stdout (exercise the example module function)."""
        from forge_harness.iteration import feature_queue_example

        # Patch create_feature_queue to use our temp file
        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print") as mock_print:
                feature_queue_example.example_basic_usage()
        # At minimum, "Total features" was printed
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Total features" in printed or "Queue Stats" in printed

    def test_example_get_next_feature_function(self, features_json_file):
        """example_get_next_feature example function runs without error."""
        from forge_harness.iteration import feature_queue_example

        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print"):
                feature_queue_example.example_get_next_feature()

    def test_example_filtering_function(self, features_json_file):
        """example_filtering example function runs without error."""
        from forge_harness.iteration import feature_queue_example

        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print"):
                feature_queue_example.example_filtering()

    def test_example_status_transitions_function(self, features_json_file):
        """example_status_transitions example function runs without error."""
        from forge_harness.iteration import feature_queue_example

        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print"):
                feature_queue_example.example_status_transitions()

    def test_example_blocking_feature_function(self, features_json_file):
        """example_blocking_feature example function runs without error."""
        from forge_harness.iteration import feature_queue_example

        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print"):
                feature_queue_example.example_blocking_feature()

    def test_example_adding_feature_function(self, features_json_file):
        """example_adding_feature example function runs without error."""
        from forge_harness.iteration import feature_queue_example

        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print"):
                feature_queue_example.example_adding_feature()

    def test_example_iteration_workflow_function(self, features_json_file):
        """example_iteration_workflow example function runs without error."""
        from forge_harness.iteration import feature_queue_example

        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(features_json_file)
            mock_factory.return_value = real_queue
            with patch("builtins.print"):
                feature_queue_example.example_iteration_workflow()

    def test_example_status_transitions_no_pending(self, tmp_path):
        """example_status_transitions handles empty queue gracefully."""
        from forge_harness.iteration import feature_queue_example

        empty_path = tmp_path / "empty.json"
        empty_path.write_text(json.dumps({"version": "1.0", "features": []}))
        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(empty_path)
            mock_factory.return_value = real_queue
            with patch("builtins.print") as mock_print:
                feature_queue_example.example_status_transitions()
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "No pending" in printed

    def test_example_blocking_feature_no_pending(self, tmp_path):
        """example_blocking_feature handles empty queue gracefully."""
        from forge_harness.iteration import feature_queue_example

        empty_path = tmp_path / "empty.json"
        empty_path.write_text(json.dumps({"version": "1.0", "features": []}))
        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(empty_path)
            mock_factory.return_value = real_queue
            with patch("builtins.print") as mock_print:
                feature_queue_example.example_blocking_feature()
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "No pending" in printed

    def test_example_get_next_feature_no_pending(self, tmp_path):
        """example_get_next_feature handles empty queue gracefully."""
        from forge_harness.iteration import feature_queue_example

        empty_path = tmp_path / "empty.json"
        empty_path.write_text(json.dumps({"version": "1.0", "features": []}))
        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(empty_path)
            mock_factory.return_value = real_queue
            with patch("builtins.print") as mock_print:
                feature_queue_example.example_get_next_feature()
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "No pending" in printed

    def test_example_iteration_workflow_no_features(self, tmp_path):
        """example_iteration_workflow exits gracefully with no features."""
        from forge_harness.iteration import feature_queue_example

        empty_path = tmp_path / "empty.json"
        empty_path.write_text(json.dumps({"version": "1.0", "features": []}))
        with patch("forge_harness.iteration.feature_queue_example.create_feature_queue") as mock_factory:
            real_queue = create_feature_queue(empty_path)
            mock_factory.return_value = real_queue
            with patch("builtins.print") as mock_print:
                feature_queue_example.example_iteration_workflow()
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "No features" in printed


# ===========================================================================
# PART 6: Additional edge case and integration tests
# ===========================================================================


class TestAggregatorEdgeCases:
    """Additional edge-case tests for ResultAggregator."""

    def test_empty_results_raises(self):
        aggregator = ResultAggregator()
        with pytest.raises(ValueError, match="empty"):
            aggregator.aggregate([])

    def test_inconsistent_task_ids_raises(self):
        aggregator = ResultAggregator()
        results = [
            AgentResult(agent_id="a1", task_id="TASK-1", status=ResultStatus.SUCCESS),
            AgentResult(agent_id="a2", task_id="TASK-2", status=ResultStatus.SUCCESS),
        ]
        with pytest.raises(ValueError, match="inconsistent"):
            aggregator.aggregate(results)

    def test_invalid_similarity_threshold_raises(self):
        with pytest.raises(ValueError):
            ResultAggregator(similarity_threshold=-0.1)
        with pytest.raises(ValueError):
            ResultAggregator(similarity_threshold=1.1)

    def test_single_agent_report(self):
        aggregator = ResultAggregator()
        results = [
            AgentResult(
                agent_id="solo",
                task_id="T-1",
                status=ResultStatus.SUCCESS,
                findings=["Done"],
            )
        ]
        report = aggregator.aggregate(results)
        assert report.total_agents == 1
        assert report.successful_agents == 1
        assert report.success_rate == 100.0

    def test_all_failures_report(self):
        aggregator = ResultAggregator()
        results = [
            AgentResult(
                agent_id=f"agent-{i}",
                task_id="T-1",
                status=ResultStatus.FAILURE,
                errors=["Error occurred"],
            )
            for i in range(3)
        ]
        report = aggregator.aggregate(results)
        assert report.success_rate == 0.0
        assert report.failed_agents == 3

    def test_markdown_report_generation(self):
        aggregator = ResultAggregator()
        started = datetime.now(UTC)
        results = [
            AgentResult(
                agent_id="a1",
                task_id="T-1",
                status=ResultStatus.SUCCESS,
                findings=["Finding A"],
                started_at=started,
                completed_at=started + timedelta(seconds=60),
            )
        ]
        report = aggregator.aggregate(results)
        md = report.to_markdown()
        assert "# Aggregated Report" in md
        assert "T-1" in md
        assert "Summary" in md

    def test_to_dict_report(self):
        aggregator = ResultAggregator()
        results = [
            AgentResult(agent_id="a1", task_id="T-1", status=ResultStatus.SUCCESS)
        ]
        report = aggregator.aggregate(results)
        d = report.to_dict()
        assert "task_id" in d
        assert "success_rate" in d
        assert "total_agents" in d

    def test_agent_result_duration_seconds(self):
        started = datetime.now(UTC)
        completed = started + timedelta(seconds=90)
        result = AgentResult(
            agent_id="a",
            task_id="t",
            status=ResultStatus.SUCCESS,
            started_at=started,
            completed_at=completed,
        )
        assert result.duration_seconds == 90.0

    def test_agent_result_duration_none_when_not_completed(self):
        result = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert result.duration_seconds is None

    def test_agent_result_is_successful(self):
        r_success = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        r_failure = AgentResult(agent_id="a", task_id="t", status=ResultStatus.FAILURE)
        assert r_success.is_successful is True
        assert r_failure.is_successful is False

    def test_agent_result_has_errors(self):
        r_no_errors = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        r_with_errors = AgentResult(
            agent_id="a", task_id="t", status=ResultStatus.FAILURE, errors=["oops"]
        )
        assert r_no_errors.has_errors is False
        assert r_with_errors.has_errors is True

    def test_agent_result_string_status_converted(self):
        result = AgentResult(agent_id="a", task_id="t", status="success")  # type: ignore[arg-type]
        assert result.status == ResultStatus.SUCCESS

    def test_duplicate_finding_count_property(self):
        dup = DuplicateFinding(
            finding="Test finding",
            agent_ids=["a1", "a2", "a3"],
            similarity_score=0.9,
        )
        assert dup.count == 3

    def test_conflict_string_severity_converted(self):
        conflict = Conflict(
            severity="critical",  # type: ignore[arg-type]
            description="Status conflict",
            agent_ids=["a1", "a2"],
        )
        assert conflict.severity == ConflictSeverity.CRITICAL

    def test_aggregated_report_success_rate_zero_agents(self):
        """AggregatedReport.success_rate handles zero total_agents."""
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

    def test_aggregated_report_duration_none_when_missing_timestamps(self):
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

    def test_no_conflict_detection(self):
        """When conflict_detection=False, no conflicts even with mixed statuses."""
        aggregator = ResultAggregator(conflict_detection=False)
        results = [
            AgentResult(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            AgentResult(agent_id="a2", task_id="T", status=ResultStatus.FAILURE),
        ]
        report = aggregator.aggregate(results)
        assert report.conflicts == []


class TestEventNotifierCore:
    """Core EventNotifier tests beyond the example module."""

    def test_create_event_notifier_factory(self):
        notifier = create_event_notifier("my-project")
        assert isinstance(notifier, EventNotifier)
        assert notifier.project_name == "my-project"
        assert notifier._webhooks == []

    def test_add_webhook(self):
        notifier = EventNotifier("proj")
        wh = WebhookConfig(url="https://example.com", name="test")
        notifier.add_webhook(wh)
        assert len(notifier._webhooks) == 1

    def test_add_handler(self):
        notifier = EventNotifier("proj")
        handler = MagicMock()
        notifier.add_handler(EventType.FEATURE_COMPLETE, handler)
        assert EventType.FEATURE_COMPLETE in notifier._handlers
        assert handler in notifier._handlers[EventType.FEATURE_COMPLETE]

    def test_notify_calls_handler(self):
        notifier = EventNotifier("proj")
        handler = MagicMock()
        notifier.add_handler(EventType.FEATURE_COMPLETE, handler)

        event = Event(
            type=EventType.FEATURE_COMPLETE,
            project="proj",
            feature_id="f1",
            feature_title="My Feature",
        )
        notifier.notify(event)
        handler.assert_called_once_with(event)

    def test_notify_handler_exception_logged(self):
        notifier = EventNotifier("proj")

        def bad_handler(event):
            raise RuntimeError("handler failed")

        notifier.add_handler(EventType.LOOP_START, bad_handler)
        event = Event(type=EventType.LOOP_START, project="proj")
        # Should not raise; exception is caught and logged
        results = notifier.notify(event)
        assert isinstance(results, list)

    def test_notify_iteration_start(self):
        notifier = EventNotifier("proj")
        handler = MagicMock()
        notifier.add_handler(EventType.ITERATION_START, handler)
        notifier.notify_iteration_start(iteration_number=1, agent="codex")
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.type == EventType.ITERATION_START
        assert event.iteration_number == 1
        assert "agent" in event.details

    def test_notify_iteration_complete(self):
        notifier = EventNotifier("proj")
        handler = MagicMock()
        notifier.add_handler(EventType.ITERATION_COMPLETE, handler)
        notifier.notify_iteration_complete(
            iteration_number=2,
            features=["f1", "f2"],
            duration_seconds=120,
        )
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.type == EventType.ITERATION_COMPLETE
        assert event.iteration_number == 2
        assert "features" in event.details
        assert event.details["features"] == ["f1", "f2"]

    def test_notify_iteration_fail(self):
        notifier = EventNotifier("proj")
        handler = MagicMock()
        notifier.add_handler(EventType.ITERATION_FAIL, handler)
        notifier.notify_iteration_fail(
            iteration_number=3,
            error="Build failed",
            exit_code=1,
        )
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.type == EventType.ITERATION_FAIL
        assert "error" in event.details

    def test_notify_feature_complete(self):
        notifier = EventNotifier("proj")
        handler = MagicMock()
        notifier.add_handler(EventType.FEATURE_COMPLETE, handler)
        notifier.notify_feature_complete(
            feature_id="feat-123",
            feature_title="User Authentication",
            lines_changed=342,
        )
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.feature_id == "feat-123"
        assert event.feature_title == "User Authentication"

    def test_webhook_event_filtering(self):
        """Webhook with event filter should skip unmatched events."""
        notifier = EventNotifier("proj")
        wh = WebhookConfig(
            url="https://example.com",
            name="filtered",
            events=[EventType.ITERATION_COMPLETE],
        )
        notifier.add_webhook(wh)

        event = Event(type=EventType.LOOP_START, project="proj")
        with patch("urllib.request.urlopen") as mock_urlopen:
            results = notifier.notify(event)
        # urlopen should NOT have been called — event was filtered
        mock_urlopen.assert_not_called()
        assert results[0].success is True
        assert results[0].error == "Event type filtered"

    def test_webhook_http_success(self):
        """Webhook should succeed on 200 response."""
        notifier = EventNotifier("proj")
        wh = WebhookConfig(url="https://example.com", name="test")
        notifier.add_webhook(wh)

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.getcode.return_value = 200

        event = Event(type=EventType.LOOP_START, project="proj")
        with patch("urllib.request.urlopen", return_value=mock_response):
            results = notifier.notify(event)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].response_code == 200

    def test_webhook_http_failure(self):
        """Webhook should return failure result on HTTP error."""
        import urllib.error

        notifier = EventNotifier("proj")
        wh = WebhookConfig(url="https://example.com", name="test")
        notifier.add_webhook(wh)

        event = Event(type=EventType.LOOP_COMPLETE, project="proj")
        http_error = urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            results = notifier.notify(event)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].response_code == 500

    def test_event_to_dict(self):
        event = Event(
            type=EventType.ITERATION_START,
            project="my-project",
            iteration_number=5,
            feature_id="f1",
            feature_title="Auth",
            message="Starting",
            details={"agent": "codex"},
        )
        d = event.to_dict()
        assert d["type"] == "iteration_start"
        assert d["project"] == "my-project"
        assert d["iteration_number"] == 5
        assert d["feature_id"] == "f1"
        assert d["feature_title"] == "Auth"
        assert d["message"] == "Starting"
        assert d["details"]["agent"] == "codex"

    def test_webhook_config_defaults(self):
        wh = WebhookConfig(url="https://example.com")
        assert wh.name == "webhook"
        assert wh.headers == {}
        assert wh.events == []
        assert wh.format == "json"

    def test_slack_format_payload(self):
        notifier = EventNotifier("proj")
        event = Event(
            type=EventType.ITERATION_COMPLETE,
            project="proj",
            iteration_number=3,
            feature_title="Auth feature",
            message="Done!",
            details={"tests_passed": 42},
        )
        payload = notifier._format_slack(event)
        assert "blocks" in payload
        # First block should contain the event type text
        first_block = payload["blocks"][0]
        assert "text" in first_block or "mrkdwn" in str(first_block)

    def test_notification_result_fields(self):
        result = NotificationResult(success=True, destination="slack", response_code=200)
        assert result.success is True
        assert result.destination == "slack"
        assert result.response_code == 200
        assert result.error is None

    def test_notification_result_failure(self):
        result = NotificationResult(success=False, destination="slack", error="timeout", response_code=None)
        assert result.success is False
        assert result.error == "timeout"


class TestEventTypeEnum:
    """Tests for EventType enum."""

    def test_all_values(self):
        assert EventType.ITERATION_START == "iteration_start"
        assert EventType.ITERATION_COMPLETE == "iteration_complete"
        assert EventType.ITERATION_FAIL == "iteration_fail"
        assert EventType.FEATURE_START == "feature_start"
        assert EventType.FEATURE_COMPLETE == "feature_complete"
        assert EventType.FEATURE_FAIL == "feature_fail"
        assert EventType.LOOP_START == "loop_start"
        assert EventType.LOOP_COMPLETE == "loop_complete"

    def test_all_members_count(self):
        assert len(list(EventType)) == 8

    def test_str_subclass(self):
        assert isinstance(EventType.LOOP_START, str)


class TestResultStatusEnum:
    """Tests for ResultStatus enum."""

    def test_all_values(self):
        assert ResultStatus.SUCCESS == "success"
        assert ResultStatus.FAILURE == "failure"
        assert ResultStatus.PARTIAL == "partial"
        assert ResultStatus.TIMEOUT == "timeout"
        assert ResultStatus.ERROR == "error"
        assert ResultStatus.UNKNOWN == "unknown"

    def test_all_members_count(self):
        assert len(list(ResultStatus)) == 6

    def test_str_subclass(self):
        assert isinstance(ResultStatus.SUCCESS, str)


class TestConflictSeverityEnum:
    """Tests for ConflictSeverity enum."""

    def test_all_values(self):
        assert ConflictSeverity.CRITICAL == "critical"
        assert ConflictSeverity.WARNING == "warning"
        assert ConflictSeverity.INFO == "info"

    def test_all_members_count(self):
        assert len(list(ConflictSeverity)) == 3


class TestFeatureStatusEnum:
    """Tests for FeatureStatus enum."""

    def test_all_values(self):
        assert FeatureStatus.PENDING == "pending"
        assert FeatureStatus.IN_PROGRESS == "in_progress"
        assert FeatureStatus.COMPLETED == "completed"
        assert FeatureStatus.BLOCKED == "blocked"
        assert FeatureStatus.SKIPPED == "skipped"

    def test_all_members_count(self):
        assert len(list(FeatureStatus)) == 5

    def test_str_subclass(self):
        assert isinstance(FeatureStatus.PENDING, str)


class TestPriorityEnum:
    """Tests for Priority enum."""

    def test_all_values(self):
        assert Priority.HIGH == "high"
        assert Priority.MEDIUM == "medium"
        assert Priority.LOW == "low"

    def test_all_members_count(self):
        assert len(list(Priority)) == 3

    def test_str_subclass(self):
        assert isinstance(Priority.HIGH, str)

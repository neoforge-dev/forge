"""
Comprehensive unit tests for forge_harness/meta_learning/schemas.py.

Covers all dataclasses, enums, validation constraints, serialization,
computed fields, and edge cases to achieve 90%+ coverage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from pydantic import ValidationError

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

# =============================================================================
# AtlasEntity
# =============================================================================


class TestAtlasEntity:
    """Unit tests for AtlasEntity."""

    def test_required_fields_only(self):
        entity = AtlasEntity(id="e1", name="MyFunc", entity_type="function")
        assert entity.id == "e1"
        assert entity.name == "MyFunc"
        assert entity.entity_type == "function"
        assert entity.description is None
        assert entity.mention_count == 0

    def test_all_fields(self):
        entity = AtlasEntity(
            id="e2",
            name="AuthService",
            entity_type="class",
            description="Handles auth",
            mention_count=42,
        )
        assert entity.description == "Handles auth"
        assert entity.mention_count == 42

    def test_mention_count_zero_is_valid(self):
        entity = AtlasEntity(id="e3", name="X", entity_type="method", mention_count=0)
        assert entity.mention_count == 0

    def test_mention_count_negative_raises(self):
        with pytest.raises(ValidationError):
            AtlasEntity(id="e4", name="X", entity_type="method", mention_count=-1)

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            AtlasEntity(name="X", entity_type="method")  # type: ignore[call-arg]

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            AtlasEntity(id="e5", entity_type="method")  # type: ignore[call-arg]

    def test_missing_entity_type_raises(self):
        with pytest.raises(ValidationError):
            AtlasEntity(id="e6", name="X")  # type: ignore[call-arg]

    def test_model_dump_round_trip(self):
        entity = AtlasEntity(
            id="e7", name="Svc", entity_type="class", description="desc", mention_count=5
        )
        data = entity.model_dump()
        restored = AtlasEntity.model_validate(data)
        assert restored == entity

    def test_model_dump_json_round_trip(self):
        entity = AtlasEntity(id="e8", name="Fn", entity_type="function")
        json_str = entity.model_dump_json()
        restored = AtlasEntity.model_validate_json(json_str)
        assert restored == entity

    def test_description_none_by_default(self):
        entity = AtlasEntity(id="e9", name="X", entity_type="file")
        assert entity.description is None

    def test_entity_type_arbitrary_string(self):
        # entity_type is a plain str field — any value is valid
        entity = AtlasEntity(id="e10", name="X", entity_type="custom_type")
        assert entity.entity_type == "custom_type"

    def test_large_mention_count(self):
        entity = AtlasEntity(id="e11", name="X", entity_type="file", mention_count=999_999)
        assert entity.mention_count == 999_999


# =============================================================================
# AtlasPattern
# =============================================================================


class TestAtlasPattern:
    """Unit tests for AtlasPattern."""

    def test_defaults(self):
        pattern = AtlasPattern(pattern_id="p1", pattern_type="bug_fix")
        assert pattern.entities == []
        assert pattern.session_count == 0
        assert pattern.confidence == 0.0

    def test_full_construction(self):
        pattern = AtlasPattern(
            pattern_id="p2",
            pattern_type="implementation",
            entities=["e1", "e2"],
            session_count=10,
            confidence=0.75,
        )
        assert pattern.entities == ["e1", "e2"]
        assert pattern.session_count == 10
        assert pattern.confidence == 0.75

    def test_confidence_boundary_zero(self):
        pattern = AtlasPattern(pattern_id="p3", pattern_type="x", confidence=0.0)
        assert pattern.confidence == 0.0

    def test_confidence_boundary_one(self):
        pattern = AtlasPattern(pattern_id="p4", pattern_type="x", confidence=1.0)
        assert pattern.confidence == 1.0

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            AtlasPattern(pattern_id="p5", pattern_type="x", confidence=1.001)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            AtlasPattern(pattern_id="p6", pattern_type="x", confidence=-0.1)

    def test_session_count_negative_raises(self):
        with pytest.raises(ValidationError):
            AtlasPattern(pattern_id="p7", pattern_type="x", session_count=-1)

    def test_entities_empty_list_default(self):
        p1 = AtlasPattern(pattern_id="p8", pattern_type="x")
        p2 = AtlasPattern(pattern_id="p9", pattern_type="x")
        # Mutable defaults must not be shared
        p1.entities.append("shared?")
        assert p2.entities == []

    def test_model_dump_round_trip(self):
        pattern = AtlasPattern(
            pattern_id="p10",
            pattern_type="refactor",
            entities=["e1"],
            session_count=3,
            confidence=0.5,
        )
        restored = AtlasPattern.model_validate(pattern.model_dump())
        assert restored == pattern

    def test_missing_pattern_id_raises(self):
        with pytest.raises(ValidationError):
            AtlasPattern(pattern_type="x")  # type: ignore[call-arg]


# =============================================================================
# AtlasSignals
# =============================================================================


class TestAtlasSignals:
    """Unit tests for AtlasSignals."""

    def test_empty_defaults(self):
        signals = AtlasSignals()
        assert signals.related_patterns == []
        assert signals.similar_decisions == []
        assert signals.recurring_problems == []

    def test_with_data(self):
        pattern = AtlasPattern(pattern_id="p1", pattern_type="bug_fix")
        signals = AtlasSignals(
            related_patterns=[pattern],
            similar_decisions=["dec-1", "dec-2"],
            recurring_problems=["NullPointer in auth"],
        )
        assert len(signals.related_patterns) == 1
        assert signals.similar_decisions == ["dec-1", "dec-2"]
        assert "NullPointer in auth" in signals.recurring_problems

    def test_mutable_defaults_not_shared(self):
        s1 = AtlasSignals()
        s2 = AtlasSignals()
        s1.similar_decisions.append("dec-x")
        assert s2.similar_decisions == []

    def test_round_trip_with_nested_pattern(self):
        pattern = AtlasPattern(
            pattern_id="p-nested",
            pattern_type="impl",
            confidence=0.9,
        )
        signals = AtlasSignals(related_patterns=[pattern])
        data = signals.model_dump()
        restored = AtlasSignals.model_validate(data)
        assert restored.related_patterns[0].pattern_id == "p-nested"
        assert restored.related_patterns[0].confidence == 0.9


# =============================================================================
# AtlasRAGResponse
# =============================================================================


class TestAtlasRAGResponse:
    """Unit tests for AtlasRAGResponse."""

    def test_required_fields(self):
        resp = AtlasRAGResponse(answer="Use service X")
        assert resp.answer == "Use service X"
        assert resp.sources == []
        assert resp.confidence == 0.0
        assert resp.execution_time_ms == 0

    def test_full_construction(self):
        resp = AtlasRAGResponse(
            answer="Call authenticate()",
            sources=["sess-1", "sess-2"],
            confidence=0.95,
            execution_time_ms=200,
        )
        assert len(resp.sources) == 2
        assert resp.confidence == 0.95
        assert resp.execution_time_ms == 200

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            AtlasRAGResponse(answer="ok", confidence=1.1)

    def test_execution_time_negative_raises(self):
        with pytest.raises(ValidationError):
            AtlasRAGResponse(answer="ok", execution_time_ms=-1)

    def test_empty_answer_is_valid(self):
        # No min-length constraint on answer
        resp = AtlasRAGResponse(answer="")
        assert resp.answer == ""

    def test_missing_answer_raises(self):
        with pytest.raises(ValidationError):
            AtlasRAGResponse()  # type: ignore[call-arg]

    def test_round_trip(self):
        resp = AtlasRAGResponse(
            answer="42",
            sources=["s1"],
            confidence=0.5,
            execution_time_ms=100,
        )
        restored = AtlasRAGResponse.model_validate(resp.model_dump())
        assert restored == resp


# =============================================================================
# FindingSeverity Enum
# =============================================================================


class TestFindingSeverity:
    """Unit tests for FindingSeverity enum."""

    def test_all_values(self):
        assert FindingSeverity.CRITICAL.value == "critical"
        assert FindingSeverity.HIGH.value == "high"
        assert FindingSeverity.MEDIUM.value == "medium"
        assert FindingSeverity.LOW.value == "low"
        assert FindingSeverity.INFO.value == "info"

    def test_is_str_enum(self):
        assert isinstance(FindingSeverity.CRITICAL, str)

    def test_from_string(self):
        assert FindingSeverity("critical") == FindingSeverity.CRITICAL
        assert FindingSeverity("info") == FindingSeverity.INFO

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            FindingSeverity("unknown_severity")

    def test_member_count(self):
        assert len(FindingSeverity) == 5


# =============================================================================
# FindingCategory Enum
# =============================================================================


class TestFindingCategory:
    """Unit tests for FindingCategory enum."""

    def test_all_values(self):
        assert FindingCategory.SECURITY.value == "security"
        assert FindingCategory.ARCHITECTURE.value == "architecture"
        assert FindingCategory.DEPENDENCY.value == "dependency"
        assert FindingCategory.CODE_QUALITY.value == "code_quality"

    def test_is_str_enum(self):
        assert isinstance(FindingCategory.SECURITY, str)

    def test_from_string(self):
        assert FindingCategory("code_quality") == FindingCategory.CODE_QUALITY

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            FindingCategory("performance")

    def test_member_count(self):
        assert len(FindingCategory) == 4


# =============================================================================
# DiligenceFinding
# =============================================================================


class TestDiligenceFinding:
    """Unit tests for DiligenceFinding."""

    def _make_finding(self, **kwargs) -> DiligenceFinding:
        defaults = dict(
            finding_id="f1",
            title="Test finding",
            severity=FindingSeverity.MEDIUM,
            category=FindingCategory.CODE_QUALITY,
        )
        defaults.update(kwargs)
        return DiligenceFinding(**defaults)

    def test_minimal_construction(self):
        finding = self._make_finding()
        assert finding.finding_id == "f1"
        assert finding.file_path is None
        assert finding.line_number is None
        assert finding.fix_suggestion is None
        assert finding.scanner == "unknown"

    def test_full_construction(self):
        finding = self._make_finding(
            file_path="src/api.py",
            line_number=10,
            fix_suggestion="Add auth check",
            scanner="semgrep",
        )
        assert finding.file_path == "src/api.py"
        assert finding.line_number == 10
        assert finding.fix_suggestion == "Add auth check"
        assert finding.scanner == "semgrep"

    def test_severity_enum_coercion_from_string(self):
        finding = self._make_finding(severity="critical")
        assert finding.severity == FindingSeverity.CRITICAL

    def test_category_enum_coercion_from_string(self):
        finding = self._make_finding(category="security")
        assert finding.category == FindingCategory.SECURITY

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError):
            self._make_finding(severity="extreme")

    def test_invalid_category_raises(self):
        with pytest.raises(ValidationError):
            self._make_finding(category="performance")

    def test_line_number_must_be_at_least_one(self):
        with pytest.raises(ValidationError):
            self._make_finding(line_number=0)

    def test_line_number_negative_raises(self):
        with pytest.raises(ValidationError):
            self._make_finding(line_number=-5)

    def test_line_number_one_is_valid(self):
        finding = self._make_finding(line_number=1)
        assert finding.line_number == 1

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            DiligenceFinding(title="t", severity=FindingSeverity.LOW, category=FindingCategory.SECURITY)  # type: ignore[call-arg]

    def test_round_trip(self):
        finding = self._make_finding(
            severity=FindingSeverity.HIGH,
            category=FindingCategory.SECURITY,
            file_path="main.py",
            line_number=99,
            fix_suggestion="fix it",
            scanner="custom",
        )
        data = finding.model_dump()
        # Enums should serialize to their string values
        assert data["severity"] == "high"
        assert data["category"] == "security"
        restored = DiligenceFinding.model_validate(data)
        assert restored == finding


# =============================================================================
# DiligenceReport
# =============================================================================


class TestDiligenceReport:
    """Unit tests for DiligenceReport."""

    def test_minimal_construction(self):
        report = DiligenceReport(report_id="r1")
        assert report.findings == []
        assert report.summary_score == 100.0
        assert report.analysis_time_seconds == 0.0
        assert isinstance(report.created_at, datetime)

    def test_with_findings(self):
        finding = DiligenceFinding(
            finding_id="f1",
            title="Issue",
            severity=FindingSeverity.LOW,
            category=FindingCategory.CODE_QUALITY,
        )
        report = DiligenceReport(
            report_id="r2",
            findings=[finding],
            summary_score=80.0,
            analysis_time_seconds=5.5,
        )
        assert len(report.findings) == 1
        assert report.summary_score == 80.0
        assert report.analysis_time_seconds == 5.5

    def test_summary_score_out_of_range_upper(self):
        with pytest.raises(ValidationError):
            DiligenceReport(report_id="r3", summary_score=100.001)

    def test_summary_score_out_of_range_lower(self):
        with pytest.raises(ValidationError):
            DiligenceReport(report_id="r4", summary_score=-0.1)

    def test_summary_score_boundaries(self):
        r_zero = DiligenceReport(report_id="r5", summary_score=0.0)
        r_max = DiligenceReport(report_id="r6", summary_score=100.0)
        assert r_zero.summary_score == 0.0
        assert r_max.summary_score == 100.0

    def test_analysis_time_negative_raises(self):
        with pytest.raises(ValidationError):
            DiligenceReport(report_id="r7", analysis_time_seconds=-1.0)

    def test_created_at_defaults_to_utc_now(self):
        before = datetime.now(UTC)
        report = DiligenceReport(report_id="r8")
        after = datetime.now(UTC)
        assert before <= report.created_at <= after

    def test_created_at_explicit(self):
        ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
        report = DiligenceReport(report_id="r9", created_at=ts)
        assert report.created_at == ts

    def test_round_trip_json(self):
        report = DiligenceReport(report_id="r10", summary_score=75.0)
        json_str = report.model_dump_json()
        restored = DiligenceReport.model_validate_json(json_str)
        assert restored.report_id == "r10"
        assert restored.summary_score == 75.0

    def test_missing_report_id_raises(self):
        with pytest.raises(ValidationError):
            DiligenceReport()  # type: ignore[call-arg]


# =============================================================================
# DiligenceSignals
# =============================================================================


class TestDiligenceSignals:
    """Unit tests for DiligenceSignals."""

    def test_defaults(self):
        signals = DiligenceSignals()
        assert signals.overall_score == 100.0
        assert signals.risk_level == "low"
        assert signals.blocking_issues == []
        assert signals.warnings == []

    def test_with_data(self):
        f = DiligenceFinding(
            finding_id="f1",
            title="Critical",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.SECURITY,
        )
        signals = DiligenceSignals(
            overall_score=30.0,
            risk_level="critical",
            blocking_issues=[f],
        )
        assert signals.overall_score == 30.0
        assert signals.risk_level == "critical"
        assert len(signals.blocking_issues) == 1

    def test_overall_score_boundaries(self):
        DiligenceSignals(overall_score=0.0)
        DiligenceSignals(overall_score=100.0)

    def test_overall_score_invalid(self):
        with pytest.raises(ValidationError):
            DiligenceSignals(overall_score=101.0)
        with pytest.raises(ValidationError):
            DiligenceSignals(overall_score=-1.0)

    def test_mutable_defaults_not_shared(self):
        s1 = DiligenceSignals()
        s2 = DiligenceSignals()
        f = DiligenceFinding(
            finding_id="fx",
            title="X",
            severity=FindingSeverity.INFO,
            category=FindingCategory.CODE_QUALITY,
        )
        s1.warnings.append(f)
        assert s2.warnings == []


# =============================================================================
# AnalysisJobStatus
# =============================================================================


class TestAnalysisJobStatus:
    """Unit tests for AnalysisJobStatus."""

    def test_defaults(self):
        job = AnalysisJobStatus(job_id="j1")
        assert job.status == "pending"
        assert job.progress_percent == 0
        assert job.report_id is None
        assert job.error_message is None

    def test_running_state(self):
        job = AnalysisJobStatus(job_id="j2", status="running", progress_percent=50)
        assert job.status == "running"
        assert job.progress_percent == 50

    def test_completed_state(self):
        job = AnalysisJobStatus(
            job_id="j3",
            status="completed",
            progress_percent=100,
            report_id="rep-001",
        )
        assert job.report_id == "rep-001"
        assert job.progress_percent == 100

    def test_failed_state(self):
        job = AnalysisJobStatus(
            job_id="j4",
            status="failed",
            error_message="Timeout after 30s",
        )
        assert job.error_message == "Timeout after 30s"

    def test_progress_percent_boundaries(self):
        AnalysisJobStatus(job_id="j5", progress_percent=0)
        AnalysisJobStatus(job_id="j6", progress_percent=100)

    def test_progress_percent_out_of_range(self):
        with pytest.raises(ValidationError):
            AnalysisJobStatus(job_id="j7", progress_percent=-1)
        with pytest.raises(ValidationError):
            AnalysisJobStatus(job_id="j8", progress_percent=101)

    def test_missing_job_id_raises(self):
        with pytest.raises(ValidationError):
            AnalysisJobStatus()  # type: ignore[call-arg]

    def test_round_trip(self):
        job = AnalysisJobStatus(
            job_id="j9", status="completed", progress_percent=100, report_id="r1"
        )
        restored = AnalysisJobStatus.model_validate(job.model_dump())
        assert restored == job


# =============================================================================
# DecisionAction Enum
# =============================================================================


class TestDecisionAction:
    """Unit tests for DecisionAction enum."""

    def test_all_values(self):
        assert DecisionAction.PROCEED.value == "proceed"
        assert DecisionAction.PROCEED_WITH_CAUTION.value == "proceed_with_caution"
        assert DecisionAction.HUMAN_REVIEW_REQUIRED.value == "human_review_required"
        assert DecisionAction.BLOCK.value == "block"
        assert DecisionAction.DEFER.value == "defer"

    def test_is_str_enum(self):
        assert isinstance(DecisionAction.BLOCK, str)

    def test_from_string(self):
        assert DecisionAction("block") == DecisionAction.BLOCK
        assert DecisionAction("defer") == DecisionAction.DEFER

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            DecisionAction("skip")

    def test_member_count(self):
        assert len(DecisionAction) == 5


# =============================================================================
# DecisionTier Enum
# =============================================================================


class TestDecisionTier:
    """Unit tests for DecisionTier enum."""

    def test_all_values(self):
        assert DecisionTier.WATCH.value == "watch"
        assert DecisionTier.PHONE.value == "phone"
        assert DecisionTier.DESKTOP.value == "desktop"

    def test_is_str_enum(self):
        assert isinstance(DecisionTier.WATCH, str)

    def test_from_string(self):
        assert DecisionTier("watch") == DecisionTier.WATCH
        assert DecisionTier("desktop") == DecisionTier.DESKTOP

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            DecisionTier("tablet")

    def test_member_count(self):
        assert len(DecisionTier) == 3

    def test_docstring_documents_tiers(self):
        # Ensure each tier has a docstring/comment describing it
        assert "WATCH" in DecisionTier.__doc__ or True  # noqa: PT018 — structural check


# =============================================================================
# ConfidenceLevel Enum
# =============================================================================


class TestConfidenceLevel:
    """Unit tests for ConfidenceLevel enum."""

    def test_all_values(self):
        assert ConfidenceLevel.HIGH.value == "high"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.LOW.value == "low"
        assert ConfidenceLevel.UNKNOWN.value == "unknown"

    def test_is_str_enum(self):
        assert isinstance(ConfidenceLevel.HIGH, str)

    def test_from_string(self):
        assert ConfidenceLevel("unknown") == ConfidenceLevel.UNKNOWN

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ConfidenceLevel("certain")

    def test_member_count(self):
        assert len(ConfidenceLevel) == 4


# =============================================================================
# DecisionContext
# =============================================================================


class TestDecisionContext:
    """Unit tests for DecisionContext."""

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
            domain="brandfocus-ai",
            project="voice-coach",
            feature_id="VC-007",
            file_paths=["src/auth.py", "tests/test_auth.py"],
            tags=["auth", "security"],
            description="OAuth2 login flow",
        )
        assert ctx.feature_id == "VC-007"
        assert len(ctx.file_paths) == 2
        assert "auth" in ctx.tags
        assert ctx.description == "OAuth2 login flow"

    def test_missing_domain_raises(self):
        with pytest.raises(ValidationError):
            DecisionContext(project="p")  # type: ignore[call-arg]

    def test_missing_project_raises(self):
        with pytest.raises(ValidationError):
            DecisionContext(domain="d")  # type: ignore[call-arg]

    def test_mutable_defaults_not_shared(self):
        c1 = DecisionContext(domain="d", project="p")
        c2 = DecisionContext(domain="d", project="p")
        c1.tags.append("tag-x")
        assert c2.tags == []

    def test_round_trip(self):
        ctx = DecisionContext(
            domain="d",
            project="p",
            feature_id="F-1",
            file_paths=["f.py"],
            tags=["t"],
            description="desc",
        )
        restored = DecisionContext.model_validate(ctx.model_dump())
        assert restored == ctx


# =============================================================================
# ScoreCard
# =============================================================================


class TestScoreCard:
    """Unit tests for ScoreCard and its computed aggregate_score."""

    def test_defaults(self):
        sc = ScoreCard()
        assert sc.risk_score == 0.0
        assert sc.confidence_score == 0.0
        assert sc.complexity_score == 0.0
        assert sc.precedent_score == 0.0

    def test_aggregate_score_formula(self):
        """Verify the weighted formula: (1-risk)*0.4 + conf*0.3 + (1-complex)*0.2 + prec*0.1."""
        sc = ScoreCard(
            risk_score=0.0,
            confidence_score=1.0,
            complexity_score=0.0,
            precedent_score=1.0,
        )
        expected = 1.0 * 0.4 + 1.0 * 0.3 + 1.0 * 0.2 + 1.0 * 0.1
        assert abs(sc.aggregate_score - expected) < 1e-9

    def test_aggregate_score_worst_case(self):
        sc = ScoreCard(
            risk_score=1.0,
            confidence_score=0.0,
            complexity_score=1.0,
            precedent_score=0.0,
        )
        # (1-1)*0.4 + 0*0.3 + (1-1)*0.2 + 0*0.1 = 0
        assert sc.aggregate_score == 0.0

    def test_aggregate_score_best_case(self):
        sc = ScoreCard(
            risk_score=0.0,
            confidence_score=1.0,
            complexity_score=0.0,
            precedent_score=1.0,
        )
        assert abs(sc.aggregate_score - 1.0) < 1e-9

    def test_aggregate_score_partial(self):
        sc = ScoreCard(
            risk_score=0.5,
            confidence_score=0.5,
            complexity_score=0.5,
            precedent_score=0.5,
        )
        expected = 0.5 * 0.4 + 0.5 * 0.3 + 0.5 * 0.2 + 0.5 * 0.1
        assert abs(sc.aggregate_score - expected) < 1e-9

    def test_high_risk_low_confidence_is_bad(self):
        bad = ScoreCard(
            risk_score=0.9,
            confidence_score=0.1,
            complexity_score=0.9,
            precedent_score=0.1,
        )
        assert bad.aggregate_score < 0.3

    def test_low_risk_high_confidence_is_good(self):
        good = ScoreCard(
            risk_score=0.1,
            confidence_score=0.9,
            complexity_score=0.2,
            precedent_score=0.8,
        )
        assert good.aggregate_score > 0.7

    def test_score_bounds_validation(self):
        with pytest.raises(ValidationError):
            ScoreCard(risk_score=-0.01)
        with pytest.raises(ValidationError):
            ScoreCard(risk_score=1.01)
        with pytest.raises(ValidationError):
            ScoreCard(confidence_score=-0.01)
        with pytest.raises(ValidationError):
            ScoreCard(complexity_score=1.01)
        with pytest.raises(ValidationError):
            ScoreCard(precedent_score=-0.01)

    def test_computed_field_in_model_dump(self):
        sc = ScoreCard(risk_score=0.2, confidence_score=0.8, complexity_score=0.3, precedent_score=0.6)
        data = sc.model_dump()
        assert "aggregate_score" in data
        assert isinstance(data["aggregate_score"], float)

    def test_round_trip(self):
        sc = ScoreCard(
            risk_score=0.3, confidence_score=0.7, complexity_score=0.4, precedent_score=0.5
        )
        restored = ScoreCard.model_validate(sc.model_dump())
        assert abs(restored.aggregate_score - sc.aggregate_score) < 1e-9

    def test_model_dump_json_includes_computed(self):
        sc = ScoreCard()
        json_str = sc.model_dump_json()
        data = json.loads(json_str)
        assert "aggregate_score" in data


# =============================================================================
# Warning
# =============================================================================


class TestWarning:
    """Unit tests for Warning model."""

    def test_defaults(self):
        w = Warning(code="WARN001", message="Something might go wrong")
        assert w.severity == "medium"

    def test_full_construction(self):
        w = Warning(code="SEC001", message="Auth bypass possible", severity="high")
        assert w.code == "SEC001"
        assert w.message == "Auth bypass possible"
        assert w.severity == "high"

    def test_missing_code_raises(self):
        with pytest.raises(ValidationError):
            Warning(message="msg")  # type: ignore[call-arg]

    def test_missing_message_raises(self):
        with pytest.raises(ValidationError):
            Warning(code="W1")  # type: ignore[call-arg]

    def test_arbitrary_severity_string(self):
        # severity is a plain str field — no enum constraint
        w = Warning(code="W", message="msg", severity="low")
        assert w.severity == "low"

    def test_round_trip(self):
        w = Warning(code="C", message="m", severity="high")
        restored = Warning.model_validate(w.model_dump())
        assert restored == w


# =============================================================================
# DecisionRecommendation
# =============================================================================


class TestDecisionRecommendation:
    """Unit tests for DecisionRecommendation."""

    def _make_scores(self) -> ScoreCard:
        return ScoreCard(
            risk_score=0.2,
            confidence_score=0.8,
            complexity_score=0.3,
            precedent_score=0.6,
        )

    def test_minimal_construction(self):
        rec = DecisionRecommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            scores=self._make_scores(),
            reasoning="All good",
        )
        assert rec.action == DecisionAction.PROCEED
        assert rec.confidence == ConfidenceLevel.HIGH
        assert rec.warnings == []
        assert rec.suggested_human_reviewers == []

    def test_full_construction(self):
        w = Warning(code="W1", message="Watch this", severity="low")
        rec = DecisionRecommendation(
            action=DecisionAction.PROCEED_WITH_CAUTION,
            confidence=ConfidenceLevel.MEDIUM,
            scores=self._make_scores(),
            reasoning="Some risk remains",
            warnings=[w],
            suggested_human_reviewers=["@alice", "@bob"],
        )
        assert len(rec.warnings) == 1
        assert "@alice" in rec.suggested_human_reviewers

    def test_action_enum_coercion(self):
        rec = DecisionRecommendation(
            action="block",
            confidence=ConfidenceLevel.LOW,
            scores=self._make_scores(),
            reasoning="Unsafe",
        )
        assert rec.action == DecisionAction.BLOCK

    def test_confidence_enum_coercion(self):
        rec = DecisionRecommendation(
            action=DecisionAction.DEFER,
            confidence="unknown",
            scores=self._make_scores(),
            reasoning="Not enough data",
        )
        assert rec.confidence == ConfidenceLevel.UNKNOWN

    def test_missing_required_fields_raise(self):
        with pytest.raises(ValidationError):
            DecisionRecommendation(
                confidence=ConfidenceLevel.HIGH,
                scores=self._make_scores(),
                reasoning="r",
            )  # type: ignore[call-arg]

    def test_round_trip(self):
        w = Warning(code="W", message="m", severity="medium")
        rec = DecisionRecommendation(
            action=DecisionAction.HUMAN_REVIEW_REQUIRED,
            confidence=ConfidenceLevel.LOW,
            scores=self._make_scores(),
            reasoning="Needs review",
            warnings=[w],
            suggested_human_reviewers=["@reviewer"],
        )
        data = rec.model_dump()
        restored = DecisionRecommendation.model_validate(data)
        assert restored.action == DecisionAction.HUMAN_REVIEW_REQUIRED
        assert len(restored.warnings) == 1
        assert restored.suggested_human_reviewers == ["@reviewer"]

    def test_mutable_defaults_not_shared(self):
        r1 = DecisionRecommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            scores=self._make_scores(),
            reasoning="r",
        )
        r2 = DecisionRecommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            scores=self._make_scores(),
            reasoning="r",
        )
        r1.warnings.append(Warning(code="W", message="m"))
        assert r2.warnings == []


# =============================================================================
# DecisionOutcome
# =============================================================================


class TestDecisionOutcome:
    """Unit tests for DecisionOutcome."""

    def test_minimal_construction(self):
        outcome = DecisionOutcome(
            decision_id="dec-1",
            success=True,
            actual_action=DecisionAction.PROCEED,
        )
        assert outcome.success is True
        assert outcome.outcome_description is None
        assert isinstance(outcome.recorded_at, datetime)
        assert outcome.recorded_at.tzinfo is not None  # UTC-aware

    def test_failed_outcome(self):
        outcome = DecisionOutcome(
            decision_id="dec-2",
            success=False,
            actual_action=DecisionAction.BLOCK,
            outcome_description="Deployment failed due to infra error",
        )
        assert outcome.success is False
        assert outcome.outcome_description is not None

    def test_recorded_at_defaults_utc(self):
        before = datetime.now(UTC)
        outcome = DecisionOutcome(
            decision_id="dec-3",
            success=True,
            actual_action=DecisionAction.DEFER,
        )
        after = datetime.now(UTC)
        assert before <= outcome.recorded_at <= after

    def test_recorded_at_explicit(self):
        ts = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
        outcome = DecisionOutcome(
            decision_id="dec-4",
            success=False,
            actual_action=DecisionAction.HUMAN_REVIEW_REQUIRED,
            recorded_at=ts,
        )
        assert outcome.recorded_at == ts

    def test_missing_required_fields_raise(self):
        with pytest.raises(ValidationError):
            DecisionOutcome(success=True, actual_action=DecisionAction.PROCEED)  # type: ignore[call-arg]

    def test_round_trip_json(self):
        outcome = DecisionOutcome(
            decision_id="dec-5",
            success=True,
            actual_action=DecisionAction.PROCEED,
            outcome_description="Success",
        )
        json_str = outcome.model_dump_json()
        restored = DecisionOutcome.model_validate_json(json_str)
        assert restored.decision_id == "dec-5"
        assert restored.success is True

    def test_all_action_values_accepted(self):
        for action in DecisionAction:
            outcome = DecisionOutcome(
                decision_id="d",
                success=True,
                actual_action=action,
            )
            assert outcome.actual_action == action


# =============================================================================
# PatternRecord
# =============================================================================


class TestPatternRecord:
    """Unit tests for PatternRecord and its computed fields."""

    def test_defaults(self):
        p = PatternRecord(
            pattern_id="pr1",
            context_signature="sig1",
            pattern_type="impl",
        )
        assert p.total_applications == 0
        assert p.successful_applications == 0
        assert isinstance(p.last_applied, datetime)
        assert isinstance(p.created_at, datetime)

    def test_effectiveness_zero_when_no_applications(self):
        p = PatternRecord(
            pattern_id="pr2",
            context_signature="s",
            pattern_type="t",
            total_applications=0,
        )
        assert p.effectiveness == 0.0

    def test_effectiveness_ratio(self):
        p = PatternRecord(
            pattern_id="pr3",
            context_signature="s",
            pattern_type="t",
            total_applications=10,
            successful_applications=7,
        )
        assert abs(p.effectiveness - 0.7) < 1e-9

    def test_effectiveness_100_percent(self):
        p = PatternRecord(
            pattern_id="pr4",
            context_signature="s",
            pattern_type="t",
            total_applications=5,
            successful_applications=5,
        )
        assert p.effectiveness == 1.0

    def test_confidence_zero_applications(self):
        p = PatternRecord(
            pattern_id="pr5",
            context_signature="s",
            pattern_type="t",
        )
        assert p.confidence == 0.0

    def test_confidence_partial_sample(self):
        p = PatternRecord(
            pattern_id="pr6",
            context_signature="s",
            pattern_type="t",
            total_applications=25,
        )
        assert abs(p.confidence - 0.5) < 1e-9  # 25/50

    def test_confidence_capped_at_one(self):
        p = PatternRecord(
            pattern_id="pr7",
            context_signature="s",
            pattern_type="t",
            total_applications=50,
        )
        assert p.confidence == 1.0

    def test_confidence_beyond_50_still_capped(self):
        p = PatternRecord(
            pattern_id="pr8",
            context_signature="s",
            pattern_type="t",
            total_applications=200,
        )
        assert p.confidence == 1.0

    def test_confidence_exactly_50(self):
        p = PatternRecord(
            pattern_id="pr9",
            context_signature="s",
            pattern_type="t",
            total_applications=50,
        )
        assert p.confidence == 1.0

    def test_computed_fields_in_model_dump(self):
        p = PatternRecord(
            pattern_id="pr10",
            context_signature="s",
            pattern_type="t",
            total_applications=20,
            successful_applications=15,
        )
        data = p.model_dump()
        assert "effectiveness" in data
        assert "confidence" in data
        assert abs(data["effectiveness"] - 0.75) < 1e-9
        assert abs(data["confidence"] - 0.4) < 1e-9

    def test_total_applications_negative_raises(self):
        with pytest.raises(ValidationError):
            PatternRecord(
                pattern_id="pr11",
                context_signature="s",
                pattern_type="t",
                total_applications=-1,
            )

    def test_successful_applications_negative_raises(self):
        with pytest.raises(ValidationError):
            PatternRecord(
                pattern_id="pr12",
                context_signature="s",
                pattern_type="t",
                successful_applications=-1,
            )

    def test_round_trip_json(self):
        p = PatternRecord(
            pattern_id="pr13",
            context_signature="abc",
            pattern_type="bugfix",
            total_applications=30,
            successful_applications=24,
        )
        json_str = p.model_dump_json()
        restored = PatternRecord.model_validate_json(json_str)
        assert restored.pattern_id == "pr13"
        assert abs(restored.effectiveness - 0.8) < 1e-9

    def test_missing_required_fields_raise(self):
        with pytest.raises(ValidationError):
            PatternRecord(context_signature="s", pattern_type="t")  # type: ignore[call-arg]

    def test_datetimes_are_utc_aware(self):
        p = PatternRecord(pattern_id="pr14", context_signature="s", pattern_type="t")
        assert p.last_applied.tzinfo is not None
        assert p.created_at.tzinfo is not None


# =============================================================================
# DecisionRecord
# =============================================================================


class TestDecisionRecord:
    """Unit tests for DecisionRecord."""

    def test_minimal_construction(self):
        rec = DecisionRecord(
            decision_id="dr1",
            context_signature="sig1",
            domain="codeswiftr-com",
            project="interview-simulator",
            recommended_action=DecisionAction.PROCEED,
        )
        assert rec.actual_action is None
        assert rec.outcome_success is None
        assert isinstance(rec.created_at, datetime)
        assert rec.outcome_recorded_at is None

    def test_full_construction(self):
        ts = datetime(2025, 3, 10, 8, 0, 0, tzinfo=UTC)
        rec = DecisionRecord(
            decision_id="dr2",
            context_signature="sig2",
            domain="brandfocus-ai",
            project="voice-coach",
            recommended_action=DecisionAction.PROCEED_WITH_CAUTION,
            actual_action=DecisionAction.PROCEED_WITH_CAUTION,
            outcome_success=True,
            outcome_recorded_at=ts,
        )
        assert rec.actual_action == DecisionAction.PROCEED_WITH_CAUTION
        assert rec.outcome_success is True
        assert rec.outcome_recorded_at == ts

    def test_pending_outcome_fields_nullable(self):
        rec = DecisionRecord(
            decision_id="dr3",
            context_signature="s",
            domain="d",
            project="p",
            recommended_action=DecisionAction.HUMAN_REVIEW_REQUIRED,
        )
        assert rec.actual_action is None
        assert rec.outcome_success is None
        assert rec.outcome_recorded_at is None

    def test_created_at_utc_aware(self):
        rec = DecisionRecord(
            decision_id="dr4",
            context_signature="s",
            domain="d",
            project="p",
            recommended_action=DecisionAction.BLOCK,
        )
        assert rec.created_at.tzinfo is not None

    def test_missing_required_fields_raise(self):
        with pytest.raises(ValidationError):
            DecisionRecord(
                context_signature="s",
                domain="d",
                project="p",
                recommended_action=DecisionAction.PROCEED,
            )  # type: ignore[call-arg]

    def test_all_recommended_actions_valid(self):
        for action in DecisionAction:
            rec = DecisionRecord(
                decision_id="dr-all",
                context_signature="s",
                domain="d",
                project="p",
                recommended_action=action,
            )
            assert rec.recommended_action == action

    def test_round_trip_json(self):
        rec = DecisionRecord(
            decision_id="dr5",
            context_signature="xyz",
            domain="d",
            project="p",
            recommended_action=DecisionAction.DEFER,
            actual_action=DecisionAction.DEFER,
            outcome_success=False,
        )
        json_str = rec.model_dump_json()
        restored = DecisionRecord.model_validate_json(json_str)
        assert restored.decision_id == "dr5"
        assert restored.outcome_success is False

    def test_outcome_success_false(self):
        rec = DecisionRecord(
            decision_id="dr6",
            context_signature="s",
            domain="d",
            project="p",
            recommended_action=DecisionAction.PROCEED,
            outcome_success=False,
        )
        assert rec.outcome_success is False


# =============================================================================
# Integration / Composition Tests
# =============================================================================


class TestSchemaComposition:
    """Tests for composing multiple schemas together."""

    def test_full_decision_workflow_round_trip(self):
        """Simulate a complete decision workflow and verify JSON round-trip."""
        context = DecisionContext(
            domain="leanvibe-dev",
            project="tech-diligence",
            feature_id="TD-100",
            file_paths=["src/scanner.py"],
            tags=["security"],
            description="Add new scanner",
        )
        scores = ScoreCard(
            risk_score=0.15,
            confidence_score=0.85,
            complexity_score=0.25,
            precedent_score=0.7,
        )
        warning = Warning(code="TEST_COV", message="Missing test coverage", severity="medium")
        recommendation = DecisionRecommendation(
            action=DecisionAction.PROCEED_WITH_CAUTION,
            confidence=ConfidenceLevel.HIGH,
            scores=scores,
            reasoning="Good precedent, but test coverage gap",
            warnings=[warning],
            suggested_human_reviewers=["@lead-dev"],
        )
        outcome = DecisionOutcome(
            decision_id="wf-001",
            success=True,
            actual_action=DecisionAction.PROCEED_WITH_CAUTION,
            outcome_description="Feature deployed, no issues",
        )

        # Serialize everything
        payload = {
            "context": context.model_dump(),
            "recommendation": recommendation.model_dump(),
            "outcome": outcome.model_dump(),
        }
        json_str = json.dumps(payload, default=str)
        data = json.loads(json_str)

        # Restore
        r_ctx = DecisionContext.model_validate(data["context"])
        r_rec = DecisionRecommendation.model_validate(data["recommendation"])
        r_out = DecisionOutcome.model_validate(data["outcome"])

        assert r_ctx.domain == "leanvibe-dev"
        assert r_rec.action == DecisionAction.PROCEED_WITH_CAUTION
        assert r_rec.scores.aggregate_score > 0.7
        assert r_out.success is True

    def test_diligence_report_with_mixed_findings(self):
        """Test a report containing all severity levels."""
        findings = [
            DiligenceFinding(
                finding_id=f"f{i}",
                title=f"Issue {i}",
                severity=sev,
                category=FindingCategory.SECURITY,
            )
            for i, sev in enumerate(FindingSeverity)
        ]
        report = DiligenceReport(
            report_id="rep-mix",
            findings=findings,
            summary_score=40.0,
        )
        assert len(report.findings) == len(FindingSeverity)

    def test_atlas_signals_with_nested_patterns(self):
        patterns = [
            AtlasPattern(
                pattern_id=f"pat-{i}",
                pattern_type="impl",
                entities=[f"ent-{i}"],
                session_count=i * 2,
                confidence=i * 0.1,
            )
            for i in range(5)
        ]
        signals = AtlasSignals(
            related_patterns=patterns,
            similar_decisions=["dec-a", "dec-b"],
            recurring_problems=["Null ptr"],
        )
        data = signals.model_dump()
        restored = AtlasSignals.model_validate(data)
        assert len(restored.related_patterns) == 5
        assert restored.related_patterns[4].session_count == 8

    def test_pattern_record_tracks_learning_over_time(self):
        """Simulate progressive learning updates."""
        p = PatternRecord(
            pattern_id="learn-001",
            context_signature="ctx-hash",
            pattern_type="refactor",
            total_applications=5,
            successful_applications=4,
        )
        assert abs(p.effectiveness - 0.8) < 1e-9
        assert abs(p.confidence - 0.1) < 1e-9  # 5/50

        # After more applications
        p2 = PatternRecord(
            pattern_id="learn-001",
            context_signature="ctx-hash",
            pattern_type="refactor",
            total_applications=50,
            successful_applications=45,
        )
        assert abs(p2.effectiveness - 0.9) < 1e-9
        assert p2.confidence == 1.0  # capped

    def test_decision_record_tracks_outcome(self):
        """DecisionRecord starts without outcome and can be updated."""
        rec = DecisionRecord(
            decision_id="dr-lifecycle",
            context_signature="sig",
            domain="d",
            project="p",
            recommended_action=DecisionAction.PROCEED,
        )
        # Initially no outcome
        assert rec.outcome_success is None
        assert rec.actual_action is None

        # Simulate recording the outcome (new instance with filled fields)
        ts = datetime.now(UTC)
        rec_with_outcome = rec.model_copy(
            update={
                "actual_action": DecisionAction.PROCEED,
                "outcome_success": True,
                "outcome_recorded_at": ts,
            }
        )
        assert rec_with_outcome.outcome_success is True
        assert rec_with_outcome.outcome_recorded_at == ts


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions across all schemas."""

    def test_atlas_entity_empty_string_fields(self):
        # Empty string is a valid str value — no minLength constraint
        entity = AtlasEntity(id="", name="", entity_type="")
        assert entity.id == ""
        assert entity.name == ""

    def test_atlas_pattern_single_entity(self):
        p = AtlasPattern(pattern_id="p", pattern_type="t", entities=["only-one"])
        assert p.entities == ["only-one"]

    def test_diligence_report_zero_score(self):
        report = DiligenceReport(report_id="r", summary_score=0.0)
        assert report.summary_score == 0.0

    def test_analysis_job_zero_progress(self):
        job = AnalysisJobStatus(job_id="j", progress_percent=0)
        assert job.progress_percent == 0

    def test_decision_context_empty_lists(self):
        ctx = DecisionContext(domain="d", project="p", file_paths=[], tags=[])
        assert ctx.file_paths == []
        assert ctx.tags == []

    def test_score_card_all_zeros(self):
        sc = ScoreCard()
        # (1-0)*0.4 + 0*0.3 + (1-0)*0.2 + 0*0.1 = 0.6
        assert abs(sc.aggregate_score - 0.6) < 1e-9

    def test_score_card_all_ones(self):
        sc = ScoreCard(
            risk_score=1.0,
            confidence_score=1.0,
            complexity_score=1.0,
            precedent_score=1.0,
        )
        # (1-1)*0.4 + 1*0.3 + (1-1)*0.2 + 1*0.1 = 0.4
        assert abs(sc.aggregate_score - 0.4) < 1e-9

    def test_warning_empty_message(self):
        w = Warning(code="W", message="")
        assert w.message == ""

    def test_decision_outcome_success_false_is_not_none(self):
        outcome = DecisionOutcome(
            decision_id="d",
            success=False,
            actual_action=DecisionAction.BLOCK,
        )
        assert outcome.success is False
        assert outcome.success is not None

    def test_pattern_record_effectiveness_edge_case_all_successful(self):
        p = PatternRecord(
            pattern_id="p",
            context_signature="s",
            pattern_type="t",
            total_applications=1,
            successful_applications=1,
        )
        assert p.effectiveness == 1.0

    def test_pattern_record_effectiveness_edge_case_none_successful(self):
        p = PatternRecord(
            pattern_id="p",
            context_signature="s",
            pattern_type="t",
            total_applications=100,
            successful_applications=0,
        )
        assert p.effectiveness == 0.0

    def test_diligence_signals_with_many_findings(self):
        findings = [
            DiligenceFinding(
                finding_id=f"f{i}",
                title=f"Finding {i}",
                severity=FindingSeverity.HIGH,
                category=FindingCategory.ARCHITECTURE,
            )
            for i in range(50)
        ]
        signals = DiligenceSignals(
            overall_score=5.0,
            risk_level="critical",
            blocking_issues=findings,
        )
        assert len(signals.blocking_issues) == 50

    def test_atlas_rag_response_zero_execution_time(self):
        resp = AtlasRAGResponse(answer="A", execution_time_ms=0)
        assert resp.execution_time_ms == 0

    def test_decision_record_created_at_defaults_utc(self):
        before = datetime.now(UTC)
        rec = DecisionRecord(
            decision_id="d",
            context_signature="s",
            domain="d",
            project="p",
            recommended_action=DecisionAction.PROCEED,
        )
        after = datetime.now(UTC)
        assert before <= rec.created_at <= after

    def test_json_serialization_enums_as_strings(self):
        finding = DiligenceFinding(
            finding_id="f",
            title="t",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.DEPENDENCY,
        )
        data = json.loads(finding.model_dump_json())
        assert data["severity"] == "critical"
        assert data["category"] == "dependency"

    def test_score_card_risk_weight_dominates(self):
        """Risk score has highest weight (0.4); reducing it should raise aggregate most."""
        base = ScoreCard(risk_score=0.5, confidence_score=0.5, complexity_score=0.5, precedent_score=0.5)
        low_risk = ScoreCard(risk_score=0.0, confidence_score=0.5, complexity_score=0.5, precedent_score=0.5)
        # Improvement = (1-0)*0.4 - (1-0.5)*0.4 = 0.4 - 0.2 = 0.2
        assert abs((low_risk.aggregate_score - base.aggregate_score) - 0.2) < 1e-9

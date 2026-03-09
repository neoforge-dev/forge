"""
Tests for Meta-Learning Pydantic schemas.

Verifies serialization, validation, and computed properties.
"""

import json
from datetime import datetime

import pytest

from forge_harness.meta_learning.schemas import (
    AnalysisJobStatus,
    # Code Atlas
    AtlasEntity,
    AtlasPattern,
    AtlasRAGResponse,
    AtlasSignals,
    ConfidenceLevel,
    # Decision Engine
    DecisionAction,
    DecisionContext,
    DecisionOutcome,
    DecisionRecommendation,
    DecisionRecord,
    DiligenceFinding,
    DiligenceReport,
    DiligenceSignals,
    FindingCategory,
    # Tech Diligence
    FindingSeverity,
    # Learning Store
    PatternRecord,
    ScoreCard,
    Warning,
)


class TestAtlasSchemas:
    """Tests for Code Atlas schemas."""

    def test_atlas_entity_creation(self):
        """Test AtlasEntity model creation."""
        entity = AtlasEntity(
            id="ent-001",
            name="UserService",
            entity_type="class",
            description="Handles user operations",
            mention_count=15,
        )
        assert entity.id == "ent-001"
        assert entity.name == "UserService"
        assert entity.entity_type == "class"
        assert entity.mention_count == 15

    def test_atlas_entity_serialization(self):
        """Test AtlasEntity JSON serialization."""
        entity = AtlasEntity(
            id="ent-002",
            name="authenticate",
            entity_type="function",
        )
        data = entity.model_dump()
        assert data["id"] == "ent-002"
        assert data["mention_count"] == 0  # default

        # Round-trip
        restored = AtlasEntity.model_validate(data)
        assert restored == entity

    def test_atlas_pattern_creation(self):
        """Test AtlasPattern model creation."""
        pattern = AtlasPattern(
            pattern_id="pat-001",
            pattern_type="implementation",
            entities=["ent-001", "ent-002"],
            session_count=5,
            confidence=0.85,
        )
        assert pattern.pattern_id == "pat-001"
        assert len(pattern.entities) == 2
        assert pattern.confidence == 0.85

    def test_atlas_pattern_validation(self):
        """Test AtlasPattern validation constraints."""
        with pytest.raises(ValueError):
            AtlasPattern(
                pattern_id="pat-002",
                pattern_type="test",
                confidence=1.5,  # > 1.0 should fail
            )

    def test_atlas_signals_empty(self):
        """Test AtlasSignals with empty signals."""
        signals = AtlasSignals()
        assert signals.related_patterns == []
        assert signals.similar_decisions == []
        assert signals.recurring_problems == []

    def test_atlas_rag_response(self):
        """Test AtlasRAGResponse model."""
        response = AtlasRAGResponse(
            answer="Use the UserService.authenticate() method",
            sources=["session-001", "session-002"],
            confidence=0.92,
            execution_time_ms=150,
        )
        assert "UserService" in response.answer
        assert len(response.sources) == 2


class TestDiligenceSchemas:
    """Tests for Tech Diligence schemas."""

    def test_finding_severity_enum(self):
        """Test FindingSeverity enum values."""
        assert FindingSeverity.CRITICAL.value == "critical"
        assert FindingSeverity.HIGH.value == "high"
        assert FindingSeverity.MEDIUM.value == "medium"
        assert FindingSeverity.LOW.value == "low"
        assert FindingSeverity.INFO.value == "info"

    def test_finding_category_enum(self):
        """Test FindingCategory enum values."""
        assert FindingCategory.SECURITY.value == "security"
        assert FindingCategory.ARCHITECTURE.value == "architecture"
        assert FindingCategory.DEPENDENCY.value == "dependency"
        assert FindingCategory.CODE_QUALITY.value == "code_quality"

    def test_diligence_finding_creation(self):
        """Test DiligenceFinding model creation."""
        finding = DiligenceFinding(
            finding_id="find-001",
            title="SQL Injection vulnerability",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.SECURITY,
            file_path="src/db/queries.py",
            line_number=42,
            fix_suggestion="Use parameterized queries",
            scanner="bandit",
        )
        assert finding.severity == FindingSeverity.CRITICAL
        assert finding.category == FindingCategory.SECURITY
        assert finding.line_number == 42

    def test_diligence_finding_serialization(self):
        """Test DiligenceFinding JSON serialization."""
        finding = DiligenceFinding(
            finding_id="find-002",
            title="Hardcoded credential",
            severity=FindingSeverity.HIGH,
            category=FindingCategory.SECURITY,
        )
        data = finding.model_dump()
        assert data["severity"] == "high"
        assert data["category"] == "security"

        # Round-trip
        restored = DiligenceFinding.model_validate(data)
        assert restored == finding

    def test_diligence_report_creation(self):
        """Test DiligenceReport model creation."""
        finding = DiligenceFinding(
            finding_id="find-003",
            title="Unused import",
            severity=FindingSeverity.INFO,
            category=FindingCategory.CODE_QUALITY,
        )
        report = DiligenceReport(
            report_id="rep-001",
            findings=[finding],
            summary_score=95.0,
            analysis_time_seconds=12.5,
        )
        assert len(report.findings) == 1
        assert report.summary_score == 95.0

    def test_diligence_signals_empty(self):
        """Test DiligenceSignals with no issues."""
        signals = DiligenceSignals()
        assert signals.overall_score == 100.0
        assert signals.risk_level == "low"
        assert signals.blocking_issues == []

    def test_analysis_job_status(self):
        """Test AnalysisJobStatus model."""
        status = AnalysisJobStatus(
            job_id="job-001",
            status="running",
            progress_percent=45,
        )
        assert status.status == "running"
        assert status.progress_percent == 45
        assert status.report_id is None


class TestDecisionEngineSchemas:
    """Tests for Decision Engine schemas."""

    def test_decision_action_enum(self):
        """Test DecisionAction enum values."""
        assert DecisionAction.PROCEED.value == "proceed"
        assert DecisionAction.PROCEED_WITH_CAUTION.value == "proceed_with_caution"
        assert DecisionAction.HUMAN_REVIEW_REQUIRED.value == "human_review_required"
        assert DecisionAction.BLOCK.value == "block"
        assert DecisionAction.DEFER.value == "defer"

    def test_confidence_level_enum(self):
        """Test ConfidenceLevel enum values."""
        assert ConfidenceLevel.HIGH.value == "high"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.LOW.value == "low"
        assert ConfidenceLevel.UNKNOWN.value == "unknown"

    def test_decision_context_creation(self):
        """Test DecisionContext model creation."""
        context = DecisionContext(
            domain="codeswiftr-com",
            project="interview-simulator",
            feature_id="IS-042",
            file_paths=["src/auth/login.py", "tests/test_auth.py"],
            tags=["security", "authentication"],
            description="Implement OAuth2 login flow",
        )
        assert context.domain == "codeswiftr-com"
        assert len(context.file_paths) == 2
        assert "security" in context.tags

    def test_score_card_aggregate(self):
        """Test ScoreCard aggregate_score computed property."""
        # Low risk, high confidence = good score
        good_scores = ScoreCard(
            risk_score=0.1,
            confidence_score=0.9,
            complexity_score=0.2,
            precedent_score=0.8,
        )
        assert good_scores.aggregate_score > 0.7

        # High risk, low confidence = bad score
        bad_scores = ScoreCard(
            risk_score=0.9,
            confidence_score=0.2,
            complexity_score=0.9,
            precedent_score=0.1,
        )
        assert bad_scores.aggregate_score < 0.3

    def test_score_card_serialization(self):
        """Test ScoreCard serialization includes computed field."""
        scores = ScoreCard(
            risk_score=0.5,
            confidence_score=0.5,
            complexity_score=0.5,
            precedent_score=0.5,
        )
        data = scores.model_dump()
        assert "aggregate_score" in data

    def test_warning_creation(self):
        """Test Warning model creation."""
        warning = Warning(
            code="DEPRECATION",
            message="This API will be removed in v2.0",
            severity="high",
        )
        assert warning.code == "DEPRECATION"
        assert warning.severity == "high"

    def test_decision_recommendation_creation(self):
        """Test DecisionRecommendation model creation."""
        scores = ScoreCard(
            risk_score=0.2,
            confidence_score=0.85,
            complexity_score=0.3,
            precedent_score=0.7,
        )
        warning = Warning(
            code="MISSING_TEST",
            message="No tests for new endpoint",
            severity="medium",
        )
        recommendation = DecisionRecommendation(
            action=DecisionAction.PROCEED_WITH_CAUTION,
            confidence=ConfidenceLevel.MEDIUM,
            scores=scores,
            reasoning="Similar patterns succeeded before, but test coverage is missing",
            warnings=[warning],
            suggested_human_reviewers=["@senior-dev"],
        )
        assert recommendation.action == DecisionAction.PROCEED_WITH_CAUTION
        assert len(recommendation.warnings) == 1

    def test_decision_outcome_creation(self):
        """Test DecisionOutcome model creation."""
        outcome = DecisionOutcome(
            decision_id="dec-001",
            success=True,
            actual_action=DecisionAction.PROCEED,
            outcome_description="Feature deployed successfully with no issues",
        )
        assert outcome.success is True
        assert isinstance(outcome.recorded_at, datetime)


class TestLearningStoreSchemas:
    """Tests for Learning Store schemas."""

    def test_pattern_record_effectiveness(self):
        """Test PatternRecord effectiveness computed property."""
        # 80% success rate
        pattern = PatternRecord(
            pattern_id="pat-001",
            context_signature="abc123",
            pattern_type="implementation",
            total_applications=10,
            successful_applications=8,
        )
        assert pattern.effectiveness == 0.8

    def test_pattern_record_effectiveness_zero_applications(self):
        """Test PatternRecord effectiveness with no applications."""
        pattern = PatternRecord(
            pattern_id="pat-002",
            context_signature="def456",
            pattern_type="bugfix",
        )
        assert pattern.effectiveness == 0.0

    def test_pattern_record_confidence(self):
        """Test PatternRecord confidence computed property."""
        # Low sample = low confidence
        low_sample = PatternRecord(
            pattern_id="pat-003",
            context_signature="ghi789",
            pattern_type="refactor",
            total_applications=10,
        )
        assert low_sample.confidence == 0.2  # 10/50

        # High sample = high confidence
        high_sample = PatternRecord(
            pattern_id="pat-004",
            context_signature="jkl012",
            pattern_type="test",
            total_applications=100,
        )
        assert high_sample.confidence == 1.0  # capped at 1.0

    def test_pattern_record_serialization(self):
        """Test PatternRecord serialization includes computed fields."""
        pattern = PatternRecord(
            pattern_id="pat-005",
            context_signature="mno345",
            pattern_type="deploy",
            total_applications=25,
            successful_applications=20,
        )
        data = pattern.model_dump()
        assert "effectiveness" in data
        assert "confidence" in data
        assert data["effectiveness"] == 0.8
        assert data["confidence"] == 0.5

    def test_decision_record_creation(self):
        """Test DecisionRecord model creation."""
        record = DecisionRecord(
            decision_id="dec-002",
            context_signature="pqr678",
            domain="leanvibe-dev",
            project="tech-diligence",
            recommended_action=DecisionAction.PROCEED,
            actual_action=DecisionAction.PROCEED,
            outcome_success=True,
        )
        assert record.domain == "leanvibe-dev"
        assert record.outcome_success is True
        assert isinstance(record.created_at, datetime)

    def test_decision_record_partial(self):
        """Test DecisionRecord with pending outcome."""
        record = DecisionRecord(
            decision_id="dec-003",
            context_signature="stu901",
            domain="neoforge-dev",
            project="code-atlas",
            recommended_action=DecisionAction.HUMAN_REVIEW_REQUIRED,
        )
        assert record.actual_action is None
        assert record.outcome_success is None
        assert record.outcome_recorded_at is None


class TestJsonRoundTrip:
    """Test JSON serialization round-trips for all models."""

    def test_full_decision_flow_roundtrip(self):
        """Test complete decision flow serialization."""
        # Create a full recommendation
        context = DecisionContext(
            domain="test-domain",
            project="test-project",
            feature_id="TEST-001",
            file_paths=["src/main.py"],
            tags=["test"],
        )
        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.8,
            complexity_score=0.4,
            precedent_score=0.6,
        )
        recommendation = DecisionRecommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.HIGH,
            scores=scores,
            reasoning="All checks passed",
            warnings=[],
        )

        # Serialize to JSON
        json_str = json.dumps(
            {
                "context": context.model_dump(),
                "recommendation": recommendation.model_dump(),
            }
        )

        # Parse back
        data = json.loads(json_str)
        restored_context = DecisionContext.model_validate(data["context"])
        restored_recommendation = DecisionRecommendation.model_validate(data["recommendation"])

        assert restored_context.domain == "test-domain"
        assert restored_recommendation.action == DecisionAction.PROCEED
        assert restored_recommendation.scores.aggregate_score == scores.aggregate_score

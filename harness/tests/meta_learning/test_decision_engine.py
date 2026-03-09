"""
Tests for the Meta-Learning Decision Engine.

Tests score calculation, recommendation generation, and outcome recording.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.meta_learning.config import DecisionEngineConfig
from forge_harness.meta_learning.decision_engine import DecisionEngine
from forge_harness.meta_learning.learning_store import LearningStore
from forge_harness.meta_learning.schemas import (
    AtlasPattern,
    AtlasSignals,
    ConfidenceLevel,
    DecisionAction,
    DecisionContext,
    DecisionTier,
    DiligenceFinding,
    DiligenceSignals,
    FindingCategory,
    FindingSeverity,
    ScoreCard,
)


@pytest.fixture
def temp_storage():
    """Create a temporary directory for storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def learning_store(temp_storage):
    """Create a LearningStore instance."""
    return LearningStore(storage_path=temp_storage)


@pytest.fixture
def engine(learning_store):
    """Create a DecisionEngine with learning store."""
    return DecisionEngine(
        learning_store=learning_store,
        config=DecisionEngineConfig(),
    )


@pytest.fixture
def context():
    """Create a sample DecisionContext."""
    return DecisionContext(
        domain="test-domain",
        project="test-project",
        feature_id="TEST-001",
        file_paths=["src/main.py"],
        tags=["feature"],
        description="Test feature implementation",
    )


class TestDecisionEngineCreation:
    """Tests for DecisionEngine creation."""

    def test_basic_creation(self):
        """Test basic engine creation."""
        engine = DecisionEngine()
        assert engine.atlas_bridge is None
        assert engine.diligence_bridge is None
        assert engine.learning_store is None

    def test_creation_with_all_components(self, learning_store):
        """Test creation with all components."""
        atlas_mock = MagicMock()
        diligence_mock = MagicMock()

        engine = DecisionEngine(
            atlas_bridge=atlas_mock,
            diligence_bridge=diligence_mock,
            learning_store=learning_store,
        )

        assert engine.atlas_bridge is atlas_mock
        assert engine.diligence_bridge is diligence_mock
        assert engine.learning_store is learning_store


class TestScoreCalculation:
    """Tests for score calculation."""

    def test_risk_score_no_issues(self, engine, context):
        """Test risk score with no issues."""
        atlas = AtlasSignals()
        diligence = DiligenceSignals(risk_level="low")

        score = engine._calculate_risk_score(atlas, diligence, context)
        assert score < 0.2  # Low risk

    def test_risk_score_critical_diligence(self, engine, context):
        """Test risk score with critical diligence finding."""
        atlas = AtlasSignals()
        diligence = DiligenceSignals(risk_level="critical")

        score = engine._calculate_risk_score(atlas, diligence, context)
        assert score >= 0.6  # High risk

    def test_risk_score_high_diligence(self, engine, context):
        """Test risk score with high diligence finding."""
        atlas = AtlasSignals()
        diligence = DiligenceSignals(risk_level="high")

        score = engine._calculate_risk_score(atlas, diligence, context)
        assert 0.4 <= score < 0.7

    def test_risk_score_sensitive_tags(self, engine):
        """Test risk score increases with sensitive tags."""
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["auth.py"],
            tags=["security", "auth", "credential"],
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals(risk_level="low")

        score = engine._calculate_risk_score(atlas, diligence, context)
        assert score >= 0.2  # Sensitive tags add risk

    def test_risk_score_recurring_problems(self, engine, context):
        """Test risk score with recurring problems."""
        atlas = AtlasSignals(recurring_problems=["issue-1", "issue-2", "issue-3"])
        diligence = DiligenceSignals(risk_level="low")

        score = engine._calculate_risk_score(atlas, diligence, context)
        assert score > 0  # Problems add risk

    def test_confidence_score_base(self, engine, context):
        """Test base confidence score."""
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, context)
        assert score >= 0.3  # Base confidence

    def test_confidence_score_with_patterns(self, engine, context):
        """Test confidence increases with patterns."""
        atlas = AtlasSignals(
            related_patterns=[
                AtlasPattern(
                    pattern_id="p1",
                    pattern_type="impl",
                    confidence=0.9,
                ),
                AtlasPattern(
                    pattern_id="p2",
                    pattern_type="impl",
                    confidence=0.8,
                ),
            ]
        )
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, context)
        assert score > 0.5  # Patterns increase confidence

    def test_confidence_score_with_similar_decisions(self, engine, context):
        """Test confidence increases with similar decisions."""
        atlas = AtlasSignals(similar_decisions=["d1", "d2", "d3"])
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, context)
        assert score > 0.3  # Similar decisions increase confidence

    def test_complexity_score_single_file(self, engine):
        """Test complexity score with single file."""
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["main.py"],
            tags=[],
        )
        atlas = AtlasSignals(
            related_patterns=[AtlasPattern(pattern_id="p1", pattern_type="impl", confidence=0.8)]
        )

        score = engine._calculate_complexity_score(context, atlas)
        assert score < 0.3  # Low complexity

    def test_complexity_score_many_files(self, engine):
        """Test complexity score with many files."""
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["a.py", "b.py", "c.py", "d.py", "e.py"],
            tags=[],
        )
        atlas = AtlasSignals()

        score = engine._calculate_complexity_score(context, atlas)
        assert score >= 0.4  # More files = more complex

    def test_complexity_score_complex_tags(self, engine):
        """Test complexity score with complex tags."""
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["main.py"],
            tags=["refactor", "migration", "breaking-change"],
        )
        atlas = AtlasSignals()

        score = engine._calculate_complexity_score(context, atlas)
        assert score >= 0.4  # Complex tags increase complexity

    def test_precedent_score_no_precedent(self, engine, context):
        """Test precedent score with no precedent."""
        atlas = AtlasSignals()

        score = engine._calculate_precedent_score(atlas, context)
        assert score == 0.0

    def test_precedent_score_with_patterns(self, engine, context):
        """Test precedent score with patterns."""
        atlas = AtlasSignals(
            related_patterns=[
                AtlasPattern(pattern_id="p1", pattern_type="impl", confidence=0.8),
                AtlasPattern(pattern_id="p2", pattern_type="impl", confidence=0.6),
            ]
        )

        score = engine._calculate_precedent_score(atlas, context)
        assert score > 0


class TestRecommendationGeneration:
    """Tests for recommendation generation."""

    @pytest.mark.asyncio
    async def test_proceed_recommendation(self, engine, context):
        """Test PROCEED recommendation for safe context."""
        # Engine has no bridges, so will get empty signals (low risk)
        recommendation = await engine.get_recommendation(context)

        # With no issues, should proceed
        assert recommendation.action in (
            DecisionAction.PROCEED,
            DecisionAction.PROCEED_WITH_CAUTION,
        )

    @pytest.mark.asyncio
    async def test_block_on_critical_diligence(self, learning_store, context):
        """Test BLOCK recommendation on critical diligence."""
        # Create mock diligence bridge
        diligence_mock = AsyncMock()
        diligence_mock.get_signals = AsyncMock(
            return_value=DiligenceSignals(
                risk_level="critical",
                blocking_issues=[
                    DiligenceFinding(
                        finding_id="f1",
                        title="Critical SQL Injection",
                        severity=FindingSeverity.CRITICAL,
                        category=FindingCategory.SECURITY,
                    )
                ],
            )
        )

        engine = DecisionEngine(
            diligence_bridge=diligence_mock,
            learning_store=learning_store,
        )

        recommendation = await engine.get_recommendation(context)

        assert recommendation.action == DecisionAction.BLOCK

    @pytest.mark.asyncio
    async def test_human_review_on_blocking_issues(self, learning_store, context):
        """Test HUMAN_REVIEW_REQUIRED on blocking issues."""
        diligence_mock = AsyncMock()
        diligence_mock.get_signals = AsyncMock(
            return_value=DiligenceSignals(
                risk_level="high",
                blocking_issues=[
                    DiligenceFinding(
                        finding_id="f1",
                        title="High severity issue",
                        severity=FindingSeverity.HIGH,
                        category=FindingCategory.SECURITY,
                    )
                ],
            )
        )

        engine = DecisionEngine(
            diligence_bridge=diligence_mock,
            learning_store=learning_store,
        )

        recommendation = await engine.get_recommendation(context)

        assert recommendation.action == DecisionAction.HUMAN_REVIEW_REQUIRED

    @pytest.mark.asyncio
    async def test_warnings_generated(self, learning_store, context):
        """Test warnings are generated appropriately."""
        diligence_mock = AsyncMock()
        diligence_mock.get_signals = AsyncMock(
            return_value=DiligenceSignals(
                risk_level="medium",
                warnings=[
                    DiligenceFinding(
                        finding_id="f1",
                        title="Minor code style issue",
                        severity=FindingSeverity.LOW,
                        category=FindingCategory.CODE_QUALITY,
                    )
                ],
            )
        )

        engine = DecisionEngine(
            diligence_bridge=diligence_mock,
            learning_store=learning_store,
        )

        recommendation = await engine.get_recommendation(context)

        # Should have at least one warning
        assert len(recommendation.warnings) >= 1

    @pytest.mark.asyncio
    async def test_reasoning_provided(self, engine, context):
        """Test reasoning is provided."""
        recommendation = await engine.get_recommendation(context)

        assert recommendation.reasoning
        assert "Scores:" in recommendation.reasoning

    @pytest.mark.asyncio
    async def test_confidence_level_set(self, engine, context):
        """Test confidence level is set."""
        recommendation = await engine.get_recommendation(context)

        assert recommendation.confidence in list(ConfidenceLevel)


class TestDecisionRecording:
    """Tests for decision recording and outcome tracking."""

    @pytest.mark.asyncio
    async def test_decision_recorded(self, engine, context):
        """Test decision is recorded in learning store."""
        await engine.get_recommendation(context)

        stats = engine.learning_store.get_statistics()
        assert stats["total_decisions"] >= 1

    @pytest.mark.asyncio
    async def test_outcome_recording(self, engine, context):
        """Test outcome recording."""
        # Get a recommendation
        await engine.get_recommendation(context)

        # Get the decision ID from the cache
        assert len(engine._decision_cache) >= 1
        decision_id = list(engine._decision_cache.keys())[0]

        # Record outcome
        outcome = await engine.record_outcome(
            decision_id=decision_id,
            success=True,
            actual_action=DecisionAction.PROCEED,
        )

        assert outcome is not None
        assert outcome.success is True

    @pytest.mark.asyncio
    async def test_outcome_recording_nonexistent(self, engine):
        """Test outcome recording for nonexistent decision."""
        outcome = await engine.record_outcome(
            decision_id="nonexistent-id",
            success=True,
        )

        assert outcome is None


class TestStatistics:
    """Tests for engine statistics."""

    @pytest.mark.asyncio
    async def test_statistics_basic(self, engine, context):
        """Test basic statistics."""
        stats = engine.get_statistics()

        assert "has_atlas_bridge" in stats
        assert "has_diligence_bridge" in stats
        assert "has_learning_store" in stats

    @pytest.mark.asyncio
    async def test_statistics_after_decisions(self, engine, context):
        """Test statistics after making decisions."""
        # Make a few decisions
        await engine.get_recommendation(context)
        await engine.get_recommendation(context)

        stats = engine.get_statistics()

        assert stats["total_decisions_recorded"] >= 2
        assert stats["cached_decisions"] >= 2


class TestGracefulDegradation:
    """Tests for graceful degradation without bridges."""

    @pytest.mark.asyncio
    async def test_works_without_atlas(self, learning_store, context):
        """Test engine works without Code Atlas bridge."""
        engine = DecisionEngine(learning_store=learning_store)

        # Should not raise
        recommendation = await engine.get_recommendation(context)
        assert recommendation.action is not None

    @pytest.mark.asyncio
    async def test_works_without_diligence(self, learning_store, context):
        """Test engine works without Tech Diligence bridge."""
        engine = DecisionEngine(learning_store=learning_store)

        recommendation = await engine.get_recommendation(context)
        assert recommendation.action is not None

    @pytest.mark.asyncio
    async def test_works_without_learning_store(self, context):
        """Test engine works without learning store."""
        engine = DecisionEngine()

        recommendation = await engine.get_recommendation(context)
        assert recommendation.action is not None

    @pytest.mark.asyncio
    async def test_works_completely_standalone(self, context):
        """Test engine works with no dependencies."""
        engine = DecisionEngine()

        recommendation = await engine.get_recommendation(context)

        # Should still provide a reasonable recommendation
        assert recommendation.action is not None
        assert recommendation.reasoning is not None
        assert recommendation.scores is not None


class TestDecisionTierClassification:
    """Tests for decision tier classification (WATCH/PHONE/DESKTOP)."""

    @pytest.fixture
    def engine(self):
        return DecisionEngine()

    @pytest.fixture
    def base_scores(self):
        return ScoreCard(
            risk_score=0.1,
            confidence_score=0.8,
            complexity_score=0.2,
            precedent_score=0.6,
        )

    @pytest.fixture
    def base_context(self):
        return DecisionContext(
            domain="test-domain",
            project="test-project",
            tags=["test"],
        )

    @pytest.fixture
    def base_diligence(self):
        return DiligenceSignals(risk_level="low")

    def test_desktop_on_block_action(self, engine, base_scores, base_context, base_diligence):
        """BLOCK action always requires DESKTOP tier."""
        tier = engine.classify_tier(
            action=DecisionAction.BLOCK,
            scores=base_scores,
            context=base_context,
            diligence=base_diligence,
        )
        assert tier == DecisionTier.DESKTOP

    def test_desktop_on_critical_diligence(self, engine, base_scores, base_context):
        """Critical diligence risk level requires DESKTOP tier."""

        critical_diligence = DiligenceSignals(risk_level="critical")

        tier = engine.classify_tier(
            action=DecisionAction.PROCEED,
            scores=base_scores,
            context=base_context,
            diligence=critical_diligence,
        )
        assert tier == DecisionTier.DESKTOP

    def test_desktop_on_security_tags(self, engine, base_scores, base_diligence):
        """Security-sensitive tags require DESKTOP tier."""

        security_context = DecisionContext(
            domain="test",
            project="test",
            tags=["security", "auth"],
        )

        tier = engine.classify_tier(
            action=DecisionAction.PROCEED,
            scores=base_scores,
            context=security_context,
            diligence=base_diligence,
        )
        assert tier == DecisionTier.DESKTOP

    def test_desktop_on_high_risk_score(self, engine, base_context, base_diligence):
        """High risk score requires DESKTOP tier."""

        high_risk_scores = ScoreCard(
            risk_score=0.7,  # > 0.6 threshold
            confidence_score=0.8,
            complexity_score=0.2,
            precedent_score=0.6,
        )

        tier = engine.classify_tier(
            action=DecisionAction.PROCEED,
            scores=high_risk_scores,
            context=base_context,
            diligence=base_diligence,
        )
        assert tier == DecisionTier.DESKTOP

    def test_watch_on_low_risk_high_confidence_proceed(self, engine, base_context, base_diligence):
        """PROCEED with high confidence and low risk -> WATCH tier."""

        watch_eligible_scores = ScoreCard(
            risk_score=0.1,  # < 0.2 threshold
            confidence_score=0.8,  # > 0.7 threshold
            complexity_score=0.2,
            precedent_score=0.6,
        )

        tier = engine.classify_tier(
            action=DecisionAction.PROCEED,
            scores=watch_eligible_scores,
            context=base_context,
            diligence=base_diligence,
        )
        assert tier == DecisionTier.WATCH

    def test_watch_on_simple_tags_with_precedent(self, engine, base_diligence):
        """Simple tags (test, lint) with precedent -> WATCH tier."""

        simple_context = DecisionContext(
            domain="test",
            project="test",
            tags=["test", "retry"],
        )
        watch_scores = ScoreCard(
            risk_score=0.2,  # < 0.3
            confidence_score=0.6,
            complexity_score=0.2,
            precedent_score=0.6,  # > 0.5
        )

        tier = engine.classify_tier(
            action=DecisionAction.PROCEED_WITH_CAUTION,
            scores=watch_scores,
            context=simple_context,
            diligence=base_diligence,
        )
        assert tier == DecisionTier.WATCH

    def test_phone_as_default_middle_ground(self, engine, base_diligence):
        """Middle-ground decisions default to PHONE tier."""

        middle_context = DecisionContext(
            domain="test",
            project="test",
            tags=["feature"],  # Not in watch-eligible or desktop-required
        )
        middle_scores = ScoreCard(
            risk_score=0.4,  # Between thresholds
            confidence_score=0.5,
            complexity_score=0.4,
            precedent_score=0.3,
        )

        tier = engine.classify_tier(
            action=DecisionAction.HUMAN_REVIEW_REQUIRED,
            scores=middle_scores,
            context=middle_context,
            diligence=base_diligence,
        )
        assert tier == DecisionTier.PHONE

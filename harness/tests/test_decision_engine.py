"""
Comprehensive unit tests for DecisionEngine.

Tests tier classification, risk scoring, recommendation logic,
edge cases, graceful degradation, and feedback loops.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.meta_learning.config import DecisionEngineConfig
from forge_harness.meta_learning.decision_engine import DecisionEngine
from forge_harness.meta_learning.schemas import (
    AtlasEntity,
    AtlasPattern,
    AtlasSignals,
    ConfidenceLevel,
    DecisionAction,
    DecisionContext,
    DecisionOutcome,
    DecisionRecommendation,
    DecisionRecord,
    DecisionTier,
    DiligenceFinding,
    DiligenceSignals,
    FindingCategory,
    FindingSeverity,
    PatternRecord,
    ScoreCard,
    Warning,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_atlas_bridge():
    """Mock CodeAtlasBridge."""
    bridge = AsyncMock()
    return bridge


@pytest.fixture
def mock_diligence_bridge():
    """Mock TechDiligenceBridge."""
    bridge = AsyncMock()
    return bridge


@pytest.fixture
def mock_learning_store():
    """Mock LearningStore."""
    store = MagicMock()
    store.compute_context_signature = MagicMock(return_value="test-signature")
    store.get_decisions_by_context = MagicMock(return_value=[])
    store.get_patterns_by_context = MagicMock(return_value=[])
    store.record_decision = MagicMock()
    store.get_decision = MagicMock(return_value=None)
    store.record_outcome = MagicMock(return_value=True)
    store.get_statistics = MagicMock(return_value={
        "total_decisions": 100,
        "decision_success_rate": 0.85,
    })
    return store


@pytest.fixture
def mock_tracker():
    """Mock HarnessTracker."""
    tracker = MagicMock()
    return tracker


@pytest.fixture
def basic_context():
    """Basic decision context."""
    return DecisionContext(
        domain="codeswiftr-com",
        project="interview-simulator",
        feature_id="IS-042",
        file_paths=["src/auth.py"],
        tags=["security"],
    )


@pytest.fixture
def empty_atlas_signals():
    """Empty Atlas signals."""
    return AtlasSignals()


@pytest.fixture
def empty_diligence_signals():
    """Empty Diligence signals."""
    return DiligenceSignals()


@pytest.fixture
def sample_atlas_pattern():
    """Sample Atlas pattern."""
    return AtlasPattern(
        pattern_id="pattern-001",
        pattern_type="implementation",
        confidence=0.8,
    )


@pytest.fixture
def sample_pattern_record():
    """Sample pattern record from learning store."""
    return PatternRecord(
        pattern_id="pattern-001",
        context_signature="test-signature",
        pattern_type="implementation",
        total_applications=10,
        successful_applications=8,
    )


# =============================================================================
# Test DecisionEngine Initialization
# =============================================================================


class TestDecisionEngineInit:
    """Tests for DecisionEngine initialization."""

    def test_init_with_all_bridges(self, mock_atlas_bridge, mock_diligence_bridge, mock_learning_store):
        """Test initialization with all bridges provided."""
        config = DecisionEngineConfig()

        engine = DecisionEngine(
            atlas_bridge=mock_atlas_bridge,
            diligence_bridge=mock_diligence_bridge,
            learning_store=mock_learning_store,
            config=config,
        )

        assert engine.atlas_bridge == mock_atlas_bridge
        assert engine.diligence_bridge == mock_diligence_bridge
        assert engine.learning_store == mock_learning_store
        assert engine.config == config
        assert engine.tracker is None
        assert engine._decision_cache == {}

    def test_init_with_defaults(self):
        """Test initialization with default values (no bridges)."""
        engine = DecisionEngine()

        assert engine.atlas_bridge is None
        assert engine.diligence_bridge is None
        assert engine.learning_store is None
        assert engine.config is not None
        assert isinstance(engine.config, DecisionEngineConfig)
        assert engine._decision_cache == {}

    def test_init_with_tracker(self, mock_tracker):
        """Test initialization with tracker."""
        engine = DecisionEngine(tracker=mock_tracker)

        assert engine.tracker == mock_tracker

    def test_from_config(self, mock_atlas_bridge, mock_diligence_bridge, mock_learning_store):
        """Test creating engine from MetaLearningConfig."""
        from forge_harness.meta_learning.config import MetaLearningConfig

        meta_config = MetaLearningConfig()
        engine = DecisionEngine.from_config(
            meta_config,
            atlas_bridge=mock_atlas_bridge,
            diligence_bridge=mock_diligence_bridge,
            learning_store=mock_learning_store,
        )

        assert engine.atlas_bridge == mock_atlas_bridge
        assert engine.diligence_bridge == mock_diligence_bridge
        assert engine.learning_store == mock_learning_store
        assert engine.config == meta_config.decision_engine


# =============================================================================
# Test Signal Gathering
# =============================================================================


class TestSignalGathering:
    """Tests for signal gathering with graceful degradation."""

    @pytest.mark.asyncio
    async def test_get_atlas_signals_with_bridge(self, mock_atlas_bridge, basic_context):
        """Test getting Atlas signals when bridge is available."""
        mock_atlas_bridge.get_signals = AsyncMock(return_value=AtlasSignals(
            related_patterns=[AtlasPattern(pattern_id="p1", pattern_type="test", confidence=0.9)],
        ))

        engine = DecisionEngine(atlas_bridge=mock_atlas_bridge)
        signals = await engine._get_atlas_signals(basic_context)

        mock_atlas_bridge.get_signals.assert_called_once_with(
            domain="codeswiftr-com",
            project="interview-simulator",
            file_paths=["src/auth.py"],
            tags=["security"],
        )
        assert signals.related_patterns is not None

    @pytest.mark.asyncio
    async def test_get_atlas_signals_without_bridge(self, basic_context):
        """Test getting Atlas signals when bridge is None (graceful degradation)."""
        engine = DecisionEngine(atlas_bridge=None)
        signals = await engine._get_atlas_signals(basic_context)

        assert isinstance(signals, AtlasSignals)
        assert signals.related_patterns == []

    @pytest.mark.asyncio
    async def test_get_diligence_signals_with_bridge(self, mock_diligence_bridge, basic_context):
        """Test getting Diligence signals when bridge is available."""
        mock_diligence_bridge.get_signals = AsyncMock(return_value=DiligenceSignals(
            risk_level="low",
        ))

        engine = DecisionEngine(diligence_bridge=mock_diligence_bridge)
        signals = await engine._get_diligence_signals(basic_context)

        mock_diligence_bridge.get_signals.assert_called_once_with(
            domain="codeswiftr-com",
            project="interview-simulator",
        )
        assert signals.risk_level == "low"

    @pytest.mark.asyncio
    async def test_get_diligence_signals_without_bridge(self, basic_context):
        """Test getting Diligence signals when bridge is None (graceful degradation)."""
        engine = DecisionEngine(diligence_bridge=None)
        signals = await engine._get_diligence_signals(basic_context)

        assert isinstance(signals, DiligenceSignals)
        # Empty signals have default risk_level of "low"
        assert signals.risk_level == "low"


# =============================================================================
# Test Risk Score Calculation
# =============================================================================


class TestRiskScoreCalculation:
    """Tests for risk score calculation."""

    def test_risk_score_critical_diligence(self, basic_context):
        """Test critical risk level adds 0.6 to risk score."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(risk_level="critical")
        atlas = AtlasSignals()

        score = engine._calculate_risk_score(atlas, diligence, basic_context)

        assert score >= 0.6
        assert score <= 1.0

    def test_risk_score_high_diligence(self, basic_context):
        """Test high risk level adds 0.4 to risk score."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(risk_level="high")
        atlas = AtlasSignals()

        score = engine._calculate_risk_score(atlas, diligence, basic_context)

        assert score >= 0.4

    def test_risk_score_medium_diligence(self, basic_context):
        """Test medium risk level adds 0.2 to risk score."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(risk_level="medium")
        atlas = AtlasSignals()

        score = engine._calculate_risk_score(atlas, diligence, basic_context)

        assert score >= 0.2

    def test_risk_score_low_diligence(self, basic_context):
        """Test low risk level adds no diligence-based risk."""
        engine = DecisionEngine()

        # Use a clean context with no sensitive tags or files
        clean_context = DecisionContext(
            domain="test",
            project="test",
            file_paths=[],
            tags=[],
        )

        diligence = DiligenceSignals(risk_level="low")
        atlas = AtlasSignals()

        score = engine._calculate_risk_score(atlas, diligence, clean_context)

        assert score == 0.0

    def test_risk_score_blocking_issues(self, basic_context):
        """Test blocking issues increase risk (max 0.3)."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(
            risk_level="low",
            blocking_issues=[
                DiligenceFinding(
                    finding_id="issue1",
                    title="Issue 1",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue2",
                    title="Issue 2",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue3",
                    title="Issue 3",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue4",
                    title="Issue 4",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
            ],
        )
        atlas = AtlasSignals()

        score = engine._calculate_risk_score(atlas, diligence, basic_context)

        # 4 issues * 0.1 = 0.4, but capped at 0.3
        blocking_risk = min(0.3, len(diligence.blocking_issues) * 0.1)
        assert score >= blocking_risk

    def test_risk_score_recurring_problems(self, basic_context):
        """Test recurring problems increase risk (max 0.2)."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(risk_level="low")
        atlas = AtlasSignals(
            recurring_problems=["problem1", "problem2", "problem3", "problem4", "problem5"],
        )

        score = engine._calculate_risk_score(atlas, diligence, basic_context)

        # 5 problems * 0.05 = 0.25, but capped at 0.2
        recurring_risk = min(0.2, len(atlas.recurring_problems) * 0.05)
        assert score >= recurring_risk

    def test_risk_score_sensitive_tags(self, basic_context):
        """Test sensitive tags increase risk (max 0.2)."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(risk_level="low")
        atlas = AtlasSignals()

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["security", "auth", "payment", "pii"],  # 4 sensitive tags
        )

        score = engine._calculate_risk_score(atlas, diligence, context)

        # 4 tags * 0.1 = 0.4, but capped at 0.2
        tag_risk = min(0.2, 4 * 0.1)
        assert score >= tag_risk

    def test_risk_score_max_capped(self, basic_context):
        """Test risk score is capped at 1.0."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(
            risk_level="critical",
            blocking_issues=[
                DiligenceFinding(
                    finding_id="issue1",
                    title="Issue 1",
                    severity=FindingSeverity.CRITICAL,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue2",
                    title="Issue 2",
                    severity=FindingSeverity.CRITICAL,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue3",
                    title="Issue 3",
                    severity=FindingSeverity.CRITICAL,
                    category=FindingCategory.SECURITY,
                ),
            ],
        )
        atlas = AtlasSignals(
            recurring_problems=["problem1", "problem2", "problem3", "problem4"],
        )

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["security", "auth", "payment", "pii", "credential"],
        )

        score = engine._calculate_risk_score(atlas, diligence, context)

        # All factors combined should cap at 1.0
        assert score == 1.0

    def test_risk_score_zero_for_clean_signals(self, basic_context):
        """Test risk score is 0.0 for clean signals."""
        engine = DecisionEngine()

        diligence = DiligenceSignals(risk_level="low")
        atlas = AtlasSignals()

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["docs"],  # Not sensitive
        )

        score = engine._calculate_risk_score(atlas, diligence, context)

        assert score == 0.0


# =============================================================================
# Test Confidence Score Calculation
# =============================================================================


class TestConfidenceScoreCalculation:
    """Tests for confidence score calculation."""

    def test_confidence_score_base(self, basic_context):
        """Test base confidence is 0.3."""
        engine = DecisionEngine()

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        assert score >= 0.3

    def test_confidence_score_related_patterns(self, basic_context, sample_atlas_pattern):
        """Test related patterns increase confidence."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            related_patterns=[
                sample_atlas_pattern,
                AtlasPattern(pattern_id="p2", pattern_type="test", confidence=0.7),
            ],
        )
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # Base 0.3 + avg confidence (0.75) * 0.3 = 0.3 + 0.225 = 0.525
        expected_min = 0.3 + 0.75 * 0.3
        assert score >= expected_min

    def test_confidence_score_similar_decisions(self, basic_context):
        """Test similar decisions increase confidence (max 0.3)."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            similar_decisions=["decision1", "decision2", "decision3", "decision4"],
        )
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # 4 * 0.1 = 0.4, capped at 0.3
        similar_boost = min(0.3, 4 * 0.1)
        assert score >= 0.3 + similar_boost

    def test_confidence_score_past_decision_success_rate(self, basic_context, mock_learning_store):
        """Test past decision success rate affects confidence."""
        # Create past decisions with 100% success rate
        past_decisions = [
            DecisionRecord(
                decision_id="d1",
                context_signature="sig",
                domain="test",
                project="test",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            ),
            DecisionRecord(
                decision_id="d2",
                context_signature="sig",
                domain="test",
                project="test",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            ),
        ]

        mock_learning_store.get_decisions_by_context = MagicMock(return_value=past_decisions)
        engine = DecisionEngine(learning_store=mock_learning_store)

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # 100% success should boost confidence
        assert score > 0.3

    def test_confidence_score_past_decision_failure_rate(self, basic_context, mock_learning_store):
        """Test past decision failure rate reduces confidence."""
        # Create past decisions with 0% success rate
        past_decisions = [
            DecisionRecord(
                decision_id="d1",
                context_signature="sig",
                domain="test",
                project="test",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            ),
            DecisionRecord(
                decision_id="d2",
                context_signature="sig",
                domain="test",
                project="test",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=False,
            ),
        ]

        mock_learning_store.get_decisions_by_context = MagicMock(return_value=past_decisions)
        engine = DecisionEngine(learning_store=mock_learning_store)

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # 0% success should reduce confidence below base
        assert score < 0.3

    def test_confidence_score_pattern_success_rate(self, basic_context, mock_learning_store):
        """Test pattern success rate affects confidence."""
        patterns = [
            PatternRecord(
                pattern_id="p1",
                context_signature="test-signature",
                pattern_type="success",
                total_applications=10,
                successful_applications=10,  # 100% success
            ),
        ]

        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # High pattern success should boost confidence
        assert score > 0.3

    def test_confidence_score_clamped_to_range(self, basic_context, mock_learning_store):
        """Test confidence score is clamped to [0.0, 1.0]."""
        # Create scenario that would exceed 1.0
        past_decisions = [
            DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig",
                domain="test",
                project="test",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
            for i in range(100)  # Many successful decisions
        ]

        mock_learning_store.get_decisions_by_context = MagicMock(return_value=past_decisions)
        engine = DecisionEngine(learning_store=mock_learning_store)

        atlas = AtlasSignals(
            related_patterns=[
                AtlasPattern(pattern_id=f"p{i}", pattern_type="test", confidence=1.0)
                for i in range(100)
            ],
            similar_decisions=[f"d{i}" for i in range(100)],
        )
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # Should be capped at 1.0
        assert score == 1.0

    def test_confidence_score_without_learning_store(self, basic_context):
        """Test confidence calculation without learning store."""
        engine = DecisionEngine(learning_store=None)

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        score = engine._calculate_confidence_score(atlas, diligence, basic_context)

        # Should still return base confidence
        assert score >= 0.0


# =============================================================================
# Test Complexity Score Calculation
# =============================================================================


class TestComplexityScoreCalculation:
    """Tests for complexity score calculation."""

    def test_complexity_score_single_file(self, basic_context):
        """Test complexity score for single file."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            related_patterns=[AtlasPattern(pattern_id="p1", pattern_type="test", confidence=0.5)],
        )
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_complexity_score(context, atlas)

        # 1 file * 0.1 = 0.1 (with pattern = no +0.2)
        assert score == 0.1

    def test_complexity_score_multiple_files(self, basic_context):
        """Test complexity score increases with file count (max 0.4)."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            related_patterns=[AtlasPattern(pattern_id="p1", pattern_type="test", confidence=0.5)],
        )
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=[f"file{i}.py" for i in range(10)],
            tags=[],
        )

        score = engine._calculate_complexity_score(context, atlas)

        # 10 files * 0.1 = 1.0, capped at 0.4 (with pattern = no +0.2)
        assert score == 0.4

    def test_complexity_score_complex_tags(self, basic_context):
        """Test complex tags increase complexity (max 0.3)."""
        engine = DecisionEngine()

        atlas = AtlasSignals()
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["refactor", "migration", "breaking-change", "architecture"],
        )

        score = engine._calculate_complexity_score(context, atlas)

        # 4 tags * 0.15 = 0.6, capped at 0.3
        tag_complexity = min(0.3, 4 * 0.15)
        assert score >= tag_complexity

    def test_complexity_score_no_patterns(self, basic_context):
        """Test lack of related patterns increases complexity."""
        engine = DecisionEngine()

        atlas = AtlasSignals(related_patterns=[])  # No patterns
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_complexity_score(context, atlas)

        # No patterns = unknown territory = +0.2
        assert score >= 0.2

    def test_complexity_score_with_patterns(self, basic_context, sample_atlas_pattern):
        """Test having patterns reduces complexity impact."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            related_patterns=[sample_atlas_pattern],
        )
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_complexity_score(context, atlas)

        # Should not include +0.2 for no patterns
        assert score < 0.2

    def test_complexity_score_max_capped(self, basic_context):
        """Test complexity score is capped at 1.0."""
        engine = DecisionEngine()

        atlas = AtlasSignals(related_patterns=[])  # No patterns = +0.2
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=[f"f{i}.py" for i in range(30)],  # 30 files → 3.0, capped at 0.4
            tags=["refactor", "migration", "breaking-change", "architecture"],  # 4 tags → 0.6, capped at 0.3
        )

        score = engine._calculate_complexity_score(context, atlas)

        # Max is 0.4 (files) + 0.3 (tags) + 0.2 (no patterns) = 0.9
        # We need 30+ files to hit file cap, 4+ tags to hit tag cap, and no patterns for +0.2
        assert score >= 0.85  # Account for floating point precision, should be ~0.9


# =============================================================================
# Test Precedent Score Calculation
# =============================================================================


class TestPrecedentScoreCalculation:
    """Tests for precedent score calculation."""

    def test_precedent_score_no_precedent(self, basic_context):
        """Test precedent score with no precedent."""
        engine = DecisionEngine()

        atlas = AtlasSignals()
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_precedent_score(atlas, context)

        assert score == 0.0

    def test_precedent_score_related_patterns(self, basic_context, sample_atlas_pattern):
        """Test related patterns increase precedent (max 0.5)."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            related_patterns=[
                sample_atlas_pattern,
                AtlasPattern(pattern_id="p2", pattern_type="test", confidence=0.6),
                AtlasPattern(pattern_id="p3", pattern_type="test", confidence=0.5),
                AtlasPattern(pattern_id="p4", pattern_type="test", confidence=0.4),  # Below threshold
            ],
        )
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_precedent_score(atlas, context)

        # 3 effective patterns (confidence >= 0.5) * 0.15 = 0.45
        assert score >= 0.44  # Account for floating point precision

    def test_precedent_score_similar_decisions(self, basic_context):
        """Test similar decisions increase precedent (max 0.3)."""
        engine = DecisionEngine()

        atlas = AtlasSignals(
            similar_decisions=[f"d{i}" for i in range(5)],
        )
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_precedent_score(atlas, context)

        # 5 decisions * 0.1 = 0.5, capped at 0.3
        assert 0.3 <= score <= 0.5

    def test_precedent_score_from_learning_store(self, basic_context, mock_learning_store, sample_pattern_record):
        """Test learning store patterns increase precedent (max 0.2)."""
        patterns = [sample_pattern_record] * 5  # 5 effective patterns
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        atlas = AtlasSignals()
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_precedent_score(atlas, context)

        # 5 patterns * 0.1 = 0.5, capped at 0.2
        assert 0.2 <= score <= 0.5

    def test_precedent_score_max_capped(self, basic_context, mock_learning_store, sample_pattern_record):
        """Test precedent score is capped at 1.0."""
        patterns = [sample_pattern_record] * 20
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        atlas = AtlasSignals(
            related_patterns=[
                AtlasPattern(pattern_id=f"p{i}", pattern_type="test", confidence=0.9)
                for i in range(20)
            ],
            similar_decisions=[f"d{i}" for i in range(20)],
        )
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        score = engine._calculate_precedent_score(atlas, context)

        assert score == 1.0


# =============================================================================
# Test ScoreCard Calculation
# =============================================================================


class TestScoreCardCalculation:
    """Tests for complete ScoreCard calculation."""

    def test_calculate_scores_returns_scorecard(self, basic_context):
        """Test _calculate_scores returns valid ScoreCard."""
        engine = DecisionEngine()

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        scores = engine._calculate_scores(atlas, diligence, basic_context)

        assert isinstance(scores, ScoreCard)
        assert hasattr(scores, "risk_score")
        assert hasattr(scores, "confidence_score")
        assert hasattr(scores, "complexity_score")
        assert hasattr(scores, "precedent_score")
        assert hasattr(scores, "aggregate_score")

    def test_scorecard_aggregate_score(self, basic_context):
        """Test ScoreCard aggregate_score is calculated correctly."""
        engine = DecisionEngine()

        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        scores = engine._calculate_scores(atlas, diligence, basic_context)

        # Aggregate uses weights: risk=0.4, confidence=0.3, complexity=0.2, precedent=0.1
        # Formula: (1-risk)*0.4 + confidence*0.3 + (1-complexity)*0.2 + precedent*0.1
        expected = (
            (1 - scores.risk_score) * 0.4 +
            scores.confidence_score * 0.3 +
            (1 - scores.complexity_score) * 0.2 +
            scores.precedent_score * 0.1
        )
        assert abs(scores.aggregate_score - expected) < 0.01


# =============================================================================
# Test Action Determination
# =============================================================================


class TestActionDetermination:
    """Tests for action determination logic."""

    def test_action_block_critical_risk(self, basic_context):
        """Test BLOCK action for critical risk level."""
        engine = DecisionEngine()

        scores = ScoreCard(risk_score=0.5, confidence_score=0.8, complexity_score=0.2, precedent_score=0.7)
        diligence = DiligenceSignals(risk_level="critical")

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.BLOCK
        assert reason is None

    def test_action_human_review_blocking_issues(self, basic_context):
        """Test HUMAN_REVIEW_REQUIRED for blocking issues."""
        engine = DecisionEngine()

        scores = ScoreCard(risk_score=0.3, confidence_score=0.7, complexity_score=0.2, precedent_score=0.6)
        diligence = DiligenceSignals(
            risk_level="medium",
            blocking_issues=[
                DiligenceFinding(
                    finding_id="issue1",
                    title="Issue 1",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue2",
                    title="Issue 2",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
            ],
        )

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.HUMAN_REVIEW_REQUIRED
        assert reason is None

    def test_action_block_low_aggregate_score(self, basic_context):
        """Test BLOCK action when aggregate score is below block threshold."""
        config = DecisionEngineConfig(block_threshold=0.3)
        engine = DecisionEngine(config=config)

        scores = ScoreCard(risk_score=0.8, confidence_score=0.2, complexity_score=0.5, precedent_score=0.1)
        diligence = DiligenceSignals(risk_level="low")

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.BLOCK

    def test_action_human_review_medium_aggregate_score(self, basic_context):
        """Test HUMAN_REVIEW_REQUIRED when aggregate is below human_review threshold."""
        config = DecisionEngineConfig(
            block_threshold=0.3,
            human_review_threshold=0.5,
        )
        engine = DecisionEngine(config=config)

        scores = ScoreCard(risk_score=0.5, confidence_score=0.4, complexity_score=0.3, precedent_score=0.3)
        diligence = DiligenceSignals(risk_level="low")

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.HUMAN_REVIEW_REQUIRED

    def test_action_proceed_with_caution(self, basic_context):
        """Test PROCEED_WITH_CAUTION when aggregate is below caution threshold."""
        config = DecisionEngineConfig(
            block_threshold=0.3,
            human_review_threshold=0.5,
            caution_threshold=0.7,
        )
        engine = DecisionEngine(config=config)

        scores = ScoreCard(risk_score=0.3, confidence_score=0.5, complexity_score=0.3, precedent_score=0.4)
        diligence = DiligenceSignals(risk_level="low")

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.PROCEED_WITH_CAUTION

    def test_action_proceed_low_confidence(self, basic_context):
        """Test PROCEED_WITH_CAUTION when confidence is too low."""
        config = DecisionEngineConfig(
            min_confidence_for_proceed=0.7,
        )
        engine = DecisionEngine(config=config)

        scores = ScoreCard(risk_score=0.1, confidence_score=0.5, complexity_score=0.2, precedent_score=0.8)
        diligence = DiligenceSignals(risk_level="low")

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.PROCEED_WITH_CAUTION

    def test_action_proceed_all_good(self, basic_context):
        """Test PROCEED when all signals are positive."""
        engine = DecisionEngine()

        scores = ScoreCard(risk_score=0.1, confidence_score=0.9, complexity_score=0.2, precedent_score=0.8)
        diligence = DiligenceSignals(risk_level="low")

        action, reason = engine._determine_action(scores, diligence)

        assert action == DecisionAction.PROCEED


# =============================================================================
# Test Pattern Success Rate Check (Feedback Loop)
# =============================================================================


class TestPatternSuccessRateCheck:
    """Tests for pattern success rate-based blocking (feedback loop)."""

    def test_pattern_block_low_success_rate(self, basic_context, mock_learning_store):
        """Test blocking when pattern success rate is below 30%."""
        # Patterns with low success rate (2/10 = 20%)
        patterns = [
            PatternRecord(
                pattern_id="p1",
                context_signature="test-signature",
                pattern_type="failing",
                total_applications=10,
                successful_applications=2,
            ),
        ]
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        reason = engine._check_pattern_success_rate(basic_context)

        assert reason is not None
        assert "20%" in reason
        assert "success rate" in reason

    def test_pattern_block_insufficient_data(self, basic_context, mock_learning_store):
        """Test no blocking when there's insufficient data (< 3 applications)."""
        patterns = [
            PatternRecord(
                pattern_id="p1",
                context_signature="test-signature",
                pattern_type="new",
                total_applications=2,  # Below threshold
                successful_applications=0,
            ),
        ]
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        reason = engine._check_pattern_success_rate(basic_context)

        # Should not block due to insufficient data
        assert reason is None

    def test_pattern_block_no_patterns(self, basic_context, mock_learning_store):
        """Test no blocking when there are no patterns."""
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=[])
        engine = DecisionEngine(learning_store=mock_learning_store)

        reason = engine._check_pattern_success_rate(basic_context)

        assert reason is None

    def test_pattern_block_high_success_rate(self, basic_context, mock_learning_store):
        """Test no blocking when success rate is high (> 30%)."""
        patterns = [
            PatternRecord(
                pattern_id="p1",
                context_signature="test-signature",
                pattern_type="good",
                total_applications=10,
                successful_applications=8,  # 80% success
            ),
        ]
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        reason = engine._check_pattern_success_rate(basic_context)

        assert reason is None

    def test_pattern_block_without_learning_store(self, basic_context):
        """Test pattern blocking is skipped without learning store."""
        engine = DecisionEngine(learning_store=None)

        reason = engine._check_pattern_success_rate(basic_context)

        assert reason is None


# =============================================================================
# Test Confidence Level Determination
# =============================================================================


class TestConfidenceLevelDetermination:
    """Tests for confidence level classification."""

    def test_confidence_level_high(self, basic_context):
        """Test HIGH confidence level (>= 0.8)."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.1,
            confidence_score=0.9,
            complexity_score=0.2,
            precedent_score=0.8,
        )

        level = engine._determine_confidence_level(scores)

        assert level == ConfidenceLevel.HIGH

    def test_confidence_level_medium(self, basic_context):
        """Test MEDIUM confidence level (>= 0.5)."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.6,
            complexity_score=0.3,
            precedent_score=0.5,
        )

        level = engine._determine_confidence_level(scores)

        assert level == ConfidenceLevel.MEDIUM

    def test_confidence_level_low(self, basic_context):
        """Test LOW confidence level (>= 0.2)."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.5,
            confidence_score=0.3,
            complexity_score=0.4,
            precedent_score=0.2,
        )

        level = engine._determine_confidence_level(scores)

        assert level == ConfidenceLevel.LOW

    def test_confidence_level_unknown(self, basic_context):
        """Test UNKNOWN confidence level (< 0.2)."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.8,
            confidence_score=0.1,
            complexity_score=0.7,
            precedent_score=0.1,
        )

        level = engine._determine_confidence_level(scores)

        assert level == ConfidenceLevel.UNKNOWN


# =============================================================================
# Test Tier Classification
# =============================================================================


class TestTierClassification:
    """Tests for tier classification logic."""

    def test_tier_desktop_for_block_action(self, basic_context):
        """Test DESKTOP tier for BLOCK action."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.2,
            confidence_score=0.5,
            complexity_score=0.2,
            precedent_score=0.5,
        )
        diligence = DiligenceSignals(risk_level="low")

        tier = engine.classify_tier(
            DecisionAction.BLOCK,
            scores,
            basic_context,
            diligence,
        )

        assert tier == DecisionTier.DESKTOP

    def test_tier_desktop_for_critical_risk(self, basic_context):
        """Test DESKTOP tier for critical risk level."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.5,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.5,
        )
        diligence = DiligenceSignals(risk_level="critical")

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            basic_context,
            diligence,
        )

        assert tier == DecisionTier.DESKTOP

    def test_tier_desktop_for_high_risk_score(self, basic_context):
        """Test DESKTOP tier for high risk score (> 0.6)."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.7,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.5,
        )
        diligence = DiligenceSignals(risk_level="medium")

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            basic_context,
            diligence,
        )

        assert tier == DecisionTier.DESKTOP

    def test_tier_desktop_for_sensitive_tags(self, basic_context):
        """Test DESKTOP tier for sensitive tags."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.6,
            complexity_score=0.3,
            precedent_score=0.5,
        )
        diligence = DiligenceSignals(risk_level="low")

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["security"],
        )

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            context,
            diligence,
        )

        assert tier == DecisionTier.DESKTOP

    def test_tier_desktop_for_complex_tags(self, basic_context):
        """Test DESKTOP tier for complex tags."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.6,
            complexity_score=0.5,
            precedent_score=0.5,
        )
        diligence = DiligenceSignals(risk_level="low")

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["breaking-change"],
        )

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            context,
            diligence,
        )

        assert tier == DecisionTier.DESKTOP

    def test_tier_desktop_for_high_complexity(self, basic_context):
        """Test DESKTOP tier for high complexity score (> 0.7)."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.6,
            complexity_score=0.8,
            precedent_score=0.5,
        )
        diligence = DiligenceSignals(risk_level="low")

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            basic_context,
            diligence,
        )

        assert tier == DecisionTier.DESKTOP

    def test_tier_watch_for_eligible_tags(self, basic_context):
        """Test WATCH tier for eligible tags with low risk and high precedent."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.2,
            confidence_score=0.6,
            complexity_score=0.2,
            precedent_score=0.7,
        )
        diligence = DiligenceSignals(risk_level="low")

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["test"],
        )

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            context,
            diligence,
        )

        assert tier == DecisionTier.WATCH

    def test_tier_watch_for_proceed_with_high_confidence_low_risk(self, basic_context):
        """Test WATCH tier for PROCEED action with high confidence and low risk."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.1,
            confidence_score=0.8,
            complexity_score=0.2,
            precedent_score=0.6,
        )
        diligence = DiligenceSignals(risk_level="low")

        # Use a clean context without sensitive tags
        clean_context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["docs"],  # Non-sensitive tag
        )

        tier = engine.classify_tier(
            DecisionAction.PROCEED,
            scores,
            clean_context,
            diligence,
        )

        assert tier == DecisionTier.WATCH

    def test_tier_phone_default(self, basic_context):
        """Test PHONE tier as default for human review cases."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.4,
            confidence_score=0.5,
            complexity_score=0.4,
            precedent_score=0.4,
        )
        diligence = DiligenceSignals(risk_level="medium")

        # Use a clean context without sensitive tags
        clean_context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["feature"],  # Non-sensitive tag
        )

        tier = engine.classify_tier(
            DecisionAction.PROCEED_WITH_CAUTION,
            scores,
            clean_context,
            diligence,
        )

        assert tier == DecisionTier.PHONE


# =============================================================================
# Test Warning Generation
# =============================================================================


class TestWarningGeneration:
    """Tests for warning generation."""

    def test_warning_high_risk(self, basic_context):
        """Test HIGH_RISK warning is generated."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.8,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.4,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        warnings = engine._generate_warnings(scores, atlas, diligence)

        high_risk_warnings = [w for w in warnings if w.code == "HIGH_RISK"]
        assert len(high_risk_warnings) == 1
        assert high_risk_warnings[0].severity == "high"

    def test_warning_low_confidence(self, basic_context):
        """Test LOW_CONFIDENCE warning is generated."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.2,
            confidence_score=0.3,
            complexity_score=0.3,
            precedent_score=0.4,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        warnings = engine._generate_warnings(scores, atlas, diligence)

        low_conf_warnings = [w for w in warnings if w.code == "LOW_CONFIDENCE"]
        assert len(low_conf_warnings) == 1
        assert low_conf_warnings[0].severity == "medium"

    def test_warning_high_complexity(self, basic_context):
        """Test HIGH_COMPLEXITY warning is generated."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.2,
            confidence_score=0.5,
            complexity_score=0.8,
            precedent_score=0.4,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        warnings = engine._generate_warnings(scores, atlas, diligence)

        high_complexity_warnings = [w for w in warnings if w.code == "HIGH_COMPLEXITY"]
        assert len(high_complexity_warnings) == 1

    def test_warning_recurring_problems(self, basic_context):
        """Test RECURRING_PROBLEMS warning is generated."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.4,
        )
        atlas = AtlasSignals(
            recurring_problems=["issue1", "issue2", "issue3"],
        )
        diligence = DiligenceSignals()

        warnings = engine._generate_warnings(scores, atlas, diligence)

        recurring_warnings = [w for w in warnings if w.code == "RECURRING_PROBLEMS"]
        assert len(recurring_warnings) == 1
        assert "3 known issues" in recurring_warnings[0].message

    def test_no_warnings_for_clean_signals(self, basic_context):
        """Test no warnings for clean signals."""
        engine = DecisionEngine()

        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.6,
            complexity_score=0.3,
            precedent_score=0.5,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        warnings = engine._generate_warnings(scores, atlas, diligence)

        # Should have no warnings (all thresholds passed)
        risk_warnings = [w for w in warnings if w.code in ("HIGH_RISK", "LOW_CONFIDENCE", "HIGH_COMPLEXITY", "RECURRING_PROBLEMS")]
        assert len(risk_warnings) == 0


# =============================================================================
# Test Reasoning Generation
# =============================================================================


class TestReasoningGeneration:
    """Tests for reasoning generation."""

    def test_reasoning_proceed(self, basic_context):
        """Test reasoning for PROCEED action."""
        engine = DecisionEngine()

        action = DecisionAction.PROCEED
        scores = ScoreCard(
            risk_score=0.1,
            confidence_score=0.8,
            complexity_score=0.2,
            precedent_score=0.7,
        )
        atlas = AtlasSignals(
            related_patterns=[
                AtlasPattern(pattern_id="p1", pattern_type="test", confidence=0.8),
                AtlasPattern(pattern_id="p2", pattern_type="test", confidence=0.7),
            ],
        )
        diligence = DiligenceSignals()

        reasoning = engine._generate_reasoning(action, scores, atlas, diligence)

        assert "safe to proceed" in reasoning
        assert "2 successful patterns" in reasoning
        assert "risk=" in reasoning

    def test_reasoning_proceed_with_caution_low_confidence(self, basic_context):
        """Test reasoning for PROCEED_WITH_CAUTION due to low confidence."""
        engine = DecisionEngine()

        action = DecisionAction.PROCEED_WITH_CAUTION
        scores = ScoreCard(
            risk_score=0.2,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.4,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        reasoning = engine._generate_reasoning(action, scores, atlas, diligence)

        assert "caution" in reasoning.lower()

    def test_reasoning_human_review_blocking_issues(self, basic_context):
        """Test reasoning for HUMAN_REVIEW_REQUIRED due to blocking issues."""
        engine = DecisionEngine()

        action = DecisionAction.HUMAN_REVIEW_REQUIRED
        scores = ScoreCard(
            risk_score=0.4,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.4,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals(
            blocking_issues=[
                DiligenceFinding(
                    finding_id="issue1",
                    title="Issue 1",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
                DiligenceFinding(
                    finding_id="issue2",
                    title="Issue 2",
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                ),
            ],
        )

        reasoning = engine._generate_reasoning(action, scores, atlas, diligence)

        assert "2 blocking issues" in reasoning

    def test_reasoning_block_critical(self, basic_context):
        """Test reasoning for BLOCK due to critical risk."""
        engine = DecisionEngine()

        action = DecisionAction.BLOCK
        scores = ScoreCard(
            risk_score=0.8,
            confidence_score=0.3,
            complexity_score=0.5,
            precedent_score=0.2,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals(risk_level="critical")

        reasoning = engine._generate_reasoning(action, scores, atlas, diligence)

        assert "Critical security/quality issues" in reasoning

    def test_reasoning_block_pattern_based(self, basic_context):
        """Test reasoning for BLOCK due to pattern success rate."""
        engine = DecisionEngine()

        action = DecisionAction.BLOCK
        scores = ScoreCard(
            risk_score=0.3,
            confidence_score=0.4,
            complexity_score=0.3,
            precedent_score=0.3,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals(risk_level="low")

        pattern_reason = "Pattern history shows 20% success rate. Blocking to prevent repeated failures."

        reasoning = engine._generate_reasoning(action, scores, atlas, diligence, pattern_reason)

        assert "20% success rate" in reasoning

    def test_reasoning_includes_score_summary(self, basic_context):
        """Test reasoning always includes score summary."""
        engine = DecisionEngine()

        action = DecisionAction.PROCEED
        scores = ScoreCard(
            risk_score=0.25,
            confidence_score=0.75,
            complexity_score=0.35,
            precedent_score=0.65,
        )
        atlas = AtlasSignals()
        diligence = DiligenceSignals()

        reasoning = engine._generate_reasoning(action, scores, atlas, diligence)

        assert "risk=0.25" in reasoning
        assert "confidence=0.75" in reasoning
        assert "complexity=0.35" in reasoning
        assert "precedent=0.65" in reasoning


# =============================================================================
# Test get_recommendation (Main API)
# =============================================================================


class TestGetRecommendation:
    """Tests for get_recommendation main API."""

    @pytest.mark.asyncio
    async def test_get_recommendation_returns_valid_recommendation(self, basic_context):
        """Test get_recommendation returns valid DecisionRecommendation."""
        engine = DecisionEngine()

        recommendation = await engine.get_recommendation(basic_context)

        assert isinstance(recommendation, DecisionRecommendation)
        assert hasattr(recommendation, "action")
        assert hasattr(recommendation, "confidence")
        assert hasattr(recommendation, "scores")
        assert hasattr(recommendation, "reasoning")
        assert hasattr(recommendation, "warnings")

    @pytest.mark.asyncio
    async def test_get_recommendation_with_all_bridges(
        self,
        basic_context,
        mock_atlas_bridge,
        mock_diligence_bridge,
    ):
        """Test get_recommendation with all bridges active."""
        mock_atlas_bridge.get_signals = AsyncMock(return_value=AtlasSignals(
            related_patterns=[AtlasPattern(pattern_id="p1", pattern_type="test", confidence=0.8)],
        ))
        mock_diligence_bridge.get_signals = AsyncMock(return_value=DiligenceSignals(
            risk_level="low",
        ))

        engine = DecisionEngine(
            atlas_bridge=mock_atlas_bridge,
            diligence_bridge=mock_diligence_bridge,
        )

        recommendation = await engine.get_recommendation(basic_context)

        assert recommendation.action is not None

        # Verify bridges were called
        mock_atlas_bridge.get_signals.assert_called_once()
        mock_diligence_bridge.get_signals.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_recommendation_with_recording_enabled(
        self,
        basic_context,
        mock_learning_store,
    ):
        """Test get_recommendation records decision when enabled."""
        config = DecisionEngineConfig(enable_decision_recording=True)
        engine = DecisionEngine(
            learning_store=mock_learning_store,
            config=config,
        )

        recommendation = await engine.get_recommendation(basic_context)

        # Verify decision was recorded
        mock_learning_store.record_decision.assert_called_once()

        # Check the recorded decision
        call_args = mock_learning_store.record_decision.call_args[0][0]
        assert isinstance(call_args, DecisionRecord)

    @pytest.mark.asyncio
    async def test_get_recommendation_without_recording(self, basic_context, mock_learning_store):
        """Test get_recommendation doesn't record when disabled."""
        config = DecisionEngineConfig(enable_decision_recording=False)
        engine = DecisionEngine(
            learning_store=mock_learning_store,
            config=config,
        )

        await engine.get_recommendation(basic_context)

        # Verify decision was NOT recorded
        mock_learning_store.record_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_recommendation_with_tracker(self, basic_context, mock_tracker):
        """Test get_recommendation calls tracker when provided."""
        engine = DecisionEngine(tracker=mock_tracker)

        recommendation = await engine.get_recommendation(basic_context)

        # Verify tracker was called
        mock_tracker.decision_made.assert_called_once()

        call_args = mock_tracker.decision_made.call_args
        assert call_args[1]["action"] in [a.value for a in DecisionAction]
        assert "reasoning" in call_args[1]

    @pytest.mark.asyncio
    async def test_get_recommendation_graceful_degradation_no_bridges(self, basic_context):
        """Test get_recommendation works with no bridges (graceful degradation)."""
        engine = DecisionEngine()

        recommendation = await engine.get_recommendation(basic_context)

        # Should still return a valid recommendation
        assert isinstance(recommendation, DecisionRecommendation)
        assert recommendation.action is not None


# =============================================================================
# Test record_outcome
# =============================================================================


class TestRecordOutcome:
    """Tests for recording decision outcomes."""

    @pytest.mark.asyncio
    async def test_record_outcome_success(self, mock_learning_store):
        """Test recording a successful outcome."""
        engine = DecisionEngine(learning_store=mock_learning_store)

        # Create a cached decision
        decision_id = "test-decision-123"
        engine._decision_cache[decision_id] = DecisionRecord(
            decision_id=decision_id,
            context_signature="sig",
            domain="test",
            project="test",
            recommended_action=DecisionAction.PROCEED,
        )

        outcome = await engine.record_outcome(decision_id, success=True)

        assert isinstance(outcome, DecisionOutcome)
        assert outcome.decision_id == decision_id
        assert outcome.success is True

        mock_learning_store.record_outcome.assert_called_once_with(
            decision_id=decision_id,
            success=True,
            actual_action=None,
        )

    @pytest.mark.asyncio
    async def test_record_outcome_failure(self, mock_learning_store):
        """Test recording a failed outcome."""
        engine = DecisionEngine(learning_store=mock_learning_store)

        decision_id = "test-decision-456"
        engine._decision_cache[decision_id] = DecisionRecord(
            decision_id=decision_id,
            context_signature="sig",
            domain="test",
            project="test",
            recommended_action=DecisionAction.PROCEED,
        )

        outcome = await engine.record_outcome(decision_id, success=False)

        assert outcome.success is False

    @pytest.mark.asyncio
    async def test_record_outcome_with_actual_action(self, mock_learning_store):
        """Test recording outcome with different actual action."""
        engine = DecisionEngine(learning_store=mock_learning_store)

        decision_id = "test-decision-789"
        engine._decision_cache[decision_id] = DecisionRecord(
            decision_id=decision_id,
            context_signature="sig",
            domain="test",
            project="test",
            recommended_action=DecisionAction.PROCEED,
        )

        outcome = await engine.record_outcome(
            decision_id,
            success=True,
            actual_action=DecisionAction.PROCEED_WITH_CAUTION,
        )

        assert outcome.actual_action == DecisionAction.PROCEED_WITH_CAUTION

    @pytest.mark.asyncio
    async def test_record_outcome_without_learning_store(self):
        """Test recording outcome without learning store returns None."""
        engine = DecisionEngine(learning_store=None)

        outcome = await engine.record_outcome("test-id", success=True)

        assert outcome is None

    @pytest.mark.asyncio
    async def test_record_outcome_decision_not_found(self, mock_learning_store):
        """Test recording outcome for non-existent decision returns None."""
        mock_learning_store.get_decision = MagicMock(return_value=None)
        engine = DecisionEngine(learning_store=mock_learning_store)

        outcome = await engine.record_outcome("non-existent-id", success=True)

        assert outcome is None


# =============================================================================
# Test Statistics
# =============================================================================


class TestStatistics:
    """Tests for statistics gathering."""

    def test_get_statistics_basic(self):
        """Test basic statistics without learning store."""
        engine = DecisionEngine()

        stats = engine.get_statistics()

        assert stats["has_atlas_bridge"] is False
        assert stats["has_diligence_bridge"] is False
        assert stats["has_learning_store"] is False
        assert stats["cached_decisions"] == 0

    def test_get_statistics_with_bridges(self, mock_atlas_bridge, mock_diligence_bridge):
        """Test statistics with bridges."""
        engine = DecisionEngine(
            atlas_bridge=mock_atlas_bridge,
            diligence_bridge=mock_diligence_bridge,
        )

        stats = engine.get_statistics()

        assert stats["has_atlas_bridge"] is True
        assert stats["has_diligence_bridge"] is True

    def test_get_statistics_with_cached_decisions(self):
        """Test statistics includes cached decisions count."""
        engine = DecisionEngine()

        # Add some cached decisions
        engine._decision_cache["decision1"] = DecisionRecord(
            decision_id="decision1",
            context_signature="sig",
            domain="test",
            project="test",
            recommended_action=DecisionAction.PROCEED,
        )
        engine._decision_cache["decision2"] = DecisionRecord(
            decision_id="decision2",
            context_signature="sig",
            domain="test",
            project="test",
            recommended_action=DecisionAction.PROCEED,
        )

        stats = engine.get_statistics()

        assert stats["cached_decisions"] == 2

    def test_get_statistics_with_learning_store(self, mock_learning_store):
        """Test statistics includes learning store stats."""
        engine = DecisionEngine(learning_store=mock_learning_store)

        stats = engine.get_statistics()

        assert "total_decisions_recorded" in stats
        assert stats["total_decisions_recorded"] == 100
        assert "decision_success_rate" in stats
        assert stats["decision_success_rate"] == 0.85


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_context(self):
        """Test handling of minimal context."""
        context = DecisionContext(
            domain="",
            project="",
            file_paths=[],
            tags=[],
        )

        engine = DecisionEngine()
        recommendation = await engine.get_recommendation(context)

        assert isinstance(recommendation, DecisionRecommendation)

    @pytest.mark.asyncio
    async def test_very_long_file_list(self):
        """Test handling of very long file lists."""
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=[f"file{i}.py" for i in range(100)],
            tags=[],
        )

        engine = DecisionEngine()
        recommendation = await engine.get_recommendation(context)

        # Should handle gracefully (complexity capped)
        assert recommendation.scores.complexity_score <= 1.0

    @pytest.mark.asyncio
    async def test_unicode_tags(self):
        """Test handling of unicode tags."""
        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=["security", "认证", "auth"],
        )

        engine = DecisionEngine()
        recommendation = await engine.get_recommendation(context)

        assert isinstance(recommendation, DecisionRecommendation)

    @pytest.mark.asyncio
    async def test_none_feature_id(self):
        """Test handling of None feature_id."""
        context = DecisionContext(
            domain="test",
            project="test",
            feature_id=None,
            file_paths=["test.py"],
            tags=[],
        )

        engine = DecisionEngine()
        recommendation = await engine.get_recommendation(context)

        assert isinstance(recommendation, DecisionRecommendation)

    def test_scores_at_boundaries(self):
        """Test score calculations at boundary values."""
        engine = DecisionEngine()

        # All zeros
        atlas_zero = AtlasSignals()
        diligence_zero = DiligenceSignals()
        context_zero = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        scores = engine._calculate_scores(atlas_zero, diligence_zero, context_zero)

        # All scores should be in valid range
        assert 0.0 <= scores.risk_score <= 1.0
        assert 0.0 <= scores.confidence_score <= 1.0
        assert 0.0 <= scores.complexity_score <= 1.0
        assert 0.0 <= scores.precedent_score <= 1.0
        assert 0.0 <= scores.aggregate_score <= 1.0

    @pytest.mark.asyncio
    async def test_concurrent_recommendations(self):
        """Test handling multiple concurrent recommendations."""
        import asyncio

        contexts = [
            DecisionContext(
                domain=f"test{i}",
                project=f"proj{i}",
                file_paths=[f"file{i}.py"],
                tags=[f"tag{i}"],
            )
            for i in range(10)
        ]

        engine = DecisionEngine()

        recommendations = await asyncio.gather(*[
            engine.get_recommendation(ctx) for ctx in contexts
        ])

        assert len(recommendations) == 10
        for rec in recommendations:
            assert isinstance(rec, DecisionRecommendation)

    @pytest.mark.asyncio
    async def test_pattern_block_threshold_exactly_30_percent(self, mock_learning_store):
        """Test pattern blocking at exactly 30% success rate (boundary)."""
        # Exactly 30% success rate (3/10)
        patterns = [
            PatternRecord(
                pattern_id="p1",
                context_signature="test-signature",
                pattern_type="boundary",
                total_applications=10,
                successful_applications=3,
            ),
        ]
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        reason = engine._check_pattern_success_rate(context)

        # At exactly 30%, should NOT block (< 0.3 check)
        assert reason is None

    @pytest.mark.asyncio
    async def test_pattern_block_just_below_threshold(self, mock_learning_store):
        """Test pattern blocking just below 30% success rate."""
        # 29% success rate (2.9/10, rounded to 2/10)
        patterns = [
            PatternRecord(
                pattern_id="p1",
                context_signature="test-signature",
                pattern_type="below_threshold",
                total_applications=10,
                successful_applications=2,
            ),
        ]
        mock_learning_store.get_patterns_by_context = MagicMock(return_value=patterns)
        engine = DecisionEngine(learning_store=mock_learning_store)

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["test.py"],
            tags=[],
        )

        reason = engine._check_pattern_success_rate(context)

        # Below 30% should block
        assert reason is not None

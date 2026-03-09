"""Tests for feedback loops module."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.meta_learning import LearningStore
from forge_harness.meta_learning.feedback_loops import (
    DebtFeature,
    FeedbackConfig,
    FeedbackLoopManager,
    SessionSummary,
    _estimate_effort,
    _severity_to_priority,
    generate_debt_features,
    human_gate_optimizer,
    post_session_hook,
)
from forge_harness.meta_learning.schemas import (
    DiligenceFinding,
    DiligenceSignals,
    FindingCategory,
    FindingSeverity,
)


@pytest.fixture
def temp_dir(tmp_path):
    """Create temp directory for tests."""
    return tmp_path


@pytest.fixture
def learning_store(temp_dir):
    """Create a LearningStore instance."""
    return LearningStore(storage_path=temp_dir / "learning")


@pytest.fixture
def session_summary():
    """Create a sample session summary."""
    return SessionSummary(
        session_id="test-session-123",
        domain="test-domain",
        project="test-project",
        started_at=datetime.now(UTC) - timedelta(hours=2),
        ended_at=datetime.now(UTC),
        features_completed=5,
        features_blocked=1,
        total_decisions=6,
        success_rate=0.83,
        files_modified=["src/main.py", "tests/test_main.py"],
        patterns_learned=3,
    )


@pytest.fixture
def feedback_config():
    """Create feedback configuration."""
    return FeedbackConfig(
        auto_index_enabled=True,
        debt_features_enabled=True,
        threshold_optimization_enabled=True,
        min_decisions_for_optimization=5,  # Lower for testing
    )


class TestFeedbackConfig:
    """Tests for FeedbackConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = FeedbackConfig()
        assert config.auto_index_enabled is True
        assert config.debt_features_enabled is True
        assert config.threshold_optimization_enabled is True
        assert config.min_decisions_for_optimization == 20
        assert config.optimization_interval_hours == 24.0
        assert config.debt_scan_interval_hours == 168.0

    def test_custom_config(self):
        """Test custom configuration values."""
        config = FeedbackConfig(
            auto_index_enabled=False,
            min_decisions_for_optimization=50,
        )
        assert config.auto_index_enabled is False
        assert config.min_decisions_for_optimization == 50


class TestSessionSummary:
    """Tests for SessionSummary."""

    def test_session_summary_creation(self, session_summary):
        """Test session summary creation."""
        assert session_summary.session_id == "test-session-123"
        assert session_summary.domain == "test-domain"
        assert session_summary.features_completed == 5
        assert session_summary.success_rate == 0.83


class TestDebtFeature:
    """Tests for DebtFeature."""

    def test_debt_feature_to_dict(self):
        """Test DebtFeature.to_dict()."""
        feature = DebtFeature(
            id="DEBT-abc123",
            name="Fix SQL Injection",
            description="Critical security fix",
            priority="critical",
            finding_id="finding-123",
            category="security",
            estimated_effort=8.0,
        )

        result = feature.to_dict()

        assert result["id"] == "DEBT-abc123"
        assert result["name"] == "Fix SQL Injection"
        assert result["status"] == "pending"
        assert result["priority"] == "critical"
        assert "finding-123" in result["acceptance_criteria"][0]
        assert result["metadata"]["source"] == "tech_diligence"


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_severity_to_priority_mapping(self):
        """Test severity to priority mapping."""
        assert _severity_to_priority("critical") == "critical"
        assert _severity_to_priority("high") == "high"
        assert _severity_to_priority("medium") == "medium"
        assert _severity_to_priority("low") == "low"
        assert _severity_to_priority("info") == "low"
        assert _severity_to_priority("unknown") == "medium"

    def test_estimate_effort(self):
        """Test effort estimation."""
        assert _estimate_effort("critical") == 8.0
        assert _estimate_effort("high") == 4.0
        assert _estimate_effort("medium") == 2.0
        assert _estimate_effort("low") == 1.0
        assert _estimate_effort("info") == 0.5
        assert _estimate_effort("unknown") == 2.0


class TestPostSessionHook:
    """Tests for post_session_hook."""

    @pytest.mark.asyncio
    async def test_post_session_no_code_atlas(self, session_summary, learning_store):
        """Test post-session hook without Code Atlas."""
        result = await post_session_hook(
            session=session_summary,
            code_atlas=None,
            learning_store=learning_store,
        )

        assert result["skipped"] == "no_code_atlas"
        assert result["indexed"] is False

    @pytest.mark.asyncio
    async def test_post_session_disabled(self, session_summary):
        """Test post-session hook when disabled."""
        config = FeedbackConfig(auto_index_enabled=False)

        result = await post_session_hook(
            session=session_summary,
            config=config,
        )

        assert result["skipped"] == "auto_index_disabled"

    @pytest.mark.asyncio
    async def test_post_session_with_code_atlas(
        self, session_summary, learning_store, feedback_config
    ):
        """Test post-session hook with mock Code Atlas."""
        mock_code_atlas = AsyncMock()
        mock_code_atlas.index_session.return_value = {"indexed": True, "message": "Success"}

        result = await post_session_hook(
            session=session_summary,
            code_atlas=mock_code_atlas,
            learning_store=learning_store,
            config=feedback_config,
        )

        assert result["indexed"] is True
        assert result["session_id"] == session_summary.session_id
        mock_code_atlas.index_session.assert_called_once()


class TestGenerateDebtFeatures:
    """Tests for generate_debt_features."""

    @pytest.mark.asyncio
    async def test_generate_debt_no_bridge(self):
        """Test debt generation without bridge."""
        features = await generate_debt_features(
            domain="test",
            project="test",
            tech_diligence=None,
        )

        assert features == []

    @pytest.mark.asyncio
    async def test_generate_debt_disabled(self):
        """Test debt generation when disabled."""
        config = FeedbackConfig(debt_features_enabled=False)

        features = await generate_debt_features(
            domain="test",
            project="test",
            config=config,
        )

        assert features == []

    @pytest.mark.asyncio
    async def test_generate_debt_with_findings(self, feedback_config):
        """Test debt generation with mock findings."""
        mock_diligence = AsyncMock()
        mock_diligence.get_signals = AsyncMock(
            return_value=DiligenceSignals(
                risk_level="high",
                blocking_issues=[
                    DiligenceFinding(
                        finding_id="finding-001",
                        title="SQL Injection Vulnerability",
                        severity=FindingSeverity.CRITICAL,
                        category=FindingCategory.SECURITY,
                        file_path="src/db.py",
                        fix_suggestion="Use parameterized queries",
                    ),
                    DiligenceFinding(
                        finding_id="finding-002",
                        title="Hardcoded Credentials",
                        severity=FindingSeverity.HIGH,
                        category=FindingCategory.SECURITY,
                    ),
                ],
                warnings=[
                    DiligenceFinding(
                        finding_id="finding-003",
                        title="Missing Input Validation",
                        severity=FindingSeverity.MEDIUM,
                        category=FindingCategory.CODE_QUALITY,
                    ),
                ],
            )
        )

        features = await generate_debt_features(
            domain="test",
            project="test",
            tech_diligence=mock_diligence,
            config=feedback_config,
            max_features=10,
        )

        assert len(features) == 3
        assert features[0].priority == "critical"
        assert features[0].category == "security"
        assert "DEBT-" in features[0].id


class TestHumanGateOptimizer:
    """Tests for human_gate_optimizer."""

    @pytest.mark.asyncio
    async def test_optimizer_disabled(self, learning_store):
        """Test optimizer when disabled."""
        config = FeedbackConfig(threshold_optimization_enabled=False)

        result = await human_gate_optimizer(
            learning_store=learning_store,
            config=config,
        )

        assert result["skipped"] == "optimization_disabled"
        assert result["optimized"] is False

    @pytest.mark.asyncio
    async def test_optimizer_insufficient_data(self, learning_store, feedback_config):
        """Test optimizer with insufficient data."""
        result = await human_gate_optimizer(
            learning_store=learning_store,
            config=feedback_config,
        )

        assert result["skipped"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_optimizer_with_data(self, temp_dir):
        """Test optimizer with enough data."""
        # Create store with enough decisions
        learning_store = LearningStore(storage_path=temp_dir / "learning")

        # Add mock decisions using the store's internal methods
        import uuid

        from forge_harness.meta_learning.schemas import (
            DecisionAction,
            DecisionRecord,
        )

        for i in range(10):
            decision_id = str(uuid.uuid4())
            decision = DecisionRecord(
                decision_id=decision_id,
                context_signature=f"test/project/{i}",
                domain="test",
                project="project",
                recommended_action=DecisionAction.PROCEED,
                actual_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
            learning_store._decisions[decision.decision_id] = decision

        config = FeedbackConfig(min_decisions_for_optimization=5)

        result = await human_gate_optimizer(
            learning_store=learning_store,
            config=config,
        )

        assert result["optimized"] is True
        assert "analysis" in result
        assert result["analysis"]["total_decisions"] >= 5


class TestFeedbackLoopManager:
    """Tests for FeedbackLoopManager."""

    def test_manager_creation(self, learning_store, feedback_config):
        """Test manager creation."""
        manager = FeedbackLoopManager(
            learning_store=learning_store,
            config=feedback_config,
        )

        assert manager.learning_store is learning_store
        assert manager.config is feedback_config

    @pytest.mark.asyncio
    async def test_on_session_complete(self, learning_store, session_summary, feedback_config):
        """Test on_session_complete hook."""
        manager = FeedbackLoopManager(
            learning_store=learning_store,
            config=feedback_config,
        )

        results = await manager.on_session_complete(session_summary)

        assert results["session_id"] == session_summary.session_id
        assert "post_session" in results["hooks"]

    @pytest.mark.asyncio
    async def test_scan_for_debt(self, learning_store, feedback_config):
        """Test scan_for_debt method."""
        mock_diligence = AsyncMock()
        mock_diligence.get_signals = AsyncMock(
            return_value=DiligenceSignals(
                risk_level="low",
                blocking_issues=[],
                warnings=[],
            )
        )

        manager = FeedbackLoopManager(
            learning_store=learning_store,
            tech_diligence=mock_diligence,
            config=feedback_config,
        )

        features = await manager.scan_for_debt("test", "project")

        # Should return empty list since no findings
        assert features == []

    def test_should_optimize(self, learning_store, feedback_config):
        """Test _should_optimize logic."""
        manager = FeedbackLoopManager(
            learning_store=learning_store,
            config=feedback_config,
        )

        # First time should always return True
        assert manager._should_optimize() is True

        # After setting last optimization, should return False
        manager._last_optimization = datetime.now(UTC)
        assert manager._should_optimize() is False

        # After enough time passes, should return True
        manager._last_optimization = datetime.now(UTC) - timedelta(hours=25)
        assert manager._should_optimize() is True

    def test_should_scan_debt(self, learning_store, feedback_config):
        """Test _should_scan_debt logic."""
        manager = FeedbackLoopManager(
            learning_store=learning_store,
            config=feedback_config,
        )

        # First time should always return True
        assert manager._should_scan_debt() is True

        # After setting last scan, should return False
        manager._last_debt_scan = datetime.now(UTC)
        assert manager._should_scan_debt() is False

    @pytest.mark.asyncio
    async def test_scan_for_debt_not_due(self, learning_store, feedback_config):
        """Test scan_for_debt when scan is not due yet."""
        mock_diligence = AsyncMock()
        manager = FeedbackLoopManager(
            learning_store=learning_store,
            tech_diligence=mock_diligence,
            config=feedback_config,
        )

        # Set last scan to now so next scan is not due
        manager._last_debt_scan = datetime.now(UTC)

        features = await manager.scan_for_debt("test", "project")

        # Should return empty list without calling diligence
        assert features == []
        mock_diligence.get_signals.assert_not_called()


class TestPostSessionHookEdgeCases:
    """Tests for post_session_hook edge cases."""

    @pytest.mark.asyncio
    async def test_post_session_index_failure(
        self, session_summary, learning_store, feedback_config
    ):
        """Test post-session hook when indexing fails."""
        mock_code_atlas = AsyncMock()
        # Return indexed=False to trigger the warning branch
        mock_code_atlas.index_session.return_value = {"indexed": False, "error": "Service error"}

        result = await post_session_hook(
            session=session_summary,
            code_atlas=mock_code_atlas,
            learning_store=learning_store,
            config=feedback_config,
        )

        assert result["indexed"] is False
        assert result["session_id"] == session_summary.session_id

    @pytest.mark.asyncio
    async def test_post_session_exception(self, session_summary, learning_store, feedback_config):
        """Test post-session hook when exception occurs."""
        mock_code_atlas = AsyncMock()
        mock_code_atlas.index_session.side_effect = Exception("Connection failed")

        result = await post_session_hook(
            session=session_summary,
            code_atlas=mock_code_atlas,
            learning_store=learning_store,
            config=feedback_config,
        )

        assert result["indexed"] is False
        assert "Connection failed" in result["errors"]


class TestGenerateDebtFeaturesEdgeCases:
    """Tests for generate_debt_features edge cases."""

    @pytest.mark.asyncio
    async def test_generate_debt_exception(self, feedback_config):
        """Test debt feature generation when exception occurs."""
        mock_diligence = AsyncMock()
        mock_diligence.get_signals.side_effect = Exception("API error")

        features = await generate_debt_features(
            domain="test",
            project="test",
            tech_diligence=mock_diligence,
            config=feedback_config,
        )

        # Should return empty list on exception
        assert features == []


class TestHumanGateOptimizerBranches:
    """Tests for human_gate_optimizer threshold adjustment branches."""

    @pytest.mark.asyncio
    async def test_optimizer_raise_thresholds(self):
        """Test optimizer recommends raising thresholds when success rate high."""
        # Mock learning store with stats that trigger raise_thresholds:
        # - success_rate > 0.9
        # - human_review_rate > 0.3
        mock_store = MagicMock()
        mock_store.get_statistics.return_value = {
            "total_decisions": 10,
            "decision_success_rate": 0.95,  # >0.9
            "decisions_by_action": {
                "human_review_required": 4,  # 4/10 = 40% > 0.3
                "proceed": 6,
            },
        }

        config = FeedbackConfig(min_decisions_for_optimization=5)

        result = await human_gate_optimizer(
            learning_store=mock_store,
            config=config,
        )

        assert result["optimized"] is True
        assert result["recommendation"] == "raise_thresholds"
        assert result["recommended_thresholds"]["risk_threshold"] == 0.7
        assert result["recommended_thresholds"]["confidence_threshold"] == 0.5

    @pytest.mark.asyncio
    async def test_optimizer_lower_thresholds(self):
        """Test optimizer recommends lowering thresholds when success rate low."""
        # Mock learning store with stats that trigger lower_thresholds:
        # - success_rate < 0.7
        mock_store = MagicMock()
        mock_store.get_statistics.return_value = {
            "total_decisions": 10,
            "decision_success_rate": 0.5,  # <0.7
            "decisions_by_action": {
                "proceed": 10,
            },
        }

        config = FeedbackConfig(min_decisions_for_optimization=5)

        result = await human_gate_optimizer(
            learning_store=mock_store,
            config=config,
        )

        assert result["optimized"] is True
        assert result["recommendation"] == "lower_thresholds"
        assert result["recommended_thresholds"]["risk_threshold"] == 0.5
        assert result["recommended_thresholds"]["confidence_threshold"] == 0.7

    @pytest.mark.asyncio
    async def test_optimizer_exception(self, temp_dir):
        """Test optimizer handles exceptions gracefully."""
        # Create a mock learning store that raises on get_statistics
        mock_store = MagicMock()
        mock_store.get_statistics.side_effect = Exception("Database error")

        config = FeedbackConfig(min_decisions_for_optimization=5)

        result = await human_gate_optimizer(
            learning_store=mock_store,
            config=config,
        )

        assert result["optimized"] is False
        assert result["error"] == "Database error"

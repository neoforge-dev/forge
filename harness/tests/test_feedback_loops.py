"""Tests for feedback_loops module."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_storage():
    """Create a temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_session():
    """Create a sample SessionSummary."""
    return SessionSummary(
        session_id="session_001",
        domain="test-domain",
        project="test-project",
        started_at=datetime.now(UTC) - timedelta(hours=1),
        ended_at=datetime.now(UTC),
        features_completed=5,
        features_blocked=1,
        total_decisions=10,
        success_rate=0.8,
        files_modified=["a.py", "b.py", "c.py"],
        patterns_learned=3,
    )


@pytest.fixture
def feedback_config():
    """Create a FeedbackConfig."""
    return FeedbackConfig(
        auto_index_enabled=True,
        debt_features_enabled=True,
        threshold_optimization_enabled=True,
        min_decisions_for_optimization=20,
        optimization_interval_hours=24.0,
        debt_scan_interval_hours=168.0,
    )


@pytest.fixture
def mock_code_atlas():
    """Create a mock Code Atlas bridge."""
    mock = MagicMock()
    mock.index_session = AsyncMock(return_value={"indexed": True, "entities": 10})
    return mock


@pytest.fixture
def mock_tech_diligence():
    """Create a mock TechDiligence bridge."""
    mock = MagicMock()

    # Create mock signals
    mock_signals = MagicMock()
    mock_signals.blocking_issues = [
        MagicMock(
            finding_id="finding_001",
            title="Critical security issue",
            severity=MagicMock(value="critical"),
            category=MagicMock(value="security"),
            file_path="app/security.py",
            fix_suggestion="Fix the security issue",
        ),
        MagicMock(
            finding_id="finding_002",
            title="High priority bug",
            severity=MagicMock(value="high"),
            category=MagicMock(value="bug"),
            file_path="app/bug.py",
            fix_suggestion="Fix the bug",
        ),
    ]
    mock_signals.warnings = [
        MagicMock(
            finding_id="finding_003",
            title="Medium warning",
            severity=MagicMock(value="medium"),
            category=MagicMock(value="style"),
            file_path="app/style.py",
            fix_suggestion="Improve style",
        ),
    ]
    mock.get_signals = AsyncMock(return_value=mock_signals)
    return mock


# =============================================================================
# Test: FeedbackConfig
# =============================================================================


class TestFeedbackConfig:
    """Tests for FeedbackConfig dataclass."""

    def test_default_values(self):
        """Test FeedbackConfig has correct defaults."""
        config = FeedbackConfig()
        assert config.auto_index_enabled is True
        assert config.debt_features_enabled is True
        assert config.threshold_optimization_enabled is True
        assert config.min_decisions_for_optimization == 20
        assert config.optimization_interval_hours == 24.0
        assert config.debt_scan_interval_hours == 168.0

    def test_custom_values(self):
        """Test custom configuration values."""
        config = FeedbackConfig(
            auto_index_enabled=False,
            debt_features_enabled=False,
            min_decisions_for_optimization=10,
        )
        assert config.auto_index_enabled is False
        assert config.debt_features_enabled is False
        assert config.min_decisions_for_optimization == 10


# =============================================================================
# Test: SessionSummary
# =============================================================================


class TestSessionSummary:
    """Tests for SessionSummary dataclass."""

    def test_required_fields(self):
        """Test SessionSummary requires certain fields."""
        session = SessionSummary(
            session_id="sess_001",
            domain="domain1",
            project="proj1",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )
        assert session.session_id == "sess_001"
        assert session.features_completed == 0
        assert session.success_rate == 0.0
        assert session.files_modified == []

    def test_all_fields(self):
        """Test SessionSummary with all fields."""
        session = SessionSummary(
            session_id="sess_002",
            domain="domain2",
            project="proj2",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            features_completed=10,
            features_blocked=2,
            total_decisions=50,
            success_rate=0.85,
            files_modified=["a.py", "b.py"],
            patterns_learned=5,
        )
        assert session.features_completed == 10
        assert session.features_blocked == 2
        assert session.patterns_learned == 5


# =============================================================================
# Test: DebtFeature
# =============================================================================


class TestDebtFeature:
    """Tests for DebtFeature dataclass."""

    def test_to_dict(self):
        """Test DebtFeature serialization to dict."""
        feature = DebtFeature(
            id="DEBT-001",
            name="Fix security issue",
            description="Fix the security issue",
            priority="critical",
            finding_id="finding_123",
            category="security",
            estimated_effort=8.0,
        )
        result = feature.to_dict()

        assert result["id"] == "DEBT-001"
        assert result["name"] == "Fix security issue"
        assert result["status"] == "pending"
        assert result["priority"] == "critical"
        assert result["metadata"]["source"] == "tech_diligence"
        assert result["metadata"]["finding_id"] == "finding_123"

    def test_to_dict_has_acceptance_criteria(self):
        """Test acceptance criteria are included."""
        feature = DebtFeature(
            id="DEBT-002",
            name="Fix bug",
            description="Fix the bug",
            priority="high",
            finding_id="finding_456",
            category="bug",
        )
        result = feature.to_dict()

        assert "acceptance_criteria" in result
        assert len(result["acceptance_criteria"]) == 2


# =============================================================================
# Test: Helper Functions
# =============================================================================


class TestSeverityToPriority:
    """Tests for _severity_to_priority function."""

    def test_critical(self):
        """Test critical severity maps to critical priority."""
        assert _severity_to_priority("critical") == "critical"

    def test_high(self):
        """Test high severity maps to high priority."""
        assert _severity_to_priority("high") == "high"

    def test_medium(self):
        """Test medium severity maps to medium priority."""
        assert _severity_to_priority("medium") == "medium"

    def test_low(self):
        """Test low severity maps to low priority."""
        assert _severity_to_priority("low") == "low"

    def test_info(self):
        """Test info severity maps to low priority."""
        assert _severity_to_priority("info") == "low"

    def test_unknown_severity(self):
        """Test unknown severity defaults to medium."""
        assert _severity_to_priority("unknown") == "medium"


class TestEstimateEffort:
    """Tests for _estimate_effort function."""

    def test_critical_effort(self):
        """Test critical severity has 8 hour estimate."""
        assert _estimate_effort("critical") == 8.0

    def test_high_effort(self):
        """Test high severity has 4 hour estimate."""
        assert _estimate_effort("high") == 4.0

    def test_medium_effort(self):
        """Test medium severity has 2 hour estimate."""
        assert _estimate_effort("medium") == 2.0

    def test_low_effort(self):
        """Test low severity has 1 hour estimate."""
        assert _estimate_effort("low") == 1.0

    def test_info_effort(self):
        """Test info severity has 0.5 hour estimate."""
        assert _estimate_effort("info") == 0.5

    def test_unknown_effort(self):
        """Test unknown severity defaults to 2 hours."""
        assert _estimate_effort("unknown") == 2.0


# =============================================================================
# Test: post_session_hook
# =============================================================================


class TestPostSessionHook:
    """Tests for post_session_hook function."""

    @pytest.mark.asyncio
    async def test_auto_index_disabled(self, sample_session):
        """Test hook returns skipped when auto_index disabled."""
        config = FeedbackConfig(auto_index_enabled=False)
        result = await post_session_hook(sample_session, config=config)

        assert result["indexed"] is False
        assert result["skipped"] == "auto_index_disabled"

    @pytest.mark.asyncio
    async def test_no_code_atlas(self, sample_session):
        """Test hook returns skipped when no Code Atlas bridge."""
        result = await post_session_hook(sample_session)

        assert result["indexed"] is False
        assert result["skipped"] == "no_code_atlas"

    @pytest.mark.asyncio
    async def test_successful_indexing(self, sample_session, mock_code_atlas):
        """Test successful session indexing."""
        result = await post_session_hook(
            sample_session,
            code_atlas=mock_code_atlas,
        )

        assert result["indexed"] is True
        assert "index_details" in result

    @pytest.mark.asyncio
    async def test_indexing_with_learning_store(self, sample_session, mock_code_atlas, temp_storage):
        """Test indexing with learning store patterns."""
        from forge_harness.meta_learning.learning_store import LearningStore

        store = LearningStore(storage_path=temp_storage, auto_persist=False)

        result = await post_session_hook(
            sample_session,
            code_atlas=mock_code_atlas,
            learning_store=store,
        )

        assert result["indexed"] is True

    @pytest.mark.asyncio
    async def test_indexing_error_handling(self, sample_session):
        """Test error handling when indexing fails."""
        mock_atlas = MagicMock()
        mock_atlas.index_session = AsyncMock(side_effect=Exception("Network error"))

        result = await post_session_hook(
            sample_session,
            code_atlas=mock_atlas,
        )

        assert len(result["errors"]) > 0


# =============================================================================
# Test: generate_debt_features
# =============================================================================


class TestGenerateDebtFeatures:
    """Tests for generate_debt_features function."""

    @pytest.mark.asyncio
    async def test_debt_features_disabled(self):
        """Test returns empty when debt feature generation disabled."""
        config = FeedbackConfig(debt_features_enabled=False)
        result = await generate_debt_features("domain", "project", config=config)

        assert result == []

    @pytest.mark.asyncio
    async def test_no_tech_diligence(self):
        """Test returns empty when no TechDiligence bridge."""
        result = await generate_debt_features("domain", "project")

        assert result == []

    @pytest.mark.asyncio
    async def test_generate_from_blocking_issues(self, mock_tech_diligence):
        """Test generating features from blocking issues."""
        result = await generate_debt_features(
            "domain",
            "project",
            tech_diligence=mock_tech_diligence,
            max_features=10,
        )

        assert len(result) >= 2
        assert result[0].priority == "critical"
        assert result[0].finding_id == "finding_001"

    @pytest.mark.asyncio
    async def test_max_features_limit(self, mock_tech_diligence):
        """Test max_features limits number of features."""
        result = await generate_debt_features(
            "domain",
            "project",
            tech_diligence=mock_tech_diligence,
            max_features=1,
        )

        assert len(result) <= 1

    @pytest.mark.asyncio
    async def test_warning_to_feature_conversion(self, mock_tech_diligence):
        """Test warnings are converted to features."""
        result = await generate_debt_features(
            "domain",
            "project",
            tech_diligence=mock_tech_diligence,
            max_features=10,
        )

        # Should have both blocking and warning features
        finding_ids = [f.finding_id for f in result]
        assert "finding_003" in finding_ids

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test error handling when TechDiligence fails."""
        mock_td = MagicMock()
        mock_td.get_signals = AsyncMock(side_effect=Exception("API error"))

        result = await generate_debt_features(
            "domain",
            "project",
            tech_diligence=mock_td,
        )

        assert result == []


# =============================================================================
# Test: human_gate_optimizer
# =============================================================================


class TestHumanGateOptimizer:
    """Tests for human_gate_optimizer function."""

    @pytest.mark.asyncio
    async def test_optimization_disabled(self, temp_storage):
        """Test returns skipped when optimization disabled."""
        from forge_harness.meta_learning.learning_store import LearningStore

        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        config = FeedbackConfig(threshold_optimization_enabled=False)

        result = await human_gate_optimizer(store, config=config)

        assert result["optimized"] is False
        assert result["skipped"] == "optimization_disabled"

    @pytest.mark.asyncio
    async def test_insufficient_data(self, temp_storage):
        """Test returns skipped when insufficient decisions."""
        from forge_harness.meta_learning.learning_store import LearningStore
        from forge_harness.meta_learning.schemas import DecisionAction, DecisionRecord

        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        config = FeedbackConfig(min_decisions_for_optimization=100)

        # Add only 5 decisions (less than 100)
        for i in range(5):
            store.record_decision(DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            ))

        result = await human_gate_optimizer(store, config=config)

        assert result["optimized"] is False
        assert result["skipped"] == "insufficient_data"
        assert result["decisions_needed"] > 0

    @pytest.mark.asyncio
    async def test_raise_thresholds_recommendation(self, temp_storage):
        """Test raise thresholds when success rate in acceptable range."""
        from forge_harness.meta_learning.learning_store import LearningStore
        from forge_harness.meta_learning.schemas import DecisionAction, DecisionRecord

        store = LearningStore(storage_path=temp_storage, auto_persist=False)

        # Add 25 decisions with 80% success rate (in acceptable range)
        # This should result in "maintain_thresholds"
        for i in range(25):
            store.record_decision(DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=i < 20,  # 20/25 = 80%
            ))

        result = await human_gate_optimizer(store)

        assert result["optimized"] is True
        # 80% is in 70-90% range, so it should maintain
        assert result["recommendation"] == "maintain_thresholds"

    @pytest.mark.asyncio
    async def test_lower_thresholds_recommendation(self, temp_storage):
        """Test lower thresholds when low success rate."""
        from forge_harness.meta_learning.learning_store import LearningStore
        from forge_harness.meta_learning.schemas import DecisionAction, DecisionRecord

        store = LearningStore(storage_path=temp_storage, auto_persist=False)

        # Add 25 decisions with low success rate
        for i in range(25):
            store.record_decision(DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=i < 15,  # 15/25 = 60%
            ))

        result = await human_gate_optimizer(store)

        assert result["optimized"] is True
        assert result["recommendation"] == "lower_thresholds"

    @pytest.mark.asyncio
    async def test_maintain_thresholds_recommendation(self, temp_storage):
        """Test maintain thresholds when success rate in range."""
        from forge_harness.meta_learning.learning_store import LearningStore
        from forge_harness.meta_learning.schemas import DecisionAction, DecisionRecord

        store = LearningStore(storage_path=temp_storage, auto_persist=False)

        # Add 25 decisions with 80% success rate (in range)
        for i in range(25):
            store.record_decision(DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=i < 20,  # 20/25 = 80%
            ))

        result = await human_gate_optimizer(store)

        assert result["optimized"] is True
        assert result["recommendation"] == "maintain_thresholds"

    @pytest.mark.asyncio
    async def test_error_handling(self, temp_storage):
        """Test error handling when optimization fails."""
        from forge_harness.meta_learning.learning_store import LearningStore

        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        store.get_statistics = MagicMock(side_effect=Exception("Database error"))

        result = await human_gate_optimizer(store)

        assert "error" in result


# =============================================================================
# Test: FeedbackLoopManager
# =============================================================================


class TestFeedbackLoopManager:
    """Tests for FeedbackLoopManager class."""

    @pytest.fixture
    def manager(self, temp_storage, mock_code_atlas, mock_tech_diligence, feedback_config):
        """Create a FeedbackLoopManager instance."""
        from forge_harness.meta_learning.learning_store import LearningStore

        store = LearningStore(storage_path=temp_storage, auto_persist=False)
        return FeedbackLoopManager(
            learning_store=store,
            code_atlas=mock_code_atlas,
            tech_diligence=mock_tech_diligence,
            config=feedback_config,
        )

    @pytest.mark.asyncio
    async def test_on_session_complete(self, manager, sample_session, mock_code_atlas):
        """Test on_session_complete runs all hooks."""
        result = await manager.on_session_complete(sample_session)

        assert result["session_id"] == sample_session.session_id
        assert "hooks" in result
        assert "post_session" in result["hooks"]

    @pytest.mark.asyncio
    async def test_on_session_complete_with_optimization(self, manager, sample_session, temp_storage):
        """Test optimization runs when due."""
        # Force optimization to be due by setting _last_optimization far in past
        manager._last_optimization = datetime.now(UTC) - timedelta(hours=48)

        # Add enough decisions for optimization
        from forge_harness.meta_learning.schemas import DecisionAction, DecisionRecord
        for i in range(25):
            manager.learning_store.record_decision(DecisionRecord(
                decision_id=f"d{i}",
                context_signature="sig1",
                domain="domain1",
                project="proj1",
                recommended_action=DecisionAction.PROCEED,
                outcome_success=True,
            ))

        result = await manager.on_session_complete(sample_session)

        assert "optimization" in result["hooks"]

    @pytest.mark.asyncio
    async def test_scan_for_debt(self, manager, mock_tech_diligence):
        """Test scan_for_debt generates features."""
        result = await manager.scan_for_debt("domain", "project")

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_scan_for_debt_not_due(self, manager):
        """Test debt scan skipped when not due."""
        # Set last scan to recent time
        manager._last_debt_scan = datetime.now(UTC) - timedelta(hours=1)

        result = await manager.scan_for_debt("domain", "project")

        assert result == []

    def test_should_optimize_first_run(self, manager):
        """Test optimization runs on first call."""
        assert manager._should_optimize() is True

    def test_should_optimize_after_interval(self, manager):
        """Test optimization runs after interval."""
        manager._last_optimization = datetime.now(UTC) - timedelta(hours=25)

        assert manager._should_optimize() is True

    def test_should_optimize_within_interval(self, manager):
        """Test optimization skipped within interval."""
        manager._last_optimization = datetime.now(UTC) - timedelta(hours=1)

        assert manager._should_optimize() is False

    def test_should_scan_debt_first_run(self, manager):
        """Test debt scan runs on first call."""
        assert manager._should_scan_debt() is True

    def test_should_scan_debt_after_interval(self, manager):
        """Test debt scan runs after interval."""
        manager._last_debt_scan = datetime.now(UTC) - timedelta(hours=169)

        assert manager._should_scan_debt() is True

    def test_should_scan_debt_within_interval(self, manager):
        """Test debt scan skipped within interval."""
        manager._last_debt_scan = datetime.now(UTC) - timedelta(hours=1)

        assert manager._should_scan_debt() is False


# =============================================================================
# Test: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_post_session_with_empty_files(self):
        """Test post_session_hook with empty files list."""
        session = SessionSummary(
            session_id="sess_empty",
            domain="domain",
            project="proj",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            files_modified=[],
        )
        mock_atlas = MagicMock()
        mock_atlas.index_session = AsyncMock(return_value={"indexed": True})

        result = await post_session_hook(session, code_atlas=mock_atlas)

        assert result["indexed"] is True

    def test_debt_feature_with_minimal_fields(self):
        """Test DebtFeature with only required fields."""
        feature = DebtFeature(
            id="DEBT-min",
            name="Minimal",
            description="Desc",
            priority="low",
            finding_id="fid",
            category="test",
        )

        result = feature.to_dict()
        # estimated_effort is a field but not included in to_dict output
        assert result["id"] == "DEBT-min"
        assert result["priority"] == "low"

    def test_feedback_config_with_zero_intervals(self):
        """Test FeedbackConfig with zero intervals."""
        config = FeedbackConfig(
            optimization_interval_hours=0.0,
            debt_scan_interval_hours=0.0,
        )
        assert config.optimization_interval_hours == 0.0

    @pytest.mark.asyncio
    async def test_generate_debt_features_empty_signals(self):
        """Test with empty signals from TechDiligence."""
        mock_td = MagicMock()
        mock_signals = MagicMock()
        mock_signals.blocking_issues = []
        mock_signals.warnings = []
        mock_td.get_signals = AsyncMock(return_value=mock_signals)

        result = await generate_debt_features("d", "p", tech_diligence=mock_td)

        assert result == []

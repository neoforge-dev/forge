"""
Tests for Continuous Ralph Runner
===================================

Tests for forge_harness.continuous_runner module.
Covers initialization, lifecycle management, approval integration,
notification handling, and graceful shutdown.
"""

import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.continuous_runner import ContinuousRalphRunner
from forge_harness.meta_learning.schemas import DecisionTier


@pytest.fixture
def temp_forge_root():
    """Create a temporary forge root directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create domain/project structure
        project_path = root / "brandfocus-ai" / "voice-coach"
        project_path.mkdir(parents=True, exist_ok=True)

        # Create features.json
        features_file = project_path / "features.json"
        features_file.write_text('{"features": []}')

        yield root


@pytest.fixture
def mock_approval_queue():
    """Mock ApprovalQueueHarness."""
    queue = AsyncMock()
    queue.create_request = AsyncMock()
    queue.get_request = AsyncMock()
    return queue


@pytest.fixture
def mock_notification_harness():
    """Mock NotificationHarness."""
    notifier = AsyncMock()
    notifier.send_approval_notification = AsyncMock()
    return notifier


@pytest.fixture
def mock_decision_engine():
    """Mock DecisionEngine."""
    engine = Mock()
    engine.classify_tier = Mock(return_value=DecisionTier.PHONE)
    return engine


@pytest.fixture
def mock_ralph_loop():
    """Mock RalphLoopHarness."""
    loop = AsyncMock()
    result = Mock()
    result.success = True
    result.iterations = 10
    result.features_completed = 2
    result.features_blocked = 0
    result.features_remaining = 3
    result.duration_seconds = 120.0
    loop.run = AsyncMock(return_value=result)
    return loop


# =============================================================================
# Initialization Tests
# =============================================================================


class TestContinuousRalphRunnerInit:
    """Tests for ContinuousRalphRunner initialization."""

    def test_init_minimal_params(self, temp_forge_root):
        """Test initialization with minimal required parameters."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        assert runner.domain == "brandfocus-ai"
        assert runner.project == "voice-coach"
        assert runner.forge_root == temp_forge_root
        assert (
            runner.features_path
            == temp_forge_root / "brandfocus-ai" / "voice-coach" / "features.json"
        )
        assert runner.approval_timeout == timedelta(hours=24.0)
        assert runner.loop_cooldown == 60.0
        assert runner.max_iterations_per_run == 50
        assert runner._running is False
        assert runner._current_feature is None

    def test_init_with_custom_features_path(self, temp_forge_root):
        """Test initialization with custom features path."""
        custom_path = temp_forge_root / "custom" / "features.json"
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        custom_path.write_text('{"features": []}')

        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            features_path=custom_path,
        )

        assert runner.features_path == custom_path

    def test_init_with_custom_approval_timeout(self, temp_forge_root):
        """Test initialization with custom approval timeout."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=48.0,
        )

        assert runner.approval_timeout == timedelta(hours=48.0)

    def test_init_with_custom_loop_cooldown(self, temp_forge_root):
        """Test initialization with custom loop cooldown."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=120.0,
        )

        assert runner.loop_cooldown == 120.0

    def test_init_with_custom_max_iterations(self, temp_forge_root):
        """Test initialization with custom max iterations."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            max_iterations_per_run=100,
        )

        assert runner.max_iterations_per_run == 100

    def test_init_all_custom_params(self, temp_forge_root):
        """Test initialization with all custom parameters."""
        custom_path = temp_forge_root / "custom" / "features.json"
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        custom_path.write_text('{"features": []}')

        runner = ContinuousRalphRunner(
            domain="codeswiftr-com",
            project="interview-simulator",
            forge_root=temp_forge_root,
            features_path=custom_path,
            approval_timeout_hours=12.0,
            loop_cooldown_seconds=30.0,
            max_iterations_per_run=25,
        )

        assert runner.domain == "codeswiftr-com"
        assert runner.project == "interview-simulator"
        assert runner.features_path == custom_path
        assert runner.approval_timeout == timedelta(hours=12.0)
        assert runner.loop_cooldown == 30.0
        assert runner.max_iterations_per_run == 25

    def test_init_forge_root_defaults_to_cwd(self):
        """Test that forge_root defaults to current directory."""
        runner = ContinuousRalphRunner(
            domain="test-domain",
            project="test-project",
        )

        assert runner.forge_root == Path.cwd()

    def test_init_lazy_component_initialization(self, temp_forge_root):
        """Test that components are lazily initialized."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        # Components should be None until accessed
        assert runner._approval_queue is None
        assert runner._notification_harness is None
        assert runner._decision_engine is None

    def test_init_shutdown_event_not_set(self, temp_forge_root):
        """Test that shutdown event is not set on init."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        assert not runner._shutdown_event.is_set()


# =============================================================================
# Component Accessor Tests
# =============================================================================


class TestComponentAccessors:
    """Tests for lazy component initialization accessors."""

    @pytest.mark.asyncio
    async def test_get_approval_queue_creates_instance(self, temp_forge_root):
        """Test that _get_approval_queue creates queue on first call."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.create_approval_queue_from_env") as mock_create:
            mock_queue = AsyncMock()
            mock_create.return_value = mock_queue

            queue = await runner._get_approval_queue()

            assert queue is mock_queue
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_approval_queue_returns_cached_instance(self, temp_forge_root):
        """Test that _get_approval_queue returns cached instance."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.create_approval_queue_from_env") as mock_create:
            mock_queue = AsyncMock()
            mock_create.return_value = mock_queue

            queue1 = await runner._get_approval_queue()
            queue2 = await runner._get_approval_queue()

            assert queue1 is queue2
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_notification_harness_creates_instance(self, temp_forge_root):
        """Test that _get_notification_harness creates notifier on first call."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.create_notification_harness") as mock_create:
            mock_notifier = AsyncMock()
            mock_create.return_value = mock_notifier

            notifier = await runner._get_notification_harness()

            assert notifier is mock_notifier
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_notification_harness_returns_cached_instance(self, temp_forge_root):
        """Test that _get_notification_harness returns cached instance."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.create_notification_harness") as mock_create:
            mock_notifier = AsyncMock()
            mock_create.return_value = mock_notifier

            notifier1 = await runner._get_notification_harness()
            notifier2 = await runner._get_notification_harness()

            assert notifier1 is notifier2
            mock_create.assert_called_once()

    def test_get_decision_engine_creates_instance(self, temp_forge_root):
        """Test that _get_decision_engine creates engine on first call."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.DecisionEngine") as mock_class:
            mock_engine = Mock()
            mock_class.return_value = mock_engine

            engine = runner._get_decision_engine()

            assert engine is mock_engine
            mock_class.assert_called_once()

    def test_get_decision_engine_returns_cached_instance(self, temp_forge_root):
        """Test that _get_decision_engine returns cached instance."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.DecisionEngine") as mock_class:
            mock_engine = Mock()
            mock_class.return_value = mock_engine

            engine1 = runner._get_decision_engine()
            engine2 = runner._get_decision_engine()

            assert engine1 is engine2
            mock_class.assert_called_once()


# =============================================================================
# Tier Classification Tests
# =============================================================================


class TestTierClassification:
    """Tests for _classify_tier method."""

    def test_classify_tier_with_low_risk(self, temp_forge_root, mock_decision_engine):
        """Test tier classification with low risk score."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        mock_decision_engine.classify_tier.return_value = DecisionTier.WATCH

        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Add button",
            action_type="feature_complete",
            risk_score=0.1,
        )

        assert tier == "watch"
        mock_decision_engine.classify_tier.assert_called_once()
        call_args = mock_decision_engine.classify_tier.call_args
        # classify_tier(action, scores, context, diligence)
        assert call_args[0][1].risk_score == 0.1
        assert call_args[0][2].domain == "brandfocus-ai"
        assert call_args[0][2].project == "voice-coach"
        assert call_args[0][2].feature_id == "feat-001"

    def test_classify_tier_with_medium_risk(self, temp_forge_root, mock_decision_engine):
        """Test tier classification with medium risk score."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        mock_decision_engine.classify_tier.return_value = DecisionTier.PHONE

        tier = runner._classify_tier(
            feature_id="feat-002",
            feature_name="Deploy to staging",
            action_type="deploy",
            risk_score=0.4,
        )

        assert tier == "phone"
        call_args = mock_decision_engine.classify_tier.call_args
        assert call_args[0][1].risk_score == 0.4
        assert call_args[0][3].risk_level == "medium"

    def test_classify_tier_with_high_risk(self, temp_forge_root, mock_decision_engine):
        """Test tier classification with high risk score."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        mock_decision_engine.classify_tier.return_value = DecisionTier.DESKTOP

        tier = runner._classify_tier(
            feature_id="feat-003",
            feature_name="Production deploy",
            action_type="deploy",
            risk_score=0.8,
        )

        assert tier == "desktop"
        call_args = mock_decision_engine.classify_tier.call_args
        assert call_args[0][1].risk_score == 0.8
        assert call_args[0][3].risk_level == "high"

    def test_classify_tier_action_type_mapping(self, temp_forge_root, mock_decision_engine):
        """Test that action types are correctly mapped to DecisionAction."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        mock_decision_engine.classify_tier.return_value = DecisionTier.PHONE

        # Test each action type
        for action_type in ["feature_complete", "deploy", "test_retry", "blocked"]:
            runner._classify_tier(
                feature_id="feat-001",
                feature_name="Test",
                action_type=action_type,
                risk_score=0.2,
            )

        assert mock_decision_engine.classify_tier.call_count == 4

    def test_classify_tier_with_unknown_action_type(self, temp_forge_root, mock_decision_engine):
        """Test tier classification with unknown action type defaults correctly."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        mock_decision_engine.classify_tier.return_value = DecisionTier.PHONE

        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Unknown action",
            action_type="unknown_action",
            risk_score=0.2,
        )

        assert tier == "phone"


# =============================================================================
# Approval Request Tests
# =============================================================================


class TestApprovalRequests:
    """Tests for _request_human_approval method."""

    @pytest.mark.asyncio
    async def test_request_human_approval_approved(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test approval request that gets approved."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        # Mock approval request creation
        mock_request = ApprovalRequest(
            id="apr_test123",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test Approval",
            description="Test description",
        )
        mock_approval_queue.create_request.return_value = mock_request

        # Mock approval status check - approved immediately
        approved_request = ApprovalRequest(
            id="apr_test123",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test Approval",
            description="Test description",
            status=ApprovalStatus.APPROVED,
        )
        mock_approval_queue.get_request.return_value = approved_request

        result = await runner._request_human_approval(
            title="Test Approval",
            description="Test description",
            approval_type=ApprovalType.FEATURE,
            feature_id="feat-001",
            risk_score=0.2,
        )

        assert result is True
        mock_approval_queue.create_request.assert_called_once()
        mock_notification_harness.send_approval_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_human_approval_rejected(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test approval request that gets rejected."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=0.001,  # Very short timeout for testing
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_request = ApprovalRequest(
            id="apr_test456",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test Approval",
            description="Test description",
        )
        mock_approval_queue.create_request.return_value = mock_request

        # Mock approval status check - rejected
        rejected_request = ApprovalRequest(
            id="apr_test456",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test Approval",
            description="Test description",
            status=ApprovalStatus.REJECTED,
        )
        mock_approval_queue.get_request.return_value = rejected_request

        result = await runner._request_human_approval(
            title="Test Approval",
            description="Test description",
            approval_type=ApprovalType.FEATURE,
            feature_id="feat-002",
            risk_score=0.3,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_request_human_approval_timeout(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test approval request that times out."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=0.001,  # Very short timeout
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_request = ApprovalRequest(
            id="apr_test789",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test Approval",
            description="Test description",
        )
        mock_approval_queue.create_request.return_value = mock_request

        # Mock approval status check - still pending
        pending_request = ApprovalRequest(
            id="apr_test789",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test Approval",
            description="Test description",
            status=ApprovalStatus.PENDING,
        )
        mock_approval_queue.get_request.return_value = pending_request

        result = await runner._request_human_approval(
            title="Test Approval",
            description="Test description",
            approval_type=ApprovalType.FEATURE,
            feature_id="feat-003",
            risk_score=0.4,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_request_human_approval_with_context(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test approval request includes context in metadata."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_request = ApprovalRequest(
            id="apr_context",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
        )
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = ApprovalRequest(
            id="apr_context",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
            status=ApprovalStatus.APPROVED,
        )

        context = {"completed": 5, "remaining": 3}
        await runner._request_human_approval(
            title="Test",
            description="Test",
            approval_type=ApprovalType.FEATURE,
            context=context,
        )

        call_args = mock_approval_queue.create_request.call_args
        assert call_args[1]["metadata"] == context

    @pytest.mark.asyncio
    async def test_request_human_approval_with_dashboard_url(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test approval request includes dashboard URL in notification."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_request = ApprovalRequest(
            id="apr_url",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
        )
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = ApprovalRequest(
            id="apr_url",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
            status=ApprovalStatus.APPROVED,
        )

        with patch.dict("os.environ", {"FORGE_DASHBOARD_URL": "https://example.com"}):
            await runner._request_human_approval(
                title="Test",
                description="Test",
                approval_type=ApprovalType.FEATURE,
            )

        call_args = mock_notification_harness.send_approval_notification.call_args
        assert call_args[1]["dashboard_url"] == "https://example.com/approvals/apr_url"

    @pytest.mark.asyncio
    async def test_request_human_approval_shutdown_during_wait(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test approval request returns False when shutdown is triggered."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_request = ApprovalRequest(
            id="apr_shutdown",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
        )
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = ApprovalRequest(
            id="apr_shutdown",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )

        # Trigger shutdown
        runner._shutdown_event.set()

        result = await runner._request_human_approval(
            title="Test",
            description="Test",
            approval_type=ApprovalType.FEATURE,
        )

        assert result is False


# =============================================================================
# Single Loop Execution Tests
# =============================================================================


class TestSingleLoopExecution:
    """Tests for _run_single_loop method."""

    @pytest.mark.asyncio
    async def test_run_single_loop_success(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test successful single loop execution."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph_class:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 10
            mock_result.features_completed = 2
            mock_result.features_blocked = 0
            mock_result.features_remaining = 3
            mock_result.duration_seconds = 120.0
            mock_loop.run.return_value = mock_result
            mock_ralph_class.return_value = mock_loop

            # Mock approval as approved
            mock_request = ApprovalRequest(
                id="apr_loop",
                type=ApprovalType.FEATURE,
                domain="brandfocus-ai",
                title="Test",
                description="Test",
                status=ApprovalStatus.APPROVED,
            )
            mock_approval_queue.create_request.return_value = mock_request
            mock_approval_queue.get_request.return_value = mock_request

            should_continue = await runner._run_single_loop()

            assert should_continue is True
            mock_loop.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_single_loop_features_file_not_found(self, temp_forge_root):
        """Test single loop execution when features file doesn't exist."""
        runner = ContinuousRalphRunner(
            domain="nonexistent",
            project="project",
            forge_root=temp_forge_root,
        )

        should_continue = await runner._run_single_loop()

        assert should_continue is False

    @pytest.mark.asyncio
    async def test_run_single_loop_no_features_remaining(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test single loop execution when no features remain."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph_class:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 5
            mock_result.features_completed = 2
            mock_result.features_blocked = 0
            mock_result.features_remaining = 0
            mock_result.duration_seconds = 60.0
            mock_loop.run.return_value = mock_result
            mock_ralph_class.return_value = mock_loop

            # Mock approval as approved
            mock_request = ApprovalRequest(
                id="apr_done",
                type=ApprovalType.FEATURE,
                domain="brandfocus-ai",
                title="Test",
                description="Test",
                status=ApprovalStatus.APPROVED,
            )
            mock_approval_queue.create_request.return_value = mock_request
            mock_approval_queue.get_request.return_value = mock_request

            should_continue = await runner._run_single_loop()

            assert should_continue is False

    @pytest.mark.asyncio
    async def test_run_single_loop_approval_rejected(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test single loop execution when approval is rejected."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=0.001,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph_class:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 10
            mock_result.features_completed = 2
            mock_result.features_blocked = 0
            mock_result.features_remaining = 3
            mock_result.duration_seconds = 120.0
            mock_loop.run.return_value = mock_result
            mock_ralph_class.return_value = mock_loop

            # Mock approval as rejected
            mock_request = ApprovalRequest(
                id="apr_rejected",
                type=ApprovalType.FEATURE,
                domain="brandfocus-ai",
                title="Test",
                description="Test",
                status=ApprovalStatus.REJECTED,
            )
            mock_approval_queue.create_request.return_value = mock_request
            mock_approval_queue.get_request.return_value = mock_request

            should_continue = await runner._run_single_loop()

            assert should_continue is False

    @pytest.mark.asyncio
    async def test_run_single_loop_with_blocked_features(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test single loop execution with blocked features."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph_class:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 10
            mock_result.features_completed = 1
            mock_result.features_blocked = 2
            mock_result.features_remaining = 3
            mock_result.duration_seconds = 120.0
            mock_loop.run.return_value = mock_result
            mock_ralph_class.return_value = mock_loop

            # Mock approvals as approved
            mock_request = ApprovalRequest(
                id="apr_blocked",
                type=ApprovalType.FEATURE,
                domain="brandfocus-ai",
                title="Test",
                description="Test",
                status=ApprovalStatus.APPROVED,
            )
            mock_approval_queue.create_request.return_value = mock_request
            mock_approval_queue.get_request.return_value = mock_request

            should_continue = await runner._run_single_loop()

            # Should continue even with blocked features
            assert should_continue is True
            # Should have requested approval twice (completed + blocked)
            assert mock_approval_queue.create_request.call_count == 2


# =============================================================================
# Main Run Loop Tests
# =============================================================================


class TestMainRunLoop:
    """Tests for main run() method."""

    @pytest.mark.asyncio
    async def test_run_sets_running_flag(self, temp_forge_root):
        """Test that run() sets the _running flag."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.01,
        )

        async def mock_run_single_loop():
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            await runner.run()

            # _running should be False after run completes
            assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_installs_signal_handlers(self, temp_forge_root):
        """Test that run() installs signal handlers."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.01,
        )

        async def mock_run_single_loop():
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            with patch("asyncio.get_event_loop") as mock_get_loop:
                mock_loop_instance = Mock()
                mock_get_loop.return_value = mock_loop_instance

                await runner.run()

                # Should have added signal handlers for SIGTERM and SIGINT
                assert mock_loop_instance.add_signal_handler.call_count == 2

    @pytest.mark.asyncio
    async def test_run_continues_on_exception(self, temp_forge_root):
        """Test that run() continues after non-critical exception."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.01,
        )

        call_count = 0

        async def mock_run_single_loop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Test error")
            runner._shutdown_event.set()  # Stop after second call
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            await runner.run()

            # Should have been called twice despite exception
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_respects_shutdown_event(self, temp_forge_root):
        """Test that run() stops when shutdown event is set."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        call_count = 0

        async def mock_run_single_loop():
            nonlocal call_count
            call_count += 1
            runner._shutdown_event.set()
            return True  # Would continue if not for shutdown

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            await runner.run()

            # Should have been called once then stopped
            assert call_count == 1

    @pytest.mark.asyncio
    async def test_run_handles_cancelled_error(self, temp_forge_root):
        """Test that run() handles CancelledError gracefully."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        async def mock_run_single_loop():
            raise asyncio.CancelledError()

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            await runner.run()

            # Should have stopped gracefully
            assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_cooldown_between_iterations(self, temp_forge_root):
        """Test that run() applies cooldown between successful iterations."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.1,
        )

        call_count = 0

        async def mock_run_single_loop():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                runner._shutdown_event.set()
            return True  # Continue

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await runner.run()

                # Should have slept once (between iterations)
                mock_sleep.assert_called_with(0.1)

    @pytest.mark.asyncio
    async def test_run_idle_state_timeout(self, temp_forge_root):
        """Test that run() waits in idle state when no work."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.1,
        )

        async def mock_run_single_loop():
            runner._shutdown_event.set()  # Trigger shutdown to exit
            return False  # No more work

        with patch.object(runner, "_run_single_loop", side_effect=mock_run_single_loop):
            with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
                mock_wait.side_effect = TimeoutError()  # Simulate timeout

                await runner.run()

                # Should have called wait_for with correct timeout
                mock_wait.assert_called_once()


# =============================================================================
# Shutdown Handler Tests
# =============================================================================


class TestShutdownHandler:
    """Tests for _handle_shutdown method."""

    def test_handle_shutdown_sets_running_false(self, temp_forge_root):
        """Test that _handle_shutdown sets _running to False."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._running = True

        runner._handle_shutdown()

        assert runner._running is False

    def test_handle_shutdown_sets_shutdown_event(self, temp_forge_root):
        """Test that _handle_shutdown sets shutdown event."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        runner._handle_shutdown()

        assert runner._shutdown_event.is_set()

    def test_handle_shutdown_can_be_called_multiple_times(self, temp_forge_root):
        """Test that _handle_shutdown is idempotent."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._running = True

        runner._handle_shutdown()
        runner._handle_shutdown()
        runner._handle_shutdown()

        assert runner._running is False
        assert runner._shutdown_event.is_set()


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for ContinuousRalphRunner."""

    @pytest.mark.asyncio
    async def test_full_workflow_single_iteration(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test complete workflow with single iteration."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.01,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph_class:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 10
            mock_result.features_completed = 2
            mock_result.features_blocked = 0
            mock_result.features_remaining = 0  # No more work
            mock_result.duration_seconds = 120.0
            mock_loop.run.return_value = mock_result
            mock_ralph_class.return_value = mock_loop

            # Mock approval as approved
            mock_request = ApprovalRequest(
                id="apr_integration",
                type=ApprovalType.FEATURE,
                domain="brandfocus-ai",
                title="Test",
                description="Test",
                status=ApprovalStatus.APPROVED,
            )
            mock_approval_queue.create_request.return_value = mock_request
            mock_approval_queue.get_request.return_value = mock_request

            # Trigger shutdown after first check
            async def trigger_shutdown(*args, **kwargs):
                runner._shutdown_event.set()
                raise TimeoutError()

            with patch("asyncio.wait_for", side_effect=trigger_shutdown):
                await runner.run()

            # Verify Ralph loop was run
            mock_loop.run.assert_called_once()
            # Verify approval was requested
            mock_approval_queue.create_request.assert_called_once()
            # Verify notification was sent
            mock_notification_harness.send_approval_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_during_approval_wait(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Test graceful shutdown while waiting for approval."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_request = ApprovalRequest(
            id="apr_shutdown_wait",
            type=ApprovalType.FEATURE,
            domain="brandfocus-ai",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = mock_request

        # Simulate shutdown signal during approval wait
        async def delayed_shutdown():
            await asyncio.sleep(0.1)
            runner._handle_shutdown()

        shutdown_task = asyncio.create_task(delayed_shutdown())

        result = await runner._request_human_approval(
            title="Test",
            description="Test",
            approval_type=ApprovalType.FEATURE,
        )

        await shutdown_task

        # Should return False due to shutdown
        assert result is False
        assert runner._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_configuration_parameters_used_correctly(self, temp_forge_root):
        """Test that configuration parameters are used correctly."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=48.0,
            loop_cooldown_seconds=30.0,
            max_iterations_per_run=100,
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph_class:
            with patch("forge_harness.continuous_runner.runner.FeatureStore"):
                with patch("forge_harness.continuous_runner.runner.LoopConfig") as mock_config_class:
                    mock_loop = AsyncMock()
                    mock_result = Mock()
                    mock_result.success = True
                    mock_result.features_completed = 0
                    mock_result.features_blocked = 0
                    mock_result.features_remaining = 0
                    mock_result.duration_seconds = 60.0
                    mock_loop.run.return_value = mock_result
                    mock_ralph_class.return_value = mock_loop

                    await runner._run_single_loop()

                    # Verify LoopConfig was created with correct max_iterations
                    call_args = mock_config_class.call_args
                    assert call_args[1]["max_iterations"] == 100


# =============================================================================
# main() Entry-Point Tests
# =============================================================================


class TestMain:
    """Tests for main() entry point function."""

    def test_main_exits_when_domain_missing(self, monkeypatch):
        """Test main() exits with code 1 when FORGE_DOMAIN is not set."""
        from forge_harness.continuous_runner import main

        monkeypatch.delenv("FORGE_DOMAIN", raising=False)
        monkeypatch.delenv("FORGE_PROJECT", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_exits_when_project_missing(self, monkeypatch):
        """Test main() exits with code 1 when FORGE_PROJECT is not set."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.delenv("FORGE_PROJECT", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_exits_when_domain_empty_string(self, monkeypatch):
        """Test main() exits with code 1 when FORGE_DOMAIN is empty."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_exits_when_project_empty_string(self, monkeypatch):
        """Test main() exits with code 1 when FORGE_PROJECT is empty."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_creates_runner_and_runs(self, monkeypatch):
        """Test main() creates ContinuousRalphRunner and calls asyncio.run."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.asyncio.run") as mock_run:
            main()

        mock_run.assert_called_once()

    def test_main_uses_default_config_values(self, monkeypatch):
        """Test main() uses default values when optional env vars absent."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_runner_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_runner_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_runner_cls.call_args.kwargs
        assert kwargs["approval_timeout_hours"] == 24.0
        assert kwargs["loop_cooldown_seconds"] == 60.0
        assert kwargs["max_iterations_per_run"] == 50
        assert kwargs["features_path"] is None

    def test_main_uses_custom_config_values(self, monkeypatch):
        """Test main() uses environment-provided configuration values."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "codeswiftr-com")
        monkeypatch.setenv("FORGE_PROJECT", "interview-simulator")
        monkeypatch.setenv("APPROVAL_TIMEOUT_HOURS", "12")
        monkeypatch.setenv("LOOP_COOLDOWN_SECONDS", "30")
        monkeypatch.setenv("MAX_ITERATIONS_PER_RUN", "25")
        monkeypatch.delenv("FEATURES_PATH", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_runner_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_runner_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_runner_cls.call_args.kwargs
        assert mock_runner_cls.call_args.kwargs["domain"] == "codeswiftr-com" or \
               mock_runner_cls.call_args.args[0] == "codeswiftr-com"
        assert kwargs["approval_timeout_hours"] == 12.0
        assert kwargs["loop_cooldown_seconds"] == 30.0
        assert kwargs["max_iterations_per_run"] == 25

    def test_main_passes_features_path_when_set(self, monkeypatch, tmp_path):
        """Test main() passes features_path when FEATURES_PATH env var is set."""
        from forge_harness.continuous_runner import main

        custom_path = tmp_path / "features.json"
        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
        monkeypatch.setenv("FEATURES_PATH", str(custom_path))
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_runner_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_runner_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_runner_cls.call_args.kwargs
        assert kwargs["features_path"] == custom_path

    def test_main_features_path_none_when_not_set(self, monkeypatch):
        """Test main() sets features_path=None when FEATURES_PATH not in env."""
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
        monkeypatch.delenv("FEATURES_PATH", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_runner_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_runner_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_runner_cls.call_args.kwargs
        assert kwargs["features_path"] is None

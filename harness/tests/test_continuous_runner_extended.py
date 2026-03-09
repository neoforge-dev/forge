"""
Extended Tests for Continuous Ralph Runner
===========================================

Covers paths NOT exercised by test_continuous_runner.py:

- Risk-score boundary values for tier classification (0.3, 0.6 edges)
- Action-type tag propagation into DecisionContext
- Approval-type value forwarded to classify_tier
- Approval poll loop: status transitions mid-wait (None → PENDING → APPROVED)
- No dashboard URL → dashboard_url=None in notification
- No feature_id → "unknown" fallback
- _run_single_loop: 0 completed + 0 blocked (no approval at all)
- _run_single_loop: only blocked features, no completed (one approval call)
- _run_single_loop: blocked approval rejected but runner still continues
- _run_single_loop: FeatureStore path, LoopConfig dry_run/checkpoint/timeout values
- run(): _running starts False, becomes True, ends False
- run(): exception sleep is cooldown * 2
- run(): idle TimeoutError → continues loop (not stop)
- run(): multiple successful iterations with cooldown between each
- run(): no-work idle state uses cooldown * 10 as wait_for timeout
- main(): both domain and project empty at same time
- main(): both domain and project present passes project to runner
"""

import asyncio
import signal
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.continuous_runner import ContinuousRalphRunner, main
from forge_harness.meta_learning.schemas import DecisionAction, DecisionTier

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def temp_forge_root():
    """Temporary forge root with a valid brandfocus-ai/voice-coach project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        project_path = root / "brandfocus-ai" / "voice-coach"
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / "features.json").write_text('{"features": []}')
        yield root


@pytest.fixture
def mock_decision_engine():
    engine = Mock()
    engine.classify_tier = Mock(return_value=DecisionTier.PHONE)
    return engine


@pytest.fixture
def mock_approval_queue():
    queue = AsyncMock()
    queue.create_request = AsyncMock()
    queue.get_request = AsyncMock()
    return queue


@pytest.fixture
def mock_notification_harness():
    notifier = AsyncMock()
    notifier.send_approval_notification = AsyncMock()
    return notifier


def _make_approval_request(
    id_: str = "apr_ext",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> ApprovalRequest:
    return ApprovalRequest(
        id=id_,
        type=ApprovalType.FEATURE,
        domain="brandfocus-ai",
        title="Test",
        description="Test",
        status=status,
    )


def _mock_runner(
    temp_forge_root: Path,
    mock_approval_queue: AsyncMock,
    mock_notification_harness: AsyncMock,
    mock_decision_engine: Mock,
    **kwargs,
) -> ContinuousRalphRunner:
    runner = ContinuousRalphRunner(
        domain="brandfocus-ai",
        project="voice-coach",
        forge_root=temp_forge_root,
        **kwargs,
    )
    runner._approval_queue = mock_approval_queue
    runner._notification_harness = mock_notification_harness
    runner._decision_engine = mock_decision_engine
    return runner


# ===========================================================================
# 1. Tier classification — risk-score boundary values
# ===========================================================================


class TestTierClassificationBoundaries:
    """Boundary conditions for _classify_tier risk thresholds."""

    def test_risk_score_exactly_zero_is_low(self, temp_forge_root, mock_decision_engine):
        """risk_score=0.0 → risk_level='low'."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "feature_complete", risk_score=0.0)
        _, _, _, diligence = mock_decision_engine.classify_tier.call_args[0]
        assert diligence.risk_level == "low"

    def test_risk_score_just_below_0_3_is_low(self, temp_forge_root, mock_decision_engine):
        """risk_score=0.299 → risk_level='low'."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "feature_complete", risk_score=0.299)
        _, _, _, diligence = mock_decision_engine.classify_tier.call_args[0]
        assert diligence.risk_level == "low"

    def test_risk_score_exactly_0_3_is_medium(self, temp_forge_root, mock_decision_engine):
        """risk_score=0.3 → risk_level='medium' (boundary is < 0.3 for low)."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "feature_complete", risk_score=0.3)
        _, _, _, diligence = mock_decision_engine.classify_tier.call_args[0]
        assert diligence.risk_level == "medium"

    def test_risk_score_just_below_0_6_is_medium(self, temp_forge_root, mock_decision_engine):
        """risk_score=0.599 → risk_level='medium'."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "deploy", risk_score=0.599)
        _, _, _, diligence = mock_decision_engine.classify_tier.call_args[0]
        assert diligence.risk_level == "medium"

    def test_risk_score_exactly_0_6_is_high(self, temp_forge_root, mock_decision_engine):
        """risk_score=0.6 → risk_level='high'."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "blocked", risk_score=0.6)
        _, _, _, diligence = mock_decision_engine.classify_tier.call_args[0]
        assert diligence.risk_level == "high"

    def test_risk_score_1_0_is_high(self, temp_forge_root, mock_decision_engine):
        """risk_score=1.0 → risk_level='high'."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "deploy", risk_score=1.0)
        _, _, _, diligence = mock_decision_engine.classify_tier.call_args[0]
        assert diligence.risk_level == "high"

    def test_action_type_stored_in_context_tags(self, temp_forge_root, mock_decision_engine):
        """action_type is placed into DecisionContext.tags."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "test_retry", risk_score=0.1)
        _, _, context, _ = mock_decision_engine.classify_tier.call_args[0]
        assert "test_retry" in context.tags

    def test_classify_tier_maps_blocked_to_block_action(self, temp_forge_root, mock_decision_engine):
        """'blocked' action_type maps to DecisionAction.BLOCK."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "blocked", risk_score=0.5)
        action, _, _, _ = mock_decision_engine.classify_tier.call_args[0]
        assert action == DecisionAction.BLOCK

    def test_classify_tier_maps_test_retry_to_proceed_with_caution(
        self, temp_forge_root, mock_decision_engine
    ):
        """'test_retry' maps to DecisionAction.PROCEED_WITH_CAUTION."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        runner._decision_engine = mock_decision_engine
        runner._classify_tier("f1", "n", "test_retry", risk_score=0.1)
        action, _, _, _ = mock_decision_engine.classify_tier.call_args[0]
        assert action == DecisionAction.PROCEED_WITH_CAUTION


# ===========================================================================
# 2. _request_human_approval — additional paths
# ===========================================================================


class TestApprovalRequestAdditionalPaths:
    """Additional paths in _request_human_approval not covered by base tests."""

    @pytest.mark.asyncio
    async def test_approval_type_value_passed_to_notification(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """approval_type.value is forwarded to send_approval_notification."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr1")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr1", ApprovalStatus.APPROVED
        )

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.DEPLOY,
            feature_id="feat-99",
            risk_score=0.7,
        )

        notify_call = mock_notification_harness.send_approval_notification.call_args
        assert notify_call[1]["approval_type"] == "deploy"

    @pytest.mark.asyncio
    async def test_no_feature_id_uses_unknown_fallback(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """When feature_id=None the classify_tier call uses 'unknown'."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr2")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr2", ApprovalStatus.APPROVED
        )

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
            feature_id=None,
        )

        # The tier classification should have been called with feature_id="unknown"
        # We verify by checking the classify_tier received context.feature_id="unknown"
        _, _, context, _ = mock_decision_engine.classify_tier.call_args[0]
        assert context.feature_id == "unknown"

    @pytest.mark.asyncio
    async def test_no_dashboard_url_env_sends_none(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
        monkeypatch,
    ):
        """When FORGE_DASHBOARD_URL is not set, dashboard_url=None is sent."""
        monkeypatch.delenv("FORGE_DASHBOARD_URL", raising=False)
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr3")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr3", ApprovalStatus.APPROVED
        )

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
        )

        notify_call = mock_notification_harness.send_approval_notification.call_args
        assert notify_call[1]["dashboard_url"] is None

    @pytest.mark.asyncio
    async def test_approval_poll_transitions_pending_to_approved(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """Approval poll loop: first call returns PENDING, second APPROVED."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request(
            "apr4", ApprovalStatus.PENDING
        )
        # Sequence: first get → PENDING, second get → APPROVED
        mock_approval_queue.get_request.side_effect = [
            _make_approval_request("apr4", ApprovalStatus.PENDING),
            _make_approval_request("apr4", ApprovalStatus.APPROVED),
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner._request_human_approval(
                title="T",
                description="D",
                approval_type=ApprovalType.FEATURE,
            )

        assert result is True
        assert mock_approval_queue.get_request.call_count == 2

    @pytest.mark.asyncio
    async def test_approval_poll_get_request_returns_none(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """When get_request returns None the loop keeps polling until timeout."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=0.0001,  # instant timeout
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_approval_queue.create_request.return_value = _make_approval_request(
            "apr5", ApprovalStatus.PENDING
        )
        mock_approval_queue.get_request.return_value = None  # not found yet

        result = await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
        )

        assert result is False  # timed out

    @pytest.mark.asyncio
    async def test_approval_notification_includes_feature_id(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """feature_id is forwarded to send_approval_notification."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr6")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr6", ApprovalStatus.APPROVED
        )

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
            feature_id="IS-042",
        )

        notify_call = mock_notification_harness.send_approval_notification.call_args
        assert notify_call[1]["feature_id"] == "IS-042"

    @pytest.mark.asyncio
    async def test_approval_notification_includes_risk_score(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """risk_score is forwarded to send_approval_notification."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr7")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr7", ApprovalStatus.APPROVED
        )

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
            risk_score=0.75,
        )

        notify_call = mock_notification_harness.send_approval_notification.call_args
        assert notify_call[1]["risk_score"] == 0.75

    @pytest.mark.asyncio
    async def test_approval_create_request_uses_high_priority(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """Approval request is always created with HIGH priority."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr8")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr8", ApprovalStatus.APPROVED
        )

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
        )

        create_call = mock_approval_queue.create_request.call_args
        assert create_call[1]["priority"] == ApprovalPriority.HIGH


# ===========================================================================
# 3. _run_single_loop — uncovered paths
# ===========================================================================


class TestSingleLoopUncoveredPaths:
    """Paths in _run_single_loop not exercised by base tests."""

    @pytest.mark.asyncio
    async def test_zero_completed_zero_blocked_no_approval_requested(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """When both completed=0 and blocked=0, no approval is requested."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_class:
            mock_loop = AsyncMock()
            mock_result = Mock(
                success=True,
                iterations=5,
                features_completed=0,
                features_blocked=0,
                features_remaining=2,
                duration_seconds=30.0,
            )
            mock_loop.run.return_value = mock_result
            mock_class.return_value = mock_loop

            should_continue = await runner._run_single_loop()

        assert should_continue is True
        mock_approval_queue.create_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_blocked_features_no_completed_sends_one_approval(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """Only blocked features (completed=0) → exactly one approval request."""
        runner = _mock_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )
        mock_approval_queue.create_request.return_value = _make_approval_request("apr_b")
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr_b", ApprovalStatus.APPROVED
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_class:
            mock_loop = AsyncMock()
            mock_result = Mock(
                success=True,
                iterations=5,
                features_completed=0,
                features_blocked=3,
                features_remaining=3,
                duration_seconds=30.0,
            )
            mock_loop.run.return_value = mock_result
            mock_class.return_value = mock_loop

            should_continue = await runner._run_single_loop()

        # Approval for blocked features only
        assert mock_approval_queue.create_request.call_count == 1
        # Runner continues regardless of blocked approval result
        assert should_continue is True

    @pytest.mark.asyncio
    async def test_blocked_approval_rejected_runner_still_continues(
        self,
        temp_forge_root,
        mock_approval_queue,
        mock_notification_harness,
        mock_decision_engine,
    ):
        """Blocked features approval rejected → runner still continues (by design)."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            approval_timeout_hours=0.0001,
        )
        runner._approval_queue = mock_approval_queue
        runner._notification_harness = mock_notification_harness
        runner._decision_engine = mock_decision_engine

        mock_approval_queue.create_request.return_value = _make_approval_request(
            "apr_br", ApprovalStatus.PENDING
        )
        mock_approval_queue.get_request.return_value = _make_approval_request(
            "apr_br", ApprovalStatus.REJECTED
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_class:
            mock_loop = AsyncMock()
            mock_result = Mock(
                success=True,
                iterations=3,
                features_completed=0,
                features_blocked=1,
                features_remaining=5,
                duration_seconds=20.0,
            )
            mock_loop.run.return_value = mock_result
            mock_class.return_value = mock_loop

            should_continue = await runner._run_single_loop()

        # The comment in source says "Continue even if not approved"
        assert should_continue is True

    @pytest.mark.asyncio
    async def test_loop_config_constructed_with_dry_run_false(self, temp_forge_root):
        """LoopConfig is always constructed with dry_run=False."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph:
            with patch("forge_harness.continuous_runner.runner.LoopConfig") as mock_config:
                with patch("forge_harness.continuous_runner.runner.FeatureStore"):
                    mock_loop = AsyncMock()
                    mock_result = Mock(
                        success=True,
                        features_completed=0,
                        features_blocked=0,
                        features_remaining=0,
                        duration_seconds=5.0,
                    )
                    mock_loop.run.return_value = mock_result
                    mock_ralph.return_value = mock_loop

                    await runner._run_single_loop()

            call_kw = mock_config.call_args[1]
            assert call_kw["dry_run"] is False
            assert call_kw["max_failures_per_feature"] == 3
            assert call_kw["checkpoint_interval"] == 5
            assert call_kw["timeout_seconds"] == 300

    @pytest.mark.asyncio
    async def test_feature_store_receives_features_path(self, temp_forge_root):
        """FeatureStore is created with the runner's features_path."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_ralph:
            with patch("forge_harness.continuous_runner.runner.LoopConfig"):
                with patch("forge_harness.continuous_runner.runner.FeatureStore") as mock_store:
                    mock_loop = AsyncMock()
                    mock_result = Mock(
                        success=True,
                        features_completed=0,
                        features_blocked=0,
                        features_remaining=0,
                        duration_seconds=5.0,
                    )
                    mock_loop.run.return_value = mock_result
                    mock_ralph.return_value = mock_loop

                    await runner._run_single_loop()

            mock_store.assert_called_once_with(runner.features_path)


# ===========================================================================
# 4. run() — lifecycle and timing behaviour
# ===========================================================================


class TestRunLifecycle:
    """Lifecycle and timing paths in the main run() method."""

    @pytest.mark.asyncio
    async def test_run_sets_running_true_at_start(self, temp_forge_root):
        """run() sets _running=True before entering the loop."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        running_during_loop: list[bool] = []

        async def mock_loop():
            running_during_loop.append(runner._running)
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            await runner.run()

        assert running_during_loop == [True]
        assert runner._running is False  # cleared in finally

    @pytest.mark.asyncio
    async def test_run_exception_sleeps_cooldown_times_two(self, temp_forge_root):
        """On exception, run() sleeps for loop_cooldown * 2 before retrying."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=5.0,
        )
        call_count = 0

        async def mock_loop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await runner.run()

            mock_sleep.assert_any_call(10.0)  # cooldown * 2

    @pytest.mark.asyncio
    async def test_run_idle_timeout_error_continues_loop(self, temp_forge_root):
        """TimeoutError from asyncio.wait_for causes loop to continue (not stop)."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.01,
        )
        call_count = 0

        async def mock_loop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False  # first run: no work → idle wait
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
                mock_wait.side_effect = TimeoutError()
                await runner.run()

        # Loop ran twice: first returned False (idle), TimeoutError → continued, second stopped
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_idle_wait_uses_cooldown_times_ten(self, temp_forge_root):
        """Idle state passes loop_cooldown * 10 as timeout to asyncio.wait_for."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=3.0,
        )

        async def mock_loop():
            runner._shutdown_event.set()
            return False  # trigger idle branch

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
                mock_wait.side_effect = TimeoutError()
                await runner.run()

        # asyncio.wait_for called with timeout=30.0 (3.0 * 10)
        wait_call = mock_wait.call_args
        assert wait_call[1]["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_run_multiple_iterations_apply_cooldown(self, temp_forge_root):
        """Each 'should_continue=True' iteration sleeps loop_cooldown seconds."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=2.5,
        )
        iteration = 0

        async def mock_loop():
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                runner._shutdown_event.set()
                return True
            return True  # keep going

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await runner.run()

        # Cooldown called once per 'should_continue=True' iteration
        cooldown_calls = [c for c in mock_sleep.call_args_list if c == call(2.5)]
        assert len(cooldown_calls) >= 2

    @pytest.mark.asyncio
    async def test_run_final_running_flag_false_after_cancellation(self, temp_forge_root):
        """After CancelledError _running is False (finally block fires)."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        async def mock_loop():
            raise asyncio.CancelledError()

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            await runner.run()

        assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_multiple_exceptions_keeps_retrying(self, temp_forge_root):
        """run() retries after each exception until shutdown."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=0.0,
        )
        call_count = 0

        async def mock_loop():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ValueError(f"error {call_count}")
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await runner.run()

        assert call_count == 4


# ===========================================================================
# 5. main() — additional paths
# ===========================================================================


class TestMainAdditionalPaths:
    """Additional main() paths not covered by base tests."""

    def test_main_both_domain_and_project_empty(self, monkeypatch):
        """main() exits 1 when both FORGE_DOMAIN and FORGE_PROJECT are empty strings."""
        monkeypatch.setenv("FORGE_DOMAIN", "")
        monkeypatch.setenv("FORGE_PROJECT", "")

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1

    def test_main_passes_domain_and_project_to_runner(self, monkeypatch):
        """main() passes domain and project positional/keyword args to runner."""
        monkeypatch.setenv("FORGE_DOMAIN", "my-domain")
        monkeypatch.setenv("FORGE_PROJECT", "my-project")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_cls.call_args.kwargs
        # domain and project may be passed as positional or keyword
        args = mock_cls.call_args.args
        domain_val = kwargs.get("domain") or (args[0] if args else None)
        project_val = kwargs.get("project") or (args[1] if len(args) > 1 else None)
        assert domain_val == "my-domain"
        assert project_val == "my-project"

    def test_main_features_path_converted_to_path_object(self, monkeypatch, tmp_path):
        """main() converts FEATURES_PATH string to a Path object."""
        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
        monkeypatch.setenv("FEATURES_PATH", "/some/path/features.json")
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_cls.call_args.kwargs
        assert isinstance(kwargs["features_path"], Path)
        assert str(kwargs["features_path"]) == "/some/path/features.json"

    def test_main_max_iterations_env_parsed_as_int(self, monkeypatch):
        """MAX_ITERATIONS_PER_RUN is parsed as int, not float."""
        monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
        monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
        monkeypatch.setenv("MAX_ITERATIONS_PER_RUN", "75")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_cls:
            mock_instance = Mock()
            mock_instance.run = AsyncMock()
            mock_cls.return_value = mock_instance
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        kwargs = mock_cls.call_args.kwargs
        assert kwargs["max_iterations_per_run"] == 75
        assert isinstance(kwargs["max_iterations_per_run"], int)


# ===========================================================================
# 6. _handle_shutdown — edge cases
# ===========================================================================


class TestShutdownEdgeCases:
    """Edge cases for _handle_shutdown not in base tests."""

    def test_handle_shutdown_works_when_running_already_false(self, temp_forge_root):
        """_handle_shutdown is safe to call even when _running is already False."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        assert runner._running is False
        runner._handle_shutdown()  # should not raise
        assert runner._running is False
        assert runner._shutdown_event.is_set()

    def test_handle_shutdown_is_registered_for_sigterm_and_sigint(self, temp_forge_root):
        """run() registers _handle_shutdown for both SIGTERM and SIGINT."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        async def mock_loop():
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.get_event_loop") as mock_get_loop:
                mock_event_loop = Mock()
                mock_get_loop.return_value = mock_event_loop
                asyncio.run(runner.run())

            registered_sigs = {
                c[0][0] for c in mock_event_loop.add_signal_handler.call_args_list
            }
            assert signal.SIGTERM in registered_sigs
            assert signal.SIGINT in registered_sigs

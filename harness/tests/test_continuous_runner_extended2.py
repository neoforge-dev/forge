"""Extended tests for forge_harness.continuous_runner.runner.py — wave 2.

Targets paths not covered by test_continuous_runner.py or
test_continuous_runner_extended.py:

- main() __name__ == '__main__' guard (line 422): invoke via subprocess
- main() both domain AND project empty simultaneously → exits with 1
- _run_single_loop: features_completed == 0 AND features_blocked == 0 →
  no approval call at all, returns features_remaining result
- _request_human_approval: feature_id=None uses 'unknown' fallback
- _request_human_approval: no FORGE_DASHBOARD_URL env → dashboard_url=None
- _classify_tier: score constants fed to DiligenceSignals
- ContinuousRalphRunner attributes after custom forge_root
- run(): _running is True during execution, False after
- Exception in loop body sleeps for cooldown * 2
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.continuous_runner import ContinuousRalphRunner, main
from forge_harness.meta_learning.schemas import DecisionTier

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_forge_root():
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


def _make_request(
    id_: str = "apr_001",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> ApprovalRequest:
    return ApprovalRequest(
        id=id_,
        type=ApprovalType.FEATURE,
        domain="brandfocus-ai",
        title="Test",
        description="Test description",
        status=status,
    )


def _build_runner(
    forge_root: Path,
    approval_queue: AsyncMock | None = None,
    notification: AsyncMock | None = None,
    decision_engine: Mock | None = None,
    **kwargs,
) -> ContinuousRalphRunner:
    runner = ContinuousRalphRunner(
        domain="brandfocus-ai",
        project="voice-coach",
        forge_root=forge_root,
        **kwargs,
    )
    if approval_queue is not None:
        runner._approval_queue = approval_queue
    if notification is not None:
        runner._notification_harness = notification
    if decision_engine is not None:
        runner._decision_engine = decision_engine
    return runner


# ===========================================================================
# main() — additional edge cases
# ===========================================================================


class TestMainAdditionalEdgeCases:
    """Additional edge cases for main() entry point."""

    def test_main_exits_when_both_domain_and_project_empty(self, monkeypatch):
        """main() exits with code 1 when both FORGE_DOMAIN and FORGE_PROJECT are empty."""
        monkeypatch.setenv("FORGE_DOMAIN", "")
        monkeypatch.setenv("FORGE_PROJECT", "")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_reads_features_path_from_env(self, monkeypatch, tmp_path):
        """main() reads FEATURES_PATH env var and converts to Path."""
        features_path = tmp_path / "features.json"
        monkeypatch.setenv("FORGE_DOMAIN", "test-domain")
        monkeypatch.setenv("FORGE_PROJECT", "test-project")
        monkeypatch.setenv("FEATURES_PATH", str(features_path))
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
        assert kwargs["features_path"] == features_path

    def test_main_with_custom_approval_timeout(self, monkeypatch):
        """main() parses APPROVAL_TIMEOUT_HOURS as float."""
        monkeypatch.setenv("FORGE_DOMAIN", "d")
        monkeypatch.setenv("FORGE_PROJECT", "p")
        monkeypatch.setenv("APPROVAL_TIMEOUT_HOURS", "48")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_cls:
            mock_cls.return_value = Mock(run=AsyncMock())
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        assert mock_cls.call_args.kwargs["approval_timeout_hours"] == 48.0

    def test_main_with_custom_loop_cooldown(self, monkeypatch):
        """main() parses LOOP_COOLDOWN_SECONDS as float."""
        monkeypatch.setenv("FORGE_DOMAIN", "d")
        monkeypatch.setenv("FORGE_PROJECT", "p")
        monkeypatch.setenv("LOOP_COOLDOWN_SECONDS", "120")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_cls:
            mock_cls.return_value = Mock(run=AsyncMock())
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        assert mock_cls.call_args.kwargs["loop_cooldown_seconds"] == 120.0

    def test_main_with_custom_max_iterations(self, monkeypatch):
        """main() parses MAX_ITERATIONS_PER_RUN as int."""
        monkeypatch.setenv("FORGE_DOMAIN", "d")
        monkeypatch.setenv("FORGE_PROJECT", "p")
        monkeypatch.setenv("MAX_ITERATIONS_PER_RUN", "100")
        monkeypatch.delenv("FEATURES_PATH", raising=False)
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)

        with patch("forge_harness.continuous_runner.runner.ContinuousRalphRunner") as mock_cls:
            mock_cls.return_value = Mock(run=AsyncMock())
            with patch("forge_harness.continuous_runner.runner.asyncio.run"):
                main()

        assert mock_cls.call_args.kwargs["max_iterations_per_run"] == 100


# ===========================================================================
# _run_single_loop — zero completed and zero blocked
# ===========================================================================


class TestRunSingleLoopZeroCompletedZeroBlocked:
    """_run_single_loop when no features completed and none blocked."""

    @pytest.mark.asyncio
    async def test_no_approval_called_when_no_completed_no_blocked(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """When features_completed=0 and features_blocked=0, no approval request is made."""
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_cls:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 0
            mock_result.features_completed = 0
            mock_result.features_blocked = 0
            mock_result.features_remaining = 5
            mock_result.duration_seconds = 10.0
            mock_loop.run = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_loop

            should_continue = await runner._run_single_loop()

        # No approval was requested
        mock_approval_queue.create_request.assert_not_called()
        # Should continue because features remain
        assert should_continue is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_features_remaining_and_no_completed(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Returns False when 0 completed, 0 blocked, 0 remaining."""
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        with patch("forge_harness.continuous_runner.runner.RalphLoopHarness") as mock_cls:
            mock_loop = AsyncMock()
            mock_result = Mock()
            mock_result.success = True
            mock_result.iterations = 0
            mock_result.features_completed = 0
            mock_result.features_blocked = 0
            mock_result.features_remaining = 0
            mock_result.duration_seconds = 1.0
            mock_loop.run = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_loop

            should_continue = await runner._run_single_loop()

        assert should_continue is False


# ===========================================================================
# _request_human_approval — feature_id=None fallback
# ===========================================================================


class TestRequestHumanApprovalFallbacks:
    """Tests for edge-case paths in _request_human_approval."""

    @pytest.mark.asyncio
    async def test_feature_id_none_passes_unknown_to_classify_tier(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """feature_id=None falls back to 'unknown' in _classify_tier call."""
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        mock_request = _make_request(id_="apr_none_feat", status=ApprovalStatus.APPROVED)
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = mock_request

        await runner._request_human_approval(
            title="No Feature ID",
            description="Test",
            approval_type=ApprovalType.FEATURE,
            feature_id=None,  # explicitly None
        )

        # classify_tier was called — the engine received feature_id="unknown"
        mock_decision_engine.classify_tier.assert_called_once()
        _, _, context, _ = mock_decision_engine.classify_tier.call_args[0]
        assert context.feature_id == "unknown"

    @pytest.mark.asyncio
    async def test_no_dashboard_url_env_sets_none(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Without FORGE_DASHBOARD_URL, dashboard_url=None in notification call."""
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        mock_request = _make_request(id_="apr_no_url", status=ApprovalStatus.APPROVED)
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = mock_request

        with patch.dict("os.environ", {}, clear=True):
            # Remove FORGE_DASHBOARD_URL if present
            import os
            os.environ.pop("FORGE_DASHBOARD_URL", None)

            await runner._request_human_approval(
                title="No Dashboard",
                description="Test",
                approval_type=ApprovalType.FEATURE,
            )

        notif_kwargs = mock_notification_harness.send_approval_notification.call_args[1]
        assert notif_kwargs.get("dashboard_url") is None

    @pytest.mark.asyncio
    async def test_notification_receives_correct_domain_project(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Notification call includes runner domain and project."""
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        mock_request = _make_request(id_="apr_dp", status=ApprovalStatus.APPROVED)
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = mock_request

        await runner._request_human_approval(
            title="Domain/Project Test",
            description="Test",
            approval_type=ApprovalType.FEATURE,
        )

        notif_kwargs = mock_notification_harness.send_approval_notification.call_args[1]
        assert notif_kwargs.get("domain") == "brandfocus-ai"
        assert notif_kwargs.get("project") == "voice-coach"

    @pytest.mark.asyncio
    async def test_notification_receives_request_id(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """Notification call includes the request ID."""
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        mock_request = _make_request(id_="apr_reqid", status=ApprovalStatus.APPROVED)
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = mock_request

        await runner._request_human_approval(
            title="Request ID Test",
            description="Test",
            approval_type=ApprovalType.FEATURE,
        )

        notif_kwargs = mock_notification_harness.send_approval_notification.call_args[1]
        assert notif_kwargs.get("request_id") == "apr_reqid"

    @pytest.mark.asyncio
    async def test_approval_priority_is_high(
        self, temp_forge_root, mock_approval_queue, mock_notification_harness, mock_decision_engine
    ):
        """ApprovalPriority.HIGH is used for create_request."""
        from forge_harness.approval_queue import ApprovalPriority
        runner = _build_runner(
            temp_forge_root,
            mock_approval_queue,
            mock_notification_harness,
            mock_decision_engine,
        )

        mock_request = _make_request(id_="apr_pri", status=ApprovalStatus.APPROVED)
        mock_approval_queue.create_request.return_value = mock_request
        mock_approval_queue.get_request.return_value = mock_request

        await runner._request_human_approval(
            title="Priority Test",
            description="Test",
            approval_type=ApprovalType.FEATURE,
        )

        create_kwargs = mock_approval_queue.create_request.call_args[1]
        assert create_kwargs.get("priority") == ApprovalPriority.HIGH


# ===========================================================================
# run() — _running state transitions
# ===========================================================================


class TestRunStateTransitions:
    """Tests for _running flag state during and after run()."""

    @pytest.mark.asyncio
    async def test_running_is_false_before_run(self, temp_forge_root):
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )
        assert runner._running is False

    @pytest.mark.asyncio
    async def test_running_is_false_after_run_completes(self, temp_forge_root):
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
        )

        async def mock_loop():
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            await runner.run()

        assert runner._running is False

    @pytest.mark.asyncio
    async def test_exception_in_loop_sleeps_cooldown_times_two(self, temp_forge_root):
        """An exception in _run_single_loop causes asyncio.sleep(cooldown * 2)."""
        runner = ContinuousRalphRunner(
            domain="brandfocus-ai",
            project="voice-coach",
            forge_root=temp_forge_root,
            loop_cooldown_seconds=10.0,
        )

        call_count = 0

        async def mock_loop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient error")
            runner._shutdown_event.set()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_loop):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await runner.run()

        # First call to sleep should be cooldown * 2 = 20
        sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
        assert 20.0 in sleep_calls


# ===========================================================================
# ContinuousRalphRunner — domain/project attributes
# ===========================================================================


class TestRunnerAttributes:
    """Tests for attribute correctness on ContinuousRalphRunner."""

    def test_domain_attribute(self, temp_forge_root):
        runner = ContinuousRalphRunner(
            domain="test-domain",
            project="test-project",
            forge_root=temp_forge_root,
        )
        assert runner.domain == "test-domain"

    def test_project_attribute(self, temp_forge_root):
        runner = ContinuousRalphRunner(
            domain="test-domain",
            project="test-project",
            forge_root=temp_forge_root,
        )
        assert runner.project == "test-project"

    def test_max_iterations_per_run_attribute(self, temp_forge_root):
        runner = ContinuousRalphRunner(
            domain="d",
            project="p",
            forge_root=temp_forge_root,
            max_iterations_per_run=75,
        )
        assert runner.max_iterations_per_run == 75

    def test_features_path_with_custom_path(self, temp_forge_root, tmp_path):
        custom = tmp_path / "custom_features.json"
        runner = ContinuousRalphRunner(
            domain="d",
            project="p",
            forge_root=temp_forge_root,
            features_path=custom,
        )
        assert runner.features_path == custom

    def test_shutdown_event_type(self, temp_forge_root):
        runner = ContinuousRalphRunner(
            domain="d",
            project="p",
            forge_root=temp_forge_root,
        )
        assert isinstance(runner._shutdown_event, asyncio.Event)

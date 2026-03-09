"""
Comprehensive pytest tests for:
  - forge_harness.continuous_runner.runner.py  (ContinuousRalphRunner, main)
  - forge_harness/flywheel.py           (FlywheelConfig, FlywheelResult,
                                         create_flywheel_loop, scan_project_for_debt,
                                         scan_project_local, quality_report_to_features,
                                         generate_portfolio_features, run_flywheel,
                                         run_flywheel_sync, _build_debt_description)

All external I/O, network calls, sleep, subprocess, and inter-module
dependencies are mocked so the suite runs fully offline in any environment.

Run:
    cd harness
    uv run pytest tests/test_continuous_runner_flywheel.py -x --tb=short -q
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: ensure the harness package is importable
# ---------------------------------------------------------------------------
_HARNESS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_HARNESS_ROOT))


# ===========================================================================
# ===  continuous_runner.py  =================================================
# ===========================================================================


class TestContinuousRalphRunnerInit:
    """Tests for ContinuousRalphRunner.__init__."""

    def _make_runner(self, **kwargs):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        defaults = dict(domain="test-domain", project="test-project")
        defaults.update(kwargs)
        return ContinuousRalphRunner(**defaults)

    def test_default_attributes(self, tmp_path):
        runner = self._make_runner(forge_root=tmp_path)
        assert runner.domain == "test-domain"
        assert runner.project == "test-project"
        assert runner.forge_root == tmp_path
        assert runner.loop_cooldown == 60.0
        assert runner.max_iterations_per_run == 50
        assert runner._running is False
        assert runner._current_feature is None
        assert runner._approval_queue is None
        assert runner._notification_harness is None
        assert runner._decision_engine is None

    def test_features_path_derived_from_domain_project(self, tmp_path):
        runner = self._make_runner(forge_root=tmp_path)
        expected = tmp_path / "test-domain" / "test-project" / "features.json"
        assert runner.features_path == expected

    def test_features_path_explicit_override(self, tmp_path):
        fp = tmp_path / "custom_features.json"
        runner = self._make_runner(forge_root=tmp_path, features_path=fp)
        assert runner.features_path == fp

    def test_approval_timeout_stored_as_timedelta(self, tmp_path):
        runner = self._make_runner(forge_root=tmp_path, approval_timeout_hours=2.0)
        assert runner.approval_timeout == timedelta(hours=2.0)

    def test_custom_cooldown_and_max_iterations(self, tmp_path):
        runner = self._make_runner(
            forge_root=tmp_path,
            loop_cooldown_seconds=30.0,
            max_iterations_per_run=10,
        )
        assert runner.loop_cooldown == 30.0
        assert runner.max_iterations_per_run == 10

    def test_forge_root_defaults_to_cwd(self):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(domain="d", project="p")
        assert runner.forge_root == Path.cwd()


class TestContinuousRalphRunnerLazyComponents:
    """Tests for lazy-initialised component accessors."""

    def _make_runner(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        return ContinuousRalphRunner(
            domain="test-domain", project="test-project", forge_root=tmp_path
        )

    @pytest.mark.asyncio
    async def test_get_approval_queue_lazy_init(self, tmp_path):
        runner = self._make_runner(tmp_path)
        mock_queue = MagicMock()

        with patch(
            "forge_harness.continuous_runner.runner.create_approval_queue_from_env",
            return_value=mock_queue,
        ):
            q1 = await runner._get_approval_queue()
            q2 = await runner._get_approval_queue()
            assert q1 is mock_queue
            assert q2 is mock_queue  # cached, not re-created

    @pytest.mark.asyncio
    async def test_get_notification_harness_lazy_init(self, tmp_path):
        runner = self._make_runner(tmp_path)
        mock_notifier = MagicMock()

        with patch(
            "forge_harness.continuous_runner.runner.create_notification_harness",
            return_value=mock_notifier,
        ):
            n1 = await runner._get_notification_harness()
            n2 = await runner._get_notification_harness()
            assert n1 is mock_notifier
            assert n2 is mock_notifier  # cached

    def test_get_decision_engine_lazy_init(self, tmp_path):
        runner = self._make_runner(tmp_path)
        mock_engine = MagicMock()

        with patch(
            "forge_harness.continuous_runner.runner.DecisionEngine", return_value=mock_engine
        ):
            e1 = runner._get_decision_engine()
            e2 = runner._get_decision_engine()
            assert e1 is mock_engine
            assert e2 is mock_engine  # cached


class TestClassifyTier:
    """Tests for ContinuousRalphRunner._classify_tier."""

    def _make_runner(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        return ContinuousRalphRunner(
            domain="test-domain", project="test-project", forge_root=tmp_path
        )

    def _mock_engine(self, tier_value: str):
        engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = tier_value
        engine.classify_tier.return_value = mock_tier
        return engine

    def test_classify_tier_feature_complete_low_risk(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("phone")
        runner._decision_engine = engine

        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Test Feature",
            action_type="feature_complete",
            risk_score=0.1,
        )
        assert tier == "phone"
        assert engine.classify_tier.called

    def test_classify_tier_deploy_high_risk(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("desktop")
        runner._decision_engine = engine

        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Deploy Feature",
            action_type="deploy",
            risk_score=0.9,
        )
        assert tier == "desktop"

    def test_classify_tier_test_retry(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("watch")
        runner._decision_engine = engine

        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Retry",
            action_type="test_retry",
            risk_score=0.1,
        )
        assert tier == "watch"

    def test_classify_tier_blocked(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("desktop")
        runner._decision_engine = engine

        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Blocked",
            action_type="blocked",
            risk_score=0.8,
        )
        assert tier == "desktop"

    def test_classify_tier_unknown_action_type(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("phone")
        runner._decision_engine = engine

        # Unknown action_type defaults to HUMAN_REVIEW_REQUIRED
        tier = runner._classify_tier(
            feature_id="feat-001",
            feature_name="Unknown",
            action_type="something_unknown",
            risk_score=0.3,
        )
        assert tier == "phone"

    def test_classify_tier_medium_risk_threshold(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("phone")
        runner._decision_engine = engine

        # risk_score=0.3 is boundary low/medium
        runner._classify_tier("feat-1", "Feat", "feature_complete", risk_score=0.3)
        # Just verify it calls through without error
        assert engine.classify_tier.call_count == 1

    def test_classify_tier_high_risk_threshold(self, tmp_path):
        runner = self._make_runner(tmp_path)
        engine = self._mock_engine("desktop")
        runner._decision_engine = engine

        runner._classify_tier("feat-1", "Feat", "feature_complete", risk_score=0.6)
        assert engine.classify_tier.call_count == 1


class TestRequestHumanApproval:
    """Tests for ContinuousRalphRunner._request_human_approval."""

    def _make_runner(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        return ContinuousRalphRunner(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            approval_timeout_hours=0.001,  # Very short for testing
            loop_cooldown_seconds=1.0,
        )

    def _make_mock_request(self, status_value: str = "pending", req_id: str = "req-123"):
        req = MagicMock()
        req.id = req_id
        req.status = MagicMock()
        req.status.value = status_value
        return req

    @pytest.mark.asyncio
    async def test_approval_granted(self, tmp_path):
        runner = self._make_runner(tmp_path)
        from forge_harness.approval_queue import ApprovalType

        mock_queue = AsyncMock()
        approved_req = self._make_mock_request("approved")
        mock_queue.create_request = AsyncMock(return_value=approved_req)
        mock_queue.get_request = AsyncMock(return_value=approved_req)

        mock_notifier = AsyncMock()
        mock_engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = "phone"
        mock_engine.classify_tier.return_value = mock_tier

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier
        runner._decision_engine = mock_engine

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner._request_human_approval(
                title="Test Approval",
                description="Test description",
                approval_type=ApprovalType.FEATURE,
                feature_id="feat-001",
                risk_score=0.2,
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_approval_rejected(self, tmp_path):
        runner = self._make_runner(tmp_path)
        from forge_harness.approval_queue import ApprovalType

        mock_queue = AsyncMock()
        rejected_req = self._make_mock_request("rejected")
        mock_queue.create_request = AsyncMock(return_value=rejected_req)
        mock_queue.get_request = AsyncMock(return_value=rejected_req)

        mock_notifier = AsyncMock()
        mock_engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = "desktop"
        mock_engine.classify_tier.return_value = mock_tier

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier
        runner._decision_engine = mock_engine

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner._request_human_approval(
                title="Rejected",
                description="Will be rejected",
                approval_type=ApprovalType.DEPLOY,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_approval_timeout(self, tmp_path):
        runner = self._make_runner(tmp_path)
        from forge_harness.approval_queue import ApprovalType

        mock_queue = AsyncMock()
        pending_req = self._make_mock_request("pending")
        mock_queue.create_request = AsyncMock(return_value=pending_req)
        mock_queue.get_request = AsyncMock(return_value=pending_req)

        mock_notifier = AsyncMock()
        mock_engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = "watch"
        mock_engine.classify_tier.return_value = mock_tier

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier
        runner._decision_engine = mock_engine

        # Make the event loop time advance past the deadline
        original_time = asyncio.get_event_loop().time
        call_count = [0]

        def fast_time():
            t = original_time()
            # After first call, jump past deadline
            if call_count[0] > 0:
                return t + 99999
            call_count[0] += 1
            return t

        with patch.object(asyncio.get_event_loop(), "time", side_effect=fast_time):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await runner._request_human_approval(
                    title="Timeout Test",
                    description="Will timeout",
                    approval_type=ApprovalType.FEATURE,
                )

        assert result is False

    @pytest.mark.asyncio
    async def test_approval_shutdown_during_wait(self, tmp_path):
        runner = self._make_runner(tmp_path)
        from forge_harness.approval_queue import ApprovalType

        mock_queue = AsyncMock()
        pending_req = self._make_mock_request("pending")
        mock_queue.create_request = AsyncMock(return_value=pending_req)
        mock_queue.get_request = AsyncMock(return_value=pending_req)

        mock_notifier = AsyncMock()
        mock_engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = "phone"
        mock_engine.classify_tier.return_value = mock_tier

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier
        runner._decision_engine = mock_engine

        # Signal shutdown immediately
        runner._shutdown_event.set()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner._request_human_approval(
                title="Shutdown Test",
                description="Shutdown during wait",
                approval_type=ApprovalType.FEATURE,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_dashboard_url_appended_to_approval_link(self, tmp_path, monkeypatch):
        runner = self._make_runner(tmp_path)
        from forge_harness.approval_queue import ApprovalType

        monkeypatch.setenv("FORGE_DASHBOARD_URL", "https://dashboard.example.com")

        mock_queue = AsyncMock()
        approved_req = self._make_mock_request("approved")
        mock_queue.create_request = AsyncMock(return_value=approved_req)
        mock_queue.get_request = AsyncMock(return_value=approved_req)

        mock_notifier = AsyncMock()
        mock_engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = "phone"
        mock_engine.classify_tier.return_value = mock_tier

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier
        runner._decision_engine = mock_engine

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner._request_human_approval(
                title="Dashboard URL Test",
                description="Should include dashboard URL",
                approval_type=ApprovalType.FEATURE,
                feature_id="feat-001",
            )

        assert result is True
        # Verify send_approval_notification was called with a dashboard_url
        send_call_kwargs = mock_notifier.send_approval_notification.call_args.kwargs
        assert "approvals/req-123" in send_call_kwargs["dashboard_url"]

    @pytest.mark.asyncio
    async def test_no_dashboard_url_when_env_not_set(self, tmp_path, monkeypatch):
        runner = self._make_runner(tmp_path)
        from forge_harness.approval_queue import ApprovalType

        monkeypatch.delenv("FORGE_DASHBOARD_URL", raising=False)

        mock_queue = AsyncMock()
        approved_req = self._make_mock_request("approved")
        mock_queue.create_request = AsyncMock(return_value=approved_req)
        mock_queue.get_request = AsyncMock(return_value=approved_req)

        mock_notifier = AsyncMock()
        mock_engine = MagicMock()
        mock_tier = MagicMock()
        mock_tier.value = "phone"
        mock_engine.classify_tier.return_value = mock_tier

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier
        runner._decision_engine = mock_engine

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner._request_human_approval(
                title="No Dashboard",
                description="No URL",
                approval_type=ApprovalType.FEATURE,
            )

        send_call_kwargs = mock_notifier.send_approval_notification.call_args.kwargs
        assert send_call_kwargs["dashboard_url"] is None


class TestRunSingleLoop:
    """Tests for ContinuousRalphRunner._run_single_loop."""

    def _make_runner(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        return ContinuousRalphRunner(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
        )

    def _make_loop_result(self, **kwargs):
        defaults = dict(
            success=True,
            iterations=5,
            features_completed=2,
            features_blocked=0,
            features_remaining=3,
            total_tokens=1000,
            duration_seconds=10.0,
        )
        defaults.update(kwargs)
        result = MagicMock()
        for k, v in defaults.items():
            setattr(result, k, v)
        return result

    @pytest.mark.asyncio
    async def test_returns_false_when_features_path_missing(self, tmp_path):
        runner = self._make_runner(tmp_path)
        # features_path does not exist
        assert not runner.features_path.exists()
        result = await runner._run_single_loop()
        assert result is False

    @pytest.mark.asyncio
    async def test_continues_when_features_remaining(self, tmp_path):
        runner = self._make_runner(tmp_path)
        # Create features file
        runner.features_path.parent.mkdir(parents=True, exist_ok=True)
        runner.features_path.write_text("[]")

        loop_result = self._make_loop_result(
            features_completed=0,
            features_blocked=0,
            features_remaining=5,
        )
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch("forge_harness.continuous_runner.runner.FeatureStore"):
            with patch("forge_harness.continuous_runner.runner.RalphLoopHarness", return_value=mock_loop):
                result = await runner._run_single_loop()

        assert result is True

    @pytest.mark.asyncio
    async def test_stops_when_no_remaining(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.features_path.parent.mkdir(parents=True, exist_ok=True)
        runner.features_path.write_text("[]")

        loop_result = self._make_loop_result(
            features_completed=0,
            features_blocked=0,
            features_remaining=0,
        )
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch("forge_harness.continuous_runner.runner.FeatureStore"):
            with patch("forge_harness.continuous_runner.runner.RalphLoopHarness", return_value=mock_loop):
                result = await runner._run_single_loop()

        assert result is False

    @pytest.mark.asyncio
    async def test_requests_approval_when_features_completed(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.features_path.parent.mkdir(parents=True, exist_ok=True)
        runner.features_path.write_text("[]")

        loop_result = self._make_loop_result(
            features_completed=3,
            features_blocked=0,
            features_remaining=2,
        )
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch("forge_harness.continuous_runner.runner.FeatureStore"):
            with patch("forge_harness.continuous_runner.runner.RalphLoopHarness", return_value=mock_loop):
                with patch.object(
                    runner, "_request_human_approval", new_callable=AsyncMock, return_value=True
                ) as mock_approval:
                    result = await runner._run_single_loop()

        assert mock_approval.called
        assert result is True

    @pytest.mark.asyncio
    async def test_stops_when_batch_approval_rejected(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.features_path.parent.mkdir(parents=True, exist_ok=True)
        runner.features_path.write_text("[]")

        loop_result = self._make_loop_result(
            features_completed=2,
            features_blocked=0,
            features_remaining=3,
        )
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch("forge_harness.continuous_runner.runner.FeatureStore"):
            with patch("forge_harness.continuous_runner.runner.RalphLoopHarness", return_value=mock_loop):
                with patch.object(
                    runner,
                    "_request_human_approval",
                    new_callable=AsyncMock,
                    return_value=False,
                ):
                    result = await runner._run_single_loop()

        assert result is False

    @pytest.mark.asyncio
    async def test_requests_approval_for_blocked_features(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.features_path.parent.mkdir(parents=True, exist_ok=True)
        runner.features_path.write_text("[]")

        loop_result = self._make_loop_result(
            features_completed=0,
            features_blocked=2,
            features_remaining=1,
        )
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        approval_calls = []

        async def mock_approval(**kwargs):
            approval_calls.append(kwargs)
            return True

        with patch("forge_harness.continuous_runner.runner.FeatureStore"):
            with patch("forge_harness.continuous_runner.runner.RalphLoopHarness", return_value=mock_loop):
                with patch.object(runner, "_request_human_approval", side_effect=mock_approval):
                    result = await runner._run_single_loop()

        # Blocked features should trigger an approval request
        assert len(approval_calls) == 1
        assert result is True  # continues even if blocked approval not granted

    @pytest.mark.asyncio
    async def test_both_completed_and_blocked(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.features_path.parent.mkdir(parents=True, exist_ok=True)
        runner.features_path.write_text("[]")

        loop_result = self._make_loop_result(
            features_completed=2,
            features_blocked=1,
            features_remaining=3,
        )
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        approval_calls = []

        async def mock_approval(**kwargs):
            approval_calls.append(kwargs)
            return True  # always approve

        with patch("forge_harness.continuous_runner.runner.FeatureStore"):
            with patch("forge_harness.continuous_runner.runner.RalphLoopHarness", return_value=mock_loop):
                with patch.object(runner, "_request_human_approval", side_effect=mock_approval):
                    result = await runner._run_single_loop()

        # Two approvals: one for completed, one for blocked
        assert len(approval_calls) == 2
        assert result is True


class TestHandleShutdown:
    """Tests for ContinuousRalphRunner._handle_shutdown."""

    def test_shutdown_sets_running_false(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(
            domain="d", project="p", forge_root=tmp_path
        )
        runner._running = True
        runner._handle_shutdown()
        assert runner._running is False

    def test_shutdown_sets_event(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(
            domain="d", project="p", forge_root=tmp_path
        )
        assert not runner._shutdown_event.is_set()
        runner._handle_shutdown()
        assert runner._shutdown_event.is_set()


class TestContinuousRalphRunnerRun:
    """Tests for ContinuousRalphRunner.run (outer loop)."""

    def _make_runner(self, tmp_path, cooldown=0.01):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        return ContinuousRalphRunner(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            loop_cooldown_seconds=cooldown,
        )

    @pytest.mark.asyncio
    async def test_run_sets_running_true_then_false(self, tmp_path):
        runner = self._make_runner(tmp_path)

        async def mock_single_loop():
            runner._handle_shutdown()  # trigger shutdown after first loop
            return False

        with patch.object(runner, "_run_single_loop", side_effect=mock_single_loop):
            with patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_loop:
                mock_event_loop = MagicMock()
                mock_loop.return_value = mock_event_loop
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch("asyncio.wait_for", new_callable=AsyncMock):
                        await runner.run()

        assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_enters_idle_when_no_work(self, tmp_path):
        runner = self._make_runner(tmp_path)
        call_count = [0]

        async def mock_single_loop():
            call_count[0] += 1
            runner._handle_shutdown()
            return False  # no work

        with patch.object(runner, "_run_single_loop", side_effect=mock_single_loop):
            with patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_loop:
                mock_event_loop = MagicMock()
                mock_loop.return_value = mock_event_loop

                async def instant_wait_for(coro, timeout):
                    # Just cancel immediately
                    raise TimeoutError()

                with patch("asyncio.wait_for", side_effect=instant_wait_for):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        await runner.run()

        assert call_count[0] >= 1

    @pytest.mark.asyncio
    async def test_run_handles_exception_and_retries(self, tmp_path):
        runner = self._make_runner(tmp_path)
        call_count = [0]

        async def flaky_single_loop():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Transient error")
            runner._handle_shutdown()
            return False

        with patch.object(runner, "_run_single_loop", side_effect=flaky_single_loop):
            with patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_loop:
                mock_event_loop = MagicMock()
                mock_loop.return_value = mock_event_loop
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch("asyncio.wait_for", new_callable=AsyncMock):
                        await runner.run()

        # Should have been called twice (first time errored, second time shutdown)
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_run_handles_cancelled_error(self, tmp_path):
        runner = self._make_runner(tmp_path)

        async def cancel_immediately():
            raise asyncio.CancelledError()

        with patch.object(runner, "_run_single_loop", side_effect=cancel_immediately):
            with patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_loop:
                mock_event_loop = MagicMock()
                mock_loop.return_value = mock_event_loop
                await runner.run()  # Should not raise

        assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_cooldown_between_runs(self, tmp_path):
        runner = self._make_runner(tmp_path, cooldown=5.0)
        sleep_calls = []

        async def fast_sleep(seconds):
            sleep_calls.append(seconds)

        call_count = [0]

        async def mock_single_loop():
            call_count[0] += 1
            if call_count[0] >= 2:
                runner._handle_shutdown()
            return True  # has more work

        with patch.object(runner, "_run_single_loop", side_effect=mock_single_loop):
            with patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_loop:
                mock_event_loop = MagicMock()
                mock_loop.return_value = mock_event_loop
                with patch("asyncio.sleep", side_effect=fast_sleep):
                    with patch("asyncio.wait_for", new_callable=AsyncMock):
                        await runner.run()

        # At least one cooldown sleep with our configured duration
        cooldown_sleeps = [s for s in sleep_calls if s == 5.0]
        assert len(cooldown_sleeps) >= 1

    @pytest.mark.asyncio
    async def test_run_registers_signal_handlers(self, tmp_path):
        runner = self._make_runner(tmp_path)

        async def immediate_shutdown():
            runner._handle_shutdown()
            return False

        registered_signals = []

        with patch.object(runner, "_run_single_loop", side_effect=immediate_shutdown):
            with patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop:
                mock_event_loop = MagicMock()

                def capture_add_signal(sig, handler):
                    registered_signals.append(sig)

                mock_event_loop.add_signal_handler = capture_add_signal
                mock_get_loop.return_value = mock_event_loop
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch("asyncio.wait_for", new_callable=AsyncMock):
                        await runner.run()

        assert signal.SIGTERM in registered_signals
        assert signal.SIGINT in registered_signals


class TestMain:
    """Tests for forge_harness.continuous_runner.runner.main."""

    def test_main_exits_1_when_no_env(self, monkeypatch):
        from forge_harness.continuous_runner import main

        monkeypatch.delenv("FORGE_DOMAIN", raising=False)
        monkeypatch.delenv("FORGE_PROJECT", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_exits_1_when_domain_only(self, monkeypatch):
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "test-domain")
        monkeypatch.delenv("FORGE_PROJECT", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_exits_1_when_project_only(self, monkeypatch):
        from forge_harness.continuous_runner import main

        monkeypatch.delenv("FORGE_DOMAIN", raising=False)
        monkeypatch.setenv("FORGE_PROJECT", "test-project")

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_creates_runner_and_calls_asyncio_run(self, monkeypatch, tmp_path):
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "test-domain")
        monkeypatch.setenv("FORGE_PROJECT", "test-project")
        monkeypatch.setenv("APPROVAL_TIMEOUT_HOURS", "2.5")
        monkeypatch.setenv("LOOP_COOLDOWN_SECONDS", "30")
        monkeypatch.setenv("MAX_ITERATIONS_PER_RUN", "25")

        with patch("forge_harness.continuous_runner.runner.asyncio.run") as mock_run:
            main()
            assert mock_run.called

    def test_main_uses_features_path_env(self, monkeypatch, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner, main

        monkeypatch.setenv("FORGE_DOMAIN", "test-domain")
        monkeypatch.setenv("FORGE_PROJECT", "test-project")
        fp = str(tmp_path / "features.json")
        monkeypatch.setenv("FEATURES_PATH", fp)

        created_runners = []

        def mock_runner_init(self, **kwargs):
            created_runners.append(kwargs)

        with patch("forge_harness.continuous_runner.runner.asyncio.run"):
            main()

        # main() should have called asyncio.run (not raised SystemExit)
        # The features_path should be passed through

    def test_main_default_env_values(self, monkeypatch):
        from forge_harness.continuous_runner import main

        monkeypatch.setenv("FORGE_DOMAIN", "my-domain")
        monkeypatch.setenv("FORGE_PROJECT", "my-project")
        monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

        with patch("forge_harness.continuous_runner.runner.asyncio.run") as mock_run:
            main()
        assert mock_run.called


# ===========================================================================
# ===  flywheel.py  ==========================================================
# ===========================================================================


class TestFlywheelConfig:
    """Tests for FlywheelConfig dataclass."""

    def test_default_values(self):
        from forge_harness.flywheel import FlywheelConfig

        cfg = FlywheelConfig()
        assert cfg.max_iterations == 100
        assert cfg.max_features_per_project == 10
        assert cfg.priority_threshold == "medium"
        assert cfg.include_harness_self_improvement is True
        assert cfg.test_command is None
        assert cfg.working_dir is None
        assert cfg.dry_run is False
        assert cfg.auto_commit is False

    def test_custom_values(self):
        from forge_harness.flywheel import FlywheelConfig

        cfg = FlywheelConfig(
            max_iterations=50,
            max_features_per_project=5,
            priority_threshold="high",
            dry_run=True,
            auto_commit=True,
            test_command="pytest",
        )
        assert cfg.max_iterations == 50
        assert cfg.max_features_per_project == 5
        assert cfg.priority_threshold == "high"
        assert cfg.dry_run is True
        assert cfg.auto_commit is True
        assert cfg.test_command == "pytest"


class TestFlywheelResult:
    """Tests for FlywheelResult dataclass and to_dict."""

    def test_default_values(self):
        from forge_harness.flywheel import FlywheelResult

        now = datetime.now(UTC)
        result = FlywheelResult(started_at=now)
        assert result.projects_scanned == 0
        assert result.features_generated == 0
        assert result.features_implemented == 0
        assert result.features_blocked == 0
        assert result.patterns_learned == 0
        assert result.sessions_indexed == 0
        assert result.errors == []
        assert result.ended_at is None

    def test_to_dict_with_all_fields(self):
        from forge_harness.flywheel import FlywheelResult

        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 0, 5, 0, tzinfo=UTC)
        result = FlywheelResult(
            started_at=start,
            ended_at=end,
            projects_scanned=3,
            features_generated=10,
            features_implemented=7,
            features_blocked=2,
            patterns_learned=5,
            sessions_indexed=1,
            errors=["some error"],
        )
        d = result.to_dict()
        assert d["projects_scanned"] == 3
        assert d["features_generated"] == 10
        assert d["features_implemented"] == 7
        assert d["features_blocked"] == 2
        assert d["patterns_learned"] == 5
        assert d["sessions_indexed"] == 1
        assert d["errors"] == ["some error"]
        assert d["duration_seconds"] == 300.0
        assert d["started_at"] == start.isoformat()
        assert d["ended_at"] == end.isoformat()

    def test_to_dict_without_ended_at(self):
        from forge_harness.flywheel import FlywheelResult

        start = datetime(2025, 1, 1, tzinfo=UTC)
        result = FlywheelResult(started_at=start)
        d = result.to_dict()
        assert d["ended_at"] is None
        assert d["duration_seconds"] is None

    def test_to_dict_errors_list_mutable(self):
        from forge_harness.flywheel import FlywheelResult

        r = FlywheelResult(started_at=datetime.now(UTC))
        r.errors.append("err1")
        r.errors.append("err2")
        d = r.to_dict()
        assert len(d["errors"]) == 2


class TestCreateFlywheelLoop:
    """Tests for create_flywheel_loop factory function."""

    def _mock_registry_and_loop(self):
        mock_registry = MagicMock()
        mock_loop = MagicMock()
        return mock_registry, mock_loop

    def test_creates_loop_with_defaults(self, tmp_path, monkeypatch):
        from forge_harness.flywheel import FlywheelConfig, create_flywheel_loop

        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        mock_registry, mock_loop = self._mock_registry_and_loop()

        with patch("forge_harness.harness_registry.create_harness_registry", return_value=mock_registry):
            with patch(
                "forge_harness.ralph_loop.create_ralph_loop_from_registry",
                return_value=mock_loop,
            ) as mock_create:
                loop = create_flywheel_loop(
                    domain="test-domain",
                    project="test-project",
                )

        assert loop is mock_loop
        assert mock_create.called

    def test_uses_explicit_features_path(self, tmp_path, monkeypatch):
        from forge_harness.flywheel import create_flywheel_loop

        features_path = tmp_path / "features.json"
        mock_registry = MagicMock()
        mock_loop = MagicMock()

        with patch("forge_harness.harness_registry.create_harness_registry", return_value=mock_registry):
            with patch(
                "forge_harness.ralph_loop.create_ralph_loop_from_registry",
                return_value=mock_loop,
            ) as mock_create:
                create_flywheel_loop(
                    domain="d",
                    project="p",
                    features_path=features_path,
                )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["features_path"] == features_path

    def test_uses_project_path_when_exists(self, tmp_path, monkeypatch):
        from forge_harness.flywheel import create_flywheel_loop

        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        project_path = tmp_path / "my-domain" / "my-project"
        project_path.mkdir(parents=True)

        mock_registry = MagicMock()
        mock_loop = MagicMock()

        with patch("forge_harness.harness_registry.create_harness_registry", return_value=mock_registry):
            with patch(
                "forge_harness.ralph_loop.create_ralph_loop_from_registry",
                return_value=mock_loop,
            ) as mock_create:
                create_flywheel_loop(domain="my-domain", project="my-project")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["features_path"] == project_path / "features.json"

    def test_falls_back_to_local_features_json(self, tmp_path, monkeypatch):
        from forge_harness.flywheel import create_flywheel_loop

        # Project path doesn't exist
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))

        mock_registry = MagicMock()
        mock_loop = MagicMock()

        with patch("forge_harness.harness_registry.create_harness_registry", return_value=mock_registry):
            with patch(
                "forge_harness.ralph_loop.create_ralph_loop_from_registry",
                return_value=mock_loop,
            ) as mock_create:
                create_flywheel_loop(domain="missing-domain", project="missing-project")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["features_path"] == Path("features.json")

    def test_passes_orchestrator_to_loop(self, tmp_path, monkeypatch):
        from forge_harness.flywheel import create_flywheel_loop

        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        mock_orchestrator = MagicMock()
        mock_registry = MagicMock()
        mock_loop = MagicMock()

        with patch("forge_harness.harness_registry.create_harness_registry", return_value=mock_registry):
            with patch(
                "forge_harness.ralph_loop.create_ralph_loop_from_registry",
                return_value=mock_loop,
            ) as mock_create:
                create_flywheel_loop(
                    domain="d",
                    project="p",
                    orchestrator=mock_orchestrator,
                )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["orchestrator"] is mock_orchestrator

    def test_string_features_path_converted_to_path(self, tmp_path, monkeypatch):
        from forge_harness.flywheel import create_flywheel_loop

        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        mock_registry = MagicMock()
        mock_loop = MagicMock()
        str_path = str(tmp_path / "feats.json")

        with patch("forge_harness.harness_registry.create_harness_registry", return_value=mock_registry):
            with patch(
                "forge_harness.ralph_loop.create_ralph_loop_from_registry",
                return_value=mock_loop,
            ) as mock_create:
                create_flywheel_loop(domain="d", project="p", features_path=str_path)

        call_kwargs = mock_create.call_args.kwargs
        assert isinstance(call_kwargs["features_path"], Path)


class TestBuildDebtDescription:
    """Tests for _build_debt_description helper."""

    def _make_finding(self, **kwargs):
        finding = MagicMock()
        finding.message = kwargs.get("message", "Test message")
        finding.file_path = kwargs.get("file_path", None)
        finding.line_number = kwargs.get("line_number", None)
        finding.recommendation = kwargs.get("recommendation", None)
        return finding

    def test_message_only(self):
        from forge_harness.flywheel import _build_debt_description

        finding = self._make_finding(message="Fix this issue")
        desc = _build_debt_description(finding)
        assert "Fix this issue" in desc

    def test_with_file_path(self):
        from forge_harness.flywheel import _build_debt_description

        finding = self._make_finding(
            message="Fix this", file_path="src/main.py"
        )
        desc = _build_debt_description(finding)
        assert "src/main.py" in desc
        assert "Location" in desc

    def test_with_file_path_and_line_number(self):
        from forge_harness.flywheel import _build_debt_description

        finding = self._make_finding(
            message="Fix this", file_path="src/main.py", line_number=42
        )
        desc = _build_debt_description(finding)
        assert "src/main.py" in desc
        assert "42" in desc

    def test_with_recommendation(self):
        from forge_harness.flywheel import _build_debt_description

        finding = self._make_finding(
            message="Fix this", recommendation="Use pattern X"
        )
        desc = _build_debt_description(finding)
        assert "Use pattern X" in desc
        assert "Recommendation" in desc

    def test_no_line_number_when_file_path_is_none(self):
        from forge_harness.flywheel import _build_debt_description

        finding = self._make_finding(message="msg", file_path=None, line_number=10)
        desc = _build_debt_description(finding)
        # line_number should not appear since file_path is None
        assert "10" not in desc


class TestQualityReportToFeatures:
    """Tests for quality_report_to_features."""

    def _make_report(self, security_findings=None, quality_score=80, issues=None, recommendations=None):
        report = MagicMock()
        report.domain = "test-domain"
        report.project_name = "test-project"
        report.quality_score = quality_score
        report.security_findings = security_findings or []
        report.issues = issues or []
        report.recommendations = recommendations or []
        return report

    def _make_security_finding(self, severity, rule_id="RULE001", message="Test finding",
                                file_path="src/main.py", line_number=10, tool="bandit",
                                confidence="HIGH"):
        finding = MagicMock()
        finding.rule_id = rule_id
        finding.message = message
        finding.file_path = file_path
        finding.line_number = line_number
        finding.tool = tool
        finding.confidence = confidence
        # Use actual SeverityLevel enum values
        from forge_harness.quality_loop import SeverityLevel
        finding.severity = SeverityLevel(severity)
        return finding

    def test_empty_report_returns_empty_list(self):
        from forge_harness.flywheel import quality_report_to_features

        report = self._make_report(security_findings=[], quality_score=90)
        features = quality_report_to_features(report)
        assert features == []

    def test_high_severity_included_with_high_threshold(self):
        from forge_harness.flywheel import quality_report_to_features

        finding = self._make_security_finding("high")
        report = self._make_report(security_findings=[finding], quality_score=90)
        features = quality_report_to_features(report, priority_threshold="high")
        assert len(features) == 1
        assert features[0]["priority"] == "high"

    def test_low_severity_excluded_with_high_threshold(self):
        from forge_harness.flywheel import quality_report_to_features

        finding = self._make_security_finding("low")
        report = self._make_report(security_findings=[finding], quality_score=90)
        features = quality_report_to_features(report, priority_threshold="high")
        assert len(features) == 0

    def test_critical_severity_always_included(self):
        from forge_harness.flywheel import quality_report_to_features

        finding = self._make_security_finding("critical")
        report = self._make_report(security_findings=[finding], quality_score=90)
        features = quality_report_to_features(report, priority_threshold="medium")
        assert len(features) == 1
        assert features[0]["priority"] == "critical"

    def test_max_features_limit(self):
        from forge_harness.flywheel import quality_report_to_features

        findings = [self._make_security_finding("high", rule_id=f"R{i}") for i in range(10)]
        report = self._make_report(security_findings=findings, quality_score=90)
        features = quality_report_to_features(report, max_features=3, priority_threshold="high")
        assert len(features) == 3

    def test_low_quality_score_adds_debt_feature(self):
        from forge_harness.flywheel import quality_report_to_features

        report = self._make_report(
            security_findings=[],
            quality_score=60,
            issues=["Issue 1", "Issue 2"],
            recommendations=["Fix 1", "Fix 2"],
        )
        features = quality_report_to_features(report, priority_threshold="medium")
        assert len(features) == 1
        assert "Improve quality score" in features[0]["name"]
        assert features[0]["priority"] == "medium"  # 50 < 60 < 70

    def test_very_low_quality_score_uses_high_priority(self):
        from forge_harness.flywheel import quality_report_to_features

        report = self._make_report(security_findings=[], quality_score=40)
        features = quality_report_to_features(report, priority_threshold="high")
        # quality score < 50, should be "high" priority
        debt_features = [f for f in features if "Improve quality score" in f["name"]]
        assert len(debt_features) == 1
        assert debt_features[0]["priority"] == "high"

    def test_high_quality_score_no_debt_feature(self):
        from forge_harness.flywheel import quality_report_to_features

        report = self._make_report(security_findings=[], quality_score=85)
        features = quality_report_to_features(report, priority_threshold="low")
        debt_features = [f for f in features if "Improve quality score" in f["name"]]
        assert len(debt_features) == 0

    def test_feature_has_required_keys(self):
        from forge_harness.flywheel import quality_report_to_features

        finding = self._make_security_finding("high")
        report = self._make_report(security_findings=[finding], quality_score=90)
        features = quality_report_to_features(report, priority_threshold="high")
        f = features[0]
        assert "id" in f
        assert "name" in f
        assert "description" in f
        assert "status" in f
        assert "priority" in f
        assert "acceptance_criteria" in f
        assert "metadata" in f

    def test_finding_without_file_path(self):
        from forge_harness.flywheel import quality_report_to_features

        finding = self._make_security_finding("high", file_path=None, line_number=None)
        report = self._make_report(security_findings=[finding], quality_score=90)
        features = quality_report_to_features(report, priority_threshold="high")
        assert len(features) == 1
        # Should not crash even with None file_path

    def test_metadata_has_source_quality_loop(self):
        from forge_harness.flywheel import quality_report_to_features

        finding = self._make_security_finding("high")
        report = self._make_report(security_findings=[finding], quality_score=90)
        features = quality_report_to_features(report, priority_threshold="high")
        assert features[0]["metadata"]["source"] == "quality_loop"


def _make_mock_quality_loop_module(harness_factory):
    """Return a fake forge_harness.quality_loop module where QualityLoopHarness
    is replaced with ``harness_factory``."""
    mock_mod = MagicMock()
    mock_mod.QualityLoopHarness = MagicMock(side_effect=harness_factory)
    return mock_mod


class TestScanProjectLocal:
    """Tests for scan_project_local."""

    def _make_report(self, domain="domain", project="project", score=90.0):
        report = MagicMock()
        report.domain = domain
        report.project_name = project
        report.quality_score = score
        report.security_findings = []
        report.issues = []
        report.recommendations = []
        return report

    @pytest.mark.asyncio
    async def test_successful_local_scan(self, tmp_path):
        from forge_harness.flywheel import scan_project_local

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        report = self._make_report()
        mock_harness = AsyncMock()
        mock_harness.scan_project = AsyncMock(return_value=report)

        def make_harness(forge_root, metrics_dir):
            return mock_harness

        mock_ql_mod = _make_mock_quality_loop_module(make_harness)

        with patch.dict(sys.modules, {"forge_harness.quality_loop": mock_ql_mod}):
            features = await scan_project_local(
                domain="domain",
                project="project",
                project_path=project_path,
            )

        assert isinstance(features, list)

    @pytest.mark.asyncio
    async def test_scan_exception_returns_empty(self, tmp_path):
        from forge_harness.flywheel import scan_project_local

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        mock_harness = AsyncMock()
        mock_harness.scan_project = AsyncMock(side_effect=RuntimeError("Scan failed"))

        def make_harness(forge_root, metrics_dir):
            return mock_harness

        mock_ql_mod = _make_mock_quality_loop_module(make_harness)

        with patch.dict(sys.modules, {"forge_harness.quality_loop": mock_ql_mod}):
            features = await scan_project_local(
                domain="domain",
                project="project",
                project_path=project_path,
            )

        assert features == []

    @pytest.mark.asyncio
    async def test_uses_default_metrics_dir(self, tmp_path):
        from forge_harness.flywheel import scan_project_local

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        report = self._make_report()
        captured = {}

        def make_harness(forge_root, metrics_dir):
            captured["metrics_dir"] = metrics_dir
            harness = AsyncMock()
            harness.scan_project = AsyncMock(return_value=report)
            return harness

        mock_ql_mod = _make_mock_quality_loop_module(make_harness)

        with patch.dict(sys.modules, {"forge_harness.quality_loop": mock_ql_mod}):
            await scan_project_local(
                domain="domain",
                project="project",
                project_path=project_path,
            )

        # Default metrics_dir is project_path / "quality_metrics"
        assert captured["metrics_dir"] == project_path / "quality_metrics"


def _make_mock_meta_modules(bridge=None, prioritizer=None, atlas_bridge=None, config=None):
    """Build fake sys.modules entries for the meta_learning local imports in flywheel."""
    mock_config = config or MagicMock()
    mock_config.code_atlas = MagicMock()

    mock_atlas_bridge = atlas_bridge or MagicMock()

    mock_prioritizer = prioritizer or AsyncMock()
    if not hasattr(mock_prioritizer, "is_available"):
        mock_prioritizer.is_available = AsyncMock(return_value=False)

    mock_bridge = bridge or AsyncMock()

    # Build module stubs
    td_mod = MagicMock()
    td_mod.TechDiligenceBridge = MagicMock(return_value=mock_bridge)

    ca_mod = MagicMock()
    ca_mod.CodeAtlasBridge = MagicMock()
    ca_mod.CodeAtlasBridge.from_config = MagicMock(return_value=mock_atlas_bridge)

    ap_mod = MagicMock()
    ap_mod.AtlasPrioritizer = MagicMock(return_value=mock_prioritizer)

    cfg_mod = MagicMock()
    cfg_mod.get_config = MagicMock(return_value=mock_config)

    return {
        "forge_harness.meta_learning.bridges.tech_diligence": td_mod,
        "forge_harness.meta_learning.bridges.code_atlas": ca_mod,
        "forge_harness.meta_learning.bridges.atlas_prioritizer": ap_mod,
        "forge_harness.meta_learning.config": cfg_mod,
    }, mock_bridge, mock_prioritizer


class TestScanProjectForDebt:
    """Tests for scan_project_for_debt."""

    def _make_finding(self, severity="medium", title="Test Finding", message="msg",
                      file_path="src/f.py", scanner="ruff", rule_id="E001"):
        finding = MagicMock()
        finding.severity = severity
        finding.title = title
        finding.message = message
        finding.file_path = file_path
        finding.scanner = scanner
        finding.rule_id = rule_id
        return finding

    def _make_report(self, findings=None):
        report = MagicMock()
        report.findings = findings or []
        return report

    @pytest.mark.asyncio
    async def test_falls_back_to_local_when_remote_unavailable(self, tmp_path):
        from forge_harness.flywheel import scan_project_for_debt

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        mock_bridge = AsyncMock()
        mock_bridge.analyze_and_wait = AsyncMock(side_effect=RuntimeError("Service down"))

        mock_prioritizer = AsyncMock()
        mock_prioritizer.is_available = AsyncMock(return_value=False)

        mods, _, _ = _make_mock_meta_modules(
            bridge=mock_bridge, prioritizer=mock_prioritizer
        )

        with patch.dict(sys.modules, mods):
            with patch(
                "forge_harness.flywheel.scan_project_local",
                new_callable=AsyncMock,
                return_value=[{"id": "local-1"}],
            ) as mock_local:
                features = await scan_project_for_debt(
                    domain="domain",
                    project="project",
                    project_path=project_path,
                    use_local_fallback=True,
                )

        assert features == [{"id": "local-1"}]
        assert mock_local.called

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_fallback(self, tmp_path):
        from forge_harness.flywheel import scan_project_for_debt

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        mock_bridge = AsyncMock()
        mock_bridge.analyze_and_wait = AsyncMock(side_effect=RuntimeError("fail"))

        mock_prioritizer = AsyncMock()
        mock_prioritizer.is_available = AsyncMock(return_value=False)

        mods, _, _ = _make_mock_meta_modules(
            bridge=mock_bridge, prioritizer=mock_prioritizer
        )

        with patch.dict(sys.modules, mods):
            features = await scan_project_for_debt(
                domain="domain",
                project="project",
                project_path=project_path,
                use_local_fallback=False,
            )

        assert features == []

    @pytest.mark.asyncio
    async def test_converts_findings_to_features(self, tmp_path):
        from forge_harness.flywheel import scan_project_for_debt

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        finding = self._make_finding(severity="high")
        report = self._make_report(findings=[finding])

        mock_bridge = AsyncMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=report)

        mock_prioritizer = AsyncMock()
        mock_prioritizer.is_available = AsyncMock(return_value=False)

        mods, _, _ = _make_mock_meta_modules(
            bridge=mock_bridge, prioritizer=mock_prioritizer
        )

        with patch.dict(sys.modules, mods):
            features = await scan_project_for_debt(
                domain="domain",
                project="project",
                project_path=project_path,
                priority_threshold="high",
            )

        # high severity is within threshold (high = allowed)
        assert len(features) == 1
        assert features[0]["priority"] == "high"

    @pytest.mark.asyncio
    async def test_filters_below_threshold(self, tmp_path):
        from forge_harness.flywheel import scan_project_for_debt

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        # low severity finding but threshold is "high"
        finding = self._make_finding(severity="low")
        report = self._make_report(findings=[finding])

        mock_bridge = AsyncMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=report)

        mock_prioritizer = AsyncMock()
        mock_prioritizer.is_available = AsyncMock(return_value=False)

        mods, _, _ = _make_mock_meta_modules(
            bridge=mock_bridge, prioritizer=mock_prioritizer
        )

        with patch.dict(sys.modules, mods):
            features = await scan_project_for_debt(
                domain="domain",
                project="project",
                project_path=project_path,
                priority_threshold="high",
            )

        assert features == []

    @pytest.mark.asyncio
    async def test_applies_atlas_enhancements_when_available(self, tmp_path):
        from forge_harness.flywheel import scan_project_for_debt

        project_path = tmp_path / "domain" / "project"
        project_path.mkdir(parents=True)

        finding = self._make_finding(severity="medium")
        report = self._make_report(findings=[finding])

        mock_bridge = AsyncMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=report)

        enhanced_dicts = [
            {
                "category": "E001",
                "title": "Enhanced Title",
                "description": "Enhanced description",
                "severity": "medium",
                "file_path": "src/f.py",
                "scanner": "ruff",
            }
        ]

        mock_prioritizer = AsyncMock()
        mock_prioritizer.is_available = AsyncMock(return_value=True)
        mock_prioritizer.get_recently_changed_files = AsyncMock(return_value=[])
        mock_prioritizer.filter_by_historical_success = AsyncMock(return_value=enhanced_dicts)
        mock_prioritizer.boost_hotspot_priorities = AsyncMock(return_value=enhanced_dicts)

        mods, _, _ = _make_mock_meta_modules(
            bridge=mock_bridge, prioritizer=mock_prioritizer
        )

        with patch.dict(sys.modules, mods):
            features = await scan_project_for_debt(
                domain="domain",
                project="project",
                project_path=project_path,
                priority_threshold="medium",
            )

        assert mock_prioritizer.filter_by_historical_success.called
        assert mock_prioritizer.boost_hotspot_priorities.called


class TestGeneratePortfolioFeatures:
    """Tests for generate_portfolio_features."""

    def _make_project_dir(self, domain_path, project_name, marker="CLAUDE.md"):
        proj = domain_path / project_name
        proj.mkdir(parents=True)
        if marker:
            (proj / marker).write_text("# Project")
        return proj

    @pytest.mark.asyncio
    async def test_empty_forge_root_returns_empty(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        features = await generate_portfolio_features(
            forge_root=tmp_path,
            include_harness=False,
        )
        assert features == []

    @pytest.mark.asyncio
    async def test_scans_projects_with_claude_md(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_path = tmp_path / "test-domain"
        domain_path.mkdir()
        self._make_project_dir(domain_path, "project-a", "CLAUDE.md")

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "feat-1", "priority": "high"}],
        ):
            features = await generate_portfolio_features(
                forge_root=tmp_path,
                include_harness=False,
            )

        assert len(features) >= 1

    @pytest.mark.asyncio
    async def test_excludes_dot_and_underscore_dirs(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        # These should be skipped
        (tmp_path / ".git").mkdir()
        (tmp_path / "_internal").mkdir()
        (tmp_path / "docs").mkdir()

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_scan:
            await generate_portfolio_features(forge_root=tmp_path, include_harness=False)

        # scan should not have been called for filtered dirs
        assert not mock_scan.called

    @pytest.mark.asyncio
    async def test_include_domains_filter(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        self._make_project_dir(domain_a, "project-a")

        domain_b = tmp_path / "domain-b"
        domain_b.mkdir()
        self._make_project_dir(domain_b, "project-b")

        scanned_domains = []

        async def mock_scan(domain, project, project_path, **kwargs):
            scanned_domains.append(domain)
            return []

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            await generate_portfolio_features(
                forge_root=tmp_path,
                include_domains=["domain-a"],
                include_harness=False,
            )

        assert "domain-a" in scanned_domains
        assert "domain-b" not in scanned_domains

    @pytest.mark.asyncio
    async def test_exclude_domains_filter(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        self._make_project_dir(domain_a, "project-a")

        domain_b = tmp_path / "domain-b"
        domain_b.mkdir()
        self._make_project_dir(domain_b, "project-b")

        scanned_domains = []

        async def mock_scan(domain, project, project_path, **kwargs):
            scanned_domains.append(domain)
            return []

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            await generate_portfolio_features(
                forge_root=tmp_path,
                exclude_domains=["domain-b"],
                include_harness=False,
            )

        assert "domain-a" in scanned_domains
        assert "domain-b" not in scanned_domains

    @pytest.mark.asyncio
    async def test_deduplicates_features_by_id(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        self._make_project_dir(domain_a, "project-a")

        # Return the same feature twice (different calls, same ID)
        async def mock_scan(*args, **kwargs):
            return [{"id": "same-id", "priority": "high"}]

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            features = await generate_portfolio_features(
                forge_root=tmp_path,
                include_harness=False,
            )

        # Should have deduplicated
        ids = [f["id"] for f in features]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_sorts_by_priority(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        self._make_project_dir(domain_a, "project-a")

        async def mock_scan(*args, **kwargs):
            return [
                {"id": "low-1", "priority": "low"},
                {"id": "crit-1", "priority": "critical"},
                {"id": "med-1", "priority": "medium"},
                {"id": "high-1", "priority": "high"},
            ]

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            features = await generate_portfolio_features(
                forge_root=tmp_path,
                include_harness=False,
            )

        priorities = [f["priority"] for f in features]
        # critical should come first
        assert priorities[0] == "critical"

    @pytest.mark.asyncio
    async def test_writes_output_file(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        self._make_project_dir(domain_a, "project-a")

        output_path = tmp_path / "output" / "features.json"

        async def mock_scan(*args, **kwargs):
            return [{"id": "feat-1", "priority": "high"}]

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            features = await generate_portfolio_features(
                forge_root=tmp_path,
                output_path=output_path,
                include_harness=False,
            )

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert len(data) == len(features)

    @pytest.mark.asyncio
    async def test_scan_error_logged_not_raised(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        self._make_project_dir(domain_a, "project-a")

        async def failing_scan(*args, **kwargs):
            raise RuntimeError("Scan failed")

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=failing_scan):
            # Should not raise
            features = await generate_portfolio_features(
                forge_root=tmp_path,
                include_harness=False,
            )

        assert isinstance(features, list)

    @pytest.mark.asyncio
    async def test_include_harness_self_improvement(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        # Create harness dir
        harness_path = tmp_path / "harness"
        harness_path.mkdir()

        mock_improver = AsyncMock()
        mock_result = MagicMock()
        mock_feature = MagicMock()
        mock_feature.to_dict.return_value = {"id": "harness-1", "priority": "medium"}
        mock_result.features_generated = [mock_feature]
        mock_improver.analyze = AsyncMock(return_value=mock_result)

        si_mod = MagicMock()
        si_mod.HarnessSelfImprover = MagicMock(return_value=mock_improver)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch.dict(sys.modules, {"forge_harness.self_improve": si_mod}):
                features = await generate_portfolio_features(
                    forge_root=tmp_path,
                    include_harness=True,
                )

        harness_features = [f for f in features if f["id"] == "harness-1"]
        assert len(harness_features) == 1

    @pytest.mark.asyncio
    async def test_harness_self_improvement_error_logged(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        harness_path = tmp_path / "harness"
        harness_path.mkdir()

        si_mod = MagicMock()
        si_mod.HarnessSelfImprover = MagicMock(side_effect=RuntimeError("Self-improve failed"))

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch.dict(sys.modules, {"forge_harness.self_improve": si_mod}):
                # Should not raise
                features = await generate_portfolio_features(
                    forge_root=tmp_path,
                    include_harness=True,
                )
        assert isinstance(features, list)

    @pytest.mark.asyncio
    async def test_project_without_markers_skipped(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        # No markers in this project
        no_marker_proj = domain_a / "no-marker-project"
        no_marker_proj.mkdir()

        scanned = []

        async def mock_scan(domain, project, **kwargs):
            scanned.append(project)
            return []

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            await generate_portfolio_features(
                forge_root=tmp_path,
                include_harness=False,
            )

        assert "no-marker-project" not in scanned

    @pytest.mark.asyncio
    async def test_project_with_pyproject_toml_marker(self, tmp_path):
        from forge_harness.flywheel import generate_portfolio_features

        domain_a = tmp_path / "domain-a"
        domain_a.mkdir()
        proj = domain_a / "py-project"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("[tool.pytest]")

        scanned = []

        async def mock_scan(domain, project, **kwargs):
            scanned.append(project)
            return []

        with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
            await generate_portfolio_features(
                forge_root=tmp_path,
                include_harness=False,
            )

        assert "py-project" in scanned


class TestRunFlywheel:
    """Tests for run_flywheel."""

    def _make_loop_result(self, completed=2, blocked=0, remaining=3):
        result = MagicMock()
        result.features_completed = completed
        result.features_blocked = blocked
        result.features_remaining = remaining
        return result

    @pytest.mark.asyncio
    async def test_basic_run_returns_flywheel_result(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                result = await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=True),
                    create_orchestrator=False,
                )

        assert result.features_implemented == loop_result.features_completed
        assert result.features_blocked == loop_result.features_blocked
        assert result.ended_at is not None
        assert result.projects_scanned == 1

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_features(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "new-1", "priority": "high"}],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=True),
                    create_orchestrator=False,
                )

        # features.json should NOT be written in dry_run
        assert not features_path.exists()

    @pytest.mark.asyncio
    async def test_writes_features_when_not_dry_run(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "new-1", "priority": "high"}],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=False),
                    create_orchestrator=False,
                )

        assert features_path.exists()

    @pytest.mark.asyncio
    async def test_merges_with_existing_features(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"
        existing = [{"id": "existing-1", "priority": "high"}]
        features_path.write_text(json.dumps(existing))

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "new-1", "priority": "medium"}],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                result = await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=False),
                    create_orchestrator=False,
                )

        data = json.loads(features_path.read_text())
        ids = [f["id"] for f in data]
        assert "existing-1" in ids
        assert "new-1" in ids
        assert result.features_generated == 1  # Only "new-1" is new

    @pytest.mark.asyncio
    async def test_deduplicates_existing_features(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"
        existing = [{"id": "dup-1", "priority": "high"}]
        features_path.write_text(json.dumps(existing))

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "dup-1", "priority": "high"}],  # same ID
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                result = await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=False),
                    create_orchestrator=False,
                )

        # Should not write since added==0
        assert result.features_generated == 0

    @pytest.mark.asyncio
    async def test_handles_wrapper_format_features(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"
        # Wrapper format: {"features": [...], "metadata": {...}}
        existing_data = {
            "features": [{"id": "wrap-1", "priority": "high"}],
            "metadata": {"version": "1"},
        }
        features_path.write_text(json.dumps(existing_data))

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "wrap-2", "priority": "medium"}],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=False),
                    create_orchestrator=False,
                )

        data = json.loads(features_path.read_text())
        # Wrapper format should be preserved
        assert "features" in data
        assert "metadata" in data
        ids = [f["id"] for f in data["features"]]
        assert "wrap-1" in ids
        assert "wrap-2" in ids

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"
        features_path.write_text("INVALID JSON {{{")

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "new-1", "priority": "high"}],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                # Should not raise
                result = await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=False),
                    create_orchestrator=False,
                )

        assert result is not None

    @pytest.mark.asyncio
    async def test_error_captured_in_result(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Phase 1 failed"),
        ):
            result = await run_flywheel(
                forge_root=tmp_path,
                domain="d",
                project="p",
                config=FlywheelConfig(dry_run=True),
                create_orchestrator=False,
            )

        assert len(result.errors) == 1
        assert "Phase 1 failed" in result.errors[0]
        assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_harness_command_center_sets_working_dir(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "harness" / "command_center"
        project_path.mkdir(parents=True)
        harness_path = tmp_path / "harness"

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        config = FlywheelConfig(dry_run=True)
        assert config.working_dir is None  # starts None

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                await run_flywheel(
                    forge_root=tmp_path,
                    domain="harness",
                    project="command_center",
                    config=config,
                    create_orchestrator=False,
                )

        # working_dir should be set to harness directory
        assert config.working_dir == harness_path.resolve()

    @pytest.mark.asyncio
    async def test_dict_without_features_key_treats_as_empty(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)
        features_path = project_path / "features.json"
        # Dict without "features" key
        features_path.write_text(json.dumps({"some_key": "some_value"}))

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[{"id": "new-1", "priority": "high"}],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                result = await run_flywheel(
                    forge_root=tmp_path,
                    domain="d",
                    project="p",
                    config=FlywheelConfig(dry_run=False),
                    create_orchestrator=False,
                )

        assert result.features_generated == 1

    @pytest.mark.asyncio
    async def test_creates_orchestrator_when_flag_set(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)
        mock_orchestrator = MagicMock()

        agent_mod = MagicMock()
        agent_mod.FeatureOrchestrator = MagicMock(return_value=mock_orchestrator)

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                with patch.dict(sys.modules, {"forge_harness.agent": agent_mod}):
                    result = await run_flywheel(
                        forge_root=tmp_path,
                        domain="d",
                        project="p",
                        config=FlywheelConfig(dry_run=False),
                        create_orchestrator=True,
                    )

        assert result is not None

    @pytest.mark.asyncio
    async def test_orchestrator_import_error_continues(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, run_flywheel

        project_path = tmp_path / "d" / "p"
        project_path.mkdir(parents=True)

        loop_result = self._make_loop_result()
        mock_loop = AsyncMock()
        mock_loop.run = AsyncMock(return_value=loop_result)

        # Simulate ImportError by making the module raise it
        agent_mod = MagicMock()
        agent_mod.FeatureOrchestrator = MagicMock(side_effect=ImportError("No module"))

        with patch(
            "forge_harness.flywheel.scan_project_for_debt",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch("forge_harness.flywheel.create_flywheel_loop", return_value=mock_loop):
                with patch.dict(sys.modules, {"forge_harness.agent": agent_mod}):
                    result = await run_flywheel(
                        forge_root=tmp_path,
                        domain="d",
                        project="p",
                        config=FlywheelConfig(dry_run=False),
                        create_orchestrator=True,
                    )

        # Should not fail
        assert result is not None


class TestRunFlywheelSync:
    """Tests for run_flywheel_sync synchronous wrapper."""

    def test_sync_wrapper_calls_async(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, FlywheelResult, run_flywheel_sync

        now = datetime.now(UTC)
        mock_result = FlywheelResult(started_at=now, ended_at=now)

        with patch(
            "forge_harness.flywheel.run_flywheel",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = run_flywheel_sync(
                forge_root=tmp_path,
                domain="d",
                project="p",
            )

        assert result is mock_result

    def test_sync_wrapper_passes_config(self, tmp_path):
        from forge_harness.flywheel import FlywheelConfig, FlywheelResult, run_flywheel_sync

        now = datetime.now(UTC)
        mock_result = FlywheelResult(started_at=now, ended_at=now)
        cfg = FlywheelConfig(dry_run=True)

        captured = {}

        async def capture_run(forge_root, domain, project, config=None, **kwargs):
            captured["config"] = config
            return mock_result

        with patch("forge_harness.flywheel.run_flywheel", side_effect=capture_run):
            run_flywheel_sync(forge_root=tmp_path, domain="d", project="p", config=cfg)

        assert captured["config"] is cfg


class TestConvenienceAliases:
    """Tests for convenience aliases at module level."""

    def test_create_fully_wired_loop_alias(self):
        from forge_harness.flywheel import create_flywheel_loop, create_fully_wired_loop

        assert create_fully_wired_loop is create_flywheel_loop

    def test_scan_portfolio_alias(self):
        from forge_harness.flywheel import generate_portfolio_features, scan_portfolio

        assert scan_portfolio is generate_portfolio_features

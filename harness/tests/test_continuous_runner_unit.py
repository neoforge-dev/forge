"""
Unit tests for forge_harness.continuous_runner.runner.

Coverage targets:
- ContinuousRalphRunner.__init__
- ContinuousRalphRunner._get_approval_queue (lazy init)
- ContinuousRalphRunner._get_notification_harness (lazy init)
- ContinuousRalphRunner._get_decision_engine (lazy init)
- ContinuousRalphRunner._classify_tier
- ContinuousRalphRunner._request_human_approval
- ContinuousRalphRunner._run_single_loop
- ContinuousRalphRunner.run
- ContinuousRalphRunner._handle_shutdown
- main() entry point
"""

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


def _make_mock_request(request_id="req-001", status_value="pending"):
    """Build a minimal ApprovalRequest-like mock."""
    req = MagicMock()
    req.id = request_id
    req.status = MagicMock()
    req.status.value = status_value
    return req


def _make_loop_result(
    success=True,
    iterations=5,
    features_completed=3,
    features_blocked=0,
    features_remaining=0,
    duration_seconds=10.0,
):
    """Build a LoopResult-like mock."""
    result = MagicMock()
    result.success = success
    result.iterations = iterations
    result.features_completed = features_completed
    result.features_blocked = features_blocked
    result.features_remaining = features_remaining
    result.duration_seconds = duration_seconds
    return result


@pytest.fixture()
def runner(tmp_path):
    """ContinuousRalphRunner with a real temp directory so Path ops work."""
    from forge_harness.continuous_runner import ContinuousRalphRunner

    return ContinuousRalphRunner(
        domain="test-domain",
        project="test-project",
        forge_root=tmp_path,
        approval_timeout_hours=0.001,  # tiny timeout for fast tests
        loop_cooldown_seconds=0.01,
        max_iterations_per_run=5,
    )


# ---------------------------------------------------------------------------
# __init__ tests
# ---------------------------------------------------------------------------


class TestContinuousRalphRunnerInit:
    def test_default_features_path_derived(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(
            domain="my-domain",
            project="my-project",
            forge_root=tmp_path,
        )
        expected = tmp_path / "my-domain" / "my-project" / "features.json"
        assert runner.features_path == expected

    def test_explicit_features_path_used(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        custom = tmp_path / "custom_features.json"
        runner = ContinuousRalphRunner(
            domain="d",
            project="p",
            forge_root=tmp_path,
            features_path=custom,
        )
        assert runner.features_path == custom

    def test_forge_root_defaults_to_cwd(self):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(domain="d", project="p")
        assert runner.forge_root == Path.cwd()

    def test_approval_timeout_converted_to_timedelta(self):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(
            domain="d", project="p", approval_timeout_hours=2.0
        )
        assert runner.approval_timeout == timedelta(hours=2.0)

    def test_initial_state_not_running(self, runner):
        assert runner._running is False
        assert runner._current_feature is None
        assert runner._approval_queue is None
        assert runner._notification_harness is None
        assert runner._decision_engine is None

    def test_parameters_stored(self, tmp_path):
        from forge_harness.continuous_runner import ContinuousRalphRunner

        runner = ContinuousRalphRunner(
            domain="dom",
            project="proj",
            forge_root=tmp_path,
            loop_cooldown_seconds=120.0,
            max_iterations_per_run=99,
        )
        assert runner.domain == "dom"
        assert runner.project == "proj"
        assert runner.loop_cooldown == 120.0
        assert runner.max_iterations_per_run == 99


# ---------------------------------------------------------------------------
# Lazy initializer tests
# ---------------------------------------------------------------------------


class TestLazyInitializers:
    @pytest.mark.asyncio
    async def test_get_approval_queue_creates_once(self, runner):
        mock_queue = MagicMock()
        with patch(
            "forge_harness.continuous_runner.runner.create_approval_queue_from_env",
            return_value=mock_queue,
        ) as patched:
            q1 = await runner._get_approval_queue()
            q2 = await runner._get_approval_queue()
            assert q1 is mock_queue
            assert q2 is mock_queue
            patched.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_notification_harness_creates_once(self, runner):
        mock_notifier = MagicMock()
        with patch(
            "forge_harness.continuous_runner.runner.create_notification_harness",
            return_value=mock_notifier,
        ) as patched:
            n1 = await runner._get_notification_harness()
            n2 = await runner._get_notification_harness()
            assert n1 is mock_notifier
            assert n2 is mock_notifier
            patched.assert_called_once()

    def test_get_decision_engine_creates_once(self, runner):
        mock_engine = MagicMock()
        with patch(
            "forge_harness.continuous_runner.runner.DecisionEngine",
            return_value=mock_engine,
        ) as patched:
            e1 = runner._get_decision_engine()
            e2 = runner._get_decision_engine()
            assert e1 is mock_engine
            assert e2 is mock_engine
            patched.assert_called_once()

    def test_get_decision_engine_returns_cached(self, runner):
        sentinel = object()
        runner._decision_engine = sentinel
        assert runner._get_decision_engine() is sentinel


# ---------------------------------------------------------------------------
# _classify_tier tests
# ---------------------------------------------------------------------------


class TestClassifyTier:
    def _make_engine_mock(self, tier_value="phone"):
        engine = MagicMock()
        tier = MagicMock()
        tier.value = tier_value
        engine.classify_tier.return_value = tier
        return engine

    def test_classify_tier_returns_string(self, runner):
        engine = self._make_engine_mock("watch")
        runner._decision_engine = engine

        result = runner._classify_tier("feat-01", "My Feature", "feature_complete", 0.1)

        assert result == "watch"
        engine.classify_tier.assert_called_once()

    def test_low_risk_diligence_signal(self, runner):
        engine = self._make_engine_mock("watch")
        runner._decision_engine = engine

        runner._classify_tier("f", "n", "test_retry", risk_score=0.1)

        # Verify the diligence kwarg has risk_level="low"
        call_args = engine.classify_tier.call_args
        _, kwargs = call_args[0], call_args[1]  # positional args passed
        # engine.classify_tier is called as positional: (action, scores, context, diligence)
        diligence_arg = call_args[0][3]
        assert diligence_arg.risk_level == "low"

    def test_medium_risk_diligence_signal(self, runner):
        engine = self._make_engine_mock("phone")
        runner._decision_engine = engine

        runner._classify_tier("f", "n", "deploy", risk_score=0.45)

        diligence_arg = engine.classify_tier.call_args[0][3]
        assert diligence_arg.risk_level == "medium"

    def test_high_risk_diligence_signal(self, runner):
        engine = self._make_engine_mock("desktop")
        runner._decision_engine = engine

        runner._classify_tier("f", "n", "blocked", risk_score=0.9)

        diligence_arg = engine.classify_tier.call_args[0][3]
        assert diligence_arg.risk_level == "high"

    def test_unknown_action_type_defaults_to_human_review(self, runner):
        from forge_harness.meta_learning.schemas import DecisionAction

        engine = self._make_engine_mock("phone")
        runner._decision_engine = engine

        runner._classify_tier("f", "n", "nonexistent_action_xyz", risk_score=0.0)

        action_arg = engine.classify_tier.call_args[0][0]
        assert action_arg == DecisionAction.HUMAN_REVIEW_REQUIRED

    def test_known_action_types_mapped_correctly(self, runner):
        from forge_harness.meta_learning.schemas import DecisionAction

        engine = self._make_engine_mock("phone")
        runner._decision_engine = engine

        action_map = {
            "feature_complete": DecisionAction.HUMAN_REVIEW_REQUIRED,
            "deploy": DecisionAction.HUMAN_REVIEW_REQUIRED,
            "test_retry": DecisionAction.PROCEED_WITH_CAUTION,
            "blocked": DecisionAction.BLOCK,
        }

        for action_type, expected_action in action_map.items():
            engine.classify_tier.reset_mock()
            runner._classify_tier("f", "n", action_type)
            action_arg = engine.classify_tier.call_args[0][0]
            assert action_arg == expected_action, f"Failed for action_type={action_type}"

    def test_context_built_with_correct_domain_and_project(self, runner):
        engine = self._make_engine_mock("watch")
        runner._decision_engine = engine

        runner._classify_tier("feat-42", "Some Feature", "deploy")

        context_arg = engine.classify_tier.call_args[0][2]
        assert context_arg.domain == runner.domain
        assert context_arg.project == runner.project
        assert context_arg.feature_id == "feat-42"


# ---------------------------------------------------------------------------
# _request_human_approval tests
# ---------------------------------------------------------------------------


class TestRequestHumanApproval:
    def _patch_runner(self, runner, status_sequence=None):
        """
        Patch the approval queue and notification harness on the runner.
        status_sequence: list of status values returned by successive
                         get_request calls (e.g. ["pending", "approved"]).
        """
        from forge_harness.approval_queue import ApprovalType

        mock_request = _make_mock_request("req-abc", "pending")
        mock_queue = AsyncMock()
        mock_queue.create_request = AsyncMock(return_value=mock_request)

        if status_sequence:
            side_effects = [
                _make_mock_request("req-abc", sv) for sv in status_sequence
            ]
            mock_queue.get_request = AsyncMock(side_effect=side_effects)
        else:
            # Default: always pending (will hit timeout)
            mock_queue.get_request = AsyncMock(
                return_value=_make_mock_request("req-abc", "pending")
            )

        mock_notifier = AsyncMock()
        mock_notifier.send_approval_notification = AsyncMock()

        runner._approval_queue = mock_queue
        runner._notification_harness = mock_notifier

        # Stub the decision engine tier
        engine = MagicMock()
        tier_mock = MagicMock()
        tier_mock.value = "phone"
        engine.classify_tier.return_value = tier_mock
        runner._decision_engine = engine

        return mock_queue, mock_notifier

    @pytest.mark.asyncio
    async def test_approved_returns_true(self, runner):
        from forge_harness.approval_queue import ApprovalType

        mock_queue, _ = self._patch_runner(runner, status_sequence=["approved"])

        result = await runner._request_human_approval(
            title="Test",
            description="desc",
            approval_type=ApprovalType.FEATURE,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_rejected_returns_false(self, runner):
        from forge_harness.approval_queue import ApprovalType

        self._patch_runner(runner, status_sequence=["rejected"])

        result = await runner._request_human_approval(
            title="Test",
            description="desc",
            approval_type=ApprovalType.FEATURE,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, runner):
        """Approval timeout (very short timeout set in fixture) returns False."""
        from forge_harness.approval_queue import ApprovalType

        # Always return pending (infinite) so we hit the deadline naturally.
        # Use _patch_runner without a sequence so it defaults to pending return_value.
        self._patch_runner(runner)

        # Make asyncio.sleep a no-op so the poll loop spins and drains the deadline fast.
        with patch(
            "forge_harness.continuous_runner.runner.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await runner._request_human_approval(
                title="Test",
                description="desc",
                approval_type=ApprovalType.FEATURE,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_shutdown_during_wait_returns_false(self, runner):
        from forge_harness.approval_queue import ApprovalType

        self._patch_runner(runner)
        # Trigger shutdown event before calling
        runner._shutdown_event.set()

        result = await runner._request_human_approval(
            title="Test",
            description="desc",
            approval_type=ApprovalType.FEATURE,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_sends_notification(self, runner):
        from forge_harness.approval_queue import ApprovalType

        _, mock_notifier = self._patch_runner(runner, status_sequence=["approved"])

        await runner._request_human_approval(
            title="My Title",
            description="My Desc",
            approval_type=ApprovalType.FEATURE,
        )

        mock_notifier.send_approval_notification.assert_awaited_once()
        call_kwargs = mock_notifier.send_approval_notification.call_args.kwargs
        assert call_kwargs["title"] == "My Title"
        assert call_kwargs["description"] == "My Desc"

    @pytest.mark.asyncio
    async def test_dashboard_url_appended_with_request_id(self, runner):
        from forge_harness.approval_queue import ApprovalType

        _, mock_notifier = self._patch_runner(runner, status_sequence=["approved"])

        with patch.dict(os.environ, {"FORGE_DASHBOARD_URL": "https://example.com"}):
            await runner._request_human_approval(
                title="T",
                description="D",
                approval_type=ApprovalType.FEATURE,
            )

        call_kwargs = mock_notifier.send_approval_notification.call_args.kwargs
        assert call_kwargs["dashboard_url"] == "https://example.com/approvals/req-abc"

    @pytest.mark.asyncio
    async def test_no_dashboard_url_env_passes_none(self, runner):
        from forge_harness.approval_queue import ApprovalType

        _, mock_notifier = self._patch_runner(runner, status_sequence=["approved"])

        # Make sure env var is not set
        env = {k: v for k, v in os.environ.items() if k != "FORGE_DASHBOARD_URL"}
        with patch.dict(os.environ, env, clear=True):
            await runner._request_human_approval(
                title="T",
                description="D",
                approval_type=ApprovalType.FEATURE,
            )

        call_kwargs = mock_notifier.send_approval_notification.call_args.kwargs
        assert call_kwargs["dashboard_url"] is None

    @pytest.mark.asyncio
    async def test_feature_id_fallback_to_unknown(self, runner):
        from forge_harness.approval_queue import ApprovalType

        self._patch_runner(runner, status_sequence=["approved"])

        # No feature_id supplied — should not raise
        result = await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
            feature_id=None,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_context_metadata_passed_to_create_request(self, runner):
        from forge_harness.approval_queue import ApprovalType

        mock_queue, _ = self._patch_runner(runner, status_sequence=["approved"])
        ctx = {"key": "value", "num": 42}

        await runner._request_human_approval(
            title="T",
            description="D",
            approval_type=ApprovalType.FEATURE,
            context=ctx,
        )

        call_kwargs = mock_queue.create_request.call_args.kwargs
        assert call_kwargs["metadata"] == ctx


# ---------------------------------------------------------------------------
# _run_single_loop tests
# ---------------------------------------------------------------------------


class TestRunSingleLoop:
    def _setup_runner_with_features(self, runner, tmp_path):
        """Create features.json so the path exists."""
        features_file = tmp_path / "test-domain" / "test-project" / "features.json"
        features_file.parent.mkdir(parents=True, exist_ok=True)
        features_file.write_text("[]")
        runner.features_path = features_file
        return features_file

    @pytest.mark.asyncio
    async def test_returns_false_when_features_file_missing(self, runner):
        runner.features_path = Path("/nonexistent/features.json")
        result = await runner._run_single_loop()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_features_remaining(self, runner, tmp_path):
        self._setup_runner_with_features(runner, tmp_path)
        loop_result = _make_loop_result(
            features_completed=0,
            features_blocked=0,
            features_remaining=5,
        )

        with (
            patch("forge_harness.continuous_runner.runner.FeatureStore"),
            patch(
                "forge_harness.continuous_runner.runner.RalphLoopHarness"
            ) as MockLoop,
        ):
            mock_loop_instance = AsyncMock()
            mock_loop_instance.run = AsyncMock(return_value=loop_result)
            MockLoop.return_value = mock_loop_instance

            result = await runner._run_single_loop()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_features_remaining(self, runner, tmp_path):
        self._setup_runner_with_features(runner, tmp_path)
        loop_result = _make_loop_result(
            features_completed=0,
            features_blocked=0,
            features_remaining=0,
        )

        with (
            patch("forge_harness.continuous_runner.runner.FeatureStore"),
            patch(
                "forge_harness.continuous_runner.runner.RalphLoopHarness"
            ) as MockLoop,
        ):
            mock_loop_instance = AsyncMock()
            mock_loop_instance.run = AsyncMock(return_value=loop_result)
            MockLoop.return_value = mock_loop_instance

            result = await runner._run_single_loop()

        assert result is False

    @pytest.mark.asyncio
    async def test_requests_approval_on_features_completed(self, runner, tmp_path):
        self._setup_runner_with_features(runner, tmp_path)
        loop_result = _make_loop_result(
            features_completed=2,
            features_blocked=0,
            features_remaining=1,
        )

        with (
            patch("forge_harness.continuous_runner.runner.FeatureStore"),
            patch(
                "forge_harness.continuous_runner.runner.RalphLoopHarness"
            ) as MockLoop,
        ):
            mock_loop_instance = AsyncMock()
            mock_loop_instance.run = AsyncMock(return_value=loop_result)
            MockLoop.return_value = mock_loop_instance

            with patch.object(
                runner,
                "_request_human_approval",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_approval:
                result = await runner._run_single_loop()

        mock_approval.assert_awaited()
        assert result is True

    @pytest.mark.asyncio
    async def test_approval_denied_returns_false(self, runner, tmp_path):
        self._setup_runner_with_features(runner, tmp_path)
        loop_result = _make_loop_result(
            features_completed=1,
            features_blocked=0,
            features_remaining=2,
        )

        with (
            patch("forge_harness.continuous_runner.runner.FeatureStore"),
            patch(
                "forge_harness.continuous_runner.runner.RalphLoopHarness"
            ) as MockLoop,
        ):
            mock_loop_instance = AsyncMock()
            mock_loop_instance.run = AsyncMock(return_value=loop_result)
            MockLoop.return_value = mock_loop_instance

            with patch.object(
                runner,
                "_request_human_approval",
                new_callable=AsyncMock,
                return_value=False,
            ):
                result = await runner._run_single_loop()

        assert result is False

    @pytest.mark.asyncio
    async def test_blocked_features_trigger_approval(self, runner, tmp_path):
        self._setup_runner_with_features(runner, tmp_path)
        loop_result = _make_loop_result(
            features_completed=0,
            features_blocked=3,
            features_remaining=2,
        )

        with (
            patch("forge_harness.continuous_runner.runner.FeatureStore"),
            patch(
                "forge_harness.continuous_runner.runner.RalphLoopHarness"
            ) as MockLoop,
        ):
            mock_loop_instance = AsyncMock()
            mock_loop_instance.run = AsyncMock(return_value=loop_result)
            MockLoop.return_value = mock_loop_instance

            with patch.object(
                runner,
                "_request_human_approval",
                new_callable=AsyncMock,
                return_value=True,  # whether approved or not, runner continues
            ) as mock_approval:
                result = await runner._run_single_loop()

        # Runner continues because features_remaining > 0
        assert result is True
        mock_approval.assert_awaited()

    @pytest.mark.asyncio
    async def test_loop_config_constructed_with_correct_params(self, runner, tmp_path):
        self._setup_runner_with_features(runner, tmp_path)
        # No completed/blocked features so _request_human_approval is never called
        loop_result = _make_loop_result(
            features_completed=0,
            features_blocked=0,
            features_remaining=0,
        )

        with (
            patch("forge_harness.continuous_runner.runner.FeatureStore"),
            patch(
                "forge_harness.continuous_runner.runner.RalphLoopHarness"
            ) as MockLoop,
            patch("forge_harness.continuous_runner.runner.LoopConfig") as MockConfig,
        ):
            mock_loop_instance = AsyncMock()
            mock_loop_instance.run = AsyncMock(return_value=loop_result)
            MockLoop.return_value = mock_loop_instance
            MockConfig.return_value = MagicMock()

            await runner._run_single_loop()

        MockConfig.assert_called_once_with(
            max_iterations=runner.max_iterations_per_run,
            max_failures_per_feature=3,
            checkpoint_interval=5,
            timeout_seconds=300,
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# _handle_shutdown tests
# ---------------------------------------------------------------------------


class TestHandleShutdown:
    def test_sets_running_false(self, runner):
        runner._running = True
        runner._handle_shutdown()
        assert runner._running is False

    def test_sets_shutdown_event(self, runner):
        runner._handle_shutdown()
        assert runner._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# run() method tests
# ---------------------------------------------------------------------------


class TestRunMethod:
    @pytest.mark.asyncio
    async def test_run_exits_immediately_on_shutdown_event(self, runner):
        """If _run_single_loop returns False, runner enters idle; shutdown exits it."""

        async def fake_single_loop():
            runner._shutdown_event.set()  # trigger shutdown immediately
            return False

        with (
            patch.object(runner, "_run_single_loop", side_effect=fake_single_loop),
            patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop,
        ):
            mock_event_loop = MagicMock()
            mock_event_loop.add_signal_handler = MagicMock()
            mock_get_loop.return_value = mock_event_loop

            await runner.run()

        assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_sets_running_true_then_false(self, runner):
        running_states = []

        async def fake_single_loop():
            running_states.append(runner._running)
            runner._shutdown_event.set()
            return False

        with (
            patch.object(runner, "_run_single_loop", side_effect=fake_single_loop),
            patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop,
        ):
            mock_event_loop = MagicMock()
            mock_event_loop.add_signal_handler = MagicMock()
            mock_get_loop.return_value = mock_event_loop

            await runner.run()

        assert running_states[0] is True
        assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_handles_exception_in_loop_and_retries(self, runner):
        """An exception in _run_single_loop should be caught and runner should retry."""
        call_count = [0]

        async def fake_single_loop():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("transient error")
            runner._shutdown_event.set()
            return False

        with (
            patch.object(runner, "_run_single_loop", side_effect=fake_single_loop),
            patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop,
            patch(
                "forge_harness.continuous_runner.runner.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_event_loop = MagicMock()
            mock_event_loop.add_signal_handler = MagicMock()
            mock_get_loop.return_value = mock_event_loop

            await runner.run()

        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_run_continues_loop_when_features_remaining(self, runner):
        """When _run_single_loop returns True, runner sleeps then loops again."""
        call_count = [0]

        async def fake_single_loop():
            call_count[0] += 1
            if call_count[0] >= 2:
                runner._shutdown_event.set()
                return False
            return True  # more work available

        with (
            patch.object(runner, "_run_single_loop", side_effect=fake_single_loop),
            patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop,
            patch(
                "forge_harness.continuous_runner.runner.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            mock_event_loop = MagicMock()
            mock_event_loop.add_signal_handler = MagicMock()
            mock_get_loop.return_value = mock_event_loop

            await runner.run()

        assert call_count[0] == 2
        mock_sleep.assert_awaited()

    @pytest.mark.asyncio
    async def test_run_handles_cancelled_error_gracefully(self, runner):
        async def fake_single_loop():
            raise asyncio.CancelledError()

        with (
            patch.object(runner, "_run_single_loop", side_effect=fake_single_loop),
            patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop,
        ):
            mock_event_loop = MagicMock()
            mock_event_loop.add_signal_handler = MagicMock()
            mock_get_loop.return_value = mock_event_loop

            # Should not raise
            await runner.run()

        assert runner._running is False

    @pytest.mark.asyncio
    async def test_run_registers_signal_handlers(self, runner):
        import signal

        runner._shutdown_event.set()  # Prevent looping

        with (
            patch.object(
                runner, "_run_single_loop", new_callable=AsyncMock, return_value=False
            ),
            patch(
                "forge_harness.continuous_runner.runner.asyncio.get_event_loop"
            ) as mock_get_loop,
            patch(
                "forge_harness.continuous_runner.runner.asyncio.wait_for",
                new_callable=AsyncMock,
            ),
        ):
            mock_event_loop = MagicMock()
            mock_event_loop.add_signal_handler = MagicMock()
            mock_get_loop.return_value = mock_event_loop

            await runner.run()

        calls = mock_event_loop.add_signal_handler.call_args_list
        registered_signals = {c[0][0] for c in calls}
        assert signal.SIGTERM in registered_signals
        assert signal.SIGINT in registered_signals


# ---------------------------------------------------------------------------
# main() function tests
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_exits_without_domain(self):
        env = {k: v for k, v in os.environ.items() if k not in ("FORGE_DOMAIN", "FORGE_PROJECT")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                from forge_harness.continuous_runner import main
                main()
            assert exc_info.value.code == 1

    def test_main_exits_without_project(self):
        env = {**os.environ, "FORGE_DOMAIN": "my-domain"}
        env.pop("FORGE_PROJECT", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                from forge_harness.continuous_runner import main
                main()
            assert exc_info.value.code == 1

    def test_main_creates_runner_with_env_vars(self, tmp_path):
        env = {
            "FORGE_DOMAIN": "brandfocus-ai",
            "FORGE_PROJECT": "voice-coach",
            "APPROVAL_TIMEOUT_HOURS": "12",
            "LOOP_COOLDOWN_SECONDS": "30",
            "MAX_ITERATIONS_PER_RUN": "25",
        }
        captured_runner = []

        def fake_asyncio_run(coro):
            # Don't actually run — just capture the runner
            coro.close()

        with patch.dict(os.environ, env, clear=True):
            with (
                patch("forge_harness.continuous_runner.runner.asyncio.run", side_effect=fake_asyncio_run),
                patch(
                    "forge_harness.continuous_runner.runner.ContinuousRalphRunner"
                ) as MockRunner,
            ):
                mock_runner_instance = MagicMock()
                mock_runner_instance.run = MagicMock(return_value=MagicMock())
                MockRunner.return_value = mock_runner_instance

                from forge_harness.continuous_runner import main
                main()

            call_kwargs = MockRunner.call_args.kwargs
            assert call_kwargs["domain"] == "brandfocus-ai"
            assert call_kwargs["project"] == "voice-coach"
            assert call_kwargs["approval_timeout_hours"] == 12.0
            assert call_kwargs["loop_cooldown_seconds"] == 30.0
            assert call_kwargs["max_iterations_per_run"] == 25

    def test_main_uses_default_env_values(self):
        env = {
            "FORGE_DOMAIN": "d",
            "FORGE_PROJECT": "p",
        }
        # Remove optional vars
        for k in ("APPROVAL_TIMEOUT_HOURS", "LOOP_COOLDOWN_SECONDS", "MAX_ITERATIONS_PER_RUN"):
            env.pop(k, None)

        def fake_asyncio_run(coro):
            coro.close()

        with patch.dict(os.environ, env, clear=True):
            with (
                patch("forge_harness.continuous_runner.runner.asyncio.run", side_effect=fake_asyncio_run),
                patch(
                    "forge_harness.continuous_runner.runner.ContinuousRalphRunner"
                ) as MockRunner,
            ):
                mock_runner_instance = MagicMock()
                mock_runner_instance.run = MagicMock(return_value=MagicMock())
                MockRunner.return_value = mock_runner_instance

                from forge_harness.continuous_runner import main
                main()

            call_kwargs = MockRunner.call_args.kwargs
            assert call_kwargs["approval_timeout_hours"] == 24.0
            assert call_kwargs["loop_cooldown_seconds"] == 60.0
            assert call_kwargs["max_iterations_per_run"] == 50

    def test_main_uses_features_path_env(self, tmp_path):
        features = tmp_path / "feats.json"
        features.write_text("[]")
        env = {
            "FORGE_DOMAIN": "d",
            "FORGE_PROJECT": "p",
            "FEATURES_PATH": str(features),
        }

        def fake_asyncio_run(coro):
            coro.close()

        with patch.dict(os.environ, env, clear=True):
            with (
                patch("forge_harness.continuous_runner.runner.asyncio.run", side_effect=fake_asyncio_run),
                patch(
                    "forge_harness.continuous_runner.runner.ContinuousRalphRunner"
                ) as MockRunner,
            ):
                mock_runner_instance = MagicMock()
                mock_runner_instance.run = MagicMock(return_value=MagicMock())
                MockRunner.return_value = mock_runner_instance

                from forge_harness.continuous_runner import main
                main()

            call_kwargs = MockRunner.call_args.kwargs
            assert call_kwargs["features_path"] == features

    def test_main_no_features_path_env_passes_none(self):
        env = {"FORGE_DOMAIN": "d", "FORGE_PROJECT": "p"}
        env.pop("FEATURES_PATH", None)

        def fake_asyncio_run(coro):
            coro.close()

        with patch.dict(os.environ, env, clear=True):
            with (
                patch("forge_harness.continuous_runner.runner.asyncio.run", side_effect=fake_asyncio_run),
                patch(
                    "forge_harness.continuous_runner.runner.ContinuousRalphRunner"
                ) as MockRunner,
            ):
                mock_runner_instance = MagicMock()
                mock_runner_instance.run = MagicMock(return_value=MagicMock())
                MockRunner.return_value = mock_runner_instance

                from forge_harness.continuous_runner import main
                main()

            call_kwargs = MockRunner.call_args.kwargs
            assert call_kwargs["features_path"] is None

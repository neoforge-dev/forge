"""Comprehensive tests for forge_harness.posthog_tracker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import forge_harness.posthog_tracker as tracker_mod
from forge_harness.posthog_tracker import (
    HarnessTracker,
    NoOpTracker,
    SessionMetrics,
    create_tracker,
)


@pytest.fixture
def client_mock() -> Mock:
    client = Mock()
    client.track = AsyncMock(return_value=None)
    client.flush = AsyncMock(return_value=None)
    return client


@pytest.fixture
def enabled_tracker(monkeypatch: pytest.MonkeyPatch, client_mock: Mock) -> HarnessTracker:
    monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
    monkeypatch.setattr(tracker_mod, "PostHogClient", Mock(return_value=client_mock))
    return HarnessTracker(
        domain="codeswiftr-com",
        project="interview-simulator",
        session_id="sess-123",
        api_key="test-key",
        host="https://example.test/ph",
        enabled=True,
    )


class TestSessionMetrics:
    def test_defaults(self) -> None:
        metrics = SessionMetrics()
        assert metrics.issues_attempted == 0
        assert metrics.issues_completed == 0
        assert metrics.issues_failed == 0
        assert metrics.tests_passed == 0
        assert metrics.tests_failed == 0
        assert metrics.coverage_backend is None
        assert metrics.coverage_frontend is None
        assert metrics.errors_encountered == []

    def test_custom_values(self) -> None:
        metrics = SessionMetrics(
            issues_attempted=5,
            issues_completed=4,
            issues_failed=1,
            tests_passed=80,
            tests_failed=2,
            coverage_backend=91.2,
            coverage_frontend=77.0,
            commits_made=3,
            files_changed=9,
            lines_added=120,
            lines_removed=40,
            errors_encountered=["a", "b"],
        )
        assert metrics.issues_attempted == 5
        assert metrics.coverage_backend == 91.2
        assert metrics.errors_encountered == ["a", "b"]


class TestHarnessTrackerInit:
    def test_enabled_initialization_constructs_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client_cls = Mock()
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
        monkeypatch.setattr(tracker_mod, "PostHogClient", client_cls)

        tracker = HarnessTracker(
            domain="d",
            project="p",
            session_id="s",
            api_key="k",
            host="https://host",
            enabled=True,
        )

        assert tracker.enabled is True
        client_cls.assert_called_once_with(
            api_key="k",
            host="https://host",
            enabled=True,
            flush_at=1,
            flush_interval=1,
        )

    def test_disabled_when_posthog_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", False)
        tracker = HarnessTracker(domain="d", project="p", session_id="s", api_key="k", enabled=True)
        assert tracker.enabled is False
        assert tracker._client is None

    def test_disabled_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
        monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
        tracker = HarnessTracker(domain="d", project="p", session_id="s", enabled=True)
        assert tracker.enabled is False
        assert tracker._client is None

    def test_api_key_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client_cls = Mock()
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
        monkeypatch.setattr(tracker_mod, "PostHogClient", client_cls)
        monkeypatch.setenv("POSTHOG_API_KEY", "env-key")
        monkeypatch.setenv("POSTHOG_HOST", "https://env-host")

        HarnessTracker(domain="d", project="p", session_id="s", enabled=True)
        kwargs = client_cls.call_args.kwargs
        assert kwargs["api_key"] == "env-key"
        assert kwargs["host"] == "https://env-host"

    def test_distinct_id(self, enabled_tracker: HarnessTracker) -> None:
        assert enabled_tracker.distinct_id == "harness_sess-123"


class TestAsyncBehavior:
    def test_run_async_without_running_loop_uses_asyncio_run(
        self,
        enabled_tracker: HarnessTracker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def noop() -> None:
            return None

        monkeypatch.setattr(asyncio, "get_running_loop", Mock(side_effect=RuntimeError))
        run_mock = Mock()
        monkeypatch.setattr(asyncio, "run", run_mock)

        enabled_tracker._run_async(noop())
        run_mock.assert_called_once()
        submitted = run_mock.call_args.args[0]
        if hasattr(submitted, "close"):
            submitted.close()

    @pytest.mark.asyncio
    async def test_run_async_with_running_loop_creates_task(
        self,
        enabled_tracker: HarnessTracker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        loop_mock = Mock()
        monkeypatch.setattr(asyncio, "get_running_loop", Mock(return_value=loop_mock))

        async def noop() -> None:
            return None

        enabled_tracker._run_async(noop())
        loop_mock.create_task.assert_called_once()
        submitted = loop_mock.create_task.call_args.args[0]
        if hasattr(submitted, "close"):
            submitted.close()


class TestCapture:
    def test_capture_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", False)
        tracker = HarnessTracker(domain="d", project="p", session_id="s", api_key="k")
        tracker._run_async = Mock()
        tracker._capture("evt", {"a": 1})
        tracker._run_async.assert_not_called()

    def test_capture_merges_base_properties(self, enabled_tracker: HarnessTracker) -> None:
        enabled_tracker._run_async = Mock()
        enabled_tracker._capture("custom_event", {"x": 1})

        enabled_tracker._client.track.assert_called_once()
        kwargs = enabled_tracker._client.track.call_args.kwargs
        assert kwargs["event"] == "custom_event"
        assert kwargs["distinct_id"] == "harness_sess-123"
        assert kwargs["properties"]["domain"] == "codeswiftr-com"
        assert kwargs["properties"]["project"] == "interview-simulator"
        assert kwargs["properties"]["service"] == "forge-harness"
        assert kwargs["properties"]["x"] == 1
        enabled_tracker._run_async.assert_called_once()
        submitted = enabled_tracker._run_async.call_args.args[0]
        if hasattr(submitted, "close"):
            submitted.close()

    def test_track_event_delegates_to_capture(self, enabled_tracker: HarnessTracker) -> None:
        enabled_tracker._capture = Mock()
        enabled_tracker.track_event("evt", {"a": 2})
        enabled_tracker._capture.assert_called_once_with("evt", {"a": 2})


class TestHarnessEventMethods:
    @pytest.mark.parametrize(
        ("method", "args", "kwargs", "event_name", "expected_props"),
        [
            ("session_started", (), {"model": "claude", "is_first_run": True, "max_iterations": 10}, "harness_session_started", {"model": "claude", "is_first_run": True, "max_iterations": 10}),
            ("issue_started", (42, "Title", "high"), {}, "harness_issue_started", {"issue_id": 42, "issue_title": "Title", "priority": "high"}),
            ("issue_completed", (42, "Title", 12), {"tests_added": 3}, "harness_issue_completed", {"issue_id": 42, "issue_title": "Title", "duration_minutes": 12, "tests_added": 3}),
            ("issue_failed", (42, "Title", "blocked", 5), {}, "harness_issue_failed", {"failure_reason": "blocked", "duration_minutes": 5}),
            ("tests_run", (), {"test_type": "backend", "passed": 10, "failed": 1, "coverage": 88.5}, "harness_tests_run", {"test_type": "backend", "tests_passed": 10, "tests_failed": 1, "coverage": 88.5}),
            ("verification_completed", (), {"issue_number": 42, "passed": True, "tests_passed": 30, "tests_failed": 0, "coverage_backend": 90.1, "coverage_frontend": 80.5, "lint_errors": 0}, "harness_verification_completed", {"issue_number": 42, "verification_passed": True, "tests_passed": 30, "coverage_backend": 90.1}),
            ("human_gate_triggered", ("security", "secret leak"), {}, "harness_human_gate", {"gate_type": "security", "details": "secret leak"}),
            ("deployment_triggered", ("railway", True), {"duration_seconds": 65, "error": None}, "harness_deployment", {"target": "railway", "success": True, "duration_seconds": 65, "error": None}),
            ("error_occurred", ("api_error", "x" * 600), {}, "harness_error", {"error_type": "api_error", "error_message": "x" * 500}),
            ("iteration_completed", (), {"iteration_number": 2, "issue_id": 42, "duration_minutes": 12.345, "tests_added": 2, "coverage_delta": 1.239, "tokens_input": 1000, "tokens_output": 2000, "api_cost_usd": 0.01234}, "harness_iteration_completed", {"iteration_number": 2, "duration_minutes": 12.35, "coverage_delta": 1.24, "api_cost_usd": 0.0123}),
            ("error_pattern_detected", ("test_failure", "abc123", 4), {"first_seen": "2026-02-21T00:00:00Z"}, "harness_error_pattern", {"error_type": "test_failure", "error_hash": "abc123", "recurrence_count": 4}),
            ("preflight_completed", (), {"passed": False, "issues_found": 12, "fixes_applied": 4, "issues_remaining": [str(i) for i in range(20)]}, "harness_preflight_completed", {"preflight_passed": False, "issues_found": 12, "fixes_applied": 4, "issues_remaining": [str(i) for i in range(10)]}),
            ("content_generation_started", (), {"brief_id": "b1", "content_type": "blog", "title": "T", "auto_approve": True}, "content_generation_started", {"brief_id": "b1", "content_type": "blog", "title": "T", "auto_approve": True}),
            ("content_generation_completed", (), {"brief_id": "b1", "content_type": "blog", "final_stage": "approved", "revision_rounds": 2, "duration_seconds": 123.456, "word_count": 900, "error_count": 0}, "content_generation_completed", {"final_stage": "approved", "duration_seconds": 123.46, "success": True}),
            ("content_generation_completed", (), {"brief_id": "b2", "content_type": "blog", "final_stage": "error", "revision_rounds": 1, "duration_seconds": 1.2, "word_count": None, "error_count": 2}, "content_generation_completed", {"success": False}),
            ("content_revision_requested", (), {"brief_id": "b1", "content_type": "blog", "revision_round": 3, "feedback_preview": "y" * 300}, "content_revision_requested", {"feedback_preview": "y" * 200}),
            ("content_approved", (), {"brief_id": "b1", "content_type": "blog", "approval_method": "human", "revision_rounds": 2}, "content_approved", {"approval_method": "human", "revision_rounds": 2}),
            ("content_batch_completed", (), {"content_types": ["blog", "social"], "total_pieces": 10, "successful": 7, "failed": 3, "duration_minutes": 22.222}, "content_batch_completed", {"success_rate": 0.7, "duration_minutes": 22.22}),
            ("content_batch_completed", (), {"content_types": ["blog"], "total_pieces": 0, "successful": 0, "failed": 0, "duration_minutes": 1.0}, "content_batch_completed", {"success_rate": 0.0}),
            ("content_published", (), {"brief_id": "b1", "content_type": "blog", "platform": "wordpress", "word_count": 1000}, "content_published", {"platform": "wordpress", "word_count": 1000}),
            ("content_tier_allocation", (), {"domain": "codeswiftr-com", "tier": 2, "allocation_percentage": 35, "content_types": ["blog"]}, "content_tier_allocation", {"tier": 2, "allocation_percentage": 35}),
            ("decision_made", (), {"decision_id": "d1", "action": "PROCEED", "confidence": 0.8, "feature_id": "f1", "reasoning": "safe"}, "harness_decision_made", {"decision_id": "d1", "action": "PROCEED"}),
            ("feedback_recorded", (), {"decision_id": "d1", "outcome": "success", "feature_id": "f1", "error_message": None}, "harness_feedback_recorded", {"decision_id": "d1", "outcome": "success"}),
            ("bridge_called", (), {"bridge_name": "code_atlas", "operation": "query", "latency_ms": 12.4, "success": True, "error": None}, "harness_bridge_called", {"bridge_name": "code_atlas", "operation": "query"}),
            ("pattern_applied", (), {"pattern_id": "p1", "feature_id": "f1", "confidence": 0.77, "source": "learning_store"}, "harness_pattern_applied", {"pattern_id": "p1", "feature_id": "f1"}),
        ],
    )
    def test_event_methods_delegate_with_expected_payload(
        self,
        enabled_tracker: HarnessTracker,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        event_name: str,
        expected_props: dict[str, Any],
    ) -> None:
        enabled_tracker._capture = Mock()
        getattr(enabled_tracker, method)(*args, **kwargs)
        enabled_tracker._capture.assert_called_once()
        call_event, call_props = enabled_tracker._capture.call_args.args
        assert call_event == event_name
        for key, value in expected_props.items():
            assert call_props[key] == value

    def test_session_ended_flushes_client(self, enabled_tracker: HarnessTracker) -> None:
        enabled_tracker._run_async = Mock()
        enabled_tracker.start_time = datetime.now() - timedelta(minutes=5)
        metrics = SessionMetrics(
            issues_attempted=5,
            issues_completed=4,
            issues_failed=1,
            tests_passed=20,
            tests_failed=2,
            coverage_backend=90.0,
            coverage_frontend=80.0,
            commits_made=3,
            files_changed=10,
            lines_added=100,
            lines_removed=50,
            errors_encountered=["a", "b"],
        )
        enabled_tracker.session_ended(metrics, end_reason="completed")

        # one call from _capture(track), one call from flush()
        assert enabled_tracker._run_async.call_count == 2
        enabled_tracker._client.track.assert_called_once()
        kwargs = enabled_tracker._client.track.call_args.kwargs
        assert kwargs["event"] == "harness_session_ended"
        assert kwargs["properties"]["issues_completed"] == 4
        assert kwargs["properties"]["errors_count"] == 2
        assert kwargs["properties"]["duration_minutes"] >= 0
        enabled_tracker._client.flush.assert_called_once()
        for call in enabled_tracker._run_async.call_args_list:
            submitted = call.args[0]
            if hasattr(submitted, "close"):
                submitted.close()


class TestNoOpTracker:
    def test_noop_initialization(self) -> None:
        tracker = NoOpTracker(domain="d", project="p", session_id="s")
        assert tracker.enabled is False
        assert tracker.domain == "d"
        assert tracker.project == "p"
        assert tracker.session_id == "s"

    @pytest.mark.parametrize(
        ("method_name", "args", "kwargs"),
        [
            ("_capture", ("e", {"a": 1}), {}),
            ("track_event", ("e", {"a": 1}), {}),
            ("session_started", (), {"model": "claude"}),
            ("issue_started", (1, "title", "high"), {}),
            ("issue_completed", (1, "title", 3), {}),
            ("issue_failed", (1, "title", "blocked", 1), {}),
            ("tests_run", (), {"test_type": "backend", "passed": 1, "failed": 0}),
            ("verification_completed", (), {"issue_number": 1, "passed": True, "tests_passed": 1, "tests_failed": 0, "coverage_backend": 1.0, "coverage_frontend": 1.0, "lint_errors": 0}),
            ("human_gate_triggered", ("security", "details"), {}),
            ("deployment_triggered", ("railway", True), {}),
            ("error_occurred", ("api_error", "msg"), {}),
            ("iteration_completed", (), {}),
            ("error_pattern_detected", (), {}),
            ("preflight_completed", (), {}),
            ("content_generation_started", (), {}),
            ("content_generation_completed", (), {}),
            ("content_revision_requested", (), {}),
            ("content_approved", (), {}),
            ("content_batch_completed", (), {}),
            ("content_published", (), {}),
            ("content_tier_allocation", (), {}),
            ("decision_made", (), {}),
            ("feedback_recorded", (), {}),
            ("bridge_called", (), {}),
            ("pattern_applied", (), {}),
            ("session_ended", (SessionMetrics(),), {}),
        ],
    )
    def test_noop_methods_do_not_raise(
        self,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        tracker = NoOpTracker(domain="d", project="p", session_id="s")
        getattr(tracker, method_name)(*args, **kwargs)


class TestCreateTrackerFactory:
    def test_returns_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
        tracker = create_tracker("d", "p", "s", enabled=False)
        assert isinstance(tracker, NoOpTracker)

    def test_returns_noop_when_posthog_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", False)
        tracker = create_tracker("d", "p", "s", enabled=True)
        assert isinstance(tracker, NoOpTracker)

    def test_returns_noop_when_api_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
        monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
        tracker = create_tracker("d", "p", "s", enabled=True)
        assert isinstance(tracker, NoOpTracker)

    def test_returns_harness_tracker_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tracker_mod, "POSTHOG_AVAILABLE", True)
        monkeypatch.setenv("POSTHOG_API_KEY", "env-key")
        ctor = Mock(return_value="tracker")
        monkeypatch.setattr(tracker_mod, "HarnessTracker", ctor)

        result = create_tracker("dom", "proj", "sess", enabled=True)
        assert result == "tracker"
        ctor.assert_called_once_with(
            domain="dom",
            project="proj",
            session_id="sess",
            api_key="env-key",
            enabled=True,
        )

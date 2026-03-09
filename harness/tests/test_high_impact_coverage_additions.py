"""Additional high-impact coverage tests for key orchestration modules."""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

import forge_harness.webhook_server_main as wsm
from forge_harness.continuous_runner import main as continuous_main
from forge_harness.flywheel import (
    _build_debt_description,
    quality_report_to_features,
    run_flywheel_sync,
)
from forge_harness.quality_loop import (
    DebtMetrics,
    ProjectQualityReport,
    QualityTrend,
    SecurityFinding,
    SeverityLevel,
)
from forge_harness.ralph_loop import _extract_priority_from_line
from forge_harness.webhook_server.core.models import WebhookPayload
from forge_harness.webhook_server_main import PendingGate, WebhookHumanGate


@pytest.fixture(autouse=True)
def _reset_webhook_globals():
    """Isolate global singleton state in webhook_server_main for each test."""
    snapshot = {
        "_state_synchronizer": wsm._state_synchronizer,
        "_synchronizer_task": wsm._synchronizer_task,
        "_tmux_sync_service": wsm._tmux_sync_service,
        "_tmux_sync_task": wsm._tmux_sync_task,
        "_lease_recovery_service": wsm._lease_recovery_service,
        "_lease_recovery_task": wsm._lease_recovery_task,
        "_learning_store": wsm._learning_store,
        "_orchestration_harness": wsm._orchestration_harness,
    }
    yield
    wsm._state_synchronizer = snapshot["_state_synchronizer"]
    wsm._synchronizer_task = snapshot["_synchronizer_task"]
    wsm._tmux_sync_service = snapshot["_tmux_sync_service"]
    wsm._tmux_sync_task = snapshot["_tmux_sync_task"]
    wsm._lease_recovery_service = snapshot["_lease_recovery_service"]
    wsm._lease_recovery_task = snapshot["_lease_recovery_task"]
    wsm._learning_store = snapshot["_learning_store"]
    wsm._orchestration_harness = snapshot["_orchestration_harness"]


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("30", 30.0),
        ("45", 45.0),
        ("1", 1.0),
        ("1.5", 1.5),
        ("2.25", 2.25),
        ("100", 100.0),
        ("0", 1.0),
        ("-7", 1.0),
        ("0.8", 1.0),
        ("abc", 30.0),
        ("", 30.0),
        (" 10 ", 10.0),
    ],
)
def test_lease_recovery_poll_interval_parsing(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: float,
) -> None:
    monkeypatch.setenv("FORGE_TASK_LEASE_RECOVERY_POLL_INTERVAL_SECONDS", raw_value)
    assert wsm._lease_recovery_poll_interval_seconds() == expected


def test_set_and_get_orchestration_harness_round_trip() -> None:
    harness = object()
    wsm.set_orchestration_harness(harness)
    assert wsm.get_orchestration_harness() is harness


def test_get_state_synchronizer_returns_current_global() -> None:
    marker = object()
    wsm._state_synchronizer = marker
    assert wsm.get_state_synchronizer() is marker


def test_get_tmux_sync_service_returns_current_global() -> None:
    marker = object()
    wsm._tmux_sync_service = marker
    assert wsm.get_tmux_sync_service() is marker


@pytest.mark.asyncio
async def test_start_state_synchronizer_returns_cached_instance() -> None:
    marker = object()
    wsm._state_synchronizer = marker
    result = await wsm.start_state_synchronizer()
    assert result is marker


@pytest.mark.asyncio
async def test_start_state_synchronizer_initializes_and_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeStateSynchronizer:
        def __init__(self, forge_root: Path, event_bus: object):
            created["forge_root"] = forge_root
            created["event_bus"] = event_bus

        async def start(self, poll_interval: float) -> None:
            created["started_poll_interval"] = poll_interval

    fake_module = types.ModuleType("forge_harness.state_synchronizer")
    fake_module.StateSynchronizer = FakeStateSynchronizer
    monkeypatch.setitem(sys.modules, "forge_harness.state_synchronizer", fake_module)
    monkeypatch.setattr(wsm, "get_event_bus", lambda: "bus")
    def fake_create_task(coro: object) -> str:
        if hasattr(coro, "close"):
            coro.close()
        return "task-marker"

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    result = await wsm.start_state_synchronizer(forge_root=Path("/tmp/forge-root"))
    assert result is not None
    assert created["forge_root"] == Path("/tmp/forge-root")
    assert created["event_bus"] == "bus"
    assert wsm._synchronizer_task == "task-marker"


@pytest.mark.asyncio
async def test_start_state_synchronizer_returns_none_on_constructor_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStateSynchronizer:
        def __init__(self, *args: object, **kwargs: object):
            raise RuntimeError("boom")

    fake_module = types.ModuleType("forge_harness.state_synchronizer")
    fake_module.StateSynchronizer = BrokenStateSynchronizer
    monkeypatch.setitem(sys.modules, "forge_harness.state_synchronizer", fake_module)
    monkeypatch.setattr(wsm, "get_event_bus", lambda: "bus")

    result = await wsm.start_state_synchronizer()
    assert result is None


@pytest.mark.asyncio
async def test_stop_state_synchronizer_cleans_up() -> None:
    sync = AsyncMock()
    task = asyncio.create_task(asyncio.sleep(30))
    wsm._state_synchronizer = sync
    wsm._synchronizer_task = task

    await wsm.stop_state_synchronizer()

    sync.stop.assert_awaited_once()
    assert wsm._state_synchronizer is None
    assert wsm._synchronizer_task is None


@pytest.mark.asyncio
async def test_start_tmux_sync_service_returns_cached_instance() -> None:
    marker = object()
    wsm._tmux_sync_service = marker
    result = await wsm.start_tmux_sync_service()
    assert result is marker


@pytest.mark.asyncio
async def test_start_tmux_sync_service_initializes(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeTmuxService:
        def __init__(self, forge_root: Path, event_bus: object):
            created["forge_root"] = forge_root
            created["event_bus"] = event_bus

        async def start(self, poll_interval: float) -> None:
            created["poll_interval"] = poll_interval

    fake_module = types.ModuleType("forge_harness.sync.agent_sync")
    fake_module.TmuxDBSyncService = FakeTmuxService
    monkeypatch.setitem(sys.modules, "forge_harness.sync.agent_sync", fake_module)
    monkeypatch.setattr(wsm, "get_event_bus", lambda: "bus")

    result = await wsm.start_tmux_sync_service(forge_root=Path("/tmp/forge-root"))
    assert result is not None
    assert created["forge_root"] == Path("/tmp/forge-root")
    assert created["event_bus"] == "bus"
    assert created["poll_interval"] == 5.0


@pytest.mark.asyncio
async def test_stop_tmux_sync_service_cleans_up() -> None:
    service = AsyncMock()
    wsm._tmux_sync_service = service
    wsm._tmux_sync_task = object()

    await wsm.stop_tmux_sync_service()

    service.stop.assert_awaited_once()
    assert wsm._tmux_sync_service is None
    assert wsm._tmux_sync_task is None


@pytest.mark.asyncio
async def test_start_lease_recovery_service_returns_cached_instance() -> None:
    marker = object()
    wsm._lease_recovery_service = marker
    result = await wsm.start_lease_recovery_service()
    assert result is marker


@pytest.mark.asyncio
async def test_start_lease_recovery_service_initializes(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeLeaseService:
        def __init__(self, task_handler: object, event_bus: object):
            created["task_handler"] = task_handler
            created["event_bus"] = event_bus

        async def start(self, interval: float) -> None:
            created["interval"] = interval

    fake_module = types.ModuleType("forge_harness.webhook_server.services.lease_recovery")
    fake_module.StaleLeaseRecoveryService = FakeLeaseService
    monkeypatch.setitem(
        sys.modules,
        "forge_harness.webhook_server.services.lease_recovery",
        fake_module,
    )
    monkeypatch.setattr(wsm, "get_task_handler", lambda: "task-handler")
    monkeypatch.setattr(wsm, "get_event_bus", lambda: "bus")
    monkeypatch.setattr(wsm, "_lease_recovery_poll_interval_seconds", lambda: 12.0)
    def fake_create_task(coro: object) -> str:
        if hasattr(coro, "close"):
            coro.close()
        return "task-marker"

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    result = await wsm.start_lease_recovery_service()
    assert result is not None
    assert created["task_handler"] == "task-handler"
    assert created["event_bus"] == "bus"
    assert wsm._lease_recovery_task == "task-marker"


@pytest.mark.asyncio
async def test_stop_lease_recovery_service_cleans_up() -> None:
    service = AsyncMock()
    task = asyncio.create_task(asyncio.sleep(30))
    wsm._lease_recovery_service = service
    wsm._lease_recovery_task = task

    await wsm.stop_lease_recovery_service()

    service.stop.assert_awaited_once()
    assert wsm._lease_recovery_service is None
    assert wsm._lease_recovery_task is None


def test_get_learning_store_returns_cached() -> None:
    marker = object()
    wsm._learning_store = marker
    assert wsm.get_learning_store() is marker


def test_pending_gate_default_created_at_is_timezone_aware() -> None:
    gate = PendingGate(notification_id="n1", event=asyncio.Event())
    assert gate.created_at.tzinfo is not None


def test_webhook_human_gate_generate_notification_id_format() -> None:
    gate = WebhookHumanGate(notification_harness=None)
    notification_id = gate._generate_notification_id()
    assert notification_id.startswith("gate_")
    assert len(notification_id) == len("gate_") + 12


def test_webhook_human_gate_generate_notification_id_unique() -> None:
    gate = WebhookHumanGate(notification_harness=None)
    ids = {gate._generate_notification_id() for _ in range(20)}
    assert len(ids) == 20


def test_webhook_human_gate_resolve_gate_missing_returns_false() -> None:
    gate = WebhookHumanGate(notification_harness=None)
    payload = WebhookPayload(
        source="slack",
        event_type="button",
        notification_id="n1",
        response_type="approved",
        responder="alice",
    )
    assert gate.resolve_gate("missing", payload) is False


def test_webhook_human_gate_resolve_gate_sets_event_and_response() -> None:
    gate = WebhookHumanGate(notification_harness=None)
    event = asyncio.Event()
    pending = PendingGate(notification_id="n1", event=event)
    gate._pending_gates["n1"] = pending

    payload = WebhookPayload(
        source="slack",
        event_type="button",
        notification_id="n1",
        response_type="approved",
        responder="alice",
        message="looks good",
    )
    assert gate.resolve_gate("n1", payload) is True
    assert pending.response is payload
    assert pending.event.is_set()


def test_webhook_human_gate_get_pending_gates_returns_ids() -> None:
    gate = WebhookHumanGate(notification_harness=None)
    gate._pending_gates["a"] = PendingGate(notification_id="a", event=asyncio.Event())
    gate._pending_gates["b"] = PendingGate(notification_id="b", event=asyncio.Event())
    assert set(gate.get_pending_gates()) == {"a", "b"}


@pytest.mark.asyncio
async def test_await_feedback_returns_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")
    monkeypatch.setattr(asyncio, "wait_for", AsyncMock(side_effect=TimeoutError))

    result = await gate.await_feedback(page_ids=["p1"], timeout_hours=0.001)
    assert result["status"] == "timeout"
    assert result["notification_id"] == "fixed-id"
    assert gate.get_pending_gates() == []


@pytest.mark.asyncio
async def test_await_feedback_returns_error_when_response_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")
    async def fake_wait_for(awaitable: object, timeout: float) -> None:
        if hasattr(awaitable, "close"):
            awaitable.close()
        return None

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await gate.await_feedback(page_ids=["p1"])
    assert result["status"] == "error"
    assert result["approved_ids"] == []
    assert gate.get_pending_gates() == []


@pytest.mark.asyncio
async def test_await_feedback_returns_approved_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")

    async def fake_wait_for(awaitable: object, timeout: float) -> None:
        pending = gate._pending_gates["fixed-id"]
        pending.response = WebhookPayload(
            source="slack",
            event_type="button",
            notification_id="fixed-id",
            response_type="approved",
            responder="alice",
            message="ship it",
        )
        pending.event.set()
        await awaitable

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    result = await gate.await_feedback(page_ids=["p1", "p2"], message="review")
    assert result["status"] == "approved"
    assert result["approved_ids"] == ["p1", "p2"]
    assert result["responder"] == "alice"


@pytest.mark.asyncio
async def test_await_feedback_rejected_returns_empty_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")

    async def fake_wait_for(awaitable: object, timeout: float) -> None:
        pending = gate._pending_gates["fixed-id"]
        pending.response = WebhookPayload(
            source="slack",
            event_type="button",
            notification_id="fixed-id",
            response_type="rejected",
            responder="bob",
            message="needs changes",
        )
        pending.event.set()
        await awaitable

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    result = await gate.await_feedback(page_ids=["p1"], message="review")
    assert result["status"] == "rejected"
    assert result["approved_ids"] == []


@pytest.mark.asyncio
async def test_request_decision_returns_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")
    monkeypatch.setattr(asyncio, "wait_for", AsyncMock(side_effect=TimeoutError))

    result = await gate.request_decision(question="Proceed?", options=["yes", "no"])
    assert result["status"] == "timeout"
    assert result["decision"] is None
    assert gate.get_pending_gates() == []


@pytest.mark.asyncio
async def test_request_decision_returns_error_when_response_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")
    async def fake_wait_for(awaitable: object, timeout: float) -> None:
        if hasattr(awaitable, "close"):
            awaitable.close()
        return None

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await gate.request_decision(question="Proceed?", options=["yes", "no"])
    assert result["status"] == "error"
    assert result["decision"] is None


@pytest.mark.asyncio
async def test_request_decision_returns_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = WebhookHumanGate(notification_harness=AsyncMock(), callback_url="https://cb")
    monkeypatch.setattr(gate, "_generate_notification_id", lambda: "fixed-id")

    async def fake_wait_for(awaitable: object, timeout: float) -> None:
        pending = gate._pending_gates["fixed-id"]
        pending.response = WebhookPayload(
            source="slack",
            event_type="button",
            notification_id="fixed-id",
            response_type="approve",
            responder="carol",
            message="go ahead",
        )
        pending.event.set()
        await awaitable

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    result = await gate.request_decision(
        question="Deploy now?",
        options=["approve", "reject"],
        context={"risk": "low"},
    )
    assert result["status"] == "resolved"
    assert result["decision"] == "approve"
    assert result["responder"] == "carol"


def test_run_server_raises_runtime_error_when_create_app_returns_none() -> None:
    with (
        patch("forge_harness.webhook_server_main.create_app", return_value=None),
        patch("forge_harness.webhook_server_main.WebhookHandler"),
        patch("forge_harness.webhook_server_main.ApprovalQueueHandler"),
        patch("uvicorn.run"),
    ):
        with pytest.raises(RuntimeError, match="Failed to create FastAPI app"):
            wsm.run_server()


def test_run_server_invokes_uvicorn_with_host_port() -> None:
    fake_app = object()
    with (
        patch("forge_harness.webhook_server_main.create_app", return_value=fake_app),
        patch("forge_harness.webhook_server_main.WebhookHandler"),
        patch("forge_harness.webhook_server_main.ApprovalQueueHandler"),
        patch("uvicorn.run") as mock_run,
    ):
        wsm.run_server(host="127.0.0.1", port=9090)
        mock_run.assert_called_once_with(fake_app, host="127.0.0.1", port=9090)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("P0 emergency", "critical"),
        ("critical bugfix", "critical"),
        ("P1 item", "high"),
        ("high priority task", "high"),
        ("P2 cleanup", "medium"),
        ("P3 docs", "low"),
        ("low effort", "low"),
        ("no signal", None),
        ("mixed P1 and low", "high"),
        ("PRIORITY: HIGH", "high"),
    ],
)
def test_extract_priority_from_line_cases(line: str, expected: str | None) -> None:
    assert _extract_priority_from_line(line) == expected


class _Finding:
    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        line_number: int | None = None,
        recommendation: str | None = None,
    ) -> None:
        self.message = message
        self.file_path = file_path
        self.line_number = line_number
        self.recommendation = recommendation


def test_build_debt_description_message_only() -> None:
    desc = _build_debt_description(_Finding("fix this"))
    assert desc == "fix this"


def test_build_debt_description_with_file() -> None:
    desc = _build_debt_description(_Finding("fix this", file_path="src/app.py"))
    assert "**Location:** `src/app.py`" in desc


def test_build_debt_description_with_file_and_line() -> None:
    desc = _build_debt_description(
        _Finding("fix this", file_path="src/app.py", line_number=22)
    )
    assert "**Location:** `src/app.py`:22" in desc


def test_build_debt_description_with_recommendation() -> None:
    desc = _build_debt_description(_Finding("fix this", recommendation="apply patch"))
    assert "**Recommendation:** apply patch" in desc


def test_build_debt_description_full_content() -> None:
    desc = _build_debt_description(
        _Finding(
            "fix this",
            file_path="src/app.py",
            line_number=9,
            recommendation="refactor parser",
        )
    )
    assert "fix this" in desc
    assert "**Location:** `src/app.py`:9" in desc
    assert "**Recommendation:** refactor parser" in desc


def _build_report(
    *,
    quality_score: float,
    findings: list[SecurityFinding],
    issues: list[str] | None = None,
    recommendations: list[str] | None = None,
) -> ProjectQualityReport:
    return ProjectQualityReport(
        project_name="proj",
        domain="dom",
        project_path="/tmp/proj",
        scan_timestamp=datetime.now(UTC),
        debt_metrics=DebtMetrics(),
        security_findings=findings,
        quality_score=quality_score,
        trend=QualityTrend.UNKNOWN,
        issues=issues or ["a", "b", "c"],
        recommendations=recommendations or ["r1", "r2"],
    )


def test_quality_report_to_features_respects_threshold() -> None:
    report = _build_report(
        quality_score=85,
        findings=[
            SecurityFinding(
                tool="bandit",
                severity=SeverityLevel.HIGH,
                rule_id="B101",
                message="high finding",
                file_path="a.py",
            ),
            SecurityFinding(
                tool="bandit",
                severity=SeverityLevel.LOW,
                rule_id="B102",
                message="low finding",
                file_path="b.py",
            ),
        ],
    )
    features = quality_report_to_features(report, priority_threshold="high")
    assert len(features) == 1
    assert features[0]["priority"] == "high"


def test_quality_report_to_features_adds_quality_debt_item_for_low_score() -> None:
    report = _build_report(quality_score=60, findings=[])
    features = quality_report_to_features(report, priority_threshold="medium")
    assert len(features) == 1
    assert features[0]["metadata"]["finding_type"] == "debt"


def test_quality_report_to_features_limits_max_features() -> None:
    report = _build_report(
        quality_score=90,
        findings=[
            SecurityFinding(
                tool="bandit",
                severity=SeverityLevel.CRITICAL,
                rule_id=f"RULE{i}",
                message=f"finding {i}",
                file_path=f"f{i}.py",
            )
            for i in range(5)
        ],
    )
    features = quality_report_to_features(report, max_features=2)
    assert len(features) == 2


def test_quality_report_to_features_includes_location_line_when_present() -> None:
    report = _build_report(
        quality_score=95,
        findings=[
            SecurityFinding(
                tool="bandit",
                severity=SeverityLevel.MEDIUM,
                rule_id="RULE1",
                message="medium finding",
                file_path="src/mod.py",
                line_number=17,
            )
        ],
    )
    features = quality_report_to_features(report)
    assert ":17" in features[0]["description"]


def test_quality_report_to_features_metadata_fields_present() -> None:
    report = _build_report(
        quality_score=95,
        findings=[
            SecurityFinding(
                tool="bandit",
                severity=SeverityLevel.HIGH,
                rule_id="RULE2",
                message="high finding",
                file_path="src/x.py",
            )
        ],
    )
    features = quality_report_to_features(report)
    metadata = features[0]["metadata"]
    assert metadata["source"] == "quality_loop"
    assert metadata["domain"] == "dom"
    assert metadata["project"] == "proj"
    assert metadata["finding_type"] == "security"


def test_run_flywheel_sync_delegates_to_asyncio_run() -> None:
    fake_result = object()
    with patch("forge_harness.flywheel.asyncio.run", return_value=fake_result) as mock_run:
        result = run_flywheel_sync(Path("/tmp"), "d", "p")
    assert result is fake_result
    mock_run.assert_called_once()
    coro = mock_run.call_args.args[0]
    if hasattr(coro, "close"):
        coro.close()


def test_continuous_main_exits_when_required_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGE_DOMAIN", raising=False)
    monkeypatch.delenv("FORGE_PROJECT", raising=False)
    with pytest.raises(SystemExit):
        continuous_main()


def test_continuous_main_parses_env_and_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
    monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
    monkeypatch.setenv("FEATURES_PATH", "/tmp/features.json")
    monkeypatch.setenv("APPROVAL_TIMEOUT_HOURS", "12.5")
    monkeypatch.setenv("LOOP_COOLDOWN_SECONDS", "15")
    monkeypatch.setenv("MAX_ITERATIONS_PER_RUN", "7")

    runner_mock = Mock()
    runner_mock.run = AsyncMock()
    runner_cls = Mock(return_value=runner_mock)
    monkeypatch.setattr("forge_harness.continuous_runner.ContinuousRalphRunner", runner_cls)
    def fake_asyncio_run(coro: object) -> None:
        if hasattr(coro, "close"):
            coro.close()

    monkeypatch.setattr("forge_harness.continuous_runner.asyncio.run", fake_asyncio_run)

    continuous_main()

    runner_cls.assert_called_once()
    kwargs = runner_cls.call_args.kwargs
    assert kwargs["domain"] == "brandfocus-ai"
    assert kwargs["project"] == "voice-coach"
    assert kwargs["features_path"] == Path("/tmp/features.json")
    assert kwargs["approval_timeout_hours"] == 12.5
    assert kwargs["loop_cooldown_seconds"] == 15.0
    assert kwargs["max_iterations_per_run"] == 7


def test_continuous_main_uses_defaults_for_optional_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
    monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
    monkeypatch.delenv("FEATURES_PATH", raising=False)
    monkeypatch.delenv("APPROVAL_TIMEOUT_HOURS", raising=False)
    monkeypatch.delenv("LOOP_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("MAX_ITERATIONS_PER_RUN", raising=False)

    runner_mock = Mock()
    runner_mock.run = AsyncMock()
    runner_cls = Mock(return_value=runner_mock)
    monkeypatch.setattr("forge_harness.continuous_runner.ContinuousRalphRunner", runner_cls)
    def fake_asyncio_run(coro: object) -> None:
        if hasattr(coro, "close"):
            coro.close()

    monkeypatch.setattr("forge_harness.continuous_runner.asyncio.run", fake_asyncio_run)

    continuous_main()

    kwargs = runner_cls.call_args.kwargs
    assert kwargs["features_path"] is None
    assert kwargs["approval_timeout_hours"] == 24.0
    assert kwargs["loop_cooldown_seconds"] == 60.0
    assert kwargs["max_iterations_per_run"] == 50


def test_continuous_main_raises_on_invalid_numeric_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_DOMAIN", "brandfocus-ai")
    monkeypatch.setenv("FORGE_PROJECT", "voice-coach")
    monkeypatch.setenv("APPROVAL_TIMEOUT_HOURS", "not-a-number")

    with pytest.raises(ValueError):
        continuous_main()

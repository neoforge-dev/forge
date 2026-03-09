"""Comprehensive tests for the intake service and API routes — DF-2001.

Coverage targets:
- IntakeItem model validation (required fields, defaults, uuid generation, validators)
- IntakeResult model validation
- IntakeService.submit: accepted path (creates result with assigned lane)
- IntakeService.submit: WIP capacity full -> rejected with reason
- IntakeService.list_pending: returns only pending items, limit respected
- IntakeService.get_stats: accurate counts by status and source
- IntakeService._persist_item: writes JSONL, parent mkdir, error swallowed
- IntakeService._load_persisted: startup replay, malformed lines, blank lines
- IntakeService._emit_event: calls emitter, exception swallowed
- JSONL persistence: items survive service restart
- Singleton pattern: get_intake_service / reset_intake_service
- Thread safety: concurrent submit calls
- API routes: POST /api/intake, GET /api/intake, GET /api/intake/stats,
  GET /api/intake/{item_id}

Target: 80%+ coverage on intake_service.py and intake.py (API routes).
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.models.intake_queue import (
    VALID_SOURCES,
    IntakeItem,
    IntakeResult,
    IntakeStatus,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.intake_service import (
    _DEFAULT_QUEUE_PATH,
    IntakeService,
    get_intake_service,
    reset_intake_service,
)
from forge_harness.webhook_server.services.lane_enforcer import reset_lane_enforcer

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset all singleton state before every test for clean isolation."""
    reset_intake_service()
    reset_lane_enforcer()
    yield
    reset_intake_service()
    reset_lane_enforcer()


@pytest.fixture()
def queue_path(tmp_path: Path) -> Path:
    """Return a fresh JSONL queue file path inside a temp directory."""
    return tmp_path / ".forge" / "intake" / "queue.jsonl"


@pytest.fixture()
def service(queue_path: Path) -> IntakeService:
    """Return a fresh IntakeService bound to an isolated JSONL file."""
    return IntakeService(queue_path=queue_path)


@pytest.fixture()
def minimal_item() -> IntakeItem:
    """Return a valid minimal IntakeItem with all required fields."""
    return IntakeItem(
        title="Fix auth regression",
        task_type=TaskType.bug_fix,
        risk_tier=RiskTier.low,
        source="api",
    )


@pytest.fixture()
def full_item() -> IntakeItem:
    """Return a fully populated IntakeItem."""
    return IntakeItem(
        title="Add pagination to /api/tasks",
        description="Implement cursor-based pagination with page_size parameter.",
        task_type=TaskType.api_endpoint,
        risk_tier=RiskTier.medium,
        priority=4,
        source="cli",
        metadata={"jira_ticket": "FORGE-42", "requester": "bogdan"},
    )


@pytest.fixture()
def mock_enforcer() -> MagicMock:
    """Return a MagicMock enforcer with WIP available by default."""
    m = MagicMock()
    m.check_wip.return_value = True
    m.assign_lane.return_value = WorkCellLane.api_simple
    return m


@pytest.fixture()
def api_client(queue_path: Path):
    """Create a FastAPI TestClient with the intake router and a fresh service."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from forge_harness.webhook_server.api.intake import router

    # Wire up a fresh service instance for this test's client
    test_service = IntakeService(queue_path=queue_path)

    app = FastAPI()
    app.include_router(router)

    # Patch _get_service so all route handlers use our test service instance
    with patch(
        "forge_harness.webhook_server.api.intake._get_service",
        return_value=test_service,
    ):
        with patch(
            "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
        ):
            yield TestClient(app)


# Convenience factory used in several test classes
def _make_item(
    *,
    task_type: TaskType = TaskType.bug_fix,
    risk_tier: RiskTier = RiskTier.low,
    source: str = "api",
    title: str = "Fix a bug",
) -> IntakeItem:
    return IntakeItem(
        title=title,
        task_type=task_type,
        risk_tier=risk_tier,
        source=source,
    )


def _make_result(
    item: IntakeItem,
    *,
    status: IntakeStatus = IntakeStatus.assigned,
    lane: WorkCellLane | None = WorkCellLane.api_simple,
    task_id: str | None = None,
    reason: str | None = None,
) -> IntakeResult:
    return IntakeResult(
        item_id=item.item_id,
        status=status,
        assigned_lane=lane,
        assigned_task_id=task_id or str(uuid.uuid4()),
        rejection_reason=reason,
    )


# ===========================================================================
# IntakeItem — model validation
# ===========================================================================


class TestIntakeItemValidation:
    """Unit tests for the IntakeItem Pydantic model."""

    def test_minimal_item_is_valid(self, minimal_item: IntakeItem) -> None:
        assert minimal_item.title == "Fix auth regression"
        assert minimal_item.task_type == TaskType.bug_fix
        assert minimal_item.risk_tier == RiskTier.low
        assert minimal_item.source == "api"

    def test_item_id_is_auto_generated_uuid(self, minimal_item: IntakeItem) -> None:
        parsed = uuid.UUID(minimal_item.item_id)
        assert parsed.version == 4

    def test_two_items_have_different_ids(self) -> None:
        a = _make_item(title="A")
        b = _make_item(title="B")
        assert a.item_id != b.item_id

    def test_explicit_item_id_is_preserved(self) -> None:
        custom_id = str(uuid.uuid4())
        item = IntakeItem(
            item_id=custom_id,
            title="Custom ID item",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
            source="cli",
        )
        assert item.item_id == custom_id

    def test_submitted_at_is_iso8601_utc(self, minimal_item: IntakeItem) -> None:
        ts = minimal_item.submitted_at
        assert "T" in ts

    def test_default_priority_is_3(self, minimal_item: IntakeItem) -> None:
        assert minimal_item.priority == 3

    def test_default_description_is_empty_string(self, minimal_item: IntakeItem) -> None:
        assert minimal_item.description == ""

    def test_default_metadata_is_empty_dict(self, minimal_item: IntakeItem) -> None:
        assert minimal_item.metadata == {}

    def test_priority_range_1_to_5(self) -> None:
        for p in range(1, 6):
            item = IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
                priority=p,
            )
            assert item.priority == p

    def test_priority_below_1_rejected(self) -> None:
        with pytest.raises(Exception):
            IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
                priority=0,
            )

    def test_priority_above_5_rejected(self) -> None:
        with pytest.raises(Exception):
            IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
                priority=6,
            )

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(Exception):
            IntakeItem(
                title="",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
            )

    def test_title_over_200_chars_rejected(self) -> None:
        with pytest.raises(Exception):
            IntakeItem(
                title="X" * 201,
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
            )

    def test_invalid_source_rejected(self) -> None:
        with pytest.raises(Exception):
            IntakeItem(
                title="Bad source",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="manual",
            )

    def test_all_valid_sources_accepted(self) -> None:
        for src in VALID_SOURCES:
            item = IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source=src,
            )
            assert item.source == src

    def test_metadata_preserves_nested_values(self) -> None:
        item = IntakeItem(
            title="T",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            source="api",
            metadata={"a": 1, "nested": {"b": [1, 2, 3]}},
        )
        assert item.metadata["nested"]["b"] == [1, 2, 3]

    def test_all_task_types_accepted(self) -> None:
        for tt in TaskType:
            item = IntakeItem(
                title="T",
                task_type=tt,
                risk_tier=RiskTier.low,
                source="api",
            )
            assert item.task_type == tt

    def test_all_risk_tiers_accepted(self) -> None:
        for rt in RiskTier:
            item = IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=rt,
                source="api",
            )
            assert item.risk_tier == rt

    def test_title_exactly_200_chars_accepted(self) -> None:
        item = IntakeItem(
            title="X" * 200,
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            source="api",
        )
        assert len(item.title) == 200

    def test_title_exactly_1_char_accepted(self) -> None:
        item = IntakeItem(
            title="X",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            source="api",
        )
        assert item.title == "X"


# ===========================================================================
# IntakeResult — model validation
# ===========================================================================


class TestIntakeResultValidation:
    def test_accepted_result_has_lane_and_task_id(self) -> None:
        item_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        result = IntakeResult(
            item_id=item_id,
            status=IntakeStatus.assigned,
            assigned_lane=WorkCellLane.api_simple,
            assigned_task_id=task_id,
        )
        assert result.assigned_lane == WorkCellLane.api_simple
        assert result.assigned_task_id == task_id
        assert result.rejection_reason is None

    def test_rejected_result_has_no_lane(self) -> None:
        result = IntakeResult(
            item_id=str(uuid.uuid4()),
            status=IntakeStatus.rejected,
            rejection_reason="WIP full",
        )
        assert result.assigned_lane is None
        assert result.assigned_task_id is None
        assert result.rejection_reason == "WIP full"

    def test_default_optional_fields_are_none(self) -> None:
        result = IntakeResult(
            item_id=str(uuid.uuid4()),
            status=IntakeStatus.pending,
        )
        assert result.assigned_lane is None
        assert result.assigned_task_id is None
        assert result.rejection_reason is None

    def test_result_item_id_stored_correctly(self) -> None:
        iid = str(uuid.uuid4())
        result = IntakeResult(item_id=iid, status=IntakeStatus.assigned)
        assert result.item_id == iid

    def test_all_statuses_accepted_in_result(self) -> None:
        for status in IntakeStatus:
            result = IntakeResult(item_id=str(uuid.uuid4()), status=status)
            assert result.status == status


# ===========================================================================
# IntakeService.__init__
# ===========================================================================


class TestIntakeServiceInit:
    def test_default_queue_path_used_when_none_supplied(self) -> None:
        with patch.object(IntakeService, "_load_persisted"):
            svc = IntakeService(queue_path=None)
        assert svc._queue_path == _DEFAULT_QUEUE_PATH

    def test_custom_queue_path_stored(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom.jsonl"
        with patch.object(IntakeService, "_load_persisted"):
            svc = IntakeService(queue_path=custom)
        assert svc._queue_path == custom

    def test_internal_structures_empty_on_fresh_init(self, queue_path: Path) -> None:
        svc = IntakeService(queue_path=queue_path)
        assert svc._items == {}
        assert svc._status_index == {}
        assert svc._results == {}
        assert svc._source_index["missing_key"] == 0

    def test_lock_is_reentrant(self, queue_path: Path) -> None:
        svc = IntakeService(queue_path=queue_path)
        with svc._lock:
            with svc._lock:
                pass  # no deadlock — RLock re-entrant

    def test_load_persisted_called_during_init(self, queue_path: Path) -> None:
        with patch.object(IntakeService, "_load_persisted") as mock_load:
            IntakeService(queue_path=queue_path)
        mock_load.assert_called_once()


# ===========================================================================
# IntakeService.submit — accepted path
# ===========================================================================


class TestIntakeServiceSubmitAccepted:
    """Tests for the happy-path (item accepted, lane assigned)."""

    def test_submit_returns_intake_result(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        result = service.submit(minimal_item)
        assert isinstance(result, IntakeResult)

    def test_submit_accepted_status(self, service: IntakeService, minimal_item: IntakeItem) -> None:
        result = service.submit(minimal_item)
        assert result.status == IntakeStatus.assigned

    def test_submit_assigns_a_lane(self, service: IntakeService, minimal_item: IntakeItem) -> None:
        result = service.submit(minimal_item)
        assert result.assigned_lane is not None
        assert isinstance(result.assigned_lane, WorkCellLane)

    def test_submit_creates_task_id(self, service: IntakeService, minimal_item: IntakeItem) -> None:
        result = service.submit(minimal_item)
        assert result.assigned_task_id is not None
        parsed = uuid.UUID(result.assigned_task_id)
        assert parsed.version == 4

    def test_submit_item_id_matches(self, service: IntakeService, minimal_item: IntakeItem) -> None:
        result = service.submit(minimal_item)
        assert result.item_id == minimal_item.item_id

    def test_submit_no_rejection_reason_on_success(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        result = service.submit(minimal_item)
        assert result.rejection_reason is None

    def test_submit_bug_fix_low_maps_to_api_simple(self, service: IntakeService) -> None:
        item = _make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low)
        result = service.submit(item)
        assert result.assigned_lane == WorkCellLane.api_simple

    def test_submit_test_writing_maps_to_test_writing_lane(self, service: IntakeService) -> None:
        item = _make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low)
        result = service.submit(item)
        assert result.assigned_lane == WorkCellLane.test_writing

    def test_submit_security_change_maps_to_security_lane(self, service: IntakeService) -> None:
        item = _make_item(task_type=TaskType.security_change, risk_tier=RiskTier.low)
        result = service.submit(item)
        assert result.assigned_lane == WorkCellLane.security_change

    def test_submit_docs_update_maps_to_docs_lane(self, service: IntakeService) -> None:
        item = _make_item(
            task_type=TaskType.docs_update, risk_tier=RiskTier.low, source="heartbeat"
        )
        result = service.submit(item)
        assert result.assigned_lane == WorkCellLane.docs

    def test_multiple_unique_task_ids_per_submit(self, service: IntakeService) -> None:
        items = [_make_item(title=f"T{i}") for i in range(5)]
        task_ids = {service.submit(item).assigned_task_id for item in items}
        assert len(task_ids) == 5

    def test_status_index_updated_to_assigned(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        service.submit(minimal_item)
        assert service._status_index[minimal_item.item_id] == IntakeStatus.assigned

    def test_item_stored_in_items_dict(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        service.submit(minimal_item)
        assert minimal_item.item_id in service._items

    def test_result_stored_in_results_dict(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        result = service.submit(minimal_item)
        assert service._results[minimal_item.item_id] == result

    def test_source_index_incremented_on_accept(self, service: IntakeService) -> None:
        item = _make_item(source="cli")
        service.submit(item)
        assert service._source_index["cli"] == 1

    def test_persist_item_called_on_accepted(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item") as mock_persist,
            patch.object(service, "_emit_event"),
        ):
            result = service.submit(item)
        mock_persist.assert_called_once_with(item, result)

    def test_emit_event_called_with_accepted_type(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item"),
            patch.object(service, "_emit_event") as mock_emit,
        ):
            result = service.submit(item)
        mock_emit.assert_called_once_with("task.intake.accepted", item, result)

    def test_assign_lane_called_on_enforcer(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        item = _make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.medium)
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item"),
            patch.object(service, "_emit_event"),
        ):
            service.submit(item)
        mock_enforcer.assign_lane.assert_called_once_with(
            item.item_id, item.task_type, item.risk_tier
        )

    def test_multiple_submissions_accumulate_correctly(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        items = [_make_item(title=f"Task {i}", source="api") for i in range(3)]
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item"),
            patch.object(service, "_emit_event"),
        ):
            for item in items:
                service.submit(item)
        assert len(service._items) == 3
        assert service._source_index["api"] == 3


# ===========================================================================
# IntakeService.submit — WIP capacity rejection
# ===========================================================================


class TestIntakeServiceWIPRejection:
    """Tests for the WIP-capacity rejection path."""

    def test_deployment_lane_max_wip_is_1(self, service: IntakeService) -> None:
        item1 = _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low)
        result1 = service.submit(item1)
        assert result1.status == IntakeStatus.assigned

        item2 = _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="Deploy 2")
        result2 = service.submit(item2)
        assert result2.status == IntakeStatus.rejected

    def test_rejected_result_has_no_lane(self, service: IntakeService) -> None:
        for _ in range(1):  # fill deployment lane (max_wip=1)
            service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        result = service.submit(
            _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        )
        assert result.assigned_lane is None

    def test_rejected_result_has_no_task_id(self, service: IntakeService) -> None:
        service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        result = service.submit(
            _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        )
        assert result.assigned_task_id is None

    def test_rejected_result_includes_rejection_reason(self, service: IntakeService) -> None:
        service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        result = service.submit(
            _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        )
        assert result.rejection_reason is not None
        assert len(result.rejection_reason) > 0

    def test_rejection_reason_mentions_lane(self, service: IntakeService) -> None:
        service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        result = service.submit(
            _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        )
        assert "deployment" in (result.rejection_reason or "").lower()

    def test_rejection_reason_mentions_max_wip(self, service: IntakeService) -> None:
        service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        result = service.submit(
            _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        )
        assert "max_wip" in (result.rejection_reason or "")

    def test_security_lane_max_wip_is_2(self, service: IntakeService) -> None:
        for i in range(2):
            result = service.submit(
                _make_item(
                    task_type=TaskType.security_change, risk_tier=RiskTier.low, title=f"Sec-{i}"
                )
            )
            assert result.status == IntakeStatus.assigned

        result3 = service.submit(
            _make_item(task_type=TaskType.security_change, risk_tier=RiskTier.low, title="Sec-over")
        )
        assert result3.status == IntakeStatus.rejected

    def test_status_index_set_to_rejected(self, service: IntakeService) -> None:
        service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        item2 = _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        service.submit(item2)
        assert service._status_index[item2.item_id] == IntakeStatus.rejected

    def test_assign_lane_not_called_when_rejected(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item"),
            patch.object(service, "_emit_event"),
        ):
            service.submit(item)
        mock_enforcer.assign_lane.assert_not_called()

    def test_emit_event_called_with_rejected_type(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item"),
            patch.object(service, "_emit_event") as mock_emit,
        ):
            result = service.submit(item)
        mock_emit.assert_called_once_with("task.intake.rejected", item, result)

    def test_persist_item_still_called_on_rejection(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item") as mock_persist,
            patch.object(service, "_emit_event"),
        ):
            result = service.submit(item)
        mock_persist.assert_called_once_with(item, result)

    def test_source_index_incremented_even_on_rejection(
        self, service: IntakeService, mock_enforcer: MagicMock
    ) -> None:
        mock_enforcer.check_wip.return_value = False
        item = _make_item(source="heartbeat")
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(service, "_persist_item"),
            patch.object(service, "_emit_event"),
        ):
            service.submit(item)
        assert service._source_index["heartbeat"] == 1


# ===========================================================================
# IntakeService.list_pending
# ===========================================================================


class TestIntakeServiceListPending:
    def test_empty_service_has_no_pending(self, service: IntakeService) -> None:
        assert service.list_pending() == []

    def test_submitted_item_not_in_pending_after_assignment(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        service.submit(minimal_item)
        pending = service.list_pending()
        ids = [it.item_id for it in pending]
        assert minimal_item.item_id not in ids

    def test_list_pending_limit_is_respected(self, service: IntakeService) -> None:
        for i in range(10):
            item = _make_item(title=f"Pending-{i}")
            service._items[item.item_id] = item
            service._status_index[item.item_id] = IntakeStatus.pending

        result = service.list_pending(limit=5)
        assert len(result) <= 5

    def test_list_pending_excludes_assigned_and_rejected(self, service: IntakeService) -> None:
        assigned_item = _make_item(title="A")
        service.submit(assigned_item)

        rejected_item = _make_item(title="R")
        service._items[rejected_item.item_id] = rejected_item
        service._status_index[rejected_item.item_id] = IntakeStatus.rejected

        pending_item = _make_item(title="P")
        service._items[pending_item.item_id] = pending_item
        service._status_index[pending_item.item_id] = IntakeStatus.pending

        pending = service.list_pending()
        pending_ids = {it.item_id for it in pending}
        assert pending_item.item_id in pending_ids
        assert assigned_item.item_id not in pending_ids
        assert rejected_item.item_id not in pending_ids

    def test_list_pending_default_limit_is_50(self, service: IntakeService) -> None:
        for i in range(60):
            item = _make_item(title=f"P-{i}")
            service._items[item.item_id] = item
            service._status_index[item.item_id] = IntakeStatus.pending
        result = service.list_pending()
        assert len(result) == 50

    def test_does_not_include_expired(self, service: IntakeService) -> None:
        item = _make_item()
        service._items[item.item_id] = item
        service._status_index[item.item_id] = IntakeStatus.expired
        assert service.list_pending() == []

    def test_returns_list_of_intake_items(self, service: IntakeService) -> None:
        item = _make_item()
        service._items[item.item_id] = item
        service._status_index[item.item_id] = IntakeStatus.pending
        result = service.list_pending()
        assert isinstance(result, list)
        assert isinstance(result[0], IntakeItem)

    def test_limit_zero_returns_empty_list(self, service: IntakeService) -> None:
        item = _make_item()
        service._items[item.item_id] = item
        service._status_index[item.item_id] = IntakeStatus.pending
        result = service.list_pending(limit=0)
        assert result == []


# ===========================================================================
# IntakeService.get_stats
# ===========================================================================


class TestIntakeServiceGetStats:
    def test_fresh_service_has_zero_counts(self, service: IntakeService) -> None:
        stats = service.get_stats()
        assert stats["total"] == 0
        for count in stats["by_status"].values():
            assert count == 0

    def test_stats_total_increases_per_submit(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        service.submit(minimal_item)
        stats = service.get_stats()
        assert stats["total"] == 1

    def test_stats_by_status_assigned_count(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        service.submit(minimal_item)
        stats = service.get_stats()
        assert stats["by_status"]["assigned"] == 1
        assert stats["by_status"]["pending"] == 0

    def test_stats_by_status_rejected_count(self, service: IntakeService) -> None:
        service.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        service.submit(
            _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low, title="D2")
        )
        stats = service.get_stats()
        assert stats["by_status"]["rejected"] == 1
        assert stats["by_status"]["assigned"] == 1
        assert stats["total"] == 2

    def test_stats_by_source_counts_api(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        service.submit(minimal_item)
        stats = service.get_stats()
        assert stats["by_source"].get("api", 0) == 1

    def test_stats_by_source_multiple_sources(self, service: IntakeService) -> None:
        for source in ("api", "cli", "heartbeat"):
            service.submit(_make_item(source=source, title=f"From {source}"))
        stats = service.get_stats()
        assert stats["by_source"]["api"] == 1
        assert stats["by_source"]["cli"] == 1
        assert stats["by_source"]["heartbeat"] == 1
        assert stats["total"] == 3

    def test_stats_contains_all_status_keys(self, service: IntakeService) -> None:
        stats = service.get_stats()
        expected_keys = {s.value for s in IntakeStatus}
        assert expected_keys.issubset(stats["by_status"].keys())

    def test_stats_dict_has_expected_top_level_keys(self, service: IntakeService) -> None:
        stats = service.get_stats()
        assert set(stats.keys()) == {"by_status", "by_source", "total"}

    def test_stats_by_status_counts_correctly_across_statuses(self, service: IntakeService) -> None:
        for i in range(2):
            item = _make_item(title=f"Pending {i}")
            service._items[item.item_id] = item
            service._status_index[item.item_id] = IntakeStatus.pending
        for i in range(3):
            item = _make_item(title=f"Assigned {i}")
            service._items[item.item_id] = item
            service._status_index[item.item_id] = IntakeStatus.assigned
        item = _make_item(title="Rejected")
        service._items[item.item_id] = item
        service._status_index[item.item_id] = IntakeStatus.rejected

        stats = service.get_stats()
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["assigned"] == 3
        assert stats["by_status"]["rejected"] == 1
        assert stats["by_status"]["expired"] == 0


# ===========================================================================
# JSONL Persistence
# ===========================================================================


class TestJSONLPersistence:
    def test_item_written_to_jsonl_file(
        self, service: IntakeService, minimal_item: IntakeItem, queue_path: Path
    ) -> None:
        service.submit(minimal_item)
        assert queue_path.exists()
        lines = queue_path.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_jsonl_record_contains_item_and_result(
        self, service: IntakeService, minimal_item: IntakeItem, queue_path: Path
    ) -> None:
        service.submit(minimal_item)
        record = json.loads(queue_path.read_text().strip())
        assert "item" in record
        assert "result" in record

    def test_jsonl_item_id_matches(
        self, service: IntakeService, minimal_item: IntakeItem, queue_path: Path
    ) -> None:
        service.submit(minimal_item)
        record = json.loads(queue_path.read_text().strip())
        assert record["item"]["item_id"] == minimal_item.item_id

    def test_multiple_submits_append_multiple_lines(
        self, service: IntakeService, queue_path: Path
    ) -> None:
        for i in range(3):
            service.submit(_make_item(title=f"T-{i}"))
        lines = queue_path.read_text().strip().splitlines()
        assert len(lines) == 3

    def test_items_survive_service_restart(self, queue_path: Path) -> None:
        reset_lane_enforcer()
        svc1 = IntakeService(queue_path=queue_path)
        svc1.submit(_make_item(title="Persist me"))
        del svc1

        reset_lane_enforcer()
        svc2 = IntakeService(queue_path=queue_path)
        stats = svc2.get_stats()
        assert stats["total"] == 1

    def test_restart_preserves_status(self, queue_path: Path) -> None:
        reset_lane_enforcer()
        svc1 = IntakeService(queue_path=queue_path)
        item = _make_item(source="cli")
        result1 = svc1.submit(item)
        del svc1

        reset_lane_enforcer()
        svc2 = IntakeService(queue_path=queue_path)
        stats = svc2.get_stats()
        assert stats["by_status"][result1.status.value] == 1

    def test_malformed_jsonl_line_is_skipped(self, queue_path: Path) -> None:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("this is not valid json\n")
        reset_lane_enforcer()
        svc = IntakeService(queue_path=queue_path)
        assert svc.get_stats()["total"] == 0

    def test_missing_queue_file_is_handled(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent" / "queue.jsonl"
        reset_lane_enforcer()
        svc = IntakeService(queue_path=path)
        assert svc.get_stats()["total"] == 0

    def test_empty_lines_in_persistence_file_are_skipped(self, queue_path: Path) -> None:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("\n   \n")
        reset_lane_enforcer()
        svc = IntakeService(queue_path=queue_path)
        assert svc.get_stats()["total"] == 0

    def test_persistence_failure_does_not_break_submit(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        with patch.object(Path, "open", side_effect=OSError("Disk full")):
            result = service.submit(minimal_item)
        assert result.status == IntakeStatus.assigned

    def test_event_emission_failure_does_not_break_submit(
        self, service: IntakeService, minimal_item: IntakeItem
    ) -> None:
        with patch(
            "forge_harness.webhook_server.services.event_emitter.get_event_emitter"
        ) as mock_get:
            mock_emitter = MagicMock()
            mock_emitter.emit.side_effect = Exception("Network error")
            mock_get.return_value = mock_emitter
            result = service.submit(minimal_item)
        assert result.status == IntakeStatus.assigned

    def test_persist_creates_parent_directory(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "queue.jsonl"
        reset_lane_enforcer()
        svc = IntakeService(queue_path=deep_path)
        item = _make_item()
        result = _make_result(item)
        svc._persist_item(item, result)
        assert deep_path.parent.exists()
        assert deep_path.exists()

    def test_persist_io_error_swallowed(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item)
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            service._persist_item(item, result)  # must not raise

    def test_persist_io_error_logged(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item)
        with (
            patch.object(Path, "open", side_effect=OSError("disk full")),
            patch("forge_harness.webhook_server.services.intake_service.logger") as mock_logger,
        ):
            service._persist_item(item, result)
        mock_logger.error.assert_called_once()

    def test_load_persisted_replays_results_dict(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item()
        result = _make_result(item, status=IntakeStatus.rejected, lane=None, reason="full")
        record = {"item": item.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert item.item_id in svc._results

    def test_load_persisted_replays_source_index(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item(source="dispatch_file")
        result = _make_result(item)
        record = {"item": item.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert svc._source_index["dispatch_file"] == 1

    def test_load_persisted_multiple_valid_items(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        lines = []
        for i in range(5):
            item = _make_item(title=f"Task {i}")
            result = _make_result(item)
            record = {
                "item": item.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
            lines.append(json.dumps(record))
        queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert len(svc._items) == 5

    def test_load_persisted_malformed_line_logs_warning(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text("not-json\n", encoding="utf-8")
        with patch("forge_harness.webhook_server.services.intake_service.logger") as mock_logger:
            IntakeService(queue_path=queue_path)
        mock_logger.warning.assert_called()

    def test_load_persisted_valid_json_missing_result_key_logs_warning(
        self, tmp_path: Path
    ) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(json.dumps({"item": {}}) + "\n", encoding="utf-8")
        with patch("forge_harness.webhook_server.services.intake_service.logger") as mock_logger:
            IntakeService(queue_path=queue_path)
        mock_logger.warning.assert_called()


# ===========================================================================
# IntakeService._emit_event
# ===========================================================================


class TestEmitEvent:
    _EMITTER_PATH = "forge_harness.webhook_server.services.event_emitter.get_event_emitter"

    def test_calls_emitter_emit(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()
        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            service._emit_event("task.intake.accepted", item, result)
        mock_emitter.emit.assert_called_once()

    def test_emitter_receives_correct_sse_event_type(self, service: IntakeService) -> None:
        from forge_harness.webhook_server.models.sse_events import SSEEventType

        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()
        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            service._emit_event("task.intake.accepted", item, result)
        args, _ = mock_emitter.emit.call_args
        assert args[0] == SSEEventType.task_status_changed

    def test_emitter_payload_contains_event_key(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()
        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            service._emit_event("task.intake.rejected", item, result)
        args, _ = mock_emitter.emit.call_args
        payload = args[1]
        assert payload["event"] == "task.intake.rejected"
        assert payload["item_id"] == item.item_id

    def test_exception_in_emitter_is_swallowed(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item)
        with (
            patch(self._EMITTER_PATH, side_effect=RuntimeError("emitter down")),
            patch("forge_harness.webhook_server.services.intake_service.logger") as mock_logger,
        ):
            service._emit_event("task.intake.accepted", item, result)
        mock_logger.warning.assert_called()

    def test_emit_called_with_source_intake_service(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()
        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            service._emit_event("task.intake.accepted", item, result)
        _, kwargs = mock_emitter.emit.call_args
        assert kwargs.get("source") == "intake-service"

    def test_payload_includes_assigned_lane_value(self, service: IntakeService) -> None:
        item = _make_item()
        result = _make_result(item, lane=WorkCellLane.api_simple)
        mock_emitter = MagicMock()
        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            service._emit_event("task.intake.accepted", item, result)
        args, _ = mock_emitter.emit.call_args
        payload = args[1]
        assert payload["assigned_lane"] == WorkCellLane.api_simple.value

    def test_payload_assigned_lane_is_none_when_rejected(self, service: IntakeService) -> None:
        item = _make_item()
        result = IntakeResult(
            item_id=item.item_id,
            status=IntakeStatus.rejected,
            assigned_lane=None,
            assigned_task_id=None,
            rejection_reason="full",
        )
        mock_emitter = MagicMock()
        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            service._emit_event("task.intake.rejected", item, result)
        args, _ = mock_emitter.emit.call_args
        payload = args[1]
        assert payload["assigned_lane"] is None


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestSingletonPattern:
    def test_get_intake_service_returns_same_instance(self, queue_path: Path) -> None:
        svc1 = get_intake_service(queue_path=queue_path)
        svc2 = get_intake_service()
        assert svc1 is svc2

    def test_reset_intake_service_creates_new_instance(self, queue_path: Path) -> None:
        svc1 = get_intake_service(queue_path=queue_path)
        reset_intake_service()
        reset_lane_enforcer()
        svc2 = get_intake_service(queue_path=queue_path)
        assert svc1 is not svc2

    def test_queue_path_passed_only_on_first_call(self, queue_path: Path, tmp_path: Path) -> None:
        svc1 = get_intake_service(queue_path=queue_path)
        other_path = tmp_path / "other.jsonl"
        svc2 = get_intake_service(queue_path=other_path)
        assert svc1 is svc2

    def test_reset_before_any_get_is_safe(self) -> None:
        reset_intake_service()  # no-op — should not raise

    def test_reset_twice_is_safe(self, queue_path: Path) -> None:
        get_intake_service(queue_path=queue_path)
        reset_intake_service()
        reset_intake_service()

    def test_get_after_reset_creates_fresh_instance(self, queue_path: Path) -> None:
        svc1 = get_intake_service(queue_path=queue_path)
        svc1._source_index["api"] = 99
        reset_intake_service()
        svc2 = get_intake_service(queue_path=queue_path)
        assert svc2._source_index["api"] == 0


# ===========================================================================
# Thread safety — concurrent submit
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_submit_no_race_conditions(self, service: IntakeService) -> None:
        n = 20
        items = [_make_item(title=f"Concurrent-{i}") for i in range(n)]
        results: list[IntakeResult] = []
        lock = threading.Lock()

        def submit_one(item: IntakeItem) -> None:
            r = service.submit(item)
            with lock:
                results.append(r)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(submit_one, item) for item in items]
            concurrent.futures.wait(futures)

        assert len(results) == n
        stats = service.get_stats()
        assert stats["total"] == n

    def test_concurrent_submit_all_assigned_within_wip_limit(self, service: IntakeService) -> None:
        n = 8  # docs lane max_wip is 12 — all should be accepted
        items = [
            _make_item(
                task_type=TaskType.docs_update,
                risk_tier=RiskTier.low,
                source="cli",
                title=f"Doc-{i}",
            )
            for i in range(n)
        ]
        results: list[IntakeResult] = []
        lock = threading.Lock()

        def submit_one(item: IntakeItem) -> None:
            r = service.submit(item)
            with lock:
                results.append(r)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(submit_one, item) for item in items]
            concurrent.futures.wait(futures)

        assigned = [r for r in results if r.status == IntakeStatus.assigned]
        assert len(assigned) == n

    def test_concurrent_submit_unique_task_ids(self, service: IntakeService) -> None:
        n = 10
        items = [
            _make_item(
                task_type=TaskType.test_writing,
                risk_tier=RiskTier.low,
                source="heartbeat",
                title=f"Unique-{i}",
            )
            for i in range(n)
        ]
        results: list[IntakeResult] = []
        lock = threading.Lock()

        def submit_one(item: IntakeItem) -> None:
            r = service.submit(item)
            with lock:
                results.append(r)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(submit_one, item) for item in items]
            concurrent.futures.wait(futures)

        task_ids = {r.assigned_task_id for r in results if r.assigned_task_id}
        assigned_count = sum(1 for r in results if r.status == IntakeStatus.assigned)
        assert len(task_ids) == assigned_count

    def test_concurrent_get_stats_is_safe(self, service: IntakeService) -> None:
        """get_stats called concurrently while submitting should not raise."""
        errors: list[Exception] = []

        def submit_items() -> None:
            for i in range(5):
                service.submit(_make_item(title=f"S-{i}"))

        def read_stats() -> None:
            for _ in range(10):
                try:
                    service.get_stats()
                except Exception as exc:
                    errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(submit_items), pool.submit(read_stats), pool.submit(read_stats)]
            concurrent.futures.wait(futs)

        assert errors == []


# ===========================================================================
# API routes — POST /api/intake
# ===========================================================================


@pytest.fixture()
def api_client_fresh(tmp_path: Path):
    """
    TestClient with the intake router wired to a fresh isolated IntakeService.
    Event emission is mocked to avoid SSE dependencies.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from forge_harness.webhook_server.api.intake import router

    queue_path = tmp_path / "intake" / "queue.jsonl"
    test_service = IntakeService(queue_path=queue_path)

    app = FastAPI()
    app.include_router(router)

    with (
        patch(
            "forge_harness.webhook_server.api.intake._get_service",
            return_value=test_service,
        ),
        patch.object(test_service, "_emit_event"),
    ):
        yield TestClient(app), test_service


class TestApiSubmitIntakeItem:
    """Tests for POST /api/intake."""

    def _valid_body(self, **overrides) -> dict:
        base = {
            "title": "Add rate limiting to /api/tasks",
            "description": "Throttle to 100 req/min",
            "task_type": "bug-fix",
            "risk_tier": "low",
            "priority": 3,
            "source": "api",
            "metadata": {},
        }
        base.update(overrides)
        return base

    def test_submit_returns_201(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body())
        assert resp.status_code == 201

    def test_submit_response_has_success_true(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body())
        assert resp.json()["success"] is True

    def test_submit_response_data_has_item_id(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body())
        data = resp.json()["data"]
        assert "item_id" in data

    def test_submit_response_data_has_status(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body())
        data = resp.json()["data"]
        assert "status" in data

    def test_submit_accepted_status_in_response(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body())
        data = resp.json()["data"]
        assert data["status"] == "assigned"

    def test_submit_returns_assigned_lane(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body())
        data = resp.json()["data"]
        assert data["assigned_lane"] is not None

    def test_submit_invalid_task_type_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body(task_type="invalid-type-xyz"))
        assert resp.status_code == 422

    def test_submit_invalid_risk_tier_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body(risk_tier="extreme"))
        assert resp.status_code == 422

    def test_submit_invalid_task_type_error_mentions_task_type(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body(task_type="not-valid"))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "task_type" in str(detail).lower() or "invalid" in str(detail).lower()

    def test_submit_invalid_risk_tier_error_mentions_risk_tier(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body(risk_tier="unknown"))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "risk_tier" in str(detail).lower() or "invalid" in str(detail).lower()

    def test_submit_missing_title_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        body = self._valid_body()
        del body["title"]
        resp = client.post("/api/intake", json=body)
        assert resp.status_code == 422

    def test_submit_missing_task_type_field_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        body = self._valid_body()
        del body["task_type"]
        resp = client.post("/api/intake", json=body)
        assert resp.status_code == 422

    def test_submit_missing_risk_tier_field_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        body = self._valid_body()
        del body["risk_tier"]
        resp = client.post("/api/intake", json=body)
        assert resp.status_code == 422

    def test_submit_missing_source_field_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        body = self._valid_body()
        del body["source"]
        resp = client.post("/api/intake", json=body)
        assert resp.status_code == 422

    def test_submit_all_valid_task_types(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        for tt in TaskType:
            resp = client.post("/api/intake", json=self._valid_body(task_type=tt.value))
            # Some may be rejected by WIP; 201 or successful submission is expected
            assert resp.status_code == 201

    def test_submit_with_metadata(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post(
            "/api/intake",
            json=self._valid_body(metadata={"jira": "FORGE-99", "team": "backend"}),
        )
        assert resp.status_code == 201

    def test_submit_priority_out_of_range_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body(priority=0))
        assert resp.status_code == 422

    def test_submit_priority_too_high_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.post("/api/intake", json=self._valid_body(priority=6))
        assert resp.status_code == 422


# ===========================================================================
# API routes — GET /api/intake/stats
# ===========================================================================


class TestApiGetIntakeStats:
    """Tests for GET /api/intake/stats."""

    def test_stats_returns_200(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        assert resp.status_code == 200

    def test_stats_response_has_success_true(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        assert resp.json()["success"] is True

    def test_stats_data_has_by_status(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert "by_status" in data

    def test_stats_data_has_by_source(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert "by_source" in data

    def test_stats_data_has_total(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert "total" in data

    def test_stats_total_zero_on_empty_service(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert data["total"] == 0

    def test_stats_total_increments_after_submit(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        item = _make_item()
        with patch.object(svc, "_emit_event"):
            svc.submit(item)
        resp = client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert data["total"] == 1

    def test_stats_by_status_all_zero_on_empty(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        by_status = resp.json()["data"]["by_status"]
        for v in by_status.values():
            assert v == 0


# ===========================================================================
# API routes — GET /api/intake (list)
# ===========================================================================


class TestApiListIntakeItems:
    """Tests for GET /api/intake."""

    def test_list_returns_200(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake")
        assert resp.status_code == 200

    def test_list_response_has_items_and_count(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake")
        data = resp.json()["data"]
        assert "items" in data
        assert "count" in data

    def test_list_empty_on_fresh_service(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake")
        data = resp.json()["data"]
        assert data["count"] == 0
        assert data["items"] == []

    def test_list_with_valid_status_filter(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake?status=assigned")
        assert resp.status_code == 200

    def test_list_with_invalid_status_filter_returns_422(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get("/api/intake?status=invalid_xyz")
        assert resp.status_code == 422

    def test_list_with_pending_filter_returns_pending_items(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        # Manually inject a pending item
        item = _make_item(title="Pending task")
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.pending

        resp = client.get("/api/intake?status=pending")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["count"] >= 1
        ids = [i["item_id"] for i in data["items"]]
        assert item.item_id in ids

    def test_list_with_assigned_filter_excludes_pending(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        pending_item = _make_item(title="P")
        svc._items[pending_item.item_id] = pending_item
        svc._status_index[pending_item.item_id] = IntakeStatus.pending

        resp = client.get("/api/intake?status=assigned")
        data = resp.json()["data"]
        ids = [i["item_id"] for i in data["items"]]
        assert pending_item.item_id not in ids

    def test_list_all_valid_status_filters_accepted(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        for status in IntakeStatus:
            resp = client.get(f"/api/intake?status={status.value}")
            assert resp.status_code == 200

    def test_list_default_returns_pending_items_only(self, api_client_fresh) -> None:
        """No status filter defaults to list_pending() which returns pending items."""
        client, svc = api_client_fresh
        # Inject one pending and one assigned item
        pending = _make_item(title="Pending")
        svc._items[pending.item_id] = pending
        svc._status_index[pending.item_id] = IntakeStatus.pending

        assigned = _make_item(title="Assigned")
        svc._items[assigned.item_id] = assigned
        svc._status_index[assigned.item_id] = IntakeStatus.assigned

        resp = client.get("/api/intake")
        data = resp.json()["data"]
        ids = [i["item_id"] for i in data["items"]]
        assert pending.item_id in ids
        assert assigned.item_id not in ids


# ===========================================================================
# API routes — GET /api/intake/{item_id}
# ===========================================================================


class TestApiGetIntakeItem:
    """Tests for GET /api/intake/{item_id}."""

    def test_get_known_item_returns_200(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        item = _make_item(title="Known item")
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.assigned

        resp = client.get(f"/api/intake/{item.item_id}")
        assert resp.status_code == 200

    def test_get_known_item_has_success_true(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        item = _make_item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.assigned

        resp = client.get(f"/api/intake/{item.item_id}")
        assert resp.json()["success"] is True

    def test_get_known_item_data_has_item_id(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        item = _make_item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.assigned

        resp = client.get(f"/api/intake/{item.item_id}")
        data = resp.json()["data"]
        assert data["item_id"] == item.item_id

    def test_get_known_item_data_has_current_status(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        item = _make_item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.rejected

        resp = client.get(f"/api/intake/{item.item_id}")
        data = resp.json()["data"]
        assert data["current_status"] == "rejected"

    def test_get_unknown_item_returns_404(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get(f"/api/intake/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_unknown_item_has_not_found_error_code(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        resp = client.get(f"/api/intake/{uuid.uuid4()}")
        detail = resp.json()["detail"]
        assert detail["code"] == "NOT_FOUND"

    def test_get_item_includes_result_when_present(self, api_client_fresh) -> None:
        client, svc = api_client_fresh
        item = _make_item()
        result = _make_result(item)
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.assigned
        svc._results[item.item_id] = result

        resp = client.get(f"/api/intake/{item.item_id}")
        data = resp.json()["data"]
        assert "result" in data

    def test_get_item_after_api_submit(self, api_client_fresh) -> None:
        client, _ = api_client_fresh
        body = {
            "title": "New task via API",
            "task_type": "bug-fix",
            "risk_tier": "low",
            "source": "api",
        }
        post_resp = client.post("/api/intake", json=body)
        assert post_resp.status_code == 201
        item_id = post_resp.json()["data"]["item_id"]

        get_resp = client.get(f"/api/intake/{item_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["item_id"] == item_id

    def test_stats_endpoint_not_treated_as_item_id(self, api_client_fresh) -> None:
        """Ensure /api/intake/stats is not matched as /api/intake/{item_id}='stats'."""
        client, _ = api_client_fresh
        resp = client.get("/api/intake/stats")
        # Must return 200 with stats data, not 404
        assert resp.status_code == 200
        assert "by_status" in resp.json()["data"]

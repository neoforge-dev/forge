"""Unit tests for IntakeService (DF-2001).

Covers:
- IntakeService.__init__ (construction, queue_path default/override, _load_persisted replay)
- IntakeService.submit (accepted path, rejected/WIP-full path, source indexing, event emission,
  persistence call, logging)
- IntakeService.list_pending (empty, filters by status, respects limit)
- IntakeService.get_stats (by_status counts, by_source counts, total)
- IntakeService._persist_item (happy path, parent mkdir, error swallowed)
- IntakeService._load_persisted (no file, valid JSONL, malformed line skipped,
  blank lines skipped, multiple items)
- IntakeService._emit_event (happy path, exception swallowed)
- get_intake_service / reset_intake_service singleton lifecycle
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import RLock
from unittest.mock import MagicMock, Mock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build valid model instances without touching the filesystem
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.intake_queue import (
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

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    task_type: TaskType = TaskType.bug_fix,
    risk_tier: RiskTier = RiskTier.low,
    source: str = "api",
    title: str = "Fix a bug",
) -> IntakeItem:
    """Return a minimal valid IntakeItem."""
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
    """Return an IntakeResult matching *item*."""
    return IntakeResult(
        item_id=item.item_id,
        status=status,
        assigned_lane=lane,
        assigned_task_id=task_id or str(uuid.uuid4()),
        rejection_reason=reason,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset both the IntakeService and LaneEnforcer singletons before every test."""
    from forge_harness.webhook_server.services.lane_enforcer import reset_lane_enforcer

    reset_intake_service()
    reset_lane_enforcer()
    yield
    reset_intake_service()
    reset_lane_enforcer()


@pytest.fixture
def queue_path(tmp_path) -> Path:
    """Return a temporary JSONL path that does NOT exist yet."""
    return tmp_path / "intake" / "queue.jsonl"


@pytest.fixture
def svc(queue_path) -> IntakeService:
    """Return an IntakeService pointed at the temp path (no existing file)."""
    return IntakeService(queue_path=queue_path)


@pytest.fixture
def mock_enforcer():
    """Return a MagicMock enforcer with sensible defaults."""
    m = MagicMock()
    m.check_wip.return_value = True   # capacity available by default
    m.assign_lane.return_value = WorkCellLane.api_simple
    return m


@pytest.fixture
def mock_emitter():
    """Return a MagicMock EventEmitter."""
    return MagicMock()


# ---------------------------------------------------------------------------
# class TestIntakeServiceInit
# ---------------------------------------------------------------------------


class TestIntakeServiceInit:
    """Tests for IntakeService.__init__."""

    def test_default_queue_path_used_when_none_supplied(self):
        """When queue_path=None the service uses _DEFAULT_QUEUE_PATH."""
        with patch.object(IntakeService, "_load_persisted"):
            svc = IntakeService(queue_path=None)
        assert svc._queue_path == _DEFAULT_QUEUE_PATH

    def test_custom_queue_path_stored(self, tmp_path):
        custom = tmp_path / "custom.jsonl"
        with patch.object(IntakeService, "_load_persisted"):
            svc = IntakeService(queue_path=custom)
        assert svc._queue_path == custom

    def test_internal_structures_empty_on_fresh_init(self, queue_path):
        svc = IntakeService(queue_path=queue_path)
        assert svc._items == {}
        assert svc._status_index == {}
        assert svc._results == {}
        # _source_index is a defaultdict — accessing it should return 0
        assert svc._source_index["missing"] == 0

    def test_lock_is_rlock(self, queue_path):
        svc = IntakeService(queue_path=queue_path)
        # RLock instances are not directly inspectable by type name across
        # implementations, so we check that it can be acquired re-entrantly.
        with svc._lock:
            with svc._lock:
                pass  # no deadlock

    def test_load_persisted_called_during_init(self, queue_path):
        with patch.object(IntakeService, "_load_persisted") as mock_load:
            IntakeService(queue_path=queue_path)
        mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# class TestSubmitAccepted
# ---------------------------------------------------------------------------


class TestSubmitAccepted:
    """Tests for the happy-path (WIP capacity available) branch of submit."""

    def test_returns_assigned_status(self, svc, mock_enforcer, mock_emitter):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            result = svc.submit(item)

        assert result.status == IntakeStatus.assigned

    def test_result_contains_lane(self, svc, mock_enforcer):
        item = _make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low)
        mock_enforcer.check_wip.return_value = True

        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            result = svc.submit(item)

        # bug_fix + low → api_simple lane via LaneResolver
        assert result.assigned_lane == WorkCellLane.api_simple

    def test_result_has_task_id(self, svc, mock_enforcer):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            result = svc.submit(item)

        assert result.assigned_task_id is not None
        # Should be a valid UUID
        uuid.UUID(result.assigned_task_id)

    def test_no_rejection_reason_on_accepted(self, svc, mock_enforcer):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            result = svc.submit(item)

        assert result.rejection_reason is None

    def test_status_index_updated_to_assigned(self, svc, mock_enforcer):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            svc.submit(item)

        assert svc._status_index[item.item_id] == IntakeStatus.assigned

    def test_item_stored_in_items_dict(self, svc, mock_enforcer):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            svc.submit(item)

        assert item.item_id in svc._items

    def test_source_index_incremented(self, svc, mock_enforcer):
        item = _make_item(source="cli")
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._persist_item"
            ),
            patch(
                "forge_harness.webhook_server.services.intake_service.IntakeService._emit_event"
            ),
        ):
            svc.submit(item)

        assert svc._source_index["cli"] == 1

    def test_persist_item_called_on_accepted(self, svc, mock_enforcer):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item") as mock_persist,
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        mock_persist.assert_called_once_with(item, result)

    def test_emit_event_called_with_accepted_type(self, svc, mock_enforcer):
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event") as mock_emit,
        ):
            result = svc.submit(item)

        mock_emit.assert_called_once_with("task.intake.accepted", item, result)

    def test_assign_lane_called_on_enforcer(self, svc, mock_enforcer):
        item = _make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.medium)
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            svc.submit(item)

        mock_enforcer.assign_lane.assert_called_once_with(
            item.item_id, item.task_type, item.risk_tier
        )

    def test_multiple_submissions_accumulate_correctly(self, svc, mock_enforcer):
        items = [_make_item(title=f"Task {i}", source="api") for i in range(3)]
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            for item in items:
                svc.submit(item)

        assert len(svc._items) == 3
        assert svc._source_index["api"] == 3


# ---------------------------------------------------------------------------
# class TestSubmitRejected
# ---------------------------------------------------------------------------


class TestSubmitRejected:
    """Tests for the WIP-full (rejected) branch of submit."""

    def test_returns_rejected_status(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        assert result.status == IntakeStatus.rejected

    def test_rejection_reason_contains_lane_name(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low)
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        assert "api_simple" in result.rejection_reason
        assert "max_wip" in result.rejection_reason

    def test_rejected_result_has_no_lane(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        assert result.assigned_lane is None

    def test_rejected_result_has_no_task_id(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        assert result.assigned_task_id is None

    def test_status_index_set_to_rejected(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            svc.submit(item)

        assert svc._status_index[item.item_id] == IntakeStatus.rejected

    def test_assign_lane_not_called_when_rejected(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            svc.submit(item)

        mock_enforcer.assign_lane.assert_not_called()

    def test_emit_event_called_with_rejected_type(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event") as mock_emit,
        ):
            result = svc.submit(item)

        mock_emit.assert_called_once_with("task.intake.rejected", item, result)

    def test_persist_item_still_called_on_rejection(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item()
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item") as mock_persist,
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        mock_persist.assert_called_once_with(item, result)

    def test_source_index_incremented_even_on_rejection(self, svc, mock_enforcer):
        mock_enforcer.check_wip.return_value = False
        item = _make_item(source="heartbeat")
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            svc.submit(item)

        assert svc._source_index["heartbeat"] == 1

    def test_rejection_reason_contains_max_wip_value(self, svc, mock_enforcer):
        """The rejection message should embed the actual max_wip number."""
        mock_enforcer.check_wip.return_value = False
        item = _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low)
        with (
            patch(
                "forge_harness.webhook_server.services.intake_service.get_lane_enforcer",
                return_value=mock_enforcer,
            ),
            patch.object(svc, "_persist_item"),
            patch.object(svc, "_emit_event"),
        ):
            result = svc.submit(item)

        # deployment lane max_wip = 1
        assert "max_wip=1" in result.rejection_reason


# ---------------------------------------------------------------------------
# class TestListPending
# ---------------------------------------------------------------------------


class TestListPending:
    """Tests for IntakeService.list_pending."""

    def test_empty_queue_returns_empty_list(self, svc):
        assert svc.list_pending() == []

    def test_returns_only_pending_items(self, svc, mock_enforcer):
        item_pending = _make_item(title="Pending task")
        item_accepted = _make_item(title="Accepted task")

        # Submit both but make first one pending by manually forcing state
        svc._items[item_pending.item_id] = item_pending
        svc._status_index[item_pending.item_id] = IntakeStatus.pending

        svc._items[item_accepted.item_id] = item_accepted
        svc._status_index[item_accepted.item_id] = IntakeStatus.assigned

        pending = svc.list_pending()
        assert len(pending) == 1
        assert pending[0].item_id == item_pending.item_id

    def test_limit_is_respected(self, svc):
        for i in range(10):
            item = _make_item(title=f"Task {i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.pending

        assert len(svc.list_pending(limit=3)) == 3

    def test_default_limit_is_fifty(self, svc):
        for i in range(60):
            item = _make_item(title=f"Task {i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.pending

        assert len(svc.list_pending()) == 50

    def test_does_not_include_rejected(self, svc):
        item = _make_item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.rejected

        assert svc.list_pending() == []

    def test_does_not_include_expired(self, svc):
        item = _make_item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.expired

        assert svc.list_pending() == []

    def test_returns_list_of_intake_items(self, svc):
        item = _make_item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.pending

        result = svc.list_pending()
        assert isinstance(result, list)
        assert isinstance(result[0], IntakeItem)


# ---------------------------------------------------------------------------
# class TestGetStats
# ---------------------------------------------------------------------------


class TestGetStats:
    """Tests for IntakeService.get_stats."""

    def test_empty_service_returns_zeroed_stats(self, svc):
        stats = svc.get_stats()
        assert stats["total"] == 0
        assert all(v == 0 for v in stats["by_status"].values())

    def test_by_status_includes_all_statuses(self, svc):
        stats = svc.get_stats()
        for status in IntakeStatus:
            assert status.value in stats["by_status"]

    def test_total_equals_item_count(self, svc, mock_enforcer):
        for i in range(4):
            item = _make_item(title=f"Task {i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.assigned

        assert svc.get_stats()["total"] == 4

    def test_by_status_counts_correctly(self, svc):
        for i in range(2):
            item = _make_item(title=f"Pending {i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.pending

        for i in range(3):
            item = _make_item(title=f"Assigned {i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.assigned

        item = _make_item(title="Rejected")
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.rejected

        stats = svc.get_stats()
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["assigned"] == 3
        assert stats["by_status"]["rejected"] == 1
        assert stats["by_status"]["expired"] == 0

    def test_by_source_reflects_source_index(self, svc):
        svc._source_index["api"] = 5
        svc._source_index["cli"] = 2

        stats = svc.get_stats()
        assert stats["by_source"]["api"] == 5
        assert stats["by_source"]["cli"] == 2

    def test_stats_dict_has_expected_keys(self, svc):
        stats = svc.get_stats()
        assert set(stats.keys()) == {"by_status", "by_source", "total"}

    def test_total_zero_on_empty(self, svc):
        assert svc.get_stats()["total"] == 0


# ---------------------------------------------------------------------------
# class TestPersistItem
# ---------------------------------------------------------------------------


class TestPersistItem:
    """Tests for IntakeService._persist_item."""

    def test_creates_parent_directory_if_missing(self, tmp_path):
        queue_path = tmp_path / "deep" / "nested" / "queue.jsonl"
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()
        result = _make_result(item)

        svc._persist_item(item, result)
        assert queue_path.parent.exists()

    def test_writes_jsonl_record(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()
        result = _make_result(item)

        svc._persist_item(item, result)

        lines = queue_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert "item" in record
        assert "result" in record

    def test_written_record_contains_item_id(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()
        result = _make_result(item)

        svc._persist_item(item, result)

        record = json.loads(queue_path.read_text(encoding="utf-8").strip())
        assert record["item"]["item_id"] == item.item_id

    def test_multiple_calls_append_multiple_lines(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        svc = IntakeService(queue_path=queue_path)
        items = [_make_item(title=f"Task {i}") for i in range(3)]

        for item in items:
            result = _make_result(item)
            svc._persist_item(item, result)

        lines = queue_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_io_error_is_swallowed(self, queue_path):
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()
        result = _make_result(item)

        # Simulate an IO error via Path.open
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            # Should not raise
            svc._persist_item(item, result)

    def test_io_error_logged(self, queue_path):
        """Error path is logged — verified via mock on the module logger."""
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()
        result = _make_result(item)

        with (
            patch.object(Path, "open", side_effect=OSError("disk full")),
            patch(
                "forge_harness.webhook_server.services.intake_service.logger"
            ) as mock_logger,
        ):
            svc._persist_item(item, result)

        mock_logger.error.assert_called_once()
        logged_msg = mock_logger.error.call_args[0][0]
        assert "failed to write" in logged_msg


# ---------------------------------------------------------------------------
# class TestLoadPersisted
# ---------------------------------------------------------------------------


class TestLoadPersisted:
    """Tests for IntakeService._load_persisted."""

    def test_no_queue_file_starts_fresh(self, queue_path):
        # File does not exist — should silently succeed
        svc = IntakeService(queue_path=queue_path)
        assert svc._items == {}

    def test_valid_jsonl_replays_items(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item(title="Replayed task")
        result = _make_result(item, status=IntakeStatus.assigned)

        record = {
            "item": item.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert item.item_id in svc._items
        assert svc._status_index[item.item_id] == IntakeStatus.assigned

    def test_blank_lines_are_skipped(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item()
        result = _make_result(item)
        record = {"item": item.model_dump(mode="json"), "result": result.model_dump(mode="json")}

        # Write a blank line then a valid record
        queue_path.write_text("\n" + json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert len(svc._items) == 1

    def test_malformed_line_skipped_but_valid_lines_loaded(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item()
        result = _make_result(item)
        valid_record = json.dumps(
            {"item": item.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        )

        content = "this is not json\n" + valid_record + "\n"
        queue_path.write_text(content, encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert len(svc._items) == 1
        assert item.item_id in svc._items

    def test_malformed_line_logs_warning(self, tmp_path):
        """Malformed JSONL lines produce a logger.warning — verified via mock."""
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text("not-json\n", encoding="utf-8")

        with patch(
            "forge_harness.webhook_server.services.intake_service.logger"
        ) as mock_logger:
            IntakeService(queue_path=queue_path)

        mock_logger.warning.assert_called()
        logged_msg = mock_logger.warning.call_args[0][0]
        assert "skipping malformed line" in logged_msg

    def test_source_index_replayed(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item(source="dispatch_file")
        result = _make_result(item)
        record = {"item": item.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert svc._source_index["dispatch_file"] == 1

    def test_multiple_valid_items_loaded(self, tmp_path):
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

    def test_results_dict_populated_from_file(self, tmp_path):
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item()
        result = _make_result(item, status=IntakeStatus.rejected, lane=None, reason="full")
        record = {"item": item.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        assert item.item_id in svc._results

    def test_item_with_missing_key_logs_warning(self, tmp_path):
        """Valid JSON with missing 'result' key produces a logger.warning."""
        queue_path = tmp_path / "queue.jsonl"
        # Valid JSON but missing the "result" key
        queue_path.write_text(json.dumps({"item": {}}) + "\n", encoding="utf-8")

        with patch(
            "forge_harness.webhook_server.services.intake_service.logger"
        ) as mock_logger:
            IntakeService(queue_path=queue_path)

        mock_logger.warning.assert_called()
        logged_msg = mock_logger.warning.call_args[0][0]
        assert "skipping malformed" in logged_msg


# ---------------------------------------------------------------------------
# class TestEmitEvent
# ---------------------------------------------------------------------------


class TestEmitEvent:
    """Tests for IntakeService._emit_event.

    ``_emit_event`` uses a deferred (local) import of ``get_event_emitter``,
    so we patch it at its definition site in ``event_emitter``, not at the
    ``intake_service`` module namespace.
    """

    _EMITTER_PATH = (
        "forge_harness.webhook_server.services.event_emitter.get_event_emitter"
    )

    def test_calls_emitter_emit(self, svc):
        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            svc._emit_event("task.intake.accepted", item, result)

        mock_emitter.emit.assert_called_once()

    def test_emitter_receives_correct_event_type(self, svc):
        from forge_harness.webhook_server.models.sse_events import SSEEventType

        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            svc._emit_event("task.intake.accepted", item, result)

        args, kwargs = mock_emitter.emit.call_args
        assert args[0] == SSEEventType.task_status_changed

    def test_emitter_payload_contains_event_type_key(self, svc):
        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            svc._emit_event("task.intake.rejected", item, result)

        args, kwargs = mock_emitter.emit.call_args
        payload = args[1]
        assert payload["event"] == "task.intake.rejected"
        assert payload["item_id"] == item.item_id

    def test_exception_in_emitter_is_swallowed(self, svc):
        """Exception from get_event_emitter is caught and logged as a warning."""
        item = _make_item()
        result = _make_result(item)

        with (
            patch(self._EMITTER_PATH, side_effect=RuntimeError("emitter down")),
            patch(
                "forge_harness.webhook_server.services.intake_service.logger"
            ) as mock_logger,
        ):
            # Must not raise
            svc._emit_event("task.intake.accepted", item, result)

        mock_logger.warning.assert_called()
        logged_msg = mock_logger.warning.call_args[0][0]
        assert "failed to emit" in logged_msg

    def test_emit_called_with_source_intake_service(self, svc):
        item = _make_item()
        result = _make_result(item)
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            svc._emit_event("task.intake.accepted", item, result)

        _, kwargs = mock_emitter.emit.call_args
        assert kwargs.get("source") == "intake-service"

    def test_payload_includes_assigned_lane_value(self, svc):
        item = _make_item()
        result = _make_result(item, lane=WorkCellLane.api_simple)
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            svc._emit_event("task.intake.accepted", item, result)

        args, _ = mock_emitter.emit.call_args
        payload = args[1]
        assert payload["assigned_lane"] == WorkCellLane.api_simple.value

    def test_payload_assigned_lane_is_none_when_rejected(self, svc):
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
            svc._emit_event("task.intake.rejected", item, result)

        args, _ = mock_emitter.emit.call_args
        payload = args[1]
        assert payload["assigned_lane"] is None


# ---------------------------------------------------------------------------
# class TestGetIntakeService
# ---------------------------------------------------------------------------


class TestGetIntakeService:
    """Tests for the get_intake_service / reset_intake_service singleton."""

    def test_returns_intake_service_instance(self, queue_path):
        svc = get_intake_service(queue_path=queue_path)
        assert isinstance(svc, IntakeService)

    def test_same_instance_on_repeated_calls(self, queue_path):
        svc1 = get_intake_service(queue_path=queue_path)
        svc2 = get_intake_service()
        assert svc1 is svc2

    def test_queue_path_used_on_first_call(self, queue_path):
        svc = get_intake_service(queue_path=queue_path)
        assert svc._queue_path == queue_path

    def test_queue_path_ignored_on_subsequent_calls(self, tmp_path, queue_path):
        svc1 = get_intake_service(queue_path=queue_path)
        other_path = tmp_path / "other.jsonl"
        svc2 = get_intake_service(queue_path=other_path)
        # Second call must return the existing singleton — path must match first
        assert svc2._queue_path == queue_path

    def test_reset_clears_singleton(self, queue_path):
        svc1 = get_intake_service(queue_path=queue_path)
        reset_intake_service()
        svc2 = get_intake_service(queue_path=queue_path)
        assert svc1 is not svc2

    def test_reset_twice_is_safe(self):
        reset_intake_service()
        reset_intake_service()  # Should not raise

    def test_get_after_reset_creates_fresh_instance(self, queue_path):
        svc1 = get_intake_service(queue_path=queue_path)
        # Inject some state
        svc1._source_index["api"] = 99

        reset_intake_service()
        svc2 = get_intake_service(queue_path=queue_path)
        # Fresh instance should have an empty source index
        assert svc2._source_index["api"] == 0


# ---------------------------------------------------------------------------
# class TestResetIntakeService
# ---------------------------------------------------------------------------


class TestResetIntakeService:
    """Tests for reset_intake_service standalone behaviour."""

    def test_reset_before_any_get_is_safe(self):
        reset_intake_service()  # no prior get — should be a no-op

    def test_after_reset_get_returns_new_instance(self, queue_path):
        svc_a = get_intake_service(queue_path=queue_path)
        reset_intake_service()
        svc_b = get_intake_service(queue_path=queue_path)
        assert svc_a is not svc_b


# ---------------------------------------------------------------------------
# class TestSubmitIntegration
# ---------------------------------------------------------------------------


class TestSubmitIntegration:
    """End-to-end submit tests that exercise the full call chain without
    mocking the enforcer or emitter (uses real implementations with a fresh
    singleton enforcer). File I/O is pointed at a tmp path."""

    def test_submit_accepted_real_enforcer(self, queue_path):
        """Real LaneEnforcer + real LaneResolver — should accept a fresh item."""
        svc = IntakeService(queue_path=queue_path)
        item = _make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low)

        with patch.object(svc, "_emit_event"):
            result = svc.submit(item)

        assert result.status == IntakeStatus.assigned
        assert result.assigned_lane == WorkCellLane.test_writing

    def test_submit_writes_file(self, queue_path):
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()

        with patch.object(svc, "_emit_event"):
            svc.submit(item)

        assert queue_path.exists()
        lines = queue_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["item"]["item_id"] == item.item_id

    def test_submit_then_list_pending_shows_no_pending(self, queue_path):
        """Accepted items move to 'assigned', so list_pending returns empty."""
        svc = IntakeService(queue_path=queue_path)
        item = _make_item()

        with patch.object(svc, "_emit_event"):
            svc.submit(item)

        assert svc.list_pending() == []

    def test_submit_stats_reflect_accepted_item(self, queue_path):
        svc = IntakeService(queue_path=queue_path)
        item = _make_item(source="cli")

        with patch.object(svc, "_emit_event"):
            svc.submit(item)

        stats = svc.get_stats()
        assert stats["total"] == 1
        assert stats["by_status"]["assigned"] == 1
        assert stats["by_source"]["cli"] == 1

    def test_submit_fills_wip_and_next_is_rejected(self, tmp_path):
        """Fill the deployment lane (max_wip=1) then verify second is rejected."""
        from forge_harness.webhook_server.services.lane_enforcer import (
            get_lane_enforcer,
            reset_lane_enforcer,
        )

        reset_lane_enforcer()
        queue_path = tmp_path / "q.jsonl"
        svc = IntakeService(queue_path=queue_path)
        enforcer = get_lane_enforcer()

        item1 = _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low)
        item2 = _make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low)

        with patch.object(svc, "_emit_event"):
            result1 = svc.submit(item1)
            result2 = svc.submit(item2)

        assert result1.status == IntakeStatus.assigned
        assert result2.status == IntakeStatus.rejected
        assert "deployment" in result2.rejection_reason

    def test_crash_recovery_via_load_persisted(self, tmp_path):
        """Write a JSONL file then construct a fresh service — items must replay."""
        queue_path = tmp_path / "queue.jsonl"
        item = _make_item(title="Replayed", source="api")
        result = _make_result(item, status=IntakeStatus.assigned)

        record = {
            "item": item.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        svc = IntakeService(queue_path=queue_path)
        stats = svc.get_stats()
        assert stats["total"] == 1
        assert stats["by_source"]["api"] == 1

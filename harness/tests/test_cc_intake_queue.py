"""Comprehensive tests for IntakeService (Command Center — Dark Factory).

Targets forge_harness/webhook_server/services/intake_service.py.

Coverage goals (70%+ per task spec):
  - All public methods: submit, list_pending, get_stats
  - Persistence: _persist_item (success + OSError path), _load_persisted
    (no file, valid file, malformed line, blank line)
  - Event emission: _emit_event (success + exception fallback)
  - Singleton: get_intake_service, reset_intake_service
  - WIP-capacity logic: accept when capacity available, reject when WIP full
  - Status index: pending → assigned, pending → rejected
  - Source index accumulation across multiple submissions
  - Thread-safety under concurrent submit
  - Edge cases: empty queue, list_pending limit, get_stats breakdown, out-of-band
    JSONL replay, partial-write resilience

Note: worktree_manager.py does not exist in the services directory.
      intake_queue.py does not exist as a standalone service — the intake
      functionality lives in intake_service.py.  Tests target that module.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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
    IntakeService,
    get_intake_service,
    reset_intake_service,
)
from forge_harness.webhook_server.services.lane_enforcer import reset_lane_enforcer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset both intake and lane enforcer singletons before every test."""
    reset_intake_service()
    reset_lane_enforcer()
    yield
    reset_intake_service()
    reset_lane_enforcer()


@pytest.fixture()
def queue_file(tmp_path: Path) -> Path:
    return tmp_path / ".forge" / "intake" / "queue.jsonl"


@pytest.fixture()
def svc(queue_file: Path) -> IntakeService:
    """Fresh IntakeService backed by a temp JSONL file."""
    return IntakeService(queue_path=queue_file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    title: str = "Test task",
    task_type: TaskType = TaskType.test_writing,
    risk_tier: RiskTier = RiskTier.low,
    source: str = "api",
    priority: int = 3,
    description: str = "",
    metadata: dict | None = None,
) -> IntakeItem:
    return IntakeItem(
        title=title,
        task_type=task_type,
        risk_tier=risk_tier,
        source=source,
        priority=priority,
        description=description,
        metadata=metadata or {},
    )


def _item_bug_fix_low() -> IntakeItem:
    return _item(title="Fix auth bug", task_type=TaskType.bug_fix, risk_tier=RiskTier.low)


def _item_docs_low() -> IntakeItem:
    return _item(title="Update README", task_type=TaskType.docs_update, risk_tier=RiskTier.low)


# ===========================================================================
# IntakeItem model validation (smoke tests for model layer)
# ===========================================================================


class TestIntakeItemModel:
    def test_auto_generates_item_id(self):
        item = _item()
        assert item.item_id
        import uuid
        uuid.UUID(item.item_id)  # validates UUID format

    def test_auto_generates_submitted_at(self):
        item = _item()
        assert item.submitted_at

    def test_invalid_source_raises_validation_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="invalid_source",
            )

    def test_valid_sources_accepted(self):
        for src in VALID_SOURCES:
            item = IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source=src,
            )
            assert item.source == src

    def test_title_min_length_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IntakeItem(
                title="",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
            )

    def test_title_max_length_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IntakeItem(
                title="x" * 201,
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
            )

    def test_priority_min_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
                priority=0,
            )

    def test_priority_max_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IntakeItem(
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
                priority=6,
            )

    def test_metadata_defaults_to_empty_dict(self):
        item = _item()
        assert item.metadata == {}

    def test_description_defaults_to_empty_string(self):
        item = _item()
        assert item.description == ""


# ===========================================================================
# IntakeResult model
# ===========================================================================


class TestIntakeResultModel:
    def test_accepted_result_has_lane_and_task_id(self, svc: IntakeService):
        result = svc.submit(_item_bug_fix_low())
        assert result.status == IntakeStatus.assigned
        assert result.assigned_lane is not None
        assert result.assigned_task_id is not None
        assert result.rejection_reason is None

    def test_rejected_result_has_reason(self, svc: IntakeService):
        """Fill deployment lane (max_wip=1) then submit another."""
        # deployment lane has max_wip=1 — fill it
        svc.submit(_item(
            title="Deploy 1",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        # Submit a second deployment to trigger rejection
        result = svc.submit(_item(
            title="Deploy 2",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        assert result.status == IntakeStatus.rejected
        assert result.rejection_reason is not None
        assert result.assigned_lane is None
        assert result.assigned_task_id is None


# ===========================================================================
# IntakeService.__init__
# ===========================================================================


class TestIntakeServiceInit:
    def test_uses_provided_queue_path(self, queue_file: Path):
        svc = IntakeService(queue_path=queue_file)
        assert svc._queue_path == queue_file

    def test_uses_default_queue_path_when_none(self):
        svc = IntakeService(queue_path=None)
        assert svc._queue_path.name == "queue.jsonl"

    def test_empty_items_on_fresh_init(self, queue_file: Path):
        svc = IntakeService(queue_path=queue_file)
        assert svc._items == {}

    def test_empty_status_index_on_fresh_init(self, queue_file: Path):
        svc = IntakeService(queue_path=queue_file)
        assert svc._status_index == {}

    def test_empty_source_index_on_fresh_init(self, queue_file: Path):
        svc = IntakeService(queue_path=queue_file)
        assert dict(svc._source_index) == {}


# ===========================================================================
# submit — accepted path
# ===========================================================================


class TestSubmitAccepted:
    def test_returns_intake_result(self, svc: IntakeService):
        result = svc.submit(_item_bug_fix_low())
        assert isinstance(result, IntakeResult)

    def test_status_is_assigned(self, svc: IntakeService):
        result = svc.submit(_item_bug_fix_low())
        assert result.status == IntakeStatus.assigned

    def test_item_id_matches(self, svc: IntakeService):
        item = _item_bug_fix_low()
        result = svc.submit(item)
        assert result.item_id == item.item_id

    def test_assigned_task_id_is_uuid(self, svc: IntakeService):
        import uuid
        result = svc.submit(_item_bug_fix_low())
        assert result.assigned_task_id is not None
        uuid.UUID(result.assigned_task_id)

    def test_assigned_lane_is_work_cell_lane(self, svc: IntakeService):
        result = svc.submit(_item_bug_fix_low())
        assert isinstance(result.assigned_lane, WorkCellLane)

    def test_bug_fix_low_routes_to_api_simple(self, svc: IntakeService):
        result = svc.submit(_item_bug_fix_low())
        assert result.assigned_lane == WorkCellLane.api_simple

    def test_test_writing_routes_to_test_writing_lane(self, svc: IntakeService):
        result = svc.submit(_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low))
        assert result.assigned_lane == WorkCellLane.test_writing

    def test_docs_update_routes_to_docs_lane(self, svc: IntakeService):
        result = svc.submit(_item(task_type=TaskType.docs_update, risk_tier=RiskTier.low))
        assert result.assigned_lane == WorkCellLane.docs

    def test_security_change_routes_to_security_lane(self, svc: IntakeService):
        result = svc.submit(_item(
            task_type=TaskType.security_change, risk_tier=RiskTier.low
        ))
        assert result.assigned_lane == WorkCellLane.security_change

    def test_item_recorded_in_items_dict(self, svc: IntakeService):
        item = _item_bug_fix_low()
        svc.submit(item)
        assert item.item_id in svc._items

    def test_status_index_set_to_assigned(self, svc: IntakeService):
        item = _item_bug_fix_low()
        svc.submit(item)
        assert svc._status_index[item.item_id] == IntakeStatus.assigned

    def test_result_recorded_in_results_dict(self, svc: IntakeService):
        item = _item_bug_fix_low()
        result = svc.submit(item)
        assert item.item_id in svc._results
        assert svc._results[item.item_id].status == IntakeStatus.assigned

    def test_source_index_incremented(self, svc: IntakeService):
        svc.submit(_item(source="api"))
        svc.submit(_item(source="api"))
        svc.submit(_item(source="cli"))
        assert svc._source_index["api"] == 2
        assert svc._source_index["cli"] == 1

    def test_multiple_submissions_have_unique_task_ids(self, svc: IntakeService):
        results = [svc.submit(_item_bug_fix_low()) for _ in range(5)]
        task_ids = [r.assigned_task_id for r in results]
        assert len(set(task_ids)) == 5


# ===========================================================================
# submit — rejected path (WIP full)
# ===========================================================================


class TestSubmitRejected:
    def _fill_deployment_lane(self, svc: IntakeService):
        """Deployment lane has max_wip=1. Submit one to fill it."""
        return svc.submit(_item(
            title="Occupy deploy lane",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))

    def test_rejected_when_wip_full(self, svc: IntakeService):
        self._fill_deployment_lane(svc)
        result = svc.submit(_item(
            title="Overflow deploy",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        assert result.status == IntakeStatus.rejected

    def test_rejection_reason_mentions_lane(self, svc: IntakeService):
        self._fill_deployment_lane(svc)
        result = svc.submit(_item(
            title="Rejected deploy",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        assert result.rejection_reason is not None
        assert "deployment" in result.rejection_reason.lower()

    def test_rejected_item_has_no_assigned_lane(self, svc: IntakeService):
        self._fill_deployment_lane(svc)
        result = svc.submit(_item(
            title="Blocked deploy",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        assert result.assigned_lane is None

    def test_rejected_item_has_no_task_id(self, svc: IntakeService):
        self._fill_deployment_lane(svc)
        result = svc.submit(_item(
            title="Rejected",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        assert result.assigned_task_id is None

    def test_rejected_item_status_index_is_rejected(self, svc: IntakeService):
        self._fill_deployment_lane(svc)
        item = _item(
            title="R-item",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        )
        svc.submit(item)
        assert svc._status_index[item.item_id] == IntakeStatus.rejected

    def test_rejected_item_persisted(self, svc: IntakeService, queue_file: Path):
        self._fill_deployment_lane(svc)
        svc.submit(_item(
            title="Rejected deploy",
            task_type=TaskType.deployment,
            risk_tier=RiskTier.low,
            source="api",
        ))
        assert queue_file.exists()
        lines = [l for l in queue_file.read_text().splitlines() if l.strip()]
        # First line = accepted, second line = rejected
        assert len(lines) == 2
        record = json.loads(lines[1])
        assert record["result"]["status"] == IntakeStatus.rejected.value

    def test_security_change_lane_max_wip_2(self, svc: IntakeService):
        """security_change lane has max_wip=2. Two accepted, third rejected."""
        for _ in range(2):
            result = svc.submit(_item(
                task_type=TaskType.security_change, risk_tier=RiskTier.low
            ))
            assert result.status == IntakeStatus.assigned
        third = svc.submit(_item(
            task_type=TaskType.security_change, risk_tier=RiskTier.low
        ))
        assert third.status == IntakeStatus.rejected


# ===========================================================================
# list_pending
# ===========================================================================


class TestListPending:
    def test_empty_on_fresh_service(self, svc: IntakeService):
        assert svc.list_pending() == []

    def test_no_pending_after_submission(self, svc: IntakeService):
        """Submissions immediately transition to assigned/rejected, not pending."""
        svc.submit(_item_bug_fix_low())
        # After submit, status is assigned — should NOT appear in pending
        assert svc.list_pending() == []

    def test_manually_injected_pending_item_appears(self, svc: IntakeService):
        """Inject a pending item directly to verify list_pending filters correctly."""
        item = _item_bug_fix_low()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.pending
        pending = svc.list_pending()
        assert len(pending) == 1
        assert pending[0].item_id == item.item_id

    def test_limit_applied(self, svc: IntakeService):
        """limit parameter caps results."""
        for i in range(10):
            item = _item(title=f"Pending {i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.pending
        result = svc.list_pending(limit=3)
        assert len(result) == 3

    def test_default_limit_is_50(self, svc: IntakeService):
        for i in range(60):
            item = _item(title=f"P{i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.pending
        result = svc.list_pending()
        assert len(result) == 50

    def test_only_pending_status_returned(self, svc: IntakeService):
        """Only items with pending status are returned."""
        assigned_item = _item(title="Assigned")
        rejected_item = _item(title="Rejected")
        pending_item = _item(title="Pending")

        for it, status in [
            (assigned_item, IntakeStatus.assigned),
            (rejected_item, IntakeStatus.rejected),
            (pending_item, IntakeStatus.pending),
        ]:
            svc._items[it.item_id] = it
            svc._status_index[it.item_id] = status

        result = svc.list_pending()
        assert len(result) == 1
        assert result[0].item_id == pending_item.item_id


# ===========================================================================
# get_stats
# ===========================================================================


class TestGetStats:
    def test_empty_stats_on_fresh_service(self, svc: IntakeService):
        stats = svc.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {s.value: 0 for s in IntakeStatus}
        assert stats["by_source"] == {}

    def test_total_increments_on_submit(self, svc: IntakeService):
        svc.submit(_item_bug_fix_low())
        svc.submit(_item_docs_low())
        stats = svc.get_stats()
        assert stats["total"] == 2

    def test_by_status_assigned_count(self, svc: IntakeService):
        svc.submit(_item_bug_fix_low())
        svc.submit(_item_docs_low())
        stats = svc.get_stats()
        assert stats["by_status"]["assigned"] == 2
        assert stats["by_status"]["rejected"] == 0

    def test_by_source_accumulation(self, svc: IntakeService):
        svc.submit(_item(source="api"))
        svc.submit(_item(source="api"))
        svc.submit(_item(source="cli"))
        svc.submit(_item(source="dispatch_file"))
        stats = svc.get_stats()
        assert stats["by_source"]["api"] == 2
        assert stats["by_source"]["cli"] == 1
        assert stats["by_source"]["dispatch_file"] == 1

    def test_by_status_all_keys_present(self, svc: IntakeService):
        stats = svc.get_stats()
        for status in IntakeStatus:
            assert status.value in stats["by_status"]

    def test_rejected_counted_in_by_status(self, svc: IntakeService):
        """Fill deployment lane then overflow to get a rejection."""
        svc.submit(_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        svc.submit(_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        stats = svc.get_stats()
        assert stats["by_status"]["rejected"] == 1
        assert stats["by_status"]["assigned"] == 1

    def test_manually_pending_item_counted(self, svc: IntakeService):
        item = _item()
        svc._items[item.item_id] = item
        svc._status_index[item.item_id] = IntakeStatus.pending
        stats = svc.get_stats()
        assert stats["by_status"]["pending"] == 1
        assert stats["total"] == 1


# ===========================================================================
# Persistence — _persist_item
# ===========================================================================


class TestPersistItem:
    def test_queue_file_created_on_first_submit(self, svc: IntakeService, queue_file: Path):
        svc.submit(_item_bug_fix_low())
        assert queue_file.exists()

    def test_one_line_per_submission(self, svc: IntakeService, queue_file: Path):
        svc.submit(_item_bug_fix_low())
        svc.submit(_item_docs_low())
        lines = [l for l in queue_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_each_line_is_valid_json(self, svc: IntakeService, queue_file: Path):
        svc.submit(_item_bug_fix_low())
        for line in queue_file.read_text().splitlines():
            if line.strip():
                data = json.loads(line)
                assert "item" in data
                assert "result" in data

    def test_record_contains_item_and_result(self, svc: IntakeService, queue_file: Path):
        item = _item_bug_fix_low()
        svc.submit(item)
        line = queue_file.read_text().strip()
        record = json.loads(line)
        assert record["item"]["item_id"] == item.item_id
        assert record["result"]["item_id"] == item.item_id

    def test_parent_dir_created_automatically(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "queue.jsonl"
        svc = IntakeService(queue_path=deep_path)
        svc.submit(_item_bug_fix_low())
        assert deep_path.exists()

    def test_oserror_on_persist_does_not_propagate(self, svc: IntakeService, queue_file: Path):
        """OSError in _persist_item is logged but never raises to caller."""
        original_open = Path.open

        def mock_open(self_inner, *args, **kwargs):
            if "queue.jsonl" in str(self_inner):
                raise OSError("write permission denied")
            return original_open(self_inner, *args, **kwargs)

        # Create directory first so only the open fails
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(Path, "open", mock_open):
            result = svc.submit(_item_bug_fix_low())

        # Submission should still return a result
        assert result is not None

    def test_file_is_append_only(self, svc: IntakeService, queue_file: Path):
        svc.submit(_item_bug_fix_low())
        first_content = queue_file.read_text()
        svc.submit(_item_docs_low())
        second_content = queue_file.read_text()
        # Second write appends — first line still present
        assert first_content.strip() in second_content


# ===========================================================================
# Persistence — _load_persisted
# ===========================================================================


class TestLoadPersisted:
    def test_no_queue_file_starts_fresh(self, queue_file: Path):
        assert not queue_file.exists()
        svc = IntakeService(queue_path=queue_file)
        assert svc._items == {}

    def test_loads_valid_records(self, queue_file: Path):
        """Write a JSONL file manually, then construct a service to verify replay."""
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        item = _item_bug_fix_low()
        result = IntakeResult(
            item_id=item.item_id,
            status=IntakeStatus.assigned,
            assigned_lane=WorkCellLane.api_simple,
            assigned_task_id="task-123",
        )
        record = {
            "item": item.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        queue_file.write_text(json.dumps(record) + "\n")

        svc = IntakeService(queue_path=queue_file)
        assert item.item_id in svc._items
        assert svc._status_index[item.item_id] == IntakeStatus.assigned

    def test_malformed_line_skipped(self, queue_file: Path):
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        # Write one valid + one malformed line
        item = _item_bug_fix_low()
        result = IntakeResult(
            item_id=item.item_id,
            status=IntakeStatus.assigned,
            assigned_lane=WorkCellLane.api_simple,
            assigned_task_id="task-good",
        )
        valid_record = json.dumps({
            "item": item.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        })
        queue_file.write_text(valid_record + "\n" + "{BAD JSON LINE}\n")

        svc = IntakeService(queue_path=queue_file)
        assert item.item_id in svc._items
        assert svc.get_stats()["total"] == 1

    def test_blank_lines_skipped(self, queue_file: Path):
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        queue_file.write_text("\n\n\n")
        svc = IntakeService(queue_path=queue_file)
        assert svc.get_stats()["total"] == 0

    def test_source_index_rebuilt_from_file(self, queue_file: Path):
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        item = _item(source="cli")
        result = IntakeResult(
            item_id=item.item_id,
            status=IntakeStatus.assigned,
            assigned_lane=WorkCellLane.api_simple,
            assigned_task_id="task-x",
        )
        record = {
            "item": item.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        queue_file.write_text(json.dumps(record) + "\n")

        svc = IntakeService(queue_path=queue_file)
        assert svc._source_index["cli"] == 1

    def test_multiple_records_all_loaded(self, queue_file: Path):
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for i in range(5):
            item = _item(title=f"Task {i}", source="api")
            result = IntakeResult(
                item_id=item.item_id,
                status=IntakeStatus.assigned,
                assigned_lane=WorkCellLane.api_simple,
                assigned_task_id=f"task-{i}",
            )
            lines.append(json.dumps({
                "item": item.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }))
        queue_file.write_text("\n".join(lines) + "\n")

        svc = IntakeService(queue_path=queue_file)
        assert svc.get_stats()["total"] == 5


# ===========================================================================
# Event emission — _emit_event
# ===========================================================================


class TestEmitEvent:
    def test_accepted_submission_emits_event(self, svc: IntakeService):
        with patch.object(svc, "_emit_event") as mock_emit:
            svc.submit(_item_bug_fix_low())
            mock_emit.assert_called_once()
            args = mock_emit.call_args[0]
            assert args[0] == "task.intake.accepted"

    def test_rejected_submission_emits_event(self, svc: IntakeService):
        # Fill deployment lane
        svc.submit(_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        with patch.object(svc, "_emit_event") as mock_emit:
            svc.submit(_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
            mock_emit.assert_called_once()
            args = mock_emit.call_args[0]
            assert args[0] == "task.intake.rejected"

    def test_emit_event_exception_does_not_propagate(self, svc: IntakeService):
        """_emit_event's internal try/except swallows emission failures.

        get_event_emitter is imported lazily inside _emit_event, so we patch
        it in the event_emitter module (the source of truth for that import).
        """
        mock_emitter = MagicMock()
        mock_emitter.emit.side_effect = RuntimeError("SSE broker down")
        with patch(
            "forge_harness.webhook_server.services.event_emitter.get_event_emitter",
            return_value=mock_emitter,
        ):
            result = svc.submit(_item_bug_fix_low())
        # Result is still valid despite SSE failure
        assert result is not None
        assert result.status == IntakeStatus.assigned

    def test_emit_event_calls_emitter_with_correct_payload(self, svc: IntakeService):
        """Verify _emit_event invokes emitter.emit with item_id in payload."""
        mock_emitter = MagicMock()
        # Patch get_event_emitter where it is defined (lazy import inside _emit_event)
        with patch(
            "forge_harness.webhook_server.services.event_emitter.get_event_emitter",
            return_value=mock_emitter,
        ):
            item = _item_bug_fix_low()
            svc.submit(item)

        # emit should have been called with the payload dict as second positional arg
        mock_emitter.emit.assert_called()
        positional_args = mock_emitter.emit.call_args[0]
        # positional_args[1] is the payload dict
        assert len(positional_args) >= 2
        payload = positional_args[1]
        assert payload.get("item_id") == item.item_id

    def test_emit_event_handles_exception_from_get_event_emitter(self, svc: IntakeService):
        """If get_event_emitter itself raises, _emit_event catches and logs."""
        with patch(
            "forge_harness.webhook_server.services.event_emitter.get_event_emitter",
            side_effect=RuntimeError("service unavailable"),
        ):
            result = svc.submit(_item_bug_fix_low())
        # Should still return a valid result
        assert result.status == IntakeStatus.assigned


# ===========================================================================
# Singleton
# ===========================================================================


class TestSingleton:
    def test_returns_same_instance(self, queue_file: Path):
        s1 = get_intake_service(queue_path=queue_file)
        s2 = get_intake_service()
        assert s1 is s2

    def test_reset_creates_new_instance(self, queue_file: Path):
        s1 = get_intake_service(queue_path=queue_file)
        reset_intake_service()
        s2 = get_intake_service(queue_path=queue_file)
        assert s1 is not s2

    def test_params_ignored_on_subsequent_calls(self, tmp_path: Path):
        path1 = tmp_path / "a.jsonl"
        path2 = tmp_path / "b.jsonl"
        s1 = get_intake_service(queue_path=path1)
        s2 = get_intake_service(queue_path=path2)
        assert s1 is s2
        assert s1._queue_path == path1

    def test_reset_clears_singleton_module_variable(self, queue_file: Path):
        import forge_harness.webhook_server.services.intake_service as mod
        get_intake_service(queue_path=queue_file)
        assert mod._intake_service_instance is not None
        reset_intake_service()
        assert mod._intake_service_instance is None

    def test_thread_safe_singleton_creation(self, queue_file: Path):
        instances: list[IntakeService] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def getter():
            try:
                svc = get_intake_service(queue_path=queue_file)
                with lock:
                    instances.append(svc)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is instances[0] for inst in instances)


# ===========================================================================
# Thread safety — concurrent submit
# ===========================================================================


class TestConcurrency:
    def test_concurrent_submissions_no_exception(self, svc: IntakeService):
        errors: list[Exception] = []

        def submit_worker():
            try:
                svc.submit(_item_bug_fix_low())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=submit_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_submissions_consistent_total(self, svc: IntakeService):
        n = 20
        threads = [
            threading.Thread(target=svc.submit, args=(_item_bug_fix_low(),))
            for _ in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = svc.get_stats()
        assert stats["total"] == n

    def test_concurrent_list_pending_no_exception(self, svc: IntakeService):
        # Pre-populate some pending items
        for i in range(5):
            item = _item(title=f"P{i}")
            svc._items[item.item_id] = item
            svc._status_index[item.item_id] = IntakeStatus.pending

        errors: list[Exception] = []

        def lister():
            try:
                svc.list_pending()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=lister) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_get_stats_no_exception(self, svc: IntakeService):
        for _ in range(5):
            svc.submit(_item_bug_fix_low())
        errors: list[Exception] = []

        def statter():
            try:
                svc.get_stats()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=statter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# ===========================================================================
# Integration — round-trip: submit → persist → reload → verify
# ===========================================================================


class TestRoundTrip:
    def test_submit_then_reload_from_file(self, queue_file: Path):
        """Submit items, then create a new service pointing to the same file."""
        svc1 = IntakeService(queue_path=queue_file)
        item1 = _item(title="Task A", source="api")
        item2 = _item(title="Task B", source="cli")
        svc1.submit(item1)
        svc1.submit(item2)

        # Construct a new service — it replays the JSONL file
        svc2 = IntakeService(queue_path=queue_file)
        assert svc2.get_stats()["total"] == 2
        assert item1.item_id in svc2._items
        assert item2.item_id in svc2._items

    def test_source_index_survives_reload(self, queue_file: Path):
        svc1 = IntakeService(queue_path=queue_file)
        svc1.submit(_item(source="api"))
        svc1.submit(_item(source="api"))
        svc1.submit(_item(source="cli"))

        svc2 = IntakeService(queue_path=queue_file)
        assert svc2._source_index["api"] == 2
        assert svc2._source_index["cli"] == 1

    def test_all_task_types_accepted_when_capacity_available(self, svc: IntakeService):
        """Each task type with low risk should be accepted (below WIP limits)."""
        for task_type in [
            TaskType.bug_fix,
            TaskType.test_writing,
            TaskType.docs_update,
            TaskType.code_refactor,
            TaskType.content_generation,
        ]:
            result = svc.submit(_item(task_type=task_type, risk_tier=RiskTier.low))
            assert result.status == IntakeStatus.assigned, (
                f"Expected assigned for {task_type}, got {result.status}"
            )

    def test_all_sources_accepted(self, svc: IntakeService):
        for src in sorted(VALID_SOURCES):
            result = svc.submit(_item(
                title=f"From {src}",
                source=src,
            ))
            assert result.status == IntakeStatus.assigned

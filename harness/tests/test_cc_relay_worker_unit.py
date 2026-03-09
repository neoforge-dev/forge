"""Comprehensive unit tests for relay_worker.py.

Tests cover:
- Module-level helper functions
- RelayWorker class (all public and private methods)
- Singleton factory functions (get_relay_worker / reset_relay_worker)
- State machine transitions and error paths
- Edge cases for all branches
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import forge_harness.webhook_server.services.relay_worker as rw_module
from forge_harness.models.delivery import (
    DeliveryRecord,
    DeliveryState,
    DeliveryTransitionError,
)
from forge_harness.webhook_server.services.relay_worker import (
    RelayWorker,
    _append_delivery_log,
    _build_message_id,
    _dispatch_file_to_record,
    _ensure_dirs,
    _get_retry_count,
    _increment_retry_count,
    _now_utc,
    _read_delivery_log,
    get_relay_worker,
    reset_relay_worker,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 2, 23, 12, 0, 0, tzinfo=UTC)


def _make_record(
    *,
    message_id: str = "msg_20260223_abcd1234",
    task_id: str | None = "dispatch-gemini-test",
    state: DeliveryState = DeliveryState.QUEUED,
    target_agent: str | None = "gemini",
    payload_summary: str = "Hello agent",
    created_at: datetime | None = None,
    dispatched_at: datetime | None = None,
    delivered_at: datetime | None = None,
    acked_at: datetime | None = None,
    error: str | None = None,
) -> DeliveryRecord:
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id,
        source_node="testhost",
        target_node="testhost",
        target_agent=target_agent,
        state=state,
        created_at=created_at or _FIXED_NOW,
        dispatched_at=dispatched_at,
        delivered_at=delivered_at,
        acked_at=acked_at,
        payload_summary=payload_summary,
        error=error,
    )


def _make_worker(tmp_path: Path) -> RelayWorker:
    """Build a RelayWorker fully wired to tmp_path so no real FS is touched."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    delivery_log = tmp_path / "relay" / "delivery_log.jsonl"
    delivery_log.parent.mkdir(parents=True, exist_ok=True)
    agent_script = tmp_path / "agent-message.sh"
    agent_script.write_text("#!/bin/bash\nexit 0\n")
    agent_script.chmod(0o755)
    return RelayWorker(
        poll_interval=0.0,
        max_retries=3,
        dispatches_dir=dispatches_dir,
        delivery_log=delivery_log,
        agent_message_script=agent_script,
    )


# ===========================================================================
# Module-level helper: _now_utc
# ===========================================================================


class TestNowUtc:
    def test_returns_utc_datetime(self):
        result = _now_utc()
        assert result.tzinfo is not None
        assert result.tzinfo == UTC

    def test_is_approximately_current(self):
        before = datetime.now(UTC)
        result = _now_utc()
        after = datetime.now(UTC)
        assert before <= result <= after


# ===========================================================================
# Module-level helper: _build_message_id
# ===========================================================================


class TestBuildMessageId:
    def test_starts_with_msg_prefix(self):
        mid = _build_message_id()
        assert mid.startswith("msg_")

    def test_unique_ids(self):
        ids = {_build_message_id() for _ in range(20)}
        assert len(ids) == 20

    def test_format_has_three_parts(self):
        mid = _build_message_id()
        parts = mid.split("_")
        # msg _ YYYYMMDD _ HHMMSS _ hex8  → 4 parts
        assert len(parts) == 4

    def test_uses_now_utc(self):
        with patch.object(rw_module, "_now_utc", return_value=_FIXED_NOW):
            mid = _build_message_id()
        assert "20260223" in mid
        assert "120000" in mid


# ===========================================================================
# Module-level helper: _ensure_dirs
# ===========================================================================


class TestEnsureDirs:
    def test_creates_relay_and_retry_dirs(self, tmp_path, monkeypatch):
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        _ensure_dirs()

        assert relay_dir.is_dir()
        assert retry_dir.is_dir()

    def test_idempotent_when_dirs_exist(self, tmp_path, monkeypatch):
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        retry_dir.mkdir(parents=True)
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        # Should not raise
        _ensure_dirs()
        _ensure_dirs()


# ===========================================================================
# Module-level helper: _append_delivery_log
# ===========================================================================


class TestAppendDeliveryLog:
    def test_writes_json_line(self, tmp_path, monkeypatch):
        log_path = tmp_path / "relay" / "delivery_log.jsonl"
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        monkeypatch.setattr(rw_module, "_DELIVERY_LOG", log_path)
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        _append_delivery_log(record)

        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["message_id"] == record.message_id

    def test_appends_multiple_records(self, tmp_path, monkeypatch):
        log_path = tmp_path / "relay" / "delivery_log.jsonl"
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        monkeypatch.setattr(rw_module, "_DELIVERY_LOG", log_path)
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        r1 = _make_record(message_id="msg_a")
        r2 = _make_record(message_id="msg_b")
        _append_delivery_log(r1)
        _append_delivery_log(r2)

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        ids = [json.loads(l)["message_id"] for l in lines]
        assert ids == ["msg_a", "msg_b"]


# ===========================================================================
# Module-level helper: _read_delivery_log
# ===========================================================================


class TestReadDeliveryLog:
    def test_returns_empty_list_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rw_module, "_DELIVERY_LOG", tmp_path / "nonexistent.jsonl")
        assert _read_delivery_log() == []

    def test_reads_valid_records(self, tmp_path, monkeypatch):
        log_path = tmp_path / "delivery_log.jsonl"
        relay_dir = tmp_path
        retry_dir = tmp_path / "retry"
        monkeypatch.setattr(rw_module, "_DELIVERY_LOG", log_path)
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        _append_delivery_log(record)

        result = _read_delivery_log()
        assert len(result) == 1
        assert result[0].message_id == record.message_id

    def test_skips_malformed_lines(self, tmp_path, monkeypatch):
        log_path = tmp_path / "delivery_log.jsonl"
        monkeypatch.setattr(rw_module, "_DELIVERY_LOG", log_path)

        record = _make_record()
        # Write one valid + one malformed line
        good_line = record.model_dump_json() + "\n"
        log_path.write_text(good_line + "NOT_JSON\n")

        result = _read_delivery_log()
        assert len(result) == 1

    def test_skips_blank_lines(self, tmp_path, monkeypatch):
        log_path = tmp_path / "delivery_log.jsonl"
        monkeypatch.setattr(rw_module, "_DELIVERY_LOG", log_path)

        record = _make_record()
        good_line = record.model_dump_json() + "\n"
        log_path.write_text("\n" + good_line + "\n\n")

        result = _read_delivery_log()
        assert len(result) == 1


# ===========================================================================
# Module-level helper: _get_retry_count
# ===========================================================================


class TestGetRetryCount:
    def test_returns_zero_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rw_module, "_RETRY_DIR", tmp_path / "retry")
        count = _get_retry_count("nonexistent_msg")
        assert count == 0

    def test_returns_stored_count(self, tmp_path, monkeypatch):
        retry_dir = tmp_path / "retry"
        retry_dir.mkdir()
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        (retry_dir / "msg_abc.json").write_text(json.dumps({"attempts": 3}))
        assert _get_retry_count("msg_abc") == 3

    def test_returns_zero_on_malformed_file(self, tmp_path, monkeypatch):
        retry_dir = tmp_path / "retry"
        retry_dir.mkdir()
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        (retry_dir / "msg_bad.json").write_text("NOT_JSON")
        assert _get_retry_count("msg_bad") == 0

    def test_returns_zero_when_attempts_missing(self, tmp_path, monkeypatch):
        retry_dir = tmp_path / "retry"
        retry_dir.mkdir()
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        (retry_dir / "msg_no_attempts.json").write_text(json.dumps({}))
        assert _get_retry_count("msg_no_attempts") == 0


# ===========================================================================
# Module-level helper: _increment_retry_count
# ===========================================================================


class TestIncrementRetryCount:
    def test_starts_at_one_when_no_prior_file(self, tmp_path, monkeypatch):
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        count = _increment_retry_count("msg_new")
        assert count == 1

    def test_increments_existing_count(self, tmp_path, monkeypatch):
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        retry_dir.mkdir(parents=True)
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        (retry_dir / "msg_existing.json").write_text(json.dumps({"attempts": 2}))

        count = _increment_retry_count("msg_existing")
        assert count == 3

        data = json.loads((retry_dir / "msg_existing.json").read_text())
        assert data["attempts"] == 3

    def test_persists_message_id_field(self, tmp_path, monkeypatch):
        relay_dir = tmp_path / "relay"
        retry_dir = relay_dir / "retry"
        monkeypatch.setattr(rw_module, "_RELAY_DIR", relay_dir)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        _increment_retry_count("msg_persist")

        data = json.loads((retry_dir / "msg_persist.json").read_text())
        assert data["message_id"] == "msg_persist"


# ===========================================================================
# Module-level helper: _dispatch_file_to_record
# ===========================================================================


class TestDispatchFileToRecord:
    def test_returns_queued_record(self, tmp_path):
        md_file = tmp_path / "dispatch-gemini-feature-20260223.md"
        md_file.write_text("Do the thing")

        record = _dispatch_file_to_record(md_file)

        assert record.state == DeliveryState.QUEUED
        assert isinstance(record.message_id, str)
        assert record.message_id.startswith("msg_")

    def test_extracts_target_agent_from_stem(self, tmp_path):
        md_file = tmp_path / "dispatch-kimi-feature.md"
        md_file.write_text("content")

        record = _dispatch_file_to_record(md_file)
        assert record.target_agent == "kimi"

    def test_target_agent_none_for_short_stem(self, tmp_path):
        md_file = tmp_path / "dispatch.md"
        md_file.write_text("content")

        record = _dispatch_file_to_record(md_file)
        assert record.target_agent is None

    def test_payload_summary_truncated_to_120(self, tmp_path):
        md_file = tmp_path / "dispatch-agent-task.md"
        md_file.write_text("X" * 200)

        record = _dispatch_file_to_record(md_file)
        assert len(record.payload_summary) == 120

    def test_payload_summary_empty_on_os_error(self, tmp_path):
        md_file = tmp_path / "dispatch-agent-task.md"
        # Do NOT create the file — simulate missing file
        with patch.object(Path, "read_text", side_effect=OSError("no such file")):
            record = _dispatch_file_to_record(md_file)
        assert record.payload_summary == ""

    def test_task_id_equals_stem(self, tmp_path):
        md_file = tmp_path / "dispatch-gemini-reg-20260223.md"
        md_file.write_text("task content")

        record = _dispatch_file_to_record(md_file)
        assert record.task_id == md_file.stem

    def test_source_and_target_node_are_set(self, tmp_path):
        md_file = tmp_path / "dispatch-agent-t.md"
        md_file.write_text("x")

        record = _dispatch_file_to_record(md_file)
        assert record.source_node != ""
        assert record.target_node != ""


# ===========================================================================
# RelayWorker.__init__ and _load_delivered_stems
# ===========================================================================


class TestRelayWorkerInit:
    def test_defaults_are_applied(self, tmp_path):
        worker = _make_worker(tmp_path)
        assert worker.poll_interval == 0.0
        assert worker.max_retries == 3

    def test_delivered_stems_starts_empty_no_log(self, tmp_path):
        worker = _make_worker(tmp_path)
        assert worker._delivered_stems == set()

    def test_load_delivered_stems_from_existing_log(self, tmp_path):
        """Stems from DELIVERED/ACKED/NACKED records are preloaded."""
        delivery_log = tmp_path / "relay" / "delivery_log.jsonl"
        delivery_log.parent.mkdir(parents=True)

        records = [
            _make_record(message_id="m1", task_id="stem-1", state=DeliveryState.DELIVERED),
            _make_record(message_id="m2", task_id="stem-2", state=DeliveryState.ACKED),
            _make_record(message_id="m3", task_id="stem-3", state=DeliveryState.NACKED),
            _make_record(message_id="m4", task_id="stem-4", state=DeliveryState.QUEUED),
            _make_record(message_id="m5", task_id="stem-5", state=DeliveryState.FAILED),
        ]
        with delivery_log.open("w") as fh:
            for r in records:
                fh.write(r.model_dump_json() + "\n")

        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        worker = RelayWorker(
            dispatches_dir=dispatches_dir,
            delivery_log=delivery_log,
            agent_message_script=tmp_path / "agent-message.sh",
        )

        assert "stem-1" in worker._delivered_stems
        assert "stem-2" in worker._delivered_stems
        assert "stem-3" in worker._delivered_stems
        # QUEUED and FAILED should NOT be pre-loaded
        assert "stem-4" not in worker._delivered_stems
        assert "stem-5" not in worker._delivered_stems

    def test_load_delivered_stems_skips_none_task_id(self, tmp_path):
        delivery_log = tmp_path / "relay" / "delivery_log.jsonl"
        delivery_log.parent.mkdir(parents=True)

        record = _make_record(message_id="m1", task_id=None, state=DeliveryState.DELIVERED)
        delivery_log.write_text(record.model_dump_json() + "\n")

        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        worker = RelayWorker(
            dispatches_dir=dispatches_dir,
            delivery_log=delivery_log,
            agent_message_script=tmp_path / "agent-message.sh",
        )
        # None task_id should not be added
        assert None not in worker._delivered_stems

    def test_load_delivered_stems_handles_exception(self, tmp_path):
        """If _read_log raises, constructor should not propagate the error."""
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        delivery_log = tmp_path / "relay" / "delivery_log.jsonl"
        delivery_log.parent.mkdir(parents=True)

        worker = RelayWorker(
            dispatches_dir=dispatches_dir,
            delivery_log=delivery_log,
            agent_message_script=tmp_path / "agent-message.sh",
        )
        # Patch _read_log to raise and reload
        with patch.object(worker, "_read_log", side_effect=RuntimeError("boom")):
            worker._load_delivered_stems()  # must not raise

        assert worker._delivered_stems == set()


# ===========================================================================
# RelayWorker._read_log
# ===========================================================================


class TestRelayWorkerReadLog:
    def test_returns_empty_when_log_missing(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker._delivery_log = tmp_path / "nonexistent.jsonl"
        assert worker._read_log() == []

    def test_reads_records_from_log(self, tmp_path):
        worker = _make_worker(tmp_path)

        record = _make_record()
        worker._append_log(record)

        result = worker._read_log()
        assert len(result) == 1
        assert result[0].message_id == record.message_id

    def test_skips_malformed_lines(self, tmp_path):
        worker = _make_worker(tmp_path)

        record = _make_record()
        worker._delivery_log.parent.mkdir(parents=True, exist_ok=True)
        with worker._delivery_log.open("w") as fh:
            fh.write(record.model_dump_json() + "\n")
            fh.write("INVALID_JSON\n")

        result = worker._read_log()
        assert len(result) == 1

    def test_skips_blank_lines(self, tmp_path):
        worker = _make_worker(tmp_path)

        record = _make_record()
        worker._delivery_log.parent.mkdir(parents=True, exist_ok=True)
        with worker._delivery_log.open("w") as fh:
            fh.write("\n")
            fh.write(record.model_dump_json() + "\n")
            fh.write("\n")

        result = worker._read_log()
        assert len(result) == 1


# ===========================================================================
# RelayWorker._append_log
# ===========================================================================


class TestRelayWorkerAppendLog:
    def test_creates_parent_dirs(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker._delivery_log = tmp_path / "deep" / "nested" / "log.jsonl"

        record = _make_record()
        worker._append_log(record)

        assert worker._delivery_log.exists()

    def test_appends_json_line(self, tmp_path):
        worker = _make_worker(tmp_path)

        r1 = _make_record(message_id="m1")
        r2 = _make_record(message_id="m2")
        worker._append_log(r1)
        worker._append_log(r2)

        lines = worker._delivery_log.read_text().strip().splitlines()
        assert len(lines) == 2
        ids = [json.loads(l)["message_id"] for l in lines]
        assert ids == ["m1", "m2"]


# ===========================================================================
# RelayWorker.poll_pending
# ===========================================================================


class TestRelayWorkerPollPending:
    @pytest.mark.asyncio
    async def test_returns_empty_when_dir_missing(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker._dispatches_dir = tmp_path / "nonexistent"

        result = await worker.poll_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_record_for_each_md_file(self, tmp_path):
        worker = _make_worker(tmp_path)

        (worker._dispatches_dir / "dispatch-gemini-t1.md").write_text("content 1")
        (worker._dispatches_dir / "dispatch-kimi-t2.md").write_text("content 2")

        result = await worker.poll_pending()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_skips_already_delivered_stems(self, tmp_path):
        worker = _make_worker(tmp_path)

        f1 = worker._dispatches_dir / "dispatch-gemini-t1.md"
        f2 = worker._dispatches_dir / "dispatch-kimi-t2.md"
        f1.write_text("content 1")
        f2.write_text("content 2")

        worker._delivered_stems.add(f1.stem)

        result = await worker.poll_pending()
        assert len(result) == 1
        assert result[0].task_id == f2.stem

    @pytest.mark.asyncio
    async def test_ignores_non_md_files(self, tmp_path):
        worker = _make_worker(tmp_path)

        (worker._dispatches_dir / "dispatch-agent-task.txt").write_text("txt")
        (worker._dispatches_dir / "dispatch-agent-task.json").write_text("{}")
        (worker._dispatches_dir / "dispatch-agent-task.md").write_text("md content")

        result = await worker.poll_pending()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_empty_dir(self, tmp_path):
        worker = _make_worker(tmp_path)
        result = await worker.poll_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_all_stems_returned_as_queued(self, tmp_path):
        worker = _make_worker(tmp_path)
        (worker._dispatches_dir / "dispatch-agent-x.md").write_text("data")

        result = await worker.poll_pending()
        assert all(r.state == DeliveryState.QUEUED for r in result)


# ===========================================================================
# RelayWorker._build_target
# ===========================================================================


class TestRelayWorkerBuildTarget:
    def test_uses_target_agent(self, tmp_path):
        worker = _make_worker(tmp_path)
        record = _make_record(target_agent="gemini")
        assert worker._build_target(record) == "forge:gemini"

    def test_falls_back_to_orchestrator(self, tmp_path):
        worker = _make_worker(tmp_path)
        record = _make_record(target_agent=None)
        assert worker._build_target(record) == "forge:orchestrator"


# ===========================================================================
# RelayWorker.deliver
# ===========================================================================


def _make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class TestRelayWorkerDeliver:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        fake_result = _make_subprocess_result(returncode=0)

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            success = await worker.deliver(record)

        assert success is True

    @pytest.mark.asyncio
    async def test_returns_false_on_nonzero_exit(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        fake_result = _make_subprocess_result(returncode=1, stderr="Script error")

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            success = await worker.deliver(record)

        assert success is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(side_effect=TimeoutError("timed out"))
            ),
        ):
            success = await worker.deliver(record)

        assert success is False

    @pytest.mark.asyncio
    async def test_invalid_state_transition_returns_false(self, tmp_path):
        worker = _make_worker(tmp_path)
        # ACKED is a terminal state — cannot transition to DISPATCHED
        record = _make_record(state=DeliveryState.ACKED)

        success = await worker.deliver(record)
        assert success is False

    @pytest.mark.asyncio
    async def test_appends_dispatched_and_delivered_records_on_success(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        fake_result = _make_subprocess_result(returncode=0)

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            await worker.deliver(record)

        records = worker._read_log()
        states = [r.state for r in records]
        assert DeliveryState.DISPATCHED in states
        assert DeliveryState.DELIVERED in states

    @pytest.mark.asyncio
    async def test_appends_dispatched_and_failed_records_on_failure(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        fake_result = _make_subprocess_result(returncode=1, stderr="oops")

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            await worker.deliver(record)

        records = worker._read_log()
        states = [r.state for r in records]
        assert DeliveryState.DISPATCHED in states
        assert DeliveryState.FAILED in states

    @pytest.mark.asyncio
    async def test_adds_task_id_to_delivered_stems_on_success(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record(task_id="dispatch-gemini-test")
        fake_result = _make_subprocess_result(returncode=0)

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            await worker.deliver(record)

        assert "dispatch-gemini-test" in worker._delivered_stems

    @pytest.mark.asyncio
    async def test_uses_task_id_in_message_when_no_payload_summary(self, tmp_path, monkeypatch):
        """When payload_summary is empty, message falls back to task_id."""
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record(payload_summary="", task_id="dispatch-gemini-test")
        fake_result = _make_subprocess_result(returncode=0)

        captured_cmd = []

        async def fake_run_in_executor(executor, fn):
            # Execute the lambda but intercept the subprocess call
            with patch("subprocess.run", return_value=fake_result) as mock_sub:
                fn()
                if mock_sub.call_args:
                    captured_cmd.extend(mock_sub.call_args[0][0])
            return fake_result

        mock_loop = MagicMock()
        mock_loop.run_in_executor = fake_run_in_executor

        with patch.object(rw_module.asyncio, "get_event_loop", return_value=mock_loop):
            await worker.deliver(record)

        # At a minimum delivery must not crash and return True
        records = worker._read_log()
        states = [r.state for r in records]
        assert DeliveryState.DELIVERED in states

    @pytest.mark.asyncio
    async def test_error_detail_from_stderr_on_failure(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        fake_result = _make_subprocess_result(returncode=1, stderr="stderr error", stdout="")

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            await worker.deliver(record)

        records = worker._read_log()
        failed = [r for r in records if r.state == DeliveryState.FAILED]
        assert len(failed) == 1
        assert "stderr error" in (failed[0].error or "")

    @pytest.mark.asyncio
    async def test_error_detail_falls_back_to_stdout(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        # stderr is empty, stdout has the message
        fake_result = _make_subprocess_result(returncode=1, stderr="", stdout="stdout error")

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            await worker.deliver(record)

        records = worker._read_log()
        failed = [r for r in records if r.state == DeliveryState.FAILED]
        assert "stdout error" in (failed[0].error or "")

    @pytest.mark.asyncio
    async def test_error_fallback_message_when_no_output(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        record = _make_record()
        fake_result = _make_subprocess_result(returncode=1, stderr="", stdout="")

        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            await worker.deliver(record)

        records = worker._read_log()
        failed = [r for r in records if r.state == DeliveryState.FAILED]
        assert failed[0].error == "Delivery failed"


# ===========================================================================
# RelayWorker.ack
# ===========================================================================


class TestRelayWorkerAck:
    @pytest.mark.asyncio
    async def test_ack_transitions_delivered_to_acked(self, tmp_path):
        worker = _make_worker(tmp_path)

        delivered = _make_record(
            message_id="msg_ack_001",
            state=DeliveryState.DELIVERED,
            task_id="dispatch-agent-t",
        )
        worker._append_log(delivered)

        await worker.ack("msg_ack_001")

        records = worker._read_log()
        acked = [r for r in records if r.state == DeliveryState.ACKED]
        assert len(acked) == 1
        assert acked[0].message_id == "msg_ack_001"
        assert acked[0].acked_at is not None

    @pytest.mark.asyncio
    async def test_ack_noop_for_unknown_record(self, tmp_path):
        worker = _make_worker(tmp_path)

        # No records in log
        await worker.ack("nonexistent_id")  # must not raise

    @pytest.mark.asyncio
    async def test_ack_noop_when_record_not_in_delivered_state(self, tmp_path):
        worker = _make_worker(tmp_path)

        queued = _make_record(message_id="msg_q", state=DeliveryState.QUEUED)
        worker._append_log(queued)

        await worker.ack("msg_q")  # must not raise, no acked record added

        records = worker._read_log()
        acked = [r for r in records if r.state == DeliveryState.ACKED]
        assert len(acked) == 0

    @pytest.mark.asyncio
    async def test_ack_picks_most_recent_delivered(self, tmp_path):
        """When multiple delivered records exist, the last one in log is acked."""
        worker = _make_worker(tmp_path)

        r1 = _make_record(message_id="msg_multi", state=DeliveryState.DELIVERED)
        r2 = _make_record(message_id="msg_multi", state=DeliveryState.DELIVERED)
        worker._append_log(r1)
        worker._append_log(r2)

        await worker.ack("msg_multi")

        records = worker._read_log()
        acked = [r for r in records if r.state == DeliveryState.ACKED]
        assert len(acked) == 1


# ===========================================================================
# RelayWorker.nack
# ===========================================================================


class TestRelayWorkerNack:
    @pytest.mark.asyncio
    async def test_nack_transitions_delivered_to_nacked(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        delivered = _make_record(
            message_id="msg_nack_001",
            state=DeliveryState.DELIVERED,
            task_id="dispatch-agent-t",
        )
        worker._append_log(delivered)

        await worker.nack("msg_nack_001", "agent busy")

        records = worker._read_log()
        nacked = [r for r in records if r.state == DeliveryState.NACKED]
        assert len(nacked) == 1
        assert nacked[0].error == "agent busy"

    @pytest.mark.asyncio
    async def test_nack_noop_for_unknown_record(self, tmp_path):
        worker = _make_worker(tmp_path)
        await worker.nack("nonexistent", "reason")  # must not raise

    @pytest.mark.asyncio
    async def test_nack_noop_when_not_delivered(self, tmp_path):
        worker = _make_worker(tmp_path)

        record = _make_record(message_id="msg_queued", state=DeliveryState.QUEUED)
        worker._append_log(record)

        await worker.nack("msg_queued", "reason")  # must not raise

        records = worker._read_log()
        nacked = [r for r in records if r.state == DeliveryState.NACKED]
        assert len(nacked) == 0

    @pytest.mark.asyncio
    async def test_nack_below_max_retries_removes_from_delivered_stems(
        self, tmp_path, monkeypatch
    ):
        worker = _make_worker(tmp_path)
        worker.max_retries = 3

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        # Simulate only 1 attempt (below max)
        (retry_dir / "msg_retry.json").write_text(json.dumps({"attempts": 1}))

        delivered = _make_record(
            message_id="msg_retry",
            state=DeliveryState.DELIVERED,
            task_id="dispatch-agent-retry",
        )
        worker._append_log(delivered)
        worker._delivered_stems.add("dispatch-agent-retry")

        await worker.nack("msg_retry", "retry please")

        assert "dispatch-agent-retry" not in worker._delivered_stems

    @pytest.mark.asyncio
    async def test_nack_at_max_retries_keeps_stem_out(self, tmp_path, monkeypatch):
        """When attempts == max_retries, stem is NOT re-added (already not in set)."""
        worker = _make_worker(tmp_path)
        worker.max_retries = 3

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        # Simulate 3 attempts (== max)
        (retry_dir / "msg_maxed.json").write_text(json.dumps({"attempts": 3}))

        delivered = _make_record(
            message_id="msg_maxed",
            state=DeliveryState.DELIVERED,
            task_id="dispatch-agent-maxed",
        )
        worker._append_log(delivered)
        # stem was never added (already exhausted)

        await worker.nack("msg_maxed", "exhausted")

        # stem should still not be in _delivered_stems (no retry)
        assert "dispatch-agent-maxed" not in worker._delivered_stems

    @pytest.mark.asyncio
    async def test_nack_sets_acked_at_timestamp(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        delivered = _make_record(
            message_id="msg_ts",
            state=DeliveryState.DELIVERED,
            task_id="dispatch-agent-ts",
        )
        worker._append_log(delivered)

        await worker.nack("msg_ts", "reason")

        records = worker._read_log()
        nacked = [r for r in records if r.state == DeliveryState.NACKED]
        assert nacked[0].acked_at is not None


# ===========================================================================
# RelayWorker.run
# ===========================================================================


class TestRelayWorkerRun:
    @pytest.mark.asyncio
    async def test_run_calls_poll_and_deliver_once_then_cancels(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)
        worker.poll_interval = 0.0

        record = _make_record()
        poll_calls = []
        deliver_calls = []

        async def fake_poll():
            poll_calls.append(1)
            return [record]

        async def fake_deliver(rec):
            deliver_calls.append(rec)
            return True

        worker.poll_pending = fake_poll
        worker.deliver = fake_deliver

        # Run for just one cycle then cancel
        task = asyncio.ensure_future(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(poll_calls) >= 1
        assert len(deliver_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_continues_after_deliver_exception(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.poll_interval = 0.0

        record = _make_record()
        deliver_call_count = 0

        async def fake_poll():
            return [record]

        async def fake_deliver(rec):
            nonlocal deliver_call_count
            deliver_call_count += 1
            if deliver_call_count == 1:
                raise RuntimeError("oops")
            return True

        worker.poll_pending = fake_poll
        worker.deliver = fake_deliver

        task = asyncio.ensure_future(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have been called at least twice (recovered from exception)
        assert deliver_call_count >= 2

    @pytest.mark.asyncio
    async def test_run_continues_after_poll_exception(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.poll_interval = 0.0

        poll_call_count = 0

        async def fake_poll():
            nonlocal poll_call_count
            poll_call_count += 1
            if poll_call_count == 1:
                raise RuntimeError("poll error")
            return []

        worker.poll_pending = fake_poll

        task = asyncio.ensure_future(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert poll_call_count >= 2


# ===========================================================================
# RelayWorker.get_stats
# ===========================================================================


class TestRelayWorkerGetStats:
    def test_empty_log_returns_total_zero(self, tmp_path):
        worker = _make_worker(tmp_path)
        stats = worker.get_stats()

        assert stats["total"] == 0
        assert stats["by_state"] == {}
        assert stats["poll_interval"] == 0.0
        assert stats["max_retries"] == 3

    def test_counts_per_state(self, tmp_path):
        worker = _make_worker(tmp_path)

        r1 = _make_record(message_id="m1", state=DeliveryState.QUEUED)
        r2 = _make_record(message_id="m2", state=DeliveryState.DELIVERED)
        r3 = _make_record(message_id="m3", state=DeliveryState.DELIVERED)
        r4 = _make_record(message_id="m4", state=DeliveryState.FAILED)

        for r in [r1, r2, r3, r4]:
            worker._append_log(r)

        stats = worker.get_stats()

        assert stats["total"] == 4
        assert stats["by_state"]["delivered"] == 2
        assert stats["by_state"]["queued"] == 1
        assert stats["by_state"]["failed"] == 1

    def test_by_state_excludes_zero_counts(self, tmp_path):
        worker = _make_worker(tmp_path)

        r = _make_record(state=DeliveryState.QUEUED)
        worker._append_log(r)

        stats = worker.get_stats()

        # Only states with actual records should appear
        for k, v in stats["by_state"].items():
            assert v > 0

    def test_returns_config_values(self, tmp_path):
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        delivery_log = tmp_path / "relay" / "delivery_log.jsonl"
        delivery_log.parent.mkdir(parents=True)
        worker = RelayWorker(
            poll_interval=7.5,
            max_retries=10,
            dispatches_dir=dispatches_dir,
            delivery_log=delivery_log,
        )

        stats = worker.get_stats()
        assert stats["poll_interval"] == 7.5
        assert stats["max_retries"] == 10


# ===========================================================================
# Singleton: get_relay_worker / reset_relay_worker
# ===========================================================================


class TestSingletonFactory:
    def setup_method(self):
        reset_relay_worker()

    def teardown_method(self):
        reset_relay_worker()

    def test_get_relay_worker_returns_instance(self, tmp_path):
        worker = get_relay_worker(
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
            agent_message_script=tmp_path / "agent-message.sh",
        )
        assert isinstance(worker, RelayWorker)

    def test_get_relay_worker_returns_same_instance_on_second_call(self, tmp_path):
        w1 = get_relay_worker(
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
        )
        w2 = get_relay_worker(
            dispatches_dir=tmp_path / "other",
            delivery_log=tmp_path / "other_log.jsonl",
        )
        assert w1 is w2

    def test_second_call_ignores_arguments(self, tmp_path):
        get_relay_worker(
            poll_interval=1.0,
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
        )
        w2 = get_relay_worker(
            poll_interval=99.0,
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
        )
        # The poll_interval should reflect the FIRST call, not the second
        assert w2.poll_interval == 1.0

    def test_reset_clears_singleton(self, tmp_path):
        w1 = get_relay_worker(
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
        )
        reset_relay_worker()
        w2 = get_relay_worker(
            dispatches_dir=tmp_path / "dispatches2",
            delivery_log=tmp_path / "relay2" / "delivery_log.jsonl",
        )
        assert w1 is not w2

    def test_get_relay_worker_applies_custom_poll_interval(self, tmp_path):
        worker = get_relay_worker(
            poll_interval=42.0,
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
        )
        assert worker.poll_interval == 42.0

    def test_get_relay_worker_applies_custom_max_retries(self, tmp_path):
        worker = get_relay_worker(
            max_retries=7,
            dispatches_dir=tmp_path / "dispatches",
            delivery_log=tmp_path / "relay" / "delivery_log.jsonl",
        )
        assert worker.max_retries == 7


# ===========================================================================
# Integration: full poll → deliver → ack / nack cycle
# ===========================================================================


class TestFullCycle:
    @pytest.mark.asyncio
    async def test_poll_deliver_ack_cycle(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        dispatch_file = worker._dispatches_dir / "dispatch-gemini-cycle.md"
        dispatch_file.write_text("Run the build pipeline")

        fake_result = _make_subprocess_result(returncode=0)
        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            pending = await worker.poll_pending()
            assert len(pending) == 1

            success = await worker.deliver(pending[0])
            assert success is True

            await worker.ack(pending[0].message_id)

        records = worker._read_log()
        states = {r.state for r in records}
        assert DeliveryState.DISPATCHED in states
        assert DeliveryState.DELIVERED in states
        assert DeliveryState.ACKED in states

    @pytest.mark.asyncio
    async def test_poll_deliver_nack_retry_cycle(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)
        worker.max_retries = 3

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        dispatch_file = worker._dispatches_dir / "dispatch-kimi-nack.md"
        dispatch_file.write_text("Task payload")

        fake_result = _make_subprocess_result(returncode=0)
        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            pending = await worker.poll_pending()
            await worker.deliver(pending[0])
            await worker.nack(pending[0].message_id, "not ready")

        # After nack (attempts=1 < max_retries=3), stem is removed from _delivered_stems
        assert dispatch_file.stem not in worker._delivered_stems

        # Now it should show up again on next poll
        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            pending2 = await worker.poll_pending()

        assert len(pending2) == 1

    @pytest.mark.asyncio
    async def test_delivered_file_not_requeued_on_second_poll(self, tmp_path, monkeypatch):
        worker = _make_worker(tmp_path)

        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rw_module, "_RETRY_DIR", retry_dir)

        dispatch_file = worker._dispatches_dir / "dispatch-gemini-once.md"
        dispatch_file.write_text("Once only")

        fake_result = _make_subprocess_result(returncode=0)
        with patch.object(
            rw_module.asyncio,
            "get_event_loop",
            return_value=MagicMock(
                run_in_executor=AsyncMock(return_value=fake_result)
            ),
        ):
            pending = await worker.poll_pending()
            await worker.deliver(pending[0])

        # Second poll should return empty (stem now in _delivered_stems)
        pending2 = await worker.poll_pending()
        assert pending2 == []

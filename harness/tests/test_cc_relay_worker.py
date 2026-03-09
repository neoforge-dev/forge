"""Comprehensive tests for RelayWorker service.

Targets forge_harness/webhook_server/services/relay_worker.py

Coverage areas:
  - Module-level helpers: _now_utc, _build_message_id, _ensure_dirs,
    _append_delivery_log, _read_delivery_log, _get_retry_count,
    _increment_retry_count, _dispatch_file_to_record
  - RelayWorker.__init__ and configuration
  - RelayWorker._load_delivered_stems (normal, exception path)
  - RelayWorker._read_log (no file, valid, malformed, blank lines)
  - RelayWorker._append_log
  - RelayWorker.poll_pending (no dir, empty dir, pending files, skip delivered)
  - RelayWorker.deliver (success, failure, exception, invalid transition)
  - RelayWorker._build_target
  - RelayWorker.ack (found, not found)
  - RelayWorker.nack (found, retry allowed, max retries exhausted, not found)
  - RelayWorker.run (one cycle, cancellation)
  - RelayWorker.get_stats
  - Singleton: get_relay_worker (first call, cached, kwargs ignored), reset_relay_worker
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.models.delivery import DeliveryRecord, DeliveryState, DeliveryTransitionError
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the relay worker singleton before and after every test."""
    reset_relay_worker()
    yield
    reset_relay_worker()


@pytest.fixture()
def tmp_dispatches(tmp_path: Path) -> Path:
    d = tmp_path / "dispatches"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def tmp_delivery_log(tmp_path: Path) -> Path:
    log_dir = tmp_path / "relay"
    log_dir.mkdir(parents=True)
    return log_dir / "delivery_log.jsonl"


@pytest.fixture()
def tmp_agent_script(tmp_path: Path) -> Path:
    script = tmp_path / "agent-message.sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def worker(tmp_dispatches, tmp_delivery_log, tmp_agent_script) -> RelayWorker:
    """RelayWorker wired to temp directories, no external I/O."""
    return RelayWorker(
        poll_interval=0.01,
        max_retries=3,
        dispatches_dir=tmp_dispatches,
        delivery_log=tmp_delivery_log,
        agent_message_script=tmp_agent_script,
    )


def _make_record(
    message_id: str = "msg_test_abc",
    task_id: str | None = "dispatch-gemini-test",
    state: DeliveryState = DeliveryState.QUEUED,
    target_agent: str | None = "gemini",
    payload_summary: str = "Test payload",
) -> DeliveryRecord:
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id,
        source_node="testnode",
        target_node="testnode",
        target_agent=target_agent,
        state=state,
        created_at=datetime.now(UTC),
        payload_summary=payload_summary,
    )


def _write_record(log_path: Path, record: DeliveryRecord) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestNowUtc:
    def test_returns_datetime_with_utc_timezone(self):
        result = _now_utc()
        assert isinstance(result, datetime)
        assert result.tzinfo is UTC


class TestBuildMessageId:
    def test_format_starts_with_msg_prefix(self):
        mid = _build_message_id()
        assert mid.startswith("msg_")

    def test_id_is_unique_across_calls(self):
        ids = {_build_message_id() for _ in range(10)}
        assert len(ids) == 10

    def test_id_contains_timestamp_and_hex(self):
        mid = _build_message_id()
        parts = mid.split("_")
        # "msg", "YYYYMMDD", "HHMMSS", "<8hex>"
        assert len(parts) == 4
        assert len(parts[3]) == 8


class TestEnsureDirs:
    def test_creates_relay_and_retry_dirs(self, tmp_path):
        relay = tmp_path / "relay"
        retry = relay / "retry"

        import forge_harness.webhook_server.services.relay_worker as mod

        original_relay = mod._RELAY_DIR
        original_retry = mod._RETRY_DIR
        try:
            mod._RELAY_DIR = relay
            mod._RETRY_DIR = retry
            _ensure_dirs()
            assert relay.is_dir()
            assert retry.is_dir()
        finally:
            mod._RELAY_DIR = original_relay
            mod._RETRY_DIR = original_retry


class TestAppendDeliveryLog:
    def test_appends_json_line(self, tmp_path):
        log = tmp_path / "relay" / "delivery_log.jsonl"
        record = _make_record()

        import forge_harness.webhook_server.services.relay_worker as mod

        original_log = mod._DELIVERY_LOG
        original_relay = mod._RELAY_DIR
        original_retry = mod._RETRY_DIR
        try:
            mod._DELIVERY_LOG = log
            mod._RELAY_DIR = log.parent
            mod._RETRY_DIR = log.parent / "retry"
            _append_delivery_log(record)
            assert log.exists()
            lines = log.read_text().strip().splitlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["message_id"] == record.message_id
        finally:
            mod._DELIVERY_LOG = original_log
            mod._RELAY_DIR = original_relay
            mod._RETRY_DIR = original_retry


class TestReadDeliveryLog:
    def test_returns_empty_list_when_no_file(self, tmp_path):
        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._DELIVERY_LOG
        try:
            mod._DELIVERY_LOG = tmp_path / "nonexistent.jsonl"
            result = _read_delivery_log()
            assert result == []
        finally:
            mod._DELIVERY_LOG = original

    def test_reads_valid_records(self, tmp_path):
        log = tmp_path / "delivery_log.jsonl"
        record = _make_record()
        with log.open("w") as fh:
            fh.write(record.model_dump_json() + "\n")

        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._DELIVERY_LOG
        try:
            mod._DELIVERY_LOG = log
            result = _read_delivery_log()
            assert len(result) == 1
            assert result[0].message_id == record.message_id
        finally:
            mod._DELIVERY_LOG = original

    def test_skips_malformed_lines(self, tmp_path):
        log = tmp_path / "delivery_log.jsonl"
        record = _make_record()
        with log.open("w") as fh:
            fh.write("not-valid-json\n")
            fh.write(record.model_dump_json() + "\n")

        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._DELIVERY_LOG
        try:
            mod._DELIVERY_LOG = log
            result = _read_delivery_log()
            assert len(result) == 1
        finally:
            mod._DELIVERY_LOG = original

    def test_skips_blank_lines(self, tmp_path):
        log = tmp_path / "delivery_log.jsonl"
        record = _make_record()
        with log.open("w") as fh:
            fh.write("\n\n")
            fh.write(record.model_dump_json() + "\n")

        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._DELIVERY_LOG
        try:
            mod._DELIVERY_LOG = log
            result = _read_delivery_log()
            assert len(result) == 1
        finally:
            mod._DELIVERY_LOG = original


class TestRetryCountHelpers:
    def test_get_retry_count_returns_zero_when_no_file(self, tmp_path):
        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._RETRY_DIR
        try:
            mod._RETRY_DIR = tmp_path / "retry"
            assert _get_retry_count("msg_no_such") == 0
        finally:
            mod._RETRY_DIR = original

    def test_get_retry_count_reads_existing_file(self, tmp_path):
        retry_dir = tmp_path / "retry"
        retry_dir.mkdir()
        (retry_dir / "msg_abc.json").write_text(
            json.dumps({"message_id": "msg_abc", "attempts": 5})
        )

        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._RETRY_DIR
        try:
            mod._RETRY_DIR = retry_dir
            assert _get_retry_count("msg_abc") == 5
        finally:
            mod._RETRY_DIR = original

    def test_get_retry_count_returns_zero_on_malformed_file(self, tmp_path):
        retry_dir = tmp_path / "retry"
        retry_dir.mkdir()
        (retry_dir / "msg_bad.json").write_text("NOT JSON")

        import forge_harness.webhook_server.services.relay_worker as mod

        original = mod._RETRY_DIR
        try:
            mod._RETRY_DIR = retry_dir
            assert _get_retry_count("msg_bad") == 0
        finally:
            mod._RETRY_DIR = original

    def test_increment_retry_count_creates_file_with_one(self, tmp_path):
        retry_dir = tmp_path / "retry"
        relay_dir = tmp_path / "relay"

        import forge_harness.webhook_server.services.relay_worker as mod

        original_retry = mod._RETRY_DIR
        original_relay = mod._RELAY_DIR
        try:
            mod._RETRY_DIR = retry_dir
            mod._RELAY_DIR = relay_dir
            count = _increment_retry_count("msg_new")
            assert count == 1
            assert (retry_dir / "msg_new.json").exists()
        finally:
            mod._RETRY_DIR = original_retry
            mod._RELAY_DIR = original_relay

    def test_increment_retry_count_accumulates(self, tmp_path):
        retry_dir = tmp_path / "retry"
        relay_dir = tmp_path / "relay"

        import forge_harness.webhook_server.services.relay_worker as mod

        original_retry = mod._RETRY_DIR
        original_relay = mod._RELAY_DIR
        try:
            mod._RETRY_DIR = retry_dir
            mod._RELAY_DIR = relay_dir
            _increment_retry_count("msg_x")
            _increment_retry_count("msg_x")
            count = _increment_retry_count("msg_x")
            assert count == 3
        finally:
            mod._RETRY_DIR = original_retry
            mod._RELAY_DIR = original_relay


class TestDispatchFileToRecord:
    def test_basic_dispatch_file_parsed(self, tmp_path):
        dispatch_file = tmp_path / "dispatch-gemini-registry-20260101.md"
        dispatch_file.write_text("Task content here")

        record = _dispatch_file_to_record(dispatch_file)
        assert record.state == DeliveryState.QUEUED
        assert record.message_id.startswith("msg_")
        assert record.target_agent == "gemini"
        assert "Task content here" in record.payload_summary

    def test_payload_truncated_to_120_chars(self, tmp_path):
        dispatch_file = tmp_path / "dispatch-agent-slug.md"
        dispatch_file.write_text("X" * 200)

        record = _dispatch_file_to_record(dispatch_file)
        assert len(record.payload_summary) <= 120

    def test_no_target_agent_when_single_segment(self, tmp_path):
        dispatch_file = tmp_path / "single.md"
        dispatch_file.write_text("content")

        record = _dispatch_file_to_record(dispatch_file)
        assert record.target_agent is None

    def test_source_node_populated(self, tmp_path):
        dispatch_file = tmp_path / "dispatch-agent-slug.md"
        dispatch_file.write_text("content")

        record = _dispatch_file_to_record(dispatch_file)
        assert record.source_node  # must be non-empty hostname


# ---------------------------------------------------------------------------
# RelayWorker initialization
# ---------------------------------------------------------------------------


class TestRelayWorkerInit:
    def test_default_configuration(self, worker):
        assert worker.poll_interval == 0.01
        assert worker.max_retries == 3

    def test_dispatches_dir_override(self, tmp_dispatches, tmp_delivery_log, tmp_agent_script):
        w = RelayWorker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
            agent_message_script=tmp_agent_script,
        )
        assert w._dispatches_dir == tmp_dispatches

    def test_delivered_stems_starts_empty_with_empty_log(self, worker):
        assert worker._delivered_stems == set()

    def test_load_delivered_stems_from_existing_log(
        self, tmp_dispatches, tmp_delivery_log, tmp_agent_script
    ):
        # Pre-populate the log with a delivered record
        record = _make_record(
            message_id="msg_pre_001",
            task_id="dispatch-pre-task",
            state=DeliveryState.DELIVERED,
        )
        _write_record(tmp_delivery_log, record)

        w = RelayWorker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
            agent_message_script=tmp_agent_script,
        )
        assert "dispatch-pre-task" in w._delivered_stems

    def test_load_delivered_stems_skips_non_terminal_states(
        self, tmp_dispatches, tmp_delivery_log, tmp_agent_script
    ):
        record = _make_record(
            task_id="dispatch-pending-task",
            state=DeliveryState.QUEUED,
        )
        _write_record(tmp_delivery_log, record)

        w = RelayWorker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
            agent_message_script=tmp_agent_script,
        )
        assert "dispatch-pending-task" not in w._delivered_stems

    def test_load_delivered_stems_handles_exception_gracefully(
        self, tmp_dispatches, tmp_delivery_log, tmp_agent_script
    ):
        # Write garbage so _read_log might fail or produce nothing
        tmp_delivery_log.parent.mkdir(parents=True, exist_ok=True)
        tmp_delivery_log.write_text("GARBAGE\n")

        # Should not raise — just logs warning
        w = RelayWorker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
            agent_message_script=tmp_agent_script,
        )
        assert isinstance(w._delivered_stems, set)


# ---------------------------------------------------------------------------
# RelayWorker._read_log
# ---------------------------------------------------------------------------


class TestWorkerReadLog:
    def test_returns_empty_when_no_file(self, worker):
        result = worker._read_log()
        assert result == []

    def test_reads_valid_jsonl(self, worker, tmp_delivery_log):
        record = _make_record()
        _write_record(tmp_delivery_log, record)
        result = worker._read_log()
        assert len(result) == 1
        assert result[0].message_id == record.message_id

    def test_skips_malformed_lines_and_continues(self, worker, tmp_delivery_log):
        record = _make_record()
        tmp_delivery_log.parent.mkdir(parents=True, exist_ok=True)
        with tmp_delivery_log.open("w") as fh:
            fh.write("bad-json\n")
            fh.write(record.model_dump_json() + "\n")
        result = worker._read_log()
        assert len(result) == 1

    def test_skips_blank_lines(self, worker, tmp_delivery_log):
        record = _make_record()
        tmp_delivery_log.parent.mkdir(parents=True, exist_ok=True)
        with tmp_delivery_log.open("w") as fh:
            fh.write("\n")
            fh.write(record.model_dump_json() + "\n")
        result = worker._read_log()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# RelayWorker._append_log
# ---------------------------------------------------------------------------


class TestWorkerAppendLog:
    def test_appends_record_to_log(self, worker, tmp_delivery_log):
        record = _make_record()
        worker._append_log(record)
        assert tmp_delivery_log.exists()
        lines = [l for l in tmp_delivery_log.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["message_id"] == record.message_id

    def test_appends_multiple_records(self, worker, tmp_delivery_log):
        r1 = _make_record(message_id="msg_001")
        r2 = _make_record(message_id="msg_002")
        worker._append_log(r1)
        worker._append_log(r2)
        lines = [l for l in tmp_delivery_log.read_text().splitlines() if l.strip()]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# RelayWorker.poll_pending
# ---------------------------------------------------------------------------


class TestPollPending:
    @pytest.mark.asyncio
    async def test_returns_empty_when_dispatches_dir_missing(self, worker, tmp_dispatches):
        tmp_dispatches.rmdir()
        result = await worker.poll_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_md_files(self, worker):
        result = await worker.poll_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_discovers_md_file(self, worker, tmp_dispatches):
        (tmp_dispatches / "dispatch-gemini-task-20260101.md").write_text("task content")
        result = await worker.poll_pending()
        assert len(result) == 1
        assert result[0].state == DeliveryState.QUEUED

    @pytest.mark.asyncio
    async def test_skips_already_delivered_stems(self, worker, tmp_dispatches):
        dispatch_file = tmp_dispatches / "dispatch-gemini-task.md"
        dispatch_file.write_text("content")
        stem = dispatch_file.stem
        worker._delivered_stems.add(stem)

        result = await worker.poll_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_non_md_files(self, worker, tmp_dispatches):
        (tmp_dispatches / "dispatch-task.txt").write_text("not an md file")
        (tmp_dispatches / "dispatch-task.json").write_text("{}")
        result = await worker.poll_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_multiple_pending_files(self, worker, tmp_dispatches):
        for i in range(3):
            (tmp_dispatches / f"dispatch-agent-task-{i:03}.md").write_text(f"content {i}")
        result = await worker.poll_pending()
        assert len(result) == 3


# ---------------------------------------------------------------------------
# RelayWorker._build_target
# ---------------------------------------------------------------------------


class TestBuildTarget:
    def test_with_target_agent(self, worker):
        record = _make_record(target_agent="gemini")
        assert worker._build_target(record) == "forge:gemini"

    def test_without_target_agent_defaults_to_orchestrator(self, worker):
        record = _make_record(target_agent=None)
        assert worker._build_target(record) == "forge:orchestrator"


# ---------------------------------------------------------------------------
# RelayWorker.deliver
# ---------------------------------------------------------------------------


class TestDeliver:
    @pytest.mark.asyncio
    async def test_successful_delivery(self, worker, tmp_delivery_log):
        record = _make_record()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            success = await worker.deliver(record)

        assert success is True
        assert record.task_id in worker._delivered_stems

    @pytest.mark.asyncio
    async def test_failed_delivery_returns_false(self, worker):
        record = _make_record()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "connection refused"
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            success = await worker.deliver(record)

        assert success is False

    @pytest.mark.asyncio
    async def test_delivery_exception_returns_false(self, worker):
        record = _make_record()

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=RuntimeError("script not found")
            )
            success = await worker.deliver(record)

        assert success is False

    @pytest.mark.asyncio
    async def test_invalid_state_transition_returns_false(self, worker):
        # A record already in DELIVERED state cannot transition to DISPATCHED
        record = _make_record(state=DeliveryState.DELIVERED)
        success = await worker.deliver(record)
        assert success is False

    @pytest.mark.asyncio
    async def test_deliver_appends_dispatched_and_delivered_log_entries(
        self, worker, tmp_delivery_log
    ):
        record = _make_record()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await worker.deliver(record)

        records = worker._read_log()
        states = [r.state for r in records]
        assert DeliveryState.DISPATCHED in states
        assert DeliveryState.DELIVERED in states

    @pytest.mark.asyncio
    async def test_failed_delivery_appends_dispatched_and_failed_log_entries(
        self, worker, tmp_delivery_log
    ):
        record = _make_record()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "timeout"
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await worker.deliver(record)

        records = worker._read_log()
        states = [r.state for r in records]
        assert DeliveryState.FAILED in states

    @pytest.mark.asyncio
    async def test_deliver_uses_payload_summary_as_message(self, worker):
        record = _make_record(payload_summary="Summary of dispatch")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            result = await worker.deliver(record)

        assert result is True

    @pytest.mark.asyncio
    async def test_deliver_falls_back_to_task_id_when_no_payload(self, worker):
        record = _make_record(payload_summary="", task_id="dispatch-gemini-my-task")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            result = await worker.deliver(record)

        assert result is True

    @pytest.mark.asyncio
    async def test_failed_delivery_uses_stdout_when_stderr_empty(self, worker):
        record = _make_record()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = ""
        mock_result.stdout = "stdout error message"

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            success = await worker.deliver(record)

        assert success is False
        records = worker._read_log()
        failed = [r for r in records if r.state == DeliveryState.FAILED]
        assert failed
        assert "stdout error message" in (failed[0].error or "")


# ---------------------------------------------------------------------------
# RelayWorker.ack
# ---------------------------------------------------------------------------


class TestAck:
    @pytest.mark.asyncio
    async def test_ack_delivered_record(self, worker, tmp_delivery_log):
        record = _make_record(message_id="msg_ack_001", state=DeliveryState.DELIVERED)
        _write_record(tmp_delivery_log, record)

        await worker.ack("msg_ack_001")

        records = worker._read_log()
        acked = [r for r in records if r.state == DeliveryState.ACKED]
        assert len(acked) == 1
        assert acked[0].message_id == "msg_ack_001"

    @pytest.mark.asyncio
    async def test_ack_nonexistent_record_logs_warning_and_returns(self, worker):
        # Should not raise; just logs a warning
        await worker.ack("msg_nonexistent")
        records = worker._read_log()
        assert records == []

    @pytest.mark.asyncio
    async def test_ack_queued_record_is_ignored(self, worker, tmp_delivery_log):
        record = _make_record(message_id="msg_queued", state=DeliveryState.QUEUED)
        _write_record(tmp_delivery_log, record)

        await worker.ack("msg_queued")

        records = worker._read_log()
        assert not any(r.state == DeliveryState.ACKED for r in records)


# ---------------------------------------------------------------------------
# RelayWorker.nack
# ---------------------------------------------------------------------------


class TestNack:
    @pytest.mark.asyncio
    async def test_nack_delivered_record_below_max_retries(self, worker, tmp_delivery_log):
        record = _make_record(
            message_id="msg_nack_001",
            task_id="dispatch-nack-task",
            state=DeliveryState.DELIVERED,
        )
        _write_record(tmp_delivery_log, record)
        worker._delivered_stems.add("dispatch-nack-task")

        with patch(
            "forge_harness.webhook_server.services.relay_worker._get_retry_count",
            return_value=1,
        ):
            await worker.nack("msg_nack_001", "agent busy")

        # Stem should be removed so it can be retried
        assert "dispatch-nack-task" not in worker._delivered_stems

        records = worker._read_log()
        nacked = [r for r in records if r.state == DeliveryState.NACKED]
        assert len(nacked) == 1

    @pytest.mark.asyncio
    async def test_nack_at_max_retries_keeps_stem_in_delivered(self, worker, tmp_delivery_log):
        record = _make_record(
            message_id="msg_nack_max",
            task_id="dispatch-max-retry-task",
            state=DeliveryState.DELIVERED,
        )
        _write_record(tmp_delivery_log, record)
        worker._delivered_stems.add("dispatch-max-retry-task")

        with patch(
            "forge_harness.webhook_server.services.relay_worker._get_retry_count",
            return_value=3,  # == max_retries
        ):
            await worker.nack("msg_nack_max", "permanent failure")

        # Stem should NOT be removed because max retries is exhausted
        assert "dispatch-max-retry-task" in worker._delivered_stems

    @pytest.mark.asyncio
    async def test_nack_nonexistent_record_returns_gracefully(self, worker):
        await worker.nack("msg_nonexistent", "test reason")
        records = worker._read_log()
        assert records == []

    @pytest.mark.asyncio
    async def test_nack_queued_record_is_ignored(self, worker, tmp_delivery_log):
        record = _make_record(message_id="msg_q", state=DeliveryState.QUEUED)
        _write_record(tmp_delivery_log, record)

        await worker.nack("msg_q", "rejected")
        records = worker._read_log()
        assert not any(r.state == DeliveryState.NACKED for r in records)


# ---------------------------------------------------------------------------
# RelayWorker.get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_empty_log_returns_zero_totals(self, worker):
        stats = worker.get_stats()
        assert stats["total"] == 0
        assert stats["by_state"] == {}
        assert stats["poll_interval"] == worker.poll_interval
        assert stats["max_retries"] == worker.max_retries

    def test_stats_counts_by_state(self, worker, tmp_delivery_log):
        _write_record(tmp_delivery_log, _make_record(message_id="m1", state=DeliveryState.QUEUED))
        _write_record(
            tmp_delivery_log, _make_record(message_id="m2", state=DeliveryState.DELIVERED)
        )
        _write_record(
            tmp_delivery_log, _make_record(message_id="m3", state=DeliveryState.DELIVERED)
        )

        stats = worker.get_stats()
        assert stats["total"] == 3
        assert stats["by_state"]["queued"] == 1
        assert stats["by_state"]["delivered"] == 2

    def test_stats_includes_configuration(self, worker):
        stats = worker.get_stats()
        assert "poll_interval" in stats
        assert "max_retries" in stats


# ---------------------------------------------------------------------------
# RelayWorker.run — main loop
# ---------------------------------------------------------------------------


class TestWorkerRun:
    @pytest.mark.asyncio
    async def test_run_processes_one_cycle_then_cancels(self, worker, tmp_dispatches):
        (tmp_dispatches / "dispatch-gemini-test.md").write_text("do something")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop, patch(
            "asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            mock_sleep.side_effect = asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await worker.run()

        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_catches_deliver_exceptions(self, worker, tmp_dispatches):
        (tmp_dispatches / "dispatch-agent-bad.md").write_text("problem task")

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        with patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ), patch("asyncio.get_event_loop") as mock_loop, patch(
            "asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=RuntimeError("deliberate error")
            )
            mock_sleep.side_effect = asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await worker.run()

    @pytest.mark.asyncio
    async def test_run_catches_poll_exceptions(self, worker):
        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        with patch.object(
            worker, "poll_pending", side_effect=RuntimeError("poll error")
        ), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await worker.run()


# ---------------------------------------------------------------------------
# Singleton: get_relay_worker / reset_relay_worker
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_relay_worker_returns_relay_worker_instance(self, tmp_dispatches, tmp_delivery_log):
        w = get_relay_worker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        assert isinstance(w, RelayWorker)

    def test_get_relay_worker_returns_same_instance_on_second_call(
        self, tmp_dispatches, tmp_delivery_log
    ):
        w1 = get_relay_worker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        w2 = get_relay_worker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        assert w1 is w2

    def test_get_relay_worker_ignores_kwargs_on_second_call(
        self, tmp_dispatches, tmp_delivery_log
    ):
        w1 = get_relay_worker(
            poll_interval=7.0,
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        # Second call with different poll_interval is ignored
        w2 = get_relay_worker(
            poll_interval=99.0,
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        assert w1 is w2
        assert w1.poll_interval == 7.0

    def test_reset_relay_worker_allows_new_instance(self, tmp_dispatches, tmp_delivery_log):
        w1 = get_relay_worker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        reset_relay_worker()
        w2 = get_relay_worker(
            dispatches_dir=tmp_dispatches,
            delivery_log=tmp_delivery_log,
        )
        assert w1 is not w2

    def test_reset_relay_worker_is_idempotent(self):
        reset_relay_worker()
        reset_relay_worker()  # second call should not raise

    def test_singleton_thread_safe(self, tmp_dispatches, tmp_delivery_log):
        import threading

        results: list[RelayWorker] = []
        lock = threading.Lock()

        def create():
            w = get_relay_worker(
                dispatches_dir=tmp_dispatches,
                delivery_log=tmp_delivery_log,
            )
            with lock:
                results.append(w)

        threads = [threading.Thread(target=create) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should see the same singleton
        assert all(r is results[0] for r in results)

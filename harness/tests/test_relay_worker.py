"""Tests for forge_harness.webhook_server.services.relay_worker.

Covers:
- poll_pending with 0, 1, and multiple dispatch files
- deliver success and failure paths
- ack / nack state transitions
- retry logic (max_retries enforcement)
- run loop with mocked sleep
- get_stats accuracy
- All filesystem and subprocess calls are mocked.

Run::

    cd /Users/bogdan/work/FORGE/harness
    uv run python -m pytest tests/test_relay_worker.py -x --tb=short -q
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from forge_harness.models.delivery import DeliveryRecord, DeliveryState
from forge_harness.webhook_server.services.relay_worker import (
    RelayWorker,
    _build_message_id,
    _get_retry_count,
    _increment_retry_count,
    get_relay_worker,
    reset_relay_worker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    message_id: str = "msg_test_00000001",
    task_id: str = "dispatch-gemini-test",
    state: DeliveryState = DeliveryState.QUEUED,
    target_agent: str | None = "gemini",
    payload_summary: str = "Test payload",
    error: str | None = None,
    dispatched_at: datetime | None = None,
    delivered_at: datetime | None = None,
    acked_at: datetime | None = None,
) -> DeliveryRecord:
    """Convenience factory for DeliveryRecord instances."""
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id,
        source_node="nova",
        target_node="nova",
        target_agent=target_agent,
        state=state,
        created_at=datetime.now(UTC),
        dispatched_at=dispatched_at,
        delivered_at=delivered_at,
        acked_at=acked_at,
        payload_summary=payload_summary,
        error=error,
    )


def _delivered_record(**kwargs: Any) -> DeliveryRecord:
    return _make_record(state=DeliveryState.DELIVERED, **kwargs)


def _make_worker(
    tmp_path: Path,
    poll_interval: float = 1.0,
    max_retries: int = 3,
) -> RelayWorker:
    """Build a RelayWorker wired entirely to *tmp_path*."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    delivery_log = tmp_path / "relay" / "delivery_log.jsonl"
    agent_script = tmp_path / "agent-message.sh"
    agent_script.write_text("#!/bin/bash\nexit 0\n")
    agent_script.chmod(0o755)

    return RelayWorker(
        poll_interval=poll_interval,
        max_retries=max_retries,
        dispatches_dir=dispatches_dir,
        delivery_log=delivery_log,
        agent_message_script=agent_script,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the global singleton before every test."""
    reset_relay_worker()
    yield
    reset_relay_worker()


@pytest.fixture
def tmp_worker(tmp_path: Path) -> RelayWorker:
    return _make_worker(tmp_path)


@pytest.fixture
def dispatches_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dispatches"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Module-level helper tests
# ---------------------------------------------------------------------------


def test_build_message_id_format():
    mid = _build_message_id()
    assert mid.startswith("msg_")
    parts = mid.split("_")
    # Format: msg_YYYYMMDD_HHMMSS_<8hex> → 4 parts
    assert len(parts) == 4
    assert len(parts[3]) == 8  # 8-char hex suffix


def test_retry_count_starts_at_zero(tmp_path: Path):
    with patch("forge_harness.webhook_server.services.relay_worker._RETRY_DIR", tmp_path / "retry"):
        (tmp_path / "retry").mkdir()
        count = _get_retry_count("msg_unknown")
        assert count == 0


def test_increment_retry_count(tmp_path: Path):
    retry_dir = tmp_path / "retry"
    retry_dir.mkdir()
    with patch("forge_harness.webhook_server.services.relay_worker._RETRY_DIR", retry_dir):
        assert _increment_retry_count("msg_abc") == 1
        assert _increment_retry_count("msg_abc") == 2
        assert _get_retry_count("msg_abc") == 2


# ---------------------------------------------------------------------------
# RelayWorker initialisation
# ---------------------------------------------------------------------------


def test_worker_initialises_with_defaults(tmp_path: Path):
    worker = _make_worker(tmp_path)
    assert worker.poll_interval == 1.0
    assert worker.max_retries == 3
    assert isinstance(worker._delivered_stems, set)


def test_worker_custom_params(tmp_path: Path):
    worker = _make_worker(tmp_path, poll_interval=10.0, max_retries=7)
    assert worker.poll_interval == 10.0
    assert worker.max_retries == 7


# ---------------------------------------------------------------------------
# poll_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_pending_empty_dispatches_dir(tmp_path: Path):
    """poll_pending returns empty list when no dispatch files exist."""
    worker = _make_worker(tmp_path)
    result = await worker.poll_pending()
    assert result == []


@pytest.mark.asyncio
async def test_poll_pending_missing_dispatches_dir(tmp_path: Path):
    """poll_pending returns empty list when the directory does not exist."""
    worker = RelayWorker(
        dispatches_dir=tmp_path / "nonexistent",
        delivery_log=tmp_path / "log.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    result = await worker.poll_pending()
    assert result == []


@pytest.mark.asyncio
async def test_poll_pending_single_file(tmp_path: Path):
    """poll_pending returns one record for a single dispatch file."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir()
    (dispatches_dir / "dispatch-gemini-task-2026-02-22.md").write_text(
        "# Task\nDo something useful.", encoding="utf-8"
    )

    worker = RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=tmp_path / "log.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    result = await worker.poll_pending()

    assert len(result) == 1
    rec = result[0]
    assert rec.state == DeliveryState.QUEUED
    assert rec.target_agent == "gemini"
    assert rec.task_id == "dispatch-gemini-task-2026-02-22"


@pytest.mark.asyncio
async def test_poll_pending_multiple_files(tmp_path: Path):
    """poll_pending returns one record per undelivered dispatch file."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir()
    files = [
        "dispatch-gemini-a.md",
        "dispatch-opencode-b.md",
        "dispatch-glm-c.md",
    ]
    for fn in files:
        (dispatches_dir / fn).write_text("content", encoding="utf-8")

    worker = RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=tmp_path / "log.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    result = await worker.poll_pending()
    assert len(result) == 3


@pytest.mark.asyncio
async def test_poll_pending_skips_already_delivered(tmp_path: Path):
    """poll_pending does not re-queue files already in _delivered_stems."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir()
    (dispatches_dir / "dispatch-gemini-old.md").write_text("old", encoding="utf-8")
    (dispatches_dir / "dispatch-gemini-new.md").write_text("new", encoding="utf-8")

    worker = RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=tmp_path / "log.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    # Mark one as already delivered
    worker._delivered_stems.add("dispatch-gemini-old")

    result = await worker.poll_pending()
    assert len(result) == 1
    assert result[0].task_id == "dispatch-gemini-new"


@pytest.mark.asyncio
async def test_poll_pending_ignores_non_md_files(tmp_path: Path):
    """poll_pending only picks up .md files."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir()
    (dispatches_dir / "dispatch-gemini-task.md").write_text("md", encoding="utf-8")
    (dispatches_dir / "dispatch-gemini-task.json").write_text("{}", encoding="utf-8")
    (dispatches_dir / "dispatch-gemini-task.txt").write_text("txt", encoding="utf-8")

    worker = RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=tmp_path / "log.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    result = await worker.poll_pending()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# deliver — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_success_transitions_to_delivered(tmp_path: Path):
    """deliver returns True and appends delivered state when script exits 0."""
    worker = _make_worker(tmp_path)
    record = _make_record()

    mock_result = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch(
            "forge_harness.webhook_server.services.relay_worker.asyncio.get_event_loop"
        ) as mock_loop,
        patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ),
    ):
        loop = asyncio.get_event_loop()
        mock_loop.return_value = loop

        with patch.object(loop, "run_in_executor", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            success = await worker.deliver(record)

    assert success is True
    assert record.task_id in worker._delivered_stems

    # Verify log contains dispatched + delivered entries
    lines = worker._delivery_log.read_text().splitlines()
    states = [json.loads(l)["state"] for l in lines if l.strip()]
    assert "dispatched" in states
    assert "delivered" in states


@pytest.mark.asyncio
async def test_deliver_success_updates_delivered_stems(tmp_path: Path):
    """Successful delivery adds task_id to _delivered_stems."""
    worker = _make_worker(tmp_path)
    record = _make_record(task_id="dispatch-gemini-stem-check")

    mock_result = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch(
            "forge_harness.webhook_server.services.relay_worker.asyncio.get_event_loop"
        ) as mock_loop,
        patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ),
    ):
        loop = asyncio.get_event_loop()
        mock_loop.return_value = loop
        with patch.object(loop, "run_in_executor", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            await worker.deliver(record)

    assert "dispatch-gemini-stem-check" in worker._delivered_stems


# ---------------------------------------------------------------------------
# deliver — failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_failure_returns_false(tmp_path: Path):
    """deliver returns False and writes failed state when script exits non-zero."""
    worker = _make_worker(tmp_path)
    record = _make_record()

    mock_result = CompletedProcess(args=[], returncode=4, stdout="", stderr="Dispatch failed")

    with (
        patch(
            "forge_harness.webhook_server.services.relay_worker.asyncio.get_event_loop"
        ) as mock_loop,
        patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ),
    ):
        loop = asyncio.get_event_loop()
        mock_loop.return_value = loop
        with patch.object(loop, "run_in_executor", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            success = await worker.deliver(record)

    assert success is False

    lines = worker._delivery_log.read_text().splitlines()
    states = [json.loads(l)["state"] for l in lines if l.strip()]
    assert "failed" in states


@pytest.mark.asyncio
async def test_deliver_subprocess_exception_returns_false(tmp_path: Path):
    """deliver returns False when run_in_executor raises an exception."""
    worker = _make_worker(tmp_path)
    record = _make_record()

    with (
        patch(
            "forge_harness.webhook_server.services.relay_worker.asyncio.get_event_loop"
        ) as mock_loop,
        patch(
            "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
            return_value=1,
        ),
    ):
        loop = asyncio.get_event_loop()
        mock_loop.return_value = loop
        with patch.object(loop, "run_in_executor", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = subprocess.TimeoutExpired("agent-message.sh", 60)
            success = await worker.deliver(record)

    assert success is False
    lines = worker._delivery_log.read_text().splitlines()
    states = [json.loads(l)["state"] for l in lines if l.strip()]
    assert "failed" in states


@pytest.mark.asyncio
async def test_deliver_invalid_state_transition_returns_false(tmp_path: Path):
    """deliver returns False immediately for a record not in queued state."""
    worker = _make_worker(tmp_path)
    # A record already in DISPATCHED state cannot be dispatched again
    record = _make_record(state=DeliveryState.DELIVERED)

    with patch(
        "forge_harness.webhook_server.services.relay_worker._increment_retry_count",
        return_value=1,
    ):
        success = await worker.deliver(record)

    assert success is False
    # No log entries should be written (we returned early)
    assert not worker._delivery_log.exists()


# ---------------------------------------------------------------------------
# ack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ack_transitions_delivered_to_acked(tmp_path: Path):
    """ack appends an acked record for a delivered message."""
    worker = _make_worker(tmp_path)
    delivered = _delivered_record(message_id="msg_ack_test")
    worker._append_log(delivered)

    await worker.ack("msg_ack_test")

    lines = worker._delivery_log.read_text().splitlines()
    states = [json.loads(l)["state"] for l in lines if l.strip()]
    assert states[-1] == "acked"


@pytest.mark.asyncio
async def test_ack_sets_acked_at_timestamp(tmp_path: Path):
    """ack populates the acked_at field on the written record."""
    worker = _make_worker(tmp_path)
    delivered = _delivered_record(message_id="msg_ts_test")
    worker._append_log(delivered)

    await worker.ack("msg_ts_test")

    lines = [json.loads(l) for l in worker._delivery_log.read_text().splitlines() if l.strip()]
    acked_lines = [l for l in lines if l["state"] == "acked"]
    assert len(acked_lines) == 1
    assert acked_lines[0]["acked_at"] is not None


@pytest.mark.asyncio
async def test_ack_unknown_record_logs_warning(tmp_path: Path):
    """ack on an unknown record_id does not raise; logs a warning."""
    worker = _make_worker(tmp_path)
    # Should not raise
    await worker.ack("msg_does_not_exist")


@pytest.mark.asyncio
async def test_ack_non_delivered_record_logs_warning(tmp_path: Path):
    """ack on a queued record (not delivered) does not raise; logs a warning."""
    worker = _make_worker(tmp_path)
    queued = _make_record(message_id="msg_queued_ack")
    worker._append_log(queued)

    await worker.ack("msg_queued_ack")

    # Log should still only have the queued entry
    lines = [json.loads(l) for l in worker._delivery_log.read_text().splitlines() if l.strip()]
    assert all(l["state"] != "acked" for l in lines)


# ---------------------------------------------------------------------------
# nack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nack_transitions_delivered_to_nacked(tmp_path: Path):
    """nack appends a nacked record for a delivered message."""
    worker = _make_worker(tmp_path)
    delivered = _delivered_record(message_id="msg_nack_test")
    worker._append_log(delivered)
    worker._delivered_stems.add(delivered.task_id or "")

    with patch(
        "forge_harness.webhook_server.services.relay_worker._get_retry_count",
        return_value=5,  # exhausted
    ):
        await worker.nack("msg_nack_test", reason="Agent unavailable")

    lines = worker._delivery_log.read_text().splitlines()
    states = [json.loads(l)["state"] for l in lines if l.strip()]
    assert states[-1] == "nacked"


@pytest.mark.asyncio
async def test_nack_stores_reason_in_error_field(tmp_path: Path):
    """nack persists the reason string in the error field."""
    worker = _make_worker(tmp_path)
    delivered = _delivered_record(message_id="msg_nack_reason")
    worker._append_log(delivered)

    with patch(
        "forge_harness.webhook_server.services.relay_worker._get_retry_count",
        return_value=5,
    ):
        await worker.nack("msg_nack_reason", reason="Bad gateway")

    lines = [json.loads(l) for l in worker._delivery_log.read_text().splitlines() if l.strip()]
    nacked = [l for l in lines if l["state"] == "nacked"]
    assert nacked[0]["error"] == "Bad gateway"


@pytest.mark.asyncio
async def test_nack_unknown_record_does_not_raise(tmp_path: Path):
    """nack on an unknown record_id does not raise; logs a warning."""
    worker = _make_worker(tmp_path)
    await worker.nack("msg_unknown_nack", reason="Test")


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nack_below_max_retries_removes_from_delivered_stems(tmp_path: Path):
    """nack below max_retries removes task_id from _delivered_stems (enables retry)."""
    worker = _make_worker(tmp_path, max_retries=3)
    delivered = _delivered_record(message_id="msg_retry_check", task_id="dispatch-gemini-retryable")
    worker._append_log(delivered)
    worker._delivered_stems.add("dispatch-gemini-retryable")

    with patch(
        "forge_harness.webhook_server.services.relay_worker._get_retry_count",
        return_value=1,  # below max_retries=3
    ):
        await worker.nack("msg_retry_check", reason="retry me")

    # Task stem should be removed so it can be re-polled
    assert "dispatch-gemini-retryable" not in worker._delivered_stems


@pytest.mark.asyncio
async def test_nack_at_max_retries_keeps_in_delivered_stems(tmp_path: Path):
    """nack at max_retries does NOT remove task_id from _delivered_stems."""
    worker = _make_worker(tmp_path, max_retries=3)
    delivered = _delivered_record(message_id="msg_max_retry", task_id="dispatch-gemini-maxed")
    worker._append_log(delivered)
    worker._delivered_stems.add("dispatch-gemini-maxed")

    with patch(
        "forge_harness.webhook_server.services.relay_worker._get_retry_count",
        return_value=3,  # == max_retries
    ):
        await worker.nack("msg_max_retry", reason="giving up")

    # Stem stays: no further retries allowed
    assert "dispatch-gemini-maxed" in worker._delivered_stems


@pytest.mark.asyncio
async def test_deliver_increments_retry_counter(tmp_path: Path):
    """Each call to deliver increments the persistent retry counter."""
    worker = _make_worker(tmp_path)

    retry_dir = tmp_path / "relay" / "retry"
    retry_dir.mkdir(parents=True, exist_ok=True)

    mock_result = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch(
            "forge_harness.webhook_server.services.relay_worker.asyncio.get_event_loop"
        ) as mock_loop,
        patch("forge_harness.webhook_server.services.relay_worker._RETRY_DIR", retry_dir),
    ):
        loop = asyncio.get_event_loop()
        mock_loop.return_value = loop
        with patch.object(loop, "run_in_executor", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            record = _make_record(message_id="msg_retry_inc")
            await worker.deliver(record)

    # Retry file should exist with attempts=1
    retry_file = retry_dir / "msg_retry_inc.json"
    assert retry_file.exists()
    data = json.loads(retry_file.read_text())
    assert data["attempts"] == 1


# ---------------------------------------------------------------------------
# run loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_loop_calls_poll_and_deliver(tmp_path: Path):
    """run() calls poll_pending and deliver in the poll cycle."""
    worker = _make_worker(tmp_path, poll_interval=0.05)
    record = _make_record()

    call_count = 0

    async def fake_poll():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [record]
        raise asyncio.CancelledError

    deliver_called_with: list[DeliveryRecord] = []

    async def fake_deliver(rec: DeliveryRecord) -> bool:
        deliver_called_with.append(rec)
        return True

    worker.poll_pending = fake_poll  # type: ignore[method-assign]
    worker.deliver = fake_deliver  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await worker.run()

    assert call_count >= 1
    assert len(deliver_called_with) == 1
    assert deliver_called_with[0].message_id == record.message_id


@pytest.mark.asyncio
async def test_run_loop_sleeps_between_cycles(tmp_path: Path):
    """run() sleeps for poll_interval between each poll cycle."""
    worker = _make_worker(tmp_path, poll_interval=5.0)
    sleep_calls: list[float] = []
    cycle_count = 0

    async def fake_poll():
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count >= 2:
            raise asyncio.CancelledError
        return []

    worker.poll_pending = fake_poll  # type: ignore[method-assign]

    with patch(
        "forge_harness.webhook_server.services.relay_worker.asyncio.sleep",
        new_callable=AsyncMock,
    ) as mock_sleep:

        async def cancel_after_sleep(interval: float) -> None:
            sleep_calls.append(interval)
            if len(sleep_calls) >= 1:
                raise asyncio.CancelledError

        mock_sleep.side_effect = cancel_after_sleep

        with pytest.raises(asyncio.CancelledError):
            await worker.run()

    assert len(sleep_calls) >= 1
    assert all(s == 5.0 for s in sleep_calls)


@pytest.mark.asyncio
async def test_run_loop_continues_after_deliver_exception(tmp_path: Path):
    """run() catches individual delivery exceptions and continues the loop."""
    worker = _make_worker(tmp_path, poll_interval=0.01)
    record = _make_record()
    cycle_count = 0

    async def fake_poll():
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count == 1:
            return [record]
        if cycle_count >= 2:
            raise asyncio.CancelledError
        return []

    async def exploding_deliver(rec: DeliveryRecord) -> bool:
        raise RuntimeError("boom")

    worker.poll_pending = fake_poll  # type: ignore[method-assign]
    worker.deliver = exploding_deliver  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await worker.run()

    # If we reach here without propagating RuntimeError, the loop survived.
    assert cycle_count >= 1


@pytest.mark.asyncio
async def test_run_loop_continues_after_poll_exception(tmp_path: Path):
    """run() catches poll_pending exceptions and continues the loop."""
    worker = _make_worker(tmp_path, poll_interval=0.01)
    cycle_count = 0

    async def flaky_poll():
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count == 1:
            raise OSError("disk read error")
        raise asyncio.CancelledError

    worker.poll_pending = flaky_poll  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await worker.run()

    assert cycle_count >= 2


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


def test_get_stats_empty_log(tmp_path: Path):
    """get_stats returns zero total when no records exist."""
    worker = _make_worker(tmp_path)
    stats = worker.get_stats()

    assert stats["total"] == 0
    assert stats["by_state"] == {}
    assert stats["poll_interval"] == 1.0
    assert stats["max_retries"] == 3


def test_get_stats_counts_by_state(tmp_path: Path):
    """get_stats accurately counts records per state."""
    worker = _make_worker(tmp_path)

    records = [
        _make_record(message_id="m1", state=DeliveryState.QUEUED),
        _make_record(message_id="m2", state=DeliveryState.DISPATCHED),
        _make_record(message_id="m3", state=DeliveryState.DELIVERED),
        _make_record(message_id="m4", state=DeliveryState.ACKED),
        _make_record(message_id="m5", state=DeliveryState.ACKED),
        _make_record(message_id="m6", state=DeliveryState.FAILED),
    ]
    for rec in records:
        worker._append_log(rec)

    stats = worker.get_stats()

    assert stats["total"] == 6
    assert stats["by_state"]["queued"] == 1
    assert stats["by_state"]["dispatched"] == 1
    assert stats["by_state"]["delivered"] == 1
    assert stats["by_state"]["acked"] == 2
    assert stats["by_state"]["failed"] == 1


def test_get_stats_excludes_zero_counts(tmp_path: Path):
    """get_stats omits states with zero records from by_state."""
    worker = _make_worker(tmp_path)
    worker._append_log(_make_record(message_id="m1", state=DeliveryState.ACKED))

    stats = worker.get_stats()

    assert "acked" in stats["by_state"]
    assert "queued" not in stats["by_state"]
    assert "failed" not in stats["by_state"]


def test_get_stats_reflects_config(tmp_path: Path):
    """get_stats includes poll_interval and max_retries from config."""
    worker = _make_worker(tmp_path, poll_interval=42.0, max_retries=9)
    stats = worker.get_stats()

    assert stats["poll_interval"] == 42.0
    assert stats["max_retries"] == 9


# ---------------------------------------------------------------------------
# Delivery log persistence
# ---------------------------------------------------------------------------


def test_append_and_read_log_roundtrip(tmp_path: Path):
    """Records written to the log can be read back with the same field values."""
    worker = _make_worker(tmp_path)
    record = _make_record(message_id="msg_roundtrip_01", payload_summary="hello world")
    worker._append_log(record)

    read_back = worker._read_log()
    assert len(read_back) == 1
    assert read_back[0].message_id == "msg_roundtrip_01"
    assert read_back[0].payload_summary == "hello world"
    assert read_back[0].state == DeliveryState.QUEUED


def test_read_log_skips_malformed_lines(tmp_path: Path):
    """_read_log skips lines that are not valid JSON or DeliveryRecord."""
    log_path = tmp_path / "relay" / "delivery_log.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        '{"message_id": "m1", "source_node": "n", "target_node": "n", '
        '"state": "queued", "created_at": "2026-02-22T00:00:00+00:00", '
        '"payload_summary": ""}\n'
        "not valid json at all\n"
        '{"garbage": true}\n',
        encoding="utf-8",
    )

    worker = RelayWorker(
        dispatches_dir=tmp_path / "dispatches",
        delivery_log=log_path,
        agent_message_script=tmp_path / "noop.sh",
    )
    records = worker._read_log()
    # Only the valid first record should come through
    assert len(records) == 1
    assert records[0].message_id == "m1"


def test_read_log_returns_empty_when_file_absent(tmp_path: Path):
    """_read_log returns [] when the log file does not exist."""
    worker = RelayWorker(
        dispatches_dir=tmp_path / "dispatches",
        delivery_log=tmp_path / "nonexistent.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    assert worker._read_log() == []


# ---------------------------------------------------------------------------
# _load_delivered_stems on startup
# ---------------------------------------------------------------------------


def test_startup_loads_delivered_stems_from_log(tmp_path: Path):
    """RelayWorker skips task_ids already marked delivered/acked/nacked in log."""
    log_path = tmp_path / "relay" / "delivery_log.jsonl"
    log_path.parent.mkdir(parents=True)

    # Write one delivered and one acked record
    records = [
        _delivered_record(message_id="m1", task_id="dispatch-gemini-already-done"),
        _make_record(
            message_id="m2",
            task_id="dispatch-gemini-acked-too",
            state=DeliveryState.ACKED,
        ),
    ]
    with log_path.open("w") as fh:
        for r in records:
            fh.write(r.model_dump_json() + "\n")

    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir()
    (dispatches_dir / "dispatch-gemini-already-done.md").write_text("x")
    (dispatches_dir / "dispatch-gemini-acked-too.md").write_text("y")
    (dispatches_dir / "dispatch-gemini-fresh.md").write_text("z")

    worker = RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=log_path,
        agent_message_script=tmp_path / "noop.sh",
    )

    assert "dispatch-gemini-already-done" in worker._delivered_stems
    assert "dispatch-gemini-acked-too" in worker._delivered_stems


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_relay_worker_returns_singleton(tmp_path: Path):
    """get_relay_worker always returns the same instance."""
    w1 = get_relay_worker(
        dispatches_dir=tmp_path / "d",
        delivery_log=tmp_path / "l.jsonl",
        agent_message_script=tmp_path / "s.sh",
    )
    w2 = get_relay_worker()
    assert w1 is w2


def test_reset_relay_worker_allows_new_instance(tmp_path: Path):
    """reset_relay_worker clears the singleton so a new one can be created."""
    w1 = get_relay_worker(
        dispatches_dir=tmp_path / "d",
        delivery_log=tmp_path / "l.jsonl",
        agent_message_script=tmp_path / "s.sh",
    )
    reset_relay_worker()
    w2 = get_relay_worker(
        poll_interval=99.0,
        dispatches_dir=tmp_path / "d2",
        delivery_log=tmp_path / "l2.jsonl",
        agent_message_script=tmp_path / "s2.sh",
    )
    assert w1 is not w2
    assert w2.poll_interval == 99.0


def test_get_relay_worker_uses_provided_params(tmp_path: Path):
    """First call to get_relay_worker honours the supplied configuration."""
    w = get_relay_worker(
        poll_interval=30.0,
        max_retries=7,
        dispatches_dir=tmp_path / "d",
        delivery_log=tmp_path / "l.jsonl",
        agent_message_script=tmp_path / "s.sh",
    )
    assert w.poll_interval == 30.0
    assert w.max_retries == 7


# ---------------------------------------------------------------------------
# services __init__ re-export
# ---------------------------------------------------------------------------


def test_relay_worker_exported_from_services():
    """RelayWorker and its factory are accessible from the services package."""
    from forge_harness.webhook_server import services

    assert hasattr(services, "RelayWorker")
    assert hasattr(services, "get_relay_worker")
    assert hasattr(services, "reset_relay_worker")


# ---------------------------------------------------------------------------
# Module-level helpers: _append_delivery_log, _read_delivery_log
# ---------------------------------------------------------------------------


def test_module_append_delivery_log(tmp_path: Path):
    """Module-level _append_delivery_log writes a JSONL line to the global log path."""
    from forge_harness.webhook_server.services import relay_worker as rw_mod

    log_path = tmp_path / "relay" / "delivery_log.jsonl"
    relay_dir = tmp_path / "relay"
    relay_dir.mkdir(parents=True)
    retry_dir = tmp_path / "relay" / "retry"
    retry_dir.mkdir()

    record = _make_record(message_id="msg_module_append")

    with (
        patch.object(rw_mod, "_DELIVERY_LOG", log_path),
        patch.object(rw_mod, "_RELAY_DIR", relay_dir),
        patch.object(rw_mod, "_RETRY_DIR", retry_dir),
    ):
        rw_mod._append_delivery_log(record)

    assert log_path.exists()
    lines = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    assert lines[0]["message_id"] == "msg_module_append"


def test_module_read_delivery_log_returns_empty_when_missing(tmp_path: Path):
    """Module-level _read_delivery_log returns [] when log file is absent."""
    from forge_harness.webhook_server.services import relay_worker as rw_mod

    with patch.object(rw_mod, "_DELIVERY_LOG", tmp_path / "nonexistent.jsonl"):
        result = rw_mod._read_delivery_log()

    assert result == []


def test_module_read_delivery_log_skips_malformed(tmp_path: Path):
    """Module-level _read_delivery_log skips malformed JSON lines."""
    from forge_harness.webhook_server.services import relay_worker as rw_mod

    log_path = tmp_path / "relay" / "delivery_log.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        '{"message_id": "m_ok", "source_node": "n", "target_node": "n", '
        '"state": "queued", "created_at": "2026-02-22T00:00:00+00:00", "payload_summary": ""}\n'
        "not json\n",
        encoding="utf-8",
    )

    with patch.object(rw_mod, "_DELIVERY_LOG", log_path):
        result = rw_mod._read_delivery_log()

    assert len(result) == 1
    assert result[0].message_id == "m_ok"


def test_module_read_delivery_log_skips_blank_lines(tmp_path: Path):
    """Module-level _read_delivery_log skips blank lines (hits the continue branch)."""
    from forge_harness.webhook_server.services import relay_worker as rw_mod

    log_path = tmp_path / "relay" / "delivery_log.jsonl"
    log_path.parent.mkdir(parents=True)
    record = _make_record(message_id="m_blank_skip_mod")
    log_path.write_text(
        "\n" + record.model_dump_json() + "\n\n",
        encoding="utf-8",
    )

    with patch.object(rw_mod, "_DELIVERY_LOG", log_path):
        result = rw_mod._read_delivery_log()

    assert len(result) == 1
    assert result[0].message_id == "m_blank_skip_mod"


# ---------------------------------------------------------------------------
# _get_retry_count: corrupt JSON branch
# ---------------------------------------------------------------------------


def test_get_retry_count_returns_zero_on_corrupt_file(tmp_path: Path):
    """_get_retry_count returns 0 when the retry file contains bad JSON."""
    retry_dir = tmp_path / "retry"
    retry_dir.mkdir()
    (retry_dir / "msg_corrupt.json").write_text("not json", encoding="utf-8")

    with patch("forge_harness.webhook_server.services.relay_worker._RETRY_DIR", retry_dir):
        result = _get_retry_count("msg_corrupt")

    assert result == 0


# ---------------------------------------------------------------------------
# _dispatch_file_to_record: OSError branch on file read
# ---------------------------------------------------------------------------


def test_dispatch_file_to_record_handles_read_error(tmp_path: Path):
    """_dispatch_file_to_record uses empty summary when file cannot be read."""
    from forge_harness.webhook_server.services.relay_worker import _dispatch_file_to_record

    fake_path = MagicMock(spec=Path)
    fake_path.stem = "dispatch-gemini-unreadable"
    fake_path.read_text.side_effect = OSError("permission denied")

    record = _dispatch_file_to_record(fake_path)

    assert record.payload_summary == ""
    assert record.task_id == "dispatch-gemini-unreadable"
    assert record.target_agent == "gemini"


# ---------------------------------------------------------------------------
# _load_delivered_stems: exception on _read_log
# ---------------------------------------------------------------------------


def test_load_delivered_stems_handles_read_exception(tmp_path: Path):
    """_load_delivered_stems does not raise when _read_log throws."""
    worker = _make_worker(tmp_path)

    def raise_io():
        raise OSError("disk failure")

    worker._read_log = raise_io  # type: ignore[method-assign]

    # Force a reload by calling directly
    worker._load_delivered_stems()

    # Should not raise and _delivered_stems should remain empty (or unchanged)
    assert isinstance(worker._delivered_stems, set)


# ---------------------------------------------------------------------------
# _read_log: blank-line skipping
# ---------------------------------------------------------------------------


def test_read_log_skips_blank_lines(tmp_path: Path):
    """_read_log handles blank lines embedded in the log file."""
    log_path = tmp_path / "relay" / "delivery_log.jsonl"
    log_path.parent.mkdir(parents=True)
    record = _make_record(message_id="msg_blank_skip")
    log_path.write_text(
        "\n" + record.model_dump_json() + "\n\n",
        encoding="utf-8",
    )

    worker = RelayWorker(
        dispatches_dir=tmp_path / "dispatches",
        delivery_log=log_path,
        agent_message_script=tmp_path / "noop.sh",
    )
    records = worker._read_log()
    assert len(records) == 1
    assert records[0].message_id == "msg_blank_skip"


# ---------------------------------------------------------------------------
# _build_target helper
# ---------------------------------------------------------------------------


def test_build_target_uses_target_agent(tmp_path: Path):
    worker = _make_worker(tmp_path)
    record = _make_record(target_agent="gemini")
    assert worker._build_target(record) == "forge:gemini"


def test_build_target_defaults_to_orchestrator_when_no_agent(tmp_path: Path):
    worker = _make_worker(tmp_path)
    record = _make_record(target_agent=None)
    assert worker._build_target(record) == "forge:orchestrator"


# ---------------------------------------------------------------------------
# payload_summary truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_pending_truncates_payload_summary(tmp_path: Path):
    """poll_pending caps payload_summary at 120 characters."""
    dispatches_dir = tmp_path / "dispatches"
    dispatches_dir.mkdir()
    long_content = "A" * 300
    (dispatches_dir / "dispatch-gemini-long.md").write_text(long_content, encoding="utf-8")

    worker = RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=tmp_path / "log.jsonl",
        agent_message_script=tmp_path / "noop.sh",
    )
    result = await worker.poll_pending()
    assert len(result) == 1
    assert len(result[0].payload_summary) <= 120

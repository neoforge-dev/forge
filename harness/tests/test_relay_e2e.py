"""End-to-end integration tests for the FORGE relay worker system.

Covers the full delivery lifecycle — from dispatch file creation through
poll, deliver, ack/nack — testing state transitions, log persistence, and
singleton management in isolation.

State machine under test:
    queued --> dispatched --> delivered --> acked
                   |              |
                   |              +--> nacked
                   |
                   +--> failed
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.models.delivery import DeliveryRecord, DeliveryState
from forge_harness.webhook_server.handlers.adaptive_retry_handler import (
    reset_adaptive_retry_handler,
)
from forge_harness.webhook_server.services.relay_worker import (
    RelayWorker,
    get_relay_worker,
    reset_relay_worker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dispatches_dir(tmp_path: Path) -> Path:
    """Create and return a dispatches subdirectory."""
    d = tmp_path / "dispatches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_delivery_log(tmp_path: Path) -> Path:
    """Return the path to a delivery log (does not create the file)."""
    return tmp_path / "delivery_log.jsonl"


def _make_script(tmp_path: Path) -> Path:
    """Return the path to a placeholder agent-message script."""
    return tmp_path / "mock-agent-message.sh"


def _make_worker(
    tmp_path: Path,
    max_retries: int = 3,
    poll_interval: float = 0.1,
) -> RelayWorker:
    """Build a RelayWorker wired to tmp_path-based filesystem locations."""
    dispatches_dir = _make_dispatches_dir(tmp_path)
    delivery_log = _make_delivery_log(tmp_path)
    agent_script = _make_script(tmp_path)
    return RelayWorker(
        dispatches_dir=dispatches_dir,
        delivery_log=delivery_log,
        agent_message_script=agent_script,
        max_retries=max_retries,
        poll_interval=poll_interval,
    )


def _write_dispatch_file(dispatches_dir: Path, stem: str, content: str = "Test dispatch content") -> Path:
    """Write a .md dispatch file into dispatches_dir and return its path."""
    path = dispatches_dir / f"{stem}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _read_log_records(delivery_log: Path) -> list[DeliveryRecord]:
    """Parse all JSONL lines from the delivery log into DeliveryRecord objects."""
    if not delivery_log.exists():
        return []
    records: list[DeliveryRecord] = []
    for line in delivery_log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(DeliveryRecord.model_validate(json.loads(line)))
    return records


def _states_in_log(delivery_log: Path) -> list[str]:
    """Return ordered list of state values from the delivery log."""
    return [r.state.value for r in _read_log_records(delivery_log)]


def _make_delivered_record(message_id: str, task_id: str = "dispatch-test-task") -> DeliveryRecord:
    """Build a minimal DeliveryRecord already in DELIVERED state."""
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id,
        source_node="prya",
        target_node="prya",
        target_agent="gemini",
        state=DeliveryState.DELIVERED,
        created_at=datetime.now(UTC),
        delivered_at=datetime.now(UTC),
        payload_summary="Test payload summary",
    )


def _write_record_to_log(delivery_log: Path, record: DeliveryRecord) -> None:
    """Append a single DeliveryRecord as a JSON line to the delivery log."""
    delivery_log.parent.mkdir(parents=True, exist_ok=True)
    with delivery_log.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")


def _make_subprocess_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a mock subprocess.CompletedProcess result."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# Autouse fixture: reset singleton between every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_worker():
    """Reset relay worker singleton and adaptive retry handler between tests."""
    reset_relay_worker()
    reset_adaptive_retry_handler()
    yield
    reset_relay_worker()
    reset_adaptive_retry_handler()


# ---------------------------------------------------------------------------
# Test 1: poll_pending returns queued DeliveryRecord for each .md file
# ---------------------------------------------------------------------------


class TestPollPending:
    """Tests for RelayWorker.poll_pending()."""

    def test_enqueue_creates_delivery_record(self, tmp_path):
        """poll_pending returns one QUEUED DeliveryRecord per unprocessed .md file."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-test-task-20260226")

        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, DeliveryRecord)
        assert record.state == DeliveryState.QUEUED
        assert record.task_id == "dispatch-gemini-test-task-20260226"
        assert record.message_id.startswith("msg_")

    def test_poll_pending_returns_empty_when_no_dispatch_files(self, tmp_path):
        """poll_pending returns an empty list when dispatches directory is empty."""
        worker = _make_worker(tmp_path)

        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())

        assert records == []

    def test_poll_pending_skips_already_delivered_stems(self, tmp_path):
        """Dispatch files already tracked in _delivered_stems are not re-queued."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir

        stem = "dispatch-gemini-already-done-20260226"
        _write_dispatch_file(dispatches_dir, stem)
        worker._delivered_stems.add(stem)

        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())

        assert records == []

    def test_poll_pending_extracts_target_agent_from_filename(self, tmp_path):
        """The second segment of the dispatch filename becomes target_agent."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir

        _write_dispatch_file(dispatches_dir, "dispatch-kimi-registry-20260226")

        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())

        assert len(records) == 1
        assert records[0].target_agent == "kimi"

    def test_poll_pending_reads_payload_summary(self, tmp_path):
        """poll_pending populates payload_summary with first 120 chars of file content."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        content = "A" * 200  # longer than the 120-char limit

        _write_dispatch_file(dispatches_dir, "dispatch-minimax-payload-20260226", content=content)

        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())

        assert len(records) == 1
        assert len(records[0].payload_summary) == 120
        assert records[0].payload_summary == "A" * 120

    def test_poll_pending_returns_multiple_records_for_multiple_files(self, tmp_path):
        """Each .md file produces exactly one queued record."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-task-a-20260226")
        _write_dispatch_file(dispatches_dir, "dispatch-kimi-task-b-20260226")

        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())

        assert len(records) == 2
        task_ids = {r.task_id for r in records}
        assert "dispatch-gemini-task-a-20260226" in task_ids
        assert "dispatch-kimi-task-b-20260226" in task_ids


# ---------------------------------------------------------------------------
# Test 2: deliver transitions queued -> dispatched -> delivered on exit code 0
# ---------------------------------------------------------------------------


class TestDeliver:
    """Tests for RelayWorker.deliver()."""

    def test_deliver_transitions_to_delivered(self, tmp_path):
        """Successful delivery (exit code 0) writes queued->dispatched->delivered to log."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-deliver-test-20260226")
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        assert len(records) == 1
        record = records[0]

        mock_result = _make_subprocess_result(returncode=0)

        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            success = asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        assert success is True

        log_records = _read_log_records(delivery_log)
        states = [r.state.value for r in log_records]
        assert "dispatched" in states
        assert "delivered" in states
        # dispatched must precede delivered in the log
        assert states.index("dispatched") < states.index("delivered")

    def test_deliver_failure_transitions_to_failed(self, tmp_path):
        """Delivery failure (exit code 1) writes queued->dispatched->failed to log."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-fail-test-20260226")
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        record = records[0]

        mock_result = _make_subprocess_result(
            returncode=1, stderr="can't find session: forge"
        )

        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            success = asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        assert success is False

        log_records = _read_log_records(delivery_log)
        states = [r.state.value for r in log_records]
        assert "dispatched" in states
        assert "failed" in states
        # dispatched must precede failed
        assert states.index("dispatched") < states.index("failed")

    def test_deliver_failure_records_error_message(self, tmp_path):
        """Failed delivery persists the stderr output as the error field."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-error-test-20260226")
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        record = records[0]

        error_msg = "no such session: forge"
        mock_result = _make_subprocess_result(returncode=1, stderr=error_msg)

        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        log_records = _read_log_records(delivery_log)
        failed_records = [r for r in log_records if r.state == DeliveryState.FAILED]
        assert len(failed_records) == 1
        assert failed_records[0].error == error_msg

    def test_deliver_success_adds_to_delivered_stems(self, tmp_path):
        """Successful delivery adds the dispatch file stem to _delivered_stems."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        stem = "dispatch-gemini-stems-test-20260226"

        _write_dispatch_file(dispatches_dir, stem)
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        record = records[0]

        mock_result = _make_subprocess_result(returncode=0)

        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        assert stem in worker._delivered_stems

    def test_deliver_exception_during_subprocess_returns_false(self, tmp_path):
        """An exception raised during subprocess.run is caught; deliver returns False."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-exc-test-20260226")
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        record = records[0]

        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            side_effect=OSError("subprocess unavailable"),
        ):
            success = asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        assert success is False

        # A failed record should still be written
        log_records = _read_log_records(delivery_log)
        states = [r.state.value for r in log_records]
        assert "failed" in states


# ---------------------------------------------------------------------------
# Test 3: ack transitions delivered -> acked
# ---------------------------------------------------------------------------


class TestAck:
    """Tests for RelayWorker.ack()."""

    def test_ack_transitions_delivered_to_acked(self, tmp_path):
        """ack() appends an acked record when the most-recent state is delivered."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        msg_id = "msg_20260226_abc12345"
        record = _make_delivered_record(msg_id)
        _write_record_to_log(delivery_log, record)

        asyncio.get_event_loop().run_until_complete(worker.ack(msg_id))

        log_records = _read_log_records(delivery_log)
        acked_records = [r for r in log_records if r.state == DeliveryState.ACKED]
        assert len(acked_records) == 1
        assert acked_records[0].message_id == msg_id
        assert acked_records[0].acked_at is not None

    def test_ack_on_non_delivered_record_is_noop(self, tmp_path):
        """ack() on a record that is not in DELIVERED state is silently ignored."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        msg_id = "msg_20260226_noop0001"
        queued_record = DeliveryRecord(
            message_id=msg_id,
            task_id="dispatch-test-queued",
            source_node="prya",
            target_node="prya",
            state=DeliveryState.QUEUED,
            created_at=datetime.now(UTC),
            payload_summary="Queued payload",
        )
        _write_record_to_log(delivery_log, queued_record)

        initial_line_count = len(_read_log_records(delivery_log))

        # ack on a queued (non-delivered) record should be a no-op
        asyncio.get_event_loop().run_until_complete(worker.ack(msg_id))

        log_records = _read_log_records(delivery_log)
        # No new record should have been appended
        assert len(log_records) == initial_line_count
        acked_records = [r for r in log_records if r.state == DeliveryState.ACKED]
        assert len(acked_records) == 0

    def test_ack_on_nonexistent_message_is_noop(self, tmp_path):
        """ack() on a message_id that doesn't exist raises no error and writes nothing."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        # ack a non-existent ID — should not raise and should write nothing
        asyncio.get_event_loop().run_until_complete(worker.ack("nonexistent_id"))

        # Log file should not have been created (or is empty)
        if delivery_log.exists():
            assert _read_log_records(delivery_log) == []

    def test_ack_only_matches_delivered_state(self, tmp_path):
        """ack() uses the most recent delivered entry (not other states) for msg_id."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        msg_id = "msg_20260226_multistate"
        # Write queued and delivered entries for the same message
        queued = DeliveryRecord(
            message_id=msg_id,
            task_id="dispatch-multi-state",
            source_node="prya",
            target_node="prya",
            state=DeliveryState.QUEUED,
            created_at=datetime.now(UTC),
            payload_summary="multi-state test",
        )
        delivered = _make_delivered_record(msg_id, task_id="dispatch-multi-state")
        _write_record_to_log(delivery_log, queued)
        _write_record_to_log(delivery_log, delivered)

        asyncio.get_event_loop().run_until_complete(worker.ack(msg_id))

        log_records = _read_log_records(delivery_log)
        acked_records = [r for r in log_records if r.state == DeliveryState.ACKED]
        assert len(acked_records) == 1


# ---------------------------------------------------------------------------
# Test 4: nack transitions delivered -> nacked
# ---------------------------------------------------------------------------


class TestNack:
    """Tests for RelayWorker.nack()."""

    def test_nack_transitions_delivered_to_nacked(self, tmp_path):
        """nack() appends a nacked record when the most-recent state is delivered."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        msg_id = "msg_20260226_nack0001"
        record = _make_delivered_record(msg_id)
        _write_record_to_log(delivery_log, record)

        asyncio.get_event_loop().run_until_complete(worker.nack(msg_id, "agent rejected task"))

        log_records = _read_log_records(delivery_log)
        nacked_records = [r for r in log_records if r.state == DeliveryState.NACKED]
        assert len(nacked_records) == 1
        assert nacked_records[0].message_id == msg_id
        assert nacked_records[0].error == "agent rejected task"

    def test_nack_with_retries_remaining_removes_stem_for_requeue(self, tmp_path):
        """nack() below max_retries discards the stem from _delivered_stems so re-poll works."""
        worker = _make_worker(tmp_path, max_retries=3)
        delivery_log = worker._delivery_log

        msg_id = "msg_20260226_retry001"
        task_id = "dispatch-gemini-retry-task-20260226"
        record = _make_delivered_record(msg_id, task_id=task_id)
        _write_record_to_log(delivery_log, record)

        # Put the stem in _delivered_stems as deliver() would have done
        worker._delivered_stems.add(task_id)

        # With 0 prior attempts (retry count = 0 < max_retries=3), should allow retry
        asyncio.get_event_loop().run_until_complete(worker.nack(msg_id, "temporary failure"))

        # The stem should have been removed so next poll cycle re-queues it
        assert task_id not in worker._delivered_stems

    def test_nack_with_max_retries_stays_nacked(self, tmp_path):
        """nack() at max_retries does not remove the stem from _delivered_stems."""
        worker = _make_worker(tmp_path, max_retries=1)
        delivery_log = worker._delivery_log

        msg_id = "msg_20260226_maxret01"
        task_id = "dispatch-gemini-maxretry-20260226"
        record = _make_delivered_record(msg_id, task_id=task_id)
        _write_record_to_log(delivery_log, record)

        # Simulate that max_retries has already been used up by pre-filling
        # the retry counter file in the module-level _RETRY_DIR.
        # We patch _get_retry_count to return a value >= max_retries.
        with patch(
            "forge_harness.webhook_server.services.relay_worker._get_retry_count",
            return_value=1,  # equal to max_retries=1 means no more retries
        ):
            worker._delivered_stems.add(task_id)
            asyncio.get_event_loop().run_until_complete(
                worker.nack(msg_id, "permanent failure — max retries exhausted")
            )

        # The stem should NOT have been removed — task stays dead
        assert task_id in worker._delivered_stems

    def test_nack_on_nonexistent_message_is_noop(self, tmp_path):
        """nack() on unknown message_id is silent and writes nothing."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        asyncio.get_event_loop().run_until_complete(worker.nack("nonexistent_id", "reason"))

        if delivery_log.exists():
            nacked = [
                r for r in _read_log_records(delivery_log)
                if r.state == DeliveryState.NACKED
            ]
            assert len(nacked) == 0


# ---------------------------------------------------------------------------
# Test 5: Full lifecycle dispatch -> deliver -> ack
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """End-to-end tests covering the complete dispatch -> deliver -> ack flow."""

    def test_full_lifecycle_dispatch_deliver_ack(self, tmp_path):
        """Full path: create dispatch file -> poll -> deliver -> ack -> final state=acked."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        stem = "dispatch-gemini-lifecycle-20260226"
        _write_dispatch_file(dispatches_dir, stem, content="Execute task: update docs")

        # Step 1: poll
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        assert len(records) == 1
        record = records[0]
        assert record.state == DeliveryState.QUEUED

        # Step 2: deliver
        mock_result = _make_subprocess_result(returncode=0)
        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            success = asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        assert success is True

        # Verify delivered state in log
        log_records = _read_log_records(delivery_log)
        delivered_records = [r for r in log_records if r.state == DeliveryState.DELIVERED]
        assert len(delivered_records) == 1
        delivered = delivered_records[0]

        # Step 3: ack
        asyncio.get_event_loop().run_until_complete(worker.ack(delivered.message_id))

        # Verify final state is acked
        final_records = _read_log_records(delivery_log)
        acked_records = [r for r in final_records if r.state == DeliveryState.ACKED]
        assert len(acked_records) == 1
        assert acked_records[0].message_id == record.message_id

        # States in order: dispatched, delivered, acked (queued not written to log by worker)
        states = _states_in_log(delivery_log)
        assert states.index("dispatched") < states.index("delivered")
        assert states.index("delivered") < states.index("acked")

    def test_full_lifecycle_dispatch_deliver_nack_retry(self, tmp_path):
        """Full path: dispatch file -> poll -> deliver -> nack -> re-poll re-queues."""
        worker = _make_worker(tmp_path, max_retries=3)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        stem = "dispatch-gemini-nack-lifecycle-20260226"
        _write_dispatch_file(dispatches_dir, stem, content="Execute agent task")

        # Poll and deliver
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        assert len(records) == 1
        record = records[0]

        mock_result = _make_subprocess_result(returncode=0)
        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        # Find the delivered record's message_id
        all_records = _read_log_records(delivery_log)
        delivered_records = [r for r in all_records if r.state == DeliveryState.DELIVERED]
        assert len(delivered_records) == 1
        delivered_id = delivered_records[0].message_id

        # Nack below max_retries
        asyncio.get_event_loop().run_until_complete(
            worker.nack(delivered_id, "agent context depleted — please retry")
        )

        # Stem should have been removed — next poll can re-queue
        assert stem not in worker._delivered_stems

        # A new poll should pick it up again
        new_records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        assert len(new_records) == 1
        assert new_records[0].task_id == stem


# ---------------------------------------------------------------------------
# Test 6: get_stats reports accurate counts by state
# ---------------------------------------------------------------------------


class TestRelayWorkerStats:
    """Tests for RelayWorker.get_stats()."""

    def test_relay_worker_stats_empty_log(self, tmp_path):
        """get_stats returns zero totals when no delivery records exist."""
        worker = _make_worker(tmp_path)

        stats = worker.get_stats()

        assert stats["total"] == 0
        assert stats["by_state"] == {}
        assert stats["poll_interval"] == worker.poll_interval
        assert stats["max_retries"] == worker.max_retries

    def test_relay_worker_stats_counts_by_state(self, tmp_path):
        """get_stats returns correct counts for each state present in the log."""
        worker = _make_worker(tmp_path)
        delivery_log = worker._delivery_log

        now = datetime.now(UTC)

        def _make_record(msg_id: str, state: DeliveryState) -> DeliveryRecord:
            return DeliveryRecord(
                message_id=msg_id,
                task_id=f"task-{msg_id}",
                source_node="prya",
                target_node="prya",
                state=state,
                created_at=now,
                payload_summary=f"Payload for {msg_id}",
            )

        records_to_write = [
            _make_record("msg_stats_001", DeliveryState.DISPATCHED),
            _make_record("msg_stats_002", DeliveryState.DELIVERED),
            _make_record("msg_stats_003", DeliveryState.ACKED),
            _make_record("msg_stats_004", DeliveryState.FAILED),
            _make_record("msg_stats_005", DeliveryState.FAILED),
        ]
        for rec in records_to_write:
            _write_record_to_log(delivery_log, rec)

        stats = worker.get_stats()

        assert stats["total"] == 5
        by_state = stats["by_state"]
        assert by_state.get("dispatched", 0) == 1
        assert by_state.get("delivered", 0) == 1
        assert by_state.get("acked", 0) == 1
        assert by_state.get("failed", 0) == 2
        assert by_state.get("queued", 0) == 0

    def test_relay_worker_stats_after_full_lifecycle(self, tmp_path):
        """get_stats counts every state entry written to the log (not deduplicated)."""
        worker = _make_worker(tmp_path)
        dispatches_dir = worker._dispatches_dir
        delivery_log = worker._delivery_log

        _write_dispatch_file(dispatches_dir, "dispatch-gemini-stats-lifecycle-20260226")

        # Poll -> deliver -> ack
        records = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        record = records[0]

        mock_result = _make_subprocess_result(returncode=0)
        with patch(
            "forge_harness.webhook_server.services.relay_worker.subprocess.run",
            return_value=mock_result,
        ):
            asyncio.get_event_loop().run_until_complete(worker.deliver(record))

        all_log_records = _read_log_records(delivery_log)
        delivered_record = next(r for r in all_log_records if r.state == DeliveryState.DELIVERED)
        asyncio.get_event_loop().run_until_complete(worker.ack(delivered_record.message_id))

        stats = worker.get_stats()

        # Three entries written to log: dispatched, delivered, acked
        assert stats["total"] == 3
        by_state = stats["by_state"]
        assert by_state.get("dispatched", 0) == 1
        assert by_state.get("delivered", 0) == 1
        assert by_state.get("acked", 0) == 1

    def test_relay_worker_stats_includes_adaptive_retry_section(self, tmp_path):
        """get_stats includes an adaptive_retry sub-dict from the retry handler."""
        worker = _make_worker(tmp_path)

        stats = worker.get_stats()

        assert "adaptive_retry" in stats
        adaptive = stats["adaptive_retry"]
        assert "total_tracked" in adaptive
        assert "by_category" in adaptive


# ---------------------------------------------------------------------------
# Test 7: Singleton lifecycle
# ---------------------------------------------------------------------------


class TestSingletonLifecycle:
    """Tests for get_relay_worker and reset_relay_worker."""

    def test_get_relay_worker_returns_same_instance(self, tmp_path):
        """Calling get_relay_worker twice returns the identical singleton."""
        dispatches_dir = _make_dispatches_dir(tmp_path)
        delivery_log = _make_delivery_log(tmp_path)

        w1 = get_relay_worker(
            dispatches_dir=dispatches_dir,
            delivery_log=delivery_log,
            agent_message_script=_make_script(tmp_path),
        )
        w2 = get_relay_worker()

        assert w1 is w2

    def test_reset_relay_worker_allows_new_configuration(self, tmp_path):
        """After reset, get_relay_worker creates a fresh instance."""
        dispatches_dir = _make_dispatches_dir(tmp_path)

        w1 = get_relay_worker(dispatches_dir=dispatches_dir, max_retries=5)
        reset_relay_worker()
        w2 = get_relay_worker(dispatches_dir=dispatches_dir, max_retries=2)

        assert w1 is not w2
        assert w2.max_retries == 2

    def test_worker_loads_delivered_stems_from_existing_log(self, tmp_path):
        """Worker startup pre-populates _delivered_stems from any existing delivery log."""
        delivery_log = _make_delivery_log(tmp_path)
        task_id = "dispatch-gemini-pre-existing-20260226"

        # Write a delivered record before creating the worker
        pre_existing = _make_delivered_record("msg_20260226_pre00001", task_id=task_id)
        _write_record_to_log(delivery_log, pre_existing)

        # Create worker — it should read the log on __init__
        worker = _make_worker(tmp_path)

        assert task_id in worker._delivered_stems

    def test_worker_does_not_reload_acked_files_on_restart(self, tmp_path):
        """A dispatch file whose task_id appears as acked in the log is skipped on poll."""
        delivery_log = _make_delivery_log(tmp_path)
        dispatches_dir = _make_dispatches_dir(tmp_path)
        task_id = "dispatch-gemini-acked-task-20260226"

        # Write an acked record for this task_id before creating the worker
        acked_record = DeliveryRecord(
            message_id="msg_20260226_acked001",
            task_id=task_id,
            source_node="prya",
            target_node="prya",
            state=DeliveryState.ACKED,
            created_at=datetime.now(UTC),
            acked_at=datetime.now(UTC),
            payload_summary="acked dispatch",
        )
        _write_record_to_log(delivery_log, acked_record)

        # Also put the .md file back (simulates a re-run without cleanup)
        _write_dispatch_file(dispatches_dir, task_id)

        worker = RelayWorker(
            dispatches_dir=dispatches_dir,
            delivery_log=delivery_log,
            agent_message_script=_make_script(tmp_path),
        )

        # poll_pending should skip the already-acked stem
        pending = asyncio.get_event_loop().run_until_complete(worker.poll_pending())
        assert len(pending) == 0

"""Queue-first Relay Worker Service
=====================================

Polls pending dispatch files from ``.forge/dispatches/``, attempts delivery
via ``forge dispatch send``, and persists delivery lifecycle events to
``.forge/relay/delivery_log.jsonl`` using the ``DeliveryRecord`` / ``DeliveryState``
state machine from ``forge_harness.models.delivery``.

State flow per message
-----------------------

    queued ──► dispatched ──► delivered ──► acked
       │            │              └──────► nacked
       │            └──────────────────► failed
       └──────────────────────────────► expired

Usage
-----
Start the worker as a background task::

    from forge_harness.webhook_server.services.relay_worker import get_relay_worker

    worker = get_relay_worker()
    await worker.run()  # long-lived; cancel to stop

Singleton factory::

    # First call configures the instance; subsequent calls return the same object.
    worker = get_relay_worker(poll_interval=10.0, max_retries=5)
    await worker.run()

Testing
-------
Reset the singleton between test cases with::

    from forge_harness.webhook_server.services.relay_worker import reset_relay_worker
    reset_relay_worker()
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.models.delivery import (
    DeliveryRecord,
    DeliveryState,
    DeliveryTransitionError,
    validate_delivery_transition,
)
from forge_harness.webhook_server.handlers.adaptive_retry_handler import (
    RetryStrategy,
    get_adaptive_retry_handler,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Filesystem constants
# ---------------------------------------------------------------------------

# Root of the FORGE repo — callers can override via FORGE_ROOT env var.
_FORGE_ROOT: Path = Path(
    os.environ.get("FORGE_ROOT", Path(__file__).resolve().parents[5])
)

_DISPATCHES_DIR: Path = _FORGE_ROOT / ".forge" / "dispatches"
_RELAY_DIR: Path = _FORGE_ROOT / ".forge" / "relay"
_DELIVERY_LOG: Path = _RELAY_DIR / "delivery_log.jsonl"
# agent-message.sh has been deleted; use forge CLI dispatch send instead
_FORGE_DISPATCH_CMD: str = "forge"

# Retry state sub-directory: one JSON file per message_id tracking attempt count.
_RETRY_DIR: Path = _RELAY_DIR / "retry"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """Return the current UTC time.  Isolated for easier test patching."""
    return datetime.now(UTC)


def _build_message_id() -> str:
    """Generate a unique message identifier."""
    ts = _now_utc().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"msg_{ts}_{short}"


def _ensure_dirs() -> None:
    """Create relay directories if they do not already exist."""
    _RELAY_DIR.mkdir(parents=True, exist_ok=True)
    _RETRY_DIR.mkdir(parents=True, exist_ok=True)


def _append_delivery_log(record: DeliveryRecord) -> None:
    """Append a delivery record as a single JSON line to the delivery log."""
    _ensure_dirs()
    line = record.model_dump_json() + "\n"
    with _DELIVERY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _read_delivery_log() -> list[DeliveryRecord]:
    """Read all records from the JSONL delivery log.

    Malformed lines are skipped with a warning.
    """
    if not _DELIVERY_LOG.exists():
        return []

    records: list[DeliveryRecord] = []
    with _DELIVERY_LOG.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                records.append(DeliveryRecord.model_validate(data))
            except Exception as exc:
                logger.warning(
                    "Skipping malformed delivery log line",
                    extra={"line": line_no, "error": str(exc)},
                )
    return records


def _get_retry_count(stem: str) -> int:
    """Return how many delivery attempts have been made for a dispatch *stem*."""
    path = _RETRY_DIR / f"{stem}.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("attempts", 0))
    except Exception:
        return 0


def _increment_retry_count(stem: str) -> int:
    """Increment and persist the attempt counter for a dispatch *stem*."""
    _ensure_dirs()
    path = _RETRY_DIR / f"{stem}.json"
    attempts = _get_retry_count(stem) + 1
    path.write_text(
        json.dumps({"stem": stem, "attempts": attempts}),
        encoding="utf-8",
    )
    return attempts


def _dispatch_file_to_record(dispatch_path: Path) -> DeliveryRecord:
    """Convert a dispatch file into an initial ``queued`` DeliveryRecord.

    The dispatch file name encodes minimal topology hints following the
    convention ``dispatch-{agent}-{slug}-{date}.md``.  When the format
    differs we fall back to safe defaults.
    """
    stem = dispatch_path.stem  # e.g. "dispatch-gemini-agent-registry"
    parts = stem.split("-")

    # Attempt to extract target agent from the second segment.
    target_agent: str | None = parts[1] if len(parts) >= 2 else None

    # Payload summary: first 120 characters of the file content.
    try:
        content = dispatch_path.read_text(encoding="utf-8", errors="replace")
        summary = content[:120]
    except OSError:
        summary = ""

    return DeliveryRecord(
        message_id=_build_message_id(),
        task_id=stem,
        source_node=os.uname().nodename,
        target_node=os.uname().nodename,  # local relay; same node by default
        target_agent=target_agent,
        state=DeliveryState.QUEUED,
        created_at=_now_utc(),
        payload_summary=summary,
    )


# ---------------------------------------------------------------------------
# RelayWorker
# ---------------------------------------------------------------------------


class RelayWorker:
    """Polls pending dispatch files and delivers them to agents.

    Args:
        poll_interval: Seconds to sleep between polling cycles.
        max_retries: Maximum delivery attempts before a record is marked
            *failed* permanently.
        dispatches_dir: Override the default ``.forge/dispatches/`` directory
            (useful in tests).
        delivery_log: Override the default delivery log path.
        agent_message_script: Override path to dispatch script (deprecated, ignored).
    """

    def __init__(
        self,
        poll_interval: float = 5.0,
        max_retries: int = 3,
        dispatches_dir: Path | None = None,
        delivery_log: Path | None = None,
        agent_message_script: Path | None = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.max_retries = max_retries

        self._dispatches_dir: Path = dispatches_dir or _DISPATCHES_DIR
        self._delivery_log: Path = delivery_log or _DELIVERY_LOG
        # agent_message_script is deprecated; kept for backward compat but ignored
        self._agent_script: Path = agent_message_script or Path(_FORGE_DISPATCH_CMD)

        # In-memory tracking of already-processed dispatch file stems so we
        # don't re-queue the same file on every poll cycle.
        self._delivered_stems: set[str] = set()

        # Populate from the existing log on startup so a restart does not
        # re-deliver messages.
        self._load_delivered_stems()

        # Adaptive retry handler for context-aware retries
        self._retry_handler = get_adaptive_retry_handler(max_retries=max_retries)

        self._stop_event = asyncio.Event()

        logger.info(
            "RelayWorker initialised",
            extra={
                "poll_interval": self.poll_interval,
                "max_retries": self.max_retries,
                "dispatches_dir": str(self._dispatches_dir),
            },
        )

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    def _load_delivered_stems(self) -> None:
        """Preload already-processed dispatch stems from the delivery log."""
        try:
            records = self._read_log()
        except Exception as exc:
            logger.warning(
                "Could not load existing delivery log on startup",
                extra={"error": str(exc)},
            )
            return

        for rec in records:
            if rec.state in (
                DeliveryState.DELIVERED,
                DeliveryState.ACKED,
                DeliveryState.NACKED,
            ):
                if rec.task_id:
                    self._delivered_stems.add(rec.task_id)

    def _read_log(self) -> list[DeliveryRecord]:
        """Read the delivery log (delegates to module helper, isolated for mocking)."""
        if not self._delivery_log.exists():
            return []

        records: list[DeliveryRecord] = []
        with self._delivery_log.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    records.append(DeliveryRecord.model_validate(data))
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed delivery log line",
                        extra={"line": line_no, "error": str(exc)},
                    )
        return records

    def _append_log(self, record: DeliveryRecord) -> None:
        """Append a record to the delivery log (isolated for mocking)."""
        self._delivery_log.parent.mkdir(parents=True, exist_ok=True)
        _RETRY_DIR.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json() + "\n"
        with self._delivery_log.open("a", encoding="utf-8") as fh:
            fh.write(line)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def poll_pending(self) -> list[DeliveryRecord]:
        """Scan ``.forge/dispatches/`` for undelivered ``.md`` files.

        Returns one ``DeliveryRecord`` (state=``queued``) per file that has
        not yet been successfully delivered in this process lifetime.
        Skips files that have exhausted their retry budget.
        """
        if not self._dispatches_dir.exists():
            logger.debug(
                "Dispatches directory does not exist; nothing to poll",
                extra={"dir": str(self._dispatches_dir)},
            )
            return []

        pending: list[DeliveryRecord] = []
        for path in sorted(self._dispatches_dir.glob("*.md")):
            stem = path.stem
            if stem in self._delivered_stems:
                continue

            # Skip stems that have exhausted retries
            if _get_retry_count(stem) >= self.max_retries:
                self._delivered_stems.add(stem)
                continue

            record = self._file_to_record(path)
            pending.append(record)
            logger.debug(
                "Found pending dispatch",
                extra={"file": path.name, "message_id": record.message_id},
            )

        logger.info(
            "Poll complete",
            extra={"pending_count": len(pending)},
        )
        return pending

    def _file_to_record(self, path: Path) -> DeliveryRecord:
        """Convert a dispatch file path to a queued DeliveryRecord."""
        return _dispatch_file_to_record(path)

    async def deliver(self, record: DeliveryRecord) -> bool:
        """Attempt to deliver *record* via ``forge dispatch send``.

        Transitions:
        - ``queued`` → ``dispatched`` (script invoked)
        - ``dispatched`` → ``delivered`` (script exits 0)
        - ``dispatched`` → ``failed`` (script exits non-zero or raises)

        Uses adaptive retry handler to generate enhanced retry messages with
        failure context instead of simply replaying the original prompt.

        Returns:
            ``True`` when delivery succeeded (script exit code 0).
            ``False`` on failure.
        """
        # Transition: queued → dispatched
        try:
            validate_delivery_transition(record.state, DeliveryState.DISPATCHED)
        except DeliveryTransitionError as exc:
            logger.error(
                "Cannot dispatch record: invalid state transition",
                extra={"message_id": record.message_id, "error": str(exc)},
            )
            return False

        dispatched_record = record.model_copy(
            update={
                "state": DeliveryState.DISPATCHED,
                "dispatched_at": _now_utc(),
            }
        )
        self._append_log(dispatched_record)

        # Build the shell command.  Target format expected by forge dispatch send:
        # ``<session>:<window>`` e.g. ``forge:gemini``.
        target = self._build_target(record)

        # Use enhanced retry message with failure context if this is a retry
        # Otherwise use the original payload summary
        message = self._retry_handler.prepare_retry_message(
            record=record,
            original_message=record.payload_summary or f"Dispatch: {record.task_id}",
        )
        # Trim to 120 chars to comply with dispatch send default limit.
        message = message[:120]

        cmd: list[str] = [
            "forge",
            "dispatch",
            "send",
            target,
            message,
        ]

        stem = record.task_id or record.message_id
        attempts = _increment_retry_count(stem)
        logger.info(
            "Delivering dispatch",
            extra={
                "message_id": record.message_id,
                "target": target,
                "attempt": attempts,
                "max_retries": self.max_retries,
            },
        )

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                ),
            )
            success = result.returncode == 0
        except Exception as exc:
            logger.error(
                "forge dispatch send raised an exception",
                extra={"message_id": record.message_id, "error": str(exc)},
            )
            success = False
            result = None

        if success:
            delivered_record = dispatched_record.model_copy(
                update={
                    "state": DeliveryState.DELIVERED,
                    "delivered_at": _now_utc(),
                }
            )
            self._append_log(delivered_record)
            if record.task_id:
                self._delivered_stems.add(record.task_id)
            # Clear retry context on successful delivery
            self._retry_handler.clear_context(record.message_id)
            logger.info(
                "Dispatch delivered",
                extra={"message_id": record.message_id, "target": target},
            )
            return True
        else:
            error_detail = ""
            if result is not None:
                error_detail = (result.stderr or result.stdout or "").strip()

            # Capture failure context for adaptive retry
            self._retry_handler.capture_failure(
                record=record,
                error_message=error_detail or "Delivery failed",
                agent_state={"target": target, "attempt": attempts},
            )

            failed_record = dispatched_record.model_copy(
                update={
                    "state": DeliveryState.FAILED,
                    "error": error_detail or "Delivery failed",
                }
            )
            self._append_log(failed_record)
            logger.warning(
                "Dispatch delivery failed",
                extra={
                    "message_id": record.message_id,
                    "target": target,
                    "attempt": attempts,
                    "error": error_detail,
                },
            )
            return False

    def _build_target(self, record: DeliveryRecord) -> str:
        """Build the ``session:window`` target string for forge dispatch send."""
        session = "forge"
        window = record.target_agent or "orchestrator"
        return f"{session}:{window}"

    async def ack(self, record_id: str) -> bool:
        """Mark the delivery record with *record_id* as acknowledged.

        Appends an acked entry to the delivery log.

        Returns:
            ``True`` when the record was found and acked successfully.
            ``False`` when no delivered record was found for *record_id* (404).
        """
        records = self._read_log()
        # Find the most recent record with this message_id in a delivered state.
        target: DeliveryRecord | None = None
        for rec in reversed(records):
            if rec.message_id == record_id and rec.state == DeliveryState.DELIVERED:
                target = rec
                break

        if target is None:
            logger.warning(
                "ack called for unknown or non-delivered record",
                extra={"message_id": record_id},
            )
            return False

        validate_delivery_transition(target.state, DeliveryState.ACKED)
        acked = target.model_copy(
            update={
                "state": DeliveryState.ACKED,
                "acked_at": _now_utc(),
            }
        )
        self._append_log(acked)
        logger.info("Record acked", extra={"message_id": record_id})
        return True

    async def nack(self, record_id: str, reason: str) -> bool:
        """Mark the delivery record with *record_id* as nacked.

        If the attempt count is below *max_retries*, the record's task_id
        stem is removed from ``_delivered_stems`` so it will be re-queued
        on the next poll cycle (retry).  Otherwise the record stays nacked.

        Uses adaptive retry handler for context-aware retry decisions.

        Returns:
            ``True`` when the record was found and nacked successfully.
            ``False`` when no delivered record was found for *record_id* (404).
        """
        records = self._read_log()
        target: DeliveryRecord | None = None
        for rec in reversed(records):
            if rec.message_id == record_id and rec.state == DeliveryState.DELIVERED:
                target = rec
                break

        if target is None:
            logger.warning(
                "nack called for unknown or non-delivered record",
                extra={"message_id": record_id},
            )
            return False

        # Capture failure context via adaptive retry handler
        self._retry_handler.capture_failure(
            record=target,
            error_message=reason,
            agent_state={"nack": True},
        )

        validate_delivery_transition(target.state, DeliveryState.NACKED)
        nacked = target.model_copy(
            update={
                "state": DeliveryState.NACKED,
                "acked_at": _now_utc(),
                "error": reason,
            }
        )
        self._append_log(nacked)

        # Check both legacy retry count and adaptive retry handler
        stem = target.task_id or record_id
        attempts = _get_retry_count(stem)
        should_retry_adaptive, strategy = self._retry_handler.should_retry(record_id)

        # Prioritize legacy retry count for backward compatibility with tests
        # If legacy count is below max_retries, allow retry regardless of adaptive handler
        if attempts < self.max_retries and strategy != RetryStrategy.SKIP:
            # Allow re-delivery on the next poll cycle.
            if target.task_id and target.task_id in self._delivered_stems:
                self._delivered_stems.discard(target.task_id)

            # Get retry delay from adaptive handler
            delay = self._retry_handler.get_retry_delay(record_id)

            logger.info(
                "Record nacked — will retry with adaptive strategy",
                extra={
                    "message_id": record_id,
                    "strategy": strategy.value,
                    "attempts": attempts,
                    "delay_seconds": delay,
                    "reason": reason,
                },
            )
        else:
            logger.warning(
                "Record nacked — max retries exhausted or escalation required; no further attempts",
                extra={
                    "message_id": record_id,
                    "strategy": strategy.value,
                    "attempts": attempts,
                    "reason": reason,
                },
            )

        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main relay loop: poll → deliver → sleep, repeating indefinitely.

        Cancel the enclosing ``asyncio.Task`` or call :meth:`stop` to stop
        the loop cleanly.
        The loop catches all delivery exceptions internally so a single bad
        dispatch file cannot halt the worker.
        """
        logger.info(
            "RelayWorker started",
            extra={"poll_interval": self.poll_interval, "max_retries": self.max_retries},
        )
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                pending = await self.poll_pending()
                for record in pending:
                    try:
                        await self.deliver(record)
                    except Exception as exc:
                        logger.error(
                            "Unhandled error delivering record",
                            extra={
                                "message_id": record.message_id,
                                "error": str(exc),
                            },
                        )
            except Exception as exc:
                logger.error(
                    "Unhandled error in relay poll cycle",
                    extra={"error": str(exc)},
                )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.poll_interval
                )
            except TimeoutError:
                continue

        logger.info("RelayWorker stopped")

    async def stop(self) -> None:
        """Signal the relay loop to stop."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return counts per delivery state across all records in the log.

        Returns:
            Dictionary with ``total`` and ``by_state`` keys, plus
            ``max_retries`` and ``poll_interval`` configuration values.
            Also includes adaptive retry handler statistics.
        """
        records = self._read_log()
        by_state: dict[str, int] = {s.value: 0 for s in DeliveryState}
        for rec in records:
            state_val = (
                rec.state.value if isinstance(rec.state, DeliveryState) else str(rec.state)
            )
            by_state[state_val] = by_state.get(state_val, 0) + 1

        # Include adaptive retry handler stats
        retry_stats = self._retry_handler.get_stats()

        return {
            "total": len(records),
            "by_state": {k: v for k, v in by_state.items() if v > 0},
            "poll_interval": self.poll_interval,
            "max_retries": self.max_retries,
            "adaptive_retry": retry_stats,
        }


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_worker_instance: RelayWorker | None = None
_worker_lock: Lock = Lock()


def get_relay_worker(
    poll_interval: float = 5.0,
    max_retries: int = 3,
    dispatches_dir: Path | None = None,
    delivery_log: Path | None = None,
    agent_message_script: Path | None = None,
) -> RelayWorker:
    """Return the global ``RelayWorker`` singleton.

    On the first call, all keyword arguments are used to configure the
    instance.  Subsequent calls return the cached instance and ignore any
    supplied arguments.

    To replace the singleton (e.g. in tests) call :func:`reset_relay_worker`
    first.

    Args:
        poll_interval: Seconds between polling cycles.
        max_retries: Maximum delivery attempts per message.
        dispatches_dir: Override the dispatch directory path.
        delivery_log: Override the delivery log path.
        agent_message_script: Deprecated (agent-message.sh has been deleted).

    Returns:
        The singleton :class:`RelayWorker` instance.
    """
    global _worker_instance
    with _worker_lock:
        if _worker_instance is None:
            _worker_instance = RelayWorker(
                poll_interval=poll_interval,
                max_retries=max_retries,
                dispatches_dir=dispatches_dir,
                delivery_log=delivery_log,
                agent_message_script=agent_message_script,
            )
        return _worker_instance


def reset_relay_worker() -> None:
    """Reset the singleton — intended for use in tests only."""
    global _worker_instance
    with _worker_lock:
        _worker_instance = None

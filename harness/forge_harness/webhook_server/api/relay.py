"""Relay Delivery API Endpoints

Exposes relay delivery records from the JSONL log maintained by RelayWorker,
and provides mutation endpoints for dispatching messages and managing
delivery acknowledgement.

Endpoints:
    GET  /api/relay/deliveries              - List delivery records with filters
    GET  /api/relay/deliveries/{message_id} - Get single delivery record
    GET  /api/relay/stats                   - Delivery state histogram and stats
    POST /api/relay/dispatch                - Dispatch a message to an agent via relay
    POST /api/relay/{message_id}/ack        - Acknowledge a delivered message
    POST /api/relay/{message_id}/nack       - Reject a delivered message (triggers retry)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge_harness.logging_config import get_logger
from forge_harness.models.delivery import DeliveryRecord, DeliveryState, DeliveryStats
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.services.relay_worker import get_relay_worker

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Filesystem constants — mirrors RelayWorker layout
# ---------------------------------------------------------------------------

_FORGE_ROOT: Path = Path(
    os.environ.get("FORGE_ROOT", str(Path(__file__).resolve().parents[5]))
)
_RELAY_DIR: Path = _FORGE_ROOT / ".forge" / "relay"
_DELIVERY_LOG: Path = _RELAY_DIR / "delivery_log.jsonl"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DeliveryListResponse(BaseModel):
    """Response model for the delivery list endpoint."""

    deliveries: list[dict[str, Any]]
    total: int
    filtered: int


class DeliveryStatsResponse(BaseModel):
    """Response model for the delivery stats endpoint."""

    total: int
    by_state: dict[str, int]
    mean_delivery_seconds: float | None
    poll_interval: float | None
    max_retries: int | None
    delivery_log_path: str


class RelayDispatchRequest(BaseModel):
    """Request model for dispatching a message via relay."""

    target_agent: str
    message: str
    task_id: str | None = None
    source_agent: str | None = None
    priority: Literal["low", "normal", "high", "critical"] = "normal"


class RelayAckRequest(BaseModel):
    """Request model for nacking a delivery (ack has no body)."""

    pass


class RelayNackRequest(BaseModel):
    """Request model for nacking a delivery."""

    reason: str = "Agent rejected delivery"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_delivery_log(log_path: Path | None = None) -> list[DeliveryRecord]:
    """Read all records from the JSONL delivery log.

    Malformed lines are logged and skipped.

    Args:
        log_path: Override the default delivery log path (used in tests).

    Returns:
        List of parsed DeliveryRecord instances in file order.
    """
    path = log_path or _DELIVERY_LOG
    if not path.exists():
        return []

    records: list[DeliveryRecord] = []
    with path.open("r", encoding="utf-8") as fh:
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


def _deduplicate_records(records: list[DeliveryRecord]) -> list[DeliveryRecord]:
    """Return only the most-recent record per message_id.

    The JSONL log appends state transitions as separate lines; we surface the
    latest state for each message to callers so every message appears once.
    """
    seen: dict[str, DeliveryRecord] = {}
    for rec in records:
        seen[rec.message_id] = rec  # later entries overwrite earlier ones
    return list(seen.values())


def _record_to_dict(record: DeliveryRecord) -> dict[str, Any]:
    """Serialize a DeliveryRecord to a JSON-safe dict."""
    data = record.model_dump()
    # Ensure state is a plain string
    if hasattr(data.get("state"), "value"):
        data["state"] = data["state"].value
    # Convert datetime fields to ISO strings
    for field in ("created_at", "dispatched_at", "delivered_at", "acked_at", "expires_at"):
        val = data.get(field)
        if val is not None and hasattr(val, "isoformat"):
            data[field] = val.isoformat()
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/relay/deliveries")
async def list_deliveries(
    state: str | None = Query(None, description="Filter by delivery state (e.g. queued, delivered)"),
    agent: str | None = Query(None, description="Filter by target_agent name"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    user: dict = Depends(require_permission("operator")),
) -> JSONResponse:
    """List delivery records from the relay JSONL log.

    Records are deduplicated to show the most-recent state per message_id.
    Results are ordered newest-first based on file position.

    Query parameters:
        state:  Optional filter by DeliveryState value.
        agent:  Optional filter by target_agent field.
        limit:  Maximum number of records returned (default 100, max 1000).
    """
    # Validate state filter if provided
    if state is not None:
        valid_states = {s.value for s in DeliveryState}
        if state not in valid_states:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid state '{state}'. Valid values: {sorted(valid_states)}",
            )

    all_records = _read_delivery_log()
    deduped = _deduplicate_records(all_records)

    # Apply filters
    filtered = deduped
    if state is not None:
        filtered = [
            r for r in filtered
            if (r.state.value if isinstance(r.state, DeliveryState) else str(r.state)) == state
        ]
    if agent is not None:
        filtered = [r for r in filtered if r.target_agent == agent]

    # Newest-first — reverse so most-recent messages come first
    filtered = list(reversed(filtered))

    # Apply limit
    limited = filtered[:limit]

    return JSONResponse(
        {
            "deliveries": [_record_to_dict(r) for r in limited],
            "total": len(deduped),
            "filtered": len(filtered),
        }
    )


@router.get("/api/relay/deliveries/{message_id}")
async def get_delivery(
    message_id: str,
    user: dict = Depends(require_permission("operator")),
) -> JSONResponse:
    """Get the most-recent delivery record for a specific message_id.

    Returns the latest state entry for the message, which reflects the
    current position in the delivery state machine.

    Raises:
        404: When no record with the given message_id exists in the log.
    """
    all_records = _read_delivery_log()

    # Find the latest entry for this message_id
    target: DeliveryRecord | None = None
    for rec in reversed(all_records):
        if rec.message_id == message_id:
            target = rec
            break

    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"Delivery record '{message_id}' not found",
        )

    # Also collect all historical state entries for this message
    history = [_record_to_dict(r) for r in all_records if r.message_id == message_id]

    return JSONResponse(
        {
            "delivery": _record_to_dict(target),
            "history": history,
        }
    )


@router.get("/api/relay/stats")
async def get_stats(
    user: dict = Depends(require_permission("operator")),
) -> JSONResponse:
    """Return delivery state histogram and aggregate statistics.

    Reads the relay delivery log and computes:
    - Total record count (deduplicated by message_id)
    - Count per DeliveryState value
    - Mean delivery time (created_at to acked_at) for resolved messages

    Also attempts to surface RelayWorker configuration (poll_interval,
    max_retries) from the running singleton when available.
    """
    all_records = _read_delivery_log()
    deduped = _deduplicate_records(all_records)

    stats = DeliveryStats.from_records(deduped)

    # Optionally enrich with RelayWorker runtime config
    poll_interval: float | None = None
    max_retries: int | None = None
    try:
        worker = get_relay_worker()
        poll_interval = worker.poll_interval
        max_retries = worker.max_retries
    except Exception:
        pass  # Worker not running or not yet initialised; omit config fields

    return JSONResponse(
        {
            "total": stats.total,
            "by_state": stats.by_state,
            "mean_delivery_seconds": stats.mean_delivery_seconds,
            "poll_interval": poll_interval,
            "max_retries": max_retries,
            "delivery_log_path": str(_DELIVERY_LOG),
        }
    )


@router.post("/api/relay/dispatch")
async def dispatch_message(
    request: RelayDispatchRequest,
    user: dict = Depends(require_permission("operator")),
) -> JSONResponse:
    """Dispatch a message to an agent via the relay worker.

    Creates a DeliveryRecord, enqueues it, and attempts immediate delivery.
    """
    import uuid
    from datetime import UTC, datetime

    worker = get_relay_worker()

    message_id = f"msg_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    record = DeliveryRecord(
        message_id=message_id,
        task_id=request.task_id,
        source_node=os.uname().nodename,
        target_node=os.uname().nodename,
        source_agent=request.source_agent,
        target_agent=request.target_agent,
        state=DeliveryState.QUEUED,
        created_at=datetime.now(UTC),
        payload_summary=request.message[:120],
    )

    # Log the queued record
    worker._append_log(record)

    # Attempt immediate delivery
    delivered = await worker.deliver(record)

    return JSONResponse(
        status_code=201,
        content={
            "message_id": message_id,
            "state": "delivered" if delivered else "failed",
            "target_agent": request.target_agent,
        },
    )


@router.post("/api/relay/{message_id}/ack")
async def ack_delivery(
    message_id: str,
    user: dict = Depends(require_permission("operator")),
) -> JSONResponse:
    """Acknowledge a delivered message.

    Transitions the delivery record from delivered to acked.
    Returns 404 if message not found, 409 if not in delivered state.
    """
    worker = get_relay_worker()

    # Use worker's log reader so tests can mock it consistently
    records = worker._read_log()
    target = None
    for rec in reversed(records):
        if rec.message_id == message_id:
            target = rec
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Delivery record '{message_id}' not found")

    if target.state != DeliveryState.DELIVERED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot ack record in state '{target.state.value}'; must be 'delivered'",
        )

    await worker.ack(message_id)

    return JSONResponse({"message_id": message_id, "state": "acked"})


@router.post("/api/relay/{message_id}/nack")
async def nack_delivery(
    message_id: str,
    request: RelayNackRequest,
    user: dict = Depends(require_permission("operator")),
) -> JSONResponse:
    """Reject a delivered message and optionally trigger retry.

    Transitions delivered to nacked. If retries remain, the message
    will be re-queued on the next relay worker poll cycle.
    Returns 404 if message not found, 409 if not in delivered state.
    """
    worker = get_relay_worker()

    # Use worker's log reader so tests can mock it consistently
    records = worker._read_log()
    target = None
    for rec in reversed(records):
        if rec.message_id == message_id:
            target = rec
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Delivery record '{message_id}' not found")

    if target.state != DeliveryState.DELIVERED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot nack record in state '{target.state.value}'; must be 'delivered'",
        )

    await worker.nack(message_id, request.reason)

    return JSONResponse({"message_id": message_id, "state": "nacked", "reason": request.reason})

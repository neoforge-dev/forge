"""Heartbeat Loop API Endpoints

REST endpoints for querying, evaluating, and resetting the heartbeat loop state.

Endpoints:
    GET  /api/heartbeat-loop/state  — Read-only eval (no increment)
    POST /api/heartbeat-loop/eval   — Increment counters + emit SSE
    POST /api/heartbeat-loop/reset  — Reset all counters to initial state
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from forge_harness.error_schema import api_success_body
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# FORGE root resolution
# heartbeat_loop.py  → parents[0] = api/
#                    → parents[1] = webhook_server/
#                    → parents[2] = forge_harness/
#                    → parents[3] = harness/
#                    → parents[4] = FORGE root
# ---------------------------------------------------------------------------

_FORGE_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_service():
    """Lazy import of heartbeat loop service singleton to avoid circular imports."""
    from forge_harness.heartbeat_loop_service import get_heartbeat_loop_service

    return get_heartbeat_loop_service(forge_root=_FORGE_ROOT)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/heartbeat-loop/state")
async def get_heartbeat_loop_state():
    """Return the current heartbeat loop state without incrementing counters.

    This is a read-only snapshot of the loop's evaluation result.  No side
    effects are triggered (counters stay unchanged, no SSE is emitted).

    Returns:
        HeartbeatLoopResult dict with current loop state.
    """
    service = _get_service()
    result = service.eval(increment=False)
    return JSONResponse(content=api_success_body(result.model_dump(mode="json")))


@router.post("/api/heartbeat-loop/eval")
async def eval_heartbeat_loop():
    """Evaluate the heartbeat loop, increment counters, and emit an SSE event.

    Increments the internal tick counter and broadcasts the result over the
    Server-Sent Events channel.  SSE emission is best-effort; failures are
    swallowed and logged so that the HTTP response always succeeds.

    Returns:
        HeartbeatLoopResult dict reflecting the updated loop state.
    """
    service = _get_service()
    result = service.eval(increment=True)

    try:
        service.emit_sse(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("heartbeat_loop SSE emit failed (best-effort): %s", exc)

    return JSONResponse(content=api_success_body(result.model_dump(mode="json")))


@router.post("/api/heartbeat-loop/reset")
async def reset_heartbeat_loop():
    """Reset all heartbeat loop counters to their initial state.

    Useful for clearing stale data between test runs or after a manual
    intervention.  The returned state reflects the post-reset snapshot.

    Returns:
        HeartbeatLoopState dict with zeroed counters.
    """
    service = _get_service()
    state = service.reset()
    return JSONResponse(content=api_success_body(state.model_dump(mode="json")))

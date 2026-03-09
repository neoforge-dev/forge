"""Lead State API Endpoints

REST endpoints for reading the Lead Orchestrator FSM state file and the
human attention required marker.

Endpoints:
    GET  /api/lead-state            — Return current lead FSM state
    GET  /api/lead-state/attention  — Check if HUMAN_ATTENTION_REQUIRED marker exists
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# FORGE root resolution
# lead_state.py  → parents[0] = api/
#                → parents[1] = webhook_server/
#                → parents[2] = forge_harness/
#                → parents[3] = harness/
#                → parents[4] = FORGE root
# ---------------------------------------------------------------------------

_FORGE_ROOT = Path(__file__).resolve().parents[4]
_LEAD_STATE_PATH = _FORGE_ROOT / ".forge" / "heartbeat" / "lead_state.json"
_HUMAN_ATTENTION_PATH = _FORGE_ROOT / ".forge" / "heartbeat" / "HUMAN_ATTENTION_REQUIRED"
_TRANSITION_LOG_PATH = _FORGE_ROOT / ".forge" / "heartbeat" / "transition_log.jsonl"
_LEAD_NUDGE_PATH = _FORGE_ROOT / ".forge" / "heartbeat" / "lead_nudge.json"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/lead-state")
async def get_lead_state() -> JSONResponse:
    """Return the current lead orchestrator FSM state.

    Reads .forge/heartbeat/lead_state.json and returns its contents directly.
    Always returns HTTP 200 — callers should inspect the ``state`` field.

    Returns:
        The raw JSON object from lead_state.json, or a fallback dict if the
        file is missing or contains invalid JSON.
    """
    if not _LEAD_STATE_PATH.exists():
        logger.debug("lead_state.json not found at %s", _LEAD_STATE_PATH)
        return JSONResponse(
            content={"state": "UNKNOWN", "error": "State file not found"}
        )

    try:
        raw = _LEAD_STATE_PATH.read_text(encoding="utf-8")
        data: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("lead_state.json is not valid JSON: %s", exc)
        return JSONResponse(
            content={"state": "UNKNOWN", "error": "Invalid state file"}
        )
    except OSError as exc:
        logger.warning("Failed to read lead_state.json: %s", exc)
        return JSONResponse(
            content={"state": "UNKNOWN", "error": "State file not found"}
        )

    return JSONResponse(content=data)


@router.get("/api/lead-state/attention")
async def get_human_attention() -> JSONResponse:
    """Check if the HUMAN_ATTENTION_REQUIRED marker exists.

    When the marker file is present its contents are read and returned in the
    ``messages`` list.  Each non-empty line becomes one message entry.

    Returns:
        ``{"attention_required": bool, "messages": list[str]}``
    """
    if not _HUMAN_ATTENTION_PATH.exists():
        return JSONResponse(content={"attention_required": False, "messages": []})

    messages: list[str] = []
    try:
        raw = _HUMAN_ATTENTION_PATH.read_text(encoding="utf-8")
        messages = [line.strip() for line in raw.splitlines() if line.strip()]
    except OSError as exc:
        logger.warning("Failed to read HUMAN_ATTENTION_REQUIRED: %s", exc)
        # File exists but is unreadable — still signal attention required
        messages = []

    return JSONResponse(content={"attention_required": True, "messages": messages})


@router.get("/api/lead-state/transitions")
async def get_lead_state_transitions() -> JSONResponse:
    """Return the last 20 FSM state transitions from transition_log.jsonl.

    Reads the JSONL file line by line, parses each as JSON, and returns
    the most recent 20 entries in reverse chronological order.

    Returns:
        ``{"transitions": list[dict], "total": int}``
    """
    if not _TRANSITION_LOG_PATH.exists():
        return JSONResponse(content={"transitions": [], "total": 0})

    transitions: list[dict] = []
    try:
        raw = _TRANSITION_LOG_PATH.read_text(encoding="utf-8")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                transitions.append(entry)
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("Failed to read transition_log.jsonl: %s", exc)
        return JSONResponse(content={"transitions": [], "total": 0})

    total = len(transitions)
    # Return last 20 in reverse chronological order
    recent = list(reversed(transitions[-20:]))

    return JSONResponse(content={"transitions": recent, "total": total})


@router.get("/api/lead-state/nudge")
async def get_lead_nudge() -> JSONResponse:
    """Return the current lead nudge state.

    Reads .forge/heartbeat/lead_nudge.json and returns its contents.
    The nudge file contains: idle agents, pending tasks, trigger states,
    context percent, lead FSM state, and xnode message count.

    Returns:
        The raw JSON object from lead_nudge.json, or a fallback dict.
    """
    if not _LEAD_NUDGE_PATH.exists():
        return JSONResponse(
            content={"error": "Nudge file not found", "idle_agents": 0, "action_needed": False}
        )

    try:
        raw = _LEAD_NUDGE_PATH.read_text(encoding="utf-8")
        data: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("lead_nudge.json is not valid JSON: %s", exc)
        return JSONResponse(
            content={"error": "Invalid nudge file", "idle_agents": 0, "action_needed": False}
        )
    except OSError as exc:
        logger.warning("Failed to read lead_nudge.json: %s", exc)
        return JSONResponse(
            content={"error": "Nudge file not readable", "idle_agents": 0, "action_needed": False}
        )

    return JSONResponse(content=data)

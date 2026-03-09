"""Session management API endpoints.

Extracted from webhook_server_main for modularity.
Monitor spawned tmux agent sessions: list, details, metadata, output.
Includes SSE streaming endpoint for real-time session output.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from forge_harness.webhook_server.core.dependencies import verify_auth

logger = logging.getLogger(__name__)

router = APIRouter()

# SSE streaming constants
_SSE_POLL_INTERVAL_SECONDS = 1.0
_SSE_KEEPALIVE_INTERVAL_SECONDS = 15


def api_response(
    data: Any = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Create a standardized API response."""
    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/api/sessions/active")
async def list_active_sessions(
    _: None = Depends(verify_auth),
):
    """List all active tmux agent sessions.

    Returns information about all spawned agent sessions running in tmux,
    including terminal output snippets and detected status.
    """
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()
    summary = tracker.get_sessions_summary()
    sessions_list = summary["sessions"]

    # Calculate stats
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}

    for session in sessions_list:
        # Add context_usage based on terminal_output length
        if session.get("terminal_output"):
            output_len = len(session["terminal_output"])
            session["context_usage"] = min(100, int((output_len / 500) * 100))
        else:
            session["context_usage"] = 0

        # Count by status
        status = session.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1

        # Count by agent type
        agent_type = session.get("agent_type", "unknown")
        by_type[agent_type] = by_type.get(agent_type, 0) + 1

    response_data = {
        "sessions": sessions_list,
        "total": len(sessions_list),
        "by_status": by_status,
        "by_type": by_type,
    }

    return JSONResponse(content=api_response(response_data))


@router.get("/api/sessions/{window_name}")
async def get_session_details(
    window_name: str,
    _: None = Depends(verify_auth),
):
    """Get details for a specific tmux session by window name."""
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()
    session = tracker.get_session(window_name)

    if session is None:
        return JSONResponse(
            content=api_response(
                None,
                error_code="not_found",
                error_message=f"Session '{window_name}' not found",
            ),
            status_code=404,
        )

    return JSONResponse(content=api_response(asdict(session)))


@router.post("/api/sessions/{window_name}/metadata")
async def update_session_metadata_post(
    window_name: str,
    body: dict[str, Any],
    _: None = Depends(verify_auth),
):
    """Update metadata for a tmux session (POST).

    Can set domain, project, task, and other metadata fields.
    """
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()
    tracker.save_session_metadata(window_name, body)
    return JSONResponse(content=api_response({"updated": True, "window": window_name}))


@router.put("/api/sessions/{window_name}/metadata")
async def update_session_metadata_put(
    window_name: str,
    body: dict[str, Any],
    _: None = Depends(verify_auth),
):
    """Update metadata for a tmux session (PUT).

    Can set domain, project, task, and other metadata fields.
    """
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()
    tracker.save_session_metadata(window_name, body)
    return JSONResponse(content=api_response({"updated": True, "window": window_name}))


@router.get("/api/sessions/{window_name}/output")
async def get_session_output(
    window_name: str,
    lines: int = 50,
    _: None = Depends(verify_auth),
):
    """Get recent terminal output from a tmux session.

    Args:
        window_name: The tmux window name
        lines: Number of lines to retrieve (default 50, max 200)
    """
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()
    lines = min(lines, 200)
    output = tracker.get_session_output(window_name, lines)

    if not output and window_name not in tracker.list_tmux_sessions():
        return JSONResponse(
            content=api_response(
                None,
                error_code="not_found",
                error_message=f"Session '{window_name}' not found",
            ),
            status_code=404,
        )

    return JSONResponse(
        content=api_response({"window": window_name, "lines": lines, "output": output})
    )


async def _session_output_generator(
    window_name: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Generate SSE events from live tmux session output.

    Polls the session every second and emits new lines as SSE data events.
    Sends a keepalive comment every 15 seconds when there is no new output.

    Args:
        window_name: The tmux window name to stream.
        request: The FastAPI request, used to detect client disconnection.

    Yields:
        Formatted SSE frames as strings.
    """
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()

    # Seed with the current snapshot so we only emit *new* lines going forward.
    last_output: str = tracker.get_session_output(window_name, lines=200)
    last_seen_lines: list[str] = last_output.splitlines() if last_output else []

    last_keepalive_at: float = asyncio.get_event_loop().time()

    try:
        while True:
            # Honour client disconnect without blocking forever.
            if await request.is_disconnected():
                logger.debug("SSE client disconnected from session '%s'", window_name)
                break

            current_output: str = tracker.get_session_output(window_name, lines=200)
            current_lines: list[str] = current_output.splitlines() if current_output else []

            # Determine which lines are new since the last poll.
            new_lines: list[str] = _diff_lines(last_seen_lines, current_lines)
            last_seen_lines = current_lines

            now = asyncio.get_event_loop().time()

            if new_lines:
                timestamp = datetime.now(UTC).isoformat()
                for line in new_lines:
                    payload = json.dumps({"line": line, "timestamp": timestamp})
                    yield f"data: {payload}\n\n"
                last_keepalive_at = now
            elif now - last_keepalive_at >= _SSE_KEEPALIVE_INTERVAL_SECONDS:
                # Send a keepalive comment to prevent proxies from closing the connection.
                yield ": keepalive\n\n"
                last_keepalive_at = now

            await asyncio.sleep(_SSE_POLL_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled for session '%s'", window_name)
    except Exception:
        logger.exception("Unexpected error in SSE stream for session '%s'", window_name)


def _diff_lines(previous: list[str], current: list[str]) -> list[str]:
    """Return the lines in *current* that are beyond the end of *previous*.

    Uses a simple suffix-match strategy: find the longest suffix of *previous*
    that is a prefix of *current*, then return everything after that overlap.
    This handles the common case where tmux capture-pane returns a rolling
    buffer in which older lines are preserved and new lines are appended.

    Args:
        previous: Lines captured during the last poll cycle.
        current: Lines captured during the current poll cycle.

    Returns:
        The newly-appended lines, or an empty list if nothing changed.
    """
    if not previous:
        return current

    # Fast path: identical snapshots.
    if current == previous:
        return []

    prev_len = len(previous)
    cur_len = len(current)

    # Walk backwards through *previous* to find the longest overlap.
    for overlap in range(min(prev_len, cur_len), 0, -1):
        if previous[-overlap:] == current[:overlap]:
            return current[overlap:]

    # No overlap found — treat the whole snapshot as new output.
    return current


@router.get("/api/sessions/{window_name}/stream")
async def stream_session_output(
    window_name: str,
    request: Request,
    _: None = Depends(verify_auth),
):
    """Stream live terminal output from a tmux session via Server-Sent Events.

    Opens a long-lived HTTP connection and pushes new terminal lines as they
    appear.  Each event has the following SSE format::

        data: {"line": "<output line>", "timestamp": "<ISO-8601 UTC>"}\\n\\n

    Keepalive comments are sent every 15 seconds when the session is idle::

        : keepalive\\n\\n

    The stream ends when the client closes the connection.

    Args:
        window_name: The tmux window name to stream (e.g. ``"cursor"``).
        request: Injected by FastAPI — used to detect client disconnection.

    Returns:
        A ``StreamingResponse`` with ``Content-Type: text/event-stream``.

    Raises:
        404: If the session does not exist at the time the request is made.
    """
    from forge_harness.session_tracker import get_session_tracker

    tracker = get_session_tracker()

    # Validate that the session exists before opening the stream.
    if window_name not in tracker.list_tmux_sessions():
        return JSONResponse(
            content=api_response(
                None,
                error_code="not_found",
                error_message=f"Session '{window_name}' not found",
            ),
            status_code=404,
        )

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # Disable Nginx proxy buffering.
    }

    return StreamingResponse(
        _session_output_generator(window_name, request),
        media_type="text/event-stream",
        headers=headers,
    )

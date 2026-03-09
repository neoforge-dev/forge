"""
Ralph Loop API Router

Handles Ralph Loop state management and control operations.
Ralph Loop is the autonomous feature development loop that uses
reinforcement learning to implement features automatically.

Endpoints:
- GET /api/ralph-loop/status    - Get Ralph Loop state from checkpoints
- GET /api/ralph-loop/decisions - Get Ralph Loop decision history
- POST /api/ralph-loop/start    - Start Ralph Loop
- POST /api/ralph-loop/pause    - Pause Ralph Loop
- POST /api/ralph-loop/stop     - Stop Ralph Loop
"""

import json
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from forge_harness.webhook_server.core.dependencies import verify_auth

router = APIRouter()


def api_response(data):
    """Create standardized API response."""
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def get_approval_handler():
    """Get global approval handler instance."""
    from forge_harness.webhook_server.handlers.approval_handler import get_approval_handler
    return get_approval_handler()


# Endpoints
@router.get("/api/ralph-loop/status")
async def get_ralph_loop_status(
    _: None = Depends(verify_auth),
):
    """Get Ralph Loop state from checkpoints.

    Reads most recent Ralph Loop checkpoint to determine
    current iteration, status, and progress.

    Returns:
        APIResponse with:
        - active: bool (whether loop is running)
        - iteration: Current iteration number
        - last_decision: Last decision made
        - started_at: When loop started
        - features: Feature statistics
    """
    try:
        approval_handler = get_approval_handler()
        if approval_handler is None:
            raise HTTPException(status_code=503, detail="Approval handler not available")

        # Look for .forge/ralph_checkpoints directory
        checkpoint_dir = approval_handler._forge_root / ".forge/ralph_checkpoints"

        if not checkpoint_dir.exists():
            return JSONResponse(
                content=api_response(
                    {
                        "active": False,
                        "iteration": 0,
                        "last_decision": None,
                        "started_at": None,
                        "features": {
                            "pending": 0,
                            "in_progress": 0,
                            "passing": 0,
                            "failing": 0,
                            "blocked": 0,
                        },
                        "message": "No Ralph Loop checkpoints found",
                    }
                )
            )

        # Find most recent checkpoint
        checkpoints = sorted(
            checkpoint_dir.glob("checkpoint_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not checkpoints:
            return JSONResponse(
                content=api_response(
                    {
                        "active": False,
                        "iteration": 0,
                        "last_decision": None,
                        "started_at": None,
                        "features": {
                            "pending": 0,
                            "in_progress": 0,
                            "passing": 0,
                            "failing": 0,
                            "blocked": 0,
                        },
                        "message": "No checkpoints found",
                    }
                )
            )

        # Read most recent checkpoint
        latest_checkpoint = json.loads(checkpoints[0].read_text())

        # Determine if loop is active (checkpoint less than 5 minutes old)
        checkpoint_age = time.time() - checkpoints[0].stat().st_mtime
        active = checkpoint_age < 300  # 5 minutes

        return JSONResponse(
            content=api_response(
                {
                    "active": active,
                    "iteration": latest_checkpoint.get("iteration", 0),
                    "last_decision": latest_checkpoint.get("last_decision"),
                    "started_at": latest_checkpoint.get("start_time"),
                    "features": latest_checkpoint.get("stats", {}),
                    "checkpoint_file": checkpoints[0].name,
                    "checkpoint_age_seconds": int(checkpoint_age),
                }
            )
        )

    except Exception as e:
        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.error(f"Error reading Ralph Loop status: {e}")
        return JSONResponse(
            content=api_response(
                error_code="READ_ERROR",
                error_message=f"Failed to read Ralph Loop state: {str(e)}",
            )
        )


@router.get("/api/ralph-loop/decisions")
async def get_ralph_loop_decisions(
    limit: int = Query(50, ge=1, le=200, description="Max decisions to return"),
    _: None = Depends(verify_auth),
):
    """Get Ralph Loop decision history.

    Reads decision records from checkpoint files to show
    history of decisions made by Ralph Loop.

    Args:
        limit: Maximum number of decisions to return

    Returns:
        APIResponse with:
        - decisions: List of decision records
    """
    try:
        approval_handler = get_approval_handler()
        if approval_handler is None:
            raise HTTPException(status_code=503, detail="Approval handler not available")

        checkpoint_dir = approval_handler._forge_root / ".forge/ralph_checkpoints"

        if not checkpoint_dir.exists():
            return JSONResponse(
                content=api_response(
                    {
                        "decisions": [],
                        "message": "No Ralph Loop checkpoints found",
                    }
                )
            )

        # Collect all decisions from checkpoint files
        decisions = []

        # Read checkpoint files (sorted by modification time, newest first)
        checkpoints = sorted(
            checkpoint_dir.glob("checkpoint_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for checkpoint_file in checkpoints:
            try:
                checkpoint_data = json.loads(checkpoint_file.read_text())

                # Extract decision from checkpoint
                if "last_decision" in checkpoint_data and checkpoint_data["last_decision"]:
                    decision = checkpoint_data["last_decision"]

                    # Ensure decision has required fields
                    decision_record = {
                        "id": decision.get("id", str(uuid.uuid4())),
                        "timestamp": decision.get(
                            "timestamp",
                            datetime.fromtimestamp(
                                checkpoint_file.stat().st_mtime, tz=UTC
                            ).isoformat(),
                        ),
                        "decision_type": decision.get(
                            "decision_type", decision.get("type", "unknown")
                        ),
                        "outcome": decision.get("outcome", "unknown"),
                        "confidence": decision.get("confidence", 0.0),
                        "task": decision.get("task") or decision.get("feature"),
                    }

                    decisions.append(decision_record)

                    if len(decisions) >= limit:
                        break

            except Exception as e:
                from forge_harness.logging_config import get_logger
                logger = get_logger(__name__)
                logger.warning(f"Failed to parse checkpoint {checkpoint_file.name}: {e}")
                continue

        return JSONResponse(
            content=api_response(
                {
                    "decisions": decisions,
                    "count": len(decisions),
                }
            )
        )

    except Exception as e:
        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.error(f"Error reading Ralph Loop decisions: {e}")
        return JSONResponse(
            content=api_response(
                error_code="READ_ERROR",
                error_message=f"Failed to read decision history: {str(e)}",
            )
        )


@router.post("/api/ralph-loop/start")
async def start_ralph_loop(
    _: None = Depends(verify_auth),
):
    """Start Ralph Loop.

    Updates Ralph Loop state to 'active'. The actual loop process
    should check this state and begin execution.

    Returns:
        APIResponse with:
        - success: Whether state was updated
        - status: New status ('active')
    """
    try:
        approval_handler = get_approval_handler()
        if approval_handler is None:
            raise HTTPException(status_code=503, detail="Approval handler not available")

        checkpoint_dir = approval_handler._forge_root / ".forge/ralph_checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        state_file = checkpoint_dir / "ralph_state.json"

        # Read existing state or create new
        if state_file.exists():
            state = json.loads(state_file.read_text())
        else:
            state = {
                "iteration": 0,
                "features": {
                    "pending": 0,
                    "in_progress": 0,
                    "passing": 0,
                    "failing": 0,
                    "blocked": 0,
                },
            }

        # Update status
        state["status"] = "active"
        state["last_updated"] = datetime.now(UTC).isoformat()
        state["started_at"] = state.get("started_at") or datetime.now(UTC).isoformat()

        # Write state
        state_file.write_text(json.dumps(state, indent=2))

        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Ralph Loop started via API")

        return JSONResponse(
            content=api_response(
                {
                    "success": True,
                    "status": "active",
                    "message": "Ralph Loop started",
                }
            )
        )

    except Exception as e:
        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.error(f"Error starting Ralph Loop: {e}")
        return JSONResponse(
            content=api_response(
                error_code="STATE_UPDATE_ERROR",
                error_message=f"Failed to start Ralph Loop: {str(e)}",
            )
        )


@router.post("/api/ralph-loop/pause")
async def pause_ralph_loop(
    _: None = Depends(verify_auth),
):
    """Pause Ralph Loop.

    Updates Ralph Loop state to 'paused'. The actual loop process
    should check this state and pause execution.

    Returns:
        APIResponse with:
        - success: Whether state was updated
        - status: New status ('paused')
    """
    try:
        approval_handler = get_approval_handler()
        if approval_handler is None:
            raise HTTPException(status_code=503, detail="Approval handler not available")

        checkpoint_dir = approval_handler._forge_root / ".forge/ralph_checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        state_file = checkpoint_dir / "ralph_state.json"

        # Read existing state
        if state_file.exists():
            state = json.loads(state_file.read_text())
        else:
            state = {
                "iteration": 0,
                "features": {
                    "pending": 0,
                    "in_progress": 0,
                    "passing": 0,
                    "failing": 0,
                    "blocked": 0,
                },
            }

        # Update status
        state["status"] = "paused"
        state["last_updated"] = datetime.now(UTC).isoformat()
        state["paused_at"] = datetime.now(UTC).isoformat()

        # Write state
        state_file.write_text(json.dumps(state, indent=2))

        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Ralph Loop paused via API")

        return JSONResponse(
            content=api_response(
                {
                    "success": True,
                    "status": "paused",
                    "message": "Ralph Loop paused",
                }
            )
        )

    except Exception as e:
        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.error(f"Error pausing Ralph Loop: {e}")
        return JSONResponse(
            content=api_response(
                error_code="STATE_UPDATE_ERROR",
                error_message=f"Failed to pause Ralph Loop: {str(e)}",
            )
        )


@router.post("/api/ralph-loop/stop")
async def stop_ralph_loop(
    _: None = Depends(verify_auth),
):
    """Stop Ralph Loop.

    Updates Ralph Loop state to 'stopped'. The actual loop process
    should check this state and stop execution gracefully.

    Returns:
        APIResponse with:
        - success: Whether state was updated
        - status: New status ('stopped')
    """
    try:
        approval_handler = get_approval_handler()
        if approval_handler is None:
            raise HTTPException(status_code=503, detail="Approval handler not available")

        checkpoint_dir = approval_handler._forge_root / ".forge/ralph_checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        state_file = checkpoint_dir / "ralph_state.json"

        # Read existing state
        if state_file.exists():
            state = json.loads(state_file.read_text())
        else:
            state = {
                "iteration": 0,
                "features": {
                    "pending": 0,
                    "in_progress": 0,
                    "passing": 0,
                    "failing": 0,
                    "blocked": 0,
                },
            }

        # Update status
        state["status"] = "stopped"
        state["last_updated"] = datetime.now(UTC).isoformat()
        state["stopped_at"] = datetime.now(UTC).isoformat()

        # Write state
        state_file.write_text(json.dumps(state, indent=2))

        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Ralph Loop stopped via API")

        return JSONResponse(
            content=api_response(
                {
                    "success": True,
                    "status": "stopped",
                    "message": "Ralph Loop stopped",
                }
            )
        )

    except Exception as e:
        from forge_harness.logging_config import get_logger
        logger = get_logger(__name__)
        logger.error(f"Error stopping Ralph Loop: {e}")
        return JSONResponse(
            content=api_response(
                error_code="STATE_UPDATE_ERROR",
                error_message=f"Failed to stop Ralph Loop: {str(e)}",
            )
        )

"""Errors API Endpoints

REST API endpoints for retrieving recent errors.
Extracted from webhook_server_main.py for better modularity.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth

logger = get_logger(__name__)

router = APIRouter()


def _get_forge_repo_root() -> Path:
    """Find FORGE repo root (directory containing .forge)."""
    current = Path(__file__).resolve().parent.parent.parent.parent
    while current != current.parent:
        if (current / ".forge").is_dir():
            return current
        current = current.parent
    return Path.cwd()


def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
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


@router.get("/api/errors/recent")
async def get_recent_errors(
    limit: int = Query(20, ge=1, le=100, description="Max errors to return"),
    _: None = Depends(verify_auth),
):
    """Get recent errors from logs.

    Reads recent log entries and filters for errors/warnings.

    Returns:
        APIResponse with:
        - errors: List of recent errors
        - count: Total error count
    """
    try:
        forge_root = _get_forge_repo_root()
        # Try to read from structured log files if they exist
        errors = []

        # Check for .forge/errors directory
        errors_dir = forge_root / ".forge/errors"
        if errors_dir.exists():
            error_files = sorted(
                errors_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            for error_file in error_files[:limit]:
                try:
                    error_data = json.loads(error_file.read_text())
                    errors.append(error_data)
                except Exception as e:
                    logger.warning(f"Failed to read error file {error_file}: {e}")

        # If no structured errors, return empty list
        if not errors:
            # Try to parse from quality metrics for common errors
            quality_dir = forge_root / "quality_metrics"
            if quality_dir.exists():
                # Look for recent history files with errors
                history_files = sorted(
                    quality_dir.glob("*_history.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )

                for history_file in history_files[:5]:
                    try:
                        history_data = json.loads(history_file.read_text())
                        if isinstance(history_data, list) and history_data:
                            latest_scan = history_data[-1]
                            if "security_findings" in latest_scan:
                                for finding in latest_scan["security_findings"][:3]:
                                    errors.append(
                                        {
                                            "timestamp": latest_scan.get("scan_timestamp"),
                                            "level": finding.get("severity", "warning").upper(),
                                            "message": finding.get("message", ""),
                                            "source": finding.get(
                                                "file_path", history_file.stem
                                            ),
                                            "line": finding.get("line_number"),
                                            "rule_id": finding.get("rule_id"),
                                        }
                                    )
                    except Exception as e:
                        logger.warning(f"Failed to parse history file {history_file}: {e}")

                errors = errors[:limit]

        return JSONResponse(
            content=api_response(
                {
                    "errors": errors,
                    "count": len(errors),
                    "limit": limit,
                    "source": "forge_errors" if errors_dir.exists() else "quality_metrics",
                }
            )
        )

    except Exception as e:
        logger.error(f"Error reading recent errors: {e}")
        return JSONResponse(
            content=api_response(
                error_code="READ_ERROR",
                error_message=f"Failed to read error logs: {str(e)}",
            )
        )

"""MVP Check API for FORGE Command Center.

Returns portfolio quality scan results for the MvpCheck frontend widget.
"""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from forge_harness.webhook_server.core.dependencies import verify_auth

logger = logging.getLogger(__name__)

# parents[3] resolves to the harness/ directory — the root that owns quality_metrics/.
# If FORGE_ROOT env var is set, append "harness" to reach the same location.
_env_root = os.environ.get("FORGE_ROOT")
if _env_root:
    QUALITY_METRICS_DIR = Path(_env_root) / "harness" / "quality_metrics"
else:
    QUALITY_METRICS_DIR = Path(__file__).resolve().parents[3] / "quality_metrics"

router = APIRouter(prefix="/api/mvp-check", tags=["mvp-check"])


def api_response(data=None, error=None):
    """Standard API response format matching frontend TypeScript interface."""
    if error is not None:
        return {"success": False, "error": error}
    return {"success": True, "data": data}


def _find_latest_portfolio_file() -> Path | None:
    """Return portfolio_latest.json or fall back to newest timestamped file."""
    latest = QUALITY_METRICS_DIR / "portfolio_latest.json"
    if latest.exists():
        return latest

    candidates = sorted(
        QUALITY_METRICS_DIR.glob("portfolio_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


@router.get("/status")
async def get_mvp_check_status(_: None = Depends(verify_auth)):
    """Get MVP check results from the latest portfolio quality scan.

    Reads harness/quality_metrics/portfolio_latest.json (or the most recent
    timestamped file as a fallback) and returns MVP status information.

    Returns:
        JSONResponse:
            success=True  -> data: {status, checks, last_run, details}
            success=False -> error: {code, message}
    """
    try:
        portfolio_file = _find_latest_portfolio_file()

        if portfolio_file is None:
            return JSONResponse(
                content=api_response(
                    error={
                        "code": "NO_SCAN_DATA",
                        "message": "No quality metrics scan data found",
                    }
                )
            )

        scan_data = json.loads(portfolio_file.read_text())

        critical_issues = scan_data.get("critical_issues", 0)
        high_issues = scan_data.get("high_issues", 0)
        avg_quality_score = scan_data.get("average_quality_score", 0)

        # Determine overall pass/fail status
        if critical_issues > 0 or high_issues > 3:
            status = "fail"
        else:
            status = "pass"

        checks = [
            {
                "name": "Critical Issues",
                "status": "pass" if critical_issues == 0 else "fail",
                "count": critical_issues,
                "threshold": 0,
            },
            {
                "name": "High Priority Issues",
                "status": "pass" if high_issues <= 3 else "fail",
                "count": high_issues,
                "threshold": 3,
            },
            {
                "name": "Average Quality Score",
                "status": "pass" if avg_quality_score >= 60 else "warning",
                "count": round(avg_quality_score, 2),
                "threshold": 60,
            },
        ]

        return JSONResponse(
            content=api_response(
                data={
                    "status": status,
                    "checks": checks,
                    "last_run": scan_data.get("scan_timestamp"),
                    "details": {
                        "projects_scanned": scan_data.get("projects_scanned", 0),
                        "total_projects": scan_data.get("total_projects", 0),
                        "portfolio_trend": scan_data.get("portfolio_trend", "unknown"),
                        "degraded_projects": scan_data.get("degraded_projects", []),
                        "improved_projects": scan_data.get("improved_projects", []),
                    },
                }
            )
        )

    except Exception as e:
        logger.error("Error reading MVP check status: %s", e)
        return JSONResponse(
            content=api_response(
                error={
                    "code": "READ_ERROR",
                    "message": f"Failed to read quality metrics: {e}",
                }
            )
        )

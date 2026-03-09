"""
FORGE Metrics Common - Shared Metrics and Data Loading Utilities
==================================================================

Consolidates shared metric-fetching, formatting, and data-loading
logic used across:
- TUI Dashboard (dashboard.py)
- CLI Status Command (cli_v2/status.py)
- Detail Views (dashboard_detail_views.py)
- Dashboard Core (dashboard_core.py)

This module provides:
1. Re-exported canonical formatting functions
2. Shared data loading helpers
3. Priority ordering utilities
"""

from __future__ import annotations

from datetime import datetime

# Re-export canonical formatting functions from dashboard_core
from .dashboard_core import (
    AgentState,
    ApprovalSummary,
    MVPStatusSummary,
    PipelineStatus,
    RalphStatus,
    format_age,
    format_duration,
)

__all__ = [
    "format_age",
    "format_duration",
    "AgentState",
    "ApprovalSummary",
    "PipelineStatus",
    "RalphStatus",
    "MVPStatusSummary",
    "PRIORITY_ORDER",
    "sort_approvals_by_priority",
    "filter_approvals_by_time",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical priority ordering for approvals (used across dashboard, CLI, etc.)
PRIORITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}

# ---------------------------------------------------------------------------
# Shared approval utilities
# ---------------------------------------------------------------------------


def sort_approvals_by_priority(approvals: list[ApprovalSummary]) -> list[ApprovalSummary]:
    """Sort approvals by priority (critical first) then age (oldest first).

    Args:
        approvals: List of approval summaries to sort

    Returns:
        Sorted list of approvals
    """
    return sorted(
        approvals,
        key=lambda a: (PRIORITY_ORDER.get(a.priority, 2), a.created_at),
    )


def filter_approvals_by_time(
    approvals: list[ApprovalSummary],
    time_filter: str,
    now: datetime | None = None,
) -> list[ApprovalSummary]:
    """Filter approvals by time window.

    Args:
        approvals: List of approval summaries
        time_filter: One of "all", "1h", "24h"
        now: Current datetime (defaults to UTC now)

    Returns:
        Filtered list of approvals
    """
    if time_filter == "all":
        return approvals

    if now is None:
        from datetime import UTC

        now = datetime.now(UTC)

    from datetime import timedelta

    hours = 1 if time_filter == "1h" else 24
    cutoff = now - timedelta(hours=hours)

    return [a for a in approvals if a.created_at and a.created_at >= cutoff]

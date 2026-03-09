"""
FORGE Dashboard Core - Canonical Data Models & Shared Utilities
===============================================================

Single source of truth for all dashboard-related data structures
and utility functions.  Both the TUI dashboard (``dashboard.py``)
and the fleet dashboard (``fleet/dashboard.py``) import from here
to avoid duplication and guarantee a consistent vocabulary.

This module deliberately avoids Rich / TUI imports so it can be
used from CLI helpers, API serializers, and tests without pulling
in heavy rendering dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Canonical agent status vocabulary
# ---------------------------------------------------------------------------

AGENT_STATUSES = ("active", "idle", "stale", "error", "unknown")

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def format_age(dt: datetime) -> str:
    """Format *dt* as a human-readable age (e.g. ``'2h ago'``, ``'5m ago'``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as human-readable (e.g. ``'1m 30s'``)."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class AgentState:
    """Unified agent representation - canonical model for all dashboard views.

    This is the single source of truth for agent state across:
    - TUI Dashboard (dashboard.py)
    - Fleet monitoring (fleet/dashboard.py)
    - Metrics tracking (fleet/metrics.py)
    - REST API (webhook_server/api/dashboard.py)

    All other agent-related dataclasses should convert to/from this model.
    """

    id: str
    name: str | None = None
    source: str = "unknown"  # "api" | "tmux" | "registry" | "unknown"
    status: str = "unknown"  # "active" | "idle" | "stale" | "error" | "paused" | "completed"
    last_activity: datetime | None = None
    is_stale: bool = False
    role: str | None = None
    project: str | None = None
    task: str | None = None
    progress: int = 0
    context_estimate: int = 0
    session_id: str | None = None
    window_name: str | None = None
    agent_type: str | None = None
    domain: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    messages_count: int = 0
    focus_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON output."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "project": self.project,
            "task": self.task,
            "status": self.status,
            "progress": self.progress,
            "last_activity": (self.last_activity.isoformat() + "Z" if self.last_activity else None),
            "is_stale": self.is_stale,
            "context_estimate": self.context_estimate,
            "source": self.source,
            "session_id": self.session_id,
            "window_name": self.window_name,
            "agent_type": self.agent_type,
            "domain": self.domain,
            "token_usage": self.token_usage,
            "messages_count": self.messages_count,
            "focus_tags": self.focus_tags,
        }


@dataclass
class PipelineStatus:
    """Status of an active pipeline."""

    name: str
    current_step: str
    step_index: int
    total_steps: int
    status: str
    started_at: datetime
    error: str | None = None


@dataclass
class ApprovalSummary:
    """Summary of an approval request for display."""

    id: str
    type: str
    title: str
    domain: str
    priority: str
    tier: str
    risk_score: float
    created_at: datetime


@dataclass
class RalphStatus:
    """Status of the Ralph loop."""

    active: bool
    current_feature: str | None
    iteration: int
    max_iterations: int
    failure_count: int
    state: str
    started_at: datetime | None


@dataclass
class MVPStatusSummary:
    """Summary of the latest MVP check for a project."""

    domain: str
    project: str
    status: str
    timestamp: datetime
    missing_env_vars: int


# ---------------------------------------------------------------------------
# Health computation
# ---------------------------------------------------------------------------


def compute_fleet_health(agents: list[AgentState]) -> str:
    """Compute overall fleet health from agent states.

    Returns
    -------
    str
        ``'healthy'``, ``'degraded'``, or ``'critical'``.
    """
    if not agents:
        return "critical"
    stale_count = sum(1 for a in agents if a.is_stale)
    if stale_count == 0:
        return "healthy"
    if (stale_count / len(agents)) < 0.5:
        return "degraded"
    return "critical"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def agent_state_to_dict(agent: AgentState) -> dict[str, Any]:
    """Serialize an :class:`AgentState` to a plain ``dict`` for JSON output.

    This is a convenience function that calls AgentState.to_dict().
    Kept for backward compatibility with existing code.
    """
    return agent.to_dict()

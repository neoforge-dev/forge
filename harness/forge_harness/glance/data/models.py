"""Data models and pure utilities for the Glance dashboard.

This module is free of any TUI (textual) dependencies so it can be imported
safely in test collection without triggering heavy UI imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Pure display helpers (no textual dependency)
# ---------------------------------------------------------------------------

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int], width: int = 12) -> str:
    """Render a list of ints as a sparkline string."""
    if not values:
        return "▁" * width
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    elif len(values) < width:
        sampled = values + [0] * (width - len(values))
    else:
        sampled = list(values)
    mx = max(sampled) if sampled else 1
    if mx == 0:
        mx = 1
    return "".join(_SPARK_CHARS[min(int(v / mx * 7), 7)] for v in sampled)


def progress_bar(current: int, total: int, width: int = 10) -> str:
    """Render a simple block progress bar."""
    if total == 0:
        return "░" * width
    filled = int(current / total * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class AgentInfo:
    name: str = ""
    status: str = "idle"  # active | idle | stale | error
    task: str = ""
    progress: float = 0.0
    last_activity: str = ""
    node: str = ""  # which node this agent runs on


@dataclass
class NodeInfo:
    name: str = ""
    status: str = "offline"  # online | offline | degraded
    agent_count: int = 0
    max_agents: int = 0
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    is_online: bool = False
    heartbeat_age: str = ""


@dataclass
class TaskStats:
    total: int = 0
    pending: int = 0
    active: int = 0
    completed: int = 0
    blocked: int = 0
    next_task: str = ""
    next_priority: str = ""


@dataclass
class ErrorInfo:
    message: str = ""
    source: str = ""
    age: str = ""


@dataclass
class GitInfo:
    branch: str = "main"
    is_clean: bool = True
    ahead: int = 0
    behind: int = 0
    last_commit_hash: str = ""
    last_commit_age: str = ""
    submodule_count: int = 0
    dirty_submodules: int = 0
    total_submodules: int = 0
    commits_today: int = 0


@dataclass
class DispatchInfo:
    """Single dispatch file parsed into structured data."""

    agent_name: str = ""
    task_subject: str = ""
    sprint_id: str = ""
    status: str = "active"  # "active" or "completed"
    filename: str = ""


@dataclass
class GlanceData:
    """Unified data snapshot for all glance cards."""

    timestamp: str = ""
    source: str = "offline"  # "api" | "files" | "offline"
    last_fetch_time: float = 0.0
    fetch_success: bool = False

    # Fleet health
    active_agents: int = 0
    total_agents: int = 0
    error_count: int = 0
    health_state: str = "healthy"  # healthy | degraded | critical
    throughput_samples: list[int] = field(default_factory=list)

    # Agents
    agents: list[AgentInfo] = field(default_factory=list)

    # Tasks
    tasks: TaskStats = field(default_factory=TaskStats)

    # Nodes
    nodes: list[NodeInfo] = field(default_factory=list)

    # Approvals
    pending_approvals: int = 0

    # Errors
    errors_1h: int = 0
    errors_24h: int = 0
    error_samples: list[int] = field(default_factory=list)
    recent_errors: list[ErrorInfo] = field(default_factory=list)

    # Dispatches
    dispatches: list[DispatchInfo] = field(default_factory=list)

    # Git
    git: GitInfo = field(default_factory=GitInfo)

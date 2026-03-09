"""Shared state adapter contracts for FORGE surfaces (CP-3001).

Defines canonical response shapes for tasks, agents, and nodes that are
consumed identically by all three surfaces:

  1. CLI  (``forge status --json``)
  2. TUI  (``forge_harness.dashboard``)
  3. Web  (Command Center React client)

Import the Pydantic models to validate outbound data, or call the
``normalize_*`` helpers to coerce raw dicts from any source into the
canonical shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from forge_harness.task_queue import TaskPriority, TaskStatus

# ---------------------------------------------------------------------------
# Lease (shared sub-model)
# ---------------------------------------------------------------------------


class LeaseContract(BaseModel):
    """Task lease ownership metadata (CP-2001 schema)."""

    owner_node: str = ""
    owner_agent: str = ""
    lease_expires_at: str = ""  # ISO-8601 UTC
    path_lock: str = ""

    def is_lease_active(self) -> bool:
        """Check if lease is still valid."""
        if not self.lease_expires_at:
            return False
        try:
            from datetime import datetime

            expiry = datetime.fromisoformat(self.lease_expires_at.replace("Z", "+00:00"))
            return datetime.now(expiry.tzinfo) < expiry
        except (ValueError, AttributeError):
            return False

    def claim(self, node_id: str, agent_id: str, ttl_seconds: int = 300) -> None:
        """Set lease with node and agent ownership."""
        from datetime import datetime, timedelta

        now = datetime.now()
        self.owner_node = node_id
        self.owner_agent = agent_id
        self.lease_expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

    def release(self) -> None:
        """Clear lease fields."""
        self.owner_node = ""
        self.owner_agent = ""
        self.lease_expires_at = ""
        self.path_lock = ""


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class TaskContract(BaseModel):
    """Canonical task representation consumed by all surfaces."""

    id: str
    subject: str
    description: str = ""
    priority: str = TaskPriority.MEDIUM.value
    status: str = TaskStatus.PENDING.value
    required_role: str | None = None
    claimed_by: str | None = None
    domain: str | None = None
    project: str | None = None
    order: int = 0
    lease: LeaseContract | None = None
    depends_on: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None

    def is_lease_active(self) -> bool:
        """Check if task lease is still valid."""
        if self.lease is None:
            return False
        return self.lease.is_lease_active()

    def claim(
        self, node_id: str, agent_id: str, ttl_seconds: int = 300, path_lock: str = ""
    ) -> None:
        """Claim this task with node and agent ownership."""
        if self.lease is None:
            self.lease = LeaseContract()
        self.lease.claim(node_id, agent_id, ttl_seconds)
        self.lease.path_lock = path_lock

    def release(self) -> None:
        """Release the task lease."""
        if self.lease is not None:
            self.lease.release()


class TaskStatsContract(BaseModel):
    """Aggregated task queue statistics."""

    pending: int = 0
    assigned: int = 0
    in_progress: int = 0
    completed: int = 0
    failed: int = 0
    blocked: int = 0
    total: int = 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

_AGENT_STATUS_CANONICAL = {
    "active": "active",
    "waiting": "waiting",
    "idle": "idle",
    "completed": "completed",
    "paused": "paused",
    "failed": "failed",
    "error": "failed",
    "unknown": "idle",
}


class AgentContract(BaseModel):
    """Canonical agent representation consumed by all surfaces."""

    session_id: str
    agent_role: str
    agent_name: str
    domain: str | None = None
    project: str = ""
    current_task: str = ""
    focus_tags: list[str] = Field(default_factory=list)
    status: str = "idle"
    progress_pct: float = 0.0
    tasks_completed: int = 0
    tasks_remaining: int = 0
    started_at: str | None = None
    last_activity: str | None = None
    token_usage: int = 0
    api_calls: int = 0
    pending_approval_id: str | None = None
    parent_id: str | None = None
    children: list[str] = Field(default_factory=list)
    source: str | None = None


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class NodeAlertContract(BaseModel):
    """Single alert entry on a node."""

    level: str = "info"  # info | warning | critical
    message: str = ""
    timestamp: str = ""


class NodeCapabilitiesContract(BaseModel):
    """Tool/runtime capabilities of a node."""

    claude: bool = False
    codex: bool = False
    gemini: bool = False
    kimi: bool = False
    opencode: bool = False
    ollama: bool = False
    docker: bool = False
    ios_simulator: bool = False
    gpu: bool = False


class NodeSpecsContract(BaseModel):
    """Hardware specifications of a node."""

    cpu: str = ""
    ram_gb: int = 0
    os: str = ""
    cores: int = 0


class NodeAgentSummary(BaseModel):
    """Lightweight agent entry nested inside a node response."""

    id: str = ""
    session_id: str = ""
    agent_id: str = ""
    name: str = ""
    role: str = ""
    status: str = "idle"
    project: str = ""
    task: str = ""
    progress: int = 0
    context_percentage: int = 0


class NodeContract(BaseModel):
    """Canonical node representation consumed by all surfaces."""

    node_id: str
    name: str
    status: str = "offline"  # online | offline | degraded | stale | maintenance
    agent_count: int = 0
    cpu_load: float = 0.0
    ram_usage: float = 0.0
    last_heartbeat: str = ""
    latency_ms: int = 0
    agents: list[NodeAgentSummary] = Field(default_factory=list)
    load_average: float = 0.0
    disk_usage: float = 0.0
    alerts: list[NodeAlertContract] = Field(default_factory=list)
    capabilities: NodeCapabilitiesContract = Field(
        default_factory=NodeCapabilitiesContract,
    )
    specs: NodeSpecsContract = Field(default_factory=NodeSpecsContract)
    is_fresh: bool = False
    age_seconds: float = 0.0
    received_at: str | None = None


# ---------------------------------------------------------------------------
# Unified status snapshot  (``forge status --json`` top-level)
# ---------------------------------------------------------------------------


class StatusSnapshot(BaseModel):
    """Complete status snapshot returned by ``forge status --json``."""

    tasks: list[TaskContract] = Field(default_factory=list)
    task_stats: TaskStatsContract = Field(default_factory=TaskStatsContract)
    agents: list[AgentContract] = Field(default_factory=list)
    nodes: list[NodeContract] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )


# ===================================================================
# Normalizer helpers — coerce raw dicts from any source
# ===================================================================


def normalize_task(raw: dict[str, Any]) -> TaskContract:
    """Coerce a raw task dict (from file storage, API, or CLI) to canonical shape."""
    lease_raw = raw.get("lease")
    lease = None
    if isinstance(lease_raw, dict) and lease_raw:
        lease = LeaseContract(
            owner_node=lease_raw.get("owner_node", ""),
            owner_agent=lease_raw.get("owner_agent", ""),
            lease_expires_at=str(lease_raw.get("lease_expires_at", "")),
            path_lock=lease_raw.get("path_lock", ""),
        )

    return TaskContract(
        id=raw.get("id", ""),
        subject=raw.get("subject") or raw.get("title", ""),
        description=raw.get("description", ""),
        priority=raw.get("priority", TaskPriority.MEDIUM.value),
        status=raw.get("status", TaskStatus.PENDING.value),
        required_role=raw.get("required_role"),
        claimed_by=raw.get("claimed_by") or raw.get("assigned_agent"),
        domain=raw.get("domain"),
        project=raw.get("project"),
        order=int(raw.get("order", 0)),
        lease=lease,
        depends_on=raw.get("depends_on", []),
        created_at=str(raw.get("created_at", "")),
        updated_at=str(raw.get("updated_at", raw.get("created_at", ""))),
        completed_at=raw.get("completed_at"),
    )


def normalize_agent(raw: dict[str, Any]) -> AgentContract:
    """Coerce a raw agent dict to canonical shape.

    Handles the field-name discrepancies between surfaces:
    - API uses ``agent_role`` / ``agent_name``
    - CLI uses ``role`` / ``name``
    - session_id fallback chain: session_id → id → tmux_session
    """
    session_id = (
        raw.get("session_id")
        or raw.get("id")
        or raw.get("tmux_session")
        or f"agent-{raw.get('agent_role', raw.get('role', 'unknown'))}"
    )
    role = raw.get("agent_role") or raw.get("role", "agent")
    name = raw.get("agent_name") or raw.get("name") or role

    raw_status = str(raw.get("status", "idle")).lower()
    canonical_status = _AGENT_STATUS_CANONICAL.get(raw_status, "idle")

    progress = raw.get("progress_pct") or raw.get("progress", 0.0)
    project_raw = raw.get("project")
    project = project_raw if isinstance(project_raw, str) else ""
    current_task_raw = raw.get("current_task")
    if current_task_raw is None:
        current_task_raw = raw.get("task")
    current_task = str(current_task_raw) if current_task_raw is not None else ""

    started_at_raw = raw.get("started_at")
    if isinstance(started_at_raw, datetime):
        started_at = started_at_raw.isoformat()
    elif started_at_raw is None:
        started_at = None
    else:
        started_at = str(started_at_raw)

    last_activity_raw = raw.get("last_activity")
    if isinstance(last_activity_raw, datetime):
        last_activity = last_activity_raw.isoformat()
    elif last_activity_raw is None:
        last_activity = None
    else:
        last_activity = str(last_activity_raw)

    focus_tags_raw = raw.get("focus_tags")
    focus_tags = focus_tags_raw if isinstance(focus_tags_raw, list) else []

    children_raw = raw.get("children")
    children = children_raw if isinstance(children_raw, list) else []

    token_usage_raw = raw.get("token_usage", 0)
    if isinstance(token_usage_raw, dict):
        token_usage = 0
        for value in token_usage_raw.values():
            try:
                token_usage += int(value)
            except (TypeError, ValueError):
                continue
    else:
        try:
            token_usage = int(token_usage_raw)
        except (TypeError, ValueError):
            token_usage = 0

    return AgentContract(
        session_id=session_id,
        agent_role=role,
        agent_name=name,
        domain=raw.get("domain"),
        project=project,
        current_task=current_task,
        focus_tags=focus_tags,
        status=canonical_status,
        progress_pct=float(progress),
        tasks_completed=int(raw.get("tasks_completed", 0)),
        tasks_remaining=int(raw.get("tasks_remaining", 0)),
        started_at=started_at,
        last_activity=last_activity,
        token_usage=token_usage,
        api_calls=int(raw.get("api_calls", 0)),
        pending_approval_id=raw.get("pending_approval_id"),
        parent_id=raw.get("parent_id"),
        children=children,
        source=raw.get("source"),
    )


def normalize_node(raw: dict[str, Any]) -> NodeContract:
    """Coerce a raw node dict to canonical shape."""
    agents_raw = raw.get("agents", [])
    agents = [
        NodeAgentSummary(
            id=a.get("id", ""),
            session_id=a.get("session_id", ""),
            agent_id=a.get("agent_id", ""),
            name=a.get("name", ""),
            role=a.get("role", ""),
            status=a.get("status", "idle"),
            project=a.get("project", ""),
            task=a.get("task", ""),
            progress=int(a.get("progress", 0)),
            context_percentage=int(a.get("context_percentage", 0)),
        )
        for a in agents_raw
        if isinstance(a, dict)
    ]

    alerts_raw = raw.get("alerts", [])
    alerts = [
        NodeAlertContract(
            level=al.get("level", "info"),
            message=al.get("message", ""),
            timestamp=al.get("timestamp", ""),
        )
        for al in alerts_raw
        if isinstance(al, dict)
    ]

    caps_raw = raw.get("capabilities", {})
    caps = (
        NodeCapabilitiesContract(
            **{
                k: bool(v)
                for k, v in caps_raw.items()
                if k in NodeCapabilitiesContract.model_fields
            }
        )
        if isinstance(caps_raw, dict)
        else NodeCapabilitiesContract()
    )

    specs_raw = raw.get("specs", {})
    specs = (
        NodeSpecsContract(
            cpu=str(specs_raw.get("cpu", "")),
            ram_gb=int(specs_raw.get("ram_gb", 0)),
            os=str(specs_raw.get("os", "")),
            cores=int(specs_raw.get("cores", 0)),
        )
        if isinstance(specs_raw, dict)
        else NodeSpecsContract()
    )

    return NodeContract(
        node_id=raw.get("node_id", ""),
        name=raw.get("name", ""),
        status=raw.get("status", "offline"),
        agent_count=int(raw.get("agent_count", 0)),
        cpu_load=float(raw.get("cpu_load", 0.0)),
        ram_usage=float(raw.get("ram_usage", 0.0)),
        last_heartbeat=str(raw.get("last_heartbeat", "")),
        latency_ms=int(raw.get("latency_ms", 0)),
        agents=agents,
        load_average=float(raw.get("load_average", 0.0)),
        disk_usage=float(raw.get("disk_usage", 0.0)),
        alerts=alerts,
        capabilities=caps,
        specs=specs,
        is_fresh=bool(raw.get("is_fresh", False)),
        age_seconds=float(raw.get("age_seconds", 0.0)),
        received_at=raw.get("received_at"),
    )

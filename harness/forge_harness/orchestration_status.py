"""
Orchestration Status Auto-Sync Module
=====================================

Automatically syncs orchestration status from agent telemetry to markdown and JSON files.
Eliminates manual status updates during multi-agent orchestration.

Usage:
    from forge_harness.orchestration_status import OrchestrationStatusSync

    sync = OrchestrationStatusSync(
        forge_root=Path("/path/to/forge"),
        output_dir=Path("docs")
    )

    # Auto-sync from agent telemetry
    await sync.sync_status()

    # Generate status report
    status = await sync.get_current_status()
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .state_store import AgentStatus, StateStore

logger = get_logger(__name__)


class OrchestrationStatusSync:
    """Auto-sync orchestration status from agent telemetry."""

    def __init__(
        self,
        forge_root: Path,
        output_dir: Path | None = None,
        json_filename: str = "ORCHESTRATION_STATUS.json",
        markdown_filename: str = "ORCHESTRATION_STATUS.md",
    ):
        self.forge_root = forge_root
        self.output_dir = output_dir or forge_root / "docs"
        self.json_path = self.output_dir / json_filename
        self.markdown_path = self.output_dir / markdown_filename

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize state store (from env or defaults)
        redis_url = os.environ.get("REDIS_URL")
        sqlite_path = os.environ.get("STATE_DB_PATH", ".forge/state/state.db")
        self.state_store = StateStore(redis_url=redis_url, sqlite_path=sqlite_path)
        self.state_store.connect()

    async def sync_status(self) -> dict[str, Any]:
        """Sync current orchestration status to files."""
        status = await self.get_current_status()

        # Write JSON
        self.json_path.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")

        # Write Markdown
        markdown = self._generate_markdown(status)
        self.markdown_path.write_text(markdown, encoding="utf-8")

        logger.info(f"Synced orchestration status to {self.json_path} and {self.markdown_path}")

        return status

    async def get_current_status(self) -> dict[str, Any]:
        """Get current orchestration status from agent telemetry."""
        # Get all active agents from state store
        agents = self.state_store.get_active_agents()

        # Group agents by domain and project
        domain_projects: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for agent in agents:
            domain = agent.domain or "unknown"
            project = agent.project or "unknown"

            if domain not in domain_projects:
                domain_projects[domain] = {}
            if project not in domain_projects[domain]:
                domain_projects[domain][project] = []

            domain_projects[domain][project].append(
                {
                    "agent_id": agent.session_id,
                    "role": agent.agent_role.value
                    if hasattr(agent.agent_role, "value")
                    else str(agent.agent_role),
                    "status": agent.status.value,
                    "task": agent.current_task,
                    "progress": None,  # AgentSession doesn't track progress
                    "started_at": agent.registered_at.isoformat() if agent.registered_at else None,
                    "last_heartbeat": agent.last_heartbeat.isoformat()
                    if agent.last_heartbeat
                    else None,
                }
            )

        # Calculate aggregate metrics
        total_agents = len(agents)
        active_agents = sum(1 for a in agents if a.status == AgentStatus.ACTIVE)
        idle_agents = sum(1 for a in agents if a.status == AgentStatus.IDLE)
        # Count agents that haven't sent heartbeat in 5+ minutes as stale
        from datetime import timedelta

        now = datetime.now(UTC)
        stale_agents = sum(
            1
            for a in agents
            if a.last_heartbeat and (now - a.last_heartbeat) > timedelta(minutes=5)
        )

        # Detect phase based on agent distribution
        phase = self._detect_phase(agents)

        return {
            "version": "1.0",
            "updated_at": datetime.now(UTC).isoformat(),
            "phase": phase,
            "summary": {
                "total_agents": total_agents,
                "active": active_agents,
                "idle": idle_agents,
                "stale": stale_agents,
                "domains": len(domain_projects),
                "projects": sum(len(projects) for projects in domain_projects.values()),
            },
            "domains": domain_projects,
        }

    def _detect_phase(self, agents: list) -> str:
        """Detect current orchestration phase based on agent roles and tasks."""
        if not agents:
            return "idle"

        roles = [
            a.agent_role.value if hasattr(a.agent_role, "value") else str(a.agent_role)
            for a in agents
        ]
        tasks = [a.current_task.lower() for a in agents if a.current_task]

        # Phase detection heuristics
        if any("explor" in t or "research" in t or "niche" in t for t in tasks):
            return "exploration"
        elif any("spec" in t or "features.json" in t for t in tasks):
            return "specification"
        elif any("backend" in r or "fastapi" in t for r, t in zip(roles, tasks)):
            return "backend_development"
        elif any("frontend" in r or "landing" in t for r, t in zip(roles, tasks)):
            return "frontend_development"
        elif any("content" in r or "blog" in t for r, t in zip(roles, tasks)):
            return "content_creation"
        elif any("test" in t or "pytest" in t for t in tasks):
            return "testing"
        elif any("deploy" in t for t in tasks):
            return "deployment"
        elif any(a.status == AgentStatus.ACTIVE for a in agents):
            return "active"
        else:
            return "idle"

    def _generate_markdown(self, status: dict[str, Any]) -> str:
        """Generate markdown report from status data."""
        lines = [
            "# Orchestration Status",
            "",
            f"**Updated:** {status['updated_at']}",
            f"**Phase:** {status['phase']}",
            "",
            "## Summary",
            "",
            f"- **Total Agents:** {status['summary']['total_agents']}",
            f"- **Active:** {status['summary']['active']}",
            f"- **Idle:** {status['summary']['idle']}",
            f"- **Stale:** {status['summary']['stale']}",
            f"- **Domains:** {status['summary']['domains']}",
            f"- **Projects:** {status['summary']['projects']}",
            "",
        ]

        # Domain/Project breakdown
        if status["domains"]:
            lines.append("## Active Work")
            lines.append("")

            for domain, projects in status["domains"].items():
                lines.append(f"### {domain}")
                lines.append("")

                for project, agents in projects.items():
                    lines.append(f"#### {project}")
                    lines.append("")
                    lines.append("| Agent | Role | Status | Task | Progress |")
                    lines.append("|-------|------|--------|------|----------|")

                    for agent in agents:
                        task = agent["task"] or "No task"
                        progress = f"{agent['progress']:.0%}" if agent["progress"] else "N/A"
                        lines.append(
                            f"| `{agent['agent_id'][:8]}` | "
                            f"{agent['role'] or 'N/A'} | "
                            f"{agent['status']} | "
                            f"{task} | "
                            f"{progress} |"
                        )

                    lines.append("")

        return "\n".join(lines)


async def sync_orchestration_status(
    forge_root: Path,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Convenience function to sync orchestration status.

    Args:
        forge_root: Root directory of FORGE portfolio
        output_dir: Optional output directory (defaults to forge_root/docs)

    Returns:
        Current status dictionary
    """
    sync = OrchestrationStatusSync(forge_root, output_dir)
    return await sync.sync_status()

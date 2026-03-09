"""Unified data fetcher — tries API first, falls back to local files."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .models import AgentInfo, DispatchInfo, GitInfo, GlanceData, NodeInfo, TaskStats

logger = logging.getLogger(__name__)

# Known fleet agents — only dispatch files starting with these names are parsed
_KNOWN_AGENTS = frozenset({
    "minimax", "glm", "kimi", "gemini", "kilo", "opencode", "pi", "open-max", "opus",
})

# Node max-agent budgets (from CLAUDE.md)
_NODE_MAX_AGENTS = {
    "prya": 2,
    "sati": 6,
    "nova": 4,
    "vega": 2,
    "gaea": 3,
}


def _format_age(seconds: float) -> str:
    """Format age in seconds to a human-readable string like '2m ago' or '3h ago'."""
    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    else:
        return f"{int(seconds // 86400)}d ago"


class GlanceFetcher:
    """Fetch dashboard data from API with file-based fallback."""

    def __init__(
        self,
        api_url: str | None = None,
        api_token: str | None = None,
        forge_root: Path | None = None,
    ) -> None:
        self.api_url = api_url
        self.api_token = api_token
        self.forge_root = forge_root or self._find_forge_root()

    @staticmethod
    def _find_forge_root() -> Path:
        from forge_harness.cli_v2._common import find_forge_root

        return find_forge_root() or Path.cwd()

    async def fetch_all(self) -> GlanceData:
        """Fetch from API if available, else from local files."""
        if self.api_url:
            try:
                return await self._fetch_from_api()
            except Exception:
                logger.debug("API fetch failed, falling back to files")
        return await self._fetch_from_files()

    async def _fetch_from_api(self) -> GlanceData:
        """Fetch via status_adapter.py — builds cross-node picture.

        Fetches sequentially to avoid 429 rate-limit responses from the
        Command Center API (concurrent gather of 4 requests triggers it).
        """
        from forge_harness.status_adapter import (
            fetch_agents,
            fetch_approvals,
            fetch_fleet_status,
            fetch_tasks,
        )

        async def _retry(coro_fn, *args, retries: int = 2):
            """Call an async fetch function with retry on empty (429) results."""
            for attempt in range(retries + 1):
                result = await coro_fn(*args, timeout=5.0)
                if result or attempt == retries:
                    return result
                await asyncio.sleep(0.3 * (attempt + 1))
            return result  # type: ignore[possibly-undefined]

        # Sequential to avoid CC API 429 rate-limit on concurrent requests
        nodes_raw = await _retry(fetch_fleet_status, self.api_url, self.api_token)
        agents_raw = await _retry(fetch_agents, self.api_url, self.api_token)
        tasks_raw = await _retry(fetch_tasks, self.api_url, self.api_token)
        approvals_raw = await _retry(fetch_approvals, self.api_url, self.api_token)

        # Build agents from both top-level /api/agents AND per-node nested agents.
        # Per-node agents tell us which node each agent runs on.
        seen_agents: dict[str, AgentInfo] = {}

        # First: per-node agents (these carry the node association)
        for n in nodes_raw:
            for na in n.agents:
                agent_name = na.name or na.agent_id.replace("forge:", "")
                seen_agents[agent_name] = AgentInfo(
                    name=agent_name,
                    status=na.status,
                    task=na.task or na.project,
                    progress=float(na.progress),
                    node=n.name,
                )

        # Second: top-level agents (may have richer status/task info)
        for a in agents_raw:
            name = a.agent_name
            if name in seen_agents:
                # Enrich with top-level data (status may be more accurate)
                existing = seen_agents[name]
                existing.status = a.status
                existing.task = a.current_task or existing.task
                existing.progress = a.progress_pct or existing.progress
                existing.last_activity = a.last_activity or ""
            else:
                seen_agents[name] = AgentInfo(
                    name=name,
                    status=a.status,
                    task=a.current_task,
                    progress=a.progress_pct,
                    last_activity=a.last_activity or "",
                    node="",
                )

        agents = list(seen_agents.values())
        active = sum(1 for a in agents if a.status == "active")
        error_count = sum(1 for a in agents if a.status in ("error", "failed"))
        stale_count = sum(1 for a in agents if a.status == "stale")

        # Tasks
        task_statuses = [t.status for t in tasks_raw]
        pending = sum(1 for s in task_statuses if s == "pending")
        in_progress = sum(1 for s in task_statuses if s in ("in_progress", "assigned"))
        completed = sum(1 for s in task_statuses if s == "completed")
        blocked = sum(1 for s in task_statuses if s == "blocked")

        next_task = ""
        next_priority = ""
        for t in tasks_raw:
            if t.status == "pending":
                next_task = t.subject[:30]
                next_priority = t.priority
                break

        # Nodes — ordered canonically, with heartbeat age from age_seconds
        node_order = {name: i for i, name in enumerate(
            ["prya", "sati", "nova", "vega", "gaea"]
        )}
        nodes = []
        for n in nodes_raw:
            age_str = _format_age(n.age_seconds) if n.age_seconds > 0 else ""
            nodes.append(
                NodeInfo(
                    name=n.name,
                    status=n.status,
                    agent_count=n.agent_count,
                    max_agents=_NODE_MAX_AGENTS.get(n.name, 4),
                    cpu_percent=n.cpu_load,
                    ram_percent=n.ram_usage,
                    is_online=n.status in ("online", "healthy"),
                    heartbeat_age=age_str,
                )
            )
        nodes.sort(key=lambda nd: node_order.get(nd.name, 99))

        # Health
        health = "healthy"
        if error_count > 0 or stale_count > 0:
            health = "degraded"
        offline_nodes = sum(1 for nd in nodes if not nd.is_online)
        if offline_nodes > len(nodes) // 2 or (active == 0 and len(agents) > 0):
            health = "critical"

        # Dispatches (local files — always available regardless of API)
        dispatches = self._parse_dispatches()

        git = await self._fetch_git_info()

        return GlanceData(
            timestamp=datetime.now(UTC).isoformat(),
            source="api",
            active_agents=active,
            total_agents=len(agents),
            error_count=error_count + stale_count,
            health_state=health,
            agents=agents,
            tasks=TaskStats(
                total=len(tasks_raw),
                pending=pending,
                active=in_progress,
                completed=completed,
                blocked=blocked,
                next_task=next_task,
                next_priority=next_priority,
            ),
            nodes=nodes,
            pending_approvals=len(approvals_raw),
            dispatches=dispatches,
            git=git,
        )

    def _parse_dispatches(self) -> list[DispatchInfo]:
        """Parse .forge/dispatches/ and correlate with results."""
        dispatches: list[DispatchInfo] = []
        dispatch_dir = self.forge_root / ".forge" / "dispatches"
        results_dir = self.forge_root / ".forge" / "heartbeat" / "results"

        if not dispatch_dir.is_dir():
            return dispatches

        for dispatch_file in sorted(dispatch_dir.glob("*.md")):
            parts = dispatch_file.stem.split("-", 2)
            agent_name = parts[0]
            if agent_name not in _KNOWN_AGENTS:
                continue
            sprint_id = parts[1] if len(parts) > 1 else ""
            task_subject = parts[2].replace("-", " ") if len(parts) > 2 else ""
            has_result = results_dir.is_dir() and (results_dir / dispatch_file.name).is_file()
            dispatches.append(
                DispatchInfo(
                    agent_name=agent_name,
                    task_subject=task_subject,
                    sprint_id=sprint_id,
                    status="completed" if has_result else "active",
                    filename=dispatch_file.name,
                )
            )
        return dispatches[:12]

    async def _fetch_from_files(self) -> GlanceData:
        """Read .forge/heartbeat/ and .forge/fleet/ directly."""
        import time as _time

        nodes: list[NodeInfo] = []
        heartbeat_dir = self.forge_root / ".forge" / "heartbeat" / "nodes"

        if heartbeat_dir.is_dir():
            for node_file in heartbeat_dir.glob("*.json"):
                try:
                    raw = json.loads(node_file.read_text(encoding="utf-8"))
                    name = raw.get("node_id", node_file.stem)
                    resources = raw.get("resources", {})
                    health = raw.get("health", {})
                    ram_total = resources.get("ram_total_mb", 1)
                    ram_avail = resources.get("ram_available_mb", 0)
                    ram_pct = ((ram_total - ram_avail) / ram_total * 100) if ram_total else 0

                    # Compute heartbeat file age
                    age_seconds = _time.time() - node_file.stat().st_mtime
                    heartbeat_age = _format_age(age_seconds)

                    nodes.append(
                        NodeInfo(
                            name=name,
                            status=health.get("status", "unknown"),
                            cpu_percent=resources.get("cpu_usage_percent", 0),
                            ram_percent=round(ram_pct, 1),
                            max_agents=_NODE_MAX_AGENTS.get(name, 4),
                            is_online=health.get("status") in ("healthy", "online"),
                            heartbeat_age=heartbeat_age,
                        )
                    )
                except (json.JSONDecodeError, OSError):
                    continue

        # Parse fleet config for agent info
        agents: list[AgentInfo] = []
        fleet_config = self.forge_root / ".forge/fleet" / "config.json"
        if fleet_config.is_file():
            try:
                cfg = json.loads(fleet_config.read_text(encoding="utf-8"))
                windows = cfg.get("windows", {})
                for window_name, info in windows.items():
                    agent_name = window_name.replace("forge:", "")
                    agents.append(
                        AgentInfo(
                            name=agent_name,
                            status="idle",
                            task=info.get("role", ""),
                        )
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Parse dispatches for agent activity
        dispatches = self._parse_dispatches()

        # Update matching agent status from dispatches
        for d in dispatches:
            for agent in agents:
                if agent.name == d.agent_name:
                    agent.status = d.status
                    agent.task = d.task_subject[:30]
                    break

        # G1+G5: When no fleet config is present, synthesize agents directly
        # from dispatch files so active_agents is never stuck at zero in file mode.
        if not agents and dispatches:
            seen: set[str] = set()
            for d in dispatches:
                if d.agent_name not in seen:
                    seen.add(d.agent_name)
                    agents.append(
                        AgentInfo(
                            name=d.agent_name,
                            status=d.status,
                            task=d.task_subject[:30],
                        )
                    )

        active_agents = sum(1 for a in agents if a.status == "active")

        git = await self._fetch_git_info()

        return GlanceData(
            timestamp=datetime.now(UTC).isoformat(),
            source="files",
            active_agents=active_agents,
            total_agents=len(agents),
            agents=agents[:12],  # Cap display
            nodes=nodes,
            dispatches=dispatches[:12],  # Cap display
            git=git,
        )

    async def _fetch_git_info(self) -> GitInfo:
        """Get git status via subprocess."""
        info = GitInfo()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "status", "--porcelain", "--branch"],
                    capture_output=True,
                    text=True,
                    cwd=str(self.forge_root),
                    timeout=5,
                ),
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if lines:
                    header = lines[0]  # ## branch...tracking
                    if header.startswith("## "):
                        branch_part = header[3:]
                        if "..." in branch_part:
                            info.branch = branch_part.split("...")[0]
                        else:
                            info.branch = branch_part.split()[0] if branch_part else "main"
                        if "[ahead " in header:
                            try:
                                ahead_str = header.split("[ahead ")[1].split("]")[0].split(",")[0]
                                info.ahead = int(ahead_str)
                            except (IndexError, ValueError):
                                pass
                        if "behind " in header:
                            try:
                                behind_str = header.split("behind ")[1].split("]")[0].split(",")[0]
                                info.behind = int(behind_str)
                            except (IndexError, ValueError):
                                pass
                    dirty_lines = [ln for ln in lines[1:] if ln.strip()]
                    info.is_clean = len(dirty_lines) == 0

            # Last commit
            log_result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "log", "-1", "--format=%h %ar"],
                    capture_output=True,
                    text=True,
                    cwd=str(self.forge_root),
                    timeout=5,
                ),
            )
            if log_result.returncode == 0 and log_result.stdout.strip():
                parts = log_result.stdout.strip().split(" ", 1)
                info.last_commit_hash = parts[0]
                info.last_commit_age = parts[1] if len(parts) > 1 else ""

            # Submodule count (also detect dirty submodules)
            sub_result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "submodule", "status"],
                    capture_output=True,
                    text=True,
                    cwd=str(self.forge_root),
                    timeout=5,
                ),
            )
            if sub_result.returncode == 0:
                sub_lines = [ln for ln in sub_result.stdout.strip().splitlines() if ln.strip()]
                info.submodule_count = len(sub_lines)
                info.total_submodules = len(sub_lines)
                info.dirty_submodules = sum(1 for ln in sub_lines if ln.startswith("+"))

            # Commits today
            today_result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "log", "--since=midnight", "--oneline"],
                    capture_output=True,
                    text=True,
                    cwd=str(self.forge_root),
                    timeout=5,
                ),
            )
            if today_result.returncode == 0:
                today_lines = [ln for ln in today_result.stdout.strip().splitlines() if ln.strip()]
                info.commits_today = len(today_lines)

        except Exception:
            logger.debug("Git info fetch failed", exc_info=True)

        return info

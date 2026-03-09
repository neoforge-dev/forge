"""Resource-Aware Task Scheduler for V3.

Schedules tasks to agents based on:
- Node resource constraints (RAM, CPU)
- Agent capabilities
- Current workload
- Task requirements
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .database import get_task_db


@dataclass
class NodeResources:
    """Resource availability for a node."""
    host: str
    ram_gb: int
    cpu_cores: int
    allowed_agents: list[str]
    forbidden_agents: list[str]
    max_concurrent_agents: int
    current_agents: int = 0
    used_ram_gb: float = 0.0

    def can_accept_agent(self, agent: str, agent_ram_mb: int) -> bool:
        """Check if node can accept another agent."""
        if agent in self.forbidden_agents:
            return False

        if self.allowed_agents and agent not in self.allowed_agents:
            return False

        if self.current_agents >= self.max_concurrent_agents:
            return False

        remaining_ram = self.ram_gb - self.used_ram_gb
        if remaining_ram < (agent_ram_mb / 1024):
            return False

        return True


class ResourceScheduler:
    """Schedules tasks with resource awareness."""

    def __init__(
        self,
        config_path: Path | None = None,
        node_name: str | None = None
    ):
        self.config_path = config_path or Path(".forge/config/nodes.yaml")
        self.node_name = node_name or self._detect_node()
        self.config = self._load_config()
        self.db = get_task_db()

    def _detect_node(self) -> str:
        """Auto-detect current node name."""
        import socket
        hostname = socket.gethostname()
        # Map common hostnames to node names
        node_map = {
            "sati": "sati",
            "nova": "nova",
            "prya": "prya",
            "vega": "vega"
        }
        return node_map.get(hostname, "sati")

    def _load_config(self) -> dict[str, Any]:
        """Load node configuration."""
        if not self.config_path.exists():
            # Return default config
            return {
                "nodes": {
                    self.node_name: {
                        "ram_gb": 64,
                        "cpu_cores": 8,
                        "allowed_agents": [],
                        "forbidden_agents": [],
                        "max_concurrent_agents": 4
                    }
                },
                "agent_resources": {}
            }

        return yaml.safe_load(self.config_path.read_text())

    def get_node_resources(self, node: str | None = None) -> NodeResources:
        """Get resource info for a node."""
        node = node or self.node_name
        node_config = self.config.get("nodes", {}).get(node, {})

        # Count current agents on this node
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE assigned_agent IS NOT NULL AND status IN ('assigned', 'in_progress')"
        )
        current_agents = cursor.fetchone()[0]

        # Calculate used RAM (simplified - assumes we track this)
        cursor = conn.execute(
            "SELECT assigned_agent FROM tasks WHERE status IN ('assigned', 'in_progress')"
        )
        used_ram = 0.0
        agent_resources = self.config.get("agent_resources", {})
        for row in cursor.fetchall():
            agent = row[0]
            if agent in agent_resources:
                used_ram += agent_resources[agent].get("ram_mb", 1024) / 1024

        conn.close()

        return NodeResources(
            host=node,
            ram_gb=node_config.get("ram_gb", 64),
            cpu_cores=node_config.get("cpu_cores", 8),
            allowed_agents=node_config.get("allowed_agents", []),
            forbidden_agents=node_config.get("forbidden_agents", []),
            max_concurrent_agents=node_config.get("max_concurrent_agents", 4),
            current_agents=current_agents,
            used_ram_gb=used_ram
        )

    def find_best_agent(
        self,
        task_id: str,
        preferred_agent: str | None = None
    ) -> str | None:
        """Find best agent for a task based on resources and availability."""
        resources = self.get_node_resources()
        agent_resources = self.config.get("agent_resources", {})

        # Check preferred agent first
        if preferred_agent:
            agent_ram = agent_resources.get(preferred_agent, {}).get("ram_mb", 1024)
            if resources.can_accept_agent(preferred_agent, agent_ram):
                return preferred_agent

        # Find any available agent
        for agent in resources.allowed_agents:
            if agent == "sati":  # Skip orchestrator
                continue

            agent_ram = agent_resources.get(agent, {}).get("ram_mb", 1024)
            if resources.can_accept_agent(agent, agent_ram):
                return agent

        return None

    def validate_assignment(
        self,
        agent: str,
        node: str | None = None
    ) -> tuple[bool, str]:
        """Validate if agent can be assigned to node."""
        node = node or self.node_name
        node_config = self.config.get("nodes", {}).get(node, {})

        # Check forbidden list
        forbidden = node_config.get("forbidden_agents", [])
        if agent in forbidden:
            return False, f"Agent '{agent}' is forbidden on node '{node}'"

        # Check allowed list
        allowed = node_config.get("allowed_agents", [])
        if allowed and agent not in allowed:
            return False, f"Agent '{agent}' not in allowed list for node '{node}'"

        # Check resource constraints
        resources = self.get_node_resources(node)
        agent_resources = self.config.get("agent_resources", {}).get(agent, {})
        agent_ram = agent_resources.get("ram_mb", 1024)

        if not resources.can_accept_agent(agent, agent_ram):
            remaining = resources.ram_gb - resources.used_ram_gb
            return False, (
                f"Node '{node}' has insufficient resources for '{agent}'. "
                f"Available: {remaining:.1f}GB, Required: {agent_ram/1024:.1f}GB"
            )

        return True, "OK"

    def get_agent_for_node(self, node: str) -> list[str]:
        """Get list of agents that can run on a node."""
        node_config = self.config.get("nodes", {}).get(node, {})
        return node_config.get("allowed_agents", [])

    def get_optimal_node_for_agent(self, agent: str) -> str | None:
        """Find best node for an agent based on available resources."""
        best_node = None
        best_score = -1

        for node_name, node_config in self.config.get("nodes", {}).items():
            # Check if agent allowed
            forbidden = node_config.get("forbidden_agents", [])
            if agent in forbidden:
                continue

            allowed = node_config.get("allowed_agents", [])
            if allowed and agent not in allowed:
                continue

            # Get current resources
            resources = self.get_node_resources(node_name)
            agent_resources = self.config.get("agent_resources", {}).get(agent, {})
            agent_ram = agent_resources.get("ram_mb", 1024)

            if not resources.can_accept_agent(agent, agent_ram):
                continue

            # Score based on available headroom
            remaining_ram = resources.ram_gb - resources.used_ram_gb
            score = remaining_ram

            # Prefer less loaded nodes
            score -= resources.current_agents * 2

            if score > best_score:
                best_score = score
                best_node = node_name

        return best_node


# Singleton
_scheduler_instance: ResourceScheduler | None = None


def get_scheduler() -> ResourceScheduler:
    """Get singleton scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = ResourceScheduler()
    return _scheduler_instance

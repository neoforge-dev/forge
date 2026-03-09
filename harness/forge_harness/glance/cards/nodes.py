"""Node Resource card — per-node CPU/RAM/agent gauges."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard, progress_bar

# Catppuccin Mocha node accent colors
_NODE_COLORS = {
    "prya": "#89b4fa",
    "sati": "#a6e3a1",
    "nova": "#cba6f7",
    "vega": "#fab387",
    "gaea": "#94e2d5",
}


class NodeResourceCard(GlanceCard):
    card_name = "Nodes"
    card_priority = 4

    def update_data(self, data: GlanceData) -> None:
        if not data.nodes:
            self._body.update("[dim]No node data[/]")
            return

        lines: list[str] = []
        for node in data.nodes:
            color = _NODE_COLORS.get(node.name, "#cdd6f4")
            name = f"[{color}]{node.name:<8}[/]"

            if not node.is_online:
                lines.append(f"{name} [dim]○ offline[/]")
                continue

            agent_bar = progress_bar(node.agent_count, node.max_agents, width=4)
            cpu = f"CPU:{node.cpu_percent:>3.0f}%"
            ram = f"RAM:{node.ram_percent:>3.0f}%"
            cap = f"{node.agent_count}/{node.max_agents}"
            age_str = ""
            if node.heartbeat_age:
                # Color red if heartbeat is older than 10 minutes
                age_val = node.heartbeat_age
                # Parse the number to check threshold
                try:
                    num = int("".join(c for c in age_val if c.isdigit()))
                    unit = age_val.rstrip(" ago")[-1]
                    is_stale = (unit == "h" or unit == "d" or
                                (unit == "m" and num > 10) or
                                (unit == "s" and num > 600))
                except (ValueError, IndexError):
                    is_stale = False
                if is_stale:
                    age_str = f"  [red]{age_val}[/]"
                else:
                    age_str = f"  [dim]{age_val}[/]"
            lines.append(f"{name} {agent_bar} {cap}  {cpu} {ram}{age_str}")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        online = sum(1 for n in data.nodes if n.is_online)
        return f"Nodes {online}/{len(data.nodes)} online"

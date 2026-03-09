"""Agent Grid card — per-agent status rows with node origin."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard

_STATUS_ICONS = {
    "active": "[green]●[/]",
    "idle": "[dim]○[/]",
    "stale": "[yellow]⚠[/]",
    "error": "[red]✗[/]",
    "waiting": "[blue]◔[/]",
    "paused": "[dim]◑[/]",
    "completed": "[green]✓[/]",
    "failed": "[red]✗[/]",
}

# Catppuccin Mocha node colors for inline use
_NODE_COLORS = {
    "prya": "#89b4fa",
    "sati": "#a6e3a1",
    "nova": "#cba6f7",
    "vega": "#fab387",
    "gaea": "#94e2d5",
}


class AgentGridCard(GlanceCard):
    card_name = "Agents"
    card_priority = 2

    def update_data(self, data: GlanceData) -> None:
        if not data.agents:
            self._body.update("[dim]No agent data[/]")
            return

        lines: list[str] = []
        for agent in data.agents[:12]:
            icon = _STATUS_ICONS.get(agent.status, "[dim]?[/]")
            name = agent.name[:10].ljust(10)

            # Node tag with accent color
            if agent.node:
                color = _NODE_COLORS.get(agent.node, "#cdd6f4")
                node_tag = f"[{color}]{agent.node[:5]:>5}[/]"
            else:
                node_tag = "    —"

            task = (agent.task[:16] if agent.task else "—").ljust(16)
            pct = f"{agent.progress:.0f}%" if agent.progress > 0 else " —"
            lines.append(f"{icon} {name} {node_tag}  {task} {pct}")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        active = sum(1 for a in data.agents if a.status == "active")
        return f"Agents {active}/{len(data.agents)} active"

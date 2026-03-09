"""Dispatch card — active and completed agent dispatches."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard


class DispatchCard(GlanceCard):
    card_name = "Dispatches"
    card_priority = 7

    def update_data(self, data: GlanceData) -> None:
        if not data.dispatches:
            self._body.update("[dim]No dispatches[/]")
            return

        lines: list[str] = []
        for d in data.dispatches[:10]:
            if d.status == "completed":
                icon = "[green]●[/]"
                suffix = "[green]Done ✓[/]"
            else:
                icon = "[blue]○[/]"
                suffix = "[blue]Active[/]"
            name = d.agent_name[:10].ljust(10)
            task = (d.task_subject[:24] if d.task_subject else "—").ljust(24)
            lines.append(f"{icon} {name} {task} {suffix}")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        active = sum(1 for d in data.dispatches if d.status == "active")
        done = sum(1 for d in data.dispatches if d.status == "completed")
        return f"Dispatch {active}A {done}D"

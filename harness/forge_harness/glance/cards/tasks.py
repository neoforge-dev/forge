"""Task Queue card — queue depth and priority breakdown."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard, progress_bar


class TaskQueueCard(GlanceCard):
    card_name = "Tasks"
    card_priority = 3

    def update_data(self, data: GlanceData) -> None:
        t = data.tasks
        bar = progress_bar(t.completed, t.total if t.total else 1)

        lines = [
            f"{bar} {t.total} total",
            f"[blue]■[/] {t.pending} pending  [green]■[/] {t.active} active",
            f"[dim]■[/] {t.completed} done    [red]■[/] {t.blocked} blocked",
        ]
        if t.next_task:
            prio_color = {"high": "red", "critical": "red", "medium": "yellow"}.get(
                t.next_priority, "dim"
            )
            lines.append(f"Next: [{prio_color}]{t.next_task}[/] ({t.next_priority})")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        t = data.tasks
        return f"Tasks {t.pending}P {t.active}A {t.completed}D {t.blocked}B"

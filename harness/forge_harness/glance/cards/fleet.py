"""Fleet Health card — overall fleet status at a glance."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard, sparkline

_STATUS_ICON = {"healthy": "[green]●[/]", "degraded": "[yellow]⚠[/]", "critical": "[red]✗[/]"}


class FleetHealthCard(GlanceCard):
    card_name = "Fleet Health"
    card_priority = 1

    def update_data(self, data: GlanceData) -> None:
        icon = _STATUS_ICON.get(data.health_state, "●")
        err_str = f"[red]✗ {data.error_count}[/]" if data.error_count else "[green]✗ 0[/]"
        spark = sparkline(data.throughput_samples)

        lines = [
            f"{icon} {data.active_agents}/{data.total_agents} Active   {err_str} Errors",
            f"{spark} throughput (60s)",
            f"Tasks: {data.tasks.pending} queued / {data.tasks.active} in-flight",
        ]
        if data.pending_approvals:
            lines.append(f"[yellow]Approvals: {data.pending_approvals} pending[/]")

        src = f"[dim]src:{data.source}[/]"
        lines.append(src)
        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        icon = "●" if data.health_state == "healthy" else "⚠" if data.health_state == "degraded" else "✗"
        return f"Fleet {icon}{data.active_agents}/{data.total_agents} ✗{data.error_count} Q:{data.tasks.total} A:{data.pending_approvals}"

"""Error card — recent errors and error rate sparkline."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard, sparkline


class ErrorCard(GlanceCard):
    card_name = "Errors"
    card_priority = 5

    def update_data(self, data: GlanceData) -> None:
        # In file mode, error data is not available — show honest state
        if data.source == "files":
            lines = [
                "Last 1h: [dim]--[/]    Last 24h: [dim]--[/]",
                "[dim]Error data requires API mode[/]",
            ]
            self._body.update("\n".join(lines))
            return

        spark = sparkline(data.error_samples)
        e1h_color = "green" if data.errors_1h == 0 else "red"
        e24h_color = "green" if data.errors_24h == 0 else "yellow"

        lines = [
            f"Last 1h: [{e1h_color}]{data.errors_1h}[/]  Last 24h: [{e24h_color}]{data.errors_24h}[/]",
            f"{spark} rate (24h)",
        ]

        if data.recent_errors:
            err = data.recent_errors[0]
            lines.append(f"Latest: {err.message[:30]}")
            if err.source:
                lines.append(f"  [dim]-> {err.source} {err.age}[/]")
        else:
            lines.append("[green]No recent errors[/]")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        return f"Err {data.errors_1h}/1h {data.errors_24h}/24h"

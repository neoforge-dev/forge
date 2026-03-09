"""Git Status card — branch, dirty state, recent commits."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard


class GitStatusCard(GlanceCard):
    card_name = "Git"
    card_priority = 6

    def update_data(self, data: GlanceData) -> None:
        g = data.git
        clean_str = "[green]✓ clean[/]" if g.is_clean else "[yellow]● dirty[/]"

        lines = [
            f"Branch: {g.branch} {clean_str}",
            f"Ahead: {g.ahead}  Behind: {g.behind}",
        ]
        if g.last_commit_hash:
            lines.append(f"Last: {g.last_commit_hash} {g.last_commit_age}")
        if g.submodule_count:
            if g.dirty_submodules > 0:
                dirty_str = f" ([yellow]{g.dirty_submodules} dirty[/])"
            else:
                dirty_str = ""
            lines.append(f"Subs: {g.total_submodules}{dirty_str}")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        g = data.git
        icon = "✓" if g.is_clean else "●"
        return f"Git {g.branch} {icon} ↑{g.ahead} ↓{g.behind}"

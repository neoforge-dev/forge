"""Session card — git-based session overview."""

from __future__ import annotations

from ..data.models import GlanceData
from .base import GlanceCard


class SessionCard(GlanceCard):
    card_name = "Session"
    card_priority = 8

    def update_data(self, data: GlanceData) -> None:
        g = data.git

        lines = [
            f"Branch: {g.branch}",
            f"Commits: {g.commits_today} today",
        ]
        if g.last_commit_hash:
            lines.append(f"Last: {g.last_commit_hash} {g.last_commit_age}")

        self._body.update("\n".join(lines))

    def render_micro(self, data: GlanceData) -> str:
        g = data.git
        return f"Session {g.branch} {g.commits_today}c"

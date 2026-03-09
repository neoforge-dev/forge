"""Base class for Glance card widgets."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static

from ..data.models import GlanceData


class GlanceCard(Vertical):
    """Base card with title bar and body content area."""

    DEFAULT_CSS = """
    GlanceCard {
        border: solid $surface-lighten-2;
        height: auto;
        min-height: 5;
        max-height: 20;
        padding: 0 1;
    }
    GlanceCard:focus-within {
        border: solid $accent;
    }
    .card-title {
        text-style: bold;
        color: $text;
        margin-bottom: 0;
    }
    .card-body {
        height: auto;
    }
    """

    card_name: str = "Card"
    card_priority: int = 0  # Lower = shown first in constrained layouts

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title_widget = Static(f"-- {self.card_name} --", classes="card-title")
        self._body = Static("Loading...", classes="card-body")

    def compose(self):
        yield self._title_widget
        yield self._body

    def update_data(self, data: GlanceData) -> None:
        """Override in subclasses to render card content."""

    def render_micro(self, data: GlanceData) -> str:
        """Return a single-line summary for micro/statusbar mode."""
        return self.card_name


# Re-export pure helpers from models (avoids textual import for tests)
from ..data.models import progress_bar, sparkline

__all__ = ["GlanceCard", "sparkline", "progress_bar"]

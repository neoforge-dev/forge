"""GlanceDashboard — main Textual application."""

from __future__ import annotations

import logging
import socket
import time

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Static

from .cards import CARD_REGISTRY, GlanceCard
from .data.fetcher import GlanceFetcher
from .data.models import GlanceData

logger = logging.getLogger(__name__)

# Card display order
_CARD_ORDER = ["fleet", "agents", "tasks", "nodes", "dispatch", "session", "errors", "git"]

# Breakpoint thresholds
_BREAKPOINTS = [
    (160, "ultrawide"),
    (120, "wide"),
    (80, "medium"),
    (60, "compact"),
    (0, "micro"),
]


class GlanceDashboard(App):
    """Composable TUI dashboard that adapts to terminal size."""

    CSS_PATH = "glance.tcss"
    TITLE = "FORGE Glance"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("o", "toggle_offline", "Offline"),
        ("question_mark", "toggle_help", "Help"),
        ("f", "fullscreen", "Focus"),
        ("1", "toggle_card('fleet')", "Fleet"),
        ("2", "toggle_card('agents')", "Agents"),
        ("3", "toggle_card('tasks')", "Tasks"),
        ("4", "toggle_card('nodes')", "Nodes"),
        ("5", "toggle_card('dispatch')", "Dispatch"),
        ("6", "toggle_card('session')", "Session"),
        ("7", "toggle_card('errors')", "Errors"),
        ("8", "toggle_card('git')", "Git"),
        ("left", "prev_card", "Prev"),
        ("right", "next_card", "Next"),
        ("tab", "cycle_focus", "Cycle"),
    ]

    def __init__(
        self,
        enabled_cards: set[str],
        refresh_interval: float = 2.0,
        api_url: str | None = None,
        api_token: str | None = None,
    ) -> None:
        super().__init__()
        self._enabled_cards = enabled_cards
        self._refresh_interval = refresh_interval
        self._api_url = api_url
        self._api_token = api_token
        self._fetcher = GlanceFetcher(api_url=api_url, api_token=api_token)
        self._cards: dict[str, GlanceCard] = {}
        self._data = GlanceData()
        self._throughput_history: list[int] = []
        self._current_breakpoint = "wide"
        self._micro_index = 0
        self._offline = api_url is None
        self._hostname = socket.gethostname().strip().lower()
        self._show_help = False

    def compose(self) -> ComposeResult:
        mode = "offline" if self._offline else self._api_url or "localhost"
        yield Static(
            f" FORGE Glance  [dim]|[/]  {self._hostname}  [dim]|[/]  {mode}",
            id="header-bar",
        )
        yield Container(id="card-grid")
        yield Static("", id="status-bar")
        yield Static(
            "[bold]Keyboard[/]\n"
            "  1-8  Toggle cards    q  Quit\n"
            "  r    Refresh         o  Offline toggle\n"
            "  Tab  Cycle focus     f  Fullscreen card\n"
            "  ←→   Micro cycle    ?  This help",
            id="help-overlay",
        )
        yield Footer()

    async def on_mount(self) -> None:
        # Mount cards into the grid container
        grid = self.query_one("#card-grid", Container)
        for name in _CARD_ORDER:
            if name in self._enabled_cards and name in CARD_REGISTRY:
                card = CARD_REGISTRY[name](id=f"card-{name}")
                self._cards[name] = card
                await grid.mount(card)

        self._apply_breakpoint()
        self.set_interval(self._refresh_interval, self._tick)
        # Immediate first fetch
        self.call_later(self._tick)

    def on_resize(self) -> None:
        self._apply_breakpoint()

    def _apply_breakpoint(self) -> None:
        width = self.size.width
        height = self.size.height

        new_bp = "wide"
        for threshold, bp_name in _BREAKPOINTS:
            if width >= threshold:
                new_bp = bp_name
                break

        if self._current_breakpoint != new_bp:
            self.remove_class(self._current_breakpoint)
            self._current_breakpoint = new_bp
            self.add_class(new_bp)

        # Statusbar-only mode for very short terminals (< 8 rows)
        grid = self.query_one("#card-grid", Container)
        if height < 8:
            grid.display = False
            self._update_status_bar()
        else:
            grid.display = True

        # Micro mode: show only one card at a time
        if new_bp == "micro":
            self._apply_micro_visibility()

    def _apply_micro_visibility(self) -> None:
        visible_names = [n for n in _CARD_ORDER if n in self._cards]
        for i, name in enumerate(visible_names):
            card = self._cards[name]
            card.display = i == (self._micro_index % len(visible_names))

    def _update_status_bar(self) -> None:
        d = self._data
        parts = [
            f"FORGE [bold]●{d.active_agents}/{d.total_agents}[/] agents",
            f"✗{d.error_count} err",
            f"Q:{d.tasks.total} tasks",
        ]
        for node in d.nodes:
            if node.is_online:
                from .cards.base import progress_bar

                bar = progress_bar(node.agent_count, node.max_agents, width=3)
                parts.append(f"{node.name}:{bar}")

        status_bar = self.query_one("#status-bar", Static)
        status_bar.update("  ".join(parts))

    async def _tick(self) -> None:
        """Periodic data refresh."""
        try:
            self._data = await self._fetcher.fetch_all()
            self._data.fetch_success = True
            self._data.last_fetch_time = time.monotonic()
        except Exception as e:
            logger.warning("Glance fetch failed: %s", e)
            self._data.fetch_success = False

        # Update throughput history (rolling window of 12)
        self._throughput_history.append(self._data.active_agents)
        self._throughput_history = self._throughput_history[-12:]
        self._data.throughput_samples = list(self._throughput_history)

        for name, card in self._cards.items():
            try:
                card.update_data(self._data)
            except Exception as e:
                logger.warning("Card %s update failed: %s", name, e)

        self._update_header()
        self._update_status_bar()

    def _update_header(self) -> None:
        """Update header bar with stale indicator when data is old or fetch failed."""
        mode = "offline" if self._offline else self._api_url or "localhost"
        is_stale = (
            not self._data.fetch_success
            or self._data.last_fetch_time == 0.0
            or time.monotonic() - self._data.last_fetch_time > 30
        )
        stale = "  [red]STALE[/]" if is_stale else ""
        header = self.query_one("#header-bar", Static)
        header.update(
            f" FORGE Glance  [dim]|[/]  {self._hostname}  [dim]|[/]  {mode}{stale}"
        )

    # -- Actions --

    def action_refresh(self) -> None:
        self.call_later(self._tick)

    def action_toggle_offline(self) -> None:
        self._offline = not self._offline
        if self._offline:
            self._fetcher.api_url = None
        else:
            self._fetcher.api_url = self._api_url
        header = self.query_one("#header-bar", Static)
        mode = "offline" if self._offline else self._api_url or "localhost"
        header.update(f" FORGE Glance  [dim]|[/]  {self._hostname}  [dim]|[/]  {mode}")

    def action_toggle_help(self) -> None:
        self._show_help = not self._show_help
        if self._show_help:
            self.add_class("show-help")
        else:
            self.remove_class("show-help")

    def action_fullscreen(self) -> None:
        focused = self.focused
        if isinstance(focused, GlanceCard):
            if focused.styles.height is not None and str(focused.styles.height) == "100%":
                focused.styles.height = "auto"
                for card in self._cards.values():
                    card.display = True
            else:
                for card in self._cards.values():
                    card.display = card is focused
                focused.styles.height = "100%"

    def action_toggle_card(self, card_name: str) -> None:
        if card_name in self._cards:
            card = self._cards[card_name]
            card.display = not card.display

    def action_prev_card(self) -> None:
        if self._current_breakpoint == "micro":
            self._micro_index = max(0, self._micro_index - 1)
            self._apply_micro_visibility()

    def action_next_card(self) -> None:
        if self._current_breakpoint == "micro":
            visible = [n for n in _CARD_ORDER if n in self._cards]
            self._micro_index = min(len(visible) - 1, self._micro_index + 1)
            self._apply_micro_visibility()

    def action_cycle_focus(self) -> None:
        card_list = [self._cards[n] for n in _CARD_ORDER if n in self._cards]
        if not card_list:
            return
        current = self.focused
        try:
            idx = card_list.index(current)
            next_idx = (idx + 1) % len(card_list)
        except ValueError:
            next_idx = 0
        card_list[next_idx].focus()

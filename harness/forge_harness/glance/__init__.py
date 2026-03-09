"""FORGE Glance — Composable TUI Dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import GlanceDashboard


def create_glance_app(
    *,
    enabled_cards: set[str] | None = None,
    refresh_interval: float = 2.0,
    api_url: str | None = None,
    api_token: str | None = None,
) -> GlanceDashboard:
    """Factory: create a configured GlanceDashboard instance."""
    from .app import GlanceDashboard

    return GlanceDashboard(
        enabled_cards=enabled_cards or {"fleet", "agents", "tasks", "nodes", "dispatch", "session", "errors", "git"},
        refresh_interval=refresh_interval,
        api_url=api_url,
        api_token=api_token,
    )

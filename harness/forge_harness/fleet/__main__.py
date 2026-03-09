"""Fleet dashboard CLI entry point.

Run directly as a module:
    python -m forge_harness.fleet.dashboard

Or use via harness CLI:
    forge-harness fleet dashboard
"""

import sys
from datetime import UTC, datetime

from .dashboard import get_fleet_status

# Try to import rich for pretty output, fall back to ASCII
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def format_age(dt: datetime) -> str:
    """Format datetime as human-readable age (e.g., '2m ago', 'just now')."""
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 10:
        return "just now"
    elif seconds < 60:
        return f"{seconds}s ago"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    else:
        days = seconds // 86400
        return f"{days}d ago"


def print_rich_dashboard():
    """Print dashboard using rich library."""
    console = Console()
    status = get_fleet_status()

    # Determine health color
    health_colors = {
        "healthy": "green",
        "degraded": "yellow",
        "critical": "red",
    }
    health_color = health_colors.get(status.overall_health, "white")

    # Create header
    title = Text()
    title.append("FORGE Fleet Status", style="bold cyan")

    # Create summary
    summary = Text()
    summary.append("Overall Health: ", style="bold")
    summary.append(f"{status.overall_health.upper()}", style=f"bold {health_color}")
    summary.append(f"\nActive: {status.total_active}  │  ", style="green")
    summary.append(f"Idle: {status.total_idle}  │  ", style="yellow")
    summary.append(f"Stale: {status.total_stale}", style="red")

    # Create agent table
    table = Table(show_header=True, header_style="bold magenta", border_style="blue")
    table.add_column("Agent", style="cyan", no_wrap=True, width=15)
    table.add_column("Status", style="white", width=10)
    table.add_column("Last Activity", style="white", width=18)
    table.add_column("Context", style="white", width=10, justify="right")

    # Add rows with status-based styling
    status_styles = {
        "active": "green",
        "idle": "yellow",
        "stale": "red",
    }

    for agent in sorted(status.agents, key=lambda a: (a.status != "active", a.name)):
        status_style = status_styles.get(agent.status, "white")
        status_text = agent.status.upper()
        activity_text = format_age(agent.last_activity)
        context_text = f"{agent.context_estimate}%"

        table.add_row(
            agent.name,
            Text(status_text, style=status_style),
            activity_text,
            context_text,
        )

    if not status.agents:
        table.add_row("No agents", "-", "-", "-")

    # Print everything
    console.print()
    console.print(
        Panel(
            summary,
            title=title,
            border_style=health_color,
            padding=(1, 2),
        )
    )
    console.print()
    console.print(table)
    console.print()


def print_ascii_dashboard():
    """Print dashboard using ASCII art (fallback)."""
    status = get_fleet_status()

    # Box drawing characters
    top_border = "╔════════════════════════════════════════════════════════════╗"
    mid_border = "╠════════════════════════════════════════════════════════════╣"
    bottom_border = "╚════════════════════════════════════════════════════════════╝"
    side = "║"

    print()
    print(top_border)
    print(f"{side}{'FORGE Fleet Status':^60}{side}")
    print(mid_border)
    print(f"{side} Overall Health: {status.overall_health.upper():<43}{side}")
    print(
        f"{side} Active: {status.total_active}  │  "
        f"Idle: {status.total_idle}  │  "
        f"Stale: {status.total_stale:<32}{side}"
    )
    print(mid_border)

    # Table header
    header = (
        f"{side} {'Agent':<15} │ {'Status':<10} │ {'Last Activity':<18} │ {'Context':>8} {side}"
    )
    print(header)
    print(mid_border)

    # Table rows
    if status.agents:
        for agent in sorted(status.agents, key=lambda a: (a.status != "active", a.name)):
            status_text = agent.status.upper()
            activity_text = format_age(agent.last_activity)
            context_text = f"{agent.context_estimate}%"

            row = f"{side} {agent.name:<15} │ {status_text:<10} │ {activity_text:<18} │ {context_text:>8} {side}"
            print(row)
    else:
        empty_row = f"{side} {'No agents':<15} │ {'-':<10} │ {'-':<18} │ {'-':>8} {side}"
        print(empty_row)

    print(bottom_border)
    print()


def main():
    """Main entry point for fleet dashboard CLI."""
    if HAS_RICH:
        print_rich_dashboard()
    else:
        print_ascii_dashboard()

    return 0


if __name__ == "__main__":
    sys.exit(main())

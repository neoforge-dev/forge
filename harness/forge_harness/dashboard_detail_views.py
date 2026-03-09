"""
Detail view rendering methods for FORGE Dashboard.

These methods render detailed information panels for:
- Agents (with terminal output)
- Approvals (with full context and diffs)
- Pipelines (with step-by-step status)
- Patterns (with success history)
- Health Status (system health checks)
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .logging_config import get_logger
from .metrics_common import format_age, format_duration

if TYPE_CHECKING:
    from .dashboard_core import AgentState, ApprovalSummary, PipelineStatus
    from .health_checks import AggregatedHealth

logger = get_logger(__name__)


def render_agent_detail(agent: "AgentState", sessions_dir: Path | None = None) -> Panel:
    """Render agent detail panel."""
    content = Text()

    # Header with agent info
    content.append(f"Agent: {agent.role}\n", style="bold cyan")
    content.append(f"ID: {agent.id}\n", style="dim")
    content.append("\n")

    # Status section
    content.append("Status\n", style="bold yellow")
    status_style = {
        "active": "green",
        "idle": "yellow",
        "error": "red",
        "completed": "blue",
    }.get(agent.status, "white")
    content.append("  State: ", style="white")
    content.append(f"{agent.status}\n", style=status_style)

    if agent.project:
        content.append(f"  Project: {agent.project}\n", style="white")
    if agent.task:
        content.append(f"  Task: {agent.task[:60]}\n", style="white")

    content.append(f"  Progress: {agent.progress}%\n", style="white")

    if agent.last_activity:
        content.append(f"  Last Active: {format_age(agent.last_activity)}\n", style="dim")

    content.append("\n")

    # Recent terminal output
    content.append("Recent Terminal Output\n", style="bold yellow")
    try:
        from .session_tracker import get_session_tracker

        tracker = get_session_tracker()
        # Extract window name from session name
        window = agent.id.split(":")[-1] if ":" in agent.id else agent.id
        output = tracker.get_session_output(window, lines=15)
        if output:
            content.append(output[-800:] + "\n", style="dim cyan")
        else:
            content.append("  No output available\n", style="dim")
    except Exception as e:
        content.append(f"  Unable to capture output: {e}\n", style="dim red")

    content.append("\n")

    # Actions available
    content.append("Actions\n", style="bold yellow")
    content.append("  [a] Assign task\n", style="dim")
    content.append("  [s] Stop agent\n", style="dim")
    content.append("  [r] Restart agent\n", style="dim")
    content.append("  [Esc] Back to list\n", style="dim")

    return Panel(
        content,
        title=f"[bold cyan]Agent Detail: {agent.role}[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def render_approval_detail(approval: "ApprovalSummary", approval_storage_dir: Path) -> Panel:
    """Render approval detail panel."""
    content = Text()

    # Header
    content.append(f"{approval.title}\n", style="bold yellow")
    content.append(f"ID: {approval.id}\n", style="dim")
    content.append("\n")

    # Metadata
    content.append("Details\n", style="bold yellow")
    content.append(f"  Type: {approval.type}\n", style="white")
    content.append(f"  Domain: {approval.domain}\n", style="white")

    priority_style = {
        "critical": "red bold",
        "high": "yellow",
        "normal": "white",
        "low": "dim",
    }.get(approval.priority, "white")
    content.append("  Priority: ", style="white")
    content.append(f"{approval.priority}\n", style=priority_style)

    tier_style = {
        "WATCH": "green",
        "PHONE": "yellow",
        "DESKTOP": "red",
    }.get(approval.tier, "white")
    content.append("  Tier: ", style="white")
    content.append(f"{approval.tier}\n", style=tier_style)

    content.append(f"  Risk Score: {approval.risk_score:.2f}\n", style="white")
    content.append(f"  Age: {format_age(approval.created_at)}\n", style="dim")
    content.append("\n")

    # Load full context from file
    content.append("Context\n", style="bold yellow")
    try:
        approval_file = approval_storage_dir / f"approval_{approval.id}.json"
        if approval_file.exists():
            data = json.loads(approval_file.read_text())
            description = data.get("description", "No description")
            content.append(f"{description[:400]}\n", style="white")

            # Show metadata if available
            metadata = data.get("metadata", {})
            if metadata:
                content.append("\nMetadata\n", style="bold yellow")
                for key, value in list(metadata.items())[:5]:
                    content.append(f"  {key}: {str(value)[:50]}\n", style="dim")

            # Show diff if available
            diff = data.get("diff")
            if diff:
                content.append("\nCode Diff\n", style="bold yellow")
                content.append(f"{diff[:300]}...\n", style="dim cyan")
        else:
            content.append("  Unable to load full context\n", style="dim red")
    except Exception as e:
        content.append(f"  Error loading context: {e}\n", style="dim red")

    content.append("\n")

    # Actions
    content.append("Actions\n", style="bold yellow")
    content.append("  [a] Approve\n", style="green")
    content.append("  [r] Reject\n", style="red")
    content.append("  [Esc] Back to list\n", style="dim")

    return Panel(
        content,
        title="[bold yellow]Approval Detail[/]",
        border_style="yellow",
        padding=(1, 2),
    )


def render_pipeline_detail(pipeline: "PipelineStatus", checkpoint_dir: Path) -> Panel:
    """Render pipeline detail panel."""
    content = Text()

    # Header
    content.append(f"{pipeline.name}\n", style="bold green")
    content.append("\n")

    # Status
    status_style = {
        "running": "green",
        "paused": "yellow",
        "waiting_human_gate": "yellow bold",
    }.get(pipeline.status, "white")
    content.append("Status: ", style="bold")
    content.append(f"{pipeline.status}\n", style=status_style)
    content.append("\n")

    # Progress
    content.append("Progress\n", style="bold yellow")
    content.append(f"  Current Step: {pipeline.current_step}\n", style="white")
    content.append(f"  Step {pipeline.step_index + 1} of {pipeline.total_steps}\n", style="dim")

    # Progress bar
    progress_pct = (
        int((pipeline.step_index / pipeline.total_steps) * 100) if pipeline.total_steps > 0 else 0
    )
    filled = progress_pct // 10
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    content.append(f"  [{bar}] {progress_pct}%\n", style="cyan")
    content.append("\n")

    # Timing
    content.append("Timing\n", style="bold yellow")
    duration = (datetime.now(UTC) - pipeline.started_at).total_seconds()
    content.append(f"  Started: {format_age(pipeline.started_at)}\n", style="white")
    content.append(f"  Duration: {format_duration(duration)}\n", style="magenta")

    # Estimate remaining time
    if pipeline.step_index > 0:
        avg_step_time = duration / (pipeline.step_index + 1)
        remaining_steps = pipeline.total_steps - (pipeline.step_index + 1)
        estimated_remaining = avg_step_time * remaining_steps
        content.append(f"  Est. Remaining: {format_duration(estimated_remaining)}\n", style="dim")

    content.append("\n")

    # Load step details from checkpoint
    content.append("Step Details\n", style="bold yellow")
    try:
        checkpoint_file = checkpoint_dir / f"{pipeline.name}.json"
        if checkpoint_file.exists():
            data = json.loads(checkpoint_file.read_text())
            steps = data.get("pipeline", {}).get("steps", [])
            step_results = data.get("step_results", {})

            # Show all steps with status
            for i, step in enumerate(steps):
                step_name = step.get("name", f"step_{i}")
                if step_name in step_results:
                    result = step_results[step_name]
                    status_icon = "✓" if result.get("status") == "success" else "✗"
                    content.append(
                        f"  {status_icon} {step_name}\n",
                        style="green" if status_icon == "✓" else "red",
                    )
                elif i == pipeline.step_index:
                    content.append(f"  ► {step_name} (current)\n", style="yellow bold")
                elif i > pipeline.step_index:
                    content.append(f"  ○ {step_name}\n", style="dim")
        else:
            content.append("  Unable to load checkpoint\n", style="dim red")
    except Exception as e:
        content.append(f"  Error: {e}\n", style="dim red")

    content.append("\n")

    # Error if present
    if pipeline.error:
        content.append("Error\n", style="bold red")
        content.append(f"{pipeline.error[:200]}\n", style="red")
        content.append("\n")

    # Actions
    content.append("Actions\n", style="bold yellow")
    content.append("  [p] Pause/Resume\n", style="dim")
    content.append("  [x] Cancel\n", style="dim")
    content.append("  [Esc] Back to list\n", style="dim")

    return Panel(
        content,
        title="[bold green]Pipeline Detail[/]",
        border_style="green",
        padding=(1, 2),
    )


def render_pattern_detail(pattern: dict[str, Any]) -> Panel:
    """Render pattern detail panel."""
    content = Text()

    # Header
    name = pattern.get("name", "Unknown Pattern")
    content.append(f"{name}\n", style="bold magenta")
    content.append(f"ID: {pattern.get('id', 'unknown')}\n", style="dim")
    content.append("\n")

    # Metadata
    content.append("Details\n", style="bold yellow")
    content.append(f"  Category: {pattern.get('category', 'unknown')}\n", style="white")

    # Success rate with color coding
    success_rate = pattern.get("success_rate", 0.0)
    if success_rate >= 0.8:
        rate_style = "green"
    elif success_rate >= 0.6:
        rate_style = "yellow"
    else:
        rate_style = "red"
    content.append("  Success Rate: ", style="white")
    content.append(f"{success_rate:.1%}\n", style=rate_style)

    content.append(f"  Uses: {pattern.get('uses', 0)}\n", style="white")
    content.append(f"  Successes: {pattern.get('successes', 0)}\n", style="green")
    content.append(f"  Failures: {pattern.get('failures', 0)}\n", style="red")
    content.append("\n")

    # Description
    content.append("Description\n", style="bold yellow")
    description = pattern.get("description", "No description available")
    content.append(f"{description[:300]}\n", style="white")
    content.append("\n")

    # Recent uses
    content.append("Recent Uses\n", style="bold yellow")
    recent_uses = pattern.get("recent_uses", [])
    if recent_uses:
        for use in recent_uses[:5]:
            timestamp_str = use.get("timestamp", "unknown")
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                age = format_age(timestamp)
            except Exception:
                age = "unknown"
            outcome = use.get("outcome", "unknown")
            outcome_icon = "✓" if outcome == "success" else "✗"
            outcome_style = "green" if outcome == "success" else "red"
            content.append(f"  {outcome_icon} {age}", style=outcome_style)
            domain = use.get("domain", "")
            if domain:
                content.append(f" - {domain}", style="dim")
            content.append("\n")
    else:
        content.append("  No recent uses recorded\n", style="dim")

    content.append("\n")

    # Tags if available
    tags = pattern.get("tags", [])
    if tags:
        content.append("Tags\n", style="bold yellow")
        content.append(f"  {', '.join(tags[:10])}\n", style="cyan")
        content.append("\n")

    # Actions
    content.append("Actions\n", style="bold yellow")
    content.append("  [v] View full history\n", style="dim")
    content.append("  [e] Export pattern\n", style="dim")
    content.append("  [Esc] Back to list\n", style="dim")

    return Panel(
        content,
        title="[bold magenta]Pattern Detail[/]",
        border_style="magenta",
        padding=(1, 2),
    )


def render_health_status(health_data: "AggregatedHealth | None") -> Panel:
    """Render system health check status panel.

    Args:
        health_data: AggregatedHealth object from health checks registry,
                    or None if not available

    Returns:
        Panel with health status display
    """
    if health_data is None:
        content = Text("[dim]Unable to load health status[/dim]")
        return Panel(
            content,
            title="[bold blue]Health Status[/]",
            border_style="blue",
            padding=(1, 2),
        )

    # Create table
    table = Table(
        title="System Health",
        title_style="bold blue",
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Service", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right", style="magenta")
    table.add_column("Message", style="white")

    # Add service rows
    if health_data.services:
        for service in health_data.services:
            # Status icon and color
            if service.status.value == "healthy":
                status_text = "[green]✓[/green] healthy"
            elif service.status.value == "degraded":
                status_text = "[yellow]⚠[/yellow] degraded"
            else:  # unhealthy
                status_text = "[red]✗[/red] unhealthy"

            # Latency formatting
            latency_text = f"{service.latency_ms:.0f}ms"

            # Message (truncated)
            message = service.message[:50] if service.message else "-"

            table.add_row(
                service.name[:30],
                status_text,
                latency_text,
                message,
            )
    else:
        table.add_row("[dim]No services checked[/dim]", "", "", "")

    # Add summary row
    summary = health_data.to_dict()["summary"]
    table.add_row(
        "[bold]Summary[/]",
        f"[bold]{summary['healthy']}/{summary['total']}[/]",
        f"{health_data.total_latency_ms:.0f}ms",
        f"Degraded: {summary['degraded']}, Unhealthy: {summary['unhealthy']}",
    )

    # Add timestamp row
    timestamp_text = f"Last Check: {format_age(health_data.checked_at)}"
    table.add_row(
        "[dim]Timestamp[/]",
        "",
        "",
        f"[dim]{timestamp_text}[/dim]",
    )

    return Panel(
        table,
        title="[bold blue]Health Status[/]",
        border_style="blue",
        padding=(0, 1),
    )

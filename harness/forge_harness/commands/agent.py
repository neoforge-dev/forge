"""Agent command group."""

from __future__ import annotations

import click

from .common import get_client


@click.group()
@click.pass_context
def agents(ctx: click.Context) -> None:
    """Manage agents via Command Center API.

    View, register, message, and control agents running in the FORGE harness.
    """
    pass


def get_agents_data(status: str | None = None, url: str = "http://localhost:8080", token: str | None = None) -> list[dict]:
    """Get agents data as a list of dictionaries."""
    from ..command_center_client import AgentStatus
    client = get_client(url, token)
    status_filter = AgentStatus(status) if status else None
    agents_list_data = client.sync_list_agents(status=status_filter)

    return [
        {
            "id": a.id,
            "role": a.role,
            "project": a.project,
            "task": a.task,
            "status": a.status.value,
            "progress": a.progress,
        }
        for a in agents_list_data
    ]


@agents.command("list")
@click.option(
    "--status", type=click.Choice(["active", "idle", "completed", "error"]), help="Filter by status"
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", "-t", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_list(
    ctx: click.Context, status: str | None, json_output: bool, url: str, token: str | None
) -> None:
    """List all registered agents."""
    from rich.console import Console
    from rich.table import Table

    try:
        data = get_agents_data(status, url, token)
    except Exception as e:
        if json_output:
            import json as json_lib

            click.echo(json_lib.dumps({"status": "error", "error": str(e), "url": url}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    if json_output:
        import json as json_lib
        click.echo(json_lib.dumps(data, indent=2))
        return

    if not data:
        click.echo("No agents registered.")
        return

    console = Console()
    table = Table(title="Registered Agents", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Role", style="cyan")
    table.add_column("Project", style="green")
    table.add_column("Task", style="white", max_width=40)
    table.add_column("Status", justify="center")
    table.add_column("Progress", justify="right")

    status_colors = {"active": "green", "idle": "yellow", "completed": "blue", "error": "red"}

    for agent in data:
        color = status_colors.get(agent["status"], "white")
        table.add_row(
            agent["id"][:8] + "...",
            agent["role"],
            agent["project"] or "-",
            (agent["task"] or "-")[:40],
            f"[{color}]{agent['status']}[/{color}]",
            f"{agent['progress']}%",
        )

    console.print(table)


@agents.command("show")
@click.argument("agent_id")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", "-t", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_show(ctx: click.Context, agent_id: str, url: str, token: str | None) -> None:
    """Show details for a specific agent."""
    from rich.console import Console
    from rich.panel import Panel

    client = get_client(url, token)
    agent = client.sync_get_agent(agent_id)

    if not agent:
        click.echo(f"Agent not found: {agent_id}", err=True)
        raise SystemExit(1)

    console = Console()

    info = f"""[cyan]ID:[/cyan] {agent.id}
[cyan]Role:[/cyan] {agent.role}
[cyan]Name:[/cyan] {agent.name or "-"}
[cyan]Project:[/cyan] {agent.project or "-"}
[cyan]Task:[/cyan] {agent.task or "-"}
[cyan]Status:[/cyan] {agent.status.value}
[cyan]Progress:[/cyan] {agent.progress}%
[cyan]Current Task:[/cyan] {agent.current_task or "-"}
[cyan]Files Modified:[/cyan] {len(agent.files_modified)}
[cyan]Messages:[/cyan] {agent.messages_count}
[cyan]Registered:[/cyan] {agent.registered_at or "-"}
[cyan]Last Activity:[/cyan] {agent.last_activity or "-"}"""

    console.print(Panel(info, title=f"Agent: {agent.role}", border_style="cyan"))


@agents.command("register")
@click.option("--role", "-r", required=True, help="Agent role (e.g., content-agent, tech-agent)")
@click.option("--task", "-t", required=True, help="Task description")
@click.option("--project", "-p", help="Project path")
@click.option("--name", "-n", help="Agent name")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_register(
    ctx: click.Context,
    role: str,
    task: str,
    project: str | None,
    name: str | None,
    url: str,
    token: str | None,
) -> None:
    """Register a new agent."""
    client = get_client(url, token)
    agent = client.sync_register_agent(role=role, task=task, project=project)

    if agent:
        click.echo(f"Agent registered: {agent.id}")
        click.echo(f"  Role: {agent.role}")
        click.echo(f"  Task: {agent.task}")
    else:
        click.echo("Failed to register agent.", err=True)
        raise SystemExit(1)


@agents.command("message")
@click.argument("agent_id")
@click.argument("content")
@click.option("--type", "msg_type", default="instruction", help="Message type")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", "-t", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_message(
    ctx: click.Context, agent_id: str, content: str, msg_type: str, url: str, token: str | None
) -> None:
    """Send a message to an agent."""
    client = get_client(url, token)
    success = client.sync_send_message(agent_id, content)

    if success:
        click.echo(f"Message sent to agent {agent_id[:8]}...")
    else:
        click.echo("Failed to send message.", err=True)
        raise SystemExit(1)


@agents.command("broadcast")
@click.argument("content")
@click.option("--type", "msg_type", default="instruction", help="Message type")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", "-t", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_broadcast(
    ctx: click.Context, content: str, msg_type: str, url: str, token: str | None
) -> None:
    """Broadcast a message to all agents."""
    client = get_client(url, token)
    count = client.sync_broadcast_message(content)

    click.echo(f"Message broadcast to {count} agents.")


@agents.command("complete")
@click.argument("agent_id")
@click.option("--summary", "-s", help="Completion summary")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", "-t", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_complete(
    ctx: click.Context, agent_id: str, summary: str | None, url: str, token: str | None
) -> None:
    """Mark an agent as completed."""
    client = get_client(url, token)
    success = client.sync_complete_agent(agent_id, summary)

    if success:
        click.echo(f"Agent {agent_id[:8]}... marked as completed.")
    else:
        click.echo("Failed to complete agent.", err=True)
        raise SystemExit(1)


@agents.command("heartbeat")
@click.argument("agent_id")
@click.option("--url", default="http://localhost:8080", help="Command Center API URL")
@click.option("--token", "-t", help="API authentication token (or use FORGE_WEBHOOK_TOKEN env)")
@click.pass_context
def agents_heartbeat(ctx: click.Context, agent_id: str, url: str, token: str | None) -> None:
    """Send heartbeat to indicate agent is still alive."""
    client = get_client(url, token)
    success = client.sync_heartbeat(agent_id)

    if success:
        click.echo(f"♡ Heartbeat sent for agent {agent_id[:8]}...")
    else:
        click.echo("Failed to send heartbeat.", err=True)
        raise SystemExit(1)

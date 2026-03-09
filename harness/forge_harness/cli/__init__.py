"""
FORGE v3 Python CLI (cli)
============================

A lightweight, agent-friendly CLI that talks directly to the Go v3 backend
over HTTP. This is intentionally small and focused on the core workflows:

- Status checks
- Task creation
- Simple loop runner (status polling)
- Daemon health checks
"""

from __future__ import annotations

import os

import httpx
import typer

app = typer.Typer(help="FORGE v3 CLI — HTTP client for the forge-v3 daemon.")


def _get_base_url() -> str:
    """Return the base URL for the v3 daemon."""
    return os.environ.get("FORGE_API_URL", "http://localhost:8081")


def _client() -> httpx.Client:
    return httpx.Client(base_url=_get_base_url(), timeout=10.0)


@app.command("status")
def status() -> None:
    """Show v3 daemon health and status information."""
    base = _get_base_url()
    with _client() as client:
        health_ok = False
        status_body: dict | None = None

        # Health
        try:
            resp = client.get("/health")
            health_ok = resp.status_code == 200 and resp.json().get("status") == "ok"
        except Exception:
            health_ok = False

        # Status
        try:
            resp = client.get("/status")
            if resp.status_code == 200:
                status_body = resp.json()
        except Exception:
            status_body = None

    typer.echo(f"FORGE v3 daemon @ {base}")
    typer.echo(f"  Health: {'OK' if health_ok else 'UNAVAILABLE'}")
    if status_body:
        typer.echo(
            f"  Version: {status_body.get('version', '-')}, "
            f"Phase: {status_body.get('phase', '-')}, "
            f"Status: {status_body.get('status', '-')}"
        )
    else:
        typer.echo("  Status endpoint: unavailable")


task_app = typer.Typer(help="Task management commands.")
app.add_typer(task_app, name="task")


@task_app.command("create")
def task_create(
    domain: str = typer.Option(..., "--domain", "-d", help="Domain name (e.g. codeswiftr-com)"),
    project: str = typer.Option(..., "--project", "-p", help="Project name within the domain"),
    type_: str = typer.Option(
        "feature",
        "--type",
        "-t",
        help="Task type (feature, bugfix, research, refactor)",
    ),
    priority: int = typer.Option(
        1, "--priority", help="Numeric priority (higher = more important)"
    ),
    title: str = typer.Option(..., "--title", help="Short title/summary for the task"),
    description: str | None = typer.Option(
        None,
        "--description",
        "-m",
        help="Longer description of the task",
    ),
) -> None:
    """Create a new v3 task via the HTTP API."""
    payload = {
        "id": "",
        "domain": domain,
        "project": project,
        "type": type_,
        "priority": priority,
        "status": "",
        "result": "",
        "error": "",
    }
    # Attach human description in result field for now; the Go side can evolve schema later.
    if description:
        payload["result"] = f"{title}: {description}"
    else:
        payload["result"] = title

    with _client() as client:
        try:
            resp = client.post("/tasks", json=payload)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"Request failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)

    if resp.status_code != 200:
        typer.secho(f"Task create failed: HTTP {resp.status_code}", fg=typer.colors.RED)
        try:
            typer.echo(resp.text)
        except Exception:
            pass
        raise typer.Exit(code=1)

    data = resp.json()
    typer.secho("Task created:", fg=typer.colors.GREEN)
    typer.echo(f"  ID:       {data.get('id', '-')}")
    typer.echo(f"  Domain:   {data.get('domain', '-')}")
    typer.echo(f"  Project:  {data.get('project', '-')}")
    typer.echo(f"  Status:   {data.get('status', '-')}")


loop_app = typer.Typer(help="Simple v3 loop utilities.")
app.add_typer(loop_app, name="loop")


@loop_app.command("run")
def loop_run(
    iterations: int = typer.Option(
        10,
        "--iterations",
        "-n",
        help="Number of status iterations to run (for monitoring).",
    ),
    delay: float = typer.Option(
        5.0,
        "--delay",
        "-d",
        help="Delay in seconds between iterations.",
    ),
) -> None:
    """Run a simple monitoring loop against the v3 daemon."""
    base = _get_base_url()
    typer.echo(f"Starting v3 loop against {base} for {iterations} iterations...")

    with _client() as client:
        for i in range(1, iterations + 1):
            try:
                health = client.get("/health")
                status_resp = client.get("/status")
                ok = health.status_code == 200 and health.json().get("status") == "ok"
                status_body = status_resp.json() if status_resp.status_code == 200 else {}
            except Exception as exc:  # noqa: BLE001
                typer.secho(f"[{i}] Error contacting daemon: {exc}", fg=typer.colors.RED)
                ok = False
                status_body = {}

            line = f"[{i}] Health={'OK' if ok else 'FAIL'}"
            if status_body:
                line += (
                    f" | version={status_body.get('version', '-')}"
                    f" phase={status_body.get('phase', '-')}"
                    f" status={status_body.get('status', '-')}"
                )
            typer.echo(line)

            if i < iterations:
                try:
                    import time

                    time.sleep(delay)
                except KeyboardInterrupt:
                    typer.echo("Loop interrupted by user.")
                    break


daemon_app = typer.Typer(help="Daemon health helpers (non-invasive).")
app.add_typer(daemon_app, name="daemon")


@daemon_app.command("start")
def daemon_start_help() -> None:
    """Show how to start the forge-v3 daemon."""
    typer.echo(
        "This CLI talks to an existing forge-v3 daemon over HTTP.\n"
        "To start the daemon, run (from the repo root):\n"
        "  cd cmd/forge-v3 && go build -o forge-v3 . && ./forge-v3\n"
        "Then re-run `forge status`."
    )


@daemon_app.command("stop")
def daemon_stop_help() -> None:
    """Show how to stop the forge-v3 daemon."""
    typer.echo(
        "To stop the forge-v3 daemon, terminate the process running the binary.\n"
        "For example (Linux/macOS):\n"
        "  pkill -f 'cmd/forge-v3/forge-v3'  # or use your process manager\n"
    )


def _show_help() -> None:
    """Display the main help message for agents discovering the tool."""
    typer.echo("""
╔══════════════════════════════════════════════════════════════════╗
║                    FORGE v3 CLI                                  ║
║         HTTP client for the forge-v3 daemon                      ║
╚══════════════════════════════════════════════════════════════════╝

USAGE: forge [COMMAND] [OPTIONS]

CORE COMMANDS:
  status              Show v3 daemon health and status
  task create         Create a new v3 task
  loop run            Monitor daemon status in a loop
  fleet status        Check fleet agent status
  dispatch send       Dispatch task to fleet agent

DAEMON COMMANDS:
  daemon start        Show how to start the v3 daemon
  daemon stop         Show how to stop the v3 daemon

HANDOFF COMMANDS:
  handoff create      Create a handoff document

EXAMPLES:
  # Check system status
  $ forge status

  # Create a task
  $ forge task create -d codeswiftr-com -p interview-simulator --title "Fix auth bug"

  # Dispatch to an agent
  $ forge dispatch send forge:kimi "Research FastAPI patterns"

  # Check fleet
  $ forge fleet status

For detailed help on any command:
  $ forge [COMMAND] --help

Documentation: docs/v3/
Daemon URL: http://localhost:8081
""")


def main() -> None:
    """CLI entry point used by pyproject scripts."""
    import sys

    # Show help if no arguments provided (aids agent discovery)
    if len(sys.argv) == 1:
        _show_help()
        return

    # Handle --help flag explicitly for no-command case
    if len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h"):
        _show_help()
        return

    app()


if __name__ == "__main__":
    main()


fleet_app = typer.Typer(help="Manage the V3 fleet.")
app.add_typer(fleet_app, name="fleet")


@fleet_app.command("status")
def fleet_status() -> None:
    """Check the status of the fleet."""
    import subprocess

    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", "forge"], capture_output=True, text=True
        )
        if result.returncode == 0:
            typer.echo("Active V3 Fleet Nodes (Local):")
            for line in result.stdout.strip().split("\n"):
                typer.echo(f"  {line}")
        else:
            typer.echo("No active local fleet sessions found.")
    except Exception as e:
        typer.echo(f"Error checking fleet status: {e}")


dispatch_app = typer.Typer(help="Dispatch tasks to fleet agents.")
app.add_typer(dispatch_app, name="dispatch")


@dispatch_app.command("send")
def dispatch_send(
    agent: str,
    message: str,
    bridge: bool = typer.Option(
        True, "--bridge/--no-bridge", help="Use v2-v3 bridge for actual delivery"
    ),
) -> None:
    """Send a dispatch message to a specific agent.

    Uses the v2-v3 bridge to ensure actual delivery to tmux agents.
    """
    import asyncio

    if bridge:
        typer.echo(f"Dispatching to {agent} (via v2-v3 bridge)...")
        try:
            from ..bridge.dispatch_bridge import dispatch as bridge_dispatch

            result = asyncio.run(bridge_dispatch(agent, message))

            if result.dispatch_success:
                typer.secho(
                    f"✓ Dispatched to {agent} in {result.delivery_time_ms:.0f}ms",
                    fg=typer.colors.GREEN,
                )
                if result.task_id:
                    typer.echo(f"  v3 task: {result.task_id}")
            else:
                typer.secho(f"✗ Dispatch failed: {result.error}", fg=typer.colors.RED)
                raise typer.Exit(code=1)
        except ImportError as e:
            typer.secho(f"Bridge not available: {e}", fg=typer.colors.YELLOW)
            typer.secho("Falling back to v3 queue only", fg=typer.colors.YELLOW)
            _dispatch_v3_only(agent, message)
        except Exception as e:
            typer.secho(f"Dispatch error: {e}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
    else:
        typer.echo(f"Dispatching to {agent} (v3 queue only, no delivery)...")
        _dispatch_v3_only(agent, message)


def _dispatch_v3_only(agent: str, message: str) -> None:
    """Fallback: create task in v3 without actual delivery."""
    with _client() as client:
        try:
            resp = client.post(
                "/tasks",
                json={
                    "id": "",
                    "domain": "default",
                    "project": "default",
                    "type": "dispatch",
                    "priority": 1,
                    "status": "pending",
                    "result": f"Dispatch to {agent}: {message}",
                    "error": "",
                    "assigned_to": agent.replace("forge:", ""),
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                typer.echo(f"Task created: {data.get('id')}")
                typer.secho(
                    "WARNING: Task created but NOT delivered to agent!", fg=typer.colors.YELLOW
                )
                typer.echo("Use --bridge (default) for actual delivery.")
            else:
                typer.secho(f"Failed: HTTP {resp.status_code}", fg=typer.colors.RED)
        except Exception as e:
            typer.secho(f"Error: {e}", fg=typer.colors.RED)


handoff_app = typer.Typer(help="Manage handoffs.")
app.add_typer(handoff_app, name="handoff")


@handoff_app.command("create")
def handoff_create() -> None:
    """Create a handoff document."""
    typer.echo(
        "Handoff creation is now handled automatically by the V3 continuous runner or context envelopes."
    )

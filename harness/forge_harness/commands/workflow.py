"""Workflow CLI commands for fleet workflow management.

Commands:
    forge workflow validate <file>  - Validate workflow YAML/JSON
    forge workflow dag <file>       - Show DAG structure
    
    forge fleet status              - Show fleet status
    forge fleet health              - Show detailed health info
    forge fleet agents              - List all agents
    forge fleet register <id>       - Register a new agent
    forge fleet transition <id> <state> - Transition agent state
    forge fleet assign <id> <task>  - Assign task to agent
    forge fleet release <id>        - Release agent from task
    forge fleet history <id>        - Show agent history
    forge fleet broadcast <msg>     - Broadcast message to all agents
    forge fleet pause-all           - Pause all active agents
    forge fleet resume-all          - Resume all paused agents
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from forge_harness.logging_config import get_logger
from forge_harness.workflow.engine import WorkflowDefinition
from forge_harness.workflow.state import AgentState, FleetStateManager

logger = get_logger(__name__)


@click.group(name="workflow")
def workflow_cli():
    """Fleet workflow management commands."""
    pass


@click.group(name="fleet")
def fleet_cli():
    """Fleet state management commands."""
    pass


# =============================================================================
# Workflow Commands
# =============================================================================


@workflow_cli.command(name="validate")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["yaml", "json"]), default="yaml")
def validate_workflow(file_path: str, fmt: str):
    """Validate a workflow definition file."""
    path = Path(file_path)

    try:
        if fmt == "yaml":
            workflow = WorkflowDefinition.from_yaml(path)
        else:
            workflow = WorkflowDefinition.from_json(path)

        # Validate DAG
        topo_order = workflow.dag.topological_sort()
        parallel_groups = workflow.dag.get_parallel_groups()

        click.echo(f"✅ Workflow '{workflow.name}' is valid")
        click.echo(f"   ID: {workflow.id}")
        click.echo(f"   Tasks: {len(workflow.dag._nodes)}")
        click.echo(f"   Parallel execution levels: {len(parallel_groups)}")
        click.echo(f"   Topological order: {' → '.join(n.id for n in topo_order[:5])}{'...' if len(topo_order) > 5 else ''}")

        # Show parallel groups
        click.echo("\n   Execution plan:")
        for i, group in enumerate(parallel_groups, 1):
            task_names = ", ".join(n.name for n in group[:3])
            if len(group) > 3:
                task_names += f" (+{len(group) - 3} more)"
            click.echo(f"   Level {i}: {task_names}")

    except Exception as exc:
        click.echo(f"❌ Validation failed: {exc}", err=True)
        raise click.Exit(1)


@workflow_cli.command(name="dag")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["yaml", "json"]), default="yaml")
def show_dag(file_path: str, fmt: str):
    """Show DAG structure of a workflow."""
    path = Path(file_path)

    try:
        if fmt == "yaml":
            workflow = WorkflowDefinition.from_yaml(path)
        else:
            workflow = WorkflowDefinition.from_json(path)

        dag = workflow.dag

        click.echo(f"📊 DAG Structure for '{workflow.name}':\n")

        # Show nodes
        click.echo("Tasks:")
        for node_id, node in dag._nodes.items():
            deps = f" (depends on: {', '.join(node.dependencies)})" if node.dependencies else ""
            caps = f" [capabilities: {', '.join(node.agent_capabilities)}]" if node.agent_capabilities else ""
            click.echo(f"  • {node_id}: {node.name}{deps}{caps}")

        # Show parallel groups
        click.echo("\nParallel Execution Groups:")
        groups = dag.get_parallel_groups()
        for i, group in enumerate(groups, 1):
            click.echo(f"  Level {i}: {' | '.join(n.id for n in group)}")

        # Show critical path
        try:
            critical = dag.get_critical_path()
            click.echo(f"\nCritical Path: {' → '.join(n.id for n in critical)}")
        except Exception:
            pass

    except Exception as exc:
        click.echo(f"❌ Failed to load workflow: {exc}", err=True)
        raise click.Exit(1)


# =============================================================================
# Fleet Commands
# =============================================================================


@fleet_cli.command(name="status")
@click.option("--state-dir", type=click.Path(), help="State directory path")
def fleet_status(state_dir: str | None):
    """Show current fleet status."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    summary = state_manager.get_fleet_summary()

    click.echo("🚀 Fleet Status\n")
    click.echo(f"Total Agents: {summary['total_agents']}")
    click.echo(f"Available: {summary['available']}")
    click.echo(f"Working: {summary['working']}")
    click.echo(f"Failed: {summary['failed']}")

    click.echo("\nState Distribution:")
    for state, count in summary['by_state'].items():
        if count > 0:
            click.echo(f"  {state}: {count}")


@fleet_cli.command(name="agents")
@click.option("--state-dir", type=click.Path())
@click.option("--state", help="Filter by state")
def list_agents(state_dir: str | None, state: str | None):
    """List all agents in the fleet."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    if state:
        try:
            agent_state = AgentState(state)
            agents = state_manager.get_agents_by_state(agent_state)
        except ValueError:
            click.echo(f"❌ Invalid state: {state}", err=True)
            raise click.Exit(1)
    else:
        agents = state_manager.get_all_agents()

    if not agents:
        click.echo("No agents found.")
        return

    click.echo(f"{'Agent ID':<30} {'State':<12} {'Task':<20} {'Context':<8}")
    click.echo("-" * 70)

    for agent in agents:
        task = agent.current_task or "-"
        if len(task) > 18:
            task = task[:15] + "..."
        click.echo(
            f"{agent.agent_id:<30} "
            f"{agent.state.value:<12} "
            f"{task:<20} "
            f"{agent.context_percentage}%"
        )


@fleet_cli.command(name="register")
@click.argument("agent_id")
@click.option("--capability", "capabilities", multiple=True)
@click.option("--state-dir", type=click.Path())
def register_agent(agent_id: str, capabilities: tuple, state_dir: str | None):
    """Register a new agent in the fleet."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    agent = state_manager.register_agent(agent_id, list(capabilities))
    click.echo(f"✅ Registered agent: {agent.agent_id}")
    if agent.capabilities:
        click.echo(f"   Capabilities: {', '.join(agent.capabilities)}")


@fleet_cli.command(name="transition")
@click.argument("agent_id")
@click.argument("new_state")
@click.option("--reason", default="Manual transition")
@click.option("--state-dir", type=click.Path())
def transition_agent(agent_id: str, new_state: str, reason: str, state_dir: str | None):
    """Transition an agent to a new state."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    try:
        state = AgentState(new_state)
    except ValueError:
        valid_states = ", ".join(s.value for s in AgentState)
        click.echo(f"❌ Invalid state. Valid states: {valid_states}", err=True)
        raise click.Exit(1)

    success = state_manager.transition_agent(agent_id, state, reason)

    if success:
        click.echo(f"✅ Agent {agent_id} transitioned to {new_state}")
    else:
        agent = state_manager.get_agent(agent_id)
        if agent:
            valid = ", ".join(s.value for s in AgentState if agent.can_transition_to(s))
            click.echo(f"❌ Invalid transition from {agent.state.value}", err=True)
            click.echo(f"   Valid transitions: {valid}", err=True)
        else:
            click.echo(f"❌ Agent {agent_id} not found", err=True)
        raise click.Exit(1)


@fleet_cli.command(name="assign")
@click.argument("agent_id")
@click.argument("task_id")
@click.option("--workflow", help="Workflow ID")
@click.option("--state-dir", type=click.Path())
def assign_task(agent_id: str, task_id: str, workflow: str | None, state_dir: str | None):
    """Assign a task to an agent."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    success = state_manager.assign_task(agent_id, task_id, workflow)

    if success:
        click.echo(f"✅ Assigned task {task_id} to agent {agent_id}")
    else:
        agent = state_manager.get_agent(agent_id)
        if agent:
            click.echo(
                f"❌ Agent {agent_id} not available (state: {agent.state.value})",
                err=True,
            )
        else:
            click.echo(f"❌ Agent {agent_id} not found", err=True)
        raise click.Exit(1)


@fleet_cli.command(name="release")
@click.argument("agent_id")
@click.option("--status", default="completed", type=click.Choice(["completed", "failed", "idle"]))
@click.option("--state-dir", type=click.Path())
def release_agent(agent_id: str, status: str, state_dir: str | None):
    """Release an agent from its current task."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    success = state_manager.release_agent(agent_id, status)

    if success:
        click.echo(f"✅ Released agent {agent_id} with status: {status}")
    else:
        click.echo(f"❌ Failed to release agent {agent_id}", err=True)
        raise click.Exit(1)


@fleet_cli.command(name="history")
@click.argument("agent_id")
@click.option("--state-dir", type=click.Path())
def agent_history(agent_id: str, state_dir: str | None):
    """Show state transition history for an agent."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    agent = state_manager.get_agent(agent_id)
    if not agent:
        click.echo(f"❌ Agent {agent_id} not found", err=True)
        raise click.Exit(1)

    click.echo(f"📊 Agent {agent_id} History\n")
    click.echo(f"Current State: {agent.state.value}")
    click.echo(f"Capabilities: {', '.join(agent.capabilities) or 'None'}")
    click.echo(f"Current Task: {agent.current_task or 'None'}")
    click.echo(f"Context: {agent.context_percentage}%")
    click.echo(f"Last Heartbeat: {agent.last_heartbeat or 'Never'}")

    if agent.state_history:
        click.echo("\nState Transitions:")
        for entry in agent.state_history:
            click.echo(
                f"  {entry['from']} → {entry['to']} "
                f"({entry.get('timestamp', 'unknown')})"
            )
            if entry.get('reason'):
                click.echo(f"    Reason: {entry['reason']}")


@fleet_cli.command(name="broadcast")
@click.argument("message")
@click.option("--type", "msg_type", default="info", type=click.Choice(["info", "warning", "urgent", "command"]))
@click.option("--filter-state", help="Only broadcast to agents in this state")
@click.option("--filter-capability", help="Only broadcast to agents with this capability")
@click.option("--state-dir", type=click.Path())
@click.option("--json", "json_output", is_flag=True, help="Output results as JSON")
def broadcast_message(
    message: str,
    msg_type: str,
    filter_state: str | None,
    filter_capability: str | None,
    state_dir: str | None,
    json_output: bool,
):
    """Broadcast a message to all fleet agents.
    
    Examples:
        forge fleet broadcast "System maintenance in 5 minutes"
        forge fleet broadcast "Stop all tasks" --type urgent
        forge fleet broadcast "Hello workers" --filter-state working
    """
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    # Get target agents
    agents = state_manager.get_all_agents()

    if filter_state:
        try:
            from forge_harness.workflow.state import AgentState
            state_enum = AgentState(filter_state)
            agents = [a for a in agents if a.state == state_enum]
        except ValueError:
            click.echo(f"❌ Invalid state filter: {filter_state}", err=True)
            raise click.Exit(1)

    if filter_capability:
        agents = [a for a in agents if filter_capability in a.capabilities]

    if not agents:
        click.echo("⚠️  No agents match the filter criteria", err=True)
        raise click.Exit(0)

    # Create broadcast record
    broadcast_record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": msg_type,
        "message": message,
        "target_count": len(agents),
        "target_agents": [a.agent_id for a in agents],
    }

    # In a real implementation, this would send to agents via their communication channel
    # For now, we log it and update agent metadata
    for agent in agents:
        if "broadcasts" not in agent.metadata:
            agent.metadata["broadcasts"] = []
        agent.metadata["broadcasts"].append({
            "type": msg_type,
            "message": message,
            "received_at": datetime.now(UTC).isoformat(),
        })
        # Persist the update
        state_manager._save_state(agent.agent_id)

    if json_output:
        click.echo(json.dumps(broadcast_record, indent=2))
    else:
        click.echo(f"📢 Broadcast [{msg_type.upper()}] to {len(agents)} agent(s):")
        click.echo(f"   Message: {message}")
        click.echo("\n   Target agents:")
        for agent in agents:
            click.echo(f"     • {agent.agent_id} ({agent.state.value})")


@fleet_cli.command(name="pause-all")
@click.option("--reason", default="Manual pause")
@click.option("--state-dir", type=click.Path())
def pause_all_agents(reason: str, state_dir: str | None):
    """Pause all active agents in the fleet."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    from forge_harness.workflow.state import AgentState

    # Get all working/assigned agents
    active_states = {AgentState.WORKING, AgentState.ASSIGNED}
    agents = [a for a in state_manager.get_all_agents() if a.state in active_states]

    if not agents:
        click.echo("ℹ️  No active agents to pause")
        return

    paused_count = 0
    for agent in agents:
        if state_manager.transition_agent(agent.agent_id, AgentState.PAUSED, reason):
            paused_count += 1

    click.echo(f"⏸️  Paused {paused_count}/{len(agents)} agent(s)")
    if reason:
        click.echo(f"   Reason: {reason}")


@fleet_cli.command(name="resume-all")
@click.option("--reason", default="Manual resume")
@click.option("--state-dir", type=click.Path())
def resume_all_agents(reason: str, state_dir: str | None):
    """Resume all paused agents in the fleet."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    from forge_harness.workflow.state import AgentState

    agents = state_manager.get_agents_by_state(AgentState.PAUSED)

    if not agents:
        click.echo("ℹ️  No paused agents to resume")
        return

    resumed_count = 0
    for agent in agents:
        if state_manager.transition_agent(agent.agent_id, AgentState.IDLE, reason):
            resumed_count += 1

    click.echo(f"▶️  Resumed {resumed_count}/{len(agents)} agent(s)")


@fleet_cli.command(name="health")
@click.option("--state-dir", type=click.Path())
@click.option("--json", "json_output", is_flag=True)
def fleet_health(state_dir: str | None, json_output: bool):
    """Show detailed fleet health information."""
    if state_dir:
        state_manager = FleetStateManager(state_dir=Path(state_dir))
    else:
        state_manager = FleetStateManager()

    agents = state_manager.get_all_agents()

    # Calculate health metrics
    now = datetime.now(UTC)
    stale_threshold = timedelta(minutes=30)

    total = len(agents)
    healthy = 0
    stale = 0
    overloaded = 0

    for agent in agents:
        is_stale = False
        if agent.last_heartbeat:
            if now - agent.last_heartbeat > stale_threshold:
                is_stale = True
                stale += 1

        if agent.context_percentage > 60:
            overloaded += 1

        if not is_stale and agent.state not in {AgentState.FAILED, AgentState.TERMINATED}:
            healthy += 1

    health_report = {
        "timestamp": now.isoformat(),
        "total_agents": total,
        "healthy": healthy,
        "stale": stale,
        "overloaded": overloaded,
        "by_state": {s.value: len(state_manager.get_agents_by_state(s)) for s in AgentState},
        "agents": [
            {
                "id": a.agent_id,
                "state": a.state.value,
                "context": a.context_percentage,
                "heartbeat": a.last_heartbeat.isoformat() if a.last_heartbeat else None,
                "task": a.current_task,
            }
            for a in agents
        ],
    }

    if json_output:
        click.echo(json.dumps(health_report, indent=2))
    else:
        click.echo("🏥 Fleet Health Report\n")
        click.echo(f"Total Agents: {total}")
        click.echo(f"Healthy: {healthy} ({100*healthy//total if total else 0}%)")
        click.echo(f"Stale: {stale}")
        click.echo(f"Overloaded: {overloaded}")

        if stale > 0 or overloaded > 0:
            click.echo("\n⚠️  Issues detected:")
            for agent in agents:
                issues = []
                if agent.last_heartbeat and now - agent.last_heartbeat > stale_threshold:
                    issues.append("stale")
                if agent.context_percentage > 60:
                    issues.append("overloaded")
                if issues:
                    click.echo(f"   • {agent.agent_id}: {', '.join(issues)}")


# =============================================================================
# CLI Registration
# =============================================================================


def register_commands(cli: click.Group):
    """Register workflow and fleet commands with the main CLI."""
    cli.add_command(workflow_cli)
    cli.add_command(fleet_cli)


# Standalone entry point
if __name__ == "__main__":
    # Create standalone CLI for testing
    standalone_cli = click.Group()
    register_commands(standalone_cli)
    standalone_cli()

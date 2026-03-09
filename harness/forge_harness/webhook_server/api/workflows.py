"""Workflow API Endpoints

REST API for workflow management and execution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.workflow.dag import DAG, TaskNode
from forge_harness.workflow.engine import WorkflowDefinition, WorkflowEngine
from forge_harness.workflow.recovery import RecoveryConfig, RecoveryManager
from forge_harness.workflow.state import FleetStateManager

logger = get_logger(__name__)

router = APIRouter()

# Global instances (initialized on first request)
_state_manager: FleetStateManager | None = None
_engine: WorkflowEngine | None = None
_recovery_manager: RecoveryManager | None = None


def _get_state_manager() -> FleetStateManager:
    """Get or create the fleet state manager."""
    global _state_manager
    if _state_manager is None:
        _state_manager = FleetStateManager()
    return _state_manager


def _get_engine() -> WorkflowEngine:
    """Get or create the workflow engine."""
    global _engine
    if _engine is None:
        _engine = WorkflowEngine(state_manager=_get_state_manager())
    return _engine


def _get_recovery_manager() -> RecoveryManager:
    """Get or create the recovery manager."""
    global _recovery_manager
    if _recovery_manager is None:
        _recovery_manager = RecoveryManager(
            state_manager=_get_state_manager(),
            config=RecoveryConfig(),
        )
    return _recovery_manager


# -----------------------------------------------------------------------------
# Fleet State Endpoints
# -----------------------------------------------------------------------------


@router.get("/api/fleet/state")
async def get_fleet_state() -> JSONResponse:
    """Get current state of all agents in the fleet."""
    state_manager = _get_state_manager()
    agents = state_manager.get_all_agents()

    return JSONResponse({
        "agents": [agent.to_dict() for agent in agents],
        "summary": state_manager.get_fleet_summary(),
        "generated_at": datetime.now(UTC).isoformat(),
    })


@router.get("/api/fleet/agents/{agent_id}")
async def get_agent_state(agent_id: str) -> JSONResponse:
    """Get state for a specific agent."""
    state_manager = _get_state_manager()
    agent = state_manager.get_agent(agent_id)

    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    return JSONResponse(agent.to_dict())


@router.post("/api/fleet/agents/{agent_id}/transition")
async def transition_agent(agent_id: str, new_state: str, reason: str | None = None) -> JSONResponse:
    """Manually transition an agent to a new state."""
    from forge_harness.workflow.state import AgentState

    state_manager = _get_state_manager()

    try:
        state = AgentState(new_state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid state: {new_state}")

    success = state_manager.transition_agent(agent_id, state, reason)
    if not success:
        raise HTTPException(status_code=400, detail=f"Invalid state transition for agent {agent_id}")

    return JSONResponse({"success": True, "agent_id": agent_id, "new_state": new_state})


@router.post("/api/fleet/agents/register")
async def register_agent(
    agent_id: str,
    capabilities: list[str] | None = None,
) -> JSONResponse:
    """Register a new agent in the fleet."""
    state_manager = _get_state_manager()
    agent = state_manager.register_agent(agent_id, capabilities or [])

    return JSONResponse({"success": True, "agent": agent.to_dict()})


# -----------------------------------------------------------------------------
# Workflow Endpoints
# -----------------------------------------------------------------------------


@router.get("/api/workflows")
async def list_workflows() -> JSONResponse:
    """List all registered workflows."""
    engine = _get_engine()

    workflows = [
        {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "task_count": len(wf.dag._nodes),
        }
        for wf in engine._workflows.values()
    ]

    return JSONResponse({"workflows": workflows})


@router.post("/api/workflows/register")
async def register_workflow(
    workflow_path: str,
    format: str = "yaml",
) -> JSONResponse:
    """Register a workflow from a file."""
    engine = _get_engine()
    path = Path(workflow_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow file not found: {workflow_path}")

    try:
        if format.lower() == "yaml":
            workflow = WorkflowDefinition.from_yaml(path)
        else:
            workflow = WorkflowDefinition.from_json(path)

        engine.register_workflow(workflow)

        return JSONResponse({
            "success": True,
            "workflow_id": workflow.id,
            "name": workflow.name,
            "tasks": len(workflow.dag._nodes),
        })
    except Exception as exc:
        logger.error("Failed to register workflow: %s", exc)
        raise HTTPException(status_code=400, detail=f"Failed to parse workflow: {exc}")


@router.post("/api/workflows/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    execution_id: str | None = None,
) -> JSONResponse:
    """Start a workflow execution."""
    engine = _get_engine()

    try:
        execution = await engine.start_execution(workflow_id, execution_id)
        return JSONResponse({
            "success": True,
            "execution_id": execution.execution_id,
            "status": execution.status,
            "started_at": execution.started_at.isoformat(),
        })
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/workflows/executions/{execution_id}")
async def get_execution(execution_id: str) -> JSONResponse:
    """Get workflow execution status."""
    engine = _get_engine()
    status = engine.get_execution_status(execution_id)

    if not status:
        raise HTTPException(status_code=404, detail=f"Execution {execution_id} not found")

    return JSONResponse(status)


@router.post("/api/workflows/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str) -> JSONResponse:
    """Cancel a running workflow execution."""
    engine = _get_engine()
    success = engine.cancel_execution(execution_id)

    if not success:
        raise HTTPException(status_code=400, detail=f"Could not cancel execution {execution_id}")

    return JSONResponse({"success": True, "execution_id": execution_id})


# -----------------------------------------------------------------------------
# Recovery Endpoints
# -----------------------------------------------------------------------------


@router.get("/api/fleet/recovery/status")
async def get_recovery_status() -> JSONResponse:
    """Get recovery manager status and summary."""
    recovery = _get_recovery_manager()
    return JSONResponse(recovery.get_recovery_summary())


@router.get("/api/fleet/recovery/incidents")
async def get_recovery_incidents(
    agent_id: str | None = None,
    incident_type: str | None = None,
    limit: int = 100,
) -> JSONResponse:
    """Get health incidents."""
    recovery = _get_recovery_manager()
    incidents = recovery.get_incidents(agent_id, incident_type, limit)

    return JSONResponse({
        "incidents": [
            {
                "agent_id": i.agent_id,
                "incident_type": i.incident_type,
                "detected_at": i.detected_at.isoformat(),
                "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                "resolution": i.resolution,
                "auto_resolved": i.auto_resolved,
            }
            for i in incidents
        ],
        "count": len(incidents),
    })


@router.post("/api/fleet/recovery/start")
async def start_recovery_monitoring() -> JSONResponse:
    """Start the recovery monitoring loop."""
    import asyncio

    recovery = _get_recovery_manager()
    asyncio.create_task(recovery.start_monitoring())

    return JSONResponse({"success": True, "message": "Recovery monitoring started"})


@router.post("/api/fleet/recovery/stop")
async def stop_recovery_monitoring() -> JSONResponse:
    """Stop the recovery monitoring loop."""
    recovery = _get_recovery_manager()
    await recovery.stop_monitoring()

    return JSONResponse({"success": True, "message": "Recovery monitoring stopped"})


@router.post("/api/fleet/agents/{agent_id}/recover")
async def manual_recover(agent_id: str) -> JSONResponse:
    """Manually trigger recovery for an agent."""

    recovery = _get_recovery_manager()
    success = await recovery.manual_recover(agent_id)

    if not success:
        raise HTTPException(status_code=400, detail=f"Recovery failed for agent {agent_id}")

    return JSONResponse({"success": True, "agent_id": agent_id})


# -----------------------------------------------------------------------------
# DAG Utility Endpoints
# -----------------------------------------------------------------------------


@router.post("/api/workflows/dag/validate")
async def validate_dag(tasks: list[dict[str, Any]]) -> JSONResponse:
    """Validate a DAG definition without executing it."""
    try:
        dag = DAG()
        for task_data in tasks:
            node = TaskNode(
                id=task_data["id"],
                name=task_data["name"],
                dependencies=task_data.get("depends_on", []),
            )
            dag.add_node(node)

        # Try topological sort
        topo_order = dag.topological_sort()

        return JSONResponse({
            "valid": True,
            "task_count": len(dag._nodes),
            "topological_order": [n.id for n in topo_order],
        })
    except ValueError as exc:
        return JSONResponse({"valid": False, "error": str(exc)})


@router.post("/api/workflows/dag/parallel-groups")
async def get_parallel_groups(tasks: list[dict[str, Any]]) -> JSONResponse:
    """Get parallel execution groups for a DAG."""
    try:
        dag = DAG()
        for task_data in tasks:
            node = TaskNode(
                id=task_data["id"],
                name=task_data["name"],
                dependencies=task_data.get("depends_on", []),
            )
            dag.add_node(node)

        groups = dag.get_parallel_groups()

        return JSONResponse({
            "groups": [[n.id for n in group] for group in groups],
            "total_levels": len(groups),
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

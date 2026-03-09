"""Task Decomposition API Endpoints — DF-2002

REST endpoints for creating and managing task decomposition DAGs.
Exposes the TaskDecomposer service to the Command Center frontend and CLI.

Endpoints:
    POST /api/decomposition/graphs                                    — Create a new task graph
    GET  /api/decomposition/graphs                                    — List all graphs
    GET  /api/decomposition/graphs/{graph_id}                         — Get graph with nodes
    POST /api/decomposition/graphs/{graph_id}/subtasks                — Add a subtask
    PUT  /api/decomposition/graphs/{graph_id}/subtasks/{subtask_id}/complete — Mark subtask complete
    GET  /api/decomposition/graphs/{graph_id}/ready                   — Get ready-to-execute nodes
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.error_schema import (
    NOT_FOUND,
    VALIDATION_ERROR,
    ForgeError,
    api_success_body,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateGraphRequest(BaseModel):
    """Request body for creating a new task decomposition graph."""

    parent_task_id: str = Field(..., min_length=1, description="Unique parent task identifier.")
    title: str = Field(..., min_length=1, description="Human-readable parent task title.")


class AddSubtaskRequest(BaseModel):
    """Request body for adding a subtask to an existing graph."""

    title: str = Field(..., min_length=1, description="Human-readable subtask description.")
    task_type: str = Field(..., description="Canonical work category (e.g. 'bug-fix').")
    risk_tier: str = Field(..., description="Risk classification: low, medium, high, critical.")
    depends_on: list[str] = Field(
        default_factory=list,
        description="List of subtask_ids that must complete before this subtask.",
    )


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_decomposer():
    """Lazy import of TaskDecomposer singleton to avoid circular imports."""
    from forge_harness.webhook_server.services.task_decomposer import get_task_decomposer

    return get_task_decomposer()


def _api_response(data: Any) -> dict[str, Any]:
    """Build a canonical success response body."""
    return api_success_body(data)


def _graph_to_dict(graph: Any) -> dict[str, Any]:
    """Serialise a TaskGraph to a plain dict for API responses."""
    return graph.model_dump(mode="json")


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Serialise a SubtaskNode to a plain dict for API responses."""
    return node.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Graph collection routes
# ---------------------------------------------------------------------------


@router.post("/api/decomposition/graphs")
async def create_graph(body: CreateGraphRequest):
    """Create an empty task decomposition graph for a parent task.

    Args:
        body: parent_task_id and title for the new graph.

    Returns:
        The newly created TaskGraph dict, or 409 if the graph already exists.
    """
    decomposer = _get_decomposer()
    try:
        graph = decomposer.create_graph(body.parent_task_id, body.title)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    return JSONResponse(
        status_code=201,
        content=_api_response(_graph_to_dict(graph)),
    )


@router.get("/api/decomposition/graphs")
async def list_graphs():
    """List all task decomposition graphs ordered by creation time.

    Returns:
        List of TaskGraph dicts with node counts.
    """
    decomposer = _get_decomposer()
    graphs = decomposer.list_graphs()
    return JSONResponse(
        content=_api_response(
            {
                "graphs": [_graph_to_dict(g) for g in graphs],
                "count": len(graphs),
            }
        )
    )


# ---------------------------------------------------------------------------
# Graph-ID routes
# ---------------------------------------------------------------------------


@router.get("/api/decomposition/graphs/{graph_id}")
async def get_graph(graph_id: str):
    """Get a task graph with all its subtask nodes.

    Args:
        graph_id: The parent task identifier used as the graph key.

    Returns:
        Full TaskGraph dict including all SubtaskNode entries, or 404.
    """
    decomposer = _get_decomposer()
    graph = decomposer.get_graph(graph_id)
    if graph is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Task graph '{graph_id}' not found",
            ).to_dict(),
        )
    return JSONResponse(content=_api_response(_graph_to_dict(graph)))


@router.get("/api/decomposition/graphs/{graph_id}/ready")
async def get_ready_nodes(graph_id: str):
    """Get subtask nodes that are ready to execute in a graph.

    A node is ready when its status is 'pending' and all its dependencies
    have status 'completed'.

    Args:
        graph_id: The parent task identifier.

    Returns:
        List of ready SubtaskNode dicts, or 404 if graph not found.
    """
    decomposer = _get_decomposer()
    graph = decomposer.get_graph(graph_id)
    if graph is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Task graph '{graph_id}' not found",
            ).to_dict(),
        )
    ready_nodes = graph.get_ready_nodes()
    return JSONResponse(
        content=_api_response(
            {
                "nodes": [_node_to_dict(n) for n in ready_nodes],
                "count": len(ready_nodes),
            }
        )
    )


@router.post("/api/decomposition/graphs/{graph_id}/subtasks")
async def add_subtask(graph_id: str, body: AddSubtaskRequest):
    """Add a new subtask node to an existing task graph.

    Args:
        graph_id: The parent task identifier.
        body:     Subtask payload: title, task_type, risk_tier, depends_on.

    Returns:
        The newly created SubtaskNode dict, or 404/422 on error.
    """
    from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType

    try:
        task_type = TaskType(body.task_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid task_type {body.task_type!r}. Valid values: {[t.value for t in TaskType]}",
            ).to_dict(),
        )

    try:
        risk_tier = RiskTier(body.risk_tier)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid risk_tier {body.risk_tier!r}. Valid values: {[r.value for r in RiskTier]}",
            ).to_dict(),
        )

    decomposer = _get_decomposer()
    try:
        node = decomposer.add_subtask(
            parent_id=graph_id,
            title=body.title,
            task_type=task_type,
            risk_tier=risk_tier,
            depends_on=body.depends_on,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=str(exc),
            ).to_dict(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    return JSONResponse(
        status_code=201,
        content=_api_response(_node_to_dict(node)),
    )


@router.put("/api/decomposition/graphs/{graph_id}/subtasks/{subtask_id}/complete")
async def complete_subtask(graph_id: str, subtask_id: str):
    """Mark a subtask as completed and return the updated graph.

    Args:
        graph_id:   The parent task identifier.
        subtask_id: The subtask to mark as completed.

    Returns:
        Updated TaskGraph dict, or 404/422 on error.
    """
    decomposer = _get_decomposer()
    try:
        graph = decomposer.complete_subtask(graph_id, subtask_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=str(exc),
            ).to_dict(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    return JSONResponse(content=_api_response(_graph_to_dict(graph)))

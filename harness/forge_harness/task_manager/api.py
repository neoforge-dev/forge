"""V3 Task API - FastAPI endpoints for task management.

Replaces file-based dispatch with REST API.
"""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .database import get_task_db

app = FastAPI(title="FORGE V3 Task API", version="3.0.0")


# Pydantic models
class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    source_file: str | None = None
    result_path: str | None = None
    metadata: dict[str, Any] | None = None


class TaskResponse(BaseModel):
    id: str
    title: str
    status: str
    assigned_agent: str | None
    created_at: str
    priority: str


class TaskClaim(BaseModel):
    agent: str


class TaskComplete(BaseModel):
    agent: str
    result_summary: str = ""


class DispatchRequest(BaseModel):
    task_id: str
    agent: str


class DispatchResponse(BaseModel):
    success: bool
    dispatch_id: str | None
    message: str


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    db = get_task_db()
    # Verify DB is accessible
    pending = len(db.get_pending_tasks(limit=1))
    return {
        "status": "ok",
        "version": "3.0.0",
        "pending_tasks": pending
    }


@app.post("/tasks", response_model=TaskResponse)
async def create_task(task: TaskCreate):
    """Create a new task."""
    db = get_task_db()
    task_id = db.create_task(
        title=task.title,
        description=task.description,
        priority=task.priority,
        source_file=task.source_file,
        result_path=task.result_path,
        metadata=task.metadata
    )

    status = db.get_task_status(task_id)
    return TaskResponse(
        id=status['id'],
        title=status['title'],
        status=status['status'],
        assigned_agent=status.get('assigned_agent'),
        created_at=status['created_at'],
        priority=status['priority']
    )


@app.get("/tasks/pending", response_model=list[TaskResponse])
async def get_pending_tasks(limit: int = 10):
    """Get pending tasks."""
    db = get_task_db()
    tasks = db.get_pending_tasks(limit=limit)
    return [
        TaskResponse(
            id=t['id'],
            title=t['title'],
            status=t['status'],
            assigned_agent=t.get('assigned_agent'),
            created_at=t['created_at'],
            priority=t['priority']
        ) for t in tasks
    ]


@app.get("/tasks/{task_id}", response_model=dict[str, Any])
async def get_task(task_id: str):
    """Get task details."""
    db = get_task_db()
    status = db.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@app.post("/tasks/{task_id}/claim", response_model=dict[str, Any])
async def claim_task(task_id: str, claim: TaskClaim):
    """Claim a task for an agent."""
    db = get_task_db()

    # Check current status
    current = db.get_task_status(task_id)
    if not current:
        raise HTTPException(status_code=404, detail="Task not found")

    if current['status'] != 'pending':
        return {
            "success": False,
            "message": f"Task already {current['status']}",
            "assigned_to": current.get('assigned_agent')
        }

    success = db.claim_task(task_id, claim.agent)
    if success:
        return {
            "success": True,
            "message": f"Task claimed by {claim.agent}",
            "task_id": task_id
        }
    else:
        return {
            "success": False,
            "message": "Task claim failed - possibly claimed by another agent"
        }


@app.post("/tasks/{task_id}/complete", response_model=dict[str, Any])
async def complete_task(task_id: str, complete: TaskComplete):
    """Mark a task as completed."""
    db = get_task_db()

    current = db.get_task_status(task_id)
    if not current:
        raise HTTPException(status_code=404, detail="Task not found")

    if current.get('assigned_agent') != complete.agent:
        raise HTTPException(
            status_code=403,
            detail=f"Task not assigned to {complete.agent}"
        )

    success = db.complete_task(task_id, complete.agent, complete.result_summary)
    if success:
        return {
            "success": True,
            "message": "Task completed",
            "task_id": task_id
        }
    else:
        raise HTTPException(status_code=400, detail="Failed to complete task")


@app.post("/dispatch", response_model=DispatchResponse)
async def dispatch_task(dispatch: DispatchRequest):
    """Idempotent task dispatch.
    
    Same task+agent combination returns same dispatch_id (idempotent).
    """
    db = get_task_db()

    task = db.get_task_status(dispatch.task_id)
    if not task:
        return DispatchResponse(
            success=False,
            message="Task not found",
            dispatch_id=None
        )

    if task['status'] != 'pending':
        return DispatchResponse(
            success=False,
            message=f"Task already {task['status']}",
            dispatch_id=task.get('dispatch_id')
        )

    # Try to claim
    success = db.claim_task(dispatch.task_id, dispatch.agent)
    if success:
        updated = db.get_task_status(dispatch.task_id)
        return DispatchResponse(
            success=True,
            message=f"Task dispatched to {dispatch.agent}",
            dispatch_id=updated.get('dispatch_id')
        )
    else:
        return DispatchResponse(
            success=False,
            message="Task dispatch failed - possibly already claimed",
            dispatch_id=None
        )


@app.get("/agents/{agent}/tasks", response_model=list[dict[str, Any]])
async def get_agent_tasks(agent: str):
    """Get tasks assigned to an agent."""
    db = get_task_db()
    return db.get_agent_tasks(agent)


def start_api(port: int = 8083):
    """Start the V3 Task API server."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    start_api()

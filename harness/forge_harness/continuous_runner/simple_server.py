"""
Simple standalone webhook server for Continuous Runner.
Does not depend on full harness stack.
"""

import json
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, Request
from pydantic import BaseModel

app = FastAPI(title="Forge Continuous Runner Webhook Server")


class HealthStatus:
    """Simple health monitor."""
    def __init__(self):
        self.status = "healthy"
        self.components = {
            "webhook_server": "healthy",
            "deployer": "ready",
            "runner": "idle"
        }

    def get_status(self):
        return {
            "status": self.status,
            "components": self.components
        }

    def update_component(self, name, status):
        self.components[name] = status


monitor = HealthStatus()


class TriggerRequest(BaseModel):
    """Request to trigger a runner action."""
    action: str
    params: dict[str, Any] | None = None


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return monitor.get_status()


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Forge Continuous Runner",
        "version": "3.0.0",
        "status": "operational",
        "endpoints": [
            "/health",
            "/webhook/trigger",
            "/webhook/github"
        ]
    }


@app.post("/webhook/trigger")
async def trigger_action(request: TriggerRequest, background_tasks: BackgroundTasks):
    """Manually trigger a runner action."""
    print(f"Received trigger action: {request.action}")

    if request.action == "deploy":
        background_tasks.add_task(lambda: print("Deployment triggered"))
        return {"status": "accepted", "message": "Deployment triggered"}

    elif request.action == "restart":
        print("Restart requested via webhook")
        return {"status": "accepted", "message": "Restart requested"}

    else:
        return {"status": "error", "message": f"Unknown action: {request.action}"}


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None)
):
    """Handle GitHub webhooks."""
    payload = await request.body()
    data = json.loads(payload)

    if x_github_event == "push":
        ref = data.get("ref", "")
        if ref == "refs/heads/main":
            print("Push to main detected, would trigger deployment")
            return {"status": "accepted", "message": "Deployment triggered by push to main"}

    return {"status": "ignored", "message": f"Event {x_github_event} ignored"}


def start_server(host="0.0.0.0", port=8082):
    """Start the webhook server."""
    print(f"Starting webhook server on {host}:{port}")
    monitor.update_component("webhook_server", "healthy")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start_server()

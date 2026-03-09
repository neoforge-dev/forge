"""
Webhook server for Continuous Runner.
"""

import hashlib
import hmac
import json
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from forge_harness.continuous_runner.config import settings
from forge_harness.continuous_runner.deployer import deployer
from forge_harness.continuous_runner.health import monitor
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(title="Forge Continuous Runner Webhook Server")


class TriggerRequest(BaseModel):
    """Request to trigger a runner action."""
    action: str
    params: dict[str, Any] | None = None


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return monitor.get_status()


@app.post("/webhook/trigger")
async def trigger_action(request: TriggerRequest, background_tasks: BackgroundTasks):
    """
    Manually trigger a runner action.
    
    Actions:
    - deploy: Trigger a Railway deployment
    - restart: Restart the Ralph Loop
    """
    logger.info("Received trigger action: %s", request.action)

    if request.action == "deploy":
        background_tasks.add_task(deployer.deploy_with_check)
        return {"status": "accepted", "message": "Deployment triggered"}

    elif request.action == "restart":
        # Implementation depends on how the runner is managed
        # For now, just log and return
        logger.info("Restart requested via webhook")
        return {"status": "accepted", "message": "Restart requested"}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None),
    x_hub_signature_256: str = Header(None)
):
    """
    Handle GitHub webhooks.
    Automatically triggers deployment on push to main.
    """
    payload = await request.body()

    # Verify signature if secret is configured
    if settings.github_webhook_secret:
        if not x_hub_signature_256:
            raise HTTPException(status_code=401, detail="X-Hub-Signature-256 header missing")

        hash_object = hmac.new(
            settings.github_webhook_secret.encode(),
            msg=payload,
            digestmod=hashlib.sha256
        )
        expected_signature = "sha256=" + hash_object.hexdigest()

        if not hmac.compare_digest(expected_signature, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(payload)

    if x_github_event == "push":
        ref = data.get("ref", "")
        if ref == "refs/heads/main":
            logger.info("Push to main detected, triggering deployment")
            background_tasks.add_task(deployer.deploy_with_check)
            return {"status": "accepted", "message": "Deployment triggered by push to main"}

    return {"status": "ignored", "message": f"Event {x_github_event} ignored"}


def start_server():
    """Start the webhook server."""
    import uvicorn
    logger.info("Starting webhook server on %s:%d", settings.webhook_host, settings.webhook_port)
    monitor.update_component("webhook_server", "healthy")
    uvicorn.run(app, host=settings.webhook_host, port=settings.webhook_port)


if __name__ == "__main__":
    start_server()

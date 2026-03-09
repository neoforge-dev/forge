"""Legacy Configuration endpoints — extracted from webhook_server_main.py.

Handles /api/legacy/config/llm GET and POST routes.
"""

import json
import os
import pathlib
from typing import Any

import aiofiles
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.legacy_models import (
    ConfigUpdateRequest,
    api_response,
)
from forge_harness.webhook_server.services.event_bus import get_event_bus

logger = get_logger(__name__)

router = APIRouter(tags=["legacy-config"])

# Configuration file path (supports override via env var for testing)
config_dir_path = os.getenv("FORGE_CONFIG_DIR", ".forge/config")
FORGE_CONFIG_DIR = pathlib.Path(config_dir_path)
LLM_CONFIG_FILE = FORGE_CONFIG_DIR / "llm.json"

# Default LLM configuration
DEFAULT_LLM_CONFIG = {
    "provider": "claude",
    "model": "claude-sonnet-4-5",
    "temperature": 0.7,
    "max_tokens": 4096,
}


async def load_llm_config() -> dict[str, Any]:
    """Load LLM configuration from disk or return defaults."""
    try:
        if LLM_CONFIG_FILE.exists():
            async with aiofiles.open(LLM_CONFIG_FILE) as f:
                content = await f.read()
                config = json.loads(content)
                return {**DEFAULT_LLM_CONFIG, **config}
        return DEFAULT_LLM_CONFIG.copy()
    except Exception as e:
        logger.warning(f"Failed to load LLM config, using defaults: {e}")
        return DEFAULT_LLM_CONFIG.copy()


async def save_llm_config(config: dict[str, Any]) -> None:
    """Save LLM configuration to disk."""
    try:
        FORGE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(LLM_CONFIG_FILE, "w") as f:
            await f.write(json.dumps(config, indent=2))
    except Exception as e:
        logger.error(f"Failed to save LLM config: {e}")
        raise


@router.get("/api/legacy/config/llm")
async def get_llm_config(request: Request):
    """Get current LLM configuration."""
    config = await load_llm_config()
    return JSONResponse(content=api_response(config))


@router.post("/api/legacy/config/llm")
async def update_llm_config(body: ConfigUpdateRequest, request: Request):
    """Update LLM configuration."""
    config = await load_llm_config()

    if body.provider is not None:
        config["provider"] = body.provider
    if body.model is not None:
        config["model"] = body.model
    if body.temperature is not None:
        temp = body.temperature
        if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
            return JSONResponse(
                status_code=400,
                content=api_response(
                    error_code="INVALID_TEMPERATURE",
                    error_message="Temperature must be between 0 and 2",
                ),
            )
        config["temperature"] = temp
    if body.max_tokens is not None:
        tokens = body.max_tokens
        if not isinstance(tokens, int) or tokens < 1:
            return JSONResponse(
                status_code=400,
                content=api_response(
                    error_code="INVALID_MAX_TOKENS",
                    error_message="max_tokens must be a positive integer",
                ),
            )
        config["max_tokens"] = tokens

    try:
        await save_llm_config(config)
    except Exception as e:
        logger.error(f"Failed to save LLM config: {e}")
        return JSONResponse(
            status_code=500,
            content=api_response(
                error_code="CONFIG_WRITE_ERROR",
                error_message=f"Failed to write LLM configuration: {str(e)}",
            ),
        )

    update_fields = body.model_dump(exclude_unset=True)
    logger.info(f"LLM configuration updated: {update_fields}")

    event_bus = get_event_bus()
    await event_bus.publish("config.llm.updated", config)

    return JSONResponse(content=api_response(config))

"""Configuration API Endpoints

LLM and system configuration management.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.services.audit import get_audit_logger
from forge_harness.webhook_server.services.event_bus import get_event_bus

router = APIRouter()
logger = get_logger(__name__)


# Configuration file path (supports override via env var for testing)
# NOTE: Use pathlib.Path explicitly to avoid FastAPI.Path shadowing issue
_CONFIG_DIR_PATH = os.getenv("FORGE_CONFIG_DIR", ".forge/config")
FORGE_CONFIG_DIR = Path(_CONFIG_DIR_PATH)
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
                # Ensure all required fields exist
                return {**DEFAULT_LLM_CONFIG, **config}
        return DEFAULT_LLM_CONFIG.copy()
    except Exception as e:
        logger.warning(f"Failed to load LLM config, using defaults: {e}")
        return DEFAULT_LLM_CONFIG.copy()


async def save_llm_config(config: dict[str, Any]) -> None:
    """Save LLM configuration to disk."""
    try:
        # Ensure config directory exists
        FORGE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(LLM_CONFIG_FILE, "w") as f:
            await f.write(json.dumps(config, indent=2))
    except Exception as e:
        logger.error(f"Failed to save LLM config: {e}")
        raise


def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
    """Create a standardized API response."""
    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _extract_request_context(request: Request | None = None) -> tuple[str | None, str | None]:
    """Extract IP address and user agent from FastAPI Request."""
    if request is None:
        return None, None
    # Get client IP (check common headers first)
    ip_address = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "").strip()
        or request.client.host if request.client else None
    )
    user_agent = request.headers.get("User-Agent")
    return ip_address, user_agent


# Configuration storage (in production, use StateStore)
_config: dict[str, Any] = {
    "llm": {
        "provider": "claude",
        "model": "claude-sonnet-4-5",
        "temperature": 0.7,
        "max_tokens": 4096
    }
}


class LLMConfigUpdate(BaseModel):
    """LLM configuration update request."""
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@router.get("/api/config/llm")
async def get_llm_config(
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    """Get current LLM configuration.

    Returns:
        dict: Current LLM configuration with provider, model, temperature, max_tokens
    """
    config = await load_llm_config()
    return config


@router.post("/api/config/llm")
async def update_llm_config(
    update: LLMConfigUpdate,
    request: Request,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Update LLM configuration.

    Args:
        update: Configuration updates
        request: FastAPI Request object for audit logging

    Returns:
        dict: Updated configuration with provider, model, temperature, max_tokens
    """
    # Load current config
    config = await load_llm_config()

    # Track what changed
    changes = {}
    if update.provider is not None:
        changes["provider"] = {"old": config.get("provider"), "new": update.provider}
        config["provider"] = update.provider
    if update.model is not None:
        changes["model"] = {"old": config.get("model"), "new": update.model}
        config["model"] = update.model
    if update.temperature is not None:
        changes["temperature"] = {"old": config.get("temperature"), "new": update.temperature}
        config["temperature"] = update.temperature
    if update.max_tokens is not None:
        changes["max_tokens"] = {"old": config.get("max_tokens"), "new": update.max_tokens}
        config["max_tokens"] = update.max_tokens

    # Save to disk
    await save_llm_config(config)

    # Audit log configuration change
    if changes:
        ip_address, user_agent = _extract_request_context(request)
        audit_logger = get_audit_logger()
        await audit_logger.log_configuration_change(
            config_key="llm",
            actor={"id": "api", "type": "system"},
            context={"changes": changes},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    return config


# =============================================================================
# Legacy Config Endpoints (file-based persistence)
# =============================================================================

class LegacyConfigUpdateRequest(BaseModel):
    """Request body for updating configuration (legacy format)."""

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@router.get("/api/legacy/config/llm")
async def get_legacy_llm_config(
    _: None = Depends(verify_auth),
):
    """Get current LLM configuration (legacy endpoint).

    Returns configuration with all fields: provider, model, temperature, max_tokens.
    Loads from .forge/config/llm.json if it exists, otherwise returns defaults.
    """
    config = await load_llm_config()
    return JSONResponse(content=api_response(config))


@router.post("/api/legacy/config/llm")
async def update_legacy_llm_config(
    body: LegacyConfigUpdateRequest,
    _: None = Depends(verify_auth),
):
    """Update LLM configuration (legacy endpoint).

    Updates only the fields provided in the request body.
    Persists configuration to .forge/config/llm.json.
    Returns the full updated configuration.
    """
    # Load current config
    config = await load_llm_config()

    # Update only provided fields
    if body.provider is not None:
        config["provider"] = body.provider
    if body.model is not None:
        config["model"] = body.model
    if body.temperature is not None:
        # Validate temperature range (0-2)
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
        # Validate max_tokens (positive integer)
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

    # Save to disk
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

    # Log successful update
    update_fields = body.model_dump(exclude_unset=True)
    logger.info(f"LLM configuration updated: {update_fields}")

    # Emit SSE event for config update
    event_bus = get_event_bus()
    await event_bus.publish("config.llm.updated", config)

    return JSONResponse(content=api_response(config))

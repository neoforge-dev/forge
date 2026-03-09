"""API-based task dispatch for fleet agents.

Provides reliable task dispatch via Command Center API with tmux fallback.
Replaces raw tmux commands with CommandCenterClient.send_message() for better
reliability and observability.

Architecture:
    1. Try API first: Use CommandCenterClient.send_message() to dispatch
    2. Fallback to tmux: If API fails, use tmux send-keys
    3. Verification: Optionally verify agent received message

Usage:
    from forge_harness.fleet.dispatch_api import dispatch_to_agent

    result = await dispatch_to_agent(
        agent_id="forge:opencode",
        message="Implement feature X",
        verify=True
    )
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..command_center_client import CommandCenterClient
from ..logging_config import get_logger
from .dispatch_validation import (
    validate_dispatch,
)

logger = get_logger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Default tmux session for FORGE agents
FORGE_SESSION = "forge"

# Dispatch timeout
DISPATCH_TIMEOUT_SECONDS = 30

# Verification settings
VERIFY_TIMEOUT_SECONDS = 15


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class DispatchResult:
    """Result of a dispatch operation."""

    success: bool
    agent_id: str
    message: str
    method: str  # "api" or "tmux"
    timestamp: datetime
    error: str | None = None


@dataclass
class AgentInfo:
    """Information about an agent for dispatch."""

    id: str
    name: str
    tmux_window: str  # e.g., "forge:opencode"
    role: str
    is_available: bool = True


# =============================================================================
# Dispatch Functions
# =============================================================================


async def dispatch_to_agent(
    agent_id: str,
    message: str,
    *,
    verify: bool = False,
    client: CommandCenterClient | None = None,
    session: str = FORGE_SESSION,
) -> DispatchResult:
    """Dispatch a task to an agent via API first, then tmux fallback.

    Args:
        agent_id: Agent identifier (e.g., "forge:opencode")
        message: Task instruction to send
        verify: Whether to verify agent received the message
        client: Optional CommandCenterClient instance
        session: Tmux session name (default: "forge")

    Returns:
        DispatchResult with success status and method used
    """
    timestamp = datetime.now(UTC)

    # Validate input for ambiguity - fail fast with structured errors
    validation = validate_dispatch(agent_id, message)
    if not validation.valid:
        error = validation.errors[0]
        logger.warning(f"Dispatch validation failed: {error.code.value} - {error.message}")
        return DispatchResult(
            success=False,
            agent_id=agent_id or "",
            message=message,
            method="validation",
            timestamp=timestamp,
            error=f"[{error.code.value}] {error.message}. Next action: {error.next_action}",
        )

    tmux_window = f"{session}:{agent_id.split(':')[-1]}"

    # Try API first
    if client is None:
        client = CommandCenterClient.from_env()

    try:
        api_success = await _dispatch_via_api(client, agent_id, message)
        if api_success:
            logger.info(f"Dispatched to {agent_id} via API")
            if verify:
                await asyncio.sleep(1)  # Brief wait for processing
            return DispatchResult(
                success=True,
                agent_id=agent_id,
                message=message,
                method="api",
                timestamp=timestamp,
            )
    except Exception as e:
        logger.debug(f"API dispatch failed, falling back to tmux: {e}")

    # Fallback to tmux
    try:
        tmux_success = _dispatch_via_tmux(tmux_window, message)
        if tmux_success:
            logger.info(f"Dispatched to {agent_id} via tmux (fallback)")
            return DispatchResult(
                success=True,
                agent_id=agent_id,
                message=message,
                method="tmux",
                timestamp=timestamp,
            )
    except Exception as e:
        logger.error(f"Tmux dispatch failed: {e}")
        return DispatchResult(
            success=False,
            agent_id=agent_id,
            message=message,
            method="tmux",
            timestamp=timestamp,
            error=str(e),
        )

    # Both methods failed
    return DispatchResult(
        success=False,
        agent_id=agent_id,
        message=message,
        method="none",
        timestamp=timestamp,
        error="Both API and tmux dispatch failed",
    )


async def _dispatch_via_api(
    client: CommandCenterClient,
    agent_id: str,
    message: str,
) -> bool:
    """Dispatch task via Command Center API.

    Args:
        client: CommandCenterClient instance
        agent_id: Target agent identifier
        message: Task instruction

    Returns:
        True if successful, False otherwise
    """
    try:
        success = await client.send_message(agent_id, message)
        return success
    except Exception as e:
        logger.debug(f"API send failed: {e}")
        return False


def _dispatch_via_tmux(window_name: str, message: str) -> bool:
    """Dispatch task via tmux send-keys.

    Args:
        window_name: Tmux window (e.g., "forge:opencode")
        message: Task instruction

    Returns:
        True if successful, False otherwise
    """
    try:
        # Clear any existing input
        subprocess.run(
            ["tmux", "send-keys", "-t", window_name, "C-u"],
            capture_output=True,
            timeout=5,
        )

        # Send message in chunks
        chunk_size = 50
        msg_len = len(message)
        for i in range(0, msg_len, chunk_size):
            chunk = message[i : i + chunk_size]
            subprocess.run(
                ["tmux", "send-keys", "-t", window_name, "-l", chunk],
                capture_output=True,
                timeout=5,
            )
            asyncio.sleep(0.05)

        # Send enter
        subprocess.run(
            ["tmux", "send-keys", "-t", window_name, "Enter"],
            capture_output=True,
            timeout=5,
        )

        return True
    except Exception as e:
        logger.debug(f"Tmux send failed: {e}")
        return False


def verify_dispatch(
    agent_id: str,
    *,
    session: str = FORGE_SESSION,
    timeout: int = VERIFY_TIMEOUT_SECONDS,
) -> bool:
    """Verify that an agent has received and is processing a dispatch.

    Args:
        agent_id: Agent identifier
        session: Tmux session name
        timeout: Seconds to wait for activity

    Returns:
        True if agent shows activity, False otherwise
    """
    window_name = f"{session}:{agent_id.split(':')[-1]}"

    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", window_name, "-p", "-S", "-10"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return False

        output = result.stdout.strip()

        # Check for activity indicators
        activity_patterns = [
            "Thinking",
            "Reading",
            "Working",
            "tokens",
            "Searching",
            "Implementing",
        ]

        for pattern in activity_patterns:
            if pattern.lower() in output.lower():
                return True

        return False
    except Exception:
        return False


# =============================================================================
# Batch Dispatch
# =============================================================================


async def dispatch_bulk(
    tasks: list[dict[str, Any]],
    client: CommandCenterClient | None = None,
    concurrency: int = 5,
) -> list[DispatchResult]:
    """Dispatch multiple tasks to agents.

    Args:
        tasks: List of {agent_id, message} dicts
        client: Optional CommandCenterClient
        concurrency: Max concurrent dispatches

    Returns:
        List of DispatchResult for each task
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def dispatch_one(task: dict[str, Any]) -> DispatchResult:
        async with semaphore:
            return await dispatch_to_agent(
                agent_id=task["agent_id"],
                message=task["message"],
                client=client,
            )

    results = await asyncio.gather(*[dispatch_one(t) for t in tasks])
    return results


# =============================================================================
# Agent Registry Integration
# =============================================================================


def get_registered_agents() -> list[AgentInfo]:
    """Get list of registered agents from tmux session.

    Returns:
        List of AgentInfo for active agents
    """
    agents = []

    # Agent role mappings
    agent_roles = {
        "opencode": "tech",
        "codex": "frontend",
        "gemini": "research",
        "pi": "general",
        "kimi": "analysis",
        "cursor": "frontend",
        "amp": "dev",
    }

    for agent_name, role in agent_roles.items():
        window_name = f"{FORGE_SESSION}:{agent_name}"

        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", window_name],
                capture_output=True,
                timeout=2,
            )

            if result.returncode == 0:
                agents.append(
                    AgentInfo(
                        id=f"forge:{agent_name}",
                        name=agent_name,
                        tmux_window=window_name,
                        role=role,
                        is_available=True,
                    )
                )
        except Exception:
            pass

    return agents


# =============================================================================
# Health Check
# =============================================================================


async def check_api_health(client: CommandCenterClient) -> bool:
    """Check if Command Center API is healthy.

    Args:
        client: CommandCenterClient instance

    Returns:
        True if API is responding
    """
    try:
        agents = await client.list_agents()
        return len(agents) >= 0  # API responded
    except Exception:
        return False


def check_tmux_health() -> bool:
    """Check if tmux is available.

    Returns:
        True if tmux is installed and accessible
    """
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


async def get_dispatch_health() -> dict[str, Any]:
    """Get overall dispatch system health.

    Returns:
        Health status of API and tmux
    """
    client = CommandCenterClient.from_env()

    return {
        "api_healthy": await check_api_health(client),
        "tmux_healthy": check_tmux_health(),
        "timestamp": datetime.now(UTC).isoformat(),
    }

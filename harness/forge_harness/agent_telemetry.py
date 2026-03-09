"""
Agent Telemetry Module
======================

Provides observability and registration for Claude Code agents working
on FORGE projects. Enables automatic registration with the Command Center
dashboard and periodic heartbeat reporting.

Usage:
    from forge_harness.agent_telemetry import AgentTelemetry, create_agent_telemetry

    # Initialize telemetry
    telemetry = create_agent_telemetry(
        webhook_url="http://localhost:8000",
        token="dev-token",
        domain="codeswiftr-com",
        project="interview-simulator",
        role="feature-dev",
    )

    # Register agent
    await telemetry.register()

    # Send heartbeat (call periodically)
    await telemetry.heartbeat()

    # Report progress
    await telemetry.report_progress(
        progress=50,
        current_task="Implementing authentication",
        files_modified=["src/auth.py"],
    )

    # Report error
    await telemetry.report_error(
        error="Test failed",
        context={"test": "test_auth"},
    )

    # Complete agent session
    await telemetry.complete()
"""

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class TelemetryConfig:
    """Configuration for agent telemetry.

    Attributes:
        webhook_url: URL of the webhook server
        token: Authentication token
        domain: Domain name (e.g., "codeswiftr-com")
        project: Project name (e.g., "interview-simulator")
        role: Agent role (e.g., "feature-dev", "debug", "review")
        session_id: Unique session ID (auto-generated if None)
        name: Optional agent name
        parent_id: Parent agent ID for hierarchy tracking
        tmux_session: tmux session:window location
        skills: List of attached skills
        heartbeat_interval: Seconds between heartbeats (default 30s)
        enabled: Whether telemetry is enabled
    """

    webhook_url: str
    token: str
    domain: str
    project: str
    role: str
    session_id: str | None = None
    name: str | None = None
    parent_id: str | None = None
    tmux_session: str | None = None
    skills: list[str] = field(default_factory=list)
    heartbeat_interval: int = 30
    enabled: bool = True

    def __post_init__(self):
        """Generate session ID if not provided."""
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())[:8]

    @classmethod
    def from_env(
        cls,
        domain: str | None = None,
        project: str | None = None,
        role: str = "builder",
    ) -> "TelemetryConfig":
        """Create TelemetryConfig from environment variables.

        Args:
            domain: Override domain from environment
            project: Override project from environment
            role: Agent role (default: "builder")

        Returns:
            TelemetryConfig populated from environment
        """
        webhook_url = os.environ.get("FORGE_WEBHOOK_URL", "http://localhost:8000")
        token = os.environ.get("FORGE_WEBHOOK_TOKEN")
        if not token:
            raise ValueError(
                "FORGE_WEBHOOK_TOKEN environment variable is required for agent telemetry. "
                "Set this to the authentication token configured in your webhook server."
            )
        domain = domain or os.environ.get("FORGE_DOMAIN", "unknown")
        project = project or os.environ.get("FORGE_PROJECT", "unknown")

        # Detect tmux session if available
        tmux_session = os.environ.get("TMUX_SESSION")
        if not tmux_session:
            # Try to detect from TMUX environment variable
            tmux_var = os.environ.get("TMUX")
            if tmux_var:
                # TMUX variable format: /tmp/tmux-501/default,12345,0
                tmux_session = f"tmux:{tmux_var.split(',')[1]}"

        return cls(
            webhook_url=webhook_url,
            token=token,
            domain=domain,
            project=project,
            role=role,
            name=os.environ.get("FORGE_AGENT_NAME"),
            parent_id=os.environ.get("FORGE_PARENT_AGENT_ID"),
            tmux_session=tmux_session,
            skills=os.environ.get("FORGE_AGENT_SKILLS", "").split(",")
            if os.environ.get("FORGE_AGENT_SKILLS")
            else [],
            heartbeat_interval=int(os.environ.get("FORGE_HEARTBEAT_INTERVAL", "30")),
            enabled=os.environ.get("FORGE_TELEMETRY_ENABLED", "true").lower()
            in ("true", "1", "yes"),
        )


# =============================================================================
# Agent Telemetry
# =============================================================================


class AgentTelemetry:
    """Handles agent telemetry and registration with Command Center.

    Provides automatic registration, heartbeat, progress reporting,
    and error tracking for Claude Code agents.
    """

    def __init__(self, config: TelemetryConfig):
        """Initialize agent telemetry.

        Args:
            config: Telemetry configuration
        """
        self.config = config
        self._agent_id: str | None = None
        self._registered = False
        self._heartbeat_task: asyncio.Task | None = None
        self._stop_heartbeat = asyncio.Event()

    @property
    def agent_id(self) -> str | None:
        """Get the registered agent ID."""
        return self._agent_id

    @property
    def is_registered(self) -> bool:
        """Check if agent is registered."""
        return self._registered

    async def register(self) -> str | None:
        """Register agent with Command Center.

        Returns:
            Agent ID if successful, None otherwise
        """
        if not self.config.enabled:
            logger.debug("Telemetry disabled, skipping registration")
            return None

        if self._registered:
            logger.debug("Agent already registered")
            return self._agent_id

        try:
            import httpx

            # Build registration payload
            payload = {
                "role": self.config.role,
                "project": self.config.project,
                "task": f"Working on {self.config.domain}/{self.config.project}",
                "domain": self.config.domain,
            }

            # Add optional fields
            if self.config.name:
                payload["name"] = self.config.name
            if self.config.parent_id:
                payload["parent_id"] = self.config.parent_id
            if self.config.tmux_session:
                payload["tmux_session"] = self.config.tmux_session
            if self.config.skills:
                payload["skills"] = self.config.skills

            # Send registration request
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.config.webhook_url}/api/agents/register",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.token}"},
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success") and "data" in data:
                        self._agent_id = data["data"].get("id")
                        self._registered = True
                        logger.info(
                            f"Agent registered: {self._agent_id} ({self.config.role}) "
                            f"for {self.config.domain}/{self.config.project}"
                        )
                        return self._agent_id
                    else:
                        logger.warning(f"Unexpected registration response: {data}")
                        return None
                else:
                    logger.warning(
                        f"Agent registration failed: {response.status_code} - {response.text}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Failed to register agent: {e}", exc_info=True)
            return None

    async def heartbeat(self) -> bool:
        """Send heartbeat to Command Center.

        Returns:
            True if successful, False otherwise
        """
        if not self.config.enabled or not self._registered or not self._agent_id:
            return False

        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{self.config.webhook_url}/api/agents/{self._agent_id}/heartbeat",
                    headers={"Authorization": f"Bearer {self.config.token}"},
                )

                if response.status_code == 200:
                    logger.debug(f"Heartbeat sent for agent {self._agent_id}")
                    return True
                else:
                    logger.warning(f"Heartbeat failed: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.debug(f"Heartbeat error (non-critical): {e}")
            return False

    async def report_progress(
        self,
        progress: int,
        current_task: str | None = None,
        files_modified: list[str] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> bool:
        """Report agent progress to Command Center.

        Args:
            progress: Progress percentage (0-100)
            current_task: Current task description
            files_modified: List of files modified
            token_usage: Token usage statistics

        Returns:
            True if successful, False otherwise
        """
        if not self.config.enabled or not self._registered or not self._agent_id:
            return False

        try:
            import httpx

            payload = {"progress": progress}
            if current_task:
                payload["current_task"] = current_task
            if files_modified:
                payload["files_modified"] = files_modified
            if token_usage:
                payload["token_usage"] = token_usage

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.config.webhook_url}/api/agents/{self._agent_id}/progress",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.token}"},
                )

                if response.status_code == 200:
                    logger.debug(f"Progress reported for agent {self._agent_id}: {progress}%")
                    return True
                else:
                    logger.warning(
                        f"Progress report failed: {response.status_code} - {response.text}"
                    )
                    return False

        except Exception as e:
            logger.debug(f"Progress report error (non-critical): {e}")
            return False

    async def report_error(
        self,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Report error to Command Center.

        Args:
            error: Error message
            context: Additional error context

        Returns:
            True if successful, False otherwise
        """
        if not self.config.enabled or not self._registered or not self._agent_id:
            return False

        try:
            import httpx

            payload = {
                "error": error,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if context:
                payload["context"] = context

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.config.webhook_url}/api/agents/{self._agent_id}/error",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.token}"},
                )

                if response.status_code in (200, 201):
                    logger.debug(f"Error reported for agent {self._agent_id}")
                    return True
                else:
                    logger.warning(f"Error report failed: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.debug(f"Error report error (non-critical): {e}")
            return False

    async def complete(self, summary: dict[str, Any] | None = None) -> bool:
        """Mark agent session as complete.

        Args:
            summary: Optional summary of agent session

        Returns:
            True if successful, False otherwise
        """
        if not self.config.enabled or not self._registered or not self._agent_id:
            return False

        # Stop heartbeat task
        await self.stop_heartbeat_loop()

        try:
            import httpx

            payload = {"status": "completed"}
            if summary:
                payload["summary"] = summary

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.config.webhook_url}/api/agents/{self._agent_id}/complete",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.token}"},
                )

                if response.status_code == 200:
                    logger.info(f"Agent {self._agent_id} marked as complete")
                    return True
                else:
                    logger.warning(
                        f"Agent completion failed: {response.status_code} - {response.text}"
                    )
                    return False

        except Exception as e:
            logger.debug(f"Agent completion error (non-critical): {e}")
            return False

    async def start_heartbeat_loop(self):
        """Start periodic heartbeat loop in background."""
        if self._heartbeat_task is not None:
            logger.debug("Heartbeat loop already running")
            return

        if not self.config.enabled or not self._registered:
            logger.debug("Cannot start heartbeat loop: agent not registered")
            return

        self._stop_heartbeat.clear()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            f"Started heartbeat loop for agent {self._agent_id} "
            f"(interval: {self.config.heartbeat_interval}s)"
        )

    async def stop_heartbeat_loop(self):
        """Stop heartbeat loop."""
        if self._heartbeat_task is None:
            return

        self._stop_heartbeat.set()
        try:
            await asyncio.wait_for(self._heartbeat_task, timeout=5.0)
        except TimeoutError:
            logger.warning("Heartbeat loop did not stop gracefully")
            self._heartbeat_task.cancel()
        except Exception as e:
            logger.debug(f"Heartbeat loop stop error: {e}")

        self._heartbeat_task = None
        logger.debug("Stopped heartbeat loop")

    async def _heartbeat_loop(self):
        """Internal heartbeat loop."""
        try:
            while not self._stop_heartbeat.is_set():
                await self.heartbeat()
                await asyncio.sleep(self.config.heartbeat_interval)
        except asyncio.CancelledError:
            logger.debug("Heartbeat loop cancelled")
        except Exception as e:
            logger.error(f"Heartbeat loop error: {e}", exc_info=True)


# =============================================================================
# Factory Functions
# =============================================================================


def create_agent_telemetry(
    webhook_url: str | None = None,
    token: str | None = None,
    domain: str | None = None,
    project: str | None = None,
    role: str = "builder",
    session_id: str | None = None,
    name: str | None = None,
    parent_id: str | None = None,
    tmux_session: str | None = None,
    skills: list[str] | None = None,
    heartbeat_interval: int = 30,
    enabled: bool = True,
) -> AgentTelemetry:
    """Create agent telemetry instance.

    Args:
        webhook_url: URL of webhook server (default from env)
        token: Authentication token (default from env)
        domain: Domain name (default from env)
        project: Project name (default from env)
        role: Agent role (default: "builder")
        session_id: Unique session ID (auto-generated if None)
        name: Optional agent name
        parent_id: Parent agent ID for hierarchy tracking
        tmux_session: tmux session:window location
        skills: List of attached skills
        heartbeat_interval: Seconds between heartbeats
        enabled: Whether telemetry is enabled

    Returns:
        Configured AgentTelemetry instance
    """
    # Get defaults from environment
    base_config = TelemetryConfig.from_env(domain=domain, project=project, role=role)

    # Override with explicit arguments
    if webhook_url is not None:
        base_config.webhook_url = webhook_url
    if token is not None:
        base_config.token = token
    if session_id is not None:
        base_config.session_id = session_id
    if name is not None:
        base_config.name = name
    if parent_id is not None:
        base_config.parent_id = parent_id
    if tmux_session is not None:
        base_config.tmux_session = tmux_session
    if skills is not None:
        base_config.skills = skills
    if heartbeat_interval != 30:
        base_config.heartbeat_interval = heartbeat_interval
    if not enabled:
        base_config.enabled = enabled

    return AgentTelemetry(base_config)


def create_agent_telemetry_from_env(
    domain: str | None = None,
    project: str | None = None,
    role: str = "builder",
) -> AgentTelemetry:
    """Create agent telemetry from environment variables.

    Args:
        domain: Override domain from environment
        project: Override project from environment
        role: Agent role (default: "builder")

    Returns:
        Configured AgentTelemetry instance
    """
    config = TelemetryConfig.from_env(domain=domain, project=project, role=role)
    return AgentTelemetry(config)

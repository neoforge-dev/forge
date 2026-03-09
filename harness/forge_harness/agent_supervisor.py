"""Agent Supervisor for FORGE.

Monitors agent health and auto-restarts crashed agents.
Implements watchdog pattern with configurable restart policies.
"""

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from forge_harness.health_checks import ServiceHealth, get_health_registry

logger = logging.getLogger(__name__)


class RestartPolicy(str, Enum):
    """Agent restart policies."""

    ALWAYS = "always"  # Always restart
    ON_FAILURE = "on_failure"  # Only restart on crash
    NEVER = "never"  # Never restart


@dataclass
class AgentSpec:
    """Specification for a supervised agent."""

    agent_id: str
    role: str
    task: str
    session: str = "forge"
    window: str = ""
    domain: str | None = None
    project: str | None = None
    skills: list[str] = field(default_factory=list)

    # Provider selection (claude, amp, cursor, pi, opencode)
    provider: str = "claude"
    stream_json: bool = False

    # Restart settings
    restart_policy: RestartPolicy = RestartPolicy.ON_FAILURE
    max_restarts: int = 3
    restart_delay: float = 5.0  # Seconds between restarts
    health_check_interval: float = 30.0  # Seconds between health checks

    # Runtime state
    restart_count: int = 0
    last_restart: str | None = None
    status: str = "stopped"
    pid: int | None = None


@dataclass
class SupervisorState:
    """Supervisor runtime state."""

    agents: dict[str, AgentSpec] = field(default_factory=dict)
    started_at: str | None = None
    is_running: bool = False


class AgentSupervisor:
    """Supervisor that monitors and restarts agents."""

    def __init__(
        self,
        forge_root: Path,
        state_file: Path | None = None,
        spawn_script: Path | None = None,
    ):
        self.forge_root = Path(forge_root)
        self.state_file = state_file or (self.forge_root / ".forge_supervisor_state.json")
        self.spawn_script = spawn_script or (
            self.forge_root / ".claude/skills/spawn-agent/scripts/spawn.sh"
        )

        self.state = SupervisorState()
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}
        self._callbacks: list[Callable] = []

    def _save_state(self):
        """Save supervisor state to disk."""
        data = {
            "started_at": self.state.started_at,
            "is_running": self.state.is_running,
            "agents": {
                agent_id: {
                    "agent_id": spec.agent_id,
                    "role": spec.role,
                    "task": spec.task,
                    "session": spec.session,
                    "window": spec.window,
                    "domain": spec.domain,
                    "project": spec.project,
                    "skills": spec.skills,
                    "provider": spec.provider,
                    "stream_json": spec.stream_json,
                    "restart_policy": spec.restart_policy.value,
                    "max_restarts": spec.max_restarts,
                    "restart_delay": spec.restart_delay,
                    "health_check_interval": spec.health_check_interval,
                    "restart_count": spec.restart_count,
                    "last_restart": spec.last_restart,
                    "status": spec.status,
                    "pid": spec.pid,
                }
                for agent_id, spec in self.state.agents.items()
            },
        }
        self.state_file.write_text(json.dumps(data, indent=2))

    def _load_state(self):
        """Load supervisor state from disk."""
        if not self.state_file.exists():
            return

        data = json.loads(self.state_file.read_text())
        self.state.started_at = data.get("started_at")
        self.state.is_running = data.get("is_running", False)

        for agent_id, agent_data in data.get("agents", {}).items():
            self.state.agents[agent_id] = AgentSpec(
                agent_id=agent_data["agent_id"],
                role=agent_data["role"],
                task=agent_data["task"],
                session=agent_data.get("session", "forge"),
                window=agent_data.get("window", ""),
                domain=agent_data.get("domain"),
                project=agent_data.get("project"),
                skills=agent_data.get("skills", []),
                provider=agent_data.get("provider", "claude"),
                stream_json=agent_data.get("stream_json", False),
                restart_policy=RestartPolicy(agent_data.get("restart_policy", "on_failure")),
                max_restarts=agent_data.get("max_restarts", 3),
                restart_delay=agent_data.get("restart_delay", 5.0),
                health_check_interval=agent_data.get("health_check_interval", 30.0),
                restart_count=agent_data.get("restart_count", 0),
                last_restart=agent_data.get("last_restart"),
                status=agent_data.get("status", "stopped"),
                pid=agent_data.get("pid"),
            )

    def register(self, spec: AgentSpec) -> AgentSpec:
        """Register an agent for supervision."""
        self.state.agents[spec.agent_id] = spec
        self._save_state()
        logger.info(f"Registered agent {spec.agent_id} for supervision")
        return spec

    def unregister(self, agent_id: str) -> bool:
        """Unregister an agent from supervision."""
        if agent_id in self.state.agents:
            # Stop monitoring task if running
            if agent_id in self._tasks:
                self._tasks[agent_id].cancel()
                del self._tasks[agent_id]

            del self.state.agents[agent_id]
            self._save_state()
            logger.info(f"Unregistered agent {agent_id}")
            return True
        return False

    def _check_agent_alive(self, spec: AgentSpec) -> bool:
        """Check if an agent is still running in tmux."""
        target = f"{spec.session}:{spec.window}" if spec.window else spec.session

        try:
            # Check if tmux session exists
            result = subprocess.run(
                ["tmux", "has-session", "-t", spec.session],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False

            # Check if window exists and has claude running
            result = subprocess.run(
                ["tmux", "list-panes", "-t", target, "-F", "#{pane_current_command}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False

            # Check for claude or gtimeout (wrapper)
            commands = result.stdout.strip().split("\n")
            return any(cmd in ["claude", "gtimeout", "timeout"] for cmd in commands)

        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning(f"Health check failed for {spec.agent_id}: {e}")
            return False

    async def _spawn_agent(self, spec: AgentSpec) -> bool:
        """Spawn an agent using the spawn script."""
        try:
            args = [
                str(self.spawn_script),
                spec.role,
                spec.task,
                "--session",
                spec.session,
            ]

            if spec.window:
                args.extend(["--window", spec.window])
            if spec.domain:
                args.extend(["--domain", spec.domain])
            if spec.project:
                args.extend(["--project", spec.project])
            if spec.skills:
                args.extend(["--skills", ",".join(spec.skills)])

            # Provider selection (claude, amp, cursor, pi, opencode)
            if spec.provider and spec.provider != "claude":
                args.extend(["--provider", spec.provider])
            if spec.stream_json:
                args.append("--stream-json")

            # Don't auto-start server (supervisor handles infrastructure)
            args.append("--no-server")

            logger.info(f"Spawning agent {spec.agent_id}: {' '.join(args)}")

            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.forge_root),
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60,
            )

            if process.returncode == 0:
                spec.status = "running"
                spec.last_restart = datetime.now(UTC).isoformat()
                self._save_state()
                logger.info(f"Successfully spawned agent {spec.agent_id}")
                return True
            else:
                logger.error(f"Failed to spawn {spec.agent_id}: {stderr.decode()}")
                return False

        except TimeoutError:
            logger.error(f"Timeout spawning agent {spec.agent_id}")
            return False
        except Exception as e:
            logger.error(f"Error spawning agent {spec.agent_id}: {e}")
            return False

    async def _monitor_agent(self, agent_id: str):
        """Monitor a single agent and restart if needed."""
        while self._running and agent_id in self.state.agents:
            spec = self.state.agents[agent_id]

            await asyncio.sleep(spec.health_check_interval)

            if not self._running or agent_id not in self.state.agents:
                break

            # Check agent health
            is_alive = self._check_agent_alive(spec)

            if is_alive:
                spec.status = "running"
                self._save_state()
                continue

            # Agent is dead
            logger.warning(f"Agent {agent_id} is not responding")
            spec.status = "crashed"

            # Check restart policy
            if spec.restart_policy == RestartPolicy.NEVER:
                logger.info(f"Agent {agent_id} has restart_policy=never, not restarting")
                continue

            # Check restart count
            if spec.restart_count >= spec.max_restarts:
                logger.error(
                    f"Agent {agent_id} exceeded max restarts ({spec.max_restarts}), giving up"
                )
                spec.status = "failed"
                self._save_state()

                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback("agent_failed", spec)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

                continue

            # Wait before restart
            logger.info(f"Waiting {spec.restart_delay}s before restarting {agent_id}")
            await asyncio.sleep(spec.restart_delay)

            # Attempt restart
            spec.restart_count += 1
            logger.info(
                f"Restarting agent {agent_id} (attempt {spec.restart_count}/{spec.max_restarts})"
            )

            success = await self._spawn_agent(spec)
            if success:
                logger.info(f"Successfully restarted agent {agent_id}")

                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback("agent_restarted", spec)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

    def on_event(self, callback: Callable):
        """Register a callback for supervisor events."""
        self._callbacks.append(callback)

    async def restart_agent(self, agent_id: str, reason: str = "manual") -> bool:
        """Restart a specific agent.

        Args:
            agent_id: ID of agent to restart
            reason: Reason for restart (e.g., "health_failure", "manual")

        Returns:
            True if restart was successful, False otherwise
        """
        if agent_id not in self.state.agents:
            logger.error(f"Cannot restart unknown agent: {agent_id}")
            return False

        spec = self.state.agents[agent_id]

        # Check restart policy
        if spec.restart_policy == RestartPolicy.NEVER:
            logger.info(f"Agent {agent_id} has restart_policy=never, not restarting")
            return False

        # Check restart count
        if spec.restart_count >= spec.max_restarts:
            logger.error(
                f"Agent {agent_id} exceeded max restarts ({spec.max_restarts}), cannot restart"
            )
            return False

        # Increment restart count
        spec.restart_count += 1
        logger.info(
            f"Restarting agent {agent_id} (reason: {reason}, "
            f"attempt {spec.restart_count}/{spec.max_restarts})"
        )

        # Attempt restart
        success = await self._spawn_agent(spec)
        if success:
            logger.info(f"Successfully restarted agent {agent_id}")

            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback("agent_restarted", spec)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

            return True
        else:
            logger.error(f"Failed to restart agent {agent_id}")
            return False

    def _on_health_failure(
        self, service_name: str, failure_count: int, health: ServiceHealth
    ) -> None:
        """Handle health check failure.

        Called by HealthCheckRegistry when a service fails MAX_CONSECUTIVE_FAILURES times.

        Args:
            service_name: Name of the failing service (should match agent_id)
            failure_count: Number of consecutive failures
            health: ServiceHealth result
        """
        logger.warning(
            f"Health failure detected for {service_name}: {failure_count} consecutive failures"
        )

        # Try to find matching agent by ID
        if service_name not in self.state.agents:
            logger.debug(f"No agent registered with ID {service_name}")
            return

        self.state.agents[service_name]

        # Schedule restart in background
        asyncio.create_task(self.restart_agent(service_name, reason="health_failure"))

    def register_with_health_checks(self) -> None:
        """Register supervisor to receive health check failure notifications."""
        registry = get_health_registry()
        registry.on_health_failure(self._on_health_failure)
        logger.info("Registered supervisor with health check system")

    async def start(self):
        """Start the supervisor."""
        self._load_state()
        self._running = True
        self.state.is_running = True
        self.state.started_at = datetime.now(UTC).isoformat()
        self._save_state()

        logger.info(f"Starting supervisor with {len(self.state.agents)} agents")

        # Start monitoring tasks for all registered agents
        for agent_id in self.state.agents:
            task = asyncio.create_task(self._monitor_agent(agent_id))
            self._tasks[agent_id] = task

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop the supervisor."""
        logger.info("Stopping supervisor")
        self._running = False
        self.state.is_running = False

        # Cancel all monitoring tasks
        for task in self._tasks.values():
            task.cancel()

        # Wait for tasks to finish
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        self._tasks.clear()
        self._save_state()

    def get_status(self) -> dict:
        """Get supervisor status."""
        return {
            "is_running": self._running,
            "started_at": self.state.started_at,
            "agents": {
                agent_id: {
                    "status": spec.status,
                    "restart_count": spec.restart_count,
                    "last_restart": spec.last_restart,
                    "role": spec.role,
                    "provider": spec.provider,
                    "session": spec.session,
                    "window": spec.window,
                }
                for agent_id, spec in self.state.agents.items()
            },
        }


def create_supervisor(forge_root: Path | None = None) -> AgentSupervisor:
    """Create a supervisor instance."""
    if forge_root is None:
        forge_root = Path(os.environ.get("FORGE_ROOT", "."))

    return AgentSupervisor(forge_root=forge_root)


async def run_supervisor(forge_root: Path | None = None):
    """Run the supervisor as a standalone process."""
    supervisor = create_supervisor(forge_root)

    def log_event(event_type: str, spec: AgentSpec):
        logger.info(f"Supervisor event: {event_type} for {spec.agent_id}")

    supervisor.on_event(log_event)

    try:
        await supervisor.start()
    except KeyboardInterrupt:
        await supervisor.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_supervisor())

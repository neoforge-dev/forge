"""
Relay Worker - Queue-First Task Dispatcher

Processes task queue (.forge/tasks/*.json) and dispatches tasks to agents.
Supports multi-node scheduling with lease-based ownership.

Features:
- Scan pending tasks from .forge/tasks/*.json
- Match tasks to available agents based on assigned_agent field
- Use dispatch file pattern: write instructions to .forge/dispatches/
- Update task status to "in_progress" when dispatched
- Update task status to "completed" when agent signals done
- Run as a loop with configurable poll interval
"""

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

FORGE_ROOT = Path(os.environ.get("FORGE_ROOT", "/home/openclaw/work/FORGE"))
TASKS_DIR = FORGE_ROOT / ".forge/tasks"
DISPATCHES_DIR = FORGE_ROOT / ".forge/dispatches"
HEARTBEAT_RESULTS_DIR = FORGE_ROOT / ".forge/heartbeat/results"
DEFAULT_POLL_INTERVAL = 30  # seconds
DEFAULT_LEASE_TTL = 300  # 5 minutes


class RelayWorker:
    """Worker that scans and dispatches pending tasks to agents."""

    def __init__(
        self,
        forge_root: Path = FORGE_ROOT,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        dry_run: bool = False,
    ):
        self.forge_root = forge_root
        self.tasks_dir = forge_root / ".forge/tasks"
        self.dispatches_dir = forge_root / ".forge/dispatches"
        self.heartbeat_results_dir = forge_root / ".forge/heartbeat/results"
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.running = False

    def scan_pending_tasks(self) -> list[dict[str, Any]]:
        """Scan for pending tasks in the tasks directory."""
        tasks = []
        if not self.tasks_dir.exists():
            logger.debug(f"Tasks directory does not exist: {self.tasks_dir}")
            return tasks

        for task_file in self.tasks_dir.glob("*.json"):
            try:
                with open(task_file) as f:
                    task = json.load(f)
                status = task.get("status", "pending")
                if status == "pending":
                    task["_file"] = str(task_file)
                    tasks.append(task)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read task file {task_file}: {e}")

        logger.debug(f"Found {len(tasks)} pending tasks")
        return tasks

    def get_available_agents(self) -> list[str]:
        """Discover available tmux agents."""
        try:
            result = subprocess.run(
                ["tmux", "list-windows", "-t", "forge", "-F", "#{window_name}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return []
            agents = [
                f"forge:{line.strip()}" for line in result.stdout.splitlines() if line.strip()
            ]
            return agents
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

    def get_agent_status(self, agent: str) -> dict[str, Any]:
        """Get agent status from heartbeat results."""
        agent_name = agent.replace("forge:", "")
        results_dir = self.heartbeat_results_dir
        if not results_dir.exists():
            return {"status": "unknown", "context_pct": -1}

        # Look for agent heartbeat file
        heartbeat_file = results_dir / f"{agent_name}.json"
        if heartbeat_file.exists():
            try:
                with open(heartbeat_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback: check tmux window directly
        try:
            result = subprocess.run(
                ["tmux", "list-windows", "-t", "forge", "-F", "#{window_name}: #{window_active}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith(f"{agent_name}:") or line.startswith(f"{agent}:"):
                        # Window exists, assume available
                        return {"status": "idle", "context_pct": 50}
        except Exception:
            pass

        return {"status": "unknown", "context_pct": -1}

    def is_agent_available(self, agent: str) -> bool:
        """Check if agent is available for dispatch."""
        status = self.get_agent_status(agent)
        agent_status = status.get("status", "unknown")
        context_pct = status.get("context_pct", -1)

        # If status is unknown, check if tmux window exists
        if agent_status == "unknown":
            try:
                result = subprocess.run(
                    ["tmux", "has-session", "-t", agent],
                    capture_output=True,
                    timeout=5,
                )
                return result.returncode == 0
            except Exception:
                return False

        # Available if idle/waiting and context < 80%
        if agent_status in ("idle", "waiting", "ready"):
            return context_pct < 80
        return False

    def create_dispatch_file(self, task: dict[str, Any], agent: str) -> Path:
        """Create a dispatch file for the task."""
        task_id = task.get("id", "unknown")
        subject = task.get("subject", "No subject")
        description = task.get("description", "")

        dispatch_content = f"""# Task: {subject}
## Agent: {agent}
## Priority: {task.get("priority", "medium")}
## Type: Auto-dispatched

## What
{subject}
{description}

## Task ID
{task_id}

## Source
Auto-dispatched from relay worker
"""

        # Create unique dispatch filename
        timestamp = int(time.time())
        dispatch_file = self.dispatches_dir / f"relay-{task_id}-{timestamp}.md"

        with open(dispatch_file, "w") as f:
            f.write(dispatch_content)

        logger.info(f"Created dispatch file: {dispatch_file}")
        return dispatch_file

    def send_to_agent(self, agent: str, message: str) -> bool:
        """Send message to tmux agent."""
        try:
            # Use tmux send-keys with verification
            result = subprocess.run(
                ["tmux", "send-keys", "-t", agent, message, "Enter"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to send to {agent}: {e}")
            return False

    def dispatch_task(self, task: dict[str, Any], agent: str) -> bool:
        """Dispatch a task to an agent."""
        task_id = task.get("id", "unknown")

        dispatch_file = self.create_dispatch_file(task, agent)

        # Send message to agent
        message = f"Task: {dispatch_file.name}"

        if self.dry_run:
            logger.info(f"[DRY RUN] Would dispatch task {task_id} to {agent}: {message}")
            return True

        success = self.send_to_agent(agent, message)

        if success:
            # Update task status to in_progress
            self.update_task_status(task, "in_progress", {"assigned_agent": agent})
            logger.info(f"Dispatched task {task_id} to {agent}")
        else:
            logger.error(f"Failed to dispatch task {task_id} to {agent}")

        return success

    def update_task_status(
        self, task: dict[str, Any], status: str, extra_fields: dict[str, Any] | None = None
    ) -> None:
        """Update task status in the task file."""
        task_file = Path(task.get("_file", ""))
        if not task_file.exists():
            logger.warning(f"Task file not found: {task_file}")
            return

        task["status"] = status
        task["updated_at"] = datetime.now().isoformat()

        if extra_fields:
            task.update(extra_fields)

        if not self.dry_run:
            with open(task_file, "w") as f:
                json.dump(task, f, indent=2)

        logger.debug(f"Updated task {task.get('id')} status to {status}")

    def check_task_completion(self, task: dict[str, Any]) -> bool:
        """Check if task is completed via heartbeat results."""
        task_id = task.get("id", "")

        # Check for completion signal in heartbeat results
        results_dir = self.heartbeat_results_dir
        if not results_dir.exists():
            return False

        # Look for completion marker
        for result_file in results_dir.glob("*.json"):
            try:
                with open(result_file) as f:
                    data = json.load(f)
                completed_tasks = data.get("completed_tasks", [])
                if task_id in completed_tasks:
                    return True
            except (json.JSONDecodeError, OSError):
                continue

        return False

    def process_tasks(self) -> dict[str, int]:
        """Process all pending tasks. Returns stats."""
        stats = {"scanned": 0, "dispatched": 0, "completed": 0, "skipped": 0}

        tasks = self.scan_pending_tasks()
        stats["scanned"] = len(tasks)

        available_agents = self.get_available_agents()
        logger.debug(f"Available agents: {available_agents}")

        for task in tasks:
            task_id = task.get("id", "unknown")

            # Check if already assigned
            assigned_agent = task.get("assigned_agent")
            if assigned_agent:
                # Check if completed
                if self.check_task_completion(task):
                    self.update_task_status(task, "completed")
                    stats["completed"] += 1
                    logger.info(f"Task {task_id} marked as completed")
                else:
                    stats["skipped"] += 1
                continue

            # Find available agent
            agent = None
            for a in available_agents:
                if self.is_agent_available(a):
                    agent = a
                    break

            if agent:
                if self.dispatch_task(task, agent):
                    stats["dispatched"] += 1
                else:
                    stats["skipped"] += 1
            else:
                stats["skipped"] += 1
                logger.debug(f"No available agent for task {task_id}")

        return stats

    async def run_once(self) -> dict[str, int]:
        """Run one iteration of the relay loop."""
        logger.debug("Running relay iteration...")
        return self.process_tasks()

    async def run(self) -> None:
        """Run the relay worker loop."""
        self.running = True
        logger.info(f"Starting relay worker (poll interval: {self.poll_interval}s)")

        while self.running:
            try:
                stats = await self.run_once()
                logger.debug(f"Relay stats: {stats}")
            except Exception as e:
                logger.error(f"Error in relay loop: {e}")

            # Sleep until next iteration
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the relay worker."""
        self.running = False
        logger.info("Relay worker stopped")


def run_relay_worker(
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    dry_run: bool = False,
    once: bool = False,
) -> None:
    """Run the relay worker."""
    worker = RelayWorker(
        forge_root=FORGE_ROOT,
        poll_interval=poll_interval,
        dry_run=dry_run,
    )

    if once:
        # Run once and exit
        stats = asyncio.run(worker.run_once())
        print(f"Relay worker stats: {stats}")
    else:
        # Run continuously
        asyncio.run(worker.run())


if __name__ == "__main__":
    run_relay_worker()

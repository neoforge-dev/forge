"""Priority-based task dispatch for fleet agents.

Continuously monitors portfolio features.json files, scores tasks using the prioritizer,
and dispatches highest priority work to idle agents via tmux.

Architecture:
    1. Scan all features.json files in portfolio
    2. Score pending features using iteration.prioritizer
    3. Check tmux for idle agents (no "Working"/"Composing"/"Running" in output)
    4. Match highest priority task to appropriate idle agent
    5. Dispatch via tmux send-keys
    6. Log to .forge/heartbeat/dispatch.log
    7. Update .forge/heartbeat/forge.json with heartbeat count

Agent Mapping:
    - tech: General backend/infrastructure tasks
    - qa: Testing, quality assurance tasks
    - game: Gaming projects (startup-simulator, septica)
    - codex: Quick code generation, frontend
    - gemini: Research, documentation, content
    - kimi: Analysis, debugging, complex problem solving
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..iteration.prioritizer import (
    PrioritizationStrategy,
    PrioritizedTask,
    prioritize_tasks,
)
from ..logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Default tmux session for FORGE agents
FORGE_SESSION = "forge"

# Dispatch cooldown to prevent re-dispatching the same task
DISPATCH_COOLDOWN_MINUTES = 30

# Agent capabilities mapping (domain/project patterns to agent preferences)
AGENT_CAPABILITIES = {
    "tech": {
        "domains": ["harness", "neoforge-dev", "forge-shared"],
        "keywords": ["backend", "api", "database", "infrastructure", "devops"],
    },
    "qa": {
        "domains": ["*"],  # All domains
        "keywords": ["test", "quality", "qa", "coverage", "validation"],
    },
    "game": {
        "domains": ["codeswiftr-com"],
        "projects": ["startup-simulator", "septica", "game-engine"],
        "keywords": ["game", "simulation", "gameplay"],
    },
    "codex": {
        "domains": ["*"],  # All domains
        "keywords": ["frontend", "ui", "component", "react", "vue", "quick-fix"],
    },
    "gemini": {
        "domains": ["*"],  # All domains
        "keywords": ["research", "documentation", "content", "analysis"],
    },
    "kimi": {
        "domains": ["*"],  # All domains
        "keywords": ["debug", "troubleshoot", "investigate", "complex", "analysis"],
    },
}

# Activity indicators that mean agent is busy
BUSY_INDICATORS = [
    "Working",
    "Composing",
    "Running",
    "Implementing",
    "Writing",
    "Executing",
    "Testing",
    "Building",
]

# Idle indicators (agent is waiting for input)
IDLE_INDICATORS = [
    "context left",
    "Type your message",
    "INSERT",
    "shortcuts",
    "Command:",
    "$",  # Shell prompt
]


# =============================================================================
# Dispatch Cooldown Cache
# =============================================================================

# Module-level cache to track recently dispatched tasks
# Format: {task_id: dispatch_timestamp}
_recently_dispatched: dict[str, datetime] = {}


def is_recently_dispatched(task_id: str, cooldown_minutes: int = DISPATCH_COOLDOWN_MINUTES) -> bool:
    """Check if a task was recently dispatched and is in cooldown period.

    Args:
        task_id: Feature/task ID to check
        cooldown_minutes: Cooldown period in minutes (default from config)

    Returns:
        True if task is in cooldown, False if ready to dispatch
    """
    if task_id not in _recently_dispatched:
        return False

    dispatch_time = _recently_dispatched[task_id]
    now = datetime.now(UTC)
    elapsed = (now - dispatch_time).total_seconds() / 60.0

    if elapsed >= cooldown_minutes:
        # Cooldown expired, remove from cache
        del _recently_dispatched[task_id]
        return False

    logger.debug(
        f"Task {task_id} in cooldown (dispatched {elapsed:.1f}m ago, need {cooldown_minutes}m)"
    )
    return True


def mark_task_dispatched(task_id: str) -> None:
    """Mark a task as recently dispatched.

    Args:
        task_id: Feature/task ID that was dispatched
    """
    _recently_dispatched[task_id] = datetime.now(UTC)
    logger.debug(f"Marked task {task_id} as dispatched at {_recently_dispatched[task_id]}")


def cleanup_expired_cooldowns(cooldown_minutes: int = DISPATCH_COOLDOWN_MINUTES) -> int:
    """Remove expired entries from the cooldown cache.

    Args:
        cooldown_minutes: Cooldown period in minutes

    Returns:
        Number of entries removed
    """
    now = datetime.now(UTC)
    expired_tasks = []

    for task_id, dispatch_time in _recently_dispatched.items():
        elapsed = (now - dispatch_time).total_seconds() / 60.0
        if elapsed >= cooldown_minutes:
            expired_tasks.append(task_id)

    for task_id in expired_tasks:
        del _recently_dispatched[task_id]

    if expired_tasks:
        logger.debug(f"Cleaned up {len(expired_tasks)} expired cooldown entries")

    return len(expired_tasks)


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class AgentStatus:
    """Status of a fleet agent."""

    name: str
    is_idle: bool
    last_output: str
    capabilities: dict[str, Any]


@dataclass
class DispatchResult:
    """Result of a task dispatch operation."""

    success: bool
    agent: str
    task: PrioritizedTask
    message: str
    timestamp: datetime


# =============================================================================
# Task Collection
# =============================================================================


def get_pending_tasks(forge_root: Path | None = None) -> list[PrioritizedTask]:
    """Collect and prioritize all pending tasks from portfolio.

    Args:
        forge_root: Root of FORGE portfolio (auto-detected if None)

    Returns:
        List of prioritized tasks, highest priority first
    """
    if forge_root is None:
        forge_root = _find_forge_root()

    logger.info(f"Scanning portfolio at {forge_root} for pending tasks")

    # Use prioritizer to scan portfolio and score tasks
    tasks = prioritize_tasks(
        features_paths=None,  # Auto-discover
        forge_root=forge_root,
        strategy=PrioritizationStrategy.BALANCED,  # Balance impact, urgency, and effort
    )

    # Filter to only pending/in_progress tasks
    pending = [t for t in tasks if t.status in ["pending", "in_progress"]]

    logger.info(f"Found {len(pending)} pending tasks (from {len(tasks)} total)")

    return pending


# =============================================================================
# Agent Status Detection
# =============================================================================


def get_idle_agents() -> list[AgentStatus]:
    """Check tmux for idle agents in the forge session.

    Returns:
        List of idle agents with their capabilities
    """
    idle_agents = []

    for agent_name, capabilities in AGENT_CAPABILITIES.items():
        window_name = f"{FORGE_SESSION}:{agent_name}"

        # Capture last 10 lines from tmux pane
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", window_name, "-p", "-S", "-10"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                logger.warning(f"Agent {agent_name} not found in tmux session")
                continue

            output = result.stdout.strip()

            # Check for busy indicators
            is_busy = any(indicator in output for indicator in BUSY_INDICATORS)

            # Check for idle indicators
            is_idle = any(indicator in output for indicator in IDLE_INDICATORS)

            # If no clear signal, consider idle (conservative approach)
            if not is_busy and not is_idle:
                is_idle = True

            if is_idle and not is_busy:
                idle_agents.append(
                    AgentStatus(
                        name=agent_name,
                        is_idle=True,
                        last_output=output[-200:],  # Last 200 chars
                        capabilities=capabilities,
                    )
                )
                logger.debug(f"Agent {agent_name} is idle")
            else:
                logger.debug(f"Agent {agent_name} is busy")

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout checking agent {agent_name}")
        except FileNotFoundError:
            logger.error("tmux not found - is it installed?")
            break

    logger.info(f"Found {len(idle_agents)} idle agents")
    return idle_agents


# =============================================================================
# Agent Matching
# =============================================================================


def match_task_to_agent(task: PrioritizedTask, agents: list[AgentStatus]) -> AgentStatus | None:
    """Match a task to the most suitable idle agent.

    Args:
        task: Task to match
        agents: List of available idle agents

    Returns:
        Best matching agent or None if no match
    """
    if not agents:
        return None

    # Extract task metadata
    domain = task.metadata.get("domain", "")
    title_lower = task.title.lower()
    description = task.metadata.get("description", "").lower()
    text = f"{title_lower} {description}"

    # Score each agent for this task
    scores = []
    for agent in agents:
        score = 0.0
        capabilities = agent.capabilities

        # Domain match
        if domain in capabilities.get("domains", []) or "*" in capabilities.get("domains", []):
            score += 2.0

        # Project match
        for project in capabilities.get("projects", []):
            if project in domain or project in title_lower:
                score += 3.0
                break

        # Keyword match
        for keyword in capabilities.get("keywords", []):
            if keyword in text:
                score += 1.0

        scores.append((agent, score))

    # Sort by score (highest first)
    scores.sort(key=lambda x: x[1], reverse=True)

    # Return best match if score > 0
    if scores and scores[0][1] > 0:
        best_agent, best_score = scores[0]
        logger.info(
            f"Matched task {task.feature_id} to agent {best_agent.name} (score: {best_score:.1f})"
        )
        return best_agent

    # Fallback: return first available agent
    logger.info(f"No strong match for task {task.feature_id}, using first available agent")
    return agents[0] if agents else None


# =============================================================================
# Task Dispatch (Legacy - kept for backward compatibility)
# =============================================================================


def dispatch_task_legacy(agent: AgentStatus, task: PrioritizedTask) -> DispatchResult:
    """Dispatch a task to an agent via tmux (legacy method).

    Args:
        agent: Target agent
        task: Task to dispatch

    Returns:
        DispatchResult with success status and details
    """
    # Format task instruction
    instruction = _format_task_instruction(task)

    window_name = f"{FORGE_SESSION}:{agent.name}"

    try:
        # Send task instruction to tmux
        subprocess.run(
            ["tmux", "send-keys", "-t", window_name, instruction, "Enter"],
            check=True,
            timeout=5,
        )

        logger.info(f"Dispatched task {task.feature_id} to agent {agent.name}")

        return DispatchResult(
            success=True,
            agent=agent.name,
            task=task,
            message=f"Successfully dispatched to {agent.name}",
            timestamp=datetime.now(UTC),
        )

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to dispatch task to {agent.name}: {e}")
        return DispatchResult(
            success=False,
            agent=agent.name,
            task=task,
            message=f"tmux send-keys failed: {e}",
            timestamp=datetime.now(UTC),
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout dispatching to {agent.name}")
        return DispatchResult(
            success=False,
            agent=agent.name,
            task=task,
            message="tmux send-keys timeout",
            timestamp=datetime.now(UTC),
        )


# =============================================================================
# Task Dispatch (API-First with Tmux Fallback)
# =============================================================================


async def dispatch_task(
    agent: AgentStatus,
    task: PrioritizedTask,
    *,
    verify: bool = False,
    dry_run: bool = False,
) -> DispatchResult:
    """Dispatch a task to an agent via API first, then tmux fallback.

    This is the preferred dispatch method. Uses CommandCenterClient.send_message()
    with automatic fallback to tmux if the API is unavailable.

    Args:
        agent: Target agent
        task: Task to dispatch
        verify: Whether to verify agent received the message
        dry_run: If True, only simulate the dispatch

    Returns:
        DispatchResult with success status and method used
    """
    # Format task instruction
    instruction = _format_task_instruction(task)

    if dry_run:
        logger.info(f"[DRY RUN] Would dispatch task {task.feature_id} to agent {agent.name}")
        return DispatchResult(
            success=True,
            agent=agent.name,
            task=task,
            message=f"DRY RUN: Would dispatch to {agent.name}",
            timestamp=datetime.now(UTC),
        )

    from .dispatch_api import dispatch_to_agent

    agent_id = f"forge:{agent.name}"

    # Use the new API-based dispatch
    result = await dispatch_to_agent(
        agent_id=agent_id,
        message=instruction,
        verify=verify,
    )

    # Convert dispatch_api result to priority_dispatch format
    return DispatchResult(
        success=result.success,
        agent=agent.name,
        task=task,
        message=result.message or f"Dispatched via {result.method}",
        timestamp=result.timestamp,
    )


def _format_task_instruction(task: PrioritizedTask) -> str:
    """Format a task into a clear instruction for an agent.

    Args:
        task: Task to format

    Returns:
        Formatted instruction string
    """
    # Build instruction
    parts = [
        f"Implement feature: {task.title}",
        f"ID: {task.feature_id}",
        f"Priority: {task.metadata.get('priority', 'medium')} (score: {task.score:.1f})",
    ]

    # Add acceptance criteria if available
    if task.acceptance_criteria:
        parts.append("Acceptance Criteria:")
        for criterion in task.acceptance_criteria[:3]:  # Max 3 for brevity
            parts.append(f"  - {criterion}")

    # Add test command if available
    if task.test_command:
        parts.append(f"Test: {task.test_command}")

    return " | ".join(parts)


# =============================================================================
# Logging
# =============================================================================


def log_dispatch(result: DispatchResult, log_file: Path) -> None:
    """Log dispatch result to file.

    Args:
        result: Dispatch result to log
        log_file: Path to log file
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    timestamp = result.timestamp.isoformat()
    status = "SUCCESS" if result.success else "FAILED"

    log_entry = (
        f"[{timestamp}] {status} - "
        f"Agent: {result.agent}, "
        f"Task: {result.task.feature_id} ({result.task.title}), "
        f"Score: {result.task.score:.1f}, "
        f"Message: {result.message}\n"
    )

    with open(log_file, "a") as f:
        f.write(log_entry)

    logger.info(f"Logged dispatch: {status} - {result.task.feature_id} -> {result.agent}")


def update_heartbeat(forge_root: Path) -> None:
    """Update heartbeat count in forge.json.

    Args:
        forge_root: Root of FORGE portfolio
    """
    heartbeat_file = forge_root / ".forge" / "heartbeat" / "forge.json"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)

    # Load current state
    if heartbeat_file.exists():
        with open(heartbeat_file) as f:
            data = json.load(f)
    else:
        data = {"heartbeat_count": 0}

    # Increment
    data["heartbeat_count"] = data.get("heartbeat_count", 0) + 1
    data["timestamp"] = datetime.now(UTC).isoformat()

    # Save
    with open(heartbeat_file, "w") as f:
        json.dump(data, f, indent=2)

    logger.debug(f"Updated heartbeat count to {data['heartbeat_count']}")


# =============================================================================
# Main Dispatch Loop
# =============================================================================


async def run_dispatch_loop(
    interval: int = 60,
    forge_root: Path | None = None,
    max_iterations: int = 0,
    dry_run: bool = False,
) -> None:
    """Run continuous priority-based task dispatch loop.

    Args:
        interval: Seconds between dispatch cycles
        forge_root: Root of FORGE portfolio (auto-detected if None)
        max_iterations: Maximum iterations (0 = infinite)
        dry_run: If True, only preview dispatches
    """
    if forge_root is None:
        forge_root = _find_forge_root()

    log_file = forge_root / ".forge" / "heartbeat" / "dispatch.log"

    logger.info(f"Starting priority dispatch loop (interval: {interval}s)")
    if dry_run:
        logger.info("DRY RUN MODE: No commands will actually be sent to agents")
    logger.info(f"FORGE root: {forge_root}")
    logger.info(f"Log file: {log_file}")

    iteration = 0

    while True:
        iteration += 1
        logger.info(f"=== Dispatch Cycle #{iteration} ===")

        try:
            # 0. Cleanup expired cooldowns periodically
            if iteration % 5 == 0:  # Every 5 cycles
                cleanup_expired_cooldowns()

            # 1. Get pending tasks (prioritized)
            tasks = get_pending_tasks(forge_root)

            if not tasks:
                logger.info("No pending tasks found")
            else:
                logger.info(
                    f"Top task: {tasks[0].feature_id} ({tasks[0].title}) - score: {tasks[0].score:.1f}"
                )

                # 2. Check if top task is in cooldown
                highest_priority_task = tasks[0]
                if is_recently_dispatched(highest_priority_task.feature_id):
                    logger.info(
                        f"Top task {highest_priority_task.feature_id} is in cooldown, skipping dispatch this cycle"
                    )
                else:
                    # 3. Get idle agents
                    idle_agents = get_idle_agents()

                    if not idle_agents:
                        logger.info("No idle agents available")
                    else:
                        # 4. Match highest priority task to best agent
                        best_agent = match_task_to_agent(highest_priority_task, idle_agents)

                        if best_agent:
                            # 5. Dispatch task
                            result = await dispatch_task(
                                best_agent,
                                highest_priority_task,
                                dry_run=dry_run
                            )

                            # 6. Log result
                            log_dispatch(result, log_file)

                            if result.success:
                                # Mark task as dispatched to prevent re-dispatch
                                mark_task_dispatched(highest_priority_task.feature_id)
                                status_label = "✓ [DRY RUN]" if dry_run else "✓"
                                logger.info(
                                    f"{status_label} Dispatched {result.task.feature_id} to {result.agent} "
                                    f"(priority score: {result.task.score:.1f})"
                                )
                            else:
                                logger.error(f"✗ Failed to dispatch: {result.message}")

            # 7. Update heartbeat
            if not dry_run:
                update_heartbeat(forge_root)

        except Exception as e:
            logger.error(f"Error in dispatch cycle: {e}", exc_info=True)

        # Check max iterations
        if max_iterations > 0 and iteration >= max_iterations:
            logger.info(f"Reached max iterations ({max_iterations}), stopping")
            break

        # Wait for next cycle
        logger.info(f"Waiting {interval}s until next cycle...")
        await asyncio.sleep(interval)


# =============================================================================
# Helper Functions
# =============================================================================


def _find_forge_root() -> Path:
    """Find FORGE repository root by looking for markers.

    Returns:
        Path to FORGE root

    Raises:
        FileNotFoundError: If FORGE root cannot be found
    """
    current = Path.cwd()

    # Check current and parent directories
    for _ in range(10):
        # Check for explicit marker
        if (current / ".forge-root").exists():
            return current

        # Check for .forge directory
        if (current / ".forge").exists():
            return current

        # Check for domains.yaml (FORGE portfolio marker)
        if (current / "domains.yaml").exists():
            return current

        # Check for .git with typical FORGE structure
        if (current / ".git").exists():
            # Look for harness directory
            if (current / "harness").exists():
                return current

        parent = current.parent
        if parent == current:
            break
        current = parent

    raise FileNotFoundError(
        "Could not find FORGE repository root. "
        "Run from within FORGE or create a .forge-root marker file."
    )

"""Delegation Dispatcher for FORGE Harness.

Routes prioritized tasks to optimal agents based on type, availability, and specialization.
Creates tmux sessions and monitors execution.

Features:
- Create tmux session for each dispatched task
- Send task prompt with proper context
- Tag sessions with task ID and agent type
- Update task status to 'in_progress' in tracking
- Handle agent unavailability with fallback

Usage:
    from forge_harness.iteration.dispatch import dispatch_task, DispatchResult

    # Dispatch a single task
    result = dispatch_task("HRN-001")
    if result.success:
        print(f"Task dispatched to {result.session_id}")

    # Dispatch multiple tasks
    results = dispatch_tasks(["HRN-001", "HRN-002", "HRN-003"])

    # Check dispatch status
    status = get_dispatch_status("HRN-001")
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.fleet.dashboard import FleetStatus, get_fleet_status
from forge_harness.iteration.prioritizer import PrioritizedTask, load_features_from_file
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# Agent Type Mapping
# ============================================================================


class AgentType(str, Enum):
    """Types of agents available for dispatch."""

    BACKEND = "backend-engineer"
    FRONTEND = "frontend-builder"
    DEBUG = "debug-detective"
    QA = "qa-tester"
    CONTENT = "content-writer"
    EXTERNAL_CODEX = "codex"
    EXTERNAL_OPENCODE = "opencode"
    EXTERNAL_GEMINI = "gemini"
    EXTERNAL_CURSOR = "cursor"
    EXTERNAL_AMP = "amp"
    CLAUDE = "claude"
    UNKNOWN = "unknown"


# Agent type routing based on task characteristics
AGENT_TYPE_ROUTING = {
    # Backend tasks
    "backend": AgentType.BACKEND,
    "api": AgentType.BACKEND,
    "database": AgentType.BACKEND,
    "server": AgentType.BACKEND,
    # Frontend tasks
    "frontend": AgentType.FRONTEND,
    "ui": AgentType.FRONTEND,
    "component": AgentType.FRONTEND,
    "react": AgentType.FRONTEND,
    # Testing tasks
    "test": AgentType.QA,
    "e2e": AgentType.QA,
    "integration": AgentType.QA,
    # Debugging tasks
    "debug": AgentType.DEBUG,
    "fix": AgentType.DEBUG,
    "error": AgentType.DEBUG,
    # Content tasks
    "content": AgentType.CONTENT,
    "docs": AgentType.CONTENT,
    "blog": AgentType.CONTENT,
}

# Fallback agents when primary is unavailable
AGENT_FALLBACKS = {
    AgentType.BACKEND: [AgentType.EXTERNAL_OPENCODE, AgentType.EXTERNAL_CODEX, AgentType.CLAUDE],
    AgentType.FRONTEND: [AgentType.EXTERNAL_CURSOR, AgentType.CLAUDE],
    AgentType.DEBUG: [AgentType.BACKEND, AgentType.CLAUDE],
    AgentType.QA: [AgentType.BACKEND, AgentType.CLAUDE],
    AgentType.CONTENT: [AgentType.EXTERNAL_GEMINI, AgentType.CLAUDE],
    AgentType.EXTERNAL_CODEX: [AgentType.BACKEND, AgentType.CLAUDE],
    AgentType.EXTERNAL_OPENCODE: [AgentType.BACKEND, AgentType.CLAUDE],
    AgentType.EXTERNAL_GEMINI: [AgentType.CONTENT, AgentType.CLAUDE],
    AgentType.EXTERNAL_CURSOR: [AgentType.FRONTEND, AgentType.CLAUDE],
    AgentType.EXTERNAL_AMP: [AgentType.DEBUG, AgentType.CLAUDE],
}


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class DispatchResult:
    """Result of dispatching a task to an agent."""

    success: bool
    task_id: str
    session_id: str | None = None
    agent_type: AgentType | None = None
    status: str = "failed"  # dispatched, failed, unavailable
    error: str | None = None
    dispatched_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "agent_type": self.agent_type.value if self.agent_type else None,
            "status": self.status,
            "error": self.error,
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
            "metadata": self.metadata,
        }


@dataclass
class DispatchStatus:
    """Status of a dispatched task."""

    task_id: str
    session_id: str
    agent_type: AgentType
    status: str  # dispatched, running, completed, failed
    dispatched_at: datetime
    last_activity: datetime | None = None
    output_size: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Tmux Session Management
# ============================================================================


class TmuxDispatcher:
    """Manages tmux sessions for task dispatch."""

    def __init__(self, forge_root: Path | None = None):
        """Initialize TmuxDispatcher.

        Args:
            forge_root: Root directory of FORGE portfolio
        """
        self.forge_root = forge_root or Path.cwd()
        self.tracking_dir = self.forge_root / ".forge/dispatch"
        self.tracking_dir.mkdir(exist_ok=True)

    def create_session(
        self, task_id: str, agent_type: AgentType, working_dir: Path | None = None
    ) -> str | None:
        """Create a new tmux session for a task.

        Args:
            task_id: Unique task identifier
            agent_type: Type of agent to use
            working_dir: Working directory for the session

        Returns:
            Session ID if successful, None otherwise
        """
        # Generate session name
        session_name = f"forge-task-{task_id}"

        # Check if session already exists
        if self._session_exists(session_name):
            logger.warning(f"Session {session_name} already exists")
            return None

        # Determine working directory
        work_dir = working_dir or self.forge_root

        try:
            # Create tmux session
            subprocess.run(
                [
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-c",
                    str(work_dir),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )

            logger.info(f"Created tmux session: {session_name}")
            return session_name

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "Unknown error"
            logger.error(f"Failed to create tmux session: {stderr}")
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout creating tmux session: {session_name}")
            return None
        except FileNotFoundError:
            logger.error("tmux not found - cannot create session")
            return None

    def send_command(self, session_id: str, command: str) -> bool:
        """Send a command to a tmux session.

        Args:
            session_id: Session to send command to
            command: Command to execute

        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", session_id, command, "Enter"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            logger.debug(f"Sent command to {session_id}: {command}")
            return True

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "Unknown error"
            logger.error(f"Failed to send command: {stderr}")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout sending command to {session_id}")
            return False

    def tag_session(self, session_id: str, tags: dict[str, str]) -> bool:
        """Tag a tmux session with metadata.

        Args:
            session_id: Session to tag
            tags: Key-value pairs to tag session with

        Returns:
            True if successful, False otherwise
        """
        try:
            # Set tmux user variables for tags
            for key, value in tags.items():
                subprocess.run(
                    ["tmux", "set-option", "-t", session_id, f"@{key}", value],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )

            logger.debug(f"Tagged session {session_id} with {len(tags)} tags")
            return True

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "Unknown error"
            logger.error(f"Failed to tag session: {stderr}")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout tagging session {session_id}")
            return False

    def get_session_output(self, session_id: str, lines: int = 100) -> str:
        """Capture output from a tmux session.

        Args:
            session_id: Session to capture from
            lines: Number of lines to capture

        Returns:
            Session output as string
        """
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session_id, "-p", "-S", f"-{lines}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "Unknown error"
            logger.error(f"Failed to capture output: {stderr}")
            return ""
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout capturing output from {session_id}")
            return ""

    def _session_exists(self, session_name: str) -> bool:
        """Check if a tmux session exists."""
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session_name],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False


# ============================================================================
# Task Tracking
# ============================================================================


class TaskTracker:
    """Manages task dispatch tracking."""

    def __init__(self, tracking_dir: Path):
        """Initialize TaskTracker.

        Args:
            tracking_dir: Directory to store tracking files
        """
        self.tracking_dir = tracking_dir
        self.tracking_dir.mkdir(exist_ok=True)

    def save_dispatch(self, task_id: str, result: DispatchResult):
        """Save dispatch result to tracking file.

        Args:
            task_id: Task identifier
            result: Dispatch result to save
        """
        tracking_file = self.tracking_dir / f"{task_id}.json"

        try:
            with open(tracking_file, "w") as f:
                json.dump(result.to_dict(), f, indent=2)

            logger.debug(f"Saved dispatch tracking for {task_id}")

        except Exception as e:
            logger.error(f"Failed to save dispatch tracking: {e}")

    def load_dispatch(self, task_id: str) -> DispatchResult | None:
        """Load dispatch result from tracking file.

        Args:
            task_id: Task identifier

        Returns:
            DispatchResult if found, None otherwise
        """
        tracking_file = self.tracking_dir / f"{task_id}.json"

        if not tracking_file.exists():
            return None

        try:
            with open(tracking_file) as f:
                data = json.load(f)

            # Convert back to DispatchResult
            agent_type = AgentType(data["agent_type"]) if data.get("agent_type") else None
            dispatched_at = (
                datetime.fromisoformat(data["dispatched_at"]) if data.get("dispatched_at") else None
            )

            return DispatchResult(
                success=data["success"],
                task_id=data["task_id"],
                session_id=data.get("session_id"),
                agent_type=agent_type,
                status=data.get("status", "unknown"),
                error=data.get("error"),
                dispatched_at=dispatched_at,
                metadata=data.get("metadata", {}),
            )

        except Exception as e:
            logger.error(f"Failed to load dispatch tracking: {e}")
            return None

    def update_status(self, task_id: str, status: str):
        """Update task status.

        Args:
            task_id: Task identifier
            status: New status
        """
        result = self.load_dispatch(task_id)
        if result:
            result.status = status
            self.save_dispatch(task_id, result)


# ============================================================================
# Agent Selection
# ============================================================================


def select_agent_for_task(
    task: PrioritizedTask, fleet_status: FleetStatus | None = None
) -> AgentType:
    """Select the best agent for a task.

    Args:
        task: Task to dispatch
        fleet_status: Current fleet status for availability check

    Returns:
        Selected agent type
    """
    # Check task metadata for explicit agent type
    if task.metadata.get("agent_type"):
        try:
            return AgentType(task.metadata["agent_type"])
        except ValueError:
            pass

    # Analyze task title and description for keywords
    text = f"{task.title} {task.feature_id}".lower()

    for keyword, agent_type in AGENT_TYPE_ROUTING.items():
        if keyword in text:
            logger.debug(f"Selected {agent_type} for task {task.feature_id} (keyword: {keyword})")
            return agent_type

    # Default to backend for unknown tasks
    logger.debug(f"Defaulting to {AgentType.BACKEND} for task {task.feature_id}")
    return AgentType.BACKEND


def get_fallback_agent(primary_agent: AgentType) -> AgentType | None:
    """Get fallback agent if primary is unavailable.

    Args:
        primary_agent: Primary agent type

    Returns:
        Fallback agent type or None
    """
    fallbacks = AGENT_FALLBACKS.get(primary_agent, [])
    if fallbacks:
        return fallbacks[0]
    return None


# ============================================================================
# Dispatch Functions
# ============================================================================


def dispatch_task(
    task_id: str,
    features_path: Path | None = None,
    forge_root: Path | None = None,
    force_agent: AgentType | None = None,
) -> DispatchResult:
    """Dispatch a task to the optimal agent.

    Args:
        task_id: Task identifier (e.g., "HRN-001")
        features_path: Path to features.json (defaults to harness/features.json)
        forge_root: Root directory of FORGE portfolio
        force_agent: Force specific agent type (for testing)

    Returns:
        DispatchResult with session info and status
    """
    logger.info(f"Dispatching task: {task_id}")

    # Initialize components
    forge_root = forge_root or Path.cwd()
    features_path = features_path or forge_root / "features.json"

    dispatcher = TmuxDispatcher(forge_root)
    tracker = TaskTracker(dispatcher.tracking_dir)

    # Load task from features
    features = load_features_from_file(features_path)
    task_data = None

    for feature in features:
        if feature.get("id") == task_id:
            task_data = feature
            break

    if not task_data:
        error = f"Task {task_id} not found in {features_path}"
        logger.error(error)
        return DispatchResult(success=False, task_id=task_id, status="not_found", error=error)

    # Convert to PrioritizedTask
    task = PrioritizedTask(
        feature_id=task_id,
        title=task_data.get("title", ""),
        score=0.0,
        impact=task_data.get("priority", 5),
        urgency=5,
        effort=3,
        domain_weight=1.0,
        reasoning="",
        status=task_data.get("status", "pending"),
        epic=task_data.get("epic"),
        acceptance_criteria=task_data.get("acceptance_criteria", []),
        test_command=task_data.get("test_command"),
        metadata=task_data,
    )

    # Get fleet status for availability checking
    try:
        fleet_status = get_fleet_status()
    except Exception as e:
        logger.warning(f"Failed to get fleet status: {e}")
        fleet_status = None

    # Select agent
    agent_type = force_agent or select_agent_for_task(task, fleet_status)

    # Create tmux session
    session_id = dispatcher.create_session(task_id, agent_type, forge_root)

    if not session_id:
        # Try fallback agent
        fallback_agent = get_fallback_agent(agent_type)
        if fallback_agent:
            logger.warning(f"Primary agent {agent_type} unavailable, trying {fallback_agent}")
            agent_type = fallback_agent
            session_id = dispatcher.create_session(task_id, agent_type, forge_root)

        if not session_id:
            error = f"Failed to create tmux session for {task_id}"
            logger.error(error)
            return DispatchResult(
                success=False, task_id=task_id, agent_type=agent_type, status="failed", error=error
            )

    # Tag session
    tags = {
        "task_id": task_id,
        "agent_type": agent_type.value,
        "epic": task.epic or "unknown",
        "dispatched_at": datetime.now(UTC).isoformat(),
    }
    dispatcher.tag_session(session_id, tags)

    # Build task prompt
    prompt = _build_task_prompt(task)

    # Send task to agent
    if not dispatcher.send_command(session_id, prompt):
        error = f"Failed to send task to {session_id}"
        logger.error(error)
        return DispatchResult(
            success=False,
            task_id=task_id,
            session_id=session_id,
            agent_type=agent_type,
            status="failed",
            error=error,
        )

    # Create result
    result = DispatchResult(
        success=True,
        task_id=task_id,
        session_id=session_id,
        agent_type=agent_type,
        status="in_progress",
        dispatched_at=datetime.now(UTC),
        metadata={
            "title": task.title,
            "epic": task.epic,
            "acceptance_criteria": task.acceptance_criteria,
            "test_command": task.test_command,
        },
    )

    # Save tracking
    tracker.save_dispatch(task_id, result)

    logger.info(
        f"Successfully dispatched {task_id} to {session_id} ({agent_type.value})",
        extra={"task_id": task_id, "session_id": session_id, "agent_type": agent_type.value},
    )

    return result


def dispatch_tasks(
    task_ids: list[str],
    features_path: Path | None = None,
    forge_root: Path | None = None,
    max_parallel: int = 3,
) -> list[DispatchResult]:
    """Dispatch multiple tasks to agents.

    Args:
        task_ids: List of task identifiers
        features_path: Path to features.json
        forge_root: Root directory of FORGE portfolio
        max_parallel: Maximum number of tasks to dispatch in parallel

    Returns:
        List of DispatchResults
    """
    results = []

    # Dispatch up to max_parallel tasks
    for task_id in task_ids[:max_parallel]:
        result = dispatch_task(task_id, features_path, forge_root)
        results.append(result)

        # Small delay between dispatches
        time.sleep(0.5)

    return results


def get_dispatch_status(task_id: str, forge_root: Path | None = None) -> DispatchResult | None:
    """Get the dispatch status for a task.

    Args:
        task_id: Task identifier
        forge_root: Root directory of FORGE portfolio

    Returns:
        DispatchResult if found, None otherwise
    """
    forge_root = forge_root or Path.cwd()
    tracking_dir = forge_root / ".forge/dispatch"

    tracker = TaskTracker(tracking_dir)
    return tracker.load_dispatch(task_id)


# ============================================================================
# Helper Functions
# ============================================================================


def _build_task_prompt(task: PrioritizedTask) -> str:
    """Build a task prompt for the agent.

    Args:
        task: Task to build prompt for

    Returns:
        Formatted prompt string
    """
    prompt_parts = [
        f"Implement {task.feature_id}: {task.title}",
        "",
    ]

    if task.acceptance_criteria:
        prompt_parts.append("Acceptance Criteria:")
        for i, criteria in enumerate(task.acceptance_criteria, 1):
            prompt_parts.append(f"{i}. {criteria}")
        prompt_parts.append("")

    if task.test_command:
        prompt_parts.append(f"Test Command: {task.test_command}")
        prompt_parts.append("")

    if task.epic:
        prompt_parts.append(f"Epic: {task.epic}")

    return "\n".join(prompt_parts)


# ============================================================================
# CLI Support
# ============================================================================


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FORGE Task Dispatcher")
    parser.add_argument("task_id", help="Task ID to dispatch (e.g., HRN-001)")
    parser.add_argument(
        "--features", "-f", type=Path, help="Path to features.json (default: features.json)"
    )
    parser.add_argument(
        "--forge-root", "-r", type=Path, help="FORGE root directory (default: current directory)"
    )

    args = parser.parse_args()

    result = dispatch_task(args.task_id, args.features, args.forge_root)

    if result.success:
        print(f"✅ Task {result.task_id} dispatched to {result.session_id}")
        print(f"   Agent: {result.agent_type.value if result.agent_type else 'unknown'}")
        print(f"   Status: {result.status}")
    else:
        print(f"❌ Failed to dispatch {result.task_id}")
        print(f"   Error: {result.error}")

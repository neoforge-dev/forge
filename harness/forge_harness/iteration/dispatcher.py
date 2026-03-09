"""
FORGE Harness Delegation Dispatcher
====================================

Dispatches tasks to external agents via tmux sessions.

Usage:
    from forge_harness.iteration.dispatcher import dispatch_task, DispatchResult

    # Dispatch a task from features.json
    result = dispatch_task("HRN-001", features_path="features.json")
    print(f"Dispatched to {result.agent_type} in session {result.session_id}")
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class AgentType(str, Enum):
    """Available external agent types."""

    OPENCODE = "opencode"
    CODEX = "codex"
    GEMINI = "gemini"
    CLAUDE = "claude"

    def __str__(self) -> str:
        return self.value

    @property
    def command_template(self) -> str:
        """Return command template for this agent type."""
        return {
            AgentType.OPENCODE: "opencode run '{prompt}'",
            AgentType.CODEX: "codex exec '{prompt}'",
            AgentType.GEMINI: "gemini -y '{prompt}'",
            AgentType.CLAUDE: "claude '{prompt}'",
        }.get(self, "echo '{prompt}'")


class DispatchStatus(str, Enum):
    """Status of task dispatch."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


# =============================================================================
# Custom Exceptions
# =============================================================================


class TaskNotFoundError(Exception):
    """Task ID not found in features.json."""

    pass


class DispatchError(Exception):
    """Failed to dispatch task to agent."""

    pass


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class DispatchResult:
    """Result of task dispatch operation.

    Attributes:
        task_id: Feature ID from features.json (e.g., "HRN-001")
        session_id: tmux session name (e.g., "opencode-HRN-003")
        agent_type: Type of agent dispatched to
        started_at: When the dispatch occurred
        status: Current dispatch status
        prompt: The prompt sent to the agent
        working_directory: Directory where agent was started
    """

    task_id: str
    session_id: str
    agent_type: AgentType
    started_at: datetime
    status: DispatchStatus
    prompt: str
    working_directory: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "agent_type": self.agent_type.value,
            "started_at": self.started_at.isoformat(),
            "status": self.status.value,
            "prompt": self.prompt,
            "working_directory": self.working_directory,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DispatchResult:
        """Create from dictionary."""
        return cls(
            task_id=data["task_id"],
            session_id=data["session_id"],
            agent_type=AgentType(data["agent_type"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            status=DispatchStatus(data["status"]),
            prompt=data["prompt"],
            working_directory=data.get("working_directory"),
        )

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> DispatchResult:
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))


# =============================================================================
# Helper Functions
# =============================================================================


def _load_task(task_id: str, features_path: Path) -> dict:
    """Load task from features.json by ID.

    Args:
        task_id: Feature ID to find
        features_path: Path to features.json file

    Returns:
        Task dictionary with id, title, description, etc.

    Raises:
        TaskNotFoundError: If task_id not found in features.json
        FileNotFoundError: If features.json doesn't exist
        json.JSONDecodeError: If features.json is invalid JSON
    """
    if not features_path.exists():
        raise FileNotFoundError(f"features.json not found at: {features_path}")

    with open(features_path) as f:
        data = json.load(f)

    # Search for task by ID
    features = data.get("features", [])
    for feature in features:
        if feature.get("id") == task_id:
            logger.info(f"Found task {task_id}: {feature.get('title')}")
            return feature

    raise TaskNotFoundError(f"Task {task_id} not found in {features_path}")


def _build_prompt(task: dict) -> str:
    """Build agent prompt from task details.

    Args:
        task: Task dictionary from features.json

    Returns:
        Formatted prompt string including title, description, and criteria
    """
    prompt_parts = []

    # Add title
    if title := task.get("title"):
        prompt_parts.append(f"Task: {title}")

    # Add description
    if description := task.get("description"):
        prompt_parts.append(f"\nDescription: {description}")

    # Add acceptance criteria
    if criteria := task.get("acceptance_criteria"):
        if isinstance(criteria, list) and criteria:
            prompt_parts.append("\nAcceptance Criteria:")
            for criterion in criteria:
                prompt_parts.append(f"- {criterion}")
        elif isinstance(criteria, str):
            prompt_parts.append(f"\nAcceptance Criteria: {criteria}")

    # Add test command if available
    if test_cmd := task.get("test_command"):
        prompt_parts.append(f"\nTest Command: {test_cmd}")

    prompt = "\n".join(prompt_parts)
    logger.debug(f"Built prompt ({len(prompt)} chars)")

    return prompt


def _create_session(
    session_name: str,
    working_dir: str | None = None,
    timeout: int = 30,
) -> bool:
    """Create tmux session if it doesn't exist.

    Args:
        session_name: Name for the tmux session
        working_dir: Starting directory for session
        timeout: Timeout for tmux command in seconds

    Returns:
        True if session created or already exists, False on error

    Raises:
        subprocess.TimeoutExpired: If tmux command times out
    """
    try:
        # Check if session exists
        check_cmd = ["tmux", "has-session", "-t", session_name]
        result = subprocess.run(
            check_cmd,
            capture_output=True,
            timeout=timeout,
        )

        if result.returncode == 0:
            logger.info(f"Session {session_name} already exists")
            return True

        # Create new session
        create_cmd = ["tmux", "new-session", "-d", "-s", session_name]
        if working_dir:
            create_cmd.extend(["-c", working_dir])

        result = subprocess.run(
            create_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode == 0:
            logger.info(f"Created tmux session: {session_name}")
            return True
        else:
            logger.error(f"Failed to create session: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"tmux command timed out after {timeout} seconds")
        raise
    except FileNotFoundError:
        logger.error("tmux not found - ensure tmux is installed")
        return False


def _send_to_session(
    session_name: str,
    prompt: str,
    agent_type: AgentType,
    timeout: int = 30,
) -> bool:
    """Send prompt to tmux session via send-keys.

    Args:
        session_name: tmux session name
        prompt: Prompt to send to agent
        agent_type: Type of agent (determines command format)
        timeout: Timeout for tmux command in seconds

    Returns:
        True if command sent successfully, False on error

    Raises:
        subprocess.TimeoutExpired: If tmux command times out
    """
    try:
        # Build agent command from template
        agent_cmd = agent_type.command_template.format(prompt=prompt)

        # Send command to session
        send_cmd = ["tmux", "send-keys", "-t", session_name, agent_cmd, "Enter"]

        result = subprocess.run(
            send_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode == 0:
            logger.info(f"Sent prompt to {session_name} using {agent_type}")
            return True
        else:
            logger.error(f"Failed to send keys: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"tmux send-keys timed out after {timeout} seconds")
        raise
    except FileNotFoundError:
        logger.error("tmux not found - ensure tmux is installed")
        return False


# =============================================================================
# Agent Selection
# =============================================================================


def select_agent(task: dict) -> AgentType:
    """Select agent based on task keywords.

    Args:
        task: Task dictionary with title and description

    Returns:
        Best matching AgentType for this task
    """
    import re

    # Combine title and description for keyword matching
    text = f"{task.get('title', '')} {task.get('description', '')}".lower()

    def has_keyword(text: str, keywords: list[str]) -> bool:
        """Check if text contains any keyword using word boundaries."""
        for kw in keywords:
            # Use word boundary matching to avoid substring matches
            # e.g., "ui" won't match in "build"
            if re.search(r"\b" + re.escape(kw) + r"\b", text):
                return True
        return False

    # Research tasks → gemini (check first, most specific)
    research_keywords = [
        "research",
        "explore",
        "analyze",
        "investigate",
        "study",
        "survey",
    ]
    if has_keyword(text, research_keywords):
        logger.debug("Selected gemini (research task)")
        return AgentType.GEMINI

    # Frontend/UI tasks → codex (check before backend to prioritize)
    frontend_keywords = [
        "frontend",
        "ui",
        "component",
        "react",
        "css",
        "html",
        "tsx",
        "jsx",
        "styling",
        "vue",
        "svelte",
    ]
    if has_keyword(text, frontend_keywords):
        logger.debug("Selected codex (frontend task)")
        return AgentType.CODEX

    # Backend/API tasks → opencode
    backend_keywords = [
        "backend",
        "api",
        "endpoint",
        "fastapi",
        "database",
        "python service",
        "sql",
        "migration",
        "orm",
    ]
    if has_keyword(text, backend_keywords):
        logger.debug("Selected opencode (backend task)")
        return AgentType.OPENCODE

    # Test tasks → codex
    test_keywords = ["test", "coverage", "pytest", "unittest", "jest", "e2e"]
    if has_keyword(text, test_keywords):
        logger.debug("Selected codex (test task)")
        return AgentType.CODEX

    # Default to codex for general coding tasks
    logger.debug("Selected codex (default)")
    return AgentType.CODEX


# =============================================================================
# Main Dispatch Function
# =============================================================================


def dispatch_task(
    task_id: str,
    features_path: str | Path = "features.json",
    timeout: int = 30,
    working_dir: str | None = None,
) -> DispatchResult:
    """Dispatch a task to an agent via tmux.

    This is the main entry point for task dispatch. It:
    1. Loads the task from features.json
    2. Selects the appropriate agent type
    3. Creates a tmux session
    4. Builds and sends the prompt
    5. Returns dispatch result

    Args:
        task_id: Feature ID from features.json (e.g., "HRN-001")
        features_path: Path to features.json file
        timeout: Timeout for tmux commands in seconds
        working_dir: Working directory for tmux session

    Returns:
        DispatchResult with dispatch details

    Raises:
        TaskNotFoundError: If task_id not in features.json
        DispatchError: If tmux dispatch fails
        FileNotFoundError: If features.json not found
    """
    # Convert to Path if string
    if isinstance(features_path, str):
        features_path = Path(features_path)

    logger.info(f"Dispatching task {task_id} from {features_path}")

    # Load task from features.json
    task = _load_task(task_id, features_path)

    # Select agent type
    agent_type = select_agent(task)

    # Build session name
    session_id = f"{agent_type.value}-{task_id}"

    # Build prompt
    prompt = _build_prompt(task)

    # Create tmux session
    if not _create_session(session_id, working_dir, timeout):
        raise DispatchError(f"Failed to create tmux session: {session_id}")

    # Send prompt to session
    if not _send_to_session(session_id, prompt, agent_type, timeout):
        raise DispatchError(f"Failed to send prompt to session: {session_id}")

    # Create result
    result = DispatchResult(
        task_id=task_id,
        session_id=session_id,
        agent_type=agent_type,
        started_at=datetime.now(UTC),
        status=DispatchStatus.RUNNING,
        prompt=prompt,
        working_directory=working_dir,
    )

    logger.info(f"Successfully dispatched {task_id} to {agent_type} in session {session_id}")

    return result

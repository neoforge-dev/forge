"""Agent Selector for FORGE Harness.

Automatically selects the best agent for a task type based on capabilities matrix.
Routes frontend→Task, backend→Codex/OpenCode, research→Gemini.

Usage:
    from forge_harness.agents import AgentSelector, AgentSelectionResult

    selector = AgentSelector()
    result = selector.select_agent("Create a React component for user profiles")
    if result.is_available:
        subprocess.run(result.agent_command.split(), cwd="/path/to/project")
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum

from forge_harness.agents.codex import CodexExecutor
from forge_harness.agents.opencode import OpenCodeExecutor
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class TaskType(str, Enum):
    """Types of tasks that can be classified."""

    FRONTEND = "frontend"
    BACKEND = "backend"
    RESEARCH = "research"
    TEST = "test"
    REFACTOR = "refactor"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentCapabilities:
    """Capabilities matrix for different agents."""

    frontend: list[str] = field(
        default_factory=lambda: ["Task(subagent_type=frontend-builder)", "cursor"]
    )
    backend: list[str] = field(
        default_factory=lambda: ["opencode", "codex", "Task(subagent_type=backend-engineer)"]
    )
    research: list[str] = field(default_factory=lambda: ["gemini", "Task(subagent_type=explore)"])
    test: list[str] = field(
        default_factory=lambda: ["Task(subagent_type=qa-test-guardian)", "codex"]
    )
    refactor: list[str] = field(
        default_factory=lambda: ["Task(subagent_type=refactor-surgeon)", "amp"]
    )


@dataclass(frozen=True)
class AgentSelectionResult:
    """Result of agent selection."""

    agent_type: str
    agent_command: str
    reasoning: str
    is_available: bool
    fallback_agent: str | None = None
    task_type: TaskType = TaskType.UNKNOWN
    confidence: float = 0.0


class AgentSelector:
    """Selects the optimal agent for a given task based on type and availability."""

    def __init__(self) -> None:
        """Initialize AgentSelector with capabilities matrix."""
        self.capabilities = AgentCapabilities()
        self._opencode_executor = OpenCodeExecutor()
        self._codex_executor = CodexExecutor()

    def select_agent(self, task: str) -> AgentSelectionResult:
        """Select the best agent for a task.

        Args:
            task: Task description to classify and route

        Returns:
            AgentSelectionResult with optimal agent and reasoning
        """
        logger.info(f"Selecting agent for task: {task[:100]}...")

        # Classify task type
        task_type = self._classify_task(task)
        logger.debug(f"Task classified as: {task_type}")

        # Get available agents for this task type
        available_agents = self._get_available_agents(task_type)

        if not available_agents:
            logger.warning(f"No agents available for task type: {task_type}")
            return AgentSelectionResult(
                agent_type="none",
                agent_command="",
                reasoning=f"No agents available for {task_type} task",
                is_available=False,
                task_type=task_type,
                confidence=0.0,
            )

        # Select primary agent
        primary_agent = available_agents[0]
        fallback_agent = available_agents[1] if len(available_agents) > 1 else None

        # Generate command
        command = self._generate_agent_command(primary_agent, task)

        # Build reasoning
        reasoning = self._build_reasoning(
            task_type, primary_agent, fallback_agent, len(available_agents)
        )

        result = AgentSelectionResult(
            agent_type=primary_agent,
            agent_command=command,
            reasoning=reasoning,
            is_available=True,
            fallback_agent=fallback_agent,
            task_type=task_type,
            confidence=self._calculate_confidence(task_type, len(available_agents)),
        )

        logger.info(
            f"Selected agent: {primary_agent} for {task_type} task",
            extra={
                "agent_type": primary_agent,
                "task_type": task_type,
                "has_fallback": fallback_agent is not None,
                "confidence": result.confidence,
            },
        )

        return result

    def _classify_task(self, task: str) -> TaskType:
        """Classify task into type based on keywords and patterns.

        Args:
            task: Task description to classify

        Returns:
            TaskType classification
        """
        task_lower = task.lower()

        # Frontend patterns (higher priority for specific terms)
        frontend_keywords = [
            "react component",
            "vue component",
            "frontend component",
            "ui component",
            "css",
            "scss",
            "tailwind",
            "bootstrap",
            "javascript",
            "typescript",
            "jsx",
            "tsx",
            "html",
            "interface",
            "user interface",
            "button",
            "form",
            "modal",
            "dashboard",
            "layout",
            "responsive",
            "styled",
            "frontend",
            "ui",
        ]

        # Backend patterns
        backend_keywords = [
            "fastapi endpoint",
            "api endpoint",
            "rest api",
            "graphql api",
            "backend",
            "api",
            "server",
            "database",
            "endpoint",
            "service",
            "model",
            "controller",
            "repository",
            "migration",
            "fastapi",
            "django",
            "flask",
            "express",
            "node",
            "python",
            "sql",
            "nosql",
            "auth",
            "authentication",
            "middleware",
            "microservice",
            "rest",
            "graphql",
        ]

        # Research patterns (higher priority for research-specific terms)
        research_keywords = [
            "research best",
            "analyze performance",
            "investigate",
            "explore",
            "study",
            "research",
            "analyze",
            "documentation",
            "docs",
            "review code",
            "audit",
            "survey",
            "benchmark",
            "compare",
            "evaluation",
            "find",
            "search",
            "lookup",
            "investigation",
            "analysis",
            "report",
        ]

        # Test patterns (higher priority for test-specific terms)
        test_keywords = [
            "unit test",
            "integration test",
            "end-to-end test",
            "e2e test",
            "test",
            "testing",
            "pytest",
            "jest",
            "test suite",
            "coverage",
            "tdd",
            "spec",
            "assertion",
            "mock",
            "stub",
            "fixture",
            "test case",
            "automated test",
        ]

        # Refactor patterns (higher priority for refactor-specific terms)
        refactor_keywords = [
            "refactor legacy",
            "technical debt",
            "code cleanup",
            "optimize database",
            "refactor",
            "refactoring",
            "cleanup",
            "optimize",
            "improve",
            "restructure",
            "reorganize",
            "simplify",
            "modernize",
            "update",
            "migrate",
            "rewrite",
            "technical debt",
            "code quality",
            "maintainability",
            "performance",
        ]

        # Count matches for each category (longer phrases get higher weight)
        frontend_matches = sum(
            2 if " " in keyword else 1 for keyword in frontend_keywords if keyword in task_lower
        )
        backend_matches = sum(
            2 if " " in keyword else 1 for keyword in backend_keywords if keyword in task_lower
        )
        research_matches = sum(
            2 if " " in keyword else 1 for keyword in research_keywords if keyword in task_lower
        )
        test_matches = sum(
            2 if " " in keyword else 1 for keyword in test_keywords if keyword in task_lower
        )
        refactor_matches = sum(
            2 if " " in keyword else 1 for keyword in refactor_keywords if keyword in task_lower
        )

        # Find the category with most matches
        matches = {
            TaskType.FRONTEND: frontend_matches,
            TaskType.BACKEND: backend_matches,
            TaskType.RESEARCH: research_matches,
            TaskType.TEST: test_matches,
            TaskType.REFACTOR: refactor_matches,
        }

        max_matches = max(matches.values())
        if max_matches == 0:
            logger.debug(f"No clear classification for task: {task[:50]}...")
            return TaskType.UNKNOWN

        # Get all categories with max matches
        best_types = [task_type for task_type, count in matches.items() if count == max_matches]

        # Priority order for tie-breaking (based on common task importance)
        priority_order = [
            TaskType.RESEARCH,
            TaskType.TEST,
            TaskType.REFACTOR,
            TaskType.FRONTEND,
            TaskType.BACKEND,
        ]

        for priority_type in priority_order:
            if priority_type in best_types:
                logger.debug(f"Task classified as {priority_type} (matches: {max_matches})")
                return priority_type

        # Fallback to first best type
        selected_type = best_types[0]
        logger.debug(f"Task classified as {selected_type} (matches: {max_matches})")
        return selected_type

    def _get_available_agents(self, task_type: TaskType) -> list[str]:
        """Get list of available agents for a task type, ordered by preference.

        Args:
            task_type: Type of task to find agents for

        Returns:
            List of available agent identifiers in order of preference
        """
        # Get all possible agents for this task type
        if task_type == TaskType.FRONTEND:
            agents = self.capabilities.frontend
        elif task_type == TaskType.BACKEND:
            agents = self.capabilities.backend
        elif task_type == TaskType.RESEARCH:
            agents = self.capabilities.research
        elif task_type == TaskType.TEST:
            agents = self.capabilities.test
        elif task_type == TaskType.REFACTOR:
            agents = self.capabilities.refactor
        else:
            # Default to backend agents for unknown tasks
            agents = self.capabilities.backend

        # Check availability of each agent
        available_agents = []
        for agent in agents:
            if self._is_agent_available(agent):
                available_agents.append(agent)
                logger.debug(f"Agent {agent} is available")
            else:
                logger.debug(f"Agent {agent} is not available")

        return available_agents

    def _is_agent_available(self, agent: str) -> bool:
        """Check if a specific agent is available.

        Args:
            agent: Agent identifier to check

        Returns:
            True if agent is available, False otherwise
        """
        try:
            # Check external CLI tools
            if agent == "opencode":
                return self._opencode_executor.is_available()
            elif agent == "codex":
                return self._codex_executor.is_available()
            elif agent == "gemini":
                return self._check_cli_available("gemini")
            elif agent == "cursor":
                return self._check_cli_available("cursor")
            elif agent == "amp":
                return self._check_cli_available("amp")
            # Check Task-based agents via tmux sessions
            elif agent.startswith("Task("):
                return self._check_tmux_session_available()
            else:
                # Default to available for unknown agents
                return True

        except Exception as exc:
            logger.warning(f"Error checking availability for {agent}: {exc}")
            return False

    def _check_cli_available(self, cli_name: str) -> bool:
        """Check if a CLI tool is available in PATH.

        Args:
            cli_name: Name of CLI tool to check

        Returns:
            True if CLI is available, False otherwise
        """
        try:
            result = subprocess.run(
                ["which", cli_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_tmux_session_available(self) -> bool:
        """Check if tmux is available for Task-based agents.

        Returns:
            True if tmux is available, False otherwise
        """
        try:
            result = subprocess.run(
                ["tmux", "list-sessions"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 or "no server running" in result.stderr.lower()
        except Exception:
            return False

    def _generate_agent_command(self, agent: str, task: str) -> str:
        """Generate the command to execute for an agent.

        Args:
            agent: Agent identifier
            task: Task description

        Returns:
            Command string to execute
        """
        if agent == "opencode":
            return f"opencode run '{task}'"
        elif agent == "codex":
            return f"codex exec '{task}'"
        elif agent == "gemini":
            return f"gemini -y '{task}'"
        elif agent == "cursor":
            return f"cursor '{task}'"
        elif agent == "amp":
            return f"amp '{task}'"
        elif agent.startswith("Task("):
            # Extract subagent type from Task(subagent_type=xxx)
            subagent_type = agent.replace("Task(subagent_type=", "").replace(")", "")
            return f"Task(subagent_type={subagent_type}, prompt='{task}')"
        else:
            # Default to opencode
            return f"opencode run '{task}'"

    def _build_reasoning(
        self,
        task_type: TaskType,
        primary_agent: str,
        fallback_agent: str | None,
        total_available: int,
    ) -> str:
        """Build reasoning string for agent selection.

        Args:
            task_type: Classified task type
            primary_agent: Selected primary agent
            fallback_agent: Available fallback agent
            total_available: Total number of available agents

        Returns:
            Human-readable reasoning string
        """
        reasoning_parts = [
            f"Task classified as '{task_type}' based on keyword analysis. ",
            f"Selected {primary_agent} as primary agent from {total_available} available options.",
        ]

        if fallback_agent:
            reasoning_parts.append(f" {fallback_agent} available as fallback.")

        # Add agent-specific reasoning
        if primary_agent in ["opencode", "codex"]:
            reasoning_parts.append(
                " CLI-based code execution tool selected for optimal performance."
            )
        elif primary_agent == "gemini":
            reasoning_parts.append(" Research-focused AI selected for analysis and investigation.")
        elif primary_agent == "cursor":
            reasoning_parts.append(" IDE-integrated agent selected for complex frontend work.")
        elif primary_agent.startswith("Task("):
            reasoning_parts.append(" Specialized Task agent selected for domain expertise.")

        return "".join(reasoning_parts)

    def _calculate_confidence(self, task_type: TaskType, available_count: int) -> float:
        """Calculate confidence score for the selection.

        Args:
            task_type: Classified task type
            available_count: Number of available agents

        Returns:
            Confidence score between 0.0 and 1.0
        """
        # Base confidence by task type clarity
        if task_type == TaskType.UNKNOWN:
            base_confidence = 0.3
        else:
            base_confidence = 0.7

        # Adjust based on availability
        if available_count >= 2:
            availability_bonus = 0.2
        elif available_count == 1:
            availability_bonus = 0.1
        else:
            availability_bonus = -0.3

        confidence = base_confidence + availability_bonus
        return min(max(confidence, 0.0), 1.0)

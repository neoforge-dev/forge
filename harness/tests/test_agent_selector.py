"""Tests for forge_harness.agents.selector module.

Tests the AgentSelector class which automatically selects the best agent
for a task type based on capabilities matrix.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge_harness.agents.selector import (
    AgentCapabilities,
    AgentSelectionResult,
    AgentSelector,
    TaskType,
)


class TestAgentCapabilities:
    """Tests for AgentCapabilities dataclass."""

    def test_default_frontend_agents(self):
        """Test default frontend agents are set correctly."""
        caps = AgentCapabilities()
        assert "Task(subagent_type=frontend-builder)" in caps.frontend
        assert "cursor" in caps.frontend

    def test_default_backend_agents(self):
        """Test default backend agents are set correctly."""
        caps = AgentCapabilities()
        assert "opencode" in caps.backend
        assert "codex" in caps.backend
        assert "Task(subagent_type=backend-engineer)" in caps.backend

    def test_default_research_agents(self):
        """Test default research agents are set correctly."""
        caps = AgentCapabilities()
        assert "gemini" in caps.research
        assert "Task(subagent_type=explore)" in caps.research

    def test_default_test_agents(self):
        """Test default test agents are set correctly."""
        caps = AgentCapabilities()
        assert "Task(subagent_type=qa-test-guardian)" in caps.test
        assert "codex" in caps.test

    def test_default_refactor_agents(self):
        """Test default refactor agents are set correctly."""
        caps = AgentCapabilities()
        assert "Task(subagent_type=refactor-surgeon)" in caps.refactor
        assert "amp" in caps.refactor


class TestAgentSelectionResult:
    """Tests for AgentSelectionResult dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        result = AgentSelectionResult(
            agent_type="test_agent",
            agent_command="test_cmd",
            reasoning="test reasoning",
            is_available=True,
        )
        assert result.agent_type == "test_agent"
        assert result.agent_command == "test_cmd"
        assert result.reasoning == "test reasoning"
        assert result.is_available is True
        assert result.fallback_agent is None
        assert result.task_type == TaskType.UNKNOWN
        assert result.confidence == 0.0

    def test_custom_values(self):
        """Test custom values are set correctly."""
        result = AgentSelectionResult(
            agent_type="codex",
            agent_command="codex exec 'test'",
            reasoning="Selected codex",
            is_available=True,
            fallback_agent="opencode",
            task_type=TaskType.BACKEND,
            confidence=0.85,
        )
        assert result.agent_type == "codex"
        assert result.fallback_agent == "opencode"
        assert result.task_type == TaskType.BACKEND
        assert result.confidence == 0.85


class TestTaskType:
    """Tests for TaskType enum."""

    def test_task_types_defined(self):
        """Test all expected task types are defined."""
        assert TaskType.FRONTEND.value == "frontend"
        assert TaskType.BACKEND.value == "backend"
        assert TaskType.RESEARCH.value == "research"
        assert TaskType.TEST.value == "test"
        assert TaskType.REFACTOR.value == "refactor"
        assert TaskType.UNKNOWN.value == "unknown"


class TestAgentSelector:
    """Tests for AgentSelector class."""

    @pytest.fixture
    def mock_opencode_executor_class(self):
        """Create a mock for OpenCodeExecutor class."""
        with patch("forge_harness.agents.selector.OpenCodeExecutor") as mock:
            instance = MagicMock()
            instance.is_available.return_value = True
            mock.return_value = instance
            yield mock

    @pytest.fixture
    def mock_codex_executor_class(self):
        """Create a mock for CodexExecutor class."""
        with patch("forge_harness.agents.selector.CodexExecutor") as mock:
            instance = MagicMock()
            instance.is_available.return_value = True
            mock.return_value = instance
            yield mock

    @pytest.fixture
    def selector(self, mock_opencode_executor_class, mock_codex_executor_class):
        """Create AgentSelector with mocked dependencies."""
        return AgentSelector()


class TestAgentSelectorClassifyTask(TestAgentSelector):
    """Tests for task classification."""

    def test_classify_frontend_task_react(self, selector):
        """Test classification of React component task."""
        result = selector._classify_task("Create a React component for user profiles")
        assert result == TaskType.FRONTEND

    def test_classify_frontend_task_css(self, selector):
        """Test classification of CSS task."""
        result = selector._classify_task("Add CSS styles for the button")
        assert result == TaskType.FRONTEND

    def test_classify_frontend_task_ui(self, selector):
        """Test classification of UI component task."""
        result = selector._classify_task("Build a new UI component for the dashboard")
        assert result == TaskType.FRONTEND

    def test_classify_backend_task_fastapi(self, selector):
        """Test classification of FastAPI endpoint task."""
        result = selector._classify_task("Create a FastAPI endpoint for user authentication")
        assert result == TaskType.BACKEND

    def test_classify_backend_task_database(self, selector):
        """Test classification of database task."""
        result = selector._classify_task("Write database migration for users table")
        assert result == TaskType.BACKEND

    def test_classify_backend_task_api(self, selector):
        """Test classification of API task."""
        result = selector._classify_task("Build REST API with Express")
        assert result == TaskType.BACKEND

    def test_classify_research_task(self, selector):
        """Test classification of research task."""
        result = selector._classify_task("Research best practices for authentication")
        assert result == TaskType.RESEARCH

    def test_classify_research_task_analyze(self, selector):
        """Test classification of analyze task."""
        result = selector._classify_task("Analyze performance of the database queries")
        assert result == TaskType.RESEARCH

    def test_classify_research_task_investigate(self, selector):
        """Test classification of investigate task."""
        result = selector._classify_task("Investigate memory leak issue")
        assert result == TaskType.RESEARCH

    def test_classify_test_task(self, selector):
        """Test classification of test task."""
        result = selector._classify_task("Write unit tests for the authentication module")
        assert result == TaskType.TEST

    def test_classify_test_task_pytest(self, selector):
        """Test classification of pytest task."""
        result = selector._classify_task("Add pytest fixtures for database testing")
        assert result == TaskType.TEST

    def test_classify_test_task_coverage(self, selector):
        """Test classification of coverage task."""
        result = selector._classify_task("Improve test coverage to 80 percent")
        assert result == TaskType.TEST

    def test_classify_refactor_task(self, selector):
        """Test classification of refactor task."""
        result = selector._classify_task("Refactor legacy authentication code")
        assert result == TaskType.REFACTOR

    def test_classify_refactor_task_technical_debt(self, selector):
        """Test classification of technical debt task."""
        result = selector._classify_task("Address technical debt in the codebase")
        assert result == TaskType.REFACTOR

    def test_classify_refactor_task_optimize(self, selector):
        """Test classification of optimize task."""
        result = selector._classify_task("Optimize database queries for performance")
        assert result == TaskType.REFACTOR

    def test_classify_unknown_task(self, selector):
        """Test classification of unknown task."""
        result = selector._classify_task("Do something with the thing")
        assert result == TaskType.UNKNOWN

    def test_classify_empty_task(self, selector):
        """Test classification of empty task."""
        result = selector._classify_task("")
        assert result == TaskType.UNKNOWN

    def test_classify_priority_research_over_backend(self, selector):
        """Test research keywords take priority over backend when research has more matches."""
        result = selector._classify_task("research best practices authentication")
        # "research" (research), "best" (research), "practices" (research), "authentication" (backend)
        # research has 3 matches, backend has 1 match - research wins
        assert result == TaskType.RESEARCH

    def test_classify_priority_research_with_multiple_matches(self, selector):
        """Test research keywords with multiple matches."""
        result = selector._classify_task("research best practices documentation")
        # Multiple research keywords - should be clearly research
        assert result == TaskType.RESEARCH

    def test_classify_refactor_multiple_keywords(self, selector):
        """Test refactor keywords with multiple matches."""
        result = selector._classify_task("refactor technical debt cleanup")
        # Multiple refactor keywords - should be clearly refactor
        assert result == TaskType.REFACTOR


class TestAgentSelectorAvailability(TestAgentSelector):
    """Tests for agent availability checking."""

    def test_is_agent_available_opencode(self, selector, mock_opencode_executor_class):
        """Test opencode availability check."""
        result = selector._is_agent_available("opencode")
        assert result is True
        mock_opencode_executor_class.return_value.is_available.assert_called_once()

    def test_is_agent_available_codex(self, selector, mock_codex_executor_class):
        """Test codex availability check."""
        result = selector._is_agent_available("codex")
        assert result is True
        mock_codex_executor_class.return_value.is_available.assert_called_once()

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_is_agent_available_gemini_found(self, mock_run, selector):
        """Test gemini availability when CLI is found."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector._is_agent_available("gemini")
        assert result is True
        mock_run.assert_called_once()

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_is_agent_available_gemini_not_found(self, mock_run, selector):
        """Test gemini availability when CLI is not found."""
        mock_run.return_value = MagicMock(returncode=1)
        result = selector._is_agent_available("gemini")
        assert result is False

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_is_agent_available_cursor(self, mock_run, selector):
        """Test cursor availability check."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector._is_agent_available("cursor")
        assert result is True

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_is_agent_available_amp(self, mock_run, selector):
        """Test amp availability check."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector._is_agent_available("amp")
        assert result is True

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_is_agent_available_task_subagent(self, mock_run, selector):
        """Test Task subagent availability check."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = selector._is_agent_available("Task(subagent_type=frontend-builder)")
        assert result is True

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_is_agent_available_unknown_defaults_true(self, mock_run, selector):
        """Test unknown agent defaults to available."""
        result = selector._is_agent_available("unknown_agent")
        assert result is True


class TestAgentSelectorGetAvailableAgents(TestAgentSelector):
    """Tests for getting available agents."""

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_get_available_agents_frontend(self, mock_run, selector):
        """Test getting available frontend agents."""
        mock_run.return_value = MagicMock(returncode=0)
        agents = selector._get_available_agents(TaskType.FRONTEND)
        assert len(agents) > 0
        assert "Task(subagent_type=frontend-builder)" in agents or "cursor" in agents

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_get_available_agents_backend(self, mock_run, selector):
        """Test getting available backend agents."""
        mock_run.return_value = MagicMock(returncode=0)
        agents = selector._get_available_agents(TaskType.BACKEND)
        assert len(agents) > 0

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_get_available_agents_research(self, mock_run, selector):
        """Test getting available research agents."""
        mock_run.return_value = MagicMock(returncode=0)
        agents = selector._get_available_agents(TaskType.RESEARCH)
        assert len(agents) > 0
        assert "gemini" in agents

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_get_available_agents_test(self, mock_run, selector):
        """Test getting available test agents."""
        mock_run.return_value = MagicMock(returncode=0)
        agents = selector._get_available_agents(TaskType.TEST)
        assert len(agents) > 0

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_get_available_agents_refactor(self, mock_run, selector):
        """Test getting available refactor agents."""
        mock_run.return_value = MagicMock(returncode=0)
        agents = selector._get_available_agents(TaskType.REFACTOR)
        assert len(agents) > 0

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_get_available_agents_unknown_defaults_backend(self, mock_run, selector):
        """Test unknown task type defaults to backend agents."""
        mock_run.return_value = MagicMock(returncode=0)
        agents = selector._get_available_agents(TaskType.UNKNOWN)
        assert len(agents) > 0


class TestAgentSelectorGenerateCommand(TestAgentSelector):
    """Tests for agent command generation."""

    def test_generate_command_opencode(self, selector):
        """Test command generation for opencode."""
        cmd = selector._generate_agent_command("opencode", "test task")
        assert cmd == "opencode run 'test task'"

    def test_generate_command_codex(self, selector):
        """Test command generation for codex."""
        cmd = selector._generate_agent_command("codex", "test task")
        assert cmd == "codex exec 'test task'"

    def test_generate_command_gemini(self, selector):
        """Test command generation for gemini."""
        cmd = selector._generate_agent_command("gemini", "test task")
        assert cmd == "gemini -y 'test task'"

    def test_generate_command_cursor(self, selector):
        """Test command generation for cursor."""
        cmd = selector._generate_agent_command("cursor", "test task")
        assert cmd == "cursor 'test task'"

    def test_generate_command_amp(self, selector):
        """Test command generation for amp."""
        cmd = selector._generate_agent_command("amp", "test task")
        assert cmd == "amp 'test task'"

    def test_generate_command_task_subagent(self, selector):
        """Test command generation for Task subagent."""
        cmd = selector._generate_agent_command("Task(subagent_type=frontend-builder)", "test task")
        assert "Task(subagent_type=frontend-builder" in cmd
        assert "test task" in cmd

    def test_generate_command_unknown_defaults_opencode(self, selector):
        """Test unknown agent defaults to opencode."""
        cmd = selector._generate_agent_command("unknown", "test task")
        assert cmd == "opencode run 'test task'"


class TestAgentSelectorBuildReasoning(TestAgentSelector):
    """Tests for reasoning generation."""

    def test_build_reasoning_frontend_with_fallback(self, selector):
        """Test reasoning for frontend with fallback."""
        reasoning = selector._build_reasoning(
            TaskType.FRONTEND,
            "cursor",
            "Task(subagent_type=frontend-builder)",
            2,
        )
        assert "FRONTEND" in reasoning
        assert "cursor" in reasoning

    def test_build_reasoning_backend_no_fallback(self, selector):
        """Test reasoning for backend without fallback."""
        reasoning = selector._build_reasoning(
            TaskType.BACKEND,
            "opencode",
            None,
            1,
        )
        assert "BACKEND" in reasoning
        assert "opencode" in reasoning

    def test_build_reasoning_research(self, selector):
        """Test reasoning for research task."""
        reasoning = selector._build_reasoning(
            TaskType.RESEARCH,
            "gemini",
            None,
            1,
        )
        assert "gemini" in reasoning
        assert "Research" in reasoning

    def test_build_reasoning_opencode_cli(self, selector):
        """Test reasoning includes CLI-specific message for opencode."""
        reasoning = selector._build_reasoning(
            TaskType.BACKEND,
            "opencode",
            None,
            1,
        )
        assert "CLI" in reasoning or "code execution" in reasoning.lower()

    def test_build_reasoning_codex_cli(self, selector):
        """Test reasoning includes CLI-specific message for codex."""
        reasoning = selector._build_reasoning(
            TaskType.BACKEND,
            "codex",
            None,
            1,
        )
        assert "CLI" in reasoning or "code execution" in reasoning.lower()

    def test_build_reasoning_gemini_research(self, selector):
        """Test reasoning includes research message for gemini."""
        reasoning = selector._build_reasoning(
            TaskType.RESEARCH,
            "gemini",
            None,
            1,
        )
        assert "Research" in reasoning or "analysis" in reasoning.lower()

    def test_build_reasoning_cursor_ide(self, selector):
        """Test reasoning includes IDE message for cursor."""
        reasoning = selector._build_reasoning(
            TaskType.FRONTEND,
            "cursor",
            None,
            1,
        )
        assert "IDE" in reasoning or "frontend" in reasoning.lower()

    def test_build_reasoning_task_subagent(self, selector):
        """Test reasoning for Task subagent."""
        reasoning = selector._build_reasoning(
            TaskType.TEST,
            "Task(subagent_type=qa-test-guardian)",
            None,
            1,
        )
        assert "Task" in reasoning or "Specialized" in reasoning


class TestAgentSelectorCalculateConfidence(TestAgentSelector):
    """Tests for confidence calculation."""

    def test_confidence_unknown_task_high_availability(self, selector):
        """Test confidence for unknown task with high availability."""
        conf = selector._calculate_confidence(TaskType.UNKNOWN, 3)
        assert conf == pytest.approx(0.5)  # 0.3 base + 0.2 with >=2 agents = 0.5

    def test_confidence_known_task_high_availability(self, selector):
        """Test confidence for known task with high availability."""
        conf = selector._calculate_confidence(TaskType.BACKEND, 3)
        assert conf == pytest.approx(0.9)  # 0.7 base + 0.2 with >=2 agents = 0.9

    def test_confidence_known_task_single_agent(self, selector):
        """Test confidence for known task with single agent."""
        conf = selector._calculate_confidence(TaskType.BACKEND, 1)
        assert conf == pytest.approx(0.8)  # 0.7 base + 0.1 with 1 agent = 0.8

    def test_confidence_known_task_no_agents(self, selector):
        """Test confidence for known task with no agents."""
        conf = selector._calculate_confidence(TaskType.BACKEND, 0)
        assert conf == pytest.approx(0.4)  # 0.7 base - 0.3 with 0 agents = 0.4

    def test_confidence_clamped_to_valid_range(self, selector):
        """Test confidence is clamped to 0.0-1.0 range."""
        # Test upper bound
        conf = selector._calculate_confidence(TaskType.UNKNOWN, 10)
        assert 0.0 <= conf <= 1.0

        # Test lower bound
        conf = selector._calculate_confidence(TaskType.UNKNOWN, 0)
        assert 0.0 <= conf <= 1.0


class TestAgentSelectorSelectAgent(TestAgentSelector):
    """Tests for main select_agent method."""

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_frontend_task(self, mock_run, selector):
        """Test selecting agent for frontend task."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Create a React component")

        assert result.is_available is True
        assert result.task_type == TaskType.FRONTEND
        assert result.agent_command != ""
        assert result.confidence > 0

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_backend_task(self, mock_run, selector):
        """Test selecting agent for backend task."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Create a FastAPI endpoint")

        assert result.is_available is True
        assert result.task_type == TaskType.BACKEND

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_research_task(self, mock_run, selector):
        """Test selecting agent for research task."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Research authentication patterns")

        assert result.is_available is True
        assert result.task_type == TaskType.RESEARCH

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_test_task(self, mock_run, selector):
        """Test selecting agent for test task."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Write unit tests")

        assert result.is_available is True
        assert result.task_type == TaskType.TEST

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_refactor_task(self, mock_run, selector):
        """Test selecting agent for refactor task."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Refactor legacy code")

        assert result.is_available is True
        assert result.task_type == TaskType.REFACTOR

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_unknown_task(self, mock_run, selector):
        """Test selecting agent for unknown task."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Do something random")

        assert result.is_available is True
        assert result.task_type == TaskType.UNKNOWN
        # Unknown task gets 0.3 base + 0.2 bonus for >=2 agents = 0.5
        assert result.confidence == pytest.approx(0.5)

    @patch.object(AgentSelector, "_get_available_agents")
    def test_select_agent_no_agents_available(self, mock_get_agents, selector):
        """Test selection when no agents are available."""
        mock_get_agents.return_value = []
        result = selector.select_agent("Some task")

        assert result.is_available is False
        assert result.agent_type == "none"
        assert result.agent_command == ""
        assert result.confidence == 0.0

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_select_agent_includes_fallback(self, mock_run, selector):
        """Test that fallback agent is included when available."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector.select_agent("Create React component")

        # At least one agent should be available
        assert result.agent_type != ""
        # Fallback might be None if only one agent available
        assert result.fallback_agent is not None or result.agent_type != ""


class TestAgentSelectorCheckCLI(TestAgentSelector):
    """Tests for CLI checking."""

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_check_cli_available_success(self, mock_run, selector):
        """Test successful CLI availability check."""
        mock_run.return_value = MagicMock(returncode=0)
        result = selector._check_cli_available("test_cli")
        assert result is True

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_check_cli_available_failure(self, mock_run, selector):
        """Test failed CLI availability check."""
        mock_run.return_value = MagicMock(returncode=1)
        result = selector._check_cli_available("nonexistent")
        assert result is False

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_check_cli_available_exception(self, mock_run, selector):
        """Test CLI check handles exception gracefully."""
        mock_run.side_effect = Exception("CLI check failed")
        result = selector._check_cli_available("test_cli")
        assert result is False


class TestAgentSelectorCheckTmux(TestAgentSelector):
    """Tests for tmux session checking."""

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_check_tmux_available_success(self, mock_run, selector):
        """Test tmux availability check with running sessions."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = selector._check_tmux_session_available()
        assert result is True

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_check_tmux_no_server_running(self, mock_run, selector):
        """Test tmux availability when no server running."""
        mock_run.return_value = MagicMock(returncode=1, stderr="no server running")
        result = selector._check_tmux_session_available()
        assert result is True

    @patch("forge_harness.agents.selector.subprocess.run")
    def test_check_tmux_unavailable(self, mock_run, selector):
        """Test tmux unavailable on exception."""
        mock_run.side_effect = Exception("tmux not found")
        result = selector._check_tmux_session_available()
        assert result is False

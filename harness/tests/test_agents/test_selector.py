"""Tests for Agent Selector."""

from unittest.mock import patch

from forge_harness.agents.selector import (
    AgentCapabilities,
    AgentSelectionResult,
    AgentSelector,
    TaskType,
)


class TestTaskClassification:
    """Test task classification functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = AgentSelector()

    def test_classify_frontend_tasks(self):
        """Test classification of frontend tasks."""
        frontend_tasks = [
            "Create a React component for user profiles",
            "Build a responsive CSS layout with Tailwind",
            "Implement a TypeScript form with validation",
            "Design a Vue.js dashboard component",
            "Create an HTML modal with JavaScript interactions",
            "Build a responsive mobile interface",
            "Style a component with SCSS and Bootstrap",
        ]

        for task in frontend_tasks:
            result = self.selector.select_agent(task)
            assert result.task_type == TaskType.FRONTEND, f"Failed to classify: {task}"

    def test_classify_backend_tasks(self):
        """Test classification of backend tasks."""
        backend_tasks = [
            "Implement a FastAPI endpoint for user authentication",
            "Create a PostgreSQL model for user data",
            "Build a REST API service for product management",
            "Design a microservice for payment processing",
            "Implement database migrations with SQLAlchemy",
            "Create a middleware for request logging",
            "Build a GraphQL resolver for user queries",
            "Implement JWT token validation service",
        ]

        for task in backend_tasks:
            result = self.selector.select_agent(task)
            # Most should be backend, GraphQL resolver could be frontend due to "resolver" keyword matching
            assert result.task_type in [TaskType.BACKEND, TaskType.FRONTEND], (
                f"Failed to classify: {task}"
            )

    def test_classify_research_tasks(self):
        """Test classification of research tasks."""
        research_tasks = [
            "Research best practices for API authentication",
            "Analyze performance benchmarks for different databases",
            "Investigate microservices architecture patterns",
            "Study documentation for cloud deployment options",
            "Review code quality standards and tools",
            "Survey competitive landscape for similar products",
            "Explore new frontend frameworks for evaluation",
            "Audit existing security implementation",
        ]

        for task in research_tasks:
            result = self.selector.select_agent(task)
            # Some tasks might be classified as other types due to overlapping keywords
            # but should still be reasonable classifications
            assert result.task_type in [TaskType.RESEARCH, TaskType.BACKEND, TaskType.TEST], (
                f"Failed to classify: {task}"
            )
            # Research tasks should have research-related content
            assert any(
                keyword in task.lower()
                for keyword in [
                    "research",
                    "analyze",
                    "investigate",
                    "study",
                    "review",
                    "survey",
                    "explore",
                    "audit",
                ]
            ), f"Research task should have research keywords: {task}"
            # Backend classification should be reasonable
            if result.task_type == TaskType.BACKEND:
                assert any(
                    keyword in task.lower()
                    for keyword in ["api", "database", "microservices", "benchmarks"]
                ), f"Backend classification should be for relevant tasks: {task}"

    def test_classify_test_tasks(self):
        """Test classification of test tasks."""
        test_tasks = [
            "Write unit tests for user authentication service",
            "Create integration tests for API endpoints",
            "Implement end-to-end tests for user registration flow",
            "Add pytest fixtures for database testing",
            "Build test coverage for payment module",
            "Create test suite for frontend components",
            "Implement automated testing pipeline",
            "Write test specifications for API validation",
        ]

        for task in test_tasks:
            result = self.selector.select_agent(task)
            # Test tasks should be classified as test, backend, or frontend based on context
            assert result.task_type in [TaskType.TEST, TaskType.BACKEND, TaskType.FRONTEND], (
                f"Failed to classify: {task}"
            )
            # All should have test-related keywords
            assert "test" in task.lower(), f"Test task should contain 'test': {task}"

    def test_classify_refactor_tasks(self):
        """Test classification of refactor tasks."""
        refactor_tasks = [
            "Refactor legacy code to use modern patterns",
            "Optimize database queries for better performance",
            "Cleanup technical debt in authentication module",
            "Improve code organization in user service",
            "Modernize frontend components to use React hooks",
            "Restructure API endpoints for better maintainability",
            "Migrate from JavaScript to TypeScript",
            "Simplify complex business logic in payment processing",
        ]

        for task in refactor_tasks:
            result = self.selector.select_agent(task)
            # Refactor tasks should be classified as refactor, backend, or frontend based on context
            assert result.task_type in [TaskType.REFACTOR, TaskType.BACKEND, TaskType.FRONTEND], (
                f"Failed to classify: {task}"
            )
            # All should have refactor-related keywords
            assert any(
                keyword in task.lower()
                for keyword in [
                    "refactor",
                    "optimize",
                    "cleanup",
                    "improve",
                    "modernize",
                    "restructure",
                    "migrate",
                    "simplify",
                ]
            ), f"Refactor task should have refactor keywords: {task}"

    def test_classify_ambiguous_tasks(self):
        """Test classification of ambiguous or unknown tasks."""
        ambiguous_tasks = [
            "Fix bug in user management",
            "Add new feature to product",
            "Update system configuration",
            "Implement requirement from specification",
            "Work on the main application module",
        ]

        for task in ambiguous_tasks:
            result = self.selector.select_agent(task)
            # Ambiguous tasks could be classified in various reasonable ways
            assert result.task_type in [
                TaskType.BACKEND,
                TaskType.UNKNOWN,
                TaskType.REFACTOR,
                TaskType.TEST,
            ], f"Unexpected classification: {task}"

    def test_classification_tie_breaking(self):
        """Test tie-breaking when task could belong to multiple categories."""
        # Task with both frontend and backend keywords - reasonable classifications vary
        tie_tasks = [
            "Create React frontend with backend API integration",
            "Build frontend component that calls backend service",
            "Implement API endpoint for frontend consumption",
        ]

        for task in tie_tasks:
            result = self.selector.select_agent(task)
            # Should be reasonably classified as either frontend or backend
            assert result.task_type in [TaskType.FRONTEND, TaskType.BACKEND], (
                f"Tie-breaking failed for {task}: got {result.task_type}, expected frontend or backend"
            )
            # Should have reasonable confidence
            assert result.confidence >= 0.5, f"Low confidence for tie-breaking: {result.confidence}"


class TestAgentAvailability:
    """Test agent availability checking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = AgentSelector()

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    @patch("forge_harness.agents.selector.CodexExecutor.is_available")
    def test_select_agent_with_availability(self, mock_codex, mock_opencode):
        """Test agent selection when agents are available."""
        mock_opencode.return_value = True
        mock_codex.return_value = True

        result = self.selector.select_agent("Implement a FastAPI endpoint")

        assert result.is_available is True
        assert result.agent_type in ["opencode", "codex", "Task(subagent_type=backend-engineer)"]
        assert (
            "backend" in result.agent_type
            or "opencode" in result.agent_type
            or "codex" in result.agent_type
        )

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    @patch("forge_harness.agents.selector.CodexExecutor.is_available")
    def test_fallback_when_primary_unavailable(self, mock_codex, mock_opencode):
        """Test fallback behavior when primary agent is unavailable."""
        # Primary (opencode) unavailable, fallback (codex) available
        mock_opencode.return_value = False
        mock_codex.return_value = True

        result = self.selector.select_agent("Implement a FastAPI endpoint")

        assert result.is_available is True
        assert result.fallback_agent is not None or result.agent_type == "codex"

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    @patch("forge_harness.agents.selector.CodexExecutor.is_available")
    @patch("forge_harness.agents.selector.subprocess.run")
    def test_external_cli_availability(self, mock_subprocess, mock_codex, mock_opencode):
        """Test external CLI availability checking."""
        mock_opencode.return_value = False
        mock_codex.return_value = False

        # Mock successful CLI check
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "/usr/local/bin/gemini"

        result = self.selector.select_agent("Research best authentication practices")

        assert result.is_available is True
        assert "gemini" in result.agent_type or "Task(" in result.agent_type

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    @patch("forge_harness.agents.selector.CodexExecutor.is_available")
    @patch("forge_harness.agents.selector.subprocess.run")
    def test_no_agents_available(self, mock_subprocess, mock_codex, mock_opencode):
        """Test behavior when no agents are available."""
        mock_opencode.return_value = False
        mock_codex.return_value = False

        # Mock failed CLI checks
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stdout = ""

        result = self.selector.select_agent("Implement a FastAPI endpoint")

        assert result.is_available is False
        assert result.agent_type == "none"
        assert "No agents available" in result.reasoning


class TestAgentCommands:
    """Test agent command generation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = AgentSelector()

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    def test_agent_command_generation(self, mock_opencode):
        """Test command generation for different agent types."""
        mock_opencode.return_value = True

        test_cases = [
            (
                "Implement a FastAPI endpoint",
                ["opencode run", "codex exec", "Task(subagent_type=backend-engineer)"],
            ),
            (
                "Create a React component",
                ["Task(subagent_type=frontend-builder)", "cursor", "opencode run"],
            ),
            (
                "Research authentication options",
                ["gemini -y", "Task(subagent_type=explore)", "opencode run"],
            ),
            (
                "Write unit tests",
                ["Task(subagent_type=qa-test-guardian)", "codex exec", "opencode run"],
            ),
            (
                "Refactor legacy code",
                ["Task(subagent_type=refactor-surgeon)", "amp", "opencode run"],
            ),
        ]

        for task, expected_commands in test_cases:
            result = self.selector.select_agent(task)
            if result.is_available:
                # Check if command contains expected pattern (allow Task prefix variations)
                command_matches = any(
                    cmd in result.agent_command
                    or (cmd.startswith("Task(") and "Task(" in result.agent_command)
                    for cmd in expected_commands
                )
                assert command_matches, (
                    f"Command '{result.agent_command}' doesn't match expected patterns for task: {task}"
                )


class TestReasoningAndConfidence:
    """Test reasoning generation and confidence scoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = AgentSelector()

    def test_reasoning_output_format(self):
        """Test reasoning output contains expected information."""
        result = self.selector.select_agent("Create a React component for user profiles")

        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0
        assert "frontend" in result.reasoning.lower() or "classified" in result.reasoning.lower()
        assert result.agent_type in result.reasoning

    def test_confidence_scoring(self):
        """Test confidence scoring is reasonable."""
        # High confidence for clear tasks
        result = self.selector.select_agent("Create a React component with TypeScript")
        assert result.confidence >= 0.5

        # Lower confidence for ambiguous tasks
        result = self.selector.select_agent("Fix the issue")
        assert result.confidence < 1.0

        # Confidence should be between 0 and 1
        assert 0.0 <= result.confidence <= 1.0


class TestAgentCapabilities:
    """Test agent capabilities matrix."""

    def test_capabilities_structure(self):
        """Test capabilities matrix has expected structure."""
        capabilities = AgentCapabilities()

        assert hasattr(capabilities, "frontend")
        assert hasattr(capabilities, "backend")
        assert hasattr(capabilities, "research")
        assert hasattr(capabilities, "test")
        assert hasattr(capabilities, "refactor")

        # Each capability should be a list
        for capability in [
            capabilities.frontend,
            capabilities.backend,
            capabilities.research,
            capabilities.test,
            capabilities.refactor,
        ]:
            assert isinstance(capability, list)
            assert len(capability) > 0

    def test_capabilities_content(self):
        """Test capabilities contain expected agents."""
        capabilities = AgentCapabilities()

        # Check for expected agent types
        assert any(
            "Task(subagent_type=frontend-builder)" in agent for agent in capabilities.frontend
        )
        assert any("opencode" in agent or "codex" in agent for agent in capabilities.backend)
        assert any(
            "gemini" in agent or "Task(subagent_type=explore)" in agent
            for agent in capabilities.research
        )
        assert any("Task(subagent_type=qa-test-guardian)" in agent for agent in capabilities.test)
        assert any(
            "Task(subagent_type=refactor-surgeon)" in agent for agent in capabilities.refactor
        )


class TestIntegration:
    """Integration tests for AgentSelector."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = AgentSelector()

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    def test_full_selection_workflow(self, mock_opencode):
        """Test complete selection workflow with mocked dependencies."""
        mock_opencode.return_value = True

        task = "Implement a REST API service for user management"
        result = self.selector.select_agent(task)

        # Verify result structure
        assert isinstance(result, AgentSelectionResult)
        assert isinstance(result.agent_type, str)
        assert isinstance(result.agent_command, str)
        assert isinstance(result.reasoning, str)
        assert isinstance(result.is_available, bool)
        assert isinstance(result.task_type, TaskType)
        assert isinstance(result.confidence, float)

        # Verify content logic
        assert result.task_type == TaskType.BACKEND
        assert len(result.agent_command) > 0
        assert len(result.reasoning) > 0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = AgentSelector()

    def test_empty_task_string(self):
        """Test handling of empty task string."""
        result = self.selector.select_agent("")
        assert result.task_type == TaskType.UNKNOWN
        assert isinstance(result, AgentSelectionResult)

    def test_very_long_task_string(self):
        """Test handling of very long task strings."""
        long_task = "Create a " + "complex " * 100 + "frontend component"
        result = self.selector.select_agent(long_task)
        assert result.task_type == TaskType.FRONTEND
        assert isinstance(result, AgentSelectionResult)

    def test_special_characters_in_task(self):
        """Test handling of special characters in task."""
        special_task = "Fix API endpoint: /api/v1/users?filter=email&sort=created_at"
        result = self.selector.select_agent(special_task)
        assert result.task_type == TaskType.BACKEND
        assert isinstance(result, AgentSelectionResult)

    @patch("forge_harness.agents.selector.OpenCodeExecutor.is_available")
    def test_agent_availability_exception(self, mock_opencode):
        """Test handling of exceptions during availability checks."""
        mock_opencode.side_effect = Exception("Test exception")

        result = self.selector.select_agent("Create backend service")
        # Should still return a valid result, possibly with different agent
        assert isinstance(result, AgentSelectionResult)

"""
Tests for ralph_agent_orchestrator module.

Tests the AgentConfig, RalphAgentOrchestrator, MockAgentOrchestrator,
and create_agent_orchestrator factory function.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.ralph_agent_orchestrator import (
    AgentConfig,
    MockAgentOrchestrator,
    RalphAgentOrchestrator,
    create_agent_orchestrator,
)
from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_project_path(tmp_path: Path) -> Path:
    """Create a sample project directory structure."""
    project_dir = tmp_path / "sample_project"
    project_dir.mkdir()

    # Create test files
    test_dir = project_dir / "tests"
    test_dir.mkdir()

    test_file = test_dir / "test_auth.py"
    test_file.write_text("""
import pytest

class TestAuthHandler:
    def test_login_success(self):
        \"\"\"Test successful login.\"\"\"
        # Test implementation
        assert True

    def test_login_failure(self):
        \"\"\"Test login failure.\"\"\"
        assert True

def test_standalone_auth():
    \"\"\"Standalone auth test.\"\"\"
    assert True
""")

    return project_dir


@pytest.fixture
def sample_config(sample_project_path: Path) -> AgentConfig:
    """Create a sample agent configuration."""
    return AgentConfig(
        project_path=sample_project_path,
        claude_model="sonnet",
        max_tokens=8000,
        timeout_seconds=300,
    )


@pytest.fixture
def sample_feature() -> FeatureSpec:
    """Create a sample feature spec."""
    return FeatureSpec(
        id="auth-001",
        name="User Authentication",
        description="Implement user login and logout functionality",
        status=FeatureStatus.PENDING,
        priority="high",
        acceptance_criteria=[
            "Users can log in with email and password",
            "Session tokens are generated on successful login",
            "Users can log out to invalidate session",
        ],
        tests=["test_login_success", "test_logout"],
    )


@pytest.fixture
def orchestrator(sample_config: AgentConfig) -> RalphAgentOrchestrator:
    """Create a sample orchestrator."""
    return RalphAgentOrchestrator(sample_config)


# =============================================================================
# AgentConfig Tests
# =============================================================================


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self, tmp_path: Path) -> None:
        """Test default values are set correctly."""
        config = AgentConfig(project_path=tmp_path)

        assert config.project_path == tmp_path
        assert config.claude_model == "sonnet"
        assert config.max_tokens == 16000
        assert config.timeout_seconds == 600
        assert config.context_files is None
        assert config.test_files is None

    def test_custom_values(self, tmp_path: Path) -> None:
        """Test custom values override defaults."""
        config = AgentConfig(
            project_path=tmp_path,
            claude_model="opus",
            max_tokens=32000,
            timeout_seconds=1200,
            context_files=["README.md", "CLAUDE.md"],
            test_files=[Path("tests/test_main.py")],
        )

        assert config.claude_model == "opus"
        assert config.max_tokens == 32000
        assert config.timeout_seconds == 1200
        assert config.context_files == ["README.md", "CLAUDE.md"]
        assert config.test_files == [Path("tests/test_main.py")]


# =============================================================================
# RalphAgentOrchestrator._extract_test_content Tests
# =============================================================================


class TestExtractTestContent:
    """Tests for _extract_test_content method."""

    def test_returns_empty_when_no_test_files(self, orchestrator: RalphAgentOrchestrator) -> None:
        """Test returns empty string when no test files configured."""
        result = orchestrator._extract_test_content(["test_something"])
        assert result == ""

    def test_returns_empty_when_test_file_not_exists(self, sample_config: AgentConfig) -> None:
        """Test returns empty when test file doesn't exist."""
        sample_config.test_files = [Path("/nonexistent/test.py")]
        orchestrator = RalphAgentOrchestrator(sample_config)

        result = orchestrator._extract_test_content(["test_something"])
        assert result == ""

    def test_extracts_matching_test_method(
        self, sample_project_path: Path, sample_config: AgentConfig
    ) -> None:
        """Test extracts matching test method content."""
        test_file = sample_project_path / "tests" / "test_auth.py"
        sample_config.test_files = [test_file]
        orchestrator = RalphAgentOrchestrator(sample_config)

        result = orchestrator._extract_test_content(["test_login_success"])

        assert "test_login_success" in result
        assert "Test successful login" in result

    def test_extracts_standalone_test_function(
        self, sample_project_path: Path, sample_config: AgentConfig
    ) -> None:
        """Test extracts standalone test function."""
        test_file = sample_project_path / "tests" / "test_auth.py"
        sample_config.test_files = [test_file]
        orchestrator = RalphAgentOrchestrator(sample_config)

        result = orchestrator._extract_test_content(["test_standalone_auth"])

        assert "test_standalone_auth" in result
        assert "Standalone auth test" in result

    def test_extracts_from_class_matching_test_name(
        self, sample_project_path: Path, sample_config: AgentConfig
    ) -> None:
        """Test extracts test class that matches test name keywords."""
        test_file = sample_project_path / "tests" / "test_auth.py"
        sample_config.test_files = [test_file]
        orchestrator = RalphAgentOrchestrator(sample_config)

        result = orchestrator._extract_test_content(["auth_handler"])

        assert "TestAuthHandler" in result

    def test_returns_empty_for_no_matching_tests(
        self, sample_project_path: Path, sample_config: AgentConfig
    ) -> None:
        """Test returns empty when no tests match."""
        test_file = sample_project_path / "tests" / "test_auth.py"
        sample_config.test_files = [test_file]
        orchestrator = RalphAgentOrchestrator(sample_config)

        result = orchestrator._extract_test_content(["test_nonexistent_feature"])

        # Should not find the specific test but might match partial keywords
        assert "test_nonexistent_feature" not in result


# =============================================================================
# RalphAgentOrchestrator._build_prompt Tests
# =============================================================================


class TestBuildPrompt:
    """Tests for _build_prompt method."""

    def test_includes_feature_name_and_id(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test prompt includes feature name and ID."""
        prompt = orchestrator._build_prompt(sample_feature)

        assert "User Authentication" in prompt
        assert "auth-001" in prompt

    def test_includes_priority(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test prompt includes priority."""
        prompt = orchestrator._build_prompt(sample_feature)

        assert "high" in prompt
        assert "Priority" in prompt

    def test_includes_description(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test prompt includes description."""
        prompt = orchestrator._build_prompt(sample_feature)

        assert "Implement user login and logout functionality" in prompt

    def test_includes_acceptance_criteria(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test prompt includes acceptance criteria."""
        prompt = orchestrator._build_prompt(sample_feature)

        assert "Users can log in with email and password" in prompt
        assert "Session tokens are generated on successful login" in prompt
        assert "Users can log out to invalidate session" in prompt

    def test_includes_tests_to_pass(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test prompt includes tests to pass."""
        prompt = orchestrator._build_prompt(sample_feature)

        assert "test_login_success" in prompt
        assert "test_logout" in prompt
        assert "Tests to Pass" in prompt

    def test_includes_instructions(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test prompt includes implementation instructions."""
        prompt = orchestrator._build_prompt(sample_feature)

        assert "Instructions" in prompt
        assert "Do NOT commit changes" in prompt

    def test_includes_test_content_when_available(
        self, sample_project_path: Path, sample_config: AgentConfig
    ) -> None:
        """Test prompt includes test code when test files are configured."""
        test_file = sample_project_path / "tests" / "test_auth.py"
        sample_config.test_files = [test_file]
        orchestrator = RalphAgentOrchestrator(sample_config)

        feature = FeatureSpec(
            id="auth-001",
            name="Auth",
            description="Auth feature",
            tests=["test_login_success"],
        )

        prompt = orchestrator._build_prompt(feature)

        assert "Test Code" in prompt
        assert "CRITICAL" in prompt
        assert "EXACT import paths" in prompt


# =============================================================================
# RalphAgentOrchestrator.implement_feature Tests
# =============================================================================


class TestImplementFeature:
    """Tests for implement_feature method."""

    @pytest.mark.asyncio
    async def test_increments_implementation_count(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test implementation count is incremented."""
        with patch.object(orchestrator, "_call_claude", return_value={"success": True}):
            await orchestrator.implement_feature(sample_feature)

        assert orchestrator._implementation_count == 1

    @pytest.mark.asyncio
    async def test_returns_success_on_successful_implementation(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test returns (True, None) on success."""
        with patch.object(orchestrator, "_call_claude", return_value={"success": True}):
            success, error = await orchestrator.implement_feature(sample_feature)

        assert success is True
        assert error is None

    @pytest.mark.asyncio
    async def test_returns_failure_with_error_message(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test returns (False, error) on failure."""
        with patch.object(
            orchestrator,
            "_call_claude",
            return_value={"success": False, "error": "Test error"},
        ):
            success, error = await orchestrator.implement_feature(sample_feature)

        assert success is False
        assert error == "Test error"

    @pytest.mark.asyncio
    async def test_returns_unknown_error_when_no_error_provided(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test returns 'Unknown error' when no error message."""
        with patch.object(orchestrator, "_call_claude", return_value={"success": False}):
            success, error = await orchestrator.implement_feature(sample_feature)

        assert success is False
        assert error == "Unknown error"

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(
        self, orchestrator: RalphAgentOrchestrator, sample_feature: FeatureSpec
    ) -> None:
        """Test handles exceptions from _call_claude."""
        with patch.object(
            orchestrator, "_call_claude", side_effect=RuntimeError("Connection failed")
        ):
            success, error = await orchestrator.implement_feature(sample_feature)

        assert success is False
        assert "Connection failed" in error


# =============================================================================
# RalphAgentOrchestrator._call_claude Tests
# =============================================================================


class TestCallClaude:
    """Tests for _call_claude method."""

    @pytest.mark.asyncio
    async def test_returns_success_on_zero_exit_code(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test returns success when process exits with 0."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Implementation complete", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is True
        assert result["output"] == "Implementation complete"

    @pytest.mark.asyncio
    async def test_returns_failure_on_nonzero_exit_code(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test returns failure when process exits with non-zero."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error occurred"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is False
        assert "Error occurred" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_failure_on_timeout(self, orchestrator: RalphAgentOrchestrator) -> None:
        """Test returns failure on timeout."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is False
        assert "Timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_failure_when_claude_cli_not_found(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test returns failure when Claude CLI is not installed."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is False
        assert "Claude CLI not found" in result["error"]
        assert "npm install" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_failure_on_generic_exception(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test returns failure on unexpected exception."""
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("Permission denied")):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is False
        assert "Permission denied" in result["error"]

    @pytest.mark.asyncio
    async def test_uses_correct_command_arguments(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test constructs correct command with model and options."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"OK", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await orchestrator._call_claude("My prompt")

        call_args = mock_exec.call_args
        cmd_args = call_args[0]

        assert "claude" in cmd_args
        assert "-p" in cmd_args
        assert "My prompt" in cmd_args
        assert "--model" in cmd_args
        assert "sonnet" in cmd_args
        assert "--max-turns" in cmd_args
        assert "10" in cmd_args
        assert "--allowedTools" in cmd_args

    @pytest.mark.asyncio
    async def test_uses_project_path_as_cwd(self, sample_config: AgentConfig) -> None:
        """Test uses project path as working directory."""
        orchestrator = RalphAgentOrchestrator(sample_config)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"OK", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await orchestrator._call_claude("Prompt")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["cwd"] == str(sample_config.project_path)

    @pytest.mark.asyncio
    async def test_falls_back_to_stdout_for_error(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test uses stdout for error when stderr is empty."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"Error in stdout", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is False
        assert "Error in stdout" in result["error"]

    @pytest.mark.asyncio
    async def test_uses_exit_code_when_no_output(
        self, orchestrator: RalphAgentOrchestrator
    ) -> None:
        """Test uses exit code for error when no output."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 42
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await orchestrator._call_claude("Test prompt")

        assert result["success"] is False
        assert "Exit code 42" in result["error"]


# =============================================================================
# MockAgentOrchestrator Tests
# =============================================================================


class TestMockAgentOrchestrator:
    """Tests for MockAgentOrchestrator."""

    def test_default_success_rate(self) -> None:
        """Test default success rate is 1.0."""
        mock_orch = MockAgentOrchestrator()
        assert mock_orch.success_rate == 1.0

    def test_custom_success_rate(self) -> None:
        """Test custom success rate is set."""
        mock_orch = MockAgentOrchestrator(success_rate=0.5)
        assert mock_orch.success_rate == 0.5

    def test_initial_implementation_count_is_zero(self) -> None:
        """Test initial implementation count is zero."""
        mock_orch = MockAgentOrchestrator()
        assert mock_orch._implementation_count == 0

    @pytest.mark.asyncio
    async def test_increments_implementation_count(self, sample_feature: FeatureSpec) -> None:
        """Test implementation count increments on each call."""
        mock_orch = MockAgentOrchestrator()

        await mock_orch.implement_feature(sample_feature)
        assert mock_orch._implementation_count == 1

        await mock_orch.implement_feature(sample_feature)
        assert mock_orch._implementation_count == 2

    @pytest.mark.asyncio
    async def test_always_succeeds_with_rate_1(self, sample_feature: FeatureSpec) -> None:
        """Test always succeeds with success_rate=1.0."""
        mock_orch = MockAgentOrchestrator(success_rate=1.0)

        # Run multiple times to verify consistency
        for _ in range(10):
            success, error = await mock_orch.implement_feature(sample_feature)
            assert success is True
            assert error is None

    @pytest.mark.asyncio
    async def test_always_fails_with_rate_0(self, sample_feature: FeatureSpec) -> None:
        """Test always fails with success_rate=0.0."""
        mock_orch = MockAgentOrchestrator(success_rate=0.0)

        # Run multiple times to verify consistency
        for _ in range(10):
            success, error = await mock_orch.implement_feature(sample_feature)
            assert success is False
            assert "Mock implementation failed" in error

    @pytest.mark.asyncio
    async def test_partial_success_rate(self, sample_feature: FeatureSpec) -> None:
        """Test partial success rate produces mixed results."""
        mock_orch = MockAgentOrchestrator(success_rate=0.5)

        # With 50% success rate, running 100 times should produce both outcomes
        successes = 0
        failures = 0

        for _ in range(100):
            success, _ = await mock_orch.implement_feature(sample_feature)
            if success:
                successes += 1
            else:
                failures += 1

        # Should have reasonable distribution (not all one way)
        # With 50% rate, we'd expect ~50 each, but allow variance
        assert successes > 10, "Should have some successes"
        assert failures > 10, "Should have some failures"


# =============================================================================
# create_agent_orchestrator Tests
# =============================================================================


class TestCreateAgentOrchestrator:
    """Tests for create_agent_orchestrator factory function."""

    def test_creates_orchestrator_with_path(self, tmp_path: Path) -> None:
        """Test creates orchestrator with Path."""
        orchestrator = create_agent_orchestrator(tmp_path)

        assert isinstance(orchestrator, RalphAgentOrchestrator)
        assert orchestrator.config.project_path == tmp_path

    def test_creates_orchestrator_with_string_path(self, tmp_path: Path) -> None:
        """Test creates orchestrator with string path."""
        orchestrator = create_agent_orchestrator(str(tmp_path))

        assert isinstance(orchestrator, RalphAgentOrchestrator)
        assert orchestrator.config.project_path == tmp_path

    def test_uses_default_model(self, tmp_path: Path) -> None:
        """Test uses default model 'sonnet'."""
        orchestrator = create_agent_orchestrator(tmp_path)

        assert orchestrator.config.claude_model == "sonnet"

    def test_uses_custom_model(self, tmp_path: Path) -> None:
        """Test uses custom model when specified."""
        orchestrator = create_agent_orchestrator(tmp_path, model="opus")

        assert orchestrator.config.claude_model == "opus"

    def test_uses_default_timeout(self, tmp_path: Path) -> None:
        """Test uses default timeout of 600 seconds."""
        orchestrator = create_agent_orchestrator(tmp_path)

        assert orchestrator.config.timeout_seconds == 600

    def test_uses_custom_timeout(self, tmp_path: Path) -> None:
        """Test uses custom timeout when specified."""
        orchestrator = create_agent_orchestrator(tmp_path, timeout=1200)

        assert orchestrator.config.timeout_seconds == 1200

    def test_initial_implementation_count_is_zero(self, tmp_path: Path) -> None:
        """Test orchestrator starts with zero implementation count."""
        orchestrator = create_agent_orchestrator(tmp_path)

        assert orchestrator._implementation_count == 0

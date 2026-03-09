"""Tests for agent providers.

Tests the build_command() methods for all provider implementations.
"""

import pytest

from forge_harness.providers import (
    PROVIDERS,
    AgentProvider,
    AmpProvider,
    ClaudeCodeProvider,
    CursorAgentProvider,
    OpenCodeProvider,
    PiProvider,
    get_provider,
)


class TestProviderRegistry:
    """Test provider registry functionality."""

    def test_all_providers_registered(self):
        """All expected providers should be registered."""
        expected = {"claude", "amp", "cursor", "pi", "opencode"}
        assert set(PROVIDERS.keys()) == expected

    def test_get_provider_returns_instance(self):
        """get_provider should return provider instances."""
        for name in PROVIDERS:
            provider = get_provider(name)
            assert isinstance(provider, AgentProvider)
            assert provider.name == name

    def test_get_provider_unknown_raises(self):
        """get_provider should raise ValueError for unknown providers."""
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("nonexistent")

    def test_all_providers_have_required_properties(self):
        """All providers should implement required properties."""
        for name, provider_class in PROVIDERS.items():
            provider = provider_class()
            assert hasattr(provider, "name")
            assert hasattr(provider, "executable")
            assert hasattr(provider, "supports_stream_json")
            assert hasattr(provider, "supports_auto_approve")
            assert isinstance(provider.name, str)
            assert isinstance(provider.executable, str)
            assert isinstance(provider.supports_stream_json, bool)
            assert isinstance(provider.supports_auto_approve, bool)


class TestClaudeCodeProvider:
    """Test Claude Code provider."""

    @pytest.fixture
    def provider(self):
        return ClaudeCodeProvider()

    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path / "project"

    def test_name_and_executable(self, provider):
        assert provider.name == "claude"
        assert provider.executable == "claude"

    def test_supports_features(self, provider):
        assert provider.supports_stream_json is True
        assert provider.supports_auto_approve is True

    def test_basic_command(self, provider, workspace):
        """Basic command should use -p flag."""
        cmd = provider.build_command("Fix the bug", workspace)
        assert cmd == ["claude", "-p", "Fix the bug"]

    def test_command_with_auto_approve(self, provider, workspace):
        """Auto approve should add --dangerously-skip-permissions."""
        cmd = provider.build_command("Fix the bug", workspace, auto_approve=True)
        assert "--dangerously-skip-permissions" in cmd
        assert cmd.index("--dangerously-skip-permissions") < cmd.index("-p")

    def test_command_with_model(self, provider, workspace):
        """Model selection should add --model flag."""
        cmd = provider.build_command("Fix the bug", workspace, model="sonnet")
        assert "--model" in cmd
        assert "sonnet" in cmd

    def test_command_with_continue_session(self, provider, workspace):
        """Continue session should add --continue."""
        cmd = provider.build_command("Fix the bug", workspace, continue_session=True)
        assert "--continue" in cmd

    def test_command_with_session_id(self, provider, workspace):
        """Session ID should add --resume flag."""
        cmd = provider.build_command("Fix the bug", workspace, session_id="abc123")
        assert "--resume" in cmd
        assert "abc123" in cmd


class TestAmpProvider:
    """Test Amp provider."""

    @pytest.fixture
    def provider(self):
        return AmpProvider()

    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path / "project"

    def test_name_and_executable(self, provider):
        assert provider.name == "amp"
        assert provider.executable == "amp"

    def test_supports_features(self, provider):
        assert provider.supports_stream_json is True
        assert provider.supports_auto_approve is True

    def test_basic_command(self, provider, workspace):
        """Basic command should use -x flag."""
        cmd = provider.build_command("Fix the bug", workspace)
        assert cmd == ["amp", "-x", "Fix the bug"]

    def test_command_with_stream_json(self, provider, workspace):
        """Stream JSON should add --stream-json flag."""
        cmd = provider.build_command("Fix the bug", workspace, stream_json=True)
        assert "--stream-json" in cmd
        assert "-x" in cmd

    def test_command_with_stream_json_thinking(self, provider, workspace):
        """Include thinking should use --stream-json-thinking."""
        cmd = provider.build_command(
            "Fix the bug", workspace, stream_json=True, include_thinking=True
        )
        assert "--stream-json-thinking" in cmd
        assert "--stream-json" not in cmd

    def test_command_with_auto_approve(self, provider, workspace):
        """Auto approve should add --dangerously-allow-all before -x."""
        cmd = provider.build_command("Fix the bug", workspace, auto_approve=True)
        assert "--dangerously-allow-all" in cmd
        assert cmd.index("--dangerously-allow-all") < cmd.index("-x")

    def test_command_with_mode(self, provider, workspace):
        """Mode selection should add -m flag."""
        cmd = provider.build_command("Fix the bug", workspace, mode="smart")
        assert "-m" in cmd
        assert "smart" in cmd

    def test_command_with_labels(self, provider, workspace):
        """Labels should add -l flags."""
        cmd = provider.build_command(
            "Fix the bug", workspace, labels=["agent:test", "domain:forge"]
        )
        assert cmd.count("-l") == 2
        assert "agent:test" in cmd
        assert "domain:forge" in cmd

    def test_command_with_continue_session(self, provider, workspace):
        """Continue session should return threads continue command."""
        cmd = provider.build_command("Fix the bug", workspace, continue_session=True)
        assert cmd == ["amp", "threads", "continue"]


class TestCursorAgentProvider:
    """Test Cursor Agent provider."""

    @pytest.fixture
    def provider(self):
        return CursorAgentProvider()

    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path / "project"

    def test_name_and_executable(self, provider):
        assert provider.name == "cursor"
        assert provider.executable == "cursor-agent"

    def test_supports_features(self, provider):
        assert provider.supports_stream_json is True
        assert provider.supports_auto_approve is True

    def test_basic_command(self, provider, workspace):
        """Basic command should use -p and --workspace."""
        cmd = provider.build_command("Fix the bug", workspace)
        assert cmd[0] == "cursor-agent"
        assert "-p" in cmd
        assert "--workspace" in cmd
        assert str(workspace) in cmd
        assert "Fix the bug" in cmd

    def test_command_with_stream_json(self, provider, workspace):
        """Stream JSON should add --output-format stream-json."""
        cmd = provider.build_command("Fix the bug", workspace, stream_json=True)
        assert "--output-format" in cmd
        assert "stream-json" in cmd

    def test_command_with_auto_approve(self, provider, workspace):
        """Auto approve should add --approve-mcps."""
        cmd = provider.build_command("Fix the bug", workspace, auto_approve=True)
        assert "--approve-mcps" in cmd

    def test_command_with_model(self, provider, workspace):
        """Model selection should add --model flag."""
        cmd = provider.build_command("Fix the bug", workspace, model="sonnet-4")
        assert "--model" in cmd
        assert "sonnet-4" in cmd

    def test_command_with_mode(self, provider, workspace):
        """Mode selection should add --mode flag."""
        cmd = provider.build_command("Fix the bug", workspace, mode="plan")
        assert "--mode" in cmd
        assert "plan" in cmd

    def test_command_with_sandbox(self, provider, workspace):
        """Sandbox should add --sandbox flag."""
        cmd = provider.build_command("Fix the bug", workspace, sandbox="disabled")
        assert "--sandbox" in cmd
        assert "disabled" in cmd


class TestPiProvider:
    """Test Pi provider."""

    @pytest.fixture
    def provider(self):
        return PiProvider()

    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path / "project"

    def test_name_and_executable(self, provider):
        assert provider.name == "pi"
        assert provider.executable == "pi"

    def test_supports_features(self, provider):
        # Pi has partial JSON support (different format)
        assert provider.supports_stream_json is False
        assert provider.supports_auto_approve is False

    def test_basic_command(self, provider, workspace):
        """Basic command should use -p flag."""
        cmd = provider.build_command("Fix the bug", workspace)
        assert cmd == ["pi", "-p", "Fix the bug"]

    def test_command_with_json_mode(self, provider, workspace):
        """Stream JSON should add --mode json."""
        cmd = provider.build_command("Fix the bug", workspace, stream_json=True)
        assert "--mode" in cmd
        assert "json" in cmd

    def test_command_with_provider_and_model(self, provider, workspace):
        """Provider and model should be set."""
        cmd = provider.build_command(
            "Fix the bug", workspace, provider="anthropic", model="claude-sonnet-4"
        )
        assert "--provider" in cmd
        assert "anthropic" in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4" in cmd

    def test_command_with_thinking(self, provider, workspace):
        """Thinking level should add --thinking flag."""
        cmd = provider.build_command("Fix the bug", workspace, thinking="high")
        assert "--thinking" in cmd
        assert "high" in cmd

    def test_command_with_tools(self, provider, workspace):
        """Tools should add --tools flag."""
        cmd = provider.build_command("Fix the bug", workspace, tools=["read", "bash", "edit"])
        assert "--tools" in cmd
        assert "read,bash,edit" in cmd

    def test_command_with_session_dir(self, provider, workspace):
        """Session dir should add --session-dir flag."""
        cmd = provider.build_command("Fix the bug", workspace, session_dir="/tmp/sessions")
        assert "--session-dir" in cmd
        assert "/tmp/sessions" in cmd


class TestOpenCodeProvider:
    """Test OpenCode provider."""

    @pytest.fixture
    def provider(self):
        return OpenCodeProvider()

    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path / "project"

    def test_name_and_executable(self, provider):
        assert provider.name == "opencode"
        assert provider.executable == "opencode"

    def test_supports_features(self, provider):
        # OpenCode has no JSON streaming
        assert provider.supports_stream_json is False
        assert provider.supports_auto_approve is False

    def test_basic_command(self, provider, workspace):
        """Basic command should use run subcommand."""
        cmd = provider.build_command("Fix the bug", workspace)
        assert cmd == ["opencode", "run", "Fix the bug"]

    def test_command_with_model(self, provider, workspace):
        """Model should add -m flag."""
        cmd = provider.build_command("Fix the bug", workspace, model="anthropic/claude-sonnet-4")
        assert "-m" in cmd
        assert "anthropic/claude-sonnet-4" in cmd

    def test_command_with_continue_session(self, provider, workspace):
        """Continue session should add -c flag."""
        cmd = provider.build_command("Fix the bug", workspace, continue_session=True)
        assert "-c" in cmd

    def test_command_with_session_id(self, provider, workspace):
        """Session ID should add -s flag."""
        cmd = provider.build_command("Fix the bug", workspace, session_id="sess123")
        assert "-s" in cmd
        assert "sess123" in cmd

    def test_command_with_agent(self, provider, workspace):
        """Agent should add --agent flag."""
        cmd = provider.build_command("Fix the bug", workspace, agent="code-review")
        assert "--agent" in cmd
        assert "code-review" in cmd


class TestProviderCommandStructure:
    """Test that all providers return valid command structures."""

    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path / "project"

    @pytest.mark.parametrize("provider_name", list(PROVIDERS.keys()))
    def test_command_is_list_of_strings(self, provider_name, workspace):
        """All commands should be lists of strings."""
        provider = get_provider(provider_name)
        cmd = provider.build_command("Test prompt", workspace)
        assert isinstance(cmd, list)
        assert all(isinstance(arg, str) for arg in cmd)

    @pytest.mark.parametrize("provider_name", list(PROVIDERS.keys()))
    def test_command_starts_with_executable(self, provider_name, workspace):
        """Commands should start with the executable."""
        provider = get_provider(provider_name)
        cmd = provider.build_command("Test prompt", workspace)
        assert cmd[0] == provider.executable

    @pytest.mark.parametrize("provider_name", list(PROVIDERS.keys()))
    def test_prompt_is_in_command(self, provider_name, workspace):
        """Prompt should appear in command (unless special case)."""
        provider = get_provider(provider_name)
        prompt = "Unique test prompt XYZ123"
        cmd = provider.build_command(prompt, workspace)
        # Amp's continue_session is a special case that doesn't include prompt
        if not (provider_name == "amp" and "threads" in cmd):
            assert prompt in cmd

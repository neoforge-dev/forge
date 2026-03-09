"""Comprehensive unit tests for the CursorAgentProvider.

Tests every function, property, and branch in
forge_harness/providers/cursor.py using pytest + unittest.mock.
External dependencies (subprocess, filesystem, os.environ) are fully mocked.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.providers.cursor import CursorAgentProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> CursorAgentProvider:
    """Return a fresh CursorAgentProvider instance."""
    return CursorAgentProvider()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Return a temporary workspace Path (no need to exist on disk)."""
    return tmp_path / "my_project"


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    """Test all @property definitions on CursorAgentProvider."""

    def test_name(self, provider: CursorAgentProvider) -> None:
        assert provider.name == "cursor"

    def test_name_type(self, provider: CursorAgentProvider) -> None:
        assert isinstance(provider.name, str)

    def test_executable(self, provider: CursorAgentProvider) -> None:
        assert provider.executable == "cursor-agent"

    def test_executable_type(self, provider: CursorAgentProvider) -> None:
        assert isinstance(provider.executable, str)

    def test_supports_stream_json_is_true(self, provider: CursorAgentProvider) -> None:
        assert provider.supports_stream_json is True

    def test_supports_auto_approve_is_true(self, provider: CursorAgentProvider) -> None:
        assert provider.supports_auto_approve is True

    def test_repr(self, provider: CursorAgentProvider) -> None:
        """Inherited __repr__ should include class name and provider name."""
        r = repr(provider)
        assert "CursorAgentProvider" in r
        assert "cursor" in r


# ---------------------------------------------------------------------------
# build_command — invariants for every invocation
# ---------------------------------------------------------------------------


class TestBuildCommandInvariants:
    """Properties that must hold for every build_command call."""

    def test_returns_list(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert isinstance(cmd, list)

    def test_all_elements_are_strings(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert all(isinstance(arg, str) for arg in cmd)

    def test_starts_with_executable(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert cmd[0] == "cursor-agent"

    def test_always_contains_p_flag(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "-p" in cmd

    def test_p_flag_at_index_1(self, provider: CursorAgentProvider, workspace: Path) -> None:
        """The -p flag is always appended immediately after the executable."""
        cmd = provider.build_command("hello", workspace)
        assert cmd[1] == "-p"

    def test_prompt_is_last_element(self, provider: CursorAgentProvider, workspace: Path) -> None:
        """Prompt is always the final positional argument."""
        prompt = "unique-sentinel-prompt"
        cmd = provider.build_command(prompt, workspace)
        assert cmd[-1] == prompt

    def test_workspace_flag_present(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--workspace" in cmd

    def test_workspace_value_present(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert str(workspace) in cmd

    def test_workspace_follows_flag(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        idx = cmd.index("--workspace")
        assert cmd[idx + 1] == str(workspace)


# ---------------------------------------------------------------------------
# build_command — minimal (no optional flags)
# ---------------------------------------------------------------------------


class TestBuildCommandMinimal:
    """Minimal command: only the required elements."""

    def test_minimal_command_structure(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("Do something", workspace)
        # Must contain exactly: executable, -p, --workspace, <path>, prompt
        assert cmd == ["cursor-agent", "-p", "--workspace", str(workspace), "Do something"]

    def test_no_output_format_by_default(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--output-format" not in cmd

    def test_no_approve_mcps_by_default(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--approve-mcps" not in cmd

    def test_no_model_by_default(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--model" not in cmd

    def test_no_mode_by_default(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--mode" not in cmd

    def test_no_sandbox_by_default(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--sandbox" not in cmd

    def test_no_resume_by_default(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--resume" not in cmd

    def test_no_continue_by_default(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace)
        assert "--continue" not in cmd


# ---------------------------------------------------------------------------
# build_command — stream_json
# ---------------------------------------------------------------------------


class TestBuildCommandStreamJson:
    """Tests for the stream_json flag."""

    def test_stream_json_adds_output_format(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, stream_json=True)
        assert "--output-format" in cmd

    def test_stream_json_value_is_stream_json(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, stream_json=True)
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_stream_json_false_omits_output_format(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, stream_json=False)
        assert "--output-format" not in cmd

    def test_stream_json_appears_before_workspace(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        """Output format is declared before the workspace flag (code order)."""
        cmd = provider.build_command("hello", workspace, stream_json=True)
        assert cmd.index("--output-format") < cmd.index("--workspace")


# ---------------------------------------------------------------------------
# build_command — auto_approve
# ---------------------------------------------------------------------------


class TestBuildCommandAutoApprove:
    """Tests for the auto_approve flag."""

    def test_auto_approve_adds_approve_mcps(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, auto_approve=True)
        assert "--approve-mcps" in cmd

    def test_auto_approve_false_omits_approve_mcps(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, auto_approve=False)
        assert "--approve-mcps" not in cmd

    def test_approve_mcps_appears_before_workspace(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, auto_approve=True)
        assert cmd.index("--approve-mcps") < cmd.index("--workspace")


# ---------------------------------------------------------------------------
# build_command — model
# ---------------------------------------------------------------------------


class TestBuildCommandModel:
    """Tests for the model parameter."""

    def test_model_adds_model_flag(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace, model="sonnet-4")
        assert "--model" in cmd

    def test_model_value_follows_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, model="sonnet-4")
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet-4"

    def test_model_none_omits_flag(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace, model=None)
        assert "--model" not in cmd

    def test_model_empty_string_omits_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        """Empty string is falsy — should not add --model."""
        cmd = provider.build_command("hello", workspace, model="")
        assert "--model" not in cmd

    def test_model_various_values(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        for model_name in ("claude-opus-4", "gpt-4o", "gemini-2.0-flash"):
            cmd = provider.build_command("hello", workspace, model=model_name)
            assert model_name in cmd


# ---------------------------------------------------------------------------
# build_command — mode
# ---------------------------------------------------------------------------


class TestBuildCommandMode:
    """Tests for the mode parameter (plan / ask only)."""

    @pytest.mark.parametrize("mode", ["plan", "ask"])
    def test_valid_mode_adds_mode_flag(
        self, provider: CursorAgentProvider, workspace: Path, mode: str
    ) -> None:
        cmd = provider.build_command("hello", workspace, mode=mode)
        assert "--mode" in cmd

    @pytest.mark.parametrize("mode", ["plan", "ask"])
    def test_valid_mode_value_follows_flag(
        self, provider: CursorAgentProvider, workspace: Path, mode: str
    ) -> None:
        cmd = provider.build_command("hello", workspace, mode=mode)
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == mode

    def test_mode_none_omits_flag(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace, mode=None)
        assert "--mode" not in cmd

    @pytest.mark.parametrize("bad_mode", ["agent", "edit", "interactive", "auto", ""])
    def test_invalid_mode_omits_flag(
        self, provider: CursorAgentProvider, workspace: Path, bad_mode: str
    ) -> None:
        """Modes other than 'plan' or 'ask' should be silently ignored."""
        cmd = provider.build_command("hello", workspace, mode=bad_mode)
        assert "--mode" not in cmd

    def test_mode_plan_is_read_only_sentinel(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        """plan mode is described as read-only in the docstring."""
        cmd = provider.build_command("task", workspace, mode="plan")
        assert "plan" in cmd

    def test_mode_ask_is_qa_sentinel(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("task", workspace, mode="ask")
        assert "ask" in cmd


# ---------------------------------------------------------------------------
# build_command — sandbox
# ---------------------------------------------------------------------------


class TestBuildCommandSandbox:
    """Tests for the sandbox parameter."""

    def test_sandbox_adds_sandbox_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, sandbox="disabled")
        assert "--sandbox" in cmd

    def test_sandbox_value_follows_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, sandbox="disabled")
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "disabled"

    def test_sandbox_enabled_value(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace, sandbox="enabled")
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "enabled"

    def test_sandbox_none_omits_flag(self, provider: CursorAgentProvider, workspace: Path) -> None:
        cmd = provider.build_command("hello", workspace, sandbox=None)
        assert "--sandbox" not in cmd

    def test_sandbox_empty_string_omits_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        """Empty string is falsy — should not add --sandbox."""
        cmd = provider.build_command("hello", workspace, sandbox="")
        assert "--sandbox" not in cmd


# ---------------------------------------------------------------------------
# build_command — session management
# ---------------------------------------------------------------------------


class TestBuildCommandSessionManagement:
    """Tests for continue_session and session_id (mutually exclusive branches)."""

    # session_id branch
    def test_session_id_adds_resume_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, session_id="chat-xyz")
        assert "--resume" in cmd

    def test_session_id_value_follows_resume_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, session_id="chat-xyz")
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "chat-xyz"

    def test_session_id_does_not_add_continue(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, session_id="chat-xyz")
        assert "--continue" not in cmd

    # continue_session branch
    def test_continue_session_adds_continue_flag(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, continue_session=True)
        assert "--continue" in cmd

    def test_continue_session_does_not_add_resume(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, continue_session=True)
        assert "--resume" not in cmd

    def test_continue_session_false_omits_continue(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, continue_session=False)
        assert "--continue" not in cmd

    # session_id takes priority over continue_session
    def test_session_id_takes_priority_over_continue(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        """When both session_id and continue_session are set, --resume wins."""
        cmd = provider.build_command(
            "hello", workspace, session_id="chat-abc", continue_session=True
        )
        assert "--resume" in cmd
        assert "--continue" not in cmd

    def test_session_id_none_and_continue_false(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command(
            "hello", workspace, session_id=None, continue_session=False
        )
        assert "--resume" not in cmd
        assert "--continue" not in cmd


# ---------------------------------------------------------------------------
# build_command — labels / timeout (ignored parameters)
# ---------------------------------------------------------------------------


class TestBuildCommandIgnoredParams:
    """Labels and timeout are accepted but not used."""

    def test_labels_are_accepted_without_error(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, labels=["key:val", "other"])
        assert isinstance(cmd, list)

    def test_labels_do_not_appear_in_command(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, labels=["agent:test", "domain:forge"])
        assert "agent:test" not in cmd
        assert "domain:forge" not in cmd
        assert "-l" not in cmd

    def test_timeout_is_accepted_without_error(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, timeout=300)
        assert isinstance(cmd, list)

    def test_timeout_does_not_appear_in_command(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, timeout=300)
        assert "300" not in cmd
        assert "--timeout" not in cmd

    def test_extra_kwargs_are_silently_ignored(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command(
            "hello", workspace, some_unknown_flag=True, another_flag="value"
        )
        assert isinstance(cmd, list)
        assert "some_unknown_flag" not in cmd


# ---------------------------------------------------------------------------
# build_command — workspace path coercion
# ---------------------------------------------------------------------------


class TestBuildCommandWorkspacePath:
    """The workspace is always converted to str via str(workspace)."""

    def test_workspace_as_path_object(self, provider: CursorAgentProvider, tmp_path: Path) -> None:
        ws = tmp_path / "deep" / "nested" / "dir"
        cmd = provider.build_command("hello", ws)
        assert str(ws) in cmd

    def test_workspace_path_with_spaces(
        self, provider: CursorAgentProvider, tmp_path: Path
    ) -> None:
        ws = tmp_path / "my project dir"
        cmd = provider.build_command("hello", ws)
        assert str(ws) in cmd

    def test_workspace_root_path(self, provider: CursorAgentProvider) -> None:
        ws = Path("/")
        cmd = provider.build_command("hello", ws)
        assert "/" in cmd


# ---------------------------------------------------------------------------
# build_command — complex / combined scenarios
# ---------------------------------------------------------------------------


class TestBuildCommandCombinedFlags:
    """Integration-style tests exercising multiple flags together."""

    def test_full_featured_command(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command(
            "Implement auth",
            workspace,
            stream_json=True,
            auto_approve=True,
            model="sonnet-4",
            mode="plan",
            sandbox="disabled",
            session_id="chat-99",
            labels=["k:v"],
            timeout=600,
        )
        assert cmd[0] == "cursor-agent"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--approve-mcps" in cmd
        assert "--workspace" in cmd
        assert str(workspace) in cmd
        assert "--model" in cmd
        assert "sonnet-4" in cmd
        assert "--mode" in cmd
        assert "plan" in cmd
        assert "--sandbox" in cmd
        assert "disabled" in cmd
        assert "--resume" in cmd
        assert "chat-99" in cmd
        assert cmd[-1] == "Implement auth"

    def test_stream_json_and_auto_approve_together(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command(
            "hello", workspace, stream_json=True, auto_approve=True
        )
        assert "--output-format" in cmd
        assert "--approve-mcps" in cmd

    def test_model_and_mode_together(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command("hello", workspace, model="opus-4", mode="ask")
        assert "--model" in cmd
        assert "opus-4" in cmd
        assert "--mode" in cmd
        assert "ask" in cmd

    def test_continue_session_with_model(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        cmd = provider.build_command(
            "hello", workspace, continue_session=True, model="sonnet-4"
        )
        assert "--continue" in cmd
        assert "--model" in cmd

    def test_prompt_is_always_last_with_all_flags(
        self, provider: CursorAgentProvider, workspace: Path
    ) -> None:
        prompt = "sentinel-prompt"
        cmd = provider.build_command(
            prompt,
            workspace,
            stream_json=True,
            auto_approve=True,
            model="gpt-4o",
            mode="plan",
            sandbox="disabled",
            continue_session=True,
        )
        assert cmd[-1] == prompt


# ---------------------------------------------------------------------------
# get_env_vars
# ---------------------------------------------------------------------------


class TestGetEnvVars:
    """Tests for get_env_vars — covers the CURSOR_API_KEY env logic."""

    def test_returns_dict(self, provider: CursorAgentProvider) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = provider.get_env_vars()
        assert isinstance(result, dict)

    def test_empty_when_no_api_key(self, provider: CursorAgentProvider) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CURSOR_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = provider.get_env_vars()
        assert "CURSOR_API_KEY" not in result

    def test_returns_api_key_when_set(self, provider: CursorAgentProvider) -> None:
        with patch.dict(os.environ, {"CURSOR_API_KEY": "test-key-abc"}, clear=False):
            result = provider.get_env_vars()
        assert result == {"CURSOR_API_KEY": "test-key-abc"}

    def test_api_key_value_is_preserved(self, provider: CursorAgentProvider) -> None:
        secret = "super-secret-key-12345"
        with patch.dict(os.environ, {"CURSOR_API_KEY": secret}, clear=False):
            result = provider.get_env_vars()
        assert result["CURSOR_API_KEY"] == secret

    def test_only_cursor_api_key_in_result(self, provider: CursorAgentProvider) -> None:
        """get_env_vars should not leak unrelated env vars."""
        with patch.dict(
            os.environ,
            {"CURSOR_API_KEY": "key", "SOME_OTHER_VAR": "value"},
            clear=False,
        ):
            result = provider.get_env_vars()
        assert set(result.keys()) == {"CURSOR_API_KEY"}

    def test_walrus_operator_branch_no_key(self, provider: CursorAgentProvider) -> None:
        """When CURSOR_API_KEY is absent, result dict is empty (walrus returns None)."""
        with patch("os.environ.get", return_value=None):
            result = provider.get_env_vars()
        assert result == {}

    def test_walrus_operator_branch_with_key(self, provider: CursorAgentProvider) -> None:
        """When CURSOR_API_KEY is present, walrus assigns and adds to dict."""
        with patch("os.environ.get", return_value="mocked-key"):
            result = provider.get_env_vars()
        assert result == {"CURSOR_API_KEY": "mocked-key"}

    def test_multiple_calls_are_independent(self, provider: CursorAgentProvider) -> None:
        """Each call reads from the environment at call time."""
        with patch.dict(os.environ, {"CURSOR_API_KEY": "first-key"}, clear=False):
            result1 = provider.get_env_vars()
        env_without = {k: v for k, v in os.environ.items() if k != "CURSOR_API_KEY"}
        with patch.dict(os.environ, env_without, clear=True):
            result2 = provider.get_env_vars()
        assert result1 == {"CURSOR_API_KEY": "first-key"}
        assert result2 == {}


# ---------------------------------------------------------------------------
# Inheritance / ABC contract
# ---------------------------------------------------------------------------


class TestInheritance:
    """Verify CursorAgentProvider correctly satisfies the AgentProvider ABC."""

    def test_is_agent_provider_subclass(self, provider: CursorAgentProvider) -> None:
        from forge_harness.providers.base import AgentProvider

        assert isinstance(provider, AgentProvider)

    def test_can_be_instantiated(self) -> None:
        """Instantiation should not raise."""
        p = CursorAgentProvider()
        assert p is not None

    def test_build_command_is_callable(self, provider: CursorAgentProvider) -> None:
        assert callable(provider.build_command)

    def test_get_env_vars_is_callable(self, provider: CursorAgentProvider) -> None:
        assert callable(provider.get_env_vars)

    def test_name_property_is_not_none(self, provider: CursorAgentProvider) -> None:
        assert provider.name is not None

    def test_executable_property_is_not_none(self, provider: CursorAgentProvider) -> None:
        assert provider.executable is not None

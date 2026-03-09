"""Pi provider (BuildWithPi).

Pi offers multi-provider support (can use any LLM) and thinking levels.
"""

from pathlib import Path

from .base import AgentProvider


class PiProvider(AgentProvider):
    """Provider for Pi (BuildWithPi).

    Pi supports multiple LLM providers and has a unique thinking levels feature.
    It has similar tools to Claude Code (read, bash, edit, write).

    CLI reference:
        pi -p "prompt"                              # Non-interactive (print mode)
        pi --mode json                              # JSON output mode
        pi --provider anthropic --model claude-sonnet-4  # Provider/model
        pi --continue "prompt"                      # Continue session
        pi --resume                                 # Resume (opens TUI)
        pi --session <path>                         # Load session file
        pi --session-dir <dir>                      # Session storage dir
        pi --tools read,bash,edit,write             # Tool configuration
        pi --thinking high                          # Thinking level
        pi -e /path/to/extension.js                 # Load extension
        pi --skill /path/to/skill                   # Load skill

    Thinking levels: off, minimal, low, medium, high, xhigh

    Environment:
        ANTHROPIC_API_KEY: For Anthropic provider
        OPENAI_API_KEY: For OpenAI provider
        PI_CODING_AGENT_DIR: Session storage directory
    """

    @property
    def name(self) -> str:
        return "pi"

    @property
    def executable(self) -> str:
        return "pi"

    @property
    def supports_stream_json(self) -> bool:
        # Pi has --mode json but different format from Claude Code
        return False  # Mark as partial - format differs

    @property
    def supports_auto_approve(self) -> bool:
        return False  # No auto-approve flag

    def build_command(
        self,
        prompt: str,
        workspace: Path,
        *,
        timeout: int | None = None,
        model: str | None = None,
        stream_json: bool = False,
        auto_approve: bool = False,
        continue_session: bool = False,
        session_id: str | None = None,
        labels: list[str] | None = None,
        provider: str | None = None,
        thinking: str | None = None,
        tools: list[str] | None = None,
        session_dir: str | None = None,
        **kwargs,
    ) -> list[str]:
        """Build Pi command.

        Args:
            prompt: Task prompt
            workspace: Working directory (pi uses cwd)
            timeout: Not directly supported
            model: Model name (e.g., 'claude-sonnet-4', 'gpt-4')
            stream_json: Use --mode json (partial compatibility)
            auto_approve: Not supported
            continue_session: Use --continue
            session_id: Use --session <path>
            labels: Not supported
            provider: LLM provider (anthropic, openai, etc.)
            thinking: Thinking level (off, minimal, low, medium, high, xhigh)
            tools: List of tools to enable (read, bash, edit, write, etc.)
            session_dir: Session storage directory
            **kwargs: Additional options (ignored)

        Returns:
            Command list for subprocess
        """
        cmd = [self.executable]

        # Non-interactive mode
        cmd.append("-p")

        # JSON output mode (partial Claude Code compatibility)
        if stream_json:
            cmd.extend(["--mode", "json"])

        # Provider and model selection
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])

        # Thinking level
        if thinking and thinking in ("off", "minimal", "low", "medium", "high", "xhigh"):
            cmd.extend(["--thinking", thinking])

        # Tool configuration
        if tools:
            cmd.extend(["--tools", ",".join(tools)])

        # Session management
        if session_dir:
            cmd.extend(["--session-dir", session_dir])
        if session_id:
            cmd.extend(["--session", session_id])
        elif continue_session:
            cmd.append("--continue")

        # Prompt (positional argument)
        cmd.append(prompt)

        return cmd

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables for Pi.

        Pi can use various providers, each with their own API key.
        """
        import os

        vars = {}
        # Pi supports multiple providers
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PI_CODING_AGENT_DIR"):
            if value := os.environ.get(key):
                vars[key] = value
        return vars

"""Cursor Agent provider.

Cursor Agent has Claude Code compatible stream-json output and familiar interface.
"""

from pathlib import Path

from .base import AgentProvider


class CursorAgentProvider(AgentProvider):
    """Provider for Cursor Agent.

    Cursor Agent offers stream-json compatibility and familiar Claude-like interface.
    It also has built-in browser automation and plan mode.

    CLI reference:
        cursor-agent "prompt"                        # Interactive mode
        cursor-agent -p "prompt"                     # Non-interactive (print mode)
        cursor-agent -p --output-format json         # JSON output
        cursor-agent -p --output-format stream-json  # Claude Code compatible!
        cursor-agent --continue                      # Continue last session
        cursor-agent --resume <chatId>               # Resume specific chat
        cursor-agent --model sonnet-4                # Model selection
        cursor-agent --mode plan "prompt"            # Plan mode (read-only)
        cursor-agent --mode ask "prompt"             # Ask mode (Q&A)
        cursor-agent --workspace /path              # Workspace path
        cursor-agent -p --approve-mcps              # Auto-approve MCPs (headless)
        cursor-agent --sandbox disabled             # Disable sandbox

    Environment:
        CURSOR_API_KEY: API key for authentication
    """

    @property
    def name(self) -> str:
        return "cursor"

    @property
    def executable(self) -> str:
        return "cursor-agent"

    @property
    def supports_stream_json(self) -> bool:
        return True

    @property
    def supports_auto_approve(self) -> bool:
        return True

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
        mode: str | None = None,
        sandbox: str | None = None,
        **kwargs,
    ) -> list[str]:
        """Build Cursor Agent command.

        Args:
            prompt: Task prompt
            workspace: Working directory (uses --workspace flag)
            timeout: Not directly supported
            model: Model name (e.g., 'sonnet-4')
            stream_json: Use --output-format stream-json
            auto_approve: Use --approve-mcps
            continue_session: Use --continue
            session_id: Use --resume <chatId>
            labels: Not supported
            mode: Agent mode (plan, ask)
            sandbox: Sandbox mode (disabled, enabled)
            **kwargs: Additional options (ignored)

        Returns:
            Command list for subprocess
        """
        cmd = [self.executable]

        # Non-interactive mode (required for scripting)
        cmd.append("-p")

        # Output format - stream-json for Claude Code compatibility
        if stream_json:
            cmd.extend(["--output-format", "stream-json"])

        # Auto-approve MCPs (only works in headless mode with -p)
        if auto_approve:
            cmd.append("--approve-mcps")

        # Workspace path
        cmd.extend(["--workspace", str(workspace)])

        # Model selection
        if model:
            cmd.extend(["--model", model])

        # Mode selection (plan=read-only, ask=Q&A)
        if mode and mode in ("plan", "ask"):
            cmd.extend(["--mode", mode])

        # Sandbox control
        if sandbox:
            cmd.extend(["--sandbox", sandbox])

        # Session management
        if session_id:
            cmd.extend(["--resume", session_id])
        elif continue_session:
            cmd.append("--continue")

        # Prompt (positional argument)
        cmd.append(prompt)

        return cmd

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables for Cursor Agent.

        CURSOR_API_KEY should be set in the environment.
        """
        import os

        vars = {}
        if api_key := os.environ.get("CURSOR_API_KEY"):
            vars["CURSOR_API_KEY"] = api_key
        return vars

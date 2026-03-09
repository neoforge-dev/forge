"""OpenCode provider.

OpenCode requires the most adaptation as it doesn't support JSON streaming.
However, it has unique features like TUI mode, server mode, and GitHub PR integration.
"""

from pathlib import Path

from .base import AgentProvider


class OpenCodeProvider(AgentProvider):
    """Provider for OpenCode.

    OpenCode has a different architecture with TUI, server, and web modes.
    It doesn't support JSON streaming but has unique features.

    CLI reference:
        opencode [project_path]                   # Interactive TUI
        opencode run "prompt"                     # Non-interactive run
        opencode -m anthropic/claude-sonnet-4 run "prompt"  # With model
        opencode -c                               # Continue last session
        opencode -s <session_id>                  # Continue specific session
        opencode serve --port 8080               # Headless server mode
        opencode web                              # Web interface
        opencode --agent <name> run "prompt"      # Agent mode
        opencode export [sessionID]               # Export session
        opencode import <file>                    # Import session
        opencode pr 123                           # GitHub PR integration

    Environment:
        No specific API key required (uses opencode auth)
    """

    @property
    def name(self) -> str:
        return "opencode"

    @property
    def executable(self) -> str:
        return "opencode"

    @property
    def supports_stream_json(self) -> bool:
        return False  # No JSON streaming mode

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
        agent: str | None = None,
        **kwargs,
    ) -> list[str]:
        """Build OpenCode command.

        Args:
            prompt: Task prompt
            workspace: Working directory (can be passed as [project_path])
            timeout: Not directly supported
            model: Model name (e.g., 'anthropic/claude-sonnet-4')
            stream_json: Not supported (ignored)
            auto_approve: Not supported (ignored)
            continue_session: Use -c flag
            session_id: Use -s <session_id>
            labels: Not supported
            agent: Named agent to use
            **kwargs: Additional options (ignored)

        Returns:
            Command list for subprocess
        """
        cmd = [self.executable]

        # Model selection
        if model:
            cmd.extend(["-m", model])

        # Session management
        if session_id:
            cmd.extend(["-s", session_id])
        elif continue_session:
            cmd.append("-c")

        # Agent mode
        if agent:
            cmd.extend(["--agent", agent])

        # Non-interactive run subcommand
        cmd.append("run")

        # Prompt (positional argument)
        cmd.append(prompt)

        return cmd

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables for OpenCode.

        OpenCode uses its own auth system (opencode auth).
        """
        return {}

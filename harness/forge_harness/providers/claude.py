"""Claude Code provider.

This is the default provider, implementing support for Anthropic's Claude Code CLI.
"""

from pathlib import Path

from .base import AgentProvider


class ClaudeCodeProvider(AgentProvider):
    """Provider for Claude Code (Anthropic's official CLI).

    Claude Code is the default agent and has the most mature integration.

    CLI reference:
        claude -p "prompt"           # Non-interactive mode
        claude --continue            # Continue last session
        claude --resume <id>         # Resume specific session
        claude --model <model>       # Model selection
        claude --dangerously-skip-permissions  # Auto-approve all
    """

    @property
    def name(self) -> str:
        return "claude"

    @property
    def executable(self) -> str:
        return "claude"

    @property
    def supports_stream_json(self) -> bool:
        # Claude Code streams JSON natively in non-interactive mode
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
        **kwargs,
    ) -> list[str]:
        """Build Claude Code command.

        Args:
            prompt: Task prompt
            workspace: Working directory
            timeout: Not directly supported, use external timeout wrapper
            model: Model name (e.g., 'sonnet', 'opus')
            stream_json: Enabled by default in -p mode
            auto_approve: Use --dangerously-skip-permissions
            continue_session: Use --continue
            session_id: Use --resume <id>
            labels: Not supported
            **kwargs: Additional options (ignored)

        Returns:
            Command list for subprocess
        """
        cmd = [self.executable]

        # Auto-approve mode
        if auto_approve:
            cmd.append("--dangerously-skip-permissions")

        # Model selection
        if model:
            cmd.extend(["--model", model])

        # Session management
        if session_id:
            cmd.extend(["--resume", session_id])
        elif continue_session:
            cmd.append("--continue")

        # Non-interactive mode with prompt (must be last)
        # Note: -p flag makes it non-interactive
        cmd.extend(["-p", prompt])

        return cmd

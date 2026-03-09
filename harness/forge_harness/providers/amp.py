"""Amp provider.

Amp has the best Claude Code stream-json compatibility and clean CLI interface.
Recommended as the first alternative provider to integrate.
"""

from pathlib import Path

from .base import AgentProvider


class AmpProvider(AgentProvider):
    """Provider for Amp (Sourcegraph).

    Amp has excellent Claude Code compatibility with native stream-json support.
    It also provides thread management, labels, and mode selection.

    CLI reference:
        amp -x "prompt"                    # Execute mode (non-interactive)
        amp -x --stream-json "prompt"      # Stream JSON output
        amp -x --stream-json-thinking      # Include thinking blocks
        amp --dangerously-allow-all -x     # Auto-approve all
        amp -m free "prompt"               # Mode: free (budget)
        amp -m rush "prompt"               # Mode: rush (fast)
        amp -m smart "prompt"              # Mode: smart (best)
        amp -l "label" -x "prompt"         # Add labels
        amp threads continue               # Continue last thread
        amp threads list                   # List threads

    Environment:
        AMP_API_KEY: API key for authentication
    """

    @property
    def name(self) -> str:
        return "amp"

    @property
    def executable(self) -> str:
        return "amp"

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
        include_thinking: bool = False,
        **kwargs,
    ) -> list[str]:
        """Build Amp command.

        Args:
            prompt: Task prompt
            workspace: Working directory (amp uses cwd)
            timeout: Not directly supported
            model: Not used (use mode instead)
            stream_json: Use --stream-json
            auto_approve: Use --dangerously-allow-all
            continue_session: Use 'threads continue' subcommand
            session_id: Not directly supported (use threads)
            labels: Add -l flags for each label
            mode: Amp mode (free, rush, smart)
            include_thinking: Use --stream-json-thinking instead
            **kwargs: Additional options (ignored)

        Returns:
            Command list for subprocess
        """
        cmd = [self.executable]

        # Auto-approve must come before -x
        if auto_approve:
            cmd.append("--dangerously-allow-all")

        # Mode selection (controls model + prompt + tools)
        if mode and mode in ("free", "rush", "smart"):
            cmd.extend(["-m", mode])

        # Execute mode flag
        cmd.append("-x")

        # Stream JSON output
        if stream_json:
            if include_thinking:
                cmd.append("--stream-json-thinking")
            else:
                cmd.append("--stream-json")

        # Labels for metadata tracking
        if labels:
            for label in labels:
                cmd.extend(["-l", label])

        # Session continuation - amp uses 'threads continue' subcommand
        # For simplicity in spawn context, we just run with the prompt
        # Thread management is better handled at a higher level
        if continue_session:
            # Return threads continue command instead
            return [self.executable, "threads", "continue"]

        # Prompt (positional argument)
        cmd.append(prompt)

        return cmd

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables for Amp.

        AMP_API_KEY should be set in the environment.
        """
        import os

        vars = {}
        if api_key := os.environ.get("AMP_API_KEY"):
            vars["AMP_API_KEY"] = api_key
        return vars

"""Kimi CLI provider.

Implementation for Kimi (Moonshot AI's CLI agent).
"""

from pathlib import Path

from .base import AgentProvider


class KimiProvider(AgentProvider):
    """Provider for Kimi CLI (Moonshot AI).

    CLI reference:
        kimi -p "prompt"           # Direct prompt
        kimi --continue            # Continue last session
        kimi --session <id>        # Resume specific session
        kimi --model <model>       # Model selection
        kimi --yolo                # Auto-approve all actions
        kimi --print               # Non-interactive mode
    """

    @property
    def name(self) -> str:
        return "kimi"

    @property
    def executable(self) -> str:
        return "kimi"

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
        **kwargs,
    ) -> list[str]:
        """Build Kimi command.

        Args:
            prompt: Task prompt
            workspace: Working directory
            timeout: Not directly supported
            model: Model name
            stream_json: Use --output-format stream-json --print
            auto_approve: Use --yolo
            continue_session: Use --continue
            session_id: Use --session <id>
            labels: Not supported
            **kwargs: Additional options

        Returns:
            Command list for subprocess
        """
        cmd = [self.executable]

        # Working directory
        cmd.extend(["--work-dir", str(workspace)])

        # Auto-approve mode
        if auto_approve:
            cmd.append("--yolo")

        # Model selection
        if model:
            cmd.extend(["--model", model])

        # Session management
        if session_id:
            cmd.extend(["--session", session_id])
        elif continue_session:
            cmd.append("--continue")

        # JSON streaming / Print mode
        if stream_json:
            cmd.extend(["--print", "--output-format", "stream-json"])
        elif not sys.stdin.isatty():
            # If not in a TTY and not streaming JSON, use --print to avoid interactive hangs
            cmd.append("--print")

        # Prompt
        cmd.extend(["--prompt", prompt])

        return cmd


import sys

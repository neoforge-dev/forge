"""Base class for AI coding agent providers.

This module defines the abstract interface that all agent providers must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentResponse:
    """Unified response from any agent.

    Provides a consistent interface for agent outputs regardless of the
    underlying provider.
    """

    content: str
    thinking: str | None = None
    tool_calls: list = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    session_id: str | None = None


class AgentProvider(ABC):
    """Abstract base class for AI coding agent providers.

    Each provider implements the interface for a specific AI coding agent
    (claude, amp, cursor-agent, pi, opencode).

    Example:
        provider = AmpProvider()
        cmd = provider.build_command(
            prompt="Fix the authentication bug",
            workspace=Path("/project"),
            stream_json=True,
        )
        # cmd = ['amp', '-x', '--stream-json', 'Fix the authentication bug']
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (claude, pi, opencode, cursor, amp)."""
        pass

    @property
    @abstractmethod
    def executable(self) -> str:
        """The CLI executable name."""
        pass

    @property
    def supports_stream_json(self) -> bool:
        """Whether this provider supports JSON streaming output."""
        return False

    @property
    def supports_auto_approve(self) -> bool:
        """Whether this provider supports auto-approving tool calls."""
        return False

    @property
    def supports_session_continue(self) -> bool:
        """Whether this provider supports continuing a session."""
        return True

    @abstractmethod
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
        """Build CLI command for spawning the agent.

        Args:
            prompt: The task/prompt for the agent
            workspace: Working directory for the agent
            timeout: Max runtime in seconds (optional)
            model: Model to use (optional, provider-specific)
            stream_json: Whether to enable JSON streaming output
            auto_approve: Whether to auto-approve tool calls
            continue_session: Whether to continue the last session
            session_id: Specific session ID to resume
            labels: Metadata labels (provider-specific)
            **kwargs: Provider-specific options

        Returns:
            List of command arguments suitable for subprocess
        """
        pass

    def get_env_vars(self) -> dict[str, str]:
        """Get any environment variables needed by this provider.

        Returns:
            Dict of environment variable names to values
        """
        return {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"

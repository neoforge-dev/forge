"""Agent providers for multi-agent support.

This module provides a pluggable architecture for supporting multiple AI coding agents
(claude, amp, cursor-agent, pi, opencode) in the FORGE harness.

Usage:
    from forge_harness.providers import get_provider, PROVIDERS

    # Get a specific provider
    provider = get_provider("amp")
    cmd = provider.build_command("Fix the bug", workspace=Path("/project"))

    # List available providers
    print(list(PROVIDERS.keys()))  # ['claude', 'amp', 'cursor', 'pi', 'opencode']
"""

from .amp import AmpProvider
from .base import AgentProvider, AgentResponse
from .claude import ClaudeCodeProvider
from .cursor import CursorAgentProvider
from .kimi import KimiProvider
from .opencode import OpenCodeProvider
from .pi import PiProvider

PROVIDERS: dict[str, type[AgentProvider]] = {
    "claude": ClaudeCodeProvider,
    "amp": AmpProvider,
    "cursor": CursorAgentProvider,
    "pi": PiProvider,
    "opencode": OpenCodeProvider,
    "kimi": KimiProvider,
}


def get_provider(name: str) -> AgentProvider:
    """Get an agent provider instance by name.

    Args:
        name: Provider name (claude, amp, cursor, pi, opencode, kimi)

    Returns:
        AgentProvider instance

    Raises:
        ValueError: If provider name is not recognized
    """
    if name not in PROVIDERS:
        available = ", ".join(PROVIDERS.keys())
        raise ValueError(f"Unknown provider: {name}. Available: {available}")
    return PROVIDERS[name]()


__all__ = [
    "AgentProvider",
    "AgentResponse",
    "ClaudeCodeProvider",
    "AmpProvider",
    "CursorAgentProvider",
    "PiProvider",
    "OpenCodeProvider",
    "KimiProvider",
    "PROVIDERS",
    "get_provider",
]

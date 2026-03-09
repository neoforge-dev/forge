"""Glance card widgets."""

from .agents import AgentGridCard
from .base import GlanceCard
from .dispatch import DispatchCard
from .errors import ErrorCard
from .fleet import FleetHealthCard
from .git import GitStatusCard
from .nodes import NodeResourceCard
from .session import SessionCard
from .tasks import TaskQueueCard

CARD_REGISTRY: dict[str, type[GlanceCard]] = {
    "fleet": FleetHealthCard,
    "agents": AgentGridCard,
    "tasks": TaskQueueCard,
    "nodes": NodeResourceCard,
    "dispatch": DispatchCard,
    "session": SessionCard,
    "errors": ErrorCard,
    "git": GitStatusCard,
}

__all__ = [
    "CARD_REGISTRY",
    "AgentGridCard",
    "DispatchCard",
    "ErrorCard",
    "FleetHealthCard",
    "GitStatusCard",
    "GlanceCard",
    "NodeResourceCard",
    "SessionCard",
    "TaskQueueCard",
]

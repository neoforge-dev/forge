"""Audit Log Service

Provides immutable record of control actions (dispatch, pause, resume, kill, complete).
Per harness/docs/audit_log_spec.md.
"""

import json
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Backend Interface
# -----------------------------------------------------------------------------


class AuditBackend(ABC):
    """Base interface for audit storage backends."""

    @abstractmethod
    async def emit(self, event: dict[str, Any]) -> None:
        """Persist an audit event."""
        pass


# -----------------------------------------------------------------------------
# JSONL Backend (Default)
# -----------------------------------------------------------------------------


class MemoryAuditBackend(AuditBackend):
    """In-memory backend for tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class JSONLAuditBackend(AuditBackend):
    """File-based audit log using JSONL format.

    Path: .forge/state/audit/audit.jsonl (or configurable).
    """

    def __init__(self, path: Path | str | None = None, forge_root: Path | None = None):
        if path is not None:
            self._path = Path(path)
        else:
            root = forge_root or self._find_forge_root()
            self._path = root / ".forge/state" / "audit" / "audit.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _find_forge_root(self) -> Path:
        """Find FORGE root (look for harness/forge_harness)."""
        current = Path(__file__).resolve().parent
        depth = 0
        while depth < 10:
            if (current / "forge_harness").exists() or (current / "pyproject.toml").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
            depth += 1
        return Path.cwd()

    async def emit(self, event: dict[str, Any]) -> None:
        """Append event as JSONL line."""
        try:
            line = json.dumps(event, default=str) + "\n"
            with open(self._path, "a") as f:
                f.write(line)
        except Exception as e:
            logger.warning("Audit log emit failed: %s", e)


# -----------------------------------------------------------------------------
# Audit Logger
# -----------------------------------------------------------------------------


class AuditLogger:
    """Centralized audit logger for control actions.

    Per spec: events for dispatch, pause, resume, kill, complete.
    """

    def __init__(self, backend: AuditBackend | None = None, forge_root: Path | None = None):
        self._backend = backend or JSONLAuditBackend(forge_root=forge_root)

    async def log(
        self,
        action: str,
        actor: dict[str, Any],
        target: dict[str, Any],
        context: dict[str, Any] | None = None,
        status: str = "success",
        error: str | None = None,
        request_id: str | None = None,
        source: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Emit an audit event per spec schema.

        source: Identifies which domain/agent/system triggered the event
        (e.g. 'webhook_api', 'cli', 'command_center', domain name).
        """
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_id": str(uuid.uuid4()),
            "actor": actor,
            "action": action,
            "target": target,
            "context": context or {},
            "status": status,
            "error": error,
            "request_id": request_id,
            "source": source or "webhook_api",
        }
        # Add IP address and user agent if provided
        if ip_address:
            event["ip_address"] = ip_address
        if user_agent:
            event["user_agent"] = user_agent
        try:
            await self._backend.emit(event)
        except Exception as e:
            logger.warning("Audit log failed (action=%s): %s", action, e)

    async def log_agent_registration(
        self,
        agent_id: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log agent registration."""
        await self.log(
            action="agent_registration",
            actor=actor,
            target={"id": agent_id, "type": "agent"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_agent_deregistration(
        self,
        agent_id: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log agent deregistration."""
        await self.log(
            action="agent_deregistration",
            actor=actor,
            target={"id": agent_id, "type": "agent"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_task_creation(
        self,
        task_id: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log task creation."""
        await self.log(
            action="task_creation",
            actor=actor,
            target={"id": task_id, "type": "task"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_task_deletion(
        self,
        task_id: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log task deletion."""
        await self.log(
            action="task_deletion",
            actor=actor,
            target={"id": task_id, "type": "task"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_task_assignment(
        self,
        task_id: str,
        agent_id: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log task assignment."""
        ctx = context or {}
        ctx["agent_id"] = agent_id
        await self.log(
            action="task_assignment",
            actor=actor,
            target={"id": task_id, "type": "task"},
            context=ctx,
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_approval_decision(
        self,
        approval_id: str,
        decision: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log approval decision (approved/rejected)."""
        ctx = context or {}
        ctx["decision"] = decision
        action_name = "approval_approve" if decision == "approved" else "approval_reject"
        await self.log(
            action=action_name,
            actor=actor,
            target={"id": approval_id, "type": "approval_request"},
            context=ctx,
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_handoff_operation(
        self,
        handoff_id: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log handoff operation."""
        await self.log(
            action="handoff_operation",
            actor=actor,
            target={"id": handoff_id, "type": "handoff"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_fleet_control(
        self,
        action_type: str,
        actor: dict[str, Any],
        target_id: str | None = None,
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log fleet control actions (pause/resume/broadcast)."""
        await self.log(
            action=f"fleet_{action_type}",
            actor=actor,
            target={"id": target_id or "fleet", "type": "fleet"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_configuration_change(
        self,
        config_key: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log configuration changes."""
        ctx = context or {}
        ctx["config_key"] = config_key
        await self.log(
            action="configuration_change",
            actor=actor,
            target={"id": config_key, "type": "configuration"},
            context=ctx,
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_authentication_event(
        self,
        event_type: str,
        actor: dict[str, Any],
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log authentication events (login/logout/refresh)."""
        await self.log(
            action=f"auth_{event_type}",
            actor=actor,
            target={"id": actor.get("id", "unknown"), "type": "user"},
            context=context or {},
            source="webhook_api",
            ip_address=ip_address,
            user_agent=user_agent,
        )


# -----------------------------------------------------------------------------
# Singleton
# -----------------------------------------------------------------------------

_audit_logger: AuditLogger | None = None


def get_audit_logger(forge_root: Path | None = None) -> AuditLogger:
    """Get or create global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(forge_root=forge_root)
    return _audit_logger


def reset_audit_logger() -> None:
    """Reset singleton (for tests)."""
    global _audit_logger
    _audit_logger = None

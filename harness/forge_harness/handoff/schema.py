"""Handoff schema and utilities."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class HandoffStatus(str, Enum):
    """Handoff lifecycle status."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class HandoffPriority(str, Enum):
    """Priority for handoff routing."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Handoff:
    """Handoff artifact for agent-to-agent transfers."""

    id: str
    from_agent: str
    to_agent: str
    task: str
    status: HandoffStatus = HandoffStatus.PENDING
    priority: HandoffPriority = HandoffPriority.MEDIUM
    description: str = ""
    files: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    test_command: str | None = None
    acceptance_criteria: list[str] = field(default_factory=list)
    domain: str | None = None
    project: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    accepted_at: str | None = None
    completed_at: str | None = None
    expires_at: str | None = None
    result: dict[str, Any] | None = None
    notes: str | None = None

    @classmethod
    def new(
        cls,
        from_agent: str,
        to_agent: str,
        task: str,
        **kwargs: Any,
    ) -> Handoff:
        return cls(
            id=str(uuid.uuid4())[:8],
            from_agent=from_agent,
            to_agent=to_agent,
            task=task,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task": self.task,
            "status": self.status,
            "priority": self.priority,
            "description": self.description,
            "files": self.files,
            "context": self.context,
            "test_command": self.test_command,
            "acceptance_criteria": self.acceptance_criteria,
            "domain": self.domain,
            "project": self.project,
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "completed_at": self.completed_at,
            "expires_at": self.expires_at,
            "result": self.result,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Handoff:
        status = data.get("status", HandoffStatus.PENDING)
        if isinstance(status, str):
            status = HandoffStatus(status)
        priority = data.get("priority", HandoffPriority.MEDIUM)
        if isinstance(priority, str):
            priority = HandoffPriority(priority)
        return cls(
            id=data.get("id", ""),
            from_agent=data.get("from_agent", ""),
            to_agent=data.get("to_agent", ""),
            task=data.get("task", ""),
            status=status,
            priority=priority,
            description=data.get("description", ""),
            files=list(data.get("files", [])),
            context=dict(data.get("context", {})),
            test_command=data.get("test_command"),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            domain=data.get("domain"),
            project=data.get("project"),
            created_at=data.get("created_at") or datetime.now(UTC).isoformat(),
            accepted_at=data.get("accepted_at"),
            completed_at=data.get("completed_at"),
            expires_at=data.get("expires_at"),
            result=data.get("result"),
            notes=data.get("notes"),
        )

    def to_json(self) -> str:
        payload = self.to_dict().copy()
        payload["status"] = self.status.value
        payload["priority"] = self.priority.value
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, payload: str) -> Handoff:
        return cls.from_dict(json.loads(payload))

    def to_prompt(self) -> str:
        lines: list[str] = []
        lines.append(f"# Handoff from {self.from_agent}")
        lines.append("")
        lines.append(f"**Task:** {self.task}")
        lines.append(f"**Priority:** {self.priority.value}")
        lines.append(f"Handoff ID: `{self.id}`")
        lines.append("")
        lines.append("Commands:")
        lines.append(f"- `forge-harness handoff accept {self.id}`")
        lines.append(f"- `forge-harness handoff reject {self.id}`")

        if self.description:
            lines.append("")
            lines.append("## Description")
            lines.append(self.description)

        if self.files:
            lines.append("")
            lines.append("## Files to Review")
            for file in self.files:
                lines.append(f"- `{file}`")

        if self.context:
            lines.append("")
            lines.append("## Context")
            lines.append("```json")
            lines.append(json.dumps(self.context, indent=2))
            lines.append("```")

        if self.test_command:
            lines.append("")
            lines.append("## Test Command")
            lines.append("```bash")
            lines.append(self.test_command)
            lines.append("```")

        if self.acceptance_criteria:
            lines.append("")
            lines.append("## Acceptance Criteria")
            for item in self.acceptance_criteria:
                lines.append(f"- [ ] {item}")

        return "\n".join(lines)

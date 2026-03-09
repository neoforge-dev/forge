"""Agent Handoff System for FORGE.

Structured handoffs between agents with automatic routing and notifications.
"""

import asyncio
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiofiles

from forge_harness.handoff.schema import HandoffPriority, HandoffStatus


@dataclass
class Handoff:
    """A handoff between agents."""

    id: str
    from_agent: str
    to_agent: str
    task: str
    status: HandoffStatus = HandoffStatus.PENDING
    priority: HandoffPriority = HandoffPriority.MEDIUM

    # Context
    description: str = ""
    files: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    test_command: str | None = None
    acceptance_criteria: list[str] = field(default_factory=list)

    # Targeting
    domain: str | None = None
    project: str | None = None

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    accepted_at: str | None = None
    completed_at: str | None = None
    expires_at: str | None = None

    # Result
    result: dict | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Handoff":
        """Create from dictionary."""
        if "status" in data and isinstance(data["status"], str):
            data["status"] = HandoffStatus(data["status"])
        if "priority" in data and isinstance(data["priority"], str):
            data["priority"] = HandoffPriority(data["priority"])
        return cls(**data)

    def to_prompt(self) -> str:
        """Generate a prompt for the receiving agent."""
        lines = [
            f"# Handoff from {self.from_agent}",
            "",
            f"**Task:** {self.task}",
            f"**Priority:** {self.priority.value}",
        ]

        if self.description:
            lines.extend(["", "## Description", "", self.description])

        if self.files:
            lines.extend(["", "## Files to Review", ""])
            for f in self.files:
                lines.append(f"- `{f}`")

        if self.context:
            lines.extend(["", "## Context", "", "```json"])
            lines.append(json.dumps(self.context, indent=2))
            lines.append("```")

        if self.test_command:
            lines.extend(["", "## Test Command", "", "```bash", self.test_command, "```"])

        if self.acceptance_criteria:
            lines.extend(["", "## Acceptance Criteria", ""])
            for criterion in self.acceptance_criteria:
                lines.append(f"- [ ] {criterion}")

        lines.extend(
            [
                "",
                "---",
                f"Handoff ID: `{self.id}`",
                f"Accept with: `forge-harness handoff accept {self.id}`",
                f'Reject with: `forge-harness handoff reject {self.id} --reason "..."`',
            ]
        )

        return "\n".join(lines)


class HandoffManager:
    """Manages handoffs between agents."""

    def __init__(self, handoff_dir: Path):
        self.handoff_dir = Path(handoff_dir)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _handoff_path(self, handoff_id: str) -> Path:
        """Get path for a handoff file."""
        return self.handoff_dir / f"{handoff_id}.json"

    async def create(
        self,
        from_agent: str,
        to_agent: str,
        task: str,
        **kwargs,
    ) -> Handoff:
        """Create a new handoff."""
        handoff_id = str(uuid.uuid4())[:8]

        handoff = Handoff(
            id=handoff_id,
            from_agent=from_agent,
            to_agent=to_agent,
            task=task,
            **kwargs,
        )

        async with self._lock:
            path = self._handoff_path(handoff_id)
            async with aiofiles.open(path, "w") as f:
                await f.write(json.dumps(handoff.to_dict(), indent=2))

        return handoff

    async def get(self, handoff_id: str) -> Handoff | None:
        """Get a handoff by ID."""
        path = self._handoff_path(handoff_id)
        if not path.exists():
            return None

        async with aiofiles.open(path) as f:
            data = json.loads(await f.read())
        return Handoff.from_dict(data)

    async def update(self, handoff: Handoff) -> Handoff:
        """Update a handoff."""
        async with self._lock:
            path = self._handoff_path(handoff.id)
            async with aiofiles.open(path, "w") as f:
                await f.write(json.dumps(handoff.to_dict(), indent=2))
        return handoff

    async def list_for_agent(
        self,
        agent_id: str,
        status: HandoffStatus | None = None,
        as_sender: bool = False,
    ) -> list[Handoff]:
        """List handoffs for an agent."""
        handoffs = []

        for path in self.handoff_dir.glob("*.json"):
            async with aiofiles.open(path) as f:
                data = json.loads(await f.read())
            handoff = Handoff.from_dict(data)

            # Filter by agent
            if as_sender:
                if handoff.from_agent != agent_id:
                    continue
            else:
                if handoff.to_agent != agent_id:
                    continue

            # Filter by status
            if status and handoff.status != status:
                continue

            handoffs.append(handoff)

        # Sort by priority and created_at
        priority_order = {
            HandoffPriority.CRITICAL: 0,
            HandoffPriority.HIGH: 1,
            HandoffPriority.MEDIUM: 2,
            HandoffPriority.LOW: 3,
        }
        handoffs.sort(key=lambda h: (priority_order.get(h.priority, 99), h.created_at))

        return handoffs

    async def list_pending(self) -> list[Handoff]:
        """List all pending handoffs."""
        handoffs = []

        for path in self.handoff_dir.glob("*.json"):
            async with aiofiles.open(path) as f:
                data = json.loads(await f.read())
            handoff = Handoff.from_dict(data)

            if handoff.status == HandoffStatus.PENDING:
                handoffs.append(handoff)

        return handoffs

    async def accept(self, handoff_id: str, agent_id: str) -> Handoff | None:
        """Accept a handoff."""
        handoff = await self.get(handoff_id)
        if not handoff:
            return None

        if handoff.to_agent != agent_id:
            raise ValueError(f"Handoff {handoff_id} is not addressed to {agent_id}")

        if handoff.status != HandoffStatus.PENDING:
            raise ValueError(f"Handoff {handoff_id} is not pending (status: {handoff.status})")

        handoff.status = HandoffStatus.ACCEPTED
        handoff.accepted_at = datetime.now(UTC).isoformat()
        await self.update(handoff)
        return handoff

    async def reject(
        self,
        handoff_id: str,
        agent_id: str,
        reason: str = "",
    ) -> Handoff | None:
        """Reject a handoff."""
        handoff = await self.get(handoff_id)
        if not handoff:
            return None

        if handoff.to_agent != agent_id:
            raise ValueError(f"Handoff {handoff_id} is not addressed to {agent_id}")

        handoff.status = HandoffStatus.REJECTED
        handoff.notes = reason
        handoff.completed_at = datetime.now(UTC).isoformat()
        await self.update(handoff)
        return handoff

    async def start(self, handoff_id: str) -> Handoff | None:
        """Mark a handoff as in progress."""
        handoff = await self.get(handoff_id)
        if handoff and handoff.status == HandoffStatus.ACCEPTED:
            handoff.status = HandoffStatus.IN_PROGRESS
            await self.update(handoff)
        return handoff

    async def complete(
        self,
        handoff_id: str,
        result: dict | None = None,
        notes: str = "",
    ) -> Handoff | None:
        """Mark a handoff as completed."""
        handoff = await self.get(handoff_id)
        if handoff:
            handoff.status = HandoffStatus.COMPLETED
            handoff.completed_at = datetime.now(UTC).isoformat()
            handoff.result = result
            if notes:
                handoff.notes = notes
            await self.update(handoff)
        return handoff

    async def get_stats(self) -> dict:
        """Get handoff statistics."""
        stats = {
            "total": 0,
            "by_status": {},
            "by_priority": {},
            "avg_completion_time_hours": 0,
        }

        completion_times = []

        for path in self.handoff_dir.glob("*.json"):
            async with aiofiles.open(path) as f:
                data = json.loads(await f.read())
            handoff = Handoff.from_dict(data)

            stats["total"] += 1

            status = handoff.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            priority = handoff.priority.value
            stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1

            if handoff.completed_at and handoff.created_at:
                try:
                    created = datetime.fromisoformat(handoff.created_at.replace("Z", "+00:00"))
                    completed = datetime.fromisoformat(handoff.completed_at.replace("Z", "+00:00"))
                    hours = (completed - created).total_seconds() / 3600
                    completion_times.append(hours)
                except Exception:
                    pass

        if completion_times:
            stats["avg_completion_time_hours"] = sum(completion_times) / len(completion_times)

        return stats


def create_handoff_manager(forge_root: Path | None = None) -> HandoffManager:
    """Create a handoff manager instance."""
    if forge_root is None:
        forge_root = Path(os.environ.get("FORGE_ROOT", "."))

    handoff_dir = forge_root / ".forge/handoffs"
    return HandoffManager(handoff_dir)

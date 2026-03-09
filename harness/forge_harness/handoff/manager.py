"""Handoff manager for creating and tracking handoffs."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schema import Handoff, HandoffPriority, HandoffStatus

_PRIORITY_ORDER = {
    HandoffPriority.CRITICAL: 0,
    HandoffPriority.HIGH: 1,
    HandoffPriority.MEDIUM: 2,
    HandoffPriority.LOW: 3,
}


class HandoffManager:
    """Manage handoff lifecycle and persistence."""

    def __init__(self, handoff_dir: Path):
        self.handoff_dir = Path(handoff_dir)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, handoff_id: str) -> Path:
        return self.handoff_dir / f"{handoff_id}.json"

    async def create(self, from_agent: str, to_agent: str, task: str, **kwargs: Any) -> Handoff:
        handoff = Handoff.new(from_agent=from_agent, to_agent=to_agent, task=task, **kwargs)
        await self.update(handoff)
        return handoff

    async def get(self, handoff_id: str) -> Handoff | None:
        path = self._path(handoff_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Handoff.from_dict(data)

    async def update(self, handoff: Handoff) -> Handoff:
        async with self._lock:
            path = self._path(handoff.id)
            path.write_text(handoff.to_json())
        return handoff

    async def list_for_agent(
        self,
        agent: str,
        *,
        as_sender: bool = False,
        status: HandoffStatus | None = None,
    ) -> list[Handoff]:
        handoffs: list[Handoff] = []
        for path in sorted(self.handoff_dir.glob("*.json")):
            data = json.loads(path.read_text())
            handoff = Handoff.from_dict(data)
            if as_sender:
                if handoff.from_agent != agent:
                    continue
            else:
                if handoff.to_agent != agent:
                    continue
            if status is not None and handoff.status != status:
                continue
            handoffs.append(handoff)

        handoffs.sort(key=lambda h: _PRIORITY_ORDER.get(h.priority, 99))
        return handoffs

    async def list_pending(self) -> list[Handoff]:
        handoffs: list[Handoff] = []
        for path in sorted(self.handoff_dir.glob("*.json")):
            data = json.loads(path.read_text())
            handoff = Handoff.from_dict(data)
            if handoff.status == HandoffStatus.PENDING:
                handoffs.append(handoff)
        handoffs.sort(key=lambda h: _PRIORITY_ORDER.get(h.priority, 99))
        return handoffs

    async def accept(self, handoff_id: str, agent: str) -> Handoff | None:
        handoff = await self.get(handoff_id)
        if handoff is None:
            return None
        if handoff.to_agent and handoff.to_agent != agent:
            raise ValueError("handoff not addressed to this agent")
        if handoff.status != HandoffStatus.PENDING:
            raise ValueError("handoff not pending")
        handoff.status = HandoffStatus.ACCEPTED
        handoff.to_agent = agent
        handoff.accepted_at = datetime.now(UTC).isoformat()
        await self.update(handoff)
        return handoff

    async def reject(
        self, handoff_id: str, agent: str, reason: str | None = None
    ) -> Handoff | None:
        handoff = await self.get(handoff_id)
        if handoff is None:
            return None
        if handoff.to_agent and handoff.to_agent != agent:
            raise ValueError("handoff not addressed to this agent")
        handoff.status = HandoffStatus.REJECTED
        handoff.notes = reason
        handoff.completed_at = datetime.now(UTC).isoformat()
        await self.update(handoff)
        return handoff

    async def start(self, handoff_id: str) -> Handoff | None:
        handoff = await self.get(handoff_id)
        if handoff is None:
            return None
        if handoff.status != HandoffStatus.ACCEPTED:
            return handoff
        handoff.status = HandoffStatus.IN_PROGRESS
        await self.update(handoff)
        return handoff

    async def complete(
        self,
        handoff_id: str,
        *,
        result: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> Handoff | None:
        handoff = await self.get(handoff_id)
        if handoff is None:
            return None
        handoff.status = HandoffStatus.COMPLETED
        handoff.completed_at = datetime.now(UTC).isoformat()
        if result is not None:
            handoff.result = result
        if notes is not None:
            handoff.notes = notes
        await self.update(handoff)
        return handoff

    async def get_stats(self) -> dict[str, Any]:
        paths = list(self.handoff_dir.glob("*.json"))
        if not paths:
            return {
                "total": 0,
                "by_status": {},
                "by_priority": {},
                "avg_completion_time_hours": 0,
            }

        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        completion_times: list[float] = []

        for path in paths:
            data = json.loads(path.read_text())
            handoff = Handoff.from_dict(data)
            by_status[handoff.status.value] = by_status.get(handoff.status.value, 0) + 1
            by_priority[handoff.priority.value] = by_priority.get(handoff.priority.value, 0) + 1
            if handoff.completed_at and handoff.created_at:
                try:
                    created = datetime.fromisoformat(handoff.created_at)
                    completed = datetime.fromisoformat(handoff.completed_at)
                    completion_times.append((completed - created).total_seconds() / 3600)
                except ValueError:
                    pass

        avg_completion = 0
        if completion_times:
            avg_completion = sum(completion_times) / len(completion_times)

        return {
            "total": len(paths),
            "by_status": by_status,
            "by_priority": by_priority,
            "avg_completion_time_hours": avg_completion,
        }


def create_handoff_manager(forge_root: Path | None = None) -> HandoffManager:
    if forge_root is None:
        env_root = os.getenv("FORGE_ROOT")
        forge_root = Path(env_root) if env_root else Path.cwd()
    handoff_dir = Path(forge_root) / ".forge/handoffs"
    return HandoffManager(handoff_dir)

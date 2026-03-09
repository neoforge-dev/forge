"""Storage helpers for handoff documents."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_harness.handoff.schema import Handoff


class HandoffStore:
    """Persist handoff documents to disk and index."""

    def __init__(self, root: Path):
        self.root = root
        self.handoff_dir = self.root / ".forge" / "handoffs"
        self.index_path = self.handoff_dir / "index.json"
        self.handoff_dir.mkdir(parents=True, exist_ok=True)

    def save(self, handoff: Handoff) -> Path:
        path = self.handoff_dir / f"{handoff.id}.json"
        path.write_text(handoff.to_json())
        self._update_index(handoff)
        return path

    def load(self, handoff_id: str) -> Handoff | None:
        from forge_harness.handoff.schema import Handoff

        path = self.handoff_dir / f"{handoff_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Handoff.from_dict(data)

    def _update_index(self, handoff: Handoff) -> None:
        index = self._read_index()
        index[handoff.id] = {
            "id": handoff.id,
            "domain": handoff.domain,
            "project": handoff.project,
            "from_agent": handoff.from_agent,
            "to_agent": handoff.to_agent,
            "status": handoff.status,
            "priority": handoff.priority,
            "description": handoff.description[:200],
            "created_at": handoff.created_at,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.index_path.write_text(json.dumps(index, indent=2))

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text())
        except json.JSONDecodeError:
            return {}

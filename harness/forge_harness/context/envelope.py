"""
Context Envelope System for FORGE v3

Stores handoff data in v3 SQLite database.
Provides context envelopes for agent handoffs.
"""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ContextEnvelope:
    """Context envelope for agent handoffs."""

    agent_id: str
    session_id: str
    context_data: dict[str, Any]
    handoff_reason: str
    timestamp: str
    priority: str = "normal"
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert envelope to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert envelope to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextEnvelope":
        """Create envelope from dictionary."""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "ContextEnvelope":
        """Create envelope from JSON string."""
        return cls.from_dict(json.loads(json_str))


class ContextStore:
    """SQLite-based context store for v3."""

    def __init__(self, db_path: str | None = None):
        """Initialize context store.

        Args:
            db_path: Path to SQLite database. Defaults to v3 database.
        """
        if db_path is None:
            # Use the canonical v3 database path
            import os

            forge_root = os.environ.get("FORGE_ROOT", str(Path.home() / "FORGE"))
            db_path = str(Path(forge_root) / ".forge" / "forge-v3.db")

        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create context_envelopes table if not exists."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_envelopes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    handoff_reason TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    priority TEXT DEFAULT 'normal',
                    metadata TEXT,
                    retrieved BOOLEAN DEFAULT FALSE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def store(self, envelope: ContextEnvelope) -> int:
        """Store context envelope.

        Args:
            envelope: Context envelope to store

        Returns:
            ID of stored envelope
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO context_envelopes 
                (agent_id, session_id, context_data, handoff_reason, timestamp, priority, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.agent_id,
                    envelope.session_id,
                    json.dumps(envelope.context_data),
                    envelope.handoff_reason,
                    envelope.timestamp,
                    envelope.priority,
                    json.dumps(envelope.metadata) if envelope.metadata else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid if cursor.lastrowid is not None else -1

    def retrieve(
        self, agent_id: str, session_id: str | None = None
    ) -> ContextEnvelope | None:
        """Retrieve context envelope for agent.

        Args:
            agent_id: Agent ID
            session_id: Optional session ID filter

        Returns:
            Most recent context envelope or None
        """
        with sqlite3.connect(self.db_path) as conn:
            if session_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM context_envelopes 
                    WHERE agent_id = ? AND session_id = ? AND retrieved = FALSE
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (agent_id, session_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM context_envelopes 
                    WHERE agent_id = ? AND retrieved = FALSE
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (agent_id,),
                )

            row = cursor.fetchone()
            if not row:
                return None

            # Mark as retrieved
            conn.execute("UPDATE context_envelopes SET retrieved = TRUE WHERE id = ?", (row[0],))
            conn.commit()

            return ContextEnvelope(
                agent_id=row[1],
                session_id=row[2],
                context_data=json.loads(row[3]),
                handoff_reason=row[4],
                timestamp=row[5],
                priority=row[6],
                metadata=json.loads(row[7]) if row[7] else None,
            )

    def list_pending(self, agent_id: str | None = None) -> list[ContextEnvelope]:
        """List pending context envelopes.

        Args:
            agent_id: Optional agent ID filter

        Returns:
            List of pending envelopes
        """
        with sqlite3.connect(self.db_path) as conn:
            if agent_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM context_envelopes 
                    WHERE agent_id = ? AND retrieved = FALSE
                    ORDER BY created_at DESC
                    """,
                    (agent_id,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM context_envelopes 
                    WHERE retrieved = FALSE
                    ORDER BY created_at DESC
                    """
                )

            rows = cursor.fetchall()
            return [
                ContextEnvelope(
                    agent_id=row[1],
                    session_id=row[2],
                    context_data=json.loads(row[3]),
                    handoff_reason=row[4],
                    timestamp=row[5],
                    priority=row[6],
                    metadata=json.loads(row[7]) if row[7] else None,
                )
                for row in rows
            ]

    def cleanup_old(self, days: int = 7) -> int:
        """Clean up old retrieved envelopes.

        Args:
            days: Age in days to clean up

        Returns:
            Number of records deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM context_envelopes 
                WHERE retrieved = TRUE 
                AND created_at < datetime('now', '-{days} days')
                """
            )
            conn.commit()
            return cursor.rowcount


def create_handoff_envelope(
    agent_id: str,
    context_pct: float,
    current_task: str,
    blockers: list[str] | None = None,
    notes: str | None = None,
    priority: str = "normal",
) -> ContextEnvelope:
    """Create a handoff envelope from agent context.

    Args:
        agent_id: Agent ID
        context_pct: Context window percentage
        current_task: Current task description
        blockers: List of blockers
        notes: Additional notes
        priority: Priority level

    Returns:
        Context envelope ready for storage
    """
    return ContextEnvelope(
        agent_id=agent_id,
        session_id=f"{agent_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        context_data={
            "context_pct": context_pct,
            "current_task": current_task,
            "blockers": blockers or [],
            "notes": notes,
            "handoff_time": datetime.now().isoformat(),
        },
        handoff_reason="context_threshold" if context_pct > 70 else "task_complete",
        timestamp=datetime.now().isoformat(),
        priority=priority,
        metadata={"source": "v2_handoff", "version": "3.0"},
    )


def migrate_v2_handoff_to_v3(v2_handoff_path: str, store: ContextStore) -> int:
    """Migrate v2 handoff file to v3 context envelope.

    Args:
        v2_handoff_path: Path to v2 handoff file
        store: Context store instance

    Returns:
        Envelope ID
    """
    from pathlib import Path

    if not Path(v2_handoff_path).exists():
        raise FileNotFoundError(f"Handoff file not found: {v2_handoff_path}")

    with open(v2_handoff_path) as f:
        v2_data = json.load(f)

    envelope = ContextEnvelope(
        agent_id=v2_data.get("agent", "unknown"),
        session_id=v2_data.get("session_id", "migrated"),
        context_data=v2_data.get("context", {}),
        handoff_reason=v2_data.get("reason", "migrated_from_v2"),
        timestamp=v2_data.get("timestamp", datetime.now().isoformat()),
        priority=v2_data.get("priority", "normal"),
        metadata={"source": "v2_migration", "original_path": v2_handoff_path},
    )

    return store.store(envelope)

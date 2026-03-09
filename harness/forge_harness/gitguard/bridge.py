"""Bridge approval queue to v3 SQLite — store approvals in v3 DB.

Phase 1 integration: GitGuard (approval queue) with v3 approvals table.
Backward compatible: v2 JSON file storage remains default; v3 is opt-in
via FORGE_APPROVALS_V3=1 or create_approval_queue(..., storage=v3_storage).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStorage,
    ApprovalType,
)
from ..logging_config import get_logger

logger = get_logger(__name__)

# Default v3 DB path (matches cmd/forge-v3/migrate.go default)
DEFAULT_V3_DB_PATH = ".forge/forge-v3.db"

# Schema for approval queue table (GitGuard / harness).
# Use a dedicated table name to avoid conflicting with Go v3 approvals schema if present.
_APPROVALS_TABLE = "approval_queue"
_APPROVALS_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_APPROVALS_TABLE} (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    metadata TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    tier TEXT NOT NULL,
    risk_score REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    approved_by TEXT,
    resolved_at TEXT,
    resolution_reason TEXT,
    workflow_checkpoint TEXT,
    notification_ids TEXT NOT NULL,
    tags TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_queue_status ON {_APPROVALS_TABLE}(status);
CREATE INDEX IF NOT EXISTS idx_approval_queue_domain ON {_APPROVALS_TABLE}(domain);
CREATE INDEX IF NOT EXISTS idx_approval_queue_created_at ON {_APPROVALS_TABLE}(created_at);
"""


def get_v3_db_path() -> Path:
    """Return v3 SQLite database path (FORGE_ROOT/.forge/forge-v3.db)."""
    forge_root = os.environ.get("FORGE_ROOT")
    if forge_root:
        return Path(forge_root) / ".forge" / "forge-v3.db"
    return Path.cwd() / DEFAULT_V3_DB_PATH


def _ensure_approvals_table(conn: sqlite3.Connection) -> None:
    """Create approvals table and indexes if not present."""
    conn.executescript(_APPROVALS_SCHEMA)
    conn.commit()


def _request_to_row(request: ApprovalRequest) -> tuple[Any, ...]:
    """Convert ApprovalRequest to DB row tuple."""
    return (
        request.id,
        request.type.value,
        request.domain,
        request.title,
        request.description,
        json.dumps(request.metadata),
        request.status.value,
        request.priority.value,
        request.tier,
        request.risk_score,
        request.created_at.isoformat(),
        request.expires_at.isoformat() if request.expires_at else None,
        request.approved_by,
        request.resolved_at.isoformat() if request.resolved_at else None,
        request.resolution_reason,
        request.workflow_checkpoint,
        json.dumps(request.notification_ids),
        json.dumps(request.tags),
        request.resolved_at.isoformat() if request.resolved_at else request.created_at.isoformat(),
    )


def _row_to_request(row: tuple[Any, ...]) -> ApprovalRequest:
    """Convert DB row to ApprovalRequest."""
    (
        id_,
        type_,
        domain,
        title,
        description,
        metadata_json,
        status,
        priority,
        tier,
        risk_score,
        created_at,
        expires_at,
        approved_by,
        resolved_at,
        resolution_reason,
        workflow_checkpoint,
        notification_ids_json,
        tags_json,
        _,
    ) = row
    metadata = json.loads(metadata_json) if metadata_json else {}
    notification_ids = json.loads(notification_ids_json) if notification_ids_json else []
    tags = json.loads(tags_json) if tags_json else []
    return ApprovalRequest(
        id=id_,
        type=ApprovalType(type_),
        domain=domain,
        title=title,
        description=description,
        metadata=metadata,
        status=ApprovalStatus(status),
        priority=ApprovalPriority(priority),
        tier=tier,
        risk_score=float(risk_score),
        created_at=__parse_dt(created_at),
        expires_at=__parse_dt(expires_at),
        approved_by=approved_by,
        resolved_at=__parse_dt(resolved_at),
        resolution_reason=resolution_reason,
        workflow_checkpoint=workflow_checkpoint,
        notification_ids=notification_ids,
        tags=tags,
    )


def __parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class SQLiteApprovalStorage(ApprovalStorage):
    """Store approval requests in v3 SQLite.

    Uses the same DB path as the v3 daemon (.forge/forge-v3.db by default).
    Table approvals is created on first use (CREATE TABLE IF NOT EXISTS).
    Async methods run SQLite calls in a thread to avoid blocking the event loop.
    """

    def __init__(self, db_path: Path | str | None = None):
        """Initialize v3 approval storage.

        Args:
            db_path: SQLite database path. Defaults to get_v3_db_path().
        """
        self.db_path = Path(db_path) if db_path else get_v3_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            _ensure_approvals_table(self._conn)
        return self._conn

    def _save_sync(self, request: ApprovalRequest) -> None:
        conn = self._connect()
        row = _request_to_row(request)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_APPROVALS_TABLE} (
                id, type, domain, title, description, metadata, status, priority,
                tier, risk_score, created_at, expires_at, approved_by, resolved_at,
                resolution_reason, workflow_checkpoint, notification_ids, tags, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        conn.commit()
        logger.debug("SQLiteApprovalStorage: saved approval %s", request.id)

    def _load_sync(self, request_id: str) -> ApprovalRequest | None:
        conn = self._connect()
        cur = conn.execute(
            f"SELECT * FROM {_APPROVALS_TABLE} WHERE id = ?",
            (request_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_request(tuple(row))

    def _list_all_sync(self) -> list[ApprovalRequest]:
        conn = self._connect()
        cur = conn.execute(
            f"SELECT * FROM {_APPROVALS_TABLE} ORDER BY created_at DESC"
        )
        return [_row_to_request(tuple(r)) for r in cur.fetchall()]

    def _delete_sync(self, request_id: str) -> bool:
        conn = self._connect()
        cur = conn.execute(f"DELETE FROM {_APPROVALS_TABLE} WHERE id = ?", (request_id,))
        conn.commit()
        return cur.rowcount > 0

    async def save(self, request: ApprovalRequest) -> None:
        await asyncio.to_thread(self._save_sync, request)

    async def load(self, request_id: str) -> ApprovalRequest | None:
        return await asyncio.to_thread(self._load_sync, request_id)

    async def list_all(self) -> list[ApprovalRequest]:
        return await asyncio.to_thread(self._list_all_sync)

    async def delete(self, request_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, request_id)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("SQLiteApprovalStorage: closed connection to %s", self.db_path)


def create_v3_approval_storage(
    db_path: Path | str | None = None,
) -> SQLiteApprovalStorage:
    """Create SQLiteApprovalStorage for the v3 SQLite approvals table.

    Args:
        db_path: Optional DB path. Defaults to get_v3_db_path().

    Returns:
        SQLiteApprovalStorage instance (can be passed to ApprovalQueueHarness).
    """
    return SQLiteApprovalStorage(db_path=db_path)

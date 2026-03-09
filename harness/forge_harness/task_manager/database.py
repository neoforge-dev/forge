"""V3 Task Database - SQLite-backed state management."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


class TaskDatabase:
    """SQLite database for V3 task state management."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or Path(".forge/v3/state.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    @contextmanager
    def _connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_database(self):
        """Initialize database schema."""
        schema = """
        -- Tasks table - single source of truth
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT CHECK(priority IN ('critical', 'high', 'medium', 'low')) DEFAULT 'medium',
            status TEXT CHECK(status IN ('pending', 'assigned', 'in_progress', 'completed', 'failed', 'cancelled')) DEFAULT 'pending',
            assigned_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            assigned_at TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            attempt_count INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            dispatch_id TEXT UNIQUE,
            source_file TEXT,
            result_path TEXT,
            metadata JSON
        );

        CREATE TABLE IF NOT EXISTS dispatches (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            claimed_at TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT CHECK(status IN ('pending', 'claimed', 'completed', 'failed', 'expired')) DEFAULT 'pending',
            result_summary TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            agent TEXT PRIMARY KEY,
            status TEXT CHECK(status IN ('idle', 'busy', 'error', 'offline')) DEFAULT 'idle',
            current_task_id TEXT,
            last_heartbeat TIMESTAMP,
            context_usage_percent INTEGER,
            capabilities JSON,
            FOREIGN KEY (current_task_id) REFERENCES tasks(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT NOT NULL,
            task_id TEXT,
            agent TEXT,
            details JSON,
            source_ip TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_assigned_agent ON tasks(assigned_agent);
        CREATE INDEX IF NOT EXISTS idx_dispatches_task ON dispatches(task_id);
        CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id);
        """

        with self._connection() as conn:
            conn.executescript(schema)

    def create_task(self, title: str, description: str = "",
                    priority: str = "medium", source_file: str | None = None,
                    result_path: str | None = None,
                    metadata: dict | None = None) -> str:
        """Create a new task. Returns task ID."""
        task_id = f"TC-{uuid.uuid4().hex[:8]}"
        dispatch_id = f"d-{uuid.uuid4().hex[:12]}"

        with self._connection() as conn:
            conn.execute("""
                INSERT INTO tasks (id, title, description, priority, status,
                                 dispatch_id, source_file, result_path, metadata)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """, (task_id, title, description, priority, dispatch_id,
                  source_file, result_path, json.dumps(metadata) if metadata else None))

            self._log_event(conn, 'task_created', task_id, None,
                          {'title': title, 'priority': priority})

        return task_id

    def claim_task(self, task_id: str, agent: str) -> bool:
        """Claim a pending task for an agent. Returns True if successful."""
        with self._connection() as conn:
            # Check if task is pending
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task_id,)
            ).fetchone()

            if not row or row['status'] != 'pending':
                return False

            # Atomic claim
            now = datetime.utcnow().isoformat()
            conn.execute("""
                UPDATE tasks 
                SET status = 'assigned', assigned_agent = ?, assigned_at = ?,
                    attempt_count = attempt_count + 1
                WHERE id = ? AND status = 'pending'
            """, (agent, now, task_id))

            if conn.total_changes == 0:
                return False

            # Create dispatch record
            dispatch_id = f"d-{uuid.uuid4().hex[:12]}"
            conn.execute("""
                INSERT INTO dispatches (id, task_id, agent, claimed_at, status)
                VALUES (?, ?, ?, ?, 'claimed')
            """, (dispatch_id, task_id, agent, now))

            # Update agent state
            conn.execute("""
                INSERT OR REPLACE INTO agent_state (agent, status, current_task_id, last_heartbeat)
                VALUES (?, 'busy', ?, ?)
            """, (agent, task_id, now))

            self._log_event(conn, 'task_claimed', task_id, agent)
            return True

    def complete_task(self, task_id: str, agent: str,
                      result_summary: str = "") -> bool:
        """Mark a task as completed."""
        with self._connection() as conn:
            now = datetime.utcnow().isoformat()

            conn.execute("""
                UPDATE tasks 
                SET status = 'completed', completed_at = ?
                WHERE id = ? AND assigned_agent = ?
            """, (now, task_id, agent))

            if conn.total_changes == 0:
                return False

            conn.execute("""
                UPDATE dispatches 
                SET completed_at = ?, status = 'completed', result_summary = ?
                WHERE task_id = ? AND agent = ?
            """, (now, result_summary, task_id, agent))

            # Free agent
            conn.execute("""
                UPDATE agent_state 
                SET status = 'idle', current_task_id = NULL
                WHERE agent = ?
            """, (agent,))

            self._log_event(conn, 'task_completed', task_id, agent,
                          {'summary': result_summary})
            return True

    def get_pending_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get pending tasks ordered by priority."""
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks 
                WHERE status = 'pending'
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        ELSE 3
                    END,
                    created_at ASC
                LIMIT ?
            """, (limit,)).fetchall()

            return [dict(row) for row in rows]

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Get current status of a task."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_agent_tasks(self, agent: str) -> list[dict[str, Any]]:
        """Get all tasks assigned to an agent."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assigned_agent = ? ORDER BY created_at DESC",
                (agent,)
            ).fetchall()
            return [dict(row) for row in rows]

    def _log_event(self, conn: sqlite3.Connection, event_type: str,
                   task_id: str | None, agent: str | None,
                   details: dict | None = None):
        """Log an audit event."""
        conn.execute("""
            INSERT INTO audit_log (event_type, task_id, agent, details)
            VALUES (?, ?, ?, ?)
        """, (event_type, task_id, agent, json.dumps(details) if details else None))


# Singleton instance
_db_instance: TaskDatabase | None = None


def get_task_db() -> TaskDatabase:
    """Get singleton task database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = TaskDatabase()
    return _db_instance

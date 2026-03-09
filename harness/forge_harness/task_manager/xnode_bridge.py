"""XNode Bridge - Synchronizes file-based XNode with V3 SQLite.

This bridge allows V2 (file-based) and V3 (SQLite) to coexist during
the transition period. Messages flow bidirectionally:

V2 File → Bridge → V3 SQLite
V3 SQLite → Bridge → V2 File

The bridge ensures no messages are lost during the transition.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any, Optional

from .database import get_task_db

logger = logging.getLogger(__name__)


@dataclass
class XNodeMessage:
    """Represents an XNode message."""
    id: str
    schema_version: str
    message_id: str
    correlation_id: str
    idempotency_key: str
    sent_at: str
    expires_at: str | None
    source_node: str
    source_agent: str
    target_node: str
    target_agent: str
    priority: str
    msg_type: str
    requires_ack: bool
    payload: dict[str, Any]

    @classmethod
    def from_file(cls, file_path: Path) -> Optional["XNodeMessage"]:
        """Parse XNode message from JSON file."""
        try:
            data = json.loads(file_path.read_text())
            return cls(
                id=data.get("id", ""),
                schema_version=data.get("schema_version", "1.0"),
                message_id=data.get("message_id", ""),
                correlation_id=data.get("correlation_id", ""),
                idempotency_key=data.get("idempotency_key", ""),
                sent_at=data.get("sent_at", ""),
                expires_at=data.get("expires_at"),
                source_node=data.get("source", {}).get("node", ""),
                source_agent=data.get("source", {}).get("agent", ""),
                target_node=data.get("target", {}).get("node", ""),
                target_agent=data.get("target", {}).get("agent", ""),
                priority=data.get("priority", "medium"),
                msg_type=data.get("type", ""),
                requires_ack=data.get("requires_ack", False),
                payload=data.get("payload", {})
            )
        except Exception as e:
            logger.error(f"Failed to parse XNode message from {file_path}: {e}")
            return None


class XNodeBridge:
    """Bidirectional bridge between file-based XNode and V3 SQLite."""

    def __init__(
        self,
        forge_root: Path | None = None,
        poll_interval: float = 5.0
    ):
        self.forge_root = forge_root or Path.cwd()
        self.poll_interval = poll_interval
        self.xnode_dir = self.forge_root / ".forge" / "xnode"
        self.inbox_dir = self.xnode_dir / "lead-inbox"
        self.outbox_dir = self.xnode_dir / "lead-outbox"
        self.acks_dir = self.xnode_dir / "acks"

        self.db = get_task_db()
        self._stop_event = Event()
        self._thread: Thread | None = None

        # Ensure directories exist
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.acks_dir.mkdir(parents=True, exist_ok=True)

        # Track processed messages to avoid duplicates
        self._processed_messages: set = set()
        self._load_processed_cache()

    def _load_processed_cache(self):
        """Load already-processed message IDs from database."""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.execute(
            "SELECT details FROM audit_log WHERE event_type = 'xnode_message_processed'"
        )
        for row in cursor.fetchall():
            try:
                details = json.loads(row[0])
                self._processed_messages.add(details.get("message_id"))
            except:
                pass
        conn.close()

    def _is_processed(self, message_id: str) -> bool:
        """Check if message was already processed."""
        return message_id in self._processed_messages

    def _mark_processed(self, message_id: str):
        """Mark message as processed."""
        self._processed_messages.add(message_id)
        conn = sqlite3.connect(self.db.db_path)
        conn.execute("""
            INSERT INTO audit_log (event_type, details)
            VALUES (?, ?)
        """, ("xnode_message_processed", json.dumps({"message_id": message_id})))
        conn.commit()
        conn.close()

    def _process_inbox_file(self, file_path: Path) -> bool:
        """Process a single inbox file into V3 database."""
        message = XNodeMessage.from_file(file_path)
        if not message:
            return False

        # Check for duplicates
        if self._is_processed(message.message_id):
            logger.debug(f"Skipping already-processed message: {message.message_id}")
            # Archive the file
            archive_dir = self.inbox_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            file_path.rename(archive_dir / file_path.name)
            return True

        # Check if task already exists
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.execute(
            "SELECT id FROM tasks WHERE metadata LIKE ?",
            (f'%"xnode_message_id": "{message.message_id}"%',)
        )
        if cursor.fetchone():
            logger.debug(f"Task already exists for message: {message.message_id}")
            self._mark_processed(message.message_id)
            conn.close()
            return True

        conn.close()

        # Create task from XNode message
        task_id = self.db.create_task(
            title=message.payload.get("summary", "XNode Task")[:100],
            description=message.payload.get("summary", ""),
            priority=message.priority if message.priority in ["critical", "high", "medium", "low"] else "medium",
            source_file=str(file_path),
            result_path=f".forge/heartbeat/results/{message.message_id}.md",
            metadata={
                "xnode_message_id": message.message_id,
                "xnode_correlation_id": message.correlation_id,
                "source_node": message.source_node,
                "source_agent": message.source_agent,
                "target_node": message.target_node,
                "target_agent": message.target_agent,
                "xnode_type": message.msg_type,
                "migrated_from_xnode": True
            }
        )

        self._mark_processed(message.message_id)
        logger.info(f"Created V3 task {task_id} from XNode message {message.message_id}")

        # Archive the file
        archive_dir = self.inbox_dir / "archive"
        archive_dir.mkdir(exist_ok=True)
        file_path.rename(archive_dir / file_path.name)

        return True

    def _scan_inbox(self):
        """Scan inbox directory for new messages."""
        if not self.inbox_dir.exists():
            return

        for jsonl_file in self.inbox_dir.glob("*.jsonl"):
            # Process each line in jsonl file
            try:
                with open(jsonl_file) as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue

                        # Write individual message to temp file
                        temp_file = self.inbox_dir / f"temp_{jsonl_file.stem}_{line_num}.json"
                        temp_file.write_text(line)

                        # Process it
                        self._process_inbox_file(temp_file)

                        # Clean up temp file
                        temp_file.unlink(missing_ok=True)

                # Archive the jsonl file
                archive_dir = self.inbox_dir / "archive"
                archive_dir.mkdir(exist_ok=True)
                jsonl_file.rename(archive_dir / jsonl_file.name)

            except Exception as e:
                logger.error(f"Failed to process inbox file {jsonl_file}: {e}")

        # Also check for individual json files
        for json_file in self.inbox_dir.glob("*.json"):
            if json_file.name.startswith("temp_"):
                continue
            self._process_inbox_file(json_file)

    def _write_outbox(self):
        """Write V3 tasks to outbox for V2 nodes."""
        # Find tasks that need to be sent to other nodes
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.execute("""
            SELECT id, title, metadata, assigned_agent 
            FROM tasks 
            WHERE metadata LIKE '%"needs_xnode_dispatch": true%'
            AND status = 'assigned'
        """)

        for row in cursor.fetchall():
            try:
                metadata = json.loads(row[2] or "{}")
                target_node = metadata.get("target_node", "prya")

                # Create XNode message
                message = {
                    "schema_version": "1.0",
                    "message_id": f"v3-{row[0]}",
                    "correlation_id": row[0],
                    "idempotency_key": f"v3-dispatch-{row[0]}",
                    "sent_at": datetime.now(UTC).isoformat(),
                    "source": {"node": "sati", "agent": "orchestrator"},
                    "target": {"node": target_node, "agent": row[3] or "any"},
                    "priority": "medium",
                    "type": "task.dispatch",
                    "requires_ack": True,
                    "payload": {
                        "task_id": row[0],
                        "summary": row[1],
                        "v3_task": True
                    }
                }

                # Write to outbox
                outbox_file = self.outbox_dir / f"{target_node}.jsonl"
                with open(outbox_file, "a") as f:
                    f.write(json.dumps(message) + "\n")

                # Mark as dispatched
                conn.execute("""
                    UPDATE tasks 
                    SET metadata = REPLACE(metadata, '"needs_xnode_dispatch": true', '"xnode_dispatched": true')
                    WHERE id = ?
                """, (row[0],))

                logger.info(f"Wrote task {row[0]} to XNode outbox for {target_node}")

            except Exception as e:
                logger.error(f"Failed to write outbox for task {row[0]}: {e}")

        conn.commit()
        conn.close()

    def _bridge_loop(self):
        """Main bridge loop."""
        logger.info("XNode Bridge started")

        while not self._stop_event.is_set():
            try:
                # File → SQLite
                self._scan_inbox()

                # SQLite → File
                self._write_outbox()

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")

            # Wait for next poll
            self._stop_event.wait(self.poll_interval)

        logger.info("XNode Bridge stopped")

    def start(self):
        """Start the bridge in background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Bridge already running")
            return

        self._stop_event.clear()
        self._thread = Thread(target=self._bridge_loop, name="XNodeBridge")
        self._thread.daemon = True
        self._thread.start()
        logger.info("XNode Bridge thread started")

    def stop(self):
        """Stop the bridge."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("XNode Bridge stopped")

    def is_running(self) -> bool:
        """Check if bridge is running."""
        return self._thread is not None and self._thread.is_alive()

    def process_once(self):
        """Process one cycle (for testing/debugging)."""
        self._scan_inbox()
        self._write_outbox()


# Singleton instance
_bridge_instance: XNodeBridge | None = None


def get_xnode_bridge() -> XNodeBridge:
    """Get singleton XNode bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = XNodeBridge()
    return _bridge_instance

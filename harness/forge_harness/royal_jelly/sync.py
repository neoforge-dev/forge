"""
Royal Jelly Sync — Context Synchronization between Filesystem and v3 SQLite.
========================================================================

Estibles the Filesystem as the Source of Truth (SoT) and synchronizes
context artifacts to the v3 SQLite database for high-performance indexing
and Go orchestrator access.

Usage:
    python3 -m forge_harness.royal_jelly.sync
"""

import hashlib
import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("forge_harness.royal_jelly.sync")


def get_v3_db_path() -> Path:
    """Return v3 SQLite database path (FORGE_ROOT/.forge/forge-v3.db)."""
    forge_root = os.environ.get("FORGE_ROOT", ".")
    return Path(forge_root) / ".forge" / "forge-v3.db"


class RoyalJellySync:
    """Synchronizes domain context from filesystem to v3 SQLite."""

    def __init__(self, db_path: Path | None = None, forge_root: Path | None = None):
        self.forge_root = Path(forge_root or os.environ.get("FORGE_ROOT", "."))
        self.db_path = db_path or (self.forge_root / ".forge" / "forge-v3.db")
        self.context_root = self.forge_root / ".forge" / "context"
        self._conn: sqlite3.Connection | None = None
        self._registered_agents: set[str] = set()

    def _connect(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            if not self.db_path.exists():
                logger.warning("v3 database not found at %s. Creating directory...", self.db_path)
                self.db_path.parent.mkdir(parents=True, exist_ok=True)

            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            # Ensure table exists (minimal schema for Phase 1)
            self._ensure_schema(self._conn)
            # Load registered agents
            self._load_registered_agents()
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection):
        """Ensure context_artifacts table exists with all required columns."""
        # Ensure row_factory is set for this connection
        conn.row_factory = sqlite3.Row

        conn.execute("""
        CREATE TABLE IF NOT EXISTS context_artifacts (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            project TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            content TEXT,
            hash TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """)

        # Add columns if they are missing (for existing databases)
        cursor = conn.execute("PRAGMA table_info(context_artifacts)")
        columns = [row["name"] for row in cursor.fetchall()]

        if "hash" not in columns:
            logger.info("Adding 'hash' column to context_artifacts")
            conn.execute("ALTER TABLE context_artifacts ADD COLUMN hash TEXT")

        if "updated_at" not in columns:
            logger.info("Adding 'updated_at' column to context_artifacts")
            conn.execute("ALTER TABLE context_artifacts ADD COLUMN updated_at TEXT")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_artifacts_domain_project ON context_artifacts (domain, project, updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_artifacts_agent ON context_artifacts (agent_id, updated_at)"
        )
        conn.commit()

    def _load_registered_agents(self):
        """Load set of all registered agent IDs from agents table."""
        try:
            cursor = self._conn.execute("SELECT id FROM agents")
            self._registered_agents = {row["id"] for row in cursor.fetchall()}
        except sqlite3.Error:
            # Table might not exist yet
            self._registered_agents = set()

    def _calculate_hash(self, content: str) -> str:
        """Calculate SHA256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _get_file_mtime(self, file_path: Path) -> datetime:
        """Get file modification time as UTC datetime."""
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime, tz=UTC)

    def _parse_db_time(self, db_time_str: str) -> datetime:
        """Parse ISO format time string from DB to UTC datetime."""
        if not db_time_str:
            return datetime.fromtimestamp(0, tz=UTC)
        try:
            # Handle potential Z or +00:00
            clean_time = db_time_str.replace("Z", "+00:00")
            return datetime.fromisoformat(clean_time)
        except ValueError:
            return datetime.fromtimestamp(0, tz=UTC)

    def _parse_agent_id(self, domain: str, content: str) -> str:
        """Extract agent_id from Markdown lead-context."""
        agent_id = None

        # Look for **Domain Lead:** `agent_id`
        match = re.search(r"\*\*Domain Lead:\*\* `([^`]+)`", content)
        if match:
            agent_id = match.group(1)

        # Look for **Lead:** agent_id
        if not agent_id:
            match = re.search(r"\*\*Lead:\*\* ([^\s(]+)", content)
            if match:
                agent_id = match.group(1)

        if not agent_id or agent_id == "unassigned":
            # Try to find an agent whose name matches the domain
            for aid in self._registered_agents:
                if domain in aid:
                    return aid
            return f"{domain}.lead"

        return agent_id

    def sync_files_to_db(self, force: bool = False):
        """Scan .forge/context and update SQLite.
        
        Args:
            force: If True, overwrite DB even if file is older (SoT mode).
        """
        conn = self._connect()
        if not self.context_root.exists():
            logger.warning("Context root not found: %s", self.context_root)
            return

        logger.info("Starting Royal Jelly Sync: Files -> SQLite (%s)", self.db_path)

        synced_count = 0
        skipped_count = 0
        for domain_dir in self.context_root.iterdir():
            if not domain_dir.is_dir():
                continue

            domain = domain_dir.name

            # Read lead-context for agent_id if it exists
            agent_id = f"{domain}.lead"
            lead_context_path = domain_dir / "lead-context.md"
            if lead_context_path.exists():
                try:
                    with open(lead_context_path, encoding="utf-8") as f:
                        agent_id = self._parse_agent_id(domain, f.read())
                except Exception as e:
                    logger.error("Failed to read %s: %s", lead_context_path, e)

            # Sync all .md files in the domain directory
            for file_path in domain_dir.glob("*.md"):
                filename = file_path.name
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()

                    content_hash = self._calculate_hash(content)
                    artifact_id = f"{domain}:{filename}"
                    file_mtime = self._get_file_mtime(file_path)

                    # Check for existing record
                    cursor = conn.execute(
                        "SELECT hash, updated_at, agent_id FROM context_artifacts WHERE id = ?", (artifact_id,)
                    )
                    row = cursor.fetchone()

                    if row:
                        db_hash = row["hash"]
                        db_updated_at = self._parse_db_time(row["updated_at"])
                        db_agent_id = row["agent_id"]

                        if db_hash == content_hash and db_agent_id == agent_id:
                            # Content and agent_id haven't changed
                            skipped_count += 1
                            continue

                        if not force and db_updated_at > file_mtime and db_hash != content_hash:
                            # DB is newer, and content differs, skip file -> DB sync (DB -> file will handle it)
                            logger.info("Skipping %s -> DB (DB is newer: %s > %s)", artifact_id, db_updated_at, file_mtime)
                            skipped_count += 1
                            continue

                    # Determine project (for now defaults to domain)
                    project = domain

                    now_iso = datetime.now(UTC).isoformat()
                    if row:
                        # Update existing
                        conn.execute(
                            """
                            UPDATE context_artifacts 
                            SET content = ?, hash = ?, agent_id = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (content, content_hash, agent_id, now_iso, artifact_id),
                        )
                    else:
                        # Insert new
                        conn.execute(
                            """
                            INSERT INTO context_artifacts 
                            (id, domain, project, agent_id, content, hash, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                artifact_id,
                                domain,
                                project,
                                agent_id,
                                content,
                                content_hash,
                                now_iso,
                                now_iso,
                            ),
                        )
                    synced_count += 1
                except Exception as e:
                    logger.error("Failed to sync %s: %s", file_path, e)

        conn.commit()
        logger.info(
            "Royal Jelly Sync completed: %d synced, %d skipped.",
            synced_count,
            skipped_count,
        )

    def sync_db_to_files(self, force: bool = False):
        """Sync context artifacts from SQLite to filesystem.

        Restores context files from v3 SQLite.
        
        Args:
            force: If True, overwrite files even if they are newer.
        """
        conn = self._connect()

        if not self.context_root.exists():
            self.context_root.mkdir(parents=True, exist_ok=True)

        logger.info("Starting Royal Jelly Sync: SQLite -> Files (%s)", self.context_root)

        restored_count = 0
        skipped_count = 0
        cursor = conn.execute("SELECT id, domain, content, hash, updated_at FROM context_artifacts")

        for row in cursor.fetchall():
            artifact_id = row["id"]
            domain = row["domain"]
            content = row["content"]
            db_hash = row["hash"]
            db_updated_at = self._parse_db_time(row["updated_at"])

            if ":" not in artifact_id:
                continue

            domain_part, filename = artifact_id.split(":", 1)
            domain_dir = self.context_root / domain_part

            if not domain_dir.exists():
                domain_dir.mkdir(parents=True, exist_ok=True)

            file_path = domain_dir / filename

            # Check if file exists and has same content
            if file_path.exists():
                file_content = file_path.read_text(encoding="utf-8")
                file_hash = self._calculate_hash(file_content)
                file_mtime = self._get_file_mtime(file_path)

                if file_hash == db_hash:
                    skipped_count += 1
                    continue

                if not force and file_mtime > db_updated_at:
                    # File is newer, skip DB -> file sync (file -> DB will handle it)
                    logger.info("Skipping DB -> %s (File is newer: %s > %s)", file_path, file_mtime, db_updated_at)
                    skipped_count += 1
                    continue

            # Update/Restore file
            try:
                file_path.write_text(content, encoding="utf-8")
                # Set mtime to match DB's updated_at to prevent immediate resync
                # But we use the now_iso in sync_files_to_db, so it's better to let them stay in sync.
                restored_count += 1
                logger.info("Restored/Updated: %s", file_path)
            except Exception as e:
                logger.error("Failed to restore %s: %s", file_path, e)

        logger.info(
            "Royal Jelly Restore completed: %d restored/updated, %d skipped.",
            restored_count,
            skipped_count,
        )

    def sync_bidirectional(self):
        """Bidirectional sync: Filesystem <-> SQLite.

        Uses timestamps to determine which side is newer.
        1. First syncs files to db if files are newer.
        2. Then syncs db to files if db is newer.

        This ensures both sides are consistent based on latest changes.
        """
        logger.info("Starting Bidirectional Royal Jelly Sync")

        # Step 1: Files -> DB (if files are newer)
        self.sync_files_to_db(force=False)

        # Step 2: DB -> Files (if DB is newer or files missing)
        self.sync_db_to_files(force=False)

        logger.info("Bidirectional Royal Jelly Sync completed")

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Royal Jelly Sync")
    parser.add_argument("--db", help="Path to v3 SQLite database")
    parser.add_argument("--root", help="Path to FORGE root")
    parser.add_argument(
        "--bidirectional", "-b", action="store_true", help="Bidirectional sync (files <-> db)"
    )
    parser.add_argument("--to-files", action="store_true", help="Restore from DB to files")

    args = parser.parse_args()

    sync = RoyalJellySync(
        db_path=Path(args.db) if args.db else None,
        forge_root=Path(args.root) if args.root else None,
    )
    try:
        if args.to_files:
            sync.sync_db_to_files()
        elif args.bidirectional:
            sync.sync_bidirectional()
        else:
            sync.sync_files_to_db()
    finally:
        sync.close()

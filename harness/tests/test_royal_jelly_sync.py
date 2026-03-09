import os
import shutil
import sqlite3
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

from forge_harness.royal_jelly.sync import RoyalJellySync


class TestRoyalJellySync(unittest.TestCase):
    def setUp(self):
        # Create a temporary test directory
        self.test_root = Path("/tmp/forge_test_sync")
        if self.test_root.exists():
            shutil.rmtree(self.test_root)
        self.test_root.mkdir(parents=True)

        # Setup .forge/context and .forge/forge-v3.db paths
        self.context_root = self.test_root / ".forge" / "context"
        self.context_root.mkdir(parents=True)
        self.db_path = self.test_root / ".forge" / "forge-v3.db"

        # Set FORGE_ROOT for the test
        os.environ["FORGE_ROOT"] = str(self.test_root)

        self.sync = RoyalJellySync(db_path=self.db_path, forge_root=self.test_root)

    def tearDown(self):
        self.sync.close()
        # Clean up temporary test directory
        if self.test_root.exists():
            shutil.rmtree(self.test_root)

    def test_sync_files_to_db(self):
        # Create a test domain and lead-context
        domain = "test_domain"
        domain_dir = self.context_root / domain
        domain_dir.mkdir()

        lead_context_content = """# Test Lead Context
**Domain Lead:** `test.agent`"""
        (domain_dir / "lead-context.md").write_text(lead_context_content)

        artifact_content = "This is a test artifact."
        (domain_dir / "test_artifact.md").write_text(artifact_content)

        # Run sync
        self.sync.sync_files_to_db()

        # Verify database content
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM context_artifacts").fetchall()

        artifact_ids = [row["id"] for row in rows]
        self.assertIn(f"{domain}:lead-context.md", artifact_ids)
        self.assertIn(f"{domain}:test_artifact.md", artifact_ids)

        test_artifact_row = next(row for row in rows if row["id"] == f"{domain}:test_artifact.md")
        self.assertEqual(test_artifact_row["content"], artifact_content)
        self.assertEqual(test_artifact_row["agent_id"], "test.agent")

        conn.close()

    def test_sync_db_to_files(self):
        # Manually insert into database
        conn = sqlite3.connect(str(self.db_path))
        self.sync._ensure_schema(conn)

        domain = "db_domain"
        artifact_id = f"{domain}:db_artifact.md"
        content = "Content from database"
        agent_id = "db.agent"
        content_hash = self.sync._calculate_hash(content)

        conn.execute(
            "INSERT INTO context_artifacts (id, domain, project, agent_id, content, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, domain, domain, agent_id, content, content_hash, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat())
        )
        conn.commit()
        conn.close()

        # Run restore
        self.sync.sync_db_to_files()

        # Verify filesystem content
        restored_file = self.context_root / domain / "db_artifact.md"
        self.assertTrue(restored_file.exists())
        self.assertEqual(restored_file.read_text(), content)

    def test_bidirectional_sync(self):
        # 1. Create file
        domain = "bi_domain"
        domain_dir = self.context_root / domain
        domain_dir.mkdir()
        (domain_dir / "file1.md").write_text("File 1 content")

        # Sync
        self.sync.sync_bidirectional()

        # 2. Update file
        (domain_dir / "file1.md").write_text("File 1 content updated")

        # Sync
        self.sync.sync_bidirectional()

        # Verify DB is updated
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute("SELECT content FROM context_artifacts WHERE id = ?", (f"{domain}:file1.md",)).fetchone()
        self.assertEqual(row[0], "File 1 content updated")

        # 3. Add to DB manually
        content2 = "File 2 content"
        content2_hash = self.sync._calculate_hash(content2)
        conn.execute(
            "INSERT INTO context_artifacts (id, domain, project, agent_id, content, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{domain}:file2.md", domain, domain, "bi.agent", content2, content2_hash, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat())
        )
        conn.commit()
        conn.close()

        # Sync
        self.sync.sync_bidirectional()

        # Verify file is restored
        self.assertEqual((domain_dir / "file2.md").read_text(), "File 2 content")

    def test_bidirectional_sync_timestamp(self):
        # 1. Create file and sync to DB
        domain = "time_domain"
        domain_dir = self.context_root / domain
        domain_dir.mkdir()
        file_path = domain_dir / "file1.md"
        file_path.write_text("Old content")

        self.sync.sync_bidirectional()

        # 2. Update DB manually with newer content
        conn = sqlite3.connect(str(self.db_path))
        new_content = "Newer content from DB"
        new_hash = self.sync._calculate_hash(new_content)
        # Use a future time to ensure it's newer
        future_time = "2099-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE context_artifacts SET content = ?, hash = ?, updated_at = ? WHERE id = ?",
            (new_content, new_hash, future_time, f"{domain}:file1.md")
        )
        conn.commit()
        conn.close()

        # 3. Sync - DB should win because it's newer
        self.sync.sync_bidirectional()

        # 4. Verify filesystem was updated from DB
        self.assertEqual(file_path.read_text(), "Newer content from DB")

        # 5. Update file and set mtime to even newer
        file_path.write_text("Even newer content from File")
        # We need to sleep a bit to ensure mtime is different if we don't manually set it,
        # but here we can just wait or manually set mtime if needed.
        # For simplicity, we'll just check if it syncs to DB if it's newer than the 2099 date.

        # Actually, let's just test that if file is newer than DB, file wins.
        # We'll update the DB time to something in the past.
        conn = sqlite3.connect(str(self.db_path))
        past_time = "2000-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE context_artifacts SET updated_at = ? WHERE id = ?",
            (past_time, f"{domain}:file1.md")
        )
        conn.commit()
        conn.close()

        # Sync - File should win because its mtime is definitely > 2000
        self.sync.sync_bidirectional()

        # Verify DB was updated from File
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute("SELECT content FROM context_artifacts WHERE id = ?", (f"{domain}:file1.md",)).fetchone()
        self.assertEqual(row[0], "Even newer content from File")
        conn.close()

    def test_idempotency(self):
        # 1. Create file
        domain = "idem_domain"
        domain_dir = self.context_root / domain
        domain_dir.mkdir()
        (domain_dir / "file1.md").write_text("Constant content")

        # Sync 1
        self.sync.sync_files_to_db()

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row1 = conn.execute("SELECT updated_at FROM context_artifacts WHERE id = ?", (f"{domain}:file1.md",)).fetchone()
        updated_at1 = row1["updated_at"]
        conn.close()

        # Sync 2 (nothing changed)
        time.sleep(0.01)
        self.sync.sync_files_to_db()

        # Verify it skipped (updated_at should be same)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row2 = conn.execute("SELECT updated_at FROM context_artifacts WHERE id = ?", (f"{domain}:file1.md",)).fetchone()
        self.assertEqual(row2["updated_at"], updated_at1)
        conn.close()

        # 3. Change file
        (domain_dir / "file1.md").write_text("Changed content")
        self.sync.sync_files_to_db()

        # Verify it updated (updated_at should be different)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row3 = conn.execute("SELECT updated_at FROM context_artifacts WHERE id = ?", (f"{domain}:file1.md",)).fetchone()
        self.assertNotEqual(row3["updated_at"], updated_at1)
        conn.close()

if __name__ == "__main__":
    unittest.main()

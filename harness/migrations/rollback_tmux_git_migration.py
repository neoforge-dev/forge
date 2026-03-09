#!/usr/bin/env python3
"""Rollback the tmux/git state migration.

This script removes migrated data from the database.
"""
import json
from datetime import datetime

from sqlalchemy import create_engine, text


def run_rollback():
    """Run the rollback."""
    print("Rolling back tmux/git state migration...")

    engine = create_engine("sqlite:///./forge_harness.db")

    with engine.connect() as conn:
        # Step 1: Get migration record
        migration_record = conn.execute(text("""
            SELECT new_value FROM sync_log
            WHERE record_id = 'tmux_git_migration'
            ORDER BY timestamp DESC
            LIMIT 1
        """)).fetchone()

        migration_data = {}
        if migration_record:
            try:
                migration_data = json.loads(migration_record[0])
                print(f"Migration data found: {migration_data}")
            except Exception:
                pass

        # Step 2: Delete migrated agents
        result = conn.execute(text("""
            DELETE FROM cc_agents
            WHERE tmux_session IS NOT NULL
            AND id LIKE 'agent-%'
        """))
        print(f"Deleted {result.rowcount} agents")

        # Step 3: Delete sessions
        result = conn.execute(text("""
            DELETE FROM sessions
            WHERE id LIKE 'session-git-%'
            OR id LIKE 'session-tmux-%'
        """))
        print(f"Deleted {result.rowcount} sessions")

        # Step 4: Record rollback
        conn.execute(text("""
            INSERT INTO sync_log (
                table_name, record_id, operation, node_id,
                old_value, new_value, timestamp
            ) VALUES (
                :table_name, :record_id, :operation, :node_id,
                :old_value, :new_value, :timestamp
            )
        """), {
            'table_name': 'cc_agents',
            'record_id': 'tmux_git_migration_rollback',
            'operation': 'DELETE',
            'node_id': 'local',
            'old_value': json.dumps(migration_data),
            'new_value': None,
            'timestamp': datetime.utcnow().isoformat(),
        })

        conn.commit()
        print("Rollback complete!")


if __name__ == "__main__":
    run_rollback()

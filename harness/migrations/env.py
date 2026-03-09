"""Environment configuration for Alembic migrations.

This module configures the Alembic migration environment to auto-detect
model changes and apply them to the database.
"""

import os
import sys

# Add harness to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import all models here for auto-discovery
try:
    from forge_harness.models.database import (
        Agent,  # noqa: F401
        AgentCapability,  # noqa: F401
        Base,
        Task,  # noqa: F401
        TaskHistory,  # noqa: F401
        TaskRecommendation,  # noqa: F401
    )
    target_metadata = Base.metadata
except ImportError:
    # Models don't exist yet, skip for initial migration
    target_metadata = None


def get_database_url():
    """Get the database URL from environment or alembic.ini."""
    from alembic.config import Config

    # First try environment variable
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # Try alembic.ini
    config_file = os.environ.get("ALEMBIC_CONFIG", "alembic.ini")
    if os.path.exists(config_file):
        cfg = Config(config_file)
        url = cfg.get_main_option("sqlalchemy.url")
        if url:
            return url

    # Default
    return "sqlite:///./forge_harness.db"


def run_migrations():
    """Run migrations synchronously using direct SQL."""
    from sqlalchemy import create_engine, text

    url = get_database_url()
    engine = create_engine(url)

    with engine.connect() as conn:
        # Run the initial migration SQL (original tables)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tasks (
                id UUID PRIMARY KEY,
                subject VARCHAR(500) NOT NULL,
                description TEXT,
                priority INTEGER DEFAULT 5,
                domain VARCHAR(100),
                project VARCHAR(100),
                status VARCHAR(11) DEFAULT 'pending',
                task_type VARCHAR(18) DEFAULT 'general',
                complexity VARCHAR(1) DEFAULT '2',
                estimated_duration_minutes INTEGER,
                dependencies JSON DEFAULT '[]',
                blocked BOOLEAN DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME,
                started_at DATETIME,
                completed_at DATETIME
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agents (
                id VARCHAR(100) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                model VARCHAR(100) NOT NULL,
                status VARCHAR(6) DEFAULT 'idle',
                max_complexity VARCHAR(1) DEFAULT '3',
                preferred_domains JSON DEFAULT '[]',
                current_task_id UUID,
                last_activity DATETIME,
                total_tasks_completed INTEGER DEFAULT 0,
                success_rate FLOAT DEFAULT 0.0,
                avg_task_duration_minutes FLOAT DEFAULT 0.0,
                avg_quality_score FLOAT DEFAULT 0.0,
                created_at DATETIME,
                updated_at DATETIME,
                FOREIGN KEY (current_task_id) REFERENCES tasks (id)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_capabilities (
                id UUID PRIMARY KEY,
                agent_id VARCHAR(100) NOT NULL,
                task_type VARCHAR(18) NOT NULL,
                confidence_score FLOAT DEFAULT 0.0,
                tasks_completed INTEGER DEFAULT 0,
                tasks_failed INTEGER DEFAULT 0,
                last_used DATETIME,
                FOREIGN KEY (agent_id) REFERENCES agents (id)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS task_recommendations (
                id UUID PRIMARY KEY,
                task_id UUID NOT NULL,
                agent_id VARCHAR(100) NOT NULL,
                priority_score FLOAT DEFAULT 0.0,
                capability_match_score FLOAT,
                final_score FLOAT,
                was_recommended BOOLEAN DEFAULT 0,
                was_accepted BOOLEAN DEFAULT 0,
                was_completed BOOLEAN DEFAULT 0,
                quality_score FLOAT,
                actual_duration_minutes INTEGER,
                feedback TEXT,
                recommended_at DATETIME,
                accepted_at DATETIME,
                completed_at DATETIME,
                FOREIGN KEY (task_id) REFERENCES tasks (id),
                FOREIGN KEY (agent_id) REFERENCES agents (id)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS task_history (
                id UUID PRIMARY KEY,
                task_id UUID NOT NULL,
                previous_status VARCHAR(11),
                new_status VARCHAR(11) NOT NULL,
                changed_by VARCHAR(100),
                change_reason TEXT,
                created_at DATETIME,
                extra_data JSON,
                FOREIGN KEY (task_id) REFERENCES tasks (id)
            )
        """))

        # =============================================
        # COMMAND CENTER TABLES (New)
        # =============================================

        # Create agents table for Command Center
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cc_agents (
                id VARCHAR(100) PRIMARY KEY,
                session_id VARCHAR(100) UNIQUE,
                name VARCHAR(200) NOT NULL,
                role VARCHAR(100),
                status VARCHAR(20) DEFAULT 'offline',
                current_task_id VARCHAR(100),
                domain VARCHAR(100),
                project VARCHAR(100),
                skills TEXT DEFAULT '[]',
                token_usage TEXT DEFAULT '{}',
                last_activity DATETIME,
                tmux_session VARCHAR(100),
                node_id VARCHAR(100) DEFAULT 'local',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # Create tasks table for Command Center
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cc_tasks (
                id VARCHAR(100) PRIMARY KEY,
                title VARCHAR(500) NOT NULL,
                description TEXT,
                status VARCHAR(20) DEFAULT 'pending',
                assigned_to VARCHAR(100),
                priority INTEGER DEFAULT 5,
                domain VARCHAR(100),
                project VARCHAR(100),
                acceptance_criteria TEXT DEFAULT '[]',
                deliverables TEXT DEFAULT '[]',
                blocked_by TEXT DEFAULT '[]',
                blocks TEXT DEFAULT '[]',
                started_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assigned_to) REFERENCES cc_agents (id)
            )
        """))

        # Create task_assignments table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS task_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id VARCHAR(100) NOT NULL,
                agent_id VARCHAR(100) NOT NULL,
                assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                notification_sent BOOLEAN DEFAULT 0,
                FOREIGN KEY (task_id) REFERENCES cc_tasks (id),
                FOREIGN KEY (agent_id) REFERENCES cc_agents (id)
            )
        """))

        # Create sessions table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sessions (
                id VARCHAR(100) PRIMARY KEY,
                agent_id VARCHAR(100) NOT NULL,
                start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                end_time DATETIME,
                context_summary TEXT,
                files_modified TEXT DEFAULT '[]',
                token_usage INTEGER DEFAULT 0,
                cost_estimate REAL DEFAULT 0.0,
                deliverables TEXT DEFAULT '[]',
                checkpoint_data TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (agent_id) REFERENCES cc_agents (id)
            )
        """))

        # Create nodes table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS nodes (
                id VARCHAR(100) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                hostname VARCHAR(255),
                ip_address VARCHAR(45),
                status VARCHAR(20) DEFAULT 'offline',
                last_heartbeat DATETIME,
                capabilities TEXT DEFAULT '[]',
                cpu_cores INTEGER DEFAULT 0,
                memory_gb REAL DEFAULT 0.0,
                disk_gb REAL DEFAULT 0.0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # Create sync_log table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name VARCHAR(100) NOT NULL,
                record_id VARCHAR(100) NOT NULL,
                operation VARCHAR(10) NOT NULL,
                node_id VARCHAR(100),
                old_value TEXT,
                new_value TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                checksum VARCHAR(64),
                FOREIGN KEY (node_id) REFERENCES nodes (id)
            )
        """))

        # Create alembic_version table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) PRIMARY KEY
            )
        """))

        # Set the version
        conn.execute(text("""
            INSERT OR IGNORE INTO alembic_version (version_num) VALUES ('cc_initial_001')
        """))

        conn.commit()
        print("Migration complete!")


if __name__ == "__main__":
    run_migrations()

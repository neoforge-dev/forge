-- Migration 010: Agent Health Tracking
-- Tracks agent heartbeats and current status

CREATE TABLE IF NOT EXISTS agent_heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL UNIQUE,
    node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    current_task_id TEXT,
    context_pct REAL DEFAULT 0,
    capabilities TEXT,
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    connected_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (current_task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_last_seen ON agent_heartbeats (last_seen);
CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_status ON agent_heartbeats (status);

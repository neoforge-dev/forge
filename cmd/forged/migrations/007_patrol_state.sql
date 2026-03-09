-- +migrate Up
-- Patrol execution tracking

CREATE TABLE IF NOT EXISTS patrol_executions (
    id TEXT PRIMARY KEY,
    patrol_id TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'running',
    result TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_patrol_id ON patrol_executions(patrol_id);
CREATE INDEX IF NOT EXISTS idx_patrol_status ON patrol_executions(status);

-- +migrate Down
DROP INDEX IF EXISTS idx_patrol_status;
DROP INDEX IF EXISTS idx_patrol_id;
DROP TABLE IF EXISTS patrol_executions;


CREATE TABLE IF NOT EXISTS patrol_executions (
    id          TEXT PRIMARY KEY,
    patrol_id   TEXT NOT NULL,
    started_at  DATETIME NOT NULL DEFAULT (datetime('now')),
    completed_at DATETIME,
    status      TEXT NOT NULL DEFAULT 'running',
    result      TEXT,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_patrol_executions_patrol_id ON patrol_executions(patrol_id);
CREATE INDEX IF NOT EXISTS idx_patrol_executions_started_at ON patrol_executions(started_at);

-- +migrate Down
DROP INDEX IF EXISTS idx_patrol_executions_started_at;
DROP INDEX IF EXISTS idx_patrol_executions_patrol_id;
DROP TABLE IF EXISTS patrol_executions;

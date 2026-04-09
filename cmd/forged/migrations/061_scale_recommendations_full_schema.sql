-- 061: Recreate scale_recommendations with full schema
-- Migration 058 created the table with id INTEGER PRIMARY KEY AUTOINCREMENT,
-- but fleet_scaler.go and tests use TEXT ids. Recreate the table with the
-- correct schema matching ensureScaleRecommendationsTable() in fleet_scaler.go.

-- Preserve any existing rows (rename old table, create new, copy, drop old).
ALTER TABLE scale_recommendations RENAME TO scale_recommendations_old;

CREATE TABLE scale_recommendations (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    agent_type TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    queue_depth INTEGER NOT NULL DEFAULT 0,
    idle_agents INTEGER NOT NULL DEFAULT 0,
    auto_execute INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL DEFAULT '',
    processed_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_scale_recommendations_processed
ON scale_recommendations(processed_at) WHERE processed_at IS NULL;

-- Copy existing rows; cast INTEGER id to TEXT, use defaults for new columns.
INSERT INTO scale_recommendations (id, node_id, action, reason, created_at, processed_at)
SELECT CAST(id AS TEXT), node_id, action,
       COALESCE(reason, ''),
       COALESCE(created_at, datetime('now')),
       processed_at
FROM scale_recommendations_old;

DROP TABLE scale_recommendations_old;

-- +migrate Down
-- SQLite does not support reversing table recreation; no-op.

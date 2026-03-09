CREATE TABLE IF NOT EXISTS relay_deliveries (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    acked_at DATETIME
);

-- +migrate Down
DROP TABLE IF EXISTS relay_deliveries;

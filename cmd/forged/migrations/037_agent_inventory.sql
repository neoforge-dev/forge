-- +migrate Up
CREATE TABLE IF NOT EXISTS agent_inventory (
    id           TEXT PRIMARY KEY,
    agent_type   TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    tmux_window  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'idle',
    context_pct  REAL NOT NULL DEFAULT 0,
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_heartbeat TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_inventory_node ON agent_inventory(node_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_inventory_type ON agent_inventory(agent_type, status);

-- +migrate Down
DROP INDEX IF EXISTS idx_agent_inventory_node;
DROP INDEX IF EXISTS idx_agent_inventory_type;
DROP TABLE IF EXISTS agent_inventory;

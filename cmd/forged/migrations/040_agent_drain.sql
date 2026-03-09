-- Migration 040: Agent drain state + tmux tracking
-- ADR-036 Phase 3: Graceful deflation lifecycle

-- Agent inventory extensions
-- NOTE: tmux_window already exists in migration 037 — skipped here
ALTER TABLE agent_inventory ADD COLUMN drain_state TEXT NOT NULL DEFAULT 'normal';
-- Values: 'normal', 'draining', 'drained'

ALTER TABLE agent_inventory ADD COLUMN last_task_at TEXT;
ALTER TABLE agent_inventory ADD COLUMN spawned_at TEXT;
ALTER TABLE agent_inventory ADD COLUMN spawn_command TEXT;
ALTER TABLE agent_inventory ADD COLUMN agent_tier TEXT NOT NULL DEFAULT 'medium';
-- Values: 'lightweight', 'medium', 'heavy'

-- Work strategy catalog execution log
CREATE TABLE IF NOT EXISTS work_strategy_log (
    id          TEXT PRIMARY KEY,
    catalog_id  TEXT NOT NULL,
    task_id     TEXT,
    node_id     TEXT NOT NULL,
    agent_type  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    status      TEXT NOT NULL DEFAULT 'created'
);

CREATE INDEX idx_work_strategy_catalog ON work_strategy_log(catalog_id, created_at);
CREATE INDEX idx_agent_inventory_drain ON agent_inventory(drain_state, last_heartbeat);

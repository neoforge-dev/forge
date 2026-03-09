-- Migration: 008_xnode.up.sql
-- Created: 2026-03-04

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    address TEXT NOT NULL,
    status TEXT NOT NULL,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS xnode_tasks (
    id TEXT PRIMARY KEY,
    source_node TEXT NOT NULL,
    target_node TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_xnode_tasks_task ON xnode_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_xnode_tasks_nodes ON xnode_tasks(source_node, target_node);

-- +migrate Down
DROP TABLE IF EXISTS xnode_tasks;
DROP TABLE IF EXISTS nodes;

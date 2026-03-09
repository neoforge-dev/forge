-- Migration: 006_task_states.sql
-- Created: 2026-03-05
-- Task Lifecycle State Machine (ADR-028)

-- Add state column to tasks table
ALTER TABLE tasks ADD COLUMN state TEXT DEFAULT 'QUEUED';

-- State transitions log
CREATE TABLE IF NOT EXISTS task_state_transitions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT,
    transitioned_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_task_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_transitions_task ON task_state_transitions(task_id);

-- +migrate Down
DROP INDEX IF EXISTS idx_transitions_task;
DROP INDEX IF EXISTS idx_task_state;
DROP TABLE IF EXISTS task_state_transitions;
-- SQLite does not support ALTER TABLE DROP COLUMN

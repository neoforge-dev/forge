-- Migration 051: Add duration_ms column to tasks and patrol_executions
-- Necessary for performance tracking and dashboard stats

ALTER TABLE tasks ADD COLUMN duration_ms INTEGER;
ALTER TABLE patrol_executions ADD COLUMN duration_ms INTEGER;

-- +migrate Down
-- SQLite does not support ALTER TABLE DROP COLUMN

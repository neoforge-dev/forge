-- Migration 020: Add completed_at column to tasks table
-- Necessary for tracking task completion time

ALTER TABLE tasks ADD COLUMN completed_at TEXT;

-- +migrate Down
-- SQLite does not support ALTER TABLE DROP COLUMN

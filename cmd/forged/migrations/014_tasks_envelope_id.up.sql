-- +migrate Up
-- Add envelope_id to tasks table for context bootstrap linkage

ALTER TABLE tasks ADD COLUMN envelope_id TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_envelope_id ON tasks(envelope_id);

-- +migrate Down
-- Drop index; column is retained for backwards-compatibility
DROP INDEX IF EXISTS idx_tasks_envelope_id;


-- +migrate Up
-- No-op: updated_at already included in CREATE TABLE agents (initial schema).
-- ALTER TABLE agents ADD COLUMN updated_at TEXT; -- kept for reference, do not re-enable
SELECT 1;

-- +migrate Down
-- SQLite doesn't support DROP COLUMN before 3.35.0; leave as-is on rollback

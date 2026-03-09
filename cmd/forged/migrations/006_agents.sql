-- +migrate Up
-- Agent registry indexes for status and heartbeat-style queries

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_last_activity ON agents(last_activity);

-- +migrate Down
DROP INDEX IF EXISTS idx_agents_last_activity;
DROP INDEX IF EXISTS idx_agents_status;


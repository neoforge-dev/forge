-- Migration: 005_context.up.sql
-- Created: 2026-03-04

CREATE TABLE IF NOT EXISTS context_envelopes (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    project TEXT NOT NULL,
    task_id TEXT NOT NULL,
    summary TEXT,
    content TEXT,  -- JSON content
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_agent ON context_envelopes(agent_id);
CREATE INDEX IF NOT EXISTS idx_context_task ON context_envelopes(task_id);
CREATE INDEX IF NOT EXISTS idx_context_expires ON context_envelopes(expires_at);

-- +migrate Down
DROP INDEX IF EXISTS idx_context_expires;
DROP INDEX IF EXISTS idx_context_task;
DROP INDEX IF EXISTS idx_context_agent;
DROP TABLE IF EXISTS context_envelopes;

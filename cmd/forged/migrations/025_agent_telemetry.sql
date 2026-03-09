-- Migration: 018_agent_telemetry.sql
-- Date: 2026-03-05
-- Purpose: Replace sidecar heartbeat files with SQLite-backed telemetry

-- +migrate Up
-- Create agent_telemetry table for real-time agent state tracking
CREATE TABLE IF NOT EXISTS agent_telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    node        TEXT NOT NULL,
    context_pct REAL NOT NULL CHECK (context_pct >= 0 AND context_pct <= 100),
    task_id     TEXT,
    status      TEXT NOT NULL CHECK (status IN ('idle', 'busy', 'wrapup')),
    tool        TEXT NOT NULL,
    metadata    TEXT,  -- JSON blob for extensible metadata
    timestamp   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Index for querying latest telemetry by agent
CREATE INDEX IF NOT EXISTS idx_telemetry_agent 
    ON agent_telemetry(agent_id, timestamp DESC);

-- Index for querying telemetry by node
CREATE INDEX IF NOT EXISTS idx_telemetry_node 
    ON agent_telemetry(node, timestamp DESC);

-- Index for time-range queries
CREATE INDEX IF NOT EXISTS idx_telemetry_time 
    ON agent_telemetry(timestamp DESC);

-- Index for status-based queries (finding busy/idle agents)
CREATE INDEX IF NOT EXISTS idx_telemetry_status 
    ON agent_telemetry(status, timestamp DESC);

-- Composite index for context monitoring queries
CREATE INDEX IF NOT EXISTS idx_telemetry_context_monitor 
    ON agent_telemetry(agent_id, context_pct, timestamp DESC);

-- +migrate Down
DROP INDEX IF EXISTS idx_telemetry_context_monitor;
DROP INDEX IF EXISTS idx_telemetry_status;
DROP INDEX IF EXISTS idx_telemetry_time;
DROP INDEX IF EXISTS idx_telemetry_node;
DROP INDEX IF EXISTS idx_telemetry_agent;
DROP TABLE IF EXISTS agent_telemetry;

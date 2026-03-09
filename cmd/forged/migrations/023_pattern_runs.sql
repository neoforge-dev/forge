-- Migration: 009_pattern_runs.sql
-- Description: Create pattern_runs and pattern_rewards tables for ADR-18 Pattern Library and RL Learning Store
-- Date: 2026-03-05

-- +migrate Up
-- Table for tracking pattern execution history
CREATE TABLE IF NOT EXISTS pattern_runs (
    run_id      TEXT PRIMARY KEY,  -- ULID
    pattern_id  TEXT NOT NULL,     -- e.g. "api_endpoint:simple"
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    domain      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    completed_at TEXT,
    success     INTEGER,          -- 1 = success, 0 = failure, NULL = in-progress
    confidence  REAL,             -- 0.0-1.0 pre-run confidence
    tokens_used INTEGER,
    duration_s  INTEGER,
    error_type  TEXT,             -- NULL on success, categorized on failure
    metadata    TEXT              -- JSON blob for pattern-specific data
);

-- Table for tracking reward signals (RL learning)
CREATE TABLE IF NOT EXISTS pattern_rewards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES pattern_runs(run_id),
    reward_type TEXT NOT NULL,    -- "test_pass", "review_approved", "deploy_success", "revert"
    value       REAL NOT NULL,   -- positive = good, negative = bad
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_runs_pattern ON pattern_runs(pattern_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_domain ON pattern_runs(domain, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_rewards_run ON pattern_rewards(run_id);

-- Pending Dispatch Queue (Originally 009_pending_dispatches.sql)
CREATE TABLE IF NOT EXISTS pending_dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    dispatched_at TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_pending_dispatches_agent ON pending_dispatches (agent_id, dispatched_at);
CREATE INDEX IF NOT EXISTS idx_pending_dispatches_task ON pending_dispatches (task_id);

-- +migrate Down
DROP INDEX IF EXISTS idx_pending_dispatches_task;
DROP INDEX IF EXISTS idx_pending_dispatches_agent;
DROP TABLE IF EXISTS pending_dispatches;
DROP INDEX IF EXISTS idx_rewards_run;
DROP INDEX IF EXISTS idx_runs_domain;
DROP INDEX IF EXISTS idx_runs_pattern;
DROP TABLE IF EXISTS pattern_rewards;
DROP TABLE IF EXISTS pattern_runs;

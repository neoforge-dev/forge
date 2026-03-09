-- Migration: 021_metrics_rollup.sql
-- Task: Implement Metrics Rollup Patrol (ADR-27)
-- Created: 2026-03-05

-- +migrate Up
CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    value       REAL NOT NULL,
    labels      TEXT,  -- JSON: {"agent": "kimi", "node": "sati"}
    period      TEXT NOT NULL,  -- '1m', '5m', '1h'
    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name, computed_at DESC);

-- +migrate Down
DROP INDEX IF EXISTS idx_metrics_name;
DROP TABLE IF EXISTS metrics;

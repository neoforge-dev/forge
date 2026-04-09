-- Add processed_at column to scale_recommendations for fleet-auto-deflate patrol
-- Issue: patrol was failing with "no such column: r.processed_at"

-- Ensure the table exists (fleet_scaler.go creates it at runtime, but migrations
-- may run before the scaler initialises).
CREATE TABLE IF NOT EXISTS scale_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    agent_name TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    applied INTEGER DEFAULT 0
);

ALTER TABLE scale_recommendations ADD COLUMN processed_at TEXT DEFAULT NULL;

-- Index for faster queries on unprocessed recommendations
CREATE INDEX IF NOT EXISTS idx_scale_recommendations_processed
ON scale_recommendations(processed_at) WHERE processed_at IS NULL;

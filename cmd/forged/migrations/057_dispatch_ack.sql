-- Migration 057: Add dispatch ACK tracking columns for Epic 4 dispatch reliability
-- Tracks dispatch attempts, timestamps for stale-dispatch patrol, and ACK confirmation.

ALTER TABLE tasks ADD COLUMN dispatch_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN last_dispatch_at TEXT;
ALTER TABLE tasks ADD COLUMN acked_at TEXT;
ALTER TABLE tasks ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3;

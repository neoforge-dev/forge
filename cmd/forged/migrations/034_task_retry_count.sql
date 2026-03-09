-- Migration 034: Add retry_count column for auto-requeue feature
-- Adds retry tracking for failed tasks that can be automatically requeued

ALTER TABLE tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;

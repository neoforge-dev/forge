-- Migration 056: Add failure_context for remediation-aware requeue
ALTER TABLE tasks ADD COLUMN failure_context TEXT DEFAULT NULL;

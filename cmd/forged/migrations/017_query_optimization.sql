-- Migration: 017_query_optimization.sql
-- Task: DF-007 - Optimize Database Queries
-- Created: 2026-03-05
-- Objective: Add indexes to ensure query time < 100ms for common operations

-- +migrate Up

-- Index for queue polling by state and priority (used by Dequeue, GetClaimableTasks)
CREATE INDEX IF NOT EXISTS idx_tasks_state_priority ON tasks (state, priority DESC);

-- Index for agent task lookup by assigned agent and state
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to_state ON tasks (assigned_to, state);

-- Composite index for lease recovery queries (used by Recover)
CREATE INDEX IF NOT EXISTS idx_leases_task_expires ON leases (task_id, expires_at);

-- Index for task state transition history queries (used by GetTransitionHistory)
CREATE INDEX IF NOT EXISTS idx_transitions_task_time ON task_state_transitions (task_id, transitioned_at DESC);

-- +migrate Down
DROP INDEX IF EXISTS idx_transitions_task_time;
DROP INDEX IF EXISTS idx_leases_task_expires;
DROP INDEX IF EXISTS idx_tasks_assigned_to_state;
DROP INDEX IF EXISTS idx_tasks_state_priority;

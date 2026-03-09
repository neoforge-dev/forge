-- +migrate Up
-- Performance optimization indexes for frequent queries

-- Agent status and activity indexes (used by patrol.go health-check)
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_status_activity ON agents(status, last_activity);

-- Task dependencies composite index (used by queue.go Dequeue)
CREATE INDEX IF NOT EXISTS idx_task_deps_task_status ON task_dependencies(task_id, depends_on);

-- Task queue optimization (used by GetClaimableTasks)
CREATE INDEX IF NOT EXISTS idx_tasks_status_assigned ON tasks(status, assigned_to);

-- Approval queries by status and tier
CREATE INDEX IF NOT EXISTS idx_approvals_status_expires ON approvals(status, expires_at) WHERE status = 'pending';

-- +migrate Down
DROP INDEX IF EXISTS idx_agents_status;
DROP INDEX IF EXISTS idx_agents_status_activity;
DROP INDEX IF EXISTS idx_task_deps_task_status;
DROP INDEX IF EXISTS idx_tasks_status_assigned;
DROP INDEX IF EXISTS idx_approvals_status_expires;

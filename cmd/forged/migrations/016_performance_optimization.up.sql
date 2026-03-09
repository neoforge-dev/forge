-- Performance Optimization Migration: DF-007
-- Adds indexes for frequently queried columns in tasks and agents tables

-- Index for finding tasks by plan (common in agentic workflows)
CREATE INDEX IF NOT EXISTS idx_tasks_plan_id ON tasks (plan_id);

-- Index for finding tasks assigned to specific agents
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks (assigned_to);

-- Index for filtering agents by status (e.g. finding idle agents)
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents (status);

-- Index for agent last activity (used for stale agent detection)
CREATE INDEX IF NOT EXISTS idx_agents_last_activity ON agents (last_activity);

-- Composite index for task lookups by domain/project and status
CREATE INDEX IF NOT EXISTS idx_tasks_domain_project_status ON tasks (domain, project, status);

-- Index for context envelopes by agent_id (if not already covered)
CREATE INDEX IF NOT EXISTS idx_context_envelopes_agent_id ON context_envelopes (agent_id);

-- +migrate Down
DROP INDEX IF EXISTS idx_context_envelopes_agent_id;
DROP INDEX IF EXISTS idx_tasks_domain_project_status;
DROP INDEX IF EXISTS idx_agents_last_activity;
DROP INDEX IF EXISTS idx_agents_status;
DROP INDEX IF EXISTS idx_tasks_assigned_to;
DROP INDEX IF EXISTS idx_tasks_plan_id;

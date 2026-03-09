-- Forge v3 PostgreSQL Schema
-- Migration: 001_initial_postgres.sql
-- Created: 2026-03-03

-- Enable UUID extension for ULID compatibility
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Event log: source of truth
CREATE TABLE IF NOT EXISTS task_events (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    project TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_events_type_time ON task_events (event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_task_events_domain_project ON task_events (domain, project);

-- Projection: minimal for queue + status
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    project TEXT NOT NULL,
    type TEXT NOT NULL,
    priority INTEGER DEFAULT 50,
    status TEXT NOT NULL,
    assigned_to TEXT,
    plan_version INTEGER DEFAULT 1,
    plan_id TEXT,
    result TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks (status, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_domain_project ON tasks (domain, project);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks (assigned_to, status);

-- Magentic Ledger tables
CREATE TABLE IF NOT EXISTS plan_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    plan TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_versions_task ON plan_versions (task_id, version);

CREATE TABLE IF NOT EXISTS plan_events (
    id BIGSERIAL PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plan_versions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_events_plan ON plan_events (plan_id, created_at);

-- Idempotency
CREATE TABLE IF NOT EXISTS idempotent_actions (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_hash TEXT NOT NULL UNIQUE,
    executed_at TIMESTAMPTZ DEFAULT NOW(),
    result JSONB
);

CREATE INDEX IF NOT EXISTS idx_idempotent_type ON idempotent_actions (type, executed_at);

-- Leases
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);
CREATE INDEX IF NOT EXISTS idx_leases_agent ON leases (agent_id);

-- Agents
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    node TEXT NOT NULL,
    role TEXT NOT NULL,
    tier TEXT,
    status TEXT NOT NULL,
    context_pct REAL DEFAULT 0,
    current_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    capabilities JSONB,
    last_activity TIMESTAMPTZ,
    registered_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents (status, last_activity);
CREATE INDEX IF NOT EXISTS idx_agents_node ON agents (node, status);

-- Task dependencies
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on)
);

CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends ON task_dependencies (depends_on);

-- Approvals: human-in-the-loop approval requests
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    requester TEXT NOT NULL,
    payload JSONB,
    confidence_score REAL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_requester ON approvals (requester, status);

-- Context artifacts: handoff and session continuity storage
CREATE TABLE IF NOT EXISTS context_artifacts (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    project TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    content TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_context_artifacts_domain_project ON context_artifacts (domain, project, created_at);
CREATE INDEX IF NOT EXISTS idx_context_artifacts_agent ON context_artifacts (agent_id, created_at);

-- Applied migrations tracking (for compatibility)
CREATE TABLE IF NOT EXISTS applied_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Metrics tracking
CREATE TABLE IF NOT EXISTS metrics (
    id BIGSERIAL PRIMARY KEY,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION,
    labels JSONB,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics (metric_name, recorded_at);

-- Create trigger function for updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to tasks table
DROP TRIGGER IF EXISTS update_tasks_updated_at ON tasks;
CREATE TRIGGER update_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Row-level security policies (optional, for multi-tenant)
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_artifacts ENABLE ROW LEVEL SECURITY;

-- Create policy for domain isolation
CREATE POLICY task_domain_isolation ON tasks
    USING (domain = current_setting('app.current_domain', true));

CREATE POLICY events_domain_isolation ON task_events
    USING (domain = current_setting('app.current_domain', true));

-- +migrate Down

-- Remove triggers
DROP TRIGGER IF EXISTS update_tasks_updated_at ON tasks;

-- Remove functions
DROP FUNCTION IF EXISTS update_updated_at_column();

-- Remove tables (in dependency order)
DROP TABLE IF EXISTS metrics CASCADE;
DROP TABLE IF EXISTS context_artifacts CASCADE;
DROP TABLE IF EXISTS approvals CASCADE;
DROP TABLE IF EXISTS task_dependencies CASCADE;
DROP TABLE IF EXISTS agents CASCADE;
DROP TABLE IF EXISTS leases CASCADE;
DROP TABLE IF EXISTS idempotent_actions CASCADE;
DROP TABLE IF EXISTS plan_events CASCADE;
DROP TABLE IF EXISTS plan_versions CASCADE;
DROP TABLE IF EXISTS task_events CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS applied_migrations CASCADE;

-- Auto-generated from .forge/schema/v3_schema.yaml
-- Database: forge_v3 Version: 3.0.0

-- Main task queue
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY NOT NULL,
    domain TEXT NOT NULL,
    project TEXT NOT NULL,
    type TEXT NOT NULL,
    priority INTEGER DEFAULT 3,
    status TEXT NOT NULL,
    assigned_to TEXT,
    envelope_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks (status, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks (assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_envelope_id ON tasks (envelope_id);

-- Context preservation envelopes
CREATE TABLE IF NOT EXISTS context_envelopes (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL,
    domain TEXT,
    project TEXT,
    task_id TEXT,
    summary TEXT,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_context_envelopes_agent_id_created_at ON context_envelopes (agent_id, created_at);

-- Agent registry and status
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY NOT NULL,
    node TEXT NOT NULL,
    status TEXT NOT NULL,
    context_pct REAL DEFAULT 0.0,
    current_task_id TEXT,
    capabilities TEXT,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    connected_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents (status);
CREATE INDEX IF NOT EXISTS idx_agents_node ON agents (node);
CREATE INDEX IF NOT EXISTS idx_agents_context_pct ON agents (context_pct);

-- Task lease management
CREATE TABLE IF NOT EXISTS task_leases (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    renewed_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_task_leases_task_id ON task_leases (task_id);
CREATE INDEX IF NOT EXISTS idx_task_leases_agent_id ON task_leases (agent_id);
CREATE INDEX IF NOT EXISTS idx_task_leases_expires_at ON task_leases (expires_at);

-- Pending approval requests
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT DEFAULT pending,
    requester TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_approvals_status_created_at ON approvals (status, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_task_id ON approvals (task_id);


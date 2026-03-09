-- Forge v3 Phase 0.5 - Initial SQLite Schema
-- Migration: 001_initial.sql
-- Created: 2026-03-02

-- Event log: source of truth
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    project TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_events_type_time ON task_events (event_type, created_at);

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
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks (status, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_domain_project ON tasks (domain, project);

-- Magentic Ledger tables
CREATE TABLE IF NOT EXISTS plan_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    plan TEXT NOT NULL,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Idempotency
CREATE TABLE IF NOT EXISTS idempotent_actions (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_hash TEXT NOT NULL UNIQUE,
    executed_at TEXT DEFAULT (datetime('now')),
    result TEXT
);

-- Leases
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);

-- Agents
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    node TEXT NOT NULL,
    role TEXT NOT NULL,
    tier TEXT,
    status TEXT NOT NULL,
    context_pct REAL DEFAULT 0,
    current_task_id TEXT,
    capabilities TEXT,
    last_activity TEXT,
    registered_at TEXT DEFAULT (datetime('now'))
);

-- Task dependencies
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on)
);

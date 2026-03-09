-- Migration: 032_worktrees.sql
-- ADR-024: Orchestration-Level Worktree Isolation

CREATE TABLE IF NOT EXISTS worktrees (
    id          TEXT PRIMARY KEY,        -- task_id
    path        TEXT NOT NULL,           -- filesystem path to worktree
    branch      TEXT NOT NULL,           -- git branch name
    agent_id    TEXT,                    -- agent that owns this worktree
    status      TEXT NOT NULL DEFAULT 'active',  -- active | removed
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    removed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_worktrees_status ON worktrees(status);
CREATE INDEX IF NOT EXISTS idx_worktrees_agent ON worktrees(agent_id);


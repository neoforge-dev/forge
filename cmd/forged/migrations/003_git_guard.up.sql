-- +migrate Up
-- GitGuard: single-writer git operations with idempotency and branch-per-task enforcement

CREATE TABLE IF NOT EXISTS git_guard_actions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('commit', 'push', 'branch', 'merge', 'pull')),
    payload_hash TEXT UNIQUE NOT NULL,
    branch TEXT,
    message TEXT,
    files TEXT,
    author TEXT,
    commit_id TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed', 'skipped')),
    result TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_git_guard_task ON git_guard_actions(task_id);
CREATE INDEX IF NOT EXISTS idx_git_guard_hash ON git_guard_actions(payload_hash);
CREATE INDEX IF NOT EXISTS idx_git_guard_branch ON git_guard_actions(branch);
CREATE INDEX IF NOT EXISTS idx_git_guard_status ON git_guard_actions(status);
CREATE INDEX IF NOT EXISTS idx_git_guard_type ON git_guard_actions(type);
CREATE INDEX IF NOT EXISTS idx_git_guard_created ON git_guard_actions(created_at);

-- Track active branch locks per task (enforce branch-per-task)
CREATE TABLE IF NOT EXISTS git_branch_locks (
    task_id TEXT PRIMARY KEY,
    branch TEXT NOT NULL,
    locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locked_by TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- +migrate Down
DROP INDEX IF EXISTS idx_git_guard_created;
DROP INDEX IF EXISTS idx_git_guard_type;
DROP INDEX IF EXISTS idx_git_guard_status;
DROP INDEX IF EXISTS idx_git_guard_branch;
DROP INDEX IF EXISTS idx_git_guard_hash;
DROP INDEX IF EXISTS idx_git_guard_task;
DROP TABLE IF EXISTS git_branch_locks;
DROP TABLE IF EXISTS git_guard_actions;

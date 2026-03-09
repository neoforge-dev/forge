-- +migrate Up
-- Approval workflow system with confidence scoring and tiered risk management

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('task_completion', 'merge', 'deploy', 'security', 'budget', 'pattern', 'lane', 'destructive')),
    task_id TEXT,
    agent_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    diff_summary TEXT,
    
    risk_score REAL DEFAULT 0.5 CHECK (risk_score >= 0.0 AND risk_score <= 1.0),
    confidence_score REAL DEFAULT 0.5 CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
    recommendation TEXT,
    
    tier TEXT DEFAULT 'phone' CHECK (tier IN ('watch', 'phone', 'desktop')),
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'auto_approved')),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP,
    resolved_by TEXT,
    
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_tier ON approvals(tier);
CREATE INDEX IF NOT EXISTS idx_approvals_type ON approvals(type);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);
CREATE INDEX IF NOT EXISTS idx_approvals_agent ON approvals(agent_id);
CREATE INDEX IF NOT EXISTS idx_approvals_domain ON approvals(domain);
CREATE INDEX IF NOT EXISTS idx_approvals_created ON approvals(created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_expires ON approvals(expires_at);

-- Index for pending approvals by tier (commonly queried)
CREATE INDEX IF NOT EXISTS idx_approvals_pending_tier ON approvals(status, tier) WHERE status = 'pending';

-- +migrate Down
DROP INDEX IF EXISTS idx_approvals_pending_tier;
DROP INDEX IF EXISTS idx_approvals_expires;
DROP INDEX IF EXISTS idx_approvals_created;
DROP INDEX IF EXISTS idx_approvals_domain;
DROP INDEX IF EXISTS idx_approvals_agent;
DROP INDEX IF EXISTS idx_approvals_task;
DROP INDEX IF EXISTS idx_approvals_type;
DROP INDEX IF EXISTS idx_approvals_tier;
DROP INDEX IF EXISTS idx_approvals_status;
DROP TABLE IF EXISTS approvals;

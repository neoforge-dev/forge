-- Handoffs for seamless agent session transitions (ADR-014 bridge)
CREATE TABLE IF NOT EXISTS handoffs (
    id               TEXT PRIMARY KEY,
    from_agent       TEXT NOT NULL,
    to_agent         TEXT NOT NULL,
    task_description TEXT NOT NULL,
    files            TEXT NOT NULL, -- JSON array of strings
    priority         TEXT NOT NULL DEFAULT 'medium',
    status           TEXT NOT NULL DEFAULT 'pending', -- pending|accepted|rejected|completed
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    accepted_at      TEXT,
    rejected_at      TEXT,
    completed_at     TEXT,
    reason           TEXT, -- Rejection reason
    notes            TEXT, -- Completion notes
    accepting_agent  TEXT  -- ID of agent that accepted
);

CREATE INDEX IF NOT EXISTS idx_handoffs_status ON handoffs(status);
CREATE INDEX IF NOT EXISTS idx_handoffs_from ON handoffs(from_agent);
CREATE INDEX IF NOT EXISTS idx_handoffs_to ON handoffs(to_agent);

-- +migrate Down
DROP INDEX IF EXISTS idx_handoffs_to;
DROP INDEX IF EXISTS idx_handoffs_from;
DROP INDEX IF EXISTS idx_handoffs_status;
DROP TABLE IF EXISTS handoffs;

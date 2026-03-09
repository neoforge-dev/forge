-- Migration 019: Create lanes table
-- For autonomous task routing in Dark Factory mode

CREATE TABLE IF NOT EXISTS lanes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    capabilities TEXT, -- Comma-separated list of required capabilities
    auto_claim BOOLEAN DEFAULT 1,
    max_parallel INTEGER DEFAULT 5,
    timeout_minutes INTEGER DEFAULT 30,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Seed default lanes
INSERT OR IGNORE INTO lanes (id, name, description, capabilities, auto_claim, max_parallel, timeout_minutes)
VALUES 
('dev', 'Development', 'Software development and feature implementation', 'python,shell,code', 1, 10, 60),
('test', 'Testing', 'Unit, integration and E2E testing', 'python,test', 1, 5, 30),
('docs', 'Documentation', 'Documentation and technical writing', 'markdown', 1, 3, 15),
('review', 'Review', 'Code review and quality assurance', 'review', 1, 5, 30);

-- +migrate Down
DROP TABLE IF EXISTS lanes;

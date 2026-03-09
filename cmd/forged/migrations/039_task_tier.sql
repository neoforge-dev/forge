-- Migration 039: Task tier field for routing
-- ADR-036 Phase 3: Right task to right agent

ALTER TABLE tasks ADD COLUMN required_tier TEXT NOT NULL DEFAULT 'any';
CREATE INDEX idx_tasks_required_tier ON tasks(required_tier, status);

-- Tier values: 'any', 'lightweight', 'medium', 'heavy'
-- Allows routing heavy tasks (Go refactoring) to opencode/kilo
-- while lightweight tasks (docs) go to kimi/minimax/pi

CREATE TABLE IF NOT EXISTS blueprint_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    blueprint_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    current_step INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error TEXT
);

CREATE TABLE IF NOT EXISTS blueprint_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    evidence TEXT,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES blueprint_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_blueprint_runs_task_id ON blueprint_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_blueprint_steps_run_id ON blueprint_steps(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_blueprint_steps_run_step ON blueprint_steps(run_id, step_index);

-- +migrate Down
DROP INDEX IF EXISTS idx_blueprint_steps_run_step;
DROP INDEX IF EXISTS idx_blueprint_steps_run_id;
DROP INDEX IF EXISTS idx_blueprint_runs_task_id;
DROP TABLE IF EXISTS blueprint_steps;
DROP TABLE IF EXISTS blueprint_runs;

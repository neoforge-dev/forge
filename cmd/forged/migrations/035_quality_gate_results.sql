-- +migrate Up
CREATE TABLE IF NOT EXISTS quality_gate_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT    NOT NULL,
    test_pass_rate  REAL    NOT NULL DEFAULT 0.0,
    coverage_pct    REAL    NOT NULL DEFAULT 0.0,
    lint_issues     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quality_gate_task_id ON quality_gate_results (task_id);
CREATE INDEX IF NOT EXISTS idx_quality_gate_created_at ON quality_gate_results (task_id, created_at DESC);

-- +migrate Down
DROP INDEX IF EXISTS idx_quality_gate_created_at;
DROP INDEX IF EXISTS idx_quality_gate_task_id;
DROP TABLE IF EXISTS quality_gate_results;

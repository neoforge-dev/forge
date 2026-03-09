-- +migrate Up
-- ADR-035 Phase 2: Token budget server-side tables.
-- Per-node JSON files remain the authoritative live source;
-- token_budget_snapshots is a materialized aggregate for cross-node reads.
-- token_budget_log provides an audit trail for every status change.

CREATE TABLE IF NOT EXISTS token_budget_log (
    id              TEXT PRIMARY KEY,
    node_id         TEXT NOT NULL,
    provider        TEXT NOT NULL,
    event_type      TEXT NOT NULL,      -- seed | update | cooldown_set | cooldown_clear | reset
    monthly_pct     INTEGER,
    weekly_pct      INTEGER,
    h5_pct          INTEGER,
    status          TEXT NOT NULL,      -- OK | CAUTION | RED | DEPLETED | COOLDOWN
    cooldown_until  TEXT,
    source          TEXT,               -- manual_seed | patrol | api
    recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_token_budget_log_node_provider
    ON token_budget_log(node_id, provider, recorded_at DESC);

-- token_budget_snapshots: one row per (node, provider), updated by patrol every 5 min.
-- snapshot_age_seconds gate (codex wildcard): spawns fail-closed if data is too stale.
CREATE TABLE IF NOT EXISTS token_budget_snapshots (
    node_id              TEXT NOT NULL,
    provider             TEXT NOT NULL,
    monthly_pct          INTEGER NOT NULL DEFAULT 0,
    weekly_pct           INTEGER NOT NULL DEFAULT 0,
    h5_pct               INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'OK',
    cooldown_until       TEXT,
    agents               TEXT,           -- JSON array ["kimi","minimax"]
    snapshot_age_seconds INTEGER NOT NULL DEFAULT 0,
    last_reconciled_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (node_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_token_budget_snapshots_status
    ON token_budget_snapshots(status);
CREATE INDEX IF NOT EXISTS idx_token_budget_snapshots_node
    ON token_budget_snapshots(node_id);

-- +migrate Down
DROP INDEX IF EXISTS idx_token_budget_snapshots_node;
DROP INDEX IF EXISTS idx_token_budget_snapshots_status;
DROP TABLE IF EXISTS token_budget_snapshots;
DROP INDEX IF EXISTS idx_token_budget_log_node_provider;
DROP TABLE IF EXISTS token_budget_log;

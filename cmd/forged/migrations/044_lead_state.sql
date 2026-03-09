-- Lead state for orchestrator tracking (ADR-028)
CREATE TABLE IF NOT EXISTS lead_state (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    version           INTEGER NOT NULL DEFAULT 1,
    state             TEXT NOT NULL DEFAULT 'IDLE',
    previous_state    TEXT,
    entered_at        TEXT NOT NULL DEFAULT (datetime('now')),
    context_percent   REAL DEFAULT 0.0,
    current_sprint    TEXT,
    current_wave      INTEGER DEFAULT 0,
    agents_dispatched TEXT, -- JSON array
    agents_complete   TEXT, -- JSON array
    pending_commits   INTEGER DEFAULT 0,
    last_dispatch_time TEXT,
    last_transition_from TEXT,
    last_transition_to TEXT,
    last_transition_reason TEXT,
    last_transition_at TEXT
);

CREATE TABLE IF NOT EXISTS lead_state_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    reason      TEXT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- +migrate Down
DROP TABLE IF EXISTS lead_state_history;
DROP TABLE IF EXISTS lead_state;

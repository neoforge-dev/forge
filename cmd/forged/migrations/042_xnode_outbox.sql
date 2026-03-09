-- XNode outbox for durable cross-node message delivery (ADR-023)
CREATE TABLE IF NOT EXISTS xnode_outbox (
    id            TEXT PRIMARY KEY,
    target_node   TEXT NOT NULL,
    message_type  TEXT NOT NULL,
    payload       TEXT NOT NULL,  -- JSON
    idempotency_key TEXT UNIQUE,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|serialized|acked
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    serialized_at TEXT,
    acked_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_xnode_outbox_status ON xnode_outbox(status, target_node);

-- XNode inbox for idempotent processing of received messages
CREATE TABLE IF NOT EXISTS xnode_inbox (
    id              TEXT PRIMARY KEY,
    source_node     TEXT NOT NULL,
    message_type    TEXT NOT NULL,
    payload         TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'received'  -- received|processed
);

-- +migrate Down
DROP INDEX IF EXISTS idx_xnode_outbox_status;
DROP TABLE IF EXISTS xnode_outbox;
DROP TABLE IF EXISTS xnode_inbox;

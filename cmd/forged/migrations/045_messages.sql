-- Messages for agent-to-agent and agent-to-user communication (ADR-014 bridge)
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT, -- NULL means broadcast
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'sent', -- sent|delivered|read
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    read_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

-- +migrate Down
DROP INDEX IF EXISTS idx_messages_status;
DROP INDEX IF EXISTS idx_messages_to;
DROP INDEX IF EXISTS idx_messages_thread;
DROP TABLE IF EXISTS messages;

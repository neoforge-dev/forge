CREATE TABLE IF NOT EXISTS voice_commands (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    raw_text TEXT NOT NULL,
    intent TEXT,
    confidence REAL,
    entities JSON,
    executed BOOLEAN DEFAULT 0,
    success BOOLEAN DEFAULT 0,
    error TEXT,
    result JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    executed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_voice_commands_created ON voice_commands(created_at);
CREATE INDEX IF NOT EXISTS idx_voice_commands_intent ON voice_commands(intent);
CREATE INDEX IF NOT EXISTS idx_voice_commands_success ON voice_commands(success);

-- +migrate Down
DROP INDEX IF EXISTS idx_voice_commands_success;
DROP INDEX IF EXISTS idx_voice_commands_intent;
DROP INDEX IF EXISTS idx_voice_commands_created;
DROP TABLE IF EXISTS voice_commands;

-- 059: Agent cooldown tracking for self-healing fleet
CREATE TABLE IF NOT EXISTS agent_cooldowns (
    agent_id   TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'unknown',
    cooldown_until TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (agent_id)
);

-- +migrate Down
DROP TABLE IF EXISTS agent_cooldowns;

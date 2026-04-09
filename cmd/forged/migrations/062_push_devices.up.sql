CREATE TABLE IF NOT EXISTS push_devices (
    token TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK (platform IN ('ios', 'watchos')),
    user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_push_devices_platform ON push_devices(platform);
CREATE INDEX IF NOT EXISTS idx_push_devices_user ON push_devices(user_id);

-- +migrate Down
DROP INDEX IF EXISTS idx_push_devices_user;
DROP INDEX IF EXISTS idx_push_devices_platform;
DROP TABLE IF EXISTS push_devices;

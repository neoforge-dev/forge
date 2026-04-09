-- 053_domains.sql: domain metadata table for PATCH /api/domains/{key}
-- Stores mutable daemon-side metadata for domains defined in config/domains.yaml.
-- The key column mirrors the YAML map key (e.g. "codeswiftr-com").

CREATE TABLE IF NOT EXISTS domains (
    key        TEXT PRIMARY KEY,
    score      INTEGER NOT NULL DEFAULT 0 CHECK (score >= 0 AND score <= 100),
    status     TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'archived')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

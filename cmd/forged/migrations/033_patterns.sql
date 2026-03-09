-- Migration: 033_patterns.sql
-- Description: Create patterns table for pattern library CRUD (ADR-014 CC retirement gate #2)
-- Date: 2026-03-06

-- +migrate Up
CREATE TABLE IF NOT EXISTS patterns (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    domain       TEXT NOT NULL DEFAULT '',
    yaml_content TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Index for domain-based lookups
CREATE INDEX IF NOT EXISTS idx_patterns_domain ON patterns(domain, updated_at DESC);

-- +migrate Down
DROP INDEX IF EXISTS idx_patterns_domain;
DROP TABLE IF EXISTS patterns;

-- Migration: 022_projects.sql
-- Task: Implement Projects Table (ADR-28)
-- Created: 2026-03-06

-- +migrate Up
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    key         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    domain      TEXT NOT NULL,
    description TEXT,
    path        TEXT,
    type        TEXT,
    status      TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_domain ON projects(domain);
CREATE INDEX IF NOT EXISTS idx_projects_key ON projects(key);

-- +migrate Down
DROP INDEX IF EXISTS idx_projects_key;
DROP INDEX IF EXISTS idx_projects_domain;
DROP TABLE IF EXISTS projects;

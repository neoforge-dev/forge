-- Migration: 054_rename_projects_to_products.sql
-- TC-TAXONOMY-S150: Council decision (4-0) to rename "project" → "product" globally.
-- Renames the projects table to products and creates a backward-compat view.
-- Created: 2026-03-21

-- +migrate Up

-- Step 1: Rename the physical table.
ALTER TABLE projects RENAME TO products;

-- Step 2: Rename the indexes to match the new table name.
DROP INDEX IF EXISTS idx_projects_domain;
DROP INDEX IF EXISTS idx_projects_key;
CREATE INDEX IF NOT EXISTS idx_products_domain ON products(domain);
CREATE INDEX IF NOT EXISTS idx_products_key ON products(key);

-- Step 3: Create a backward-compatibility VIEW so existing queries against
-- "projects" continue to work during the transition period.
CREATE VIEW IF NOT EXISTS projects AS
    SELECT id, key, name, domain, description, path, type, status, created_at, updated_at
    FROM products;

-- +migrate Down
DROP VIEW IF EXISTS projects;
DROP INDEX IF EXISTS idx_products_key;
DROP INDEX IF EXISTS idx_products_domain;
ALTER TABLE products RENAME TO projects;
CREATE INDEX IF NOT EXISTS idx_projects_domain ON projects(domain);
CREATE INDEX IF NOT EXISTS idx_projects_key ON projects(key);

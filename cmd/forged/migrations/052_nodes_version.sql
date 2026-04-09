-- Migration: 052_nodes_version.sql
-- Adds version and git_commit columns to nodes table for cross-node version tracking.

ALTER TABLE nodes ADD COLUMN version TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN git_commit TEXT NOT NULL DEFAULT '';

-- +migrate Down
-- SQLite does not support DROP COLUMN in older versions; leave columns in place.
SELECT 1;

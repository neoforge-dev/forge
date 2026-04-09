-- Migration: 055_task_origin.sql
-- Add task origin metadata fields so we can track where tasks come from
-- (CLI, OpenClaw/Telegram, API, patrol).
-- Created: 2026-03-24

-- +migrate Up
ALTER TABLE tasks ADD COLUMN origin TEXT DEFAULT '';
ALTER TABLE tasks ADD COLUMN requester TEXT DEFAULT '';
ALTER TABLE tasks ADD COLUMN source_channel TEXT DEFAULT '';

-- +migrate Down
-- SQLite doesn't support DROP COLUMN easily; these columns are nullable and harmless

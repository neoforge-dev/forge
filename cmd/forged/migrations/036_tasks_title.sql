-- +migrate Up
ALTER TABLE tasks ADD COLUMN title TEXT DEFAULT '';
ALTER TABLE tasks ADD COLUMN description TEXT DEFAULT '';

-- +migrate Down
-- SQLite does not support ALTER TABLE DROP COLUMN

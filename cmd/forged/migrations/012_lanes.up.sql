-- Add lane column to tasks table for lane-based routing

ALTER TABLE tasks ADD COLUMN lane TEXT DEFAULT 'dev';

-- +migrate Down

ALTER TABLE tasks DROP COLUMN lane;

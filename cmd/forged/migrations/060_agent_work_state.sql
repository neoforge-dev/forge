-- Migration 059: Agent Work State
-- Adds work_state column to agent_heartbeats for smart dispatch routing.
-- Valid values: 'idle' | 'working' | 'blocked'
-- This is a secondary field alongside status (online/offline/unknown).
-- The task-dispatcher patrol sets work_state='working' on dispatch and
-- work_state='idle' on task completion to enable dispatch routing decisions.

ALTER TABLE agent_heartbeats ADD COLUMN work_state TEXT NOT NULL DEFAULT 'idle';

-- Index for fast dispatch queries: find idle agents quickly.
CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_work_state ON agent_heartbeats (work_state, status);

-- +migrate Down
DROP INDEX IF EXISTS idx_agent_heartbeats_work_state;

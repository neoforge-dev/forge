-- Migration 041: Agent Table Consolidation
-- ADR: agent_heartbeats becomes single source of truth for live agent state.
-- agents and agent_inventory are preserved (no drops) — this is a safe additive migration.
-- A unified view is created for query convenience.

-- ============================================================
-- Extend agent_heartbeats with columns from agents
-- ============================================================

-- Human-readable agent name (e.g. "kimi", "gemini-2")
ALTER TABLE agent_heartbeats ADD COLUMN name TEXT;

-- Agent role: 'orchestrator' | 'worker' | 'fleet'
ALTER TABLE agent_heartbeats ADD COLUMN role TEXT DEFAULT 'worker';

-- Agent tier from agents table: 'lightweight' | 'medium' | 'heavy'
-- Mirrors agent_inventory.agent_tier — use same vocabulary going forward.
ALTER TABLE agent_heartbeats ADD COLUMN tier TEXT DEFAULT 'medium';

-- When this agent first registered (preserved across reconnects)
-- Note: DEFAULT (datetime('now')) not allowed in SQLite ALTER TABLE — use NULL default
ALTER TABLE agent_heartbeats ADD COLUMN registered_at TEXT;

-- ============================================================
-- Extend agent_heartbeats with columns from agent_inventory
-- ============================================================

-- CLI/model type: 'kimi' | 'gemini' | 'claude' | 'minimax' | 'glm' | 'pi' | etc.
ALTER TABLE agent_heartbeats ADD COLUMN agent_type TEXT;

-- tmux window name used for dispatch (e.g. "kimi", "gemini-2")
ALTER TABLE agent_heartbeats ADD COLUMN tmux_window TEXT;

-- Cumulative tokens consumed this session (for budget tracking)
ALTER TABLE agent_heartbeats ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0;

-- When agent is eligible to accept new tasks again (NULL = no cooldown)
ALTER TABLE agent_heartbeats ADD COLUMN cooldown_until TEXT;

-- Graceful deflation lifecycle: 'normal' | 'draining' | 'drained'
ALTER TABLE agent_heartbeats ADD COLUMN drain_state TEXT NOT NULL DEFAULT 'normal';

-- Shell command used to spawn this agent (for restart/audit)
ALTER TABLE agent_heartbeats ADD COLUMN spawn_command TEXT;

-- Timestamp of most recent task assignment (used for idle detection)
ALTER TABLE agent_heartbeats ADD COLUMN last_task_at TEXT;

-- When the agent process was first started (distinct from connected_at which resets per session)
ALTER TABLE agent_heartbeats ADD COLUMN spawned_at TEXT;

-- ============================================================
-- Indexes on new columns
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_drain ON agent_heartbeats (drain_state, last_seen);
CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_tier  ON agent_heartbeats (tier, status);

-- ============================================================
-- Unified view: single query surface for all agent consumers
-- Prefers agent_heartbeats as authoritative; joins inventory for
-- columns not yet backfilled (agent_type, tmux_window from legacy rows).
-- ============================================================

DROP VIEW IF EXISTS agent_unified;
CREATE VIEW agent_unified AS
SELECT
    ah.agent_id,
    COALESCE(ah.name,       a.name)         AS name,
    ah.node,
    COALESCE(ah.role,       a.role)         AS role,
    COALESCE(ah.tier,       a.tier,
             ai.agent_tier)                 AS tier,
    ah.status,
    ah.context_pct,
    ah.current_task_id,
    ah.capabilities,
    ah.last_seen,
    ah.connected_at,
    COALESCE(ah.registered_at, a.registered_at) AS registered_at,
    -- inventory-origin columns (heartbeat wins if backfilled)
    COALESCE(ah.agent_type,  ai.agent_type) AS agent_type,
    COALESCE(ah.tmux_window, ai.tmux_window) AS tmux_window,
    COALESCE(ah.tokens_used, ai.tokens_used, 0) AS tokens_used,
    COALESCE(ah.cooldown_until, ai.cooldown_until) AS cooldown_until,
    COALESCE(ah.drain_state, ai.drain_state, 'normal') AS drain_state,
    COALESCE(ah.spawn_command, ai.spawn_command) AS spawn_command,
    COALESCE(ah.last_task_at,  ai.last_task_at)  AS last_task_at,
    COALESCE(ah.spawned_at,    ai.spawned_at)     AS spawned_at
FROM agent_heartbeats ah
LEFT JOIN agents a
    ON a.id = ah.agent_id
LEFT JOIN agent_inventory ai
    ON ai.id = ah.agent_id   -- NOTE: agent_inventory.id is the PK (migration 037)
;

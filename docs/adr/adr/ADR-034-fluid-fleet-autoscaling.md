# ADR-034: Fluid Fleet Auto-Scaling

**Status:** Proposed
**Date:** 2026-03-07
**Author:** Lead Orchestrator (node-1) + Council (backend-engineer, industry-patterns, token-inventory, edge-cases)

---

## Context

FORGE currently requires manual agent spawn/kill decisions by the human orchestrator. As the task queue grows and spans multiple nodes (node-1, node-2, node-3, node-4, node-5), we need the node lead to dynamically right-size its fleet based on:

1. **Queue depth** — pending tasks vs idle agents
2. **Node RAM budget** — avoid OOM (node-1 at 16 GB is tight)
3. **Token budgets** — three overlapping limits:
   - **5-hour rolling limit** (Claude Code: ~4h 15m window, rate-limited by Anthropic)
   - **Weekly limit** (Claude Code: resets every 7 days)
   - **Monthly limit** (most providers: resets on 1st of month)

The current state: human orchestrator on node-1 manually runs `kimi -y` or `claude --dangerously-skip-permissions` in a tmux window. No automation. No token tracking. No cooldown awareness.

---

## Decision

Implement a **daemon-side `fleetScaleRecommendPatrol`** that generates recommendations, combined with a **hybrid approval model**: daemon recommends, orchestrator approves (via `forge fleet recommendations`). Autonomous execution deferred to Phase 2 after trust is established.

---

## Council Refinements (Integrated 2026-03-07)

Four council agents reviewed this ADR. Critical corrections applied:

1. **Token state lives in per-node JSON files, NOT SQLite** — git-merge-able, works offline, no daemon dependency. SQLite binary conflicts when multiple nodes write simultaneously (token-inventory agent).
2. **Phase 1 is inflate-ONLY** — no autonomous deflation until trust established. Deflation risks zombie-agent tombstones and flapping (edge-cases agent).
3. **Autoscaling MUST be a Go daemon patrol, not an orchestrator action** — orchestrator is a Claude agent subject to context limits; the patrol is immune (edge-cases agent).
4. **node-1 already has 7 active agents despite a 2-agent documented max** — enforce hard ceilings in code first before adding dynamic scaling. Step 0 is ceiling enforcement (edge-cases agent).
5. **RAM sampling must read `/proc/meminfo` live**, not the stale heartbeat JSON which can be minutes old (edge-cases agent).
6. **Scale-up is aggressive (0s delay), scale-down is conservative** — 120s lightweight, 300s medium, 600s heavy (industry-patterns agent).

---

## Token Budget Model (Critical Design)

### Three overlapping limits per agent type (Anthropic-specific)

| Limit | Scope | Example (from `/usage`) |
|-------|-------|------------------------|
| 5-hour rolling | Per Claude Code session | 100% left, resets in ~4h |
| Weekly | Per provider account | 82% left, resets in ~3d 15h |
| Monthly | Per provider account | Resets 1st of month |

All three must be checked independently. A fresh 5h window doesn't help if the weekly is at 99%.

### Token state machine

```
OK       (0–79%)   → spawn freely
CAUTION  (80–89%)  → prefer substitute providers for new spawns; in-flight agents continue
RED      (90–95%)  → stop spawning this provider type; drain existing agents
DEPLETED (95%+)    → all tasks reroute to fallback providers
RESET    (on cooldown_until reached) → return to OK
```

For Claude Code (Anthropic provider):
- `cooldown_until` = nearest of: (5h window reset) OR (weekly reset)
- Agents with depleted 5h windows should NOT be spawned even if weekly is fine
- Agents with depleted weekly should NOT be spawned until `week_reset_date`

### Provider reset schedule

| Provider | Agents | 5h rolling | Weekly | Monthly | CLI to check |
|----------|--------|------------|--------|---------|--------------|
| anthropic | claude, amp | YES | YES | YES | `/usage` in Claude Code |
| openai | opencode, kilo | YES | YES | YES | shown in Codex header |
| google | gemini | no | no | 1st | provider dashboard |
| moonshot | kimi | no | no | 1st | provider dashboard |
| minimax | minimax | no | no | 1st | provider dashboard |
| inflection | pi | no | no | 1st | provider dashboard |
| cursor | cursor | no | no | 1st | provider dashboard |

**Key insight:** Both Anthropic (Claude Code) and OpenAI (Codex) have 5h + weekly compound limits. These are the two providers most likely to hit mid-session exhaustion. Google/Moonshot/MiniMax only have monthly limits — they are safer for sustained overnight work.

---

## Schema

### Migration 037: agent_inventory

```sql
-- +migrate Up
CREATE TABLE IF NOT EXISTS agent_inventory (
    id           TEXT PRIMARY KEY,
    agent_type   TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    tmux_window  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'idle',  -- idle, busy, starting, stopping
    context_pct  REAL NOT NULL DEFAULT 0,
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,                          -- ISO8601, NULL = available
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_heartbeat TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_inventory_node ON agent_inventory(node_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_inventory_type ON agent_inventory(agent_type, status);

-- +migrate Down
DROP TABLE IF EXISTS agent_inventory;
```

### Token budgets: per-node JSON (NOT SQLite migration)

**Decision (council correction):** Token state lives in `.forge/heartbeat/token-budgets-{node}.json`, NOT in SQLite. Reason: SQLite binary files don't merge in git. Multiple nodes writing simultaneously create unresolvable conflicts. Per-node JSON files have the same pattern as existing `.forge/heartbeat/nodes/{node}.json` and are trivially merge-safe.

File: `.forge/heartbeat/token-budgets-node-1.json`

```json
{
  "node_id": "node-1",
  "month": "2026-03",
  "updated_at": "2026-03-07T14:30:00Z",
  "providers": {
    "anthropic": {
      "provider": "anthropic",
      "agent_types": ["claude", "amp"],
      "monthly_usage": 45000,
      "monthly_limit": 500000,
      "window_5h_used_pct": 0,
      "window_5h_resets": "2026-03-07T18:30:00Z",
      "week_used_pct": 18,
      "week_resets": "2026-03-10T04:20:00Z",
      "status": "ok",
      "cooldown_until": null
    },
    "openai": {
      "provider": "openai",
      "agent_types": ["opencode", "kilo"],
      "monthly_usage": 48000,
      "monthly_limit": 50000,
      "window_5h_used_pct": 0,
      "window_5h_resets": "2026-03-07T14:26:00Z",
      "week_used_pct": 29,
      "week_resets": "2026-03-10T04:20:00Z",
      "status": "red",
      "cooldown_until": null
    },
    "moonshot": {
      "provider": "moonshot",
      "agent_types": ["kimi"],
      "monthly_usage": 87500,
      "monthly_limit": 100000,
      "window_5h_used_pct": null,
      "window_5h_resets": null,
      "week_used_pct": null,
      "week_resets": null,
      "status": "warning",
      "cooldown_until": null
    }
  }
}
```

**Read-time month rollover:** On every read, if `month != current_month`, reset all `*_usage` to 0. No cron job needed. Values prefixed with `~` in CLI display to signal approximation (usage is manually reported, not API-extracted).

**Extend `agent-registry.toml`** (not a new migration):
```toml
# Add to each [[agents]] entry:
token_monthly_limit  = 100000     # 0 = unlimited
token_limit_unit     = "requests" # "tokens" | "requests" | "credits"
token_warn_pct       = 80
token_hard_pct       = 95
substitutes          = ["gemini", "pi"]  # fallback chain when depleted
```

---

## Inflate / Deflate Logic

### Agent capability tiers (for RAM budgeting and auto-execute decisions)

| Tier | Agents | RAM | Spawn headroom | Downscale cooldown | Auto-execute inflate |
|------|--------|-----|---------------|-------------------|---------------------|
| Lightweight | kimi, minimax, pi, gemini | 100–300 MB | 1.3x | 120s | Yes |
| Medium | claude, cursor, amp | ~800 MB | 1.3x | 300s | Yes |
| Heavy | opencode, kilo | 1400–2500 MB | 1.3x | 600s | No (recommend only) |

**1.3x headroom multiplier** accounts for fork+exec transient spike and OS overhead. Example: opencode (2500 MB) requires 3250 MB free before spawn.

### Inflate trigger (all must be true)

```
queue_depth > effective_idle_agents * 2 -- effective_idle = agents with context_pct < 70%
ram_free_mb > agent_ram_mb * 1.3        -- 1.3x headroom (live /proc/meminfo, not heartbeat JSON)
node_ceiling_not_exceeded               -- hard cap enforced in code
token_status != RED|DEPLETED            -- monthly budget
window_5h_pct < 90%                     -- 5h window (anthropic + openai only)
week_pct < 90%                          -- weekly limit (anthropic + openai only)
no_spawn_in_last_30s_on_node            -- rate limiter, prevents spawn storm
```

**Note:** Context > 70% agents are EXCLUDED from `effective_idle_agents`. An agent at 75% context cannot accept new tasks.

### RAM emergency thresholds (reactive, higher priority than patrol)

```
< 70% used:  normal
70–80% used: block new spawns, emit warning
80–85% used: deflate oldest idle agent (Phase 2)
85–92% used: deflate all idle agents immediately (Phase 2)
> 92% used:  emergency deflate active agent, requeue task (Phase 2)
```

### Deflate trigger (Phase 2 only — all must be true)

```
agent idle > downscale_cooldown[tier]   -- 120s/300s/600s by tier
queue has no tasks for this agent's capability
agent context_pct < 10%                 -- don't kill mid-task
no manual-spawn marker (.forge/autoscale/manual/{node}-{agent}.json)
```

### Node RAM gates (hard limits — never override)

| Node | RAM | Max concurrent agents | Hard limit |
|------|-----|----------------------|------------|
| node-1 | 16 GB | 2 | Never spawn opencode/kilo |
| node-2 | 64 GB | 6 | All types OK |
| node-3 | 48 GB | 4 | All types OK |
| node-4 | 16 GB | 2 | Never spawn opencode/kilo |
| node-5 | 16 GB | 2 | Never spawn opencode/kilo |

---

## Architecture

### Pre-condition: Enforce hard ceilings FIRST (Step 0)

**Critical finding:** node-1 currently shows 7 active agents despite a documented 2-agent maximum. Autoscaling on top of a system that already violates its own limits will amplify the problem. Before any autoscaling ships, the Go daemon must enforce hard per-node ceilings in code:

```go
var NodeHardCeilings = map[string]int{
    "node-1": 2, "node-4": 2, "node-5": 2,  // 16GB nodes
    "node-3": 4,                          // 48GB
    "node-2": 6,                          // 64GB
}
```

Any spawn that would exceed this ceiling returns an error regardless of RAM math.

### Phase 1 — Inflate-ONLY, daemon patrol (this sprint)

**Key decision:** Autoscaling is a **Go daemon patrol** (`fleetScaleRecommendPatrol`), NOT an orchestrator action. The orchestrator is a Claude agent subject to context limits; the patrol runs as a long-lived Go process, immune to context exhaustion.

1. `fleetScaleRecommendPatrol` runs every **2 minutes** in `StandardPatrols()`
2. Reads live RAM from `/proc/meminfo` (not stale heartbeat JSON)
3. Evaluates inflate-only decisions (no autonomous deflation in Phase 1)
4. Writes `ScaleRecommendation` rows (action=inflate, agent_type, node, reason, token_budget_status)
5. `forge fleet recommendations` shows pending recommendations
6. Orchestrator approves with `forge fleet apply <rec-id>`

**Spawn safety chain (all must pass):**
```
1. node ceiling not exceeded
2. live RAM available > agent_ram * 1.3
3. no spawn in last 30s on this node (rate limiter)
4. provider token status != RED or DEPLETED
5. 5h window not depleted (anthropic/openai only)
6. weekly limit < 90% used (anthropic/openai only)
7. spawn failure count for this type < 3 in last 1h
```

**Post-spawn liveness gate:** After spawn, wait up to 90s for agent to appear in `agent_heartbeats` table or WebSocket registry. If absent after 90s, kill tmux window and increment failure counter. After 3 consecutive failures: 1h circuit break for that agent type.

**Kill switch:** `.forge/autoscale-disabled` sentinel file — checked before every decision. Write it to halt all autoscaling immediately.

### Phase 2 — Deflation + Token auto-tracking (after Phase 1 stable 2+ weeks)

- Autonomous deflation with per-type cooldowns (120s/300s/600s by weight class)
- Agents self-report token usage via `forge work --daemon` after each task
- Token budget auto-tracking via `/proc/meminfo`-style reads
- Multi-node coordination via claim-before-spawn (eliminates double-spawn race)
- Add `auto_approve` flag per node for lightweight spawns

### Phase 3 — ML-based (future)

- Historical queue depth + agent utilization → predictive scaling
- Cost-per-task optimization across providers
- Cross-node work stealing

---

## New API Endpoints

```
GET  /api/fleet/inventory                    — all agents + status
GET  /api/fleet/recommendations              — pending scale recommendations
POST /api/fleet/recommendations/:id/apply    — execute a recommendation
POST /api/agents/:id/token-usage             — agents report token usage
GET  /api/token-budgets                      — current budget status per provider
POST /api/token-budgets/:provider/reset      — manual reset (after known billing cycle)
```

### POST /api/agents/:id/token-usage body

```json
{
  "tokens_used": 12500,
  "window_5h_used": 8000,
  "window_5h_resets": "2026-03-07T18:30:00Z",
  "week_resets": "2026-03-10T00:00:00Z"
}
```

Agents (or their work loop) call this after each task to keep budgets current.

---

## New CLI Commands

```bash
forge fleet recommendations              # list pending scale recommendations
forge fleet apply <rec-id>              # execute one recommendation (orchestrator approves)
forge fleet inventory                   # show all agents + RAM + context% + token status
forge fleet budget                      # per-provider budget table (~-prefixed = approximate)
forge fleet budget set <provider> <n>   # manually seed usage counter
forge fleet budget reset <provider>     # reset to 0 (new billing period override)
forge fleet budget log                  # recent entries from token-log.jsonl
```

Sample `forge fleet budget` output:
```
Provider     Agents          Status    5h%   Week%   Monthly           Resets
-----------  --------------  --------  ----  ------  ----------------  ---------
anthropic    claude, amp     OK        0%    18%     ~45k / ~500k tok  24 days
openai       opencode, kilo  RED       0%    29%     ~48k / ~50k tok   24 days
moonshot     kimi            WARNING   --    --      ~88k / ~100k req  24 days
google       gemini          OK        --    --      ~12k / unlimited  N/A
```

`~` prefix signals all values are approximations. Color: green=OK, yellow=WARNING, red=RED/DEPLETED.

---

## Integration with Dark Factory (ADR-033)

`fleetScaleRecommendPatrol` is registered as a standard patrol in `StandardPatrols()`:

```go
{ID: "fleet-scale-recommend", Fn: fleetScaleRecommendPatrol, Interval: 3 * time.Minute},
{ID: "token-budget-reset",    Fn: tokenBudgetResetPatrol,    Interval: 1 * time.Hour},
```

**Interaction with `confidenceApproveCompletedTasks`:**
- Fleet scaler does NOT approve tasks — separation of concerns
- High-confidence task completion → more queue throughput → fleet scaler may deflate (queue drains)
- Low confidence → stalled queue → fleet scaler may inflate more agents to retry

---

## Success Metrics (KPIs)

| Metric | Target |
|--------|--------|
| Agent utilization | >70% busy when queue non-empty |
| Queue wait time | <5 min for P1 tasks |
| OOM incidents | 0 (hard gate enforcement) |
| Token burn rate | <80% of 5h window per agent per window |
| Weekly depletion events | <2 per week per provider |
| False inflate (spawned, nothing to do) | <10% of inflates |

---

## Failure Mode Mitigations

| Failure | Mitigation |
|---------|-----------|
| Token tracking stale (agent doesn't report) | Deflate agent after 30min no heartbeat; assume CAUTION if budget unknown |
| RAM check race (two inflates simultaneously) | Serialize inflate via DB advisory lock (`SELECT ... FOR UPDATE` equiv in SQLite = WAL + retry) |
| Phantom 5h limit hit mid-task | Agent reports CAUTION → no new inflates; current agents finish naturally |
| Cascading deflate (deflate too many) | Min floor: always keep 1 idle agent per node regardless of RAM pressure |
| Node affinity violation | Hard check in patrol — never write recommendation for opencode/kilo on 16GB nodes |
| Orchestrator ignores recommendations | Recommendations expire after 10 minutes; stale ones auto-archived |

---

## Alternatives Considered

| Option | Decision | Reason |
|--------|----------|--------|
| Daemon fully autonomous (spawn/kill) | Deferred to Phase 2 | Trust not yet established; need human validation first |
| Orchestrator-only decisions (status quo) | Rejected | Doesn't scale to multi-node |
| Per-node daemon on each node | Rejected | Complexity; node-1 as hub is the architecture |
| ML-based predictive scaling | Deferred to Phase 3 | Needs historical data first |
| Ignore 5h rolling limits | Rejected | Causes mid-session exhaustion, agent becomes unresponsive |

---

## Consequences

**Positive:**
- Zero OOM incidents from fleet growth (hard RAM gates)
- Token exhaustion visible before it hits (CAUTION zone)
- Node lead can scale from 1 → 4 agents in one `forge fleet apply` command
- Weekly and 5h limits surface in `forge fleet token-budgets` — no more surprises

**Negative:**
- Agents must self-report token usage (requires work loop instrumentation)
- Phase 1 still requires human to run `forge fleet apply` — not zero-touch
- SQLite WAL mode required for safe concurrent patrol writes

---

## Implementation Plan

### Sprint S74 (this sprint)

| Item | Owner | Files |
|------|-------|-------|
| Migration 037 (agent_inventory) | worktree | `cmd/forge-v3/migrations/037_agent_inventory.sql` |
| Migration 038 (token_budgets) | worktree | `cmd/forge-v3/migrations/038_token_budgets.sql` |
| `fleet_scaler.go` — patrol + recommendations | worktree | `cmd/forge-v3/fleet_scaler.go` |
| API endpoints (inventory, recommendations, token-usage) | worktree | `cmd/forge-v3/main.go` |
| CLI: `forge fleet recommendations/apply/inventory/token-budgets` | worktree | `cmd/forge/fleet.go` |
| Patrol registration in `StandardPatrols()` | worktree | `cmd/forge-v3/patrol.go` |

### Sprint S75

- Phase 2: `auto_approve` flag, autonomous deflate
- Work loop instrumentation: `forge work --daemon` reports token usage after each task

---

## Strategic Questions (Your Decisions Needed)

1. **How do we read the 5h and weekly reset times?** The `/usage` output from Claude Code and the Codex header both show reset times. Options:
   - (a) Orchestrator manually runs `forge fleet budget set anthropic --week-pct 18 --week-resets 2026-03-10T04:20:00Z` after checking `/usage`
   - (b) `forge work --daemon` wraps the agent CLI and parses `/usage` output automatically
   - (c) Start Phase 1 with manual only; add auto-parse in Phase 2
   **Recommended:** (c) — manual is good enough for Phase 1

2. **Cross-node recommendations?** Phase 1 stores recommendations in node-1's SQLite. node-2's lead can't see them. Options:
   - (a) node-1-as-hub is sufficient — node-2 lead checks `forge fleet recommendations` via `FORGE_API_URL=http://node-1:8081`
   - (b) Write recommendations to `.forge/xnode/scale-inbox/{node}.jsonl` for local consumption
   **Recommended:** (a) — node-1 as hub is the existing architecture

3. **`forge fleet apply inflate` execution?** Prya can't SSH to node-2 for cross-node spawns. Options:
   - (a) Phase 1 scope: only spawn on the LOCAL node (orchestrator is on node-1, spawns on node-1 only)
   - (b) Write `.forge/xnode/scale-inbox/{node}.jsonl`; target node pulls and executes on next git sync
   **Recommended:** (a) for Phase 1 — cross-node spawn in Phase 2 via xnode inbox

4. **Kimi deflate safety?** Kimi K2.5 enters extended thinking and doesn't report context_pct reliably. Should deflate timeout be 30min for kimi (vs 120s for other lightweight agents)?
   **Recommended:** Yes — kimi gets 30min idle threshold regardless of tier classification

5. **Token budget initialization?** We have real numbers now (anthropic: 18% weekly used, openai: 29% weekly). Seed them immediately with `forge fleet budget set`. Accept approximation — the `~` prefix is explicit about uncertainty.

6. **Hard ceiling enforcement now or after autoscaling ships?** node-1 shows 7 agents vs 2-agent max. Enforce ceilings immediately (Step 0) before any autoscaling code ships?
   **Recommended:** Yes — Step 0 is a prerequisite. The autoscaler amplifies existing violations.

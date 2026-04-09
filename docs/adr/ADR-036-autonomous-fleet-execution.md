# ADR-036: Autonomous Fleet Execution — Closing the Orchestration Loop

**Status:** Draft — Pre-Council
**Date:** 2026-03-07
**Author:** prya lead + council (gemini/kimi/pi — results pending)
**Supersedes:** None (extends ADR-035 Phase 2)

---

## Problem Statement

ADR-035 Phase 2 is complete. It delivers:
- Token budget tracking (per-node JSON + SQLite)
- Inflate/deflate **recommendations** (DB rows)
- Council lifecycle (TTL-protected markers)
- Zombie detection patrol

**The gap:** All of this is **passive**. The scaler writes recommendations but never
executes them. `auto_execute=1` is a flag that no code reads to act. The fleet is
described, monitored, and recommended-for — but never autonomously managed.

The user's goal is a system where:
1. Each node orchestrator **actually** inflates/deflates its local fleet
2. The token inventory gives the orchestrator full situational awareness of what
   agents it can and cannot afford to spawn
3. The orchestrator keeps worker agents **busy with meaningful work** — not idle
4. Agent onboarding (new types) is possible via CLI without code changes
5. Resource usage (RAM + CPU + tokens) is used efficiently at all times

---

## Five Execution Gaps (What ADR-035 Left Undone)

| Gap | Description | Impact |
|-----|-------------|--------|
| G1 | `auto_execute=1` recs never fire | Fleet never self-inflates |
| G2 | Deflation approved but no one kills the agent | Agents never self-deflate |
| G3 | RAM check uses stale heartbeat JSON, not live /proc/meminfo | Spawn decisions use wrong RAM data |
| G4 | Any agent can claim any task (no tier matching) | Kimi claims Go refactoring tasks; opencode sits idle |
| G5 | Idle agents starve — no proactive work generation | Expensive subscriptions go unused |

---

## ADR-036 Design

### Three Pillars

```
EXECUTE: Spawn/kill agents autonomously (close the recommendation gap)
  ↓
ROUTE: Right task to right agent tier + node (close the claim gap)
  ↓
FILL: Keep agents busy with meaningful work (close the idle gap)
```

---

## Pillar 1: EXECUTE — Close the Spawn/Kill Gap

### New patrol: `fleetAutoExecutePatrol` (2min interval)

Reads `auto_execute=1, status='pending'` recommendations and actually runs tmux commands.

```
Loop:
  1. Query: scale_recommendations WHERE auto_execute=1 AND status='pending' AND expires_at > now()
  2. For each recommendation:
     a. LIVE RAM check: read /proc/meminfo MemAvailable (not heartbeat JSON)
     b. Ceiling check: total agents on node < NodeHardCeilings[nodeID]
     c. Budget gate: checkInventoryGate(agentType, nodeID).Allowed == true
     d. Flap guard: last_scale_event_at < now - 60s
     e. If all pass: spawn agent → mark rec 'executed'
     f. If RAM fails: mark rec 'deferred-ram', log reason
     g. If budget fails: mark rec 'deferred-budget', expire
     h. If tmux fails: increment circuit breaker, mark 'failed'
```

### Agent spawn command registry

Hardcoded in Go (same philosophy as NodeHardCeilings — compile-time safety):

```go
var agentSpawnCommands = map[string]string{
    "kimi":    "kimi -y",
    "minimax": "minimax",
    "pi":      "pi",
    "gemini":  "gemini -y",
    "claude":  "claude --dangerously-skip-permissions",
    "cursor":  "cursor-agent -f",
    "amp":     "amp --dangerously-allow-all",
    "opencode":"opencode",
    "kilo":    "kilo",
}
```

Spawn environment (mandatory for every auto-spawned agent):
```bash
FORGE_AGENT_TYPE=fleet
FORGE_AGENT_NAME={agentType}-auto-{timestamp_short}
FORGE_API_URL=http://prya:8081
```

Window naming convention: `{agentType}-{N}` where N is next available suffix
(scan existing tmux windows to find the next free number).

### Live RAM gate: `readLiveRAMMB()`

```go
// readLiveRAMMB reads /proc/meminfo and returns MemAvailable in MB.
// Cached for 30s to prevent syscall spam across patrol cycles.
// Uses MemAvailable (not MemFree) — accounts for reclaimable buff/cache.
func readLiveRAMMB() (int, error) {
    data, err := os.ReadFile("/proc/meminfo")
    if err != nil { return 0, err }
    for _, line := range strings.Split(string(data), "\n") {
        if strings.HasPrefix(line, "MemAvailable:") {
            fields := strings.Fields(line)
            kb, err := strconv.Atoi(fields[1])
            if err != nil { return 0, err }
            return kb / 1024, nil  // kB → MB
        }
    }
    return 0, fmt.Errorf("MemAvailable not found in /proc/meminfo")
}
```

RAM requirements per tier (with 1.3x safety margin from ADR-035):

| Agent | RAM estimate | Spawn gate |
|-------|-------------|-----------|
| kimi | 100MB | > 130MB available |
| minimax | 100MB | > 130MB available |
| pi | 100MB | > 130MB available |
| gemini | 300MB | > 390MB available |
| claude, cursor, amp | 800MB | > 1040MB available |
| opencode | 2500MB | > 3250MB available |
| kilo | 1400MB | > 1820MB available |

Plus: always reserve 500MB for the orchestrator/daemon process itself.

### CPU stress gate

Use load average from /proc/loadavg. Threshold: `load1 > (ncpu * 0.8)`.
Above 80% sustained load → do not spawn new agents, even if RAM is available.
Spawn storms tend to spike CPU before RAM shows pressure.

### Graceful deflation (new `draining` state)

Before killing an agent:
1. Set `agent_inventory.drain_state = 'draining'`
2. Draining agents cannot claim new tasks (claim endpoint checks drain_state)
3. Wait up to 10min for current task to complete
4. After 10min timeout or task completion: `tmux kill-window -t forge:{window}`
5. Update agent_inventory status to 'offline', drain_state to 'drained'

This replaces the recommendation-only deflation with actual execution.
The `fleetAutoDeflatePatrol` (new) handles the drain lifecycle.

---

## Pillar 2: ROUTE — Right Task to Right Agent

### Task tier field

```sql
ALTER TABLE tasks ADD COLUMN required_tier TEXT NOT NULL DEFAULT 'any';
-- Values: 'any', 'lightweight', 'medium', 'heavy'
```

Task creation CLI extension:
```bash
forge task create "Refactor payment service" --tier medium --domain codeswiftr-com
forge task create "Update Royal Jelly" --tier lightweight --domain forge
forge task create "Implement OAuth provider" --tier heavy --domain codeswiftr-com
```

### Claim endpoint tier filter

```go
func agentCanClaimTask(agentType string, task Task) bool {
    if task.RequiredTier == "" || task.RequiredTier == "any" {
        return true
    }
    tierOrder := map[string]int{
        "lightweight": 1,
        "medium":      2,
        "heavy":       3,
    }
    agentTierVal := tierOrder[agentTier(agentType)]
    requiredTierVal := tierOrder[task.RequiredTier]
    // An agent can claim tasks at or below its tier
    // (medium agent can do lightweight tasks too)
    return agentTierVal >= requiredTierVal
}
```

### Node capability routing at task creation

When task is created with `required_tier=heavy`, the hub checks node capability manifest
to verify at least one node can handle it (has opencode/kilo and not in COOLDOWN).
If no capable node exists: task is created but with warning annotation.

---

## Pillar 3: FILL — Keep Agents Busy with Meaningful Work

### Orchestrator work strategy patrol (`orchestratorWorkStrategyPatrol`, 10min)

```
Every 10 minutes:
1. Count agents idle > 15min per tier, per node
2. Count queue depth (pending + queued tasks)
3. If queue depth > 0: do nothing — let agents claim naturally
4. If queue empty AND any agent idle > 30min:
   a. Read work catalog (.forge/work-strategy/catalog.toml)
   b. Filter catalog entries by: agent tier match, last_run_at < schedule
   c. Pick the highest-priority eligible entry
   d. Create task via forge task create with tier + domain
   e. Log: "[work-strategy] created task: {title} for idle {agentType} on {node}"
5. If agent idle > 120min AND queue empty AND not min-floor:
   → Emit deflation recommendation (not create work — too idle to be useful)
```

### Work strategy catalog: `.forge/work-strategy/catalog.toml`

```toml
[[tasks]]
id = "royal-jelly-sync"
title = "Royal Jelly Sync — Update lead-context.md"
tier = "lightweight"
domain = "forge"
schedule = "daily"
prompt = """
Audit the following domains for stale lead-context.md files (last updated > 7 days ago).
For each stale file, read recent commits and update the Current State, Next Priorities,
and Active Blockers sections. Domains: forge, cs, is, vc, lv, cc, ag, da, nf.
Write results to .forge/heartbeat/results/{agent}-RJ-SYNC.md
"""

[[tasks]]
id = "coverage-scout"
title = "Test Coverage Scout — Find Under-Covered Files"
tier = "lightweight"
domain = "forge"
schedule = "daily"
prompt = """
Run: cd cmd/forge-v3 && go test ./... -coverprofile=coverage.out
Identify the 5 source files with lowest coverage (< 40%).
For each: list the untested functions and estimate test effort (S/M/L).
Write results to .forge/heartbeat/results/{agent}-COVERAGE-SCOUT.md
"""

[[tasks]]
id = "dependency-audit"
title = "Dependency Audit — CVE + Outdated Packages"
tier = "lightweight"
domain = "forge"
schedule = "weekly"
prompt = """
Run: cd /home/openclaw/work/FORGE && go list -m all | head -30
Check for known CVEs in direct dependencies (use go-audit or advisory databases).
Also check Python harness: cd harness && uv pip list --outdated
Summarize findings in .forge/heartbeat/results/{agent}-DEP-AUDIT.md
"""

[[tasks]]
id = "pattern-harvest"
title = "Pattern Harvest — Mine Recent Commits for ADR-018 Patterns"
tier = "lightweight"
domain = "forge"
schedule = "weekly"
prompt = """
Review git log --oneline -30 for recent significant commits.
For each commit that adds a non-trivial pattern (new patrol, new FSM state, new API design):
Extract the pattern, describe it in 3 sentences, and upsert to the pattern library.
Use forge pattern create or write to .forge/patterns/.
Results: .forge/heartbeat/results/{agent}-PATTERN-HARVEST.md
"""

[[tasks]]
id = "doc-freshness"
title = "Doc Freshness Sweep — Stale Docs Inventory"
tier = "lightweight"
domain = "forge"
schedule = "weekly"
prompt = """
Find all docs/*.md files not updated in > 30 days (use git log --diff-filter=M).
For each stale doc: read it and determine if content is still accurate.
Flag docs that reference completed features incorrectly or mention deprecated paths.
Results: .forge/heartbeat/results/{agent}-DOC-FRESHNESS.md
"""

[[tasks]]
id = "agent-registry-sync"
title = "Agent Registry Sync — Verify Node Capabilities"
tier = "lightweight"
domain = "forge"
schedule = "daily"
prompt = """
Check: forge fleet capabilities | forge agent list
Verify that agent_inventory in SQLite matches actual tmux windows in forge session.
Flag any zombie agents (window exists, heartbeat stale > 5min).
Flag any missing agents (inventory row exists, no tmux window).
Results: .forge/heartbeat/results/{agent}-REGISTRY-SYNC.md
"""
```

### Work meaningfulness hierarchy

```
Priority 1 (ALWAYS over catalog): Real task queue items (assigned priority >= 8)
Priority 2: Unblocking tasks (task blocking other queued tasks)
Priority 3: Revenue tasks (domains: codeswiftr-com, leanvibe-ai)
Priority 4: Infrastructure tasks (domain: forge)
Priority 5: Maintenance catalog (from catalog.toml)
Priority 6: Research/exploration (no real deliverable)
```

Catalog tasks should NEVER be created if real priority 1-4 work exists in queue.

---

## Token Budget — Self-Reporting

### New API endpoint

```
POST /api/agents/{id}/token-usage
{
    "provider": "anthropic",
    "task_id": "TASK-123",
    "session_duration_seconds": 1800,
    "estimated_magnitude": "medium",  // low | medium | high | none
    "confidence": "estimated"         // estimated | provider-api
}
```

Magnitude → delta pct mapping (conservative estimates, 20% margin):

| Magnitude | Delta monthly_pct |
|-----------|-----------------|
| none | 0 |
| low | +1% |
| medium | +3% |
| high | +5% |

Human confirms actuals monthly with `forge fleet budget set` after checking billing.

### Future: Provider API polling (Phase 5)

Anthropic `/v1/organizations/usage` API exists (beta). When stable:
- Daemon polls hourly, updates token_budget_snapshots with exact figures
- Manual self-reporting becomes optional override for providers without APIs

---

## Agent Onboarding via CLI

New command: `forge agent type register`

```bash
forge agent type register \
  --type "codex" \
  --command "codex --auto-edit" \
  --tier medium \
  --provider anthropic \
  --ram-mb 800

forge agent type list    # show all registered types
forge agent type remove codex
```

Stored in `.forge/agent-types.toml` (checked in, not gitignored):

```toml
[[types]]
id = "codex"
command = "codex --auto-edit"
tier = "medium"
provider = "anthropic"
ram_mb = 800
allowed_nodes = []  # empty = all nodes respecting tier rules
```

This makes agent onboarding a CLI operation with no code changes required.

---

## Autonomous Council Invocation

### Trigger conditions (any one → council convened)

| Condition | Threshold | Council size |
|-----------|-----------|-------------|
| Same task type fails repeatedly | > 3 failures in 1h | 3 agents |
| Budget provider enters RED | any provider >= 90% | 2 agents (budget focus) |
| Fleet efficiency < 30% for sustained period | > 2h during 09:00–22:00 | 3 agents |
| New domain encountered (no lead-context.md) | first task for domain | 2 agents |
| Architectural decision blocking > 5 tasks | > 5 tasks with same blocker tag | 4 agents |

### Anti-spam design

- Minimum 6h between autonomous council invocations
- Max 1 autonomous council active at a time
- Council result must be acknowledged by human before next autonomous council
- Telegram webhook notification 5min before auto-council starts (human can cancel)

---

## Observability: `forge fleet plan`

New command showing the orchestrator's NEXT planned action:

```
forge fleet plan

FLEET PLAN — prya — next execution in ~3min 47s

PLANNED ACTIONS:
  [SKIP]    Inflate: queue=0, idle=1 — no inflate needed
  [SKIP]    Deflate: kimi idle 8min (need 15min) — not yet
  [CATALOG] gemini idle 34min → will create: "Coverage Scout" task in ~4min

TOKEN BUDGET FORECAST:
  anthropic  OK (9%/mo, resets 2026-04-01)
  kimi       CAUTION (88%/mo) — prefer substitute for new spawns
  openai     COOLDOWN (resets 2026-04-01 in 587h)

RESOURCE HEADROOM (live):
  Available RAM: 833MB
  Ceiling: 2/2 agents (at ceiling — no inflate possible on prya)
  sati: 32GB free, 3/6 ceiling (inflate possible)

CIRCUIT BREAKER: closed (0 consecutive errors)
```

---

## New Patrols Summary

| Patrol | Interval | Action |
|--------|----------|--------|
| `fleetAutoExecutePatrol` | 2min | Reads auto_execute=1 recs, spawns agents |
| `fleetAutoDeflatePatrol` | 2min | Manages drain state, kills drained agents |
| `orchestratorWorkStrategyPatrol` | 10min | Creates catalog tasks for idle agents |

---

## Schema Changes

```sql
-- 1. Task tier matching (migration 039)
ALTER TABLE tasks ADD COLUMN required_tier TEXT NOT NULL DEFAULT 'any';
CREATE INDEX idx_tasks_required_tier ON tasks(required_tier, status);

-- 2. Agent inventory drain state (migration 040)
ALTER TABLE agent_inventory ADD COLUMN drain_state TEXT NOT NULL DEFAULT 'normal';
-- Values: 'normal', 'draining', 'drained'
ALTER TABLE agent_inventory ADD COLUMN tmux_window TEXT;
ALTER TABLE agent_inventory ADD COLUMN last_task_at TEXT;
ALTER TABLE agent_inventory ADD COLUMN spawned_at TEXT;
ALTER TABLE agent_inventory ADD COLUMN spawn_command TEXT;

-- 3. Work strategy catalog execution log (migration 040)
CREATE TABLE IF NOT EXISTS work_strategy_log (
    id          TEXT PRIMARY KEY,
    catalog_id  TEXT NOT NULL,
    task_id     TEXT,
    node_id     TEXT NOT NULL,
    agent_type  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    status      TEXT NOT NULL DEFAULT 'created'
);
```

---

## Phase Plan

### Phase 3 (immediate — this sprint)

| Item | File | Notes |
|------|------|-------|
| `fleetAutoExecutePatrol` | `fleet_scaler.go` | Closes spawn gap; live RAM check |
| `readLiveRAMMB()` | `fleet_scaler.go` | /proc/meminfo reader with 30s cache |
| `agentSpawnCommands` map | `fleet_scaler.go` | Hardcoded spawn command registry |
| `fleetAutoDeflatePatrol` | `fleet_scaler.go` | Drain state + tmux kill |
| Migration 039: task `required_tier` | `migrations/039_task_tier.sql` | Tier routing field |
| Migration 040: inventory drain state | `migrations/040_agent_drain.sql` | Drain state + tmux_window |
| `forge fleet plan` CLI | `workflow_fleet.go` | Observability command |

### Phase 4 (next sprint)

| Item | Notes |
|------|-------|
| `orchestratorWorkStrategyPatrol` | Proactive work generation |
| `.forge/work-strategy/catalog.toml` | 6 evergreen maintenance tasks |
| `forge agent type register` CLI | Runtime agent onboarding |
| Agent token self-reporting API | `POST /api/agents/{id}/token-usage` |
| Claim endpoint tier filtering | Right task to right agent |

### Phase 5 (future)

| Item | Notes |
|------|-------|
| Provider API polling (Anthropic /usage) | Exact token tracking |
| Autonomous council invocation | With 6h cooldown + Telegram gate |
| Cross-node spawn coordination | Requires ADR-023 XNode completion |
| Predictive scaling | Task creation rate as leading indicator |

---

## Council Verdicts

*ADR-036 council: gemini/kimi/pi dispatched (TASK-SHARP-FLOW-328, TASK-SUPER-BEAM-753, TASK-PRIME-SYNC-888).*
*Results pending at time of draft. ADR-035 council insights (codex/kimi/gemini) directly applicable — integrated below.*

### ADR-035 Council Insights Integrated into ADR-036

**codex — `snapshot_age_seconds` spawn gate:**
Before medium/heavy spawns, check `token_budget_snapshots.snapshot_age_seconds`.
If shared-provider data is > 5min stale (next patrol hasn't run yet), fail closed.
Prevents two nodes from both approving spawns on stale "OK" budget data.
→ **Integrated into `fleetAutoExecutePatrol` pre-spawn gate.**

**kimi — Linux-first RAM gate:**
`/proc/meminfo` validates on Linux before Darwin nodes (nova/vega).
Precondition checklist for any new node daemon: token-budgets-{node}.json seeded,
ceiling enforced, prya API reachable over Tailscale.
→ **Integrated: Phase 3 targets Linux nodes only; Darwin shim deferred to Phase 4.**

**gemini — Capability-Aware Routing:**
The orchestrator placing tasks in the queue shouldn't route `opencode` tasks to prya
consumers — they'll never be claimed. Fix: Node Capability Manifest (already in ADR-035 P1)
must gate task creation, not just claim time.
→ **Integrated: `forge task create --tier heavy` warns when no capable node exists.**

**codex — prya floor agent:**
Keep one lightweight floor agent alive on prya continuously. NOT overnight scale-to-zero
on the lead node — the cost of one kimi-class agent (100MB) is trivial vs cold-start delay
and broken overnight work.
→ **Integrated: min_floor applies to lead node (prya) even during 00:00-06:00 window.**

---

## Key Metrics

| Metric | Target | Method |
|--------|--------|--------|
| Fleet execution gap closed | 0 unacted auto_execute=1 recs within 2min | `SELECT COUNT(*) FROM scale_recommendations WHERE auto_execute=1 AND status='pending' AND created_at < now-2min` |
| Agent idle time | < 30min sustained during business hours | `agent_heartbeats.last_seen` vs `tasks.assigned_to` |
| Catalog task creation rate | > 1 maintenance task/day per idle node | `work_strategy_log` daily count |
| Tier mismatch claims | 0 | New claim filter rejects mismatches |
| Token budget accuracy | manual confirm within 48h of billing period | `token_budget_log` |

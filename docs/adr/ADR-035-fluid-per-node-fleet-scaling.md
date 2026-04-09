# ADR-035: Fluid Per-Node Fleet Scaling

**Status:** Proposed
**Date:** 2026-03-07
**Author:** prya lead + 3-agent council (architect-advisor, patterns-researcher, backend-engineer)
**Supersedes:** ADR-034 (Phase 1 complete — this ADR governs Phase 2+ and corrects Phase 1 decisions)

---

## Context

ADR-034 Phase 1 shipped a daemon patrol (`fleetScaleRecommendPatrol`) on prya that writes
inflate recommendations requiring manual `forge fleet apply` approval. This is a good foundation.

The user now wants a **more fluid system** where:
- Each node lead/orchestrator can autonomously inflate/deflate its LOCAL fleet
- Decisions are based on node-local RAM, CPU cores, queue depth, and token budgets
- An agent inventory tracks which agent types are available vs on cooldown (with reset timestamps)
- When the lead needs council or extra help, it can spawn agents if local resources allow
- Worker agents stay busy with meaningful work — scaler maintains healthy queue-to-agent ratio

Three council agents reviewed the implications:
1. **Architect agent** — architecture options, queue ownership, token coordination, failure modes
2. **Patterns agent** — HPA, Nomad, Erlang, KEDA, Circuit Breaker, Borg, AWS ASG applicability
3. **Token agent** — complete schema, spawn gate, cooldown tracking, CLI display design

This ADR synthesizes their findings into a coherent design and corrects five ADR-034 decisions.

---

## Five Corrections to ADR-034

| # | ADR-034 Decision | Correction | Rationale |
|---|-----------------|------------|-----------|
| 1 | Per-node JSON token files aggregated at read time | Keep per-node JSON + add SQLite `token_budget_snapshots` table as materialized aggregate view | O(N) file reads eliminated; SQLite snapshot updated by daemon patrol; still partition-tolerant |
| 2 | All inflate recommendations require human approval | Auto-approve lightweight spawns (kimi, minimax, pi, gemini); human approval only for heavy (opencode, kilo) | Lightweight agents cost 100–300MB and are safe; requiring human approval for kimi at 2 AM blocks overnight work |
| 3 | Deflation signal = context_pct < 10% only | Compound gate: context% < 10% AND idle > cooldown AND no assigned task AND no tmux output ≥ 5min | An agent at 5% context may have just started a large task; context% alone is insufficient |
| 4 | Per-node daemon rejected ("complexity; prya as hub") | Per-node **scaling authority** accepted; per-node **task queue** rejected | Queue correctness requires centralized SQLite (exactly-once claim). Scaling correctness only requires local RAM + token data. These are separable concerns. |
| 5 | No min fleet floor | Min floor = 1 lightweight agent per active node always running | 100–300MB cost eliminates cold-start delay on first task after idle; pays for itself immediately |

---

## Decision

### Architecture: Hybrid — Hub Queue + Node-Local Scaling Authority

```
Task Queue (prya SQLite) — stays centralized, ensures exactly-once claim
     ↑ HTTP claim (FORGE_API_URL=http://prya:8081) from all nodes

Node-Local Scaling (each node daemon) — independent inflate/deflate decisions
     reads: local /proc/meminfo + local token budget JSON + queue depth from prya API
     writes: local agent_inventory + local token_budget JSON + scale_recommendations SQLite
```

**Why not fully distributed queues:** Distributed task assignment on top of JSONL/eventual
consistency creates split-brain and duplicate-claim failure modes. SQLite claim is atomic.
XNode (ADR-023) is 1% implemented. Do not distribute the queue until ADR-023 is complete.

**Why not hub-only (current):** prya (16GB, 4 cores) cannot SSH to other nodes to spawn agents
there. Cross-node inflate requires either xnode dispatch or node-local scaling authority.
Hub-only permanently blocks multi-node scaling.

**Why per-node scaling authority works:** Each node knows its own RAM live (via `/proc/meminfo`),
its own agent inventory (which tmux windows are running), and its own token budget (local JSON).
The only external dependency is queue depth, which is a single GET read from prya's API.

---

## Token Budget System

### State storage: per-node JSON + SQLite materialized view

**File:** `.forge/heartbeat/token-budgets-{node}.json` (authoritative live state)
**Table:** `token_budget_snapshots` in SQLite (materialized aggregate for cross-node API reads)
**Table:** `token_budget_log` (immutable audit trail of all budget events)

The JSON file is the source of truth. The SQLite snapshot is updated by the
`tokenBudgetCooldownResetPatrol` every 5 minutes. Remote nodes that need budget state for
other nodes call `GET /api/token-budgets?node=sati` and read from the SQLite snapshot.

### Status machine

```
OK       (0–79%)   → spawn freely
CAUTION  (80–89%)  → prefer substitute; spawn allowed, log warning
RED      (90–94%)  → stop new spawns; drain existing agents
DEPLETED (95%+)    → hard block; all tasks reroute to fallback providers
COOLDOWN           → hard block until cooldown_until timestamp; auto-cleared by patrol
```

DerivedStatus is computed from raw percentages — the worst axis wins:
`worst = max(monthly_pct, weekly_pct_if_applicable, 5h_pct_if_applicable)`

### Provider limits matrix

| Provider | Agents | 5h rolling | Weekly | Monthly |
|----------|--------|-----------|--------|---------|
| anthropic | claude, amp | YES | YES | YES |
| openai | opencode, kilo | YES | YES | YES |
| google | gemini | no | no | monthly |
| moonshot | kimi | no | no | monthly |
| minimax | minimax | no | no | monthly |
| inflection | pi | no | no | monthly |
| cursor | cursor | no | no | monthly |

### Auto-cooldown and auto-clear

- When any usage pct crosses 95%: daemon sets `cooldown_until = estimateCooldownUntil(provider)`
  (`estimateCooldownUntil` = nearest of: 5h window reset, weekly reset, monthly reset)
- `tokenBudgetCooldownResetPatrol` runs every 5min: scans all budget JSON files,
  clears any `cooldown_until < now`, recomputes derived status, writes back changed files
- Auto-clear does NOT reset usage percentages. If usage is still ≥95% after clear, the status
  re-enters DEPLETED → auto-cooldown loop immediately. Loop breaks only on manual
  `forge fleet budget set <provider> --monthly-pct 0` (explicit human confirmation of reset)

### Substitute chain (when provider blocked)

| Primary | Fallback chain |
|---------|---------------|
| claude, amp | gemini → kimi → pi |
| kimi | gemini → minimax → pi |
| opencode, kilo | kimi (no heavy-code substitute — escalate to human) |
| gemini | kimi → pi |
| minimax, pi | gemini → kimi |
| cursor | claude → gemini |

---

## Inflate / Deflate Logic

### Inflate trigger (all must pass)

```
1. queue_depth_avg_3cycles > effective_idle_agents * 2
   (3-cycle rolling average prevents false inflates from transient spikes)
2. live_ram_free_mb > agent_ram_mb * 1.3  (from /proc/meminfo, not heartbeat JSON)
3. node_ceiling_not_exceeded              (NodeHardCeilings compile-time constant)
4. checkInventoryGate(agentType, nodeID).Allowed == true
5. no_spawn_in_last_30s_on_node           (spawn storm prevention)
6. last_scale_event_at < now - 60s        (bidirectional flap prevention — covers both inflate AND deflate)
7. circuit_breaker_open == false          (< 3 consecutive systematic failures in last 1h)
```

`effective_idle_agents` = agents with context_pct < 70% AND no assigned task in queue.
Agents at 70%+ context cannot reliably accept new work.

### Deflate trigger — compound gate (Phase 2 only, all must pass)

```
1. agent.context_pct < 10%
2. agent.idle_time > downscale_cooldown[tier]  (120s lightweight / 300s medium / 600s heavy)
3. agent has no assigned or running task in prya queue
4. agent tmux window has zero output for >= 5 minutes
5. no manual-spawn marker at .forge/autoscale/manual/{node}-{agent}.json
6. node has >= 2 agents (never deflate below min floor of 1)
7. last_scale_event_at < now - deflate_cooldown[tier]
```

### Auto-approve tiers (Phase 1 update)

| Tier | Agents | RAM | auto_execute | Requires human |
|------|--------|-----|-------------|----------------|
| Lightweight | kimi, minimax, pi, gemini | 100–300MB | YES (after all 7 inflate gates pass) | No |
| Medium | claude, cursor, amp | ~800MB | NO (recommendation only) | Yes |
| Heavy | opencode, kilo | 1.4–2.5GB | NO | Yes + explicit RAM check |

**Rationale:** Lightweight agents have trivial RAM footprint and monthly-only token limits.
Auto-approving them at 2 AM when queue has 10 tasks is the right behavior. Medium agents
cost ~800MB and Anthropic tokens (compound limits) — human confirmation prevents surprises.
Heavy agents have OOM risk on 16GB nodes — always require explicit human approval.

### Scale-to-zero policy

| Condition | Action |
|-----------|--------|
| Heavy agent (opencode, kilo) idle > 600s | Deflate immediately (Phase 2) |
| All agents idle + queue empty < 2h | Keep min floor alive (1 lightweight) |
| All agents idle + queue empty >= 2h | Full scale-to-zero (overnight mode) |
| Overnight mode + new task arrives | Spawn 1 lightweight immediately (cold-start: 30–60s) |

### Node ceiling enforcement (Step 0 — prerequisite to all scaling)

```go
var NodeHardCeilings = map[string]int{
    "prya": 2, "vega": 2, "gaea": 2,   // 16GB nodes
    "nova": 4,                           // 48GB
    "sati": 6,                           // 64GB
}

var ForbiddenAgentTypes = map[string][]string{
    "prya": {"opencode", "kilo"},
    "vega": {"opencode", "kilo"},
    "gaea": {"opencode", "kilo"},
}
```

These are **compile-time constants** — cannot be overridden at runtime.
The scaler cannot write a recommendation that violates either map.

---

## Min Floor

Every node with any queue activity must keep at least 1 lightweight agent running:
- **prya:** kimi (100MB — cheapest option; frees prya's 16GB headroom for orchestrator)
- **sati:** gemini (300MB — fast, reliable, no compound token limits)
- **nova:** gemini or kimi
- **vega/gaea:** kimi

Min floor agents are excluded from deflation even when queue is empty.
Exception: overnight scale-to-zero (queue empty ≥ 2h).

---

## Council-as-a-Service

Council sessions (spin up N agents for 20–40min of parallel review, then drain) are **not** a
scaling operation — they are a time-bounded fleet burst with fixed agent count determined by
the human, not queue math. Conflating them with the scaler creates heuristic confusion.

### `forge council start` — first-class operation

```bash
forge council start \
  --size 4 \
  --topic "ADR-036 review" \
  --ttl 30m \
  --agents "gemini,kimi,pi,minimax"
```

**What this does:**
1. Creates a `council` task in the queue with `type: council` and `metadata.council_id`
2. Creates N sub-tasks (one per council role) tagged with `council_id`
3. Writes manual-spawn markers at `.forge/autoscale/council/{council_id}-{agent}.json`
   → prevents fleet scaler from deflating council agents during the session
4. Bypasses the 30s spawn rate limiter (council is explicit, not reactive)
5. Sets TTL: after 30min, `councilCleanupPatrol` cancels remaining sub-tasks, removes
   markers, and deflates council agents (regardless of deflate cooldowns)
6. Aggregates results into `.forge/council/{council_id}/result.md`

**Council tasks bypass scaler heuristics:**
- `fleetScaleRecommendPatrol` ignores tasks with `type=council` when computing queue depth
- Council agents are not counted toward the node ceiling for the duration of the session
  (rationale: human explicitly authorized them — the ceiling gate's purpose is preventing
  accidental OOM, not blocking intentional fleet use)

---

## Scaler Circuit Breaker

The scaler is a critical system component that must fail safe:

```go
type ScalerCircuitBreaker struct {
    ConsecutiveErrors int
    LastErrorAt       time.Time
    OpenUntil         time.Time   // zero = closed (normal operation)
}

// 3 consecutive systematic failures → 1h circuit open
// Failure types that count: agent crashes on start, provider 401/429
// Failure types that do NOT count: transient (tmux window exists, port conflict)
// OOM failures: bypass circuit break entirely → trigger RAM gate review immediately
```

When circuit is open:
- Patrol logs `[fleet-scaler] CIRCUIT OPEN until {time} — no inflate decisions`
- Writes a `scale_recommendation` row with `action=circuit_break` status for observability
- Clears automatically after `OpenUntil` passes on next patrol run

---

## New API Endpoints

```
GET  /api/fleet/inventory                        — all agents + RAM + context% + token status
GET  /api/fleet/recommendations                  — pending scale recommendations
POST /api/fleet/recommendations/:id/apply        — execute a recommendation (orchestrator approves)
GET  /api/token-budgets                          — per-provider budget status (this node)
GET  /api/token-budgets?node={id}               — budget status for a specific node (from SQLite snapshot)
POST /api/token-budgets/{provider}/set          — manual seed usage counter
POST /api/token-budgets/{provider}/reset        — reset to 0 (new billing period override)
POST /api/agents/{id}/token-usage               — agent self-report token usage after task
GET  /api/fleet/council/{council_id}/status     — council session status
POST /api/fleet/council                         — start a council session
DELETE /api/fleet/council/{council_id}          — terminate council early
```

---

## New CLI Commands

```bash
# Token budget management
forge fleet inventory                           # agent inventory + token status table
forge fleet budget                              # per-provider budget table
forge fleet budget set <provider> [flags]       # manually seed: --monthly-pct, --week-pct, --5h-pct, --week-resets, --5h-resets
forge fleet budget reset <provider>             # reset to 0 (override after confirmed billing reset)
forge fleet budget log                          # recent entries from token_budget_log

# Scaling control
forge fleet recommendations                     # list pending scale recommendations
forge fleet apply <rec-id>                     # execute one recommendation (medium/heavy tier)
forge fleet inflate <agent-type> [--count N]   # explicit inflate (bypasses recommendations)
forge fleet deflate <agent-id>                 # explicit deflate one agent

# Council
forge council start --size N --topic "..." --ttl 30m [--agents "gemini,kimi"]
forge council status [--id <council-id>]
forge council stop [--id <council-id>]
```

### `forge fleet inventory` output

```
AGENT INVENTORY — node: prya — 2026-03-07 14:30 UTC
All usage values are approximate (~).

TYPE         PROVIDER     STATUS      ~MONTHLY  ~WEEKLY  ~5H    BUSY  IDLE  COOLDOWN
───────────────────────────────────────────────────────────────────────────────────────
claude       anthropic    AVAILABLE      9%       18%      0%     1     0    —
amp          anthropic    AVAILABLE      9%       18%      0%     0     0    —
opencode     openai       COOLDOWN      96%       29%      0%     0     0    resets in 24d 12h
kilo         openai       COOLDOWN      96%       29%      0%     0     0    resets in 24d 12h
gemini       google       AVAILABLE     12%        —        —      0     1    —
kimi         moonshot     CAUTION       88%        —        —      1     0    —
minimax      minimax      AVAILABLE      5%        —        —      0     2    —
pi           inflection   AVAILABLE      3%        —        —      0     1    —
cursor       cursor       AVAILABLE     20%        —        —      0     0    —
───────────────────────────────────────────────────────────────────────────────────────
Legend: ~ = approximate  CAUTION = prefer substitute  COOLDOWN = spawn blocked
```

---

## Implementation Phases

### Phase 1 (current sprint) — corrections to existing code

| Item | File | Change |
|------|------|--------|
| Hard ceiling enforcement | `fleet_scaler.go` | Add `NodeHardCeilings` + `ForbiddenAgentTypes` compile-time maps |
| Auto-approve lightweights | `fleet_scaler.go` | Set `auto_execute=1` for kimi/minimax/pi/gemini when all 7 inflate gates pass |
| Queue depth averaging | `fleet_scaler.go` | Track 3-cycle rolling average; persist in SQLite temp table |
| `last_scale_event_at` | `fleet_scaler.go` | Add to per-node row in `scale_recommendations` metadata |
| Failure type classification | `fleet_scaler.go` | `ScalerCircuitBreaker` with transient/systematic/OOM distinction |
| `token_budget.go` | New file | `ProviderBudget`, `NodeBudget`, `BudgetStore`, `checkInventoryGate` |
| Migration 038 | `migrations/038_token_budgets.sql` | `token_budget_log` + `token_budget_snapshots` |
| `tokenBudgetCooldownResetPatrol` | `patrol.go` | Register in `StandardPatrols()`, 5min interval |
| `forge fleet inventory` CLI | `workflow_fleet.go` | New cobra subcommand |
| `forge fleet budget` CLI | `workflow_fleet.go` | New cobra subcommand |
| Seed token budgets | `.forge/heartbeat/token-budgets-prya.json` | Write initial state (anthropic 18% weekly, openai 29% weekly) |

### Phase 2 (next sprint) — per-node autonomous scaling

| Item | Notes |
|------|-------|
| Deflation implementation | Compound 4-signal gate; per-tier cooldowns |
| Node-local scaler mode | `forge daemon --scaler-only` or lightweight `forge-agent-manager` binary |
| Deploy on sati | sati has 64GB — safest first node for autonomous scaling |
| Cross-node spawn via xnode | Requires ADR-023 completion (currently 1% implemented) |
| Min floor enforcement | Auto-spawn replacement if floor agent crashes |
| `forge council start` | Council-as-a-service with TTL + cleanup patrol |

### Phase 3 (future) — predictive and ML-based

| Item | Notes |
|------|-------|
| Throughput-based scaling | Replace `queue > agents*2` with `queue / (agents × hourly_rate)` from task history |
| Task creation rate as leading indicator | Slope of queue depth over 10min window |
| Context% trend-based pre-spawn | Detect agents approaching 70% and pre-warm replacement |
| Token velocity tracking | Tokens/hour rate vs monthly budget remaining |
| Cross-node bin-packing | 60% RAM + 40% token composite score for node selection |

---

## Failure Mode Mitigations

| Failure | Impact | Mitigation |
|---------|--------|-----------|
| prya outage | No new task claims; running agents continue | Accept — running tasks not lost; agents retry claim every 30s. Future: litestream SQLite replication to sati (not now). |
| Token budget overshoot (two nodes spending same account) | Overshoot by 1-2 agent sessions | CAUTION zone at 80% provides 10% margin; orchestrator manually reconciles with `forge fleet budget set` after checking `/usage` |
| Spawn storm (multiple nodes react to same queue spike) | 9 agents spawned when 4 needed | 30s spawn rate limiter + `last_scale_event_at` bidirectional flap check |
| Deflation kills agent mid-task | Work lost, task stuck RUNNING | Compound deflation gate (4 signals) prevents deflation of active agents |
| Scaler bug causes OOM | Node thrashes | `ForbiddenAgentTypes` and `NodeHardCeilings` are compile-time constants — no runtime override |
| Scaler patrol encounters DB errors | Wrong decisions from partial data | `ScalerCircuitBreaker`: 3 consecutive errors → 1h circuit open; logs and emits alert recommendation |
| Council agents inflate without limit | Token exhaustion, RAM pressure | `forge council start` enforces count ceiling via manual-spawn markers; TTL-based auto-deflation |

---

## Key Metrics (KPIs)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Agent utilization | > 70% busy when queue non-empty | `assigned_tasks / active_agents`, sampled hourly |
| P1 queue wait time | < 5 min | `task.started_at - task.created_at` for priority=high tasks |
| OOM incidents | 0 | `dmesg | grep -i oom-killer` count per week |
| Token depletion events | < 2/week/provider | Count of `status → RED` transitions in `token_budget_log` |
| False inflate rate | < 10% | Agents spawned with no task claim within 5min / total spawns |
| Cold-start delay eliminated | 100% of nodes | Min floor agent alive on all active nodes |
| Cooldown tracking accuracy | Manual confirmation within 24h of auto-clear | `token_budget_log` audit trail |

---

## Council Verdicts (2026-03-07)

Council: gemini, codex, kimi (3 agents). glm shipped Phase 1 implementation during council.
Full verdicts: `.forge/heartbeat/results/{gemini,codex,kimi}-adr035-council.md`

| Q | Question | Verdict | Vote |
|---|----------|---------|------|
| Q1 | Daemon deployment priority | **sati first** — 64GB safe testbed; precondition: prya Phase 1 gates complete | 3/3 |
| Q2 | Seed token budgets now? | **YES** — seed with live data; openai `COOLDOWN until 2026-04-01` immediately | 3/3 |
| Q3 | prya ceiling: 2 or 3? | **Keep at 2** — preserves medium-tier slot; OOM on prya kills Command Center | 2/3 |
| Q4 | Council in Phase 1 or 2? | **Phase 2** — needs TTL + cleanup patrol + manual-spawn markers first | 3/3 |
| Q5 | Overnight scale-to-zero? | **YES** — 30–60s cold-start acceptable; restrict to 00:00–06:00 window | 2/3 |
| Q6 | openai COOLDOWN until Apr 1? | **YES** — 96% is depleted; heavy code tasks escalate to human | 3/3 |
| Q7 | Per-node JSON vs single ledger? | **Per-node JSON + SQLite snapshot** — partition-tolerant, zero git merge conflicts | 3/3 |
| Q8 | Medium-tier auto-approve on sati? | **Node-differentiated** — auto on sati (64GB), manual on prya/vega/gaea | 3/3 |

### Three Wildcards from Council

**codex — `snapshot_age_seconds` spawn gate:**
Before medium/heavy spawns, check freshness of cross-node budget data. If shared-provider
snapshot is too stale (>N minutes since last reconciliation), fail closed rather than spawn
on stale data. Add `last_reconciled_at` to `token_budget_snapshots`.

**gemini — Node Capability Manifest:**
`ForbiddenAgentTypes` lives in the daemon binary, but the task queue (on prya) doesn't know
about it. The orchestrator may route an `opencode` task to a queue where prya is the only
consumer — it will never be claimed. Fix: sync a `node_capability_manifest.json` to the hub
so the orchestrator filters routing at task creation time, not at claim time.

**kimi — `agentLivenessPatrol` (zombie detection):**
An agent can exist in tmux but have a stale heartbeat (crashed CLI, stuck prompt). The scaler
currently counts tmux presence as "alive." Add a `agentLivenessPatrol` that checks heartbeat
age: if a window exists but heartbeat is >5min old, the agent is a zombie — kill the window
and respawn. Prevents silent task loss. P1 for Phase 2.

### Phase 1 Shipped (2026-03-07, glm + kimi)

| Item | Commit | Status |
|------|--------|--------|
| ADR-035 written | `1546c838` | ✅ |
| `forge fleet budget show/set/reset` | `5a54b5b5`, `7972ed40` | ✅ live |
| `forge fleet inventory` | `5a54b5b5`, `7972ed40` | ✅ live |
| Token budget JSON seeded (prya) | `7972ed40` | ✅ openai COOLDOWN, kimi CAUTION |
| PROMPT.md updated | `65ffc4c1` | ✅ |

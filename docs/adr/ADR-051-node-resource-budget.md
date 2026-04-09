# ADR-051: Node Resource Budget & Enforcement

**Status:** PROPOSED
**Date:** 2026-04-05 (expanded 2026-04-06 with gemini research)
**Author:** nova (orchestrator)

---

## Decision

Node resource budgets are enforced in code via a spawn gate, not documentation. Three layers: static config, daemon-enforced gate, dynamic pressure signals.

### Spawn Gate: `cmd/forged/spawn_gate.go`

New file, clean separation from `fleet_scaler.go` (which handles recommendations, not enforcement).

```go
func CanSpawn(agentType, nodeID string) (bool, string)
```

Encapsulates all 4 budget signals. Returns `(false, reason)` on denial. Shared by daemon spawn path and CLI (`forge node check`).

| Check | Type | Behavior |
|-------|------|----------|
| `count(active) < max_agents` | HARD | Block spawn, suggest alternate node |
| `live_ram_free_mb > 2048` | HARD | Block spawn, log RAM state |
| `model NOT IN denied_models` | HARD | Block spawn, return 422 + reason |
| Token budget headroom | SOFT | Warn, allow spawn |

### Static Budget: `config/nodes.yaml`

```yaml
nodes:
  prya:
    ram_gb: 16
    max_agents: 2
    denied_models: [opencode, kilo, amp]
  sati:
    ram_gb: 64
    max_agents: 6
    denied_models: []
  nova:
    ram_gb: 48
    max_agents: 4
    denied_models: []
  vega:
    ram_gb: 16
    max_agents: 2
    denied_models: []
  gaea:
    ram_gb: 16
    max_agents: 3
    denied_models: [opencode, kilo]
```

Daemon reads on startup. Fail-safe default if missing (deny all spawns, log error with recovery: "create config/nodes.yaml").

### Dynamic Pressure (SOFT)

Fleet scaler recommends down-scale when RAM > 80% for 10min or load > CPU count x 1.5. Logged, not auto-applied (humans approve scale-down).

---

## Context

Fleet runs across 5 nodes (16-64GB RAM). Spawning heavy agents on low-RAM nodes causes OOM. CLAUDE.md documents caps but enforcement is advisory. `fleet_scaler.go` exists but hard-ceiling check is not wired.

**Incident:** prya OOM'd at 93% RAM when OpenCode spawned. Manual CLAUDE.md caps prevented recurrence but rely on human vigilance.

**Audit (2026-04-05):** ADR-034 `RecommendScale` has missing symbol; ADR-035 ceilings not enforced in spawn gate.

---

## Open Questions Resolved

| Question | Resolution | Source |
|----------|-----------|--------|
| Where does spawn gate live? | New `cmd/forged/spawn_gate.go` — separates enforcement from recommendation | gemini research |
| Per-domain model preferences? | Yes, add `domain_preferences.yaml` (e.g., mirrably prefers claude) | gemini research |
| Hot-swap on denied model? | Return 422 Soft Error. No auto-fallback — tier mismatch risk too high | gemini research |

---

## Alternatives Considered

| Alternative | Verdict | Reason |
|-------------|---------|--------|
| Keep in `fleet_scaler.go` | Rejected | 600+ line file mixing strategy + enforcement; harder to test |
| Per-node daemons | Rejected (ADR-025) | Complexity not justified for 5 nodes |
| Documentation-only caps | Status quo, rejected | Produced OOM risk — insufficient |
| Kubernetes limits | Rejected | Overkill for tmux fleet |

---

## Consequences

**Positive:**
- OOM prevented at dispatch time, not after crash
- Denial reasons explicit (not mysterious tmux failures)
- New nodes onboard by editing one YAML file
- CLI can share gate logic (`forge node check AGENT NODE`)

**Negative:**
- Config file dependency on daemon startup (mitigated: fail-safe default)
- ~50ms added to dispatch path (acceptable)
- `domain_preferences.yaml` adds another config surface (mitigated: optional, falls back to nodes.yaml)

---

## Implementation Status

- [x] Caps documented in CLAUDE.md
- [ ] `config/nodes.yaml` created
- [ ] `cmd/forged/spawn_gate.go` with `CanSpawn()` function
- [ ] Daemon reads nodes.yaml on startup
- [ ] Spawn gate wired into `spawnAgent()` call-sites
- [ ] `RecommendScale` symbol fixed (ADR-034 partial → full)
- [ ] `forge node check` CLI command
- [ ] `domain_preferences.yaml` (optional, Bundle C)

---

**Next:** Implement `spawn_gate.go` when Bundle B patrols are stable.

# Control Plane Objectives

**Status:** STALE — written Feb 2026, references deprecated patterns (forge-harness, FORGE_WEBHOOK_TOKEN, codex/control-plane-parity branch). Kept for historical context. Current state lives in docs/PLAN.md and docs/INFRASTRUCTURE_MAP.md.  
**Created:** 2026-02-25  
**Branch:** codex/control-plane-parity (archived)  

---

## 1. Goals

1. **Unified Control Plane**: Prya backend (`/api/*`) is the single source of truth for all operational state
2. **Canonical Client**: `forge` CLI v2 is the primary client surface for all nodes
3. **Data Fidelity**: Dashboard/TUI render real backend data, never synthetic/placeholder data
4. **Cross-Node Coordination**: Lead-to-lead messaging via `forge lead send --strict` is the canonical cross-node pattern
5. **Operational Clarity**: Every operator action has predictable, documented outcomes

---

## 2. Non-Goals

1. **Not** replacing Prya backend with distributed state
2. **Not** supporting multiple CLI versions simultaneously
3. **Not** maintaining backward compatibility with `forge-harness` CLI
4. **Not** adding new operational concepts beyond existing CLI v2 surface
5. **Not** modifying runtime artifact semantics (`.forge/xnode/` files remain operational, not product)

---

## 3. Operating Constraints

| Constraint | Rationale |
|------------|-----------|
| Auth via `FORGE_WEBHOOK_TOKEN` in `~/.forgerc` | Single, auditable credential path |
| Node ID = `$(hostname -s)` | Predictable, reproducible identity |
| Cross-node via `forge lead send --strict` | Durable, ack'd, realtime-verified |
| Local dispatch via `forge dispatch send` | 95%+ reliability, no tmux hacks |
| Runtime files in `.forge/xnode/` excluded from git | Operational artifacts, not product code |
| Legacy docs archived with pointer stubs | Preserve history, guide to canonical |

---

## 4. Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Dashboard widgets with real data | ~60% | 100% |
| CLI command consistency across nodes | 70% | 100% |
| Cross-node lead workflow success | 85% | 99% |
| Docs policy check pass rate | 80% | 100% |
| Fleet readiness (non-timeout dispatches) | 75% | 95% |

---

## 5. Canonical Command Grammar

### 5.1 Configuration
```bash
forge config rc-init --api-url <url> --api-token <token> --node-id <id>
forge config rc-show
forge config context list
forge config context use <name>
forge config context current
```

### 5.2 Status and Health
```bash
forge status
forge doctor
forge nodes list --offline
forge fleet ready
forge fleet ready --repair
```

### 5.3 Cross-Node Coordination
```bash
forge lead preflight --to-node <node>
forge lead send --to-node <node> --task-id <id> --summary "..." --strict
forge lead inbox
forge lead ack --message-id <id> --require-realtime-delivery
forge node list
```

### 5.4 Local Dispatch
```bash
# Canonical dispatch command (agent-message.sh has been deleted)
forge dispatch send forge:<agent> "Read .forge/dispatches/FILE.md — EXECUTE now"
```

---

## 6. References

- Canonical workflow: `./docs/runbooks/CANONICAL_WORKFLOW.md`
- CLI v2 reference: `./docs/FORGE_CLI_V2_REFERENCE.md`
- Prya lead workflow: `./docs/runbooks/PRYA_LEAD_XNODE_WORKFLOW.md`
- Execution checklist: `./docs/plans/CONTROL_PLANE_REMAINING_WORK_EXECUTION_CHECKLIST_2026-02-25.md`

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-02-25 | Initial creation | Senior Engineer |

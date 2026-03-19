# ADR-014: Retire Command Center; forge-v3 is Sole Control Plane

**Date:** 2026-03-05
**Status:** Accepted — Complete (2026-03-06, S73)
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

The FORGE control plane currently runs on two parallel stacks:

1. **Command Center (Python/React)**: A FastAPI `webhook_server` with 50+ API modules, a React PWA frontend (~15K LOC), SSE streaming, and a Python `notification_harness`. Deployed on node-1 as a uvicorn process.
2. **forge-v3 (Go)**: The new Go binary (ADR-008) providing HTTP API on `:8081`, WebSocket on `:8082`, and SQLite event store.

Maintaining both creates operational burden: two deployment pipelines, two sets of API contracts, race conditions between Python and Go state, and a React build pipeline that adds CI complexity. The Command Center was essential when v3 didn't exist, but v3 now covers the core control-plane functions (task queue, dispatch, approvals, event sourcing, patrols).

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Keep both indefinitely | No migration risk | Double maintenance, state conflicts, two deployment targets | ❌ REJECTED |
| Wrap CC behind v3 proxy | Gradual migration | Still running Python on control-plane path, added latency | ❌ REJECTED |
| **Delete CC, v3 is sole control plane** | **One binary, one deployment, one source of truth** | **Must backfill missing CC features into v3** | ✅ **ACCEPTED** |

---

## Decision

We will retire the Python Command Center and React PWA. The `forged` Go binary becomes the **sole control plane server** on every node.

### What Dies

| Component | Location | Approx Size | Replacement |
|-----------|----------|-------------|-------------|
| FastAPI webhook_server | `harness/command_center/api/*` | 50+ modules | v3 HTTP API `:8081` |
| React PWA | `harness/command_center/src/` | ~15K LOC | HTMX web UI + iOS app (ADR-019) |
| CC SSE streaming | `webhook_server/events/` | ~2K LOC | v3 WebSocket + SSE shim (ADR-017) |
| Python notification_harness | `forge_harness/notification_harness.py` | ~1K LOC | v3 patrol notifications |
| CC database (Postgres/SQLite hybrid) | CC internal | — | v3 SQLite event store |

### What Stays

- **v3 Go binary**: HTTP API `:8081`, WebSocket `:8082`, SQLite event store
- **v3 patrols**: Health, timeout, approval expiry, context sync, git cleanup, queue depth
- **v3 approval workflow**: Confidence scoring, tiered approvals
- **Python workers**: Agent adapters, Ralph Loop — connect via WebSocket (unchanged)

### Missing Features to Backfill in v3

These CC features must be ported to v3 before full retirement:

| Feature | CC Module | v3 Target | Time Estimate |
|---------|-----------|-----------|---------------|
| Fleet snapshot endpoint | `api/fleet.py` | `GET /api/fleet/snapshot` | 3 days |
| Pattern library CRUD | `api/patterns.py` | `GET/POST /api/patterns/*` (ADR-018) | 5 days |
| Agent metrics/history | `api/agents.py` | `GET /api/agents/:id/metrics` | 3 days |
| CC compatibility shims | Various | Thin HTTP adapters for legacy callers | 5 days |
| Node health aggregation | `api/nodes.py` | `GET /api/nodes/health` | 2 days |

**Time-boxed: 4 weeks** for full backfill. After that, CC code is deleted from the repository.

---

## Consequences

### Positive

1. **Single binary deployment**: One `forge` binary per node, no Python on the control-plane path
2. **No React build pipeline**: Eliminates npm/webpack/vite complexity from CI
3. **Single source of truth**: All state in v3 SQLite, no cross-system sync bugs
4. **Reduced ops burden**: One process to monitor, one log stream, one restart command
5. **Faster cold start**: Go binary starts in <100ms vs uvicorn+React ~5s

### Negative

1. **Migration effort**: 4-week backfill for missing v3 features
2. **Legacy callers**: Any script hitting CC endpoints must be updated or use compatibility shims
3. **Feature gap risk**: Some CC features may be missed during audit

### Neutral

1. **Python remains for workers**: Only the control-plane path loses Python; agents still use Python
2. **SSE compatibility**: Thin SSE shim preserves existing SSE consumers during transition

---

## Supersedes

- **ADR-009** (v3-hook-integration): Partially — hook integration now targets v3 exclusively
- **ADR-010** (v3-xnode-evolution): Partially — xnode communication moves entirely to v3

## Timeline

- **Phase 1** (Week 1-2): Audit all CC endpoints, identify callers, build compatibility shims
- **Phase 2** (Week 3-4): Port missing features to v3, update all scripts/callers
- **Phase 3** (Week 5): Delete CC code from repository, remove React build from CI
- **Rollback**: During Phase 1-2, CC remains operational. Feature flags route traffic.

---

## References

- ADR-008: FORGE CLI v3 Rewrite
- ADR-017: Unified Event Bus and Streaming Strategy
- ADR-019: Forge Terminal iOS as Mobile Control Surface
- Command Center Audit: `docs/audits/COMMAND_CENTER_AUDIT.md`

---

**Status: PROPOSED**

Decision review target: 2026-03-10

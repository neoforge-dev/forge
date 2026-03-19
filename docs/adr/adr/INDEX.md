# ADR Index

> **Last updated:** 2026-03-10 — ADR-041 ACCEPTED (council 3-0); ADR-042 public/private split proposed; ADR-040 complete
> Status legend: ✅ Implemented · 🔧 Partial · 📋 Proposed · ⏸ Hold · ❌ Superseded

| ADR | Title | Status | Notes |
|-----|-------|--------|-------|
| [000](ADR-000-architecture-overview.md) | Architecture Overview | ✅ | Reference doc |
| [001](ADR-001-cli-v2-unified-entry.md) | CLI v2 Unified Entry | ❌ | Superseded by ADR-029 (CLI v4) |
| [002](ADR-002-dispatch-consolidation.md) | Dispatch Consolidation | ❌ | Superseded by dispatch-decision.md |
| [007](ADR-007-beads-integration.md) | Beads Task Graph | ❌ | **SUPERSEDED (council 3-0, 2026-03-09)** — v3 SQLite task system (`forge task`) permanently replaces both Beads AND the planned `blocked_by` lightweight alternative. Self-claiming via `forge work --daemon` eliminates the sequencing problem that drove this ADR. If task dependencies needed at scale, implement `blocked_by` field in Go task schema (not Python harness). |
| [008](ADR-008-forge-v3-rewrite.md) | V3 Go Rewrite | ✅ | Running on node-2 + node-1 |
| [009](ADR-009-v3-agentic-patterns.md) | Agentic Patterns | ✅ | FSM wired ✅; 26 patrols real ✅; Dark Factory F1 auto-promote live S75 |
| [010](ADR-010-v3-lease-system.md) | Lease System | ✅ | Functional: Claim/Renew/Release wired; recovery patrol active |
| [011](ADR-011-v3-websocket-protocol.md) | WebSocket Protocol | ✅ | Hub running on :8082 |
| [012](ADR-012-v3-confidence-scoring.md) | Confidence Scoring | ✅ | Live S74: weighted scoring (40% test/30% cov/20% lint/10% git); ADR-033 F3 patrol (S75) |
| [013](ADR-013-v3-race-mode.md) | Race Mode | ❌ | Withdrawn S73 — superseded by ADR-010 lease system |
| [014](ADR-014-retire-command-center.md) | Retire Command Center | ✅ | 8/8 groups NATIVE in v3. ADR-040 Wave 6 (2026-03-09): cli_v2 reduced to ios.py — Python harness deletion complete. |
| [016](ADR-016-adapter-supervisor.md) | Adapter Supervisor | ❌ | **SUPERSEDED by ADR-036 (council 2-1, 2026-03-09)** — adapter subprocess telemetry model was never built and is architecturally incompatible with reality: agents self-claim via HTTP, send heartbeats directly, run in tmux independently. ADR-036 (Autonomous Fleet Execution, COMPLETE) is the actual implemented model. |
| [018](ADR-018-pattern-library.md) | Pattern Library | ✅ | CLI→API wired S75; docs/patterns/ git-tracked + loadPatternsFromDocs() seeds DB on startup S78c |
| [019](ADR-019-forge-terminal-control-surface.md) | Terminal Control Surface | ⏸ | **HOLD (updated, council 2-1, 2026-03-09)** — iOS SSH terminal v1.0 COMPLETE (SFTP, port-forward, jump hosts). Phase 1 done. Fleet control views (FleetDashboardView, ApprovalInboxView, TaskDetailView, PatrolAlertView, NodeHealthView) not built — Phase 2. HTMX /ui web fallback is live on daemon. Resume after App Store submission gates clear. |
| [020](ADR-020-eliminate-sidecar-files-unified-protocol.md) | Eliminate Sidecar Files | ✅ | SQLite primary; JSONL dirs exist but 0 active files (verified S76 audit) |
| [021](ADR-021-unified-control-plane-consolidation.md) | Unified Control Plane | ❌ | Superseded by ADR-025 |
| [023](ADR-023-v3-xnode-evolution.md) | XNode Evolution | ✅ | Dual-write (DB+JSONL) real; Tailscale IP auto-detect FIXED S82; ack processor + retry serializer patrols (S89) — durable queue complete |
| [024](ADR-024-git-worktree-isolation.md) | Git Worktree Isolation | ✅ | Wired to claimTask + completeTask |
| [025](ADR-025-local-daemon-distributed-mesh.md) | Local Daemon per Node | ❌ | Superseded S82: hub model (node-1:8081) confirmed working at 5-node scale. Per-node daemon adds complexity without benefit. Revisit only if fleet >50 agents or hub latency >500ms. |
| [026](ADR-026-authentication-model.md) | Auth Model | ✅ | **ACCEPTED (council 2-1, 2026-03-09)** — Bearer token + Tailscale achieves the security objectives. UDS was a mechanism choice, not a requirement — HTTP localhost passthrough is equivalent for a single-user system. Endpoints: /api/auth/login + /me + /refresh live (S82). Local-mode passthrough intentional for internal CLI. API-key middleware is low-priority future hardening. |
| [027](ADR-027-fleet-observability-strategy.md) | Fleet Observability | ❌ | **DROPPED** — Local observability features (SQLite logging, metrics rollup, HTMX /ui, `forge fleet metrics`) remain functional. Cross-node aggregation was never completed; superseded by simpler hub model where node-1:8081 is the single source of truth. |
| [028](ADR-028-task-state-machine.md) | Task FSM | ✅ | 7 states + hooks ✅; ClaimTask uses StateMachine.Transition() S75; QUEUED-guard prevents races |
| [029](ADR-029-forge-v4-cli-consolidation.md) | V4 CLI Consolidation | ✅ | Go CLI in `cmd/forge/`, 70+ commands live (S89) |
| [030](ADR-030-forge-configuration-model.md) | Config Model | ✅ | forge.toml parser + forge init wizard in v3+CLI |
| [031](ADR-031-cli-plugin-system.md) | CLI Plugin System | ✅ | `forge plugin list/install/remove/info` live |
| [032](ADR-032-project-structure-and-cli-quality.md) | Project Structure + CLI Quality | ✅ | Shell completion ✅; handlers extracted S77-S78 (4420→1621 lines, 15 handler files) |
| [033](ADR-033-dark-factory-autonomy.md) | Dark Factory Autonomy | ✅ | F1+F2+F3 wired S74-S75; 26 patrols active |
| [034](ADR-034-fluid-fleet-autoscaling.md) | Fluid Fleet Auto-Scaling | ✅ | fleetScaleRecommendPatrol live; hybrid recommend+approve; token budget tracking per provider |
| [035](ADR-035-fluid-per-node-fleet-scaling.md) | Per-Node Fleet Scaling | ✅ | Phase 1+2 COMPLETE S77b: token budgets, agent_inventory, deflation, council lifecycle, zombie detection |
| [036](ADR-036-autonomous-fleet-execution.md) | Autonomous Fleet Execution | ✅ | Tier-based auto-approval COMPLETE: lightweight auto_execute=1 (fleet-auto-exec patrol active), heavy = manual gate. Circuit breaker + flap guard + RAM/CPU/ceiling gates wired. |
| [038](ADR-038-node-registration-protocol.md) | Node Registration Protocol | ✅ | Complete S89: self-register on daemon startup; Tailscale IP auto-detect live |
| [040](ADR-040-cli-final-consolidation.md) | CLI Final Consolidation | ✅ | **COMPLETE (council 3-0, 2026-03-09)** — Waves 1–6 done. cli_v2 reduced to ios.py only (intentional). 70+ Go commands cover all requirements. forge + forged are the only binaries. |
| [041](ADR-041-blueprint-runtime.md) | Blueprint Runtime for Durable Task Orchestration | 📋 | **ACCEPTED (council 3-0, 2026-03-10)** — adds a linear, resumable, task-linked orchestration layer between task state and agent loops |
| [042](ADR-042-public-private-repo-split.md) | Public/Private Repo Split | 📋 | Proposed 2026-03-10 — valid follow-on, but deferred until the runtime simplification work is stable enough to make the split actionable |

---

## Quick Status Summary

### ✅ Implemented (22 ADRs)
008, 009, 010, 011, 012, 014, 018, 020, 023, 024, 026, 028, 029, 030, 031, 032, 033, 034, 035, 036, 038, **040**

### 📋 Proposed (2 ADRs)
- **041** — Blueprint Runtime for Durable Task Orchestration
- **042** — Public/Private Repo Split — deferred follow-on after runtime simplification stabilizes

### 🔧 Partial (0 ADRs)
*(none — ADR-027 moved to Dropped)*

### ⏸ Hold (1 ADR)
- **019** — iOS SSH terminal v1.0 complete; fleet control views (Phase 2) not started; resume post-App Store

### ❌ Superseded / Withdrawn / Dropped (9 ADRs)
001 (→029), 002 (→dispatch-decision.md), **007** (→v3 SQLite tasks), 013 (→010), **016** (→036), **027** (→hub model), 021 (→025), 025 (→hub model)

---

## Council Vote Record (2026-03-09)

**Agents:** gemini, claude, codex
**Decisions:**

| ADR | Vote | Score | Reasoning |
|-----|------|-------|-----------|
| 007 | SUPERSEDED | 3-0 | v3 SQLite task system + `forge work --daemon` make Beads and blocked_by both irrelevant |
| 016 | SUPERSEDED | 3-0 | Actual model (ADR-036) is self-claiming + direct HTTP, not daemon-forked adapters |
| 019 | HOLD | 3-0 | Phase 1 (SSH terminal) done; Phase 2 (fleet control iOS views) valid but not started |
| 026 | ACCEPTED | 2-1 | Bearer+Tailscale achieves objectives; UDS was mechanism not requirement (codex dissent: PARTIAL — UDS gap) |
| 027 | DROPPED | 3-0 | Cross-node aggregation never completed; hub model makes it unnecessary |
| 040 | COMPLETE | 3-0 | Waves 1–6 done; cli_v2 = ios.py only; irreversible cutover |
os.py only; irreversible cutover |

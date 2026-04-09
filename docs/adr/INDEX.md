# ADR Index

> **Last updated:** 2026-04-07 — ADR-052 (session memory hooks) + ADR-053 (semantic search, hold) proposed
> Status legend: ✅ Implemented · 🔧 Partial · 📋 Proposed · ⏸ Hold · ❌ Superseded

| ADR | Title | Status | Notes |
|-----|-------|--------|-------|
| [000](ADR-000-architecture-overview.md) | Architecture Overview | ✅ | Reference doc |
| [001](ADR-001-cli-v2-unified-entry.md) | CLI v2 Unified Entry | ❌ | Superseded by ADR-029 (CLI v4) |
| [002](ADR-002-dispatch-consolidation.md) | Dispatch Consolidation | ❌ | Superseded by dispatch-decision.md |
| [007](ADR-007-beads-integration.md) | Beads Task Graph | ❌ | **SUPERSEDED (council 3-0, 2026-03-09)** — v3 SQLite task system (`forge task`) permanently replaces both Beads AND the planned `blocked_by` lightweight alternative. Self-claiming via `forge work --daemon` eliminates the sequencing problem that drove this ADR. If task dependencies needed at scale, implement `blocked_by` field in Go task schema (not Python harness). |
| [008](ADR-008-forge-v3-rewrite.md) | V3 Go Rewrite | ✅ | Running on sati + prya |
| [009](ADR-009-v3-agentic-patterns.md) | Agentic Patterns | ✅ | FSM wired ✅; **38 patrols** (grep `ID:\s*"` cmd/forged/patrol.go); Dark Factory F1 auto-promote live S75 |
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
| [025](ADR-025-local-daemon-distributed-mesh.md) | Local Daemon per Node | ❌ | Superseded S82: hub model (prya:8081) confirmed working at 5-node scale. Per-node daemon adds complexity without benefit. Revisit only if fleet >50 agents or hub latency >500ms. |
| [026](ADR-026-authentication-model.md) | Auth Model | ✅ | **ACCEPTED (council 2-1, 2026-03-09)** — Bearer token + Tailscale achieves the security objectives. UDS was a mechanism choice, not a requirement — HTTP localhost passthrough is equivalent for a single-user system. Endpoints: /api/auth/login + /me + /refresh live (S82). Local-mode passthrough intentional for internal CLI. API-key middleware is low-priority future hardening. |
| [027](ADR-027-fleet-observability-strategy.md) | Fleet Observability | ❌ | **DROPPED** — Local observability features (SQLite logging, metrics rollup, HTMX /ui, `forge fleet metrics`) remain functional. Cross-node aggregation was never completed; superseded by simpler hub model where prya:8081 is the single source of truth. |
| [028](ADR-028-task-state-machine.md) | Task FSM | ✅ | 7 states + hooks ✅; ClaimTask uses StateMachine.Transition() S75; QUEUED-guard prevents races |
| [029](ADR-029-forge-v4-cli-consolidation.md) | V4 CLI Consolidation | ✅ | Go CLI in `cmd/forge/`, 70+ commands live (S89) |
| [030](ADR-030-forge-configuration-model.md) | Config Model | ✅ | forge.toml parser + forge init wizard in v3+CLI |
| [031](ADR-031-cli-plugin-system.md) | CLI Plugin System | 🔧 | Commands wired (`forge plugin list/install/remove/info`) but **zero plugins registered** — infrastructure without users (audit 2026-04-05) |
| [032](ADR-032-project-structure-and-cli-quality.md) | Project Structure + CLI Quality | ✅ | Shell completion ✅; handlers extracted S77-S78 (4420→1621 lines, 15 handler files) |
| [033](ADR-033-dark-factory-autonomy.md) | Dark Factory Autonomy | ✅ | F1+F2+F3 wired S74-S75; 26 patrols active |
| [034](ADR-034-fluid-fleet-autoscaling.md) | Fluid Fleet Auto-Scaling | 🔧 | fleetScaleRecommendPatrol present; token budget tracking live; but `RecommendScale` call-sites reference missing symbol — scaling recs not acted on (audit 2026-04-05) |
| [035](ADR-035-fluid-per-node-fleet-scaling.md) | Per-Node Fleet Scaling | 🔧 | token budgets + agent_inventory real; but node hard-ceilings (prya=2) documented in CLAUDE.md not enforced in spawn gate (audit 2026-04-05) |
| [036](ADR-036-autonomous-fleet-execution.md) | Autonomous Fleet Execution | 🔧 | Circuit breaker + flap guard + RAM/CPU gates wired; BUT `config/dark-factory/approval-tiers.yaml` header says *"not yet wired to daemon code (S120 Phase 2.3)"* — tier auto-approval still template-only (audit 2026-04-05) |
| [038](ADR-038-node-registration-protocol.md) | Node Registration Protocol | ✅ | Complete S89: self-register on daemon startup; Tailscale IP auto-detect live |
| [040](ADR-040-cli-final-consolidation.md) | CLI Final Consolidation | ✅ | **COMPLETE (council 3-0, 2026-03-09)** — Waves 1–6 done. cli_v2 reduced to ios.py only (intentional). 70+ Go commands cover all requirements. forge + forged are the only binaries. |
| [041](ADR-041-blueprint-runtime.md) | Blueprint Runtime for Durable Task Orchestration | ✅ | **IMPLEMENTED (council 3-0, 2026-03-10)** — linear, resumable, task-linked orchestration. `cmd/forged/blueprint.go` + `cmd/forged/blueprint_runtime_test.go` live. CLI: `forge blueprint validate/run`. Config: `config/blueprints/` |
| [042](ADR-042-public-private-repo-split.md) | Public/Private Repo Split | 📋 | Proposed 2026-03-10 — valid follow-on, but deferred until the runtime simplification work is stable enough to make the split actionable |
| [043](ADR-043-vega-cross-domain-content.md) | Vega Cross-Domain Content Pipeline | ✅ | COMPLETE 2026-03-22 — 25 blog posts across 7 domains, all CTA links fixed |
| [044](ADR-044-fix-broken-anchors-portfolio.md) | Fix Broken Nav Anchors Portfolio-Wide | ✅ | COMPLETE 2026-03-22 — ~46 broken anchors fixed across 10 domain configs |
| [045](ADR-045-startup-resilience.md) | Startup Resilience: Env Validation & Path Hardening | ✅ | ACCEPTED (council 2-0, 2026-03-22) — validates FORGE_ROOT, hardens daemon startup |
| [046](ADR-046-vega-session-retro-process-improvements.md) | Vega Session Retro & Process Improvements | ✅ | ACCEPTED 2026-04-05 (de-facto ratified) — worktree-first is already hard ban (S163-S164); 5 proposals enforced in CLAUDE.md |
| [047](ADR-047-gitsafe-mandate.md) | Mandate gitsafe.sh on Multi-Agent Nodes | ✅ | ACCEPTED (S175 P1, 4-0) — gitsafe.sh for all write ops on gaea/nova/sati |
| [048](ADR-048-council-decision-protocol.md) | Council Decision Protocol | 📋 | PROPOSED 2026-04-05 — skeleton; quorum rules, panel diversity, timeout SLA, record format |
| [049](ADR-049-autonomous-loop-lifecycle.md) | Autonomous Loop Lifecycle & Stale-Task Hygiene | 📋 | PROPOSED 2026-04-05 — skeleton; consolidates S162+S185 (4-phase lifecycle, archival TTL) |
| [050](ADR-050-royal-jelly-context-model.md) | Royal Jelly Context Persistence Model | 📋 | PROPOSED 2026-04-05 — skeleton; 3-file schema, staleness SLA, data accuracy requirement |
| [051](ADR-051-node-resource-budget.md) | Node Resource Budget & Enforcement | 📋 | PROPOSED 2026-04-05 — skeleton; config/nodes.yaml + daemon spawn gate (fixes ADR-034/035 gap) |
| [052](ADR-052-session-memory-automation.md) | Session Memory Consolidation & Self-Reinforcement Loop | ✅ | ACCEPTED 2026-04-07 — council: pi (REJECT→revise), kimi2 (APPROVE). Closes dead-end loops in session-persist + load_context. Phase 1 implemented (3 hook changes). |
| [053](ADR-053-semantic-context-search.md) | Semantic Context Search (mempalace CLI) | ⏸ | HOLD 2026-04-07 — council approved (pi 5/5, kimi2 5/5). Threshold raised to 500 docs. L0/L1 pattern adopted at zero cost. qmd baseline measurement pending. |

---

## Quick Status Summary

### ✅ Implemented (24 ADRs)
008, 009, 010, 011, 012, 014, 018, 020, 023, 024, 026, 028, 029, 030, 032, 033, 038, **040**, **041**, **043**, **044**, **045**, **046**, **047**

### 📋 Proposed (5 ADRs)
- **042** — Public/Private Repo Split — deferred follow-on after runtime simplification stabilizes
- **048** — Council Decision Protocol — skeleton (2026-04-05), awaiting fleet expansion
- **049** — Autonomous Loop Lifecycle — skeleton (2026-04-05), awaiting fleet expansion
- **050** — Royal Jelly Context Model — skeleton (2026-04-05), awaiting fleet expansion
- **051** — Node Resource Budget — skeleton (2026-04-05), awaiting fleet expansion

### 🔧 Partial (4 ADRs, audit 2026-04-05)
- **031** — Plugin system wired, zero plugins exist
- **034** — RecommendScale call-sites reference missing symbol
- **035** — Per-node ceilings not enforced in spawn gate
- **036** — Auto-approval tier YAML is template-only, not wired to daemon

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

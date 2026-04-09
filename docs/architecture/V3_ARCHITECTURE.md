# FORGE v3 Architecture

**Source of Truth:** This document consolidates ADR-008, FORGE_CLI_V3_LOCKED_SPECIFICATION, and related ADRs into a single architectural reference.

**Status:** Phase 1 - In Progress
**Last Updated:** 2026-03-03
**Version:** 3.0-LOCKED

---

## 1. Executive Summary

FORGE v3 is a Go-based multi-agent orchestration platform that manages task distribution, agent lifecycles, and cross-node communication. It replaces the file-based V2 system with ACID-compliant SQLite transactions and real-time WebSocket communication.

### Key Goals

| Goal | V2 (Current) | V3 (Target) |
|------|--------------|-------------|
| Task Persistence | File-based (loss on restart) | SQLite (ACID) |
| Dispatch Reliability | 75% (25% tmux failures) | 99%+ (idempotent) |
| Agent Coordination | File-based XNode | WebSocket hub |
| Context Preservation | Royal Jelly (manual) | Context Envelope (auto) |

---

## 2. Architecture Overview

### 2.1 Three-Layer Design

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1: STRATEGIC (Orchestrator)                                  │
│  • Portfolio decisions, cross-domain conflict resolution            │
│  • Human escalation gateway, budget management                       │
└────────────────────────┬────────────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌────────────────┐┌────────────────┐┌────────────────┐
│  LAYER 2:     ││  LAYER 2:     ││  LAYER 2:     │
│  TACTICAL      ││  TACTICAL      ││  TACTICAL      │
│  (Domain Lead) ││  (Domain Lead) ││  (Domain Lead) │
└────────┬───────┘└────────┬───────┘└────────┬───────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 3: OPERATIONAL (Workers)                                      │
│  • T1-T3 agents executing tasks                                     │
│  • Ralph Loop for feature implementation                            │
│  • Fresh context per task                                           │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        FORGE v3 Server                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   HTTP API   │  │  WebSocket  │  │   SQLite Database    │  │
│  │   (REST)     │  │   Server    │  │   (WAL Mode)        │  │
│  │   :8081      │  │   (Hub)     │  │                      │  │
│  │              │  │   :8082      │  │                      │  │
│  └──────┬───────┘  └──────┬──────┘  └──────────┬───────────┘  │
│         │                  │                     │              │
│         └──────────────────┼─────────────────────┘              │
│                            │                                   │
│  ┌─────────────────────────┴─────────────────────────────────┐  │
│  │                    Task Queue Manager                        │  │
│  │  - Task CRUD (create, read, update, delete)                │  │
│  │  - Dependency resolution                                   │  │
│  │  - Idempotency enforcement                                │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                     │
│  ┌─────────────────────────┴─────────────────────────────────┐  │
│  │                    Agent Registry                           │  │
│  │  - Heartbeat tracking                                     │  │
│  │  - Connection management                                  │  │
│  │  - Lease coordination                                    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Agent Workers (Python)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │  kimi    │  │  glm     │  │ minimax  │  │  gemini  │      │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Components

### 3.1 HTTP API Server

**Port:** 8081

Provides REST endpoints for:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/status` | GET | System status |
| `/api/tasks` | POST | Create task |
| `/api/tasks` | GET | List tasks |
| `/api/tasks/{id}` | GET | Get task |
| `/api/tasks/{id}/claim` | POST | Claim task |
| `/api/tasks/{id}/complete` | POST | Complete task |
| `/api/dispatch` | POST | Dispatch task to agent |
| `/api/xnode/inbox` | GET | XNode inbox |
| `/api/xnode/acks` | GET | XNode acknowledgments |

### 3.2 WebSocket Hub

**Port:** 8082

Manages real-time agent connections:

| Message Type | Direction | Description |
|--------------|-----------|-------------|
| `handshake` | Client→Server | Initial connection |
| `handshake_ack` | Server→Client | Connection confirmed |
| `task_available` | Server→Client | New task assigned |
| `task_started` | Client→Server | Agent started work |
| `task_completed` | Client→Server | Task done |
| `task_failed` | Client→Server | Task failed |
| `ping` | Server→Client | Keepalive |
| `pong` | Client→Server | Keepalive response |

### 3.3 SQLite Database

**Location:** `.forge/v3/state.db`

Tables:

| Table | Purpose |
|-------|---------|
| `tasks` | Task queue with status, dependencies |
| `task_events` | Event sourcing log (source of truth) |
| `idempotent_actions` | Deduplication for git/deploys |
| `agents` | Agent registry |
| `approvals` | Human-in-the-loop queue |
| `dispatches` | Task dispatch records |
| `agent_state` | Agent status tracking |

### 3.4 Task Queue

Features:
- Dependency resolution (DAG)
- Priority-based scheduling (critical, high, medium, low)
- Idempotency keys (ULID)
- Event sourcing (task_events table)

### 3.5 Agent Registry

Manages:
- Agent heartbeats (30s interval)
- Connection state (connected/disconnected/error)
- Lease coordination (prevents task theft)
- Context tracking

---

## 4. Technical Decisions

### 4.1 Language Stack

| Component | Language | Rationale |
|-----------|----------|-----------|
| Orchestrator | Go | Single-binary, concurrency, static typing |
| Workers | Python | LLM ecosystem access |
| Bridge | Python | WebSocket↔LLM adapter |

### 4.2 Database Strategy

| Phase | Database | Scale |
|-------|----------|-------|
| Phase 1 | SQLite (WAL) | 95 projects, ~500 tasks/day |
| Phase 2 | PostgreSQL | 500 projects, ~5K tasks/day |
| Phase 3+ | Sharded PostgreSQL | Unlimited |

### 4.3 Royal Jelly (Context Preservation)

**Decision:** Filesystem as source of truth, SQLite as cache/index.

- Human-readable, versioned, familiar
- Easy to audit, backup, migrate
- SQLite provides fast queries for UI

---

## 5. Phase 1 Scope (Weeks 1-6)

### 5.1 IN (Must Have)

| Component | Scope | Week |
|-----------|-------|------|
| Go Orchestrator Core | Task queue + agent registry only | 1-2 |
| SQLite Event Store | WAL-mode, events + minimal projection | 1-2 |
| WebSocket Protocol | Full duplex, heartbeat, reconnection | 3 |
| Python Worker Adapter | ForgeWorker class with local queue | 3 |
| Royal Jelly Sync | Filesystem ↔ SQLite bidirectional | 4 |
| Context Envelope | Generation + storage + bootstrap | 4-5 |
| GitGuard | Single-writer + branch-per-task | 4-5 |
| Dark Factory | 2 lanes: dev, test (deploy stubbed) | 5-6 |
| Approval Primitive | 8 types, 3 tiers, confidence scoring | 5-6 |
| Basic UI | Event stream viewer (TUI minimal) | 6 |

### 5.2 OUT (Deferred to Phase 2+)

- Full Command Center migration
- PostgreSQL migration
- Advanced flywheel features
- Production deployment automation

---

## 6. Idempotency

### 6.1 Actions Requiring Idempotency

| Action | Needs Idempotency | Implementation |
|--------|-------------------|----------------|
| Git commits | ✅ YES | ULID per commit + check if committed |
| Deploys | ✅ YES | ULID per deploy + idempotency table |
| Approvals | ✅ YES | ULID per approval + status check |
| Notifications | ❌ NO | At-least-once acceptable |

---

## 7. Data Flow

```
1. Task Created (via API, dispatch, or XNode)
      │
      ▼
2. Task stored in SQLite (tasks + task_events)
      │
      ▼
3. Agent picks up task (WebSocket push)
      │
      ▼
4. Agent processes, sends periodic updates
      │
      ▼
5. Task completed, context saved to Royal Jelly
      │
      ▼
6. GitGuard commits changes (idempotent)
```

---

## 8. File Structure

```
cmd/forge-v3/
├── main.go         # Entry point, HTTP handlers
├── websocket.go    # WebSocket hub, message routing
├── queue.go        # Task queue CRUD, dependencies
├── registry.go     # Agent registry, heartbeats
├── lease.go        # Distributed locking
├── migrate.go      # Database migrations
├── e2e_test.go    # Integration tests
└── migrations/     # SQL migration files

harness/forge_harness/v3_task_manager/
├── database.py     # SQLite operations
├── api.py          # REST API endpoints
├── websocket_server.py  # WebSocket hub
├── scheduler.py    # Resource scheduling
├── xnode_bridge.py # File↔SQLite bridge
└── openclaw_bridge.py  # Telegram/Slack→V3
```

---

## 9. XNode Integration

FORGE v3 integrates with XNode for cross-node communication:

- **Inbox:** `.forge/xnode/lead-inbox/{node}.jsonl`
- **Outbox:** `.forge/xnode/lead-outbox/{node}.jsonl`
- **Acks:** `.forge/xnode/acks/`

XNode messages flow through REST endpoints:
- `GET /api/xnode/inbox` - List all inbox files
- `GET /api/xnode/inbox/{node}` - Get messages for node
- `GET /api/xnode/acks` - List acknowledgments

---

## 10. Terminology

| Term | Definition |
|------|------------|
| **Orchestrator** | Go-based control plane (v3 server) |
| **Worker** | Python agent executing tasks |
| **Royal Jelly** | Context preservation system |
| **Context Envelope** | Auto-generated context bundle |
| **GitGuard** | Single-writer git enforcement |
| **Lease** | Temporary task assignment (prevents theft) |

---

## 11. Related Documentation

| Document | Description |
|----------|-------------|
| `docs/adr/ADR-008-forge-v3-rewrite.md` | Original ADR |
| `docs/adr/ADR-009-v3-agentic-patterns.md` | Magentic ledger, race mode |
| `docs/adr/ADR-010-v3-lease-system.md` | Lease coordination |
| `docs/adr/ADR-011-v3-websocket-protocol.md` | WebSocket protocol |
| `docs/adr/ADR-012-v3-confidence-scoring.md` | Approval scoring |
| `docs/v3/API.md` | API reference |
| `docs/v3/OPERATIONS.md` | Troubleshooting |

---

*This document is the single source of truth for FORGE v3 architecture. See docs/v3/DECISIONS/ for ADRs and docs/v3/OPERATIONS.md for operational guides.*

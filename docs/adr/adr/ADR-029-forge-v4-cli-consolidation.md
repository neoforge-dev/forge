# ADR-029: FORGE V4 CLI Consolidation - Unified Command Interface

**Date:** 2026-03-05
**Status:** Accepted
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)
- gemini (Architecture Review)
- cursor (Technical Review)
- minimax (Operations Review)
- node-2 (Lead Orchestrator)

**Amends:** ADR-001, ADR-008, ADR-021

---

## Context

FORGE currently operates with fragmented CLI interfaces:
- **CLI v2** (Python/Click): 54+ commands, feature-complete but inconsistent
- **CLI v3 Python** (Typer): Limited coverage, HTTP client to v3 daemon
- **CLI v3 Go** (Cobra): Growing organically, currently ~8,000 lines

This fragmentation causes:
- Confusion for both humans and agents
- Inconsistent command patterns and flag naming
- Poor composability (mixed text/JSON output)
- Difficulty discovering available commands
- Maintenance burden across three implementations

### Current Command Proliferation

```
forge up/down/doctor              # service control
forge fleet/dispatch/lead/xnode   # orchestration (inconsistent)
forge tasks/recommend/evaluator   # tasking (different patterns)
forge loop/df/quality/ship/work   # operations (abbreviated, unclear)
```

Problems:
- No consistent noun-verb pattern
- Abbreviations reduce discoverability (`df` for Dark Factory)
- Inconsistent flag naming (`-d` vs `--domain`)
- Some commands domain-specific, others global

---

## Decision

Build **FORGE V4 CLI**, a unified Go-based command interface that consolidates all operations into **12 core nouns** with **5 universal verbs**, respecting UNIX philosophy and the Zen of Python.

### Core Principles

1. **UNIX Philosophy**: Do one thing well, compose with pipes, text streams as universal interface
2. **Zen of Python**: Explicit > implicit, simple > complex, one obvious way
3. **Agent-First UX**: Discoverable, consistent, composable, idempotent

### The 12 Core Nouns

| Noun | Purpose | Replaces |
|------|---------|----------|
| `task` | Unit of work | features, dispatches |
| `agent` | Worker process | fleet agents |
| `node` | Physical machine | xnode, lead commands |
| `domain` | Business domain | (unchanged) |
| `project` | Repository | (unchanged) |
| `approval` | Human checkpoint | approve command |
| `queue` | Task scheduling | loop queue |
| `context` | Knowledge preservation | royal jelly files |
| `lane` | Dark Factory stage | df command |
| `patrol` | Background monitor | implicit monitoring |
| `pattern` | Reusable template | pattern files |
| `config` | Settings | (unchanged) |

### Universal Verb Taxonomy

**5 Universal Verbs** (applicable to most nouns):
- `list` — Filterable, sortable, paginated collections
- `show <id>` — Full details for single item
- `create` — Interactive or flag-based creation
- `update <id>` — Partial updates supported
- `delete <id>` — Soft delete with confirmation

**Workflow Verbs** (noun-specific actions):
- `task next` — Get next task for agent
- `task complete` — Mark task finished
- `lane promote` — Move task to next lane
- `approval decide` — Approve or reject
- `dispatch send` — Send work to agent
- `ship` — Complete and ship work

### Architecture

```
forge binary (~20MB)
├── CLI layer (Cobra)      # Command parsing
├── Service interfaces     # Per-noun business logic
├── API client             # HTTP to daemon
├── Store client           # Direct SQLite (offline mode)
└── Output formatters      # table/json/csv/quiet

forge daemon
├── HTTP server (:8081)    # REST API
├── WebSocket (:8082)      # Agent comms
├── SQLite (WAL mode)      # Persistence
└── Patrol loops           # Background tasks
```

### Dual-Mode Operation

| Mode | When Used | Use Case |
|------|-----------|----------|
| **Daemon** (default) | HTTP API available | Production, multi-agent |
| **Standalone** (`--offline`) | Explicit opt-in | Maintenance, scripting |

**Constraint**: Direct SQLite mode is explicit opt-in only (`--offline`). No silent auto-fallback to prevent concurrency issues.

### Output Formats

Global `--format` flag:
- `table` — Human-readable (default for TTY)
- `json` — Structured (default for pipes)
- `csv` — Spreadsheet-compatible
- `quiet` — Exit codes only
- `auto` — TTY→table, piped→json

### Source of Truth Strategy

| Layer | Technology | Purpose |
|-------|------------|---------|
| Runtime state | SQLite | Task queue, agent telemetry, approvals |
| Context/knowledge | Filesystem (git) | Royal Jelly, context envelopes |
| Cross-node | File-based XNode | Partition-tolerant messaging |

Bidirectional sync between SQLite and filesystem for context preservation.

---

## Command Mapping: v3 → v4

| v3 Command | v4 Equivalent | Notes |
|------------|---------------|-------|
| `forge-v3 task list` | `forge task list` | Direct mapping |
| `forge fleet status` | `forge fleet status` | Direct mapping |
| `forge dispatch send forge:kimi "..."` | `forge dispatch send forge:kimi "..."` | Direct mapping |
| `forge work -d X -p Y` | `forge work X/Y` | **New syntax**, backward compat during transition |
| `forge approve <id>` | `forge approval decide <id> --approve` | Noun-verb pattern |
| `forge df lanes` | `forge lane status` | Clearer naming |
| `forge xnode send` | `forge node send` | Unified infrastructure |
| `forge lead send` | `forge node send` | Nodes include lead functionality |
| `forge loop run` | `forge queue populate && forge task next` | Composable |
| `forge quality` | `forge lane gates` | Integrated into lanes |
| `forge handoff create` | `forge context envelope` | Clearer intent |

---

## Implementation Timeline (6 Weeks)

### Phase 1: Foundation (Weeks 1-2)
- Root command, global flags, output formatters
- Core nouns: `task`, `agent`, `queue`, `domain`, `approval`, `config`
- HTTP client to v3 daemon
- Integration tests vs v3

### Phase 2: Extended Nouns (Week 3)
- Remaining nouns: `node`, `project`, `context`, `lane`, `patrol`, `pattern`
- Service interfaces for all nouns
- Shell completion scripts

### Phase 3: Workflows (Week 4)
- Workflow commands: `dispatch`, `fleet`, `ship`, `work`, `status`
- Idempotency keys
- Error handling standardization

### Phase 4: Polish (Weeks 5-6)
- Man pages
- Migration guide
- Feature flags for gradual cutover
- Performance testing

---

## Go Package Structure

```
cmd/forge/
  main.go              # Entry point
  root.go              # Root command, global flags
  noun_*.go            # One file per noun (task.go, agent.go...)
  workflow_*.go        # dispatch.go, ship.go, fleet.go...
  daemon.go            # Embedded daemon control

internal/cli/
  flags.go             # Common flag definitions
  context.go           # Domain/project resolution
  errors.go            # Error handling utilities

internal/task/
  service.go           # TaskService interface
  types.go             # Task models

internal/agent/
  service.go           # AgentService interface
  types.go             # Agent models

# ... (one package per noun)

internal/api/
  client.go            # HTTP client implementing all services
  types.go             # API response models

internal/store/
  client.go            # Direct SQLite client
  queries.go           # SQL queries

internal/backend/
  factory.go           # Choose HTTP vs Direct client

internal/daemon/
  server.go            # HTTP/WebSocket servers
  patrol.go            # Background monitors

internal/output/
  formatter.go         # Formatter interface
  table.go             # Table formatter
  json.go              # JSON formatter
  csv.go               # CSV formatter
  quiet.go             # Quiet formatter

internal/errors/
  types.go             # Standardized error types
  codes.go             # Exit code mapping
```

---

## Migration Strategy

### Phase 0: Parallel Operation (Week 1-2)
```bash
# v3 and v4 coexist
forge-v3 task list      # existing
forge task list         # new (calls v3 daemon)
```

### Phase 1: Feature Flags (Week 3-4)
```yaml
# config.yaml
cli:
  version: 4
  legacy_fallback: true
```

### Phase 2: Cutover (Week 5)
```bash
# v4 becomes default
alias forge=/usr/local/bin/forge-v4
```

### Phase 3: Deprecation (Week 6+)
```bash
# Remove v3 CLI
rm /usr/local/bin/forge-v3
```

---

## Consequences

### Positive

1. **Simplicity**: 54+ commands → 12 nouns × 5 verbs
2. **Consistency**: Predictable patterns across all operations
3. **Composability**: Structured output enables piping and scripting
4. **Discoverability**: Progressive disclosure helps agents learn
5. **Maintainability**: Single Go binary, clear package boundaries
6. **Reliability**: Idempotent operations, standardized error handling

### Negative

1. **Migration Cost**: Existing scripts need updates
2. **Learning Curve**: Operators must learn new command structure
3. **Dual Maintenance**: v3 and v4 run in parallel during transition

### Mitigations

- Backward compatibility flags during transition
- Comprehensive migration guide with examples
- Side-by-side operation with feature flags
- Integration tests ensuring v3/v4 parity

---

## Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Scope creep (daemon refactor) | High | High | Strictly limit to CLI layer only |
| Migration resistance | Medium | Medium | Backward compat, feature flags |
| Performance regression | Low | Medium | Benchmarks, gradual rollout |
| SQLite concurrency issues | Medium | High | Explicit `--offline`, daemon-only in prod |

---

## Related Decisions

- ADR-001: CLI v2 as Canonical Entry Point (superseded)
- ADR-008: FORGE CLI v3 Rewrite (evolved by this ADR)
- ADR-021: Unified Control Plane Consolidation (realized through V4)
- ADR-028: Task State Machine (implemented in V4)

## References

- Design Specification: `docs/design/FORGE_V4_CONSOLIDATED_CLI_DESIGN.md`
- Analysis Document: `docs/design/FORGE_V4_CLI_ANALYSIS.md`
- Council Reviews: `.forge/heartbeat/results/*-V4-CLI-REVIEW.md`

---

**Status: ACCEPTED**

Implementation begins: 2026-03-05
Target completion: 2026-04-16 (6 weeks)

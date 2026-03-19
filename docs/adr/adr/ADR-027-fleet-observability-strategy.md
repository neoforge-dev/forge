# ADR-027: Fleet Observability Strategy

**Date:** 2026-03-05
**Status:** Accepted (Partial) — council vote 2026-03-09 (3-0; architecture valid, cross-node flow pending human gate)
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)

---

## Context

The FORGE fleet operates as a distributed system across multiple nodes (Prya, Sati, Nova, Vega). Previous architectures designed a local event bus (ADR-017) and agent telemetry (ADR-015), but lacked a unified observability strategy.

There was no defined way to:
1. Aggregate metrics across nodes (e.g., Fleet-wide Test Pass Rate).
2. Correlate logs for a task that hopped from Prya → Sati.
3. Alert operators when an agent gets stuck in an infinite loop.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Prometheus + Grafana + Loki | Industry Standard | Huge infrastructure overhead, multiple services | ❌ REJECTED |
| Datadog / Cloud Provider | Managed | SaaS cost, relies on external internet connection | ❌ REJECTED |
| **Local SQLite + XNode Sync** | **Zero external dependencies, offline capable** | **Requires building custom HTMX dashboards** | ✅ **ACCEPTED** |

---

## Decision

The `forge-v3` Go daemon will implement an **Embedded Observability Stack** entirely contained within SQLite, with selective aggregation pushed to the Lead Node (Prya).

### Core Architecture

1. **Structured Event Logs (Local):** Every significant action (Task Dispatch, Tool Call, Git Commit, Hook Execution) is written to a local SQLite `events` table with a structured JSON payload, a timestamp, and a `trace_id` (Task ID).
2. **Metrics Rollups (Local):** A background Goroutine periodically queries the `events` table to calculate 1-minute and 5-minute rolling averages (e.g., tokens/sec, error rates, context_pct). These are written to a `metrics` table.
3. **Selective Aggregation (Fleet):** Worker nodes (Sati, Nova) use the XNode Transactional Outbox (ADR-023) to periodically push small "Heartbeat/Summary" metrics directly to Prya's API.
4. **Dashboards:** The Fleet Dashboard (HTMX) served by Prya queries its local aggregated SQLite data to render charts and active fleet statuses. Prya does not need to query Sati's database directly for real-time logs; if an operator wants deep logs, they view the specific node's local UI or pull them via an explicit cross-node request.

### Tracing

A **Correlation ID** (usually the `task_id`) must be passed across all boundaries. If a task starts on Prya, gets sent via XNode to Sati, and runs on the `claude` subprocess, all resulting SQLite logs on both nodes must share that identical `task_id`.

---

## Consequences

### Positive

1. **Zero Ops Overhead:** No need to run or manage Prometheus, Grafana, or OpenTelemetry collectors.
2. **Partition Tolerant Observability:** If Sati loses internet connection to Prya, it still logs locally. When it reconnects, it pushes its aggregated summaries.
3. **Simplicity:** Queries for dashboards are just standard SQLite `SELECT` statements inside the Go binary.

### Negative

1. **Storage Growth:** The local `events` table could grow infinitely. A strict `VACUUM` and retention policy (e.g., delete logs older than 7 days) must be enforced by the Patrol system.
2. **Custom UI:** We must build the charts and log-viewers manually in HTML/HTMX rather than relying on Grafana.

## Related Decisions
- Merges the concepts of old ADR-015 (Telemetry) and ADR-017 (Event Bus).
- Relies on ADR-023 (XNode) for syncing metrics to the Lead Node.

**Status: Accepted (Partial)** — local metrics + events real (S78d); cross-node push needs FORGE_LEAD_URL on node-2/node-3 (human gate GATE-2/3); `forge fleet metrics` CLI pending

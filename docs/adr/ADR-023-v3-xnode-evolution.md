# ADR-023: v3 XNode Evolution - Hybrid SQLite & JSONL Outbox

**Date:** 2026-03-05
**Status:** Proposed
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)

---

## Context

The XNode system provides cross-node communication (e.g., between the Prya Lead Node and Sati Worker Nodes). Previous architectures proposed either purely file-based queues (JSONL + HTTP Gateway) or purely HTTP-based delivery (SQLite + Transactional Outbox).

A pure HTTP Outbox model introduces a severe hidden issue: **It changes the reliability model from "eventual consistency regardless of target state" to "reliable delivery only when target HTTP is online."** If Sati's HTTP server crashes, Prya's outbox backs up and retries exhaustively, rather than safely dropping files on Sati's disk via Syncthing/Taildrop for immediate processing upon reboot.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Pure JSONL Files | Partition tolerant natively | Hard to query locally, conflict resolution | ❌ REJECTED |
| Pure HTTP Outbox | Clean DB sync, uses SQL | Requires target HTTP to be online | ❌ REJECTED |
| **Hybrid SQLite + JSONL** | **Local SQL queryability + file-based partition tolerance for transport** | **Requires background serialization worker** | ✅ **ACCEPTED** |

---

## Decision

We will implement a **Hybrid Approach** for XNode. The local state is managed in SQLite for queryability and developer experience, while the transport layer leverages JSONL files synced via Syncthing/Tailscale.

### Core Architecture

1. **Local SQLite Outbox:** When Node A dispatches a task to Node B, it writes the payload to an `xnode_outbox` table in its local SQLite database (`status = pending`).
2. **Serialization Worker:** A background Goroutine inside `forge-v3` polls the `xnode_outbox`. It serializes the rows into JSONL format and writes them to `.forge/xnode/lead-outbox/{nodeB}.jsonl`. It then marks the SQL row as `status = serialized`.
3. **Transport (File Sync):** Syncthing (or a dedicated file synchronization mechanism) handles syncing the file to Node B's `.forge/xnode/lead-inbox/{nodeA}.jsonl`. *This works even if Node B's forge-v3 HTTP server is completely down.*
4. **Ingestion (Target Node):** When Node B's `forge-v3` daemon starts (or via file watcher), it reads the JSONL, checks `idempotency_key`, inserts it into its local `xnode_inbox` SQLite table, and begins processing.
5. **Acks:** Node B writes an ACK to its outbox, which flows back to Node A via the exact same hybrid file-sync mechanism.

---

## Consequences

### Positive

1. **True Partition Tolerance:** Delivery relies entirely on the filesystem sync. If the target Go daemon is down, the message safely waits on its disk.
2. **Local Queryability:** Operators can still write standard SQL queries against the local `xnode_outbox` table to debug queues, rather than using `grep` and `awk` on JSONL files.
3. **No HTTP Dependency:** Cross-node communication does not rely on fragile network HTTP connections, timeouts, or API key configuration between daemon endpoints.

### Negative

1. **Dual State:** The system must carefully manage the transition between the SQLite row state and the written JSONL file.
2. **Filesystem Dependency:** We retain a reliance on Syncthing/Taildrop for the actual transport.

## Related Decisions
- Secures ADR-010 (Lease System) cross-node distribution.
- Replaces earlier drafts of ADR-023 that proposed pure HTTP/API reliance.

**Status: PROPOSED**

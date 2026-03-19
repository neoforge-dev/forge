# ADR-026: Authentication & Authorization Model

**Date:** 2026-03-05
**Status:** Accepted — council vote 2026-03-09 (2-1; bearer tokens + Tailscale achieves objectives; UDS deferred)
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)

---

## Context

The FORGE v3 architecture exposes several control surfaces: an HTTP API, internal RPC, WebSockets (for the UI), and cross-node Tailscale endpoints. Previous architectures (ADR-000, 014, 023) assumed execution in a trusted local environment or over a VPN, but did not define a formal authentication model.

This left significant gaps:
1. **Unauthenticated Endpoints:** The Control Plane API could be hit by malicious local scripts or cross-node processes.
2. **Missing UI Auth:** A React/HTMX UI exposed on port 8081 required some form of session management.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Open (No Auth) | Simple for dev | Massive security risk, exposed via XNode | ❌ REJECTED |
| OAuth / JWT | Standard | Overkill for a local CLI orchestrator | ❌ REJECTED |
| **Multi-Tier Auth (UDS + API Keys + Tailscale ACLs)** | **Strict isolation, minimal setup, secure defaults** | **Requires API key distribution** | ✅ **ACCEPTED** |

---

## Decision

The `forge-v3` Go daemon will implement a **Multi-Tier Authentication Model** to secure its endpoints.

### 1. Local-First: Unix Domain Sockets (UDS)

The local `forge` CLI (v3) communicates with its local `forge-v3` Go Control Plane Daemon.
- **Mechanism:** The daemon listens on a local Unix socket (e.g., `/tmp/forge-v3.sock`).
- **Auth:** Unix file permissions inherently restrict access to the user running the daemon (e.g., `bogdan` or `forge`).
- **Why:** The CLI requires no configuration or token management. If you are logged into the machine, you are authenticated.

### 2. Node-to-Node API Keys (Tailscale)

When the Prya Control Plane sends a task to the Sati Node via XNode (ADR-023), it must authenticate against Sati's HTTP API (e.g., `node-2:8081`).
- **Mechanism:** Nodes exchange statically configured API Keys (Bearer Tokens) defined in `.env`.
- **Network Layer:** All cross-node communication is strictly routed over Tailscale interfaces (100.x.x.x IPs).
- **Why:** Tailscale provides point-to-point encryption (WireGuard), and Bearer Tokens provide application-level access control.

### 3. Operator HTMX UI (Basic Session)

The human operator accesses the v3 UI via a browser.
- **Mechanism:** A simple Username/Password login page that sets an HttpOnly, Secure Session Cookie.
- **Why:** The HTMX UI runs from the Go daemon. A basic session store in the local SQLite database is sufficient and completely self-contained.

---

## Consequences

### Positive

1. **Defense in Depth:** Even if an API Key is leaked, the Tailscale network perimeter prevents external attacks.
2. **Developer Experience:** The `forge` CLI just works out of the box because Unix Sockets don't prompt for passwords.
3. **Auditability:** All API requests and CLI commands can be logged to SQLite with a specific actor/identity attached.

### Negative

1. **Configuration Overhead:** Setting up a new node requires generating a secure random string for the `FORGE_API_KEY` in the `.env` file and syncing it across the fleet.

## Related Decisions
- Secures ADR-023 (XNode Evolution).

**Status: Accepted** — bearer tokens + Tailscale live (S82); UDS deferred (not required for single-user local daemon); API-key middleware is low-priority future hardening

### Implementation Status (S116)

| Tier | ADR Spec | Actual | Status |
|------|----------|--------|--------|
| 1. UDS (local CLI) | Unix socket `/tmp/forge-v3.sock` | TCP `localhost:8081` + `FORGE_API_KEY` bearer | ⏸ DEFERRED — TCP works for single-user |
| 2. Node-to-Node API Keys | Bearer tokens over Tailscale | ✅ `auth.go:AuthMiddleware` + `FORGE_API_KEY` env | ✅ LIVE |
| 3. HTMX UI Session | Username/password + session cookie | ❌ No login page, no session store | ❌ NOT BUILT |

**Endpoints:**
- `POST /api/auth/tokens` — create token (implemented)
- `GET /api/auth/tokens` — list tokens (implemented)
- Auth skip list: `/health`, `/api/health`, `/api/github/webhook`
- No `/login`, `/me`, `/refresh` endpoints exist — HTMX UI is currently unauthenticated on localhost

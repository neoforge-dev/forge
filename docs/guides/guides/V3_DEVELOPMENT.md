# FORGE v3 Development Guide

This guide is for contributors working on the FORGE v3 Go server and related components. It covers how to set up a development environment, build and run the `forge-v3` binary, and run the test suite.

---

## 1. Prerequisites

- Go **1.21+**
- SQLite3 installed on your system
- Unix-like environment (Linux/macOS recommended)
- Git (for normal workflows; not required just to build)

All commands below assume your working directory is the FORGE repo root:

```bash
cd .
```

---

## 2. Repository Layout (v3)

Key locations for v3 development:

- `cmd/forge-v3/` — main Go binary (HTTP API, WebSocket server, task queue)
- `docs/v3/` — v3 documentation (architecture, API, operations, this guide)
- `.forge/` — local state (database, xnode inbox/acks, context)

You will spend most of your time in:

- `cmd/forge-v3/*.go` — core server, queue, WebSocket, leases, metrics
- `cmd/forge-v3/*_test.go` — unit and integration tests

---

## 3. Building the `forge-v3` Binary

From the repo root:

```bash
cd cmd/forge-v3
go build -o forge-v3 .
```

This produces a `forge-v3` binary in `cmd/forge-v3/`.

If the build fails:

- Read the compiler error carefully (missing symbols, wrong imports, etc.).
- Fix the offending file(s) and rerun `go build -o forge-v3 .`.

---

## 4. Runtime Configuration

FORGE v3 uses a few environment variables, with sensible defaults baked into the code.

### 4.1 Ports

- **HTTP API port** (status API, REST endpoints):  
  - Env: `PORT`  
  - Default: `8081`

- **WebSocket port** (worker connections):  
  - Env: `WS_PORT`  
  - Default: `8082`

Example:

```bash
export PORT=8081
export WS_PORT=8082
```

### 4.2 Database

By default v3 uses SQLite with a file under `.forge`:

- Default database path (from `docs/v3/GETTING_STARTED.md`):  
  - `./.forge/forge-v3.db`

You can override this with:

- `DB_TYPE`  
  - Omit or leave empty to use SQLite.
  - Set to `postgres` to use PostgreSQL (requires additional env config; see architecture/API docs).
- `DB_PATH` (SQLite only)  
  - Path to the SQLite database file.

Examples:

```bash
# SQLite (default)
export DB_TYPE=sqlite
export DB_PATH="./.forge/forge-v3.db"

# PostgreSQL (advanced – see architecture docs before using)
export DB_TYPE=postgres
# Additional PG config loaded via forge-harness db helpers
```

### 4.3 Filesystem Layout

Hardcoded paths used by v3 (see `main.go` and `GETTING_STARTED.md`):

- Database (default): `./.forge/forge-v3.db`
- XNode inbox: `./.forge/xnode/lead-inbox`
- XNode acks: `./.forge/xnode/acks`
- Context: `./.forge/context`

Ensure these directories exist when running locally:

```bash
mkdir -p .forge .forge/xnode/lead-inbox .forge/xnode/acks .forge/context
```

---

## 5. Running the Server (Local Dev)

### 5.1 One-shot run

```bash
cd ./cmd/forge-v3
go build -o forge-v3 .

./forge-v3
```

The server will:

- Listen on `:8081` (or `$PORT`) for HTTP.
- Listen on `:8082` (or `$WS_PORT`) for WebSocket worker connections.

### 5.2 Health checks

Once the server is running:

```bash
curl http://localhost:8081/health
# OR
curl http://localhost:8081/api/health
```

You should see JSON responses indicating `status: "ok"` and basic version/phase info. (Both Go and Python implementations support base paths like /health).

---

## 6. Running the Test Suite

All v3 Go tests live under `cmd/forge-v3/`.

### 6.1 Unit and integration tests

From the repo root:

```bash
cd cmd/forge-v3
go test ./...
```

This runs:

- Core queue tests (`queue_test.go`)
- Lease system tests (`lease_test.go`)
- Any additional `_test.go` files under `cmd/forge-v3/`

### 6.2 Focus on a single package or test file

```bash
# Single package
go test .

# Specific test file
go test -run TestSomething ./...
```

Use `-run` with a regex to narrow down failing tests.

---

## 7. Typical Development Workflow

1. **Sync code (outside this doc for CI workflows)**
   - Ensure your local branch is up to date with the main repo.
2. **Edit Go source**
   - Make changes under `cmd/forge-v3/` as needed (API handlers, queue logic, WebSocket code, etc.).
3. **Format code**
   - Run `gofmt` on changed files:

     ```bash
     gofmt -w path/to/file.go
     ```

4. **Run tests**
   - From `cmd/forge-v3`:

     ```bash
     go test ./...
     ```

5. **Build and smoke test**
   - Build: `go build -o forge-v3 .`
   - Start server: `./forge-v3`
   - Hit health endpoints and key APIs to sanity check.

---

## 8. Writing and Running Workers (High Level)

Detailed worker onboarding will live in a dedicated agent guide, but at a high level:

- Workers are long-lived processes that connect over WebSocket to the v3 server.
- They:
  - Register with a `MsgAgentRegister` handshake.
  - Receive `task.assigned` messages.
  - Send `task.started`, `task.completed`, and `task.failed` events.

For a concrete example, see:

- `docs/v3/WEBSOCKET_PROTOCOL.md`
- `docs/v3/API.md` (if present)
- `docs/v3/GETTING_STARTED.md` (Python agent example)

---

## 9. Where to Go Next

- **Architecture:** `docs/v3/ARCHITECTURE.md`
- **API Details:** `docs/v3/API.md` and `docs/v3/API_REFERENCE.md`
- **Operations & Troubleshooting:** `docs/v3/OPERATIONS.md`
- **Migration & Context:** `docs/v3/MIGRATION_GUIDE.md` and `docs/v3/V2_V3_MIGRATION_MAPPING.md`


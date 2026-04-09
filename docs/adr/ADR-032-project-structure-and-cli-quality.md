# ADR-032: Project Structure and CLI Quality Bar

**Date:** 2026-03-06
**Status:** Proposed
**Decision Makers:** sati orchestrator, council (minimax — pending)
**Extends:** ADR-029, ADR-031

---

## Context

FORGE has grown organically. The current structure conflates:
- The daemon (HTTP server, SQLite, WebSocket hub) — `cmd/forge-v3/main.go` (~3500 lines)
- The CLI client (cobra commands, API calls) — mixed into the same package
- The control plane config (prya role) — not distinguished in code from worker role

Reference CLIs for quality bar: **GitHub CLI (`gh`)**, **kubectl**, **Docker CLI**.

What makes them excellent:
- Consistent noun-verb structure with tab completion
- Color output that degrades gracefully (no color when piped)
- Structured output modes (`-o json`, `-o yaml`, `-o table`)
- Helpful, actionable error messages with recovery steps
- Progress indicators for long operations
- `--dry-run` on destructive commands
- Self-documenting via `--help` at every level

---

## Decision

### 1. Module separation within monorepo (no repo split yet)

```
cmd/
  forge/          ← NEW: thin CLI client binary (no HTTP server, no SQLite)
    main.go       ← cobra root + command registration
    cli_tasks.go  ← tasks noun
    cli_agents.go ← agents noun
    cli_nodes.go  ← nodes noun
    cli_daemon.go ← daemon noun (start/stop/status/logs)
    cli_init.go   ← forge init wizard
    cli_config.go ← forge config get/set/list
    cli_plugins.go← forge plugin list/install/remove
    config.go     ← viper config loader
    client.go     ← HTTP client to control plane
    output.go     ← table/json/yaml output formatting

  forge-daemon/   ← NEW: server-only binary (extracted from forge-v3)
    main.go       ← starts HTTP + WebSocket + SQLite
    (imports shared packages)

internal/         ← NEW: shared library (both CLI and daemon use)
  api/            ← API types (Task, Agent, Node)
  config/         ← config loading (viper)
  client/         ← HTTP client
```

**Transition path:** Extract over time. `cmd/forge-v3/main.go` stays as-is. New commands added to `cmd/forge/` going forward. Once parity reached, `cmd/forge-v3` is archived.

### 2. CLI quality standards

**Output formatting** (all commands):
```go
// Three output modes, selected via --output / config [ui] output
switch cfg.Output {
case "json":  json.NewEncoder(os.Stdout).Encode(data)
case "yaml":  yaml.NewEncoder(os.Stdout).Encode(data)
default:      renderTable(data)  // colored, aligned
}
```

**Color library:** `github.com/charmbracelet/lipgloss` for all table/status rendering.
**Interactive wizard:** `github.com/charmbracelet/bubbletea` for `forge init` only.
**Markdown rendering:** `github.com/charmbracelet/glamour` for `forge docs` output.

**Error format:**
```
Error: <what went wrong>

<why it happened>

Fix:
  <specific command or action to resolve>
  <alternative if first doesn't work>

See: forge help <noun> <verb>
```

**Color degradation:** Check `NO_COLOR` env var and `isatty(stdout)`. Never emit ANSI when piped.

### 3. Shell completion

```bash
forge completion bash  >> ~/.bashrc
forge completion zsh   >> ~/.zshrc
forge completion fish  > ~/.config/fish/completions/forge.fish
```

Cobra generates these automatically.

### 4. Documentation auto-update

```
Pre-commit hook:
  forge docs generate --output docs/CLI_REFERENCE.md
  git add docs/CLI_REFERENCE.md

CI job (on merge to main):
  Regenerate and commit docs/RUNBOOKS.md from template
  Regenerate docs/ADR_INDEX.md
```

`forge docs generate` uses cobra's built-in doc generation + custom markdown templates.

### 5. Agent onboarding

`forge init` is the single entry point for agents:
```
$ forge init --agent

  FORGE Agent Setup
  ─────────────────
  ? Your agent name: kimi
  ? Control plane [http://prya:8081]: 
  ? FORGE_ROOT [auto-detected]: 
  
  ✓ Connected to prya
  ✓ Registered agent kimi
  ✓ Reading your tasks: forge tasks list --assigned-to kimi
  
  Quick start:
    forge tasks list         — see your tasks
    forge tasks claim <id>   — claim a task
    forge tasks complete <id> — complete a task
    forge docs runbooks      — read agent runbooks
```

### 6. Cleanup — what to retire

| Target | Status | Action |
|--------|--------|--------|
| `bin/forge-v3` (shell router) | Deprecated | Remove after `forge daemon` commands work |
| `bin/forge-v3-start-server` etc. | Deprecated | Remove after `forge daemon start` works |
| `bin/forge-v3-deploy-nova` | Keep short-term | Replace with `forge node deploy nova` |
| `bin/forge` (43k Python) | Deprecated | Keep until v4 CLI has feature parity (2 sprints) |
| `bin/forge-server` (68k Python) | Deprecated | Remove with CC retirement (ADR-014) |
| `harness/` Python | Keep | Not CLI — automation layer |

### 7. Monorepo vs. separate repos

**Decision: stay in monorepo, enforce explicit boundaries.**

Rationale:
- Cross-cutting changes (API type change affects CLI + daemon + tests) are one PR in monorepo
- At current fleet size, cross-repo coordination cost outweighs benefits
- Module boundaries enforced by package structure, not repo structure
- Revisit when `forge-cli` and `forge-daemon` have different release cadences

---

## Consequences

- New `cmd/forge/` directory with thin client
- New `internal/` shared packages
- Charm.sh added as dependency (~5MB binary size increase, acceptable)
- `forge init` becomes the canonical new-node/new-agent setup path
- All `bin/forge-v3-*` scripts deprecated within 2 sprints
- `bin/forge` (Python v2) deprecated when v4 reaches parity
- Shell completion works out of the box
- Docs auto-generate on commit

# ADR-031: FORGE CLI Plugin System

**Date:** 2026-03-06
**Status:** Proposed
**Decision Makers:** node-2 orchestrator, council (minimax — pending)
**Extends:** ADR-029 (V4 CLI Consolidation)

---

## Context

ADR-029 defined 12 core nouns and 5 verbs. As FORGE grows across 95 projects and 11 domains, domain-specific commands will be needed that shouldn't live in the core binary. The question is how to add new nouns or new actions for existing nouns without rebuilding the core binary.

Goals:
- Any agent or developer can add a plugin without Go knowledge
- Plugins are discoverable (`forge plugins list`)
- Plugins compose naturally with core commands (pipes, JSON output)
- Plugin installation is one command
- Core binary stays lean and focused

---

## Decision

### Subprocess (git-style) plugin pattern

A binary or script named `forge-{noun}` in `$PATH` is automatically callable as `forge {noun}`.

```bash
# Plugin: ~/.forge/plugins/forge-ios (or anywhere in $PATH)
$ forge ios build             # calls forge-ios build
$ forge ios simulator list    # calls forge-ios simulator list
```

**How it works:**
1. `forge {noun} [args]` — if `{noun}` is not a built-in command:
2. forge searches `$PATH` + `~/.forge/plugins/` for `forge-{noun}`
3. If found: `exec forge-{noun} [args]` with env vars injected:
   - `FORGE_CONTROL_PLANE=http://node-1:8081`
   - `FORGE_NODE_ID=node-2`
   - `FORGE_TOKEN=...`
   - `FORGE_OUTPUT=text|json`
4. If not found: show helpful error + `forge plugins search {noun}`

### Plugin conventions

Plugins MUST implement:
```bash
forge-{noun} --help              # usage text
forge-{noun} --forge-plugin-info # JSON: name, version, description, commands[]
```

Plugins SHOULD:
- Support `--output json` for piping
- Exit 0 on success, non-zero on error
- Print errors to stderr, data to stdout

### Plugin manifest

`~/.forge/plugins/registry.toml`:
```toml
[[plugin]]
name    = "ios"
binary  = "forge-ios"
version = "1.0.0"
source  = "local"

[[plugin]]
name    = "notion"
binary  = "forge-notion"
version = "0.2.1"
source  = "https://github.com/neoforge-dev/forge-notion"
```

### Plugin management commands

```bash
forge plugin list                    # list installed plugins
forge plugin install ./forge-ios     # install from local path
forge plugin install github.com/...  # install from URL (download + chmod +x)
forge plugin info ios                # show plugin metadata
forge plugin remove ios
```

### Adding a new action to an existing noun (core extension)

For built-in nouns (tasks, agents, nodes), new actions follow the `CLIRouter` pattern:

```go
// In cmd/forge-v3/cli_tasks.go (extracted from main.go)
func registerTasksCommands(root *cobra.Command) {
    cmd := &cobra.Command{Use: "tasks"}
    cmd.AddCommand(
        &cobra.Command{Use: "list", RunE: tasksListCmd},
        &cobra.Command{Use: "create", RunE: tasksCreateCmd},
        // New action: just add here
        &cobra.Command{Use: "retry", RunE: tasksRetryCmd},
    )
    root.AddCommand(cmd)
}
```

Each noun lives in its own file: `cli_tasks.go`, `cli_agents.go`, `cli_nodes.go`, etc.

---

## Consequences

- No new dependencies for plugin loading (subprocess is stdlib)
- Plugins can be shell scripts, Python, Go, anything
- Domain-specific commands (iOS, notion, portfolio) move out of core binary
- `bin/forge-v3-*` scripts can become `forge-v3-compat` plugin for transition period
- Plugin registry enables future `forge plugin install` ecosystem

# forge node - Module Summary

## Overview

New Forge CLI module for single-node operations: health checks, node information, and metrics.

**Usage:**
```bash
forge node health [NODE_ID]      # Check node health (exit code only with -q)
forge node info [NODE_ID]         # Show detailed node information
forge node metrics [NODE_ID]      # Show CPU/RAM/Disk metrics (table, --json, --watch)
```

---

## Commands

### `forge node health [NODE_ID]`

Quick health check with exit code for scripting.

**Exit codes:**
- `0` - Node is healthy (online and >1GB RAM free)
- `1` - Node is unhealthy or data unavailable
- `2` - Error occurred

**Options:**
- `--json` - Output as JSON
- `--quiet, -q` - Silent mode (exit code only)

**Examples:**
```bash
# Check current node
forge node health

# Check specific node
forge node health node-1

# Script-friendly usage
forge node health node-4 --quiet
if [ $? -eq 0 ]; then
    echo "Node is healthy"
else
    echo "Node has issues"
fi

# JSON output
forge node health node-1 --json | jq .healthy
```

---

### `forge node info [NODE_ID]`

Detailed node information including role, hardware specs, capabilities, and current status.

**Displays:**
- Node name and role in fleet
- RAM capacity and max agents
- Accent color (for theming)
- Capabilities (agents/tools available)
- Current status (online/stale)
- RAM availability and CPU load
- Current task (if any)

**Options:**
- `--json` - Output as JSON

**Examples:**
```bash
# Local node
forge node info

# Specific node
forge node info node-3

# JSON for scripts
forge node info node-2 --json | jq .capabilities[]
```

---

### `forge node metrics [NODE_ID]`

CPU, RAM, and disk metrics from heartbeat data.

**Displays:**
- CPU load percentage
- Load average
- Core count
- RAM used/available/total
- Disk free space

**Options:**
- `--json` - Output as JSON
- `--watch, -w` - Watch mode (updates every 2s)
- `--format {table|plain}` - Output format

**Examples:**
```bash
# Table view (default)
forge node metrics

# JSON for scripting
forge node metrics --json | jq '.ram.used_percent'

# Plain text for piping
forge node metrics --format plain | grep ram

# Watch mode
forge node metrics --watch
```

---

## UNIX Philosophy Applied

| Principle | Implementation |
|-----------|----------------|
| **Do one thing well** | Each command has single, clear purpose |
| **Text streams** | `--json` flag enables piping |
| **Exit codes** | Meaningful codes for scriptability |
| **Silence is success** | `--quiet` flag suppresses output |
| **Composability** | JSON output works with `jq`, `grep` |
| **Fail fast** | Early exit on FORGE_ROOT not found |

---

## Zen of Python Applied

| Principle | Implementation |
|-----------|----------------|
| **Explicit > implicit** | Clear args, documented behavior |
| **Simple > complex** | Minimal dependencies, straightforward code |
| **Flat > nested** | Shallow function hierarchy |
| **Readability** | Clear function/variable names |
| **Errors visible** | Proper error handling, never silent |
| **Type hints** | Full type annotations |
| **Docstrings** | Comprehensive command docs |

---

## Design Decisions

### Why separate from `forge nodes`?

| Aspect | `forge nodes` (plural) | `forge node` (singular) |
|--------|-------------------------|--------------------------|
| **Scope** | Fleet-wide operations | Single-node operations |
| **Data source** | API + local files | Local heartbeat file only |
| **Use case** | `list`, `recommend`, `heartbeat` (all) | `health`, `info`, `metrics` (one) |
| **Example** | `forge nodes list` | `forge node health node-1` |

### Node Configuration

The `NODE_CONFIG` dict defines fleet metadata:

```python
NODE_CONFIG = {
    "node-1": {
        "name": "Prya",
        "role": "Hub/Command Center",
        "ram_gb": 16,
        "max_agents": 2,
        "accent": "#89b4fa",  # Blue
        "capabilities": ["claude", "kimi", "docker"],
    },
    "node-2": {
        "name": "Sati",
        "role": "Workhorse",
        "ram_gb": 64,
        "max_agents": 6,
        "accent": "#a6e3a1",  # Green
        "capabilities": ["claude", "codex", "opencode", "kilo", "docker"],
    },
    "node-3": {
        "name": "Nova",
        "role": "Power/iOS",
        "ram_gb": 48,
        "max_agents": 4,
        "accent": "#cba6f7",  # Purple
        "capabilities": ["claude", "ios", "xcode", "simulator"],
    },
    "node-4": {
        "name": "Vega",
        "role": "Auxiliary",
        "ram_gb": 16,
        "max_agents": 2,
        "accent": "#fab387",  # Orange
        "capabilities": ["claude", "glm", "minimax"],
    },
}
```

---

## Shell Integration

The dotfiles commands (`node-health`, `node-info`, `node-ram`, `node-cpu`) can now be thin wrappers:

```zsh
# config/zsh/node-4.zsh (simplified)

# Use forge CLI for node operations (now available)
alias node-health='forge node health'
alias node-info='forge node info'
alias node-ram='forge node metrics | rg "RAM"'
alias node-cpu='forge node metrics | rg "CPU"'
```

**Migration path:**
1. Shell aliases use `forge node` under the hood
2. Users keep same commands (`node-health`, etc.)
3. Consistent behavior across fleet
4. API improvements automatically available

---

## Testing

```bash
# Test health check
forge node health
echo $?  # Should be 0 for healthy

# Test info
forge node info

# Test metrics with JSON
forge node metrics --json | jq .

# Test watch mode (Ctrl+C to exit)
forge node metrics --watch

# Test specific node
forge node health node-3
forge node info node-2
```

---

## Future Enhancements

- [ ] Add `--format {json|table|csv}` to all commands
- [ ] Add `forge node list` to show all known nodes from config
- [ ] Add `forge node ssh` to SSH into node
- [ ] Add `forge node restart` to restart agent sessions
- [ ] Integrate with Command Center API for remote queries

---

## Related Commands

| Command | Purpose |
|---------|---------|
| `forge nodes list` | List all nodes from API/heartbeat files |
| `forge nodes recommend` | Get recommended node for a task type |
| `forge nodes heartbeat` | Publish telemetry to control plane |
| `forge fleet status` | Fleet-wide status dashboard |

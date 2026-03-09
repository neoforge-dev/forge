# HRN-016: Fleet Health Dashboard Implementation

## Summary

✅ **IMPLEMENTED** - Fleet health dashboard for FORGE harness at `/home/openclaw/work/FORGE/harness/forge_harness/fleet/dashboard.py`

## Features Delivered

### Core Functionality
- ✅ Lists all tmux sessions with agent type tags
- ✅ Shows last activity timestamp for each agent  
- ✅ Displays estimated context usage (via output size proxy)
- ✅ Highlights stale agents (>30min no activity)
- ✅ Supports watch mode with configurable auto-refresh (default 10s)

### Module Exports
- ✅ `get_fleet_status()` -> FleetStatus dataclass with agents list
- ✅ `FleetAgent` dataclass with: name, session_id, agent_type, last_activity, context_estimate, is_stale
- ✅ `FleetStatus` dataclass with summary statistics

### Technical Implementation

**Tmux Integration:**
- Uses `subprocess.run` for all tmux commands as specified
- `tmux list-sessions` for session discovery
- `tmux capture-pane -p` for context estimation  
- Parses session names for agent types
- Uses `datetime` for staleness check (>30min)

**Agent Type Detection:**
- claude, cursor, tech, cto, product, content -> "claude"
- pi -> "pi"
- amp -> "amp" 
- opencode -> "opencode"
- codex -> "codex"
- gemini -> "gemini"

**Status Detection:**
- Analyzes terminal output for status patterns
- error: ["error", "failed", "traceback", "exception", "panic"]
- active: ["generating", "running", "processing", "thinking", "writing", "reading", "executing", "working", "analyzing"]
- idle: ["waiting", "idle", "paused", "ready", "completed", "finished", "done"]

### Usage Examples

**Basic Status:**
```bash
python3 forge_harness/fleet/dashboard.py
# Output: FORGE Fleet Status - 2026-02-04 07:58:25 UTC
#         Total: 13, Active: 4, Stale: 0
```

**Watch Mode:**
```bash
python3 forge_harness/fleet/dashboard.py --watch --interval 10 --count 3
# Refreshes every 10s for 3 iterations with live updates
```

**Programmatic:**
```python
from forge_harness.fleet.dashboard import get_fleet_status, FleetAgent, FleetStatus

status = get_fleet_status()
print(f"Found {len(status.agents)} agents")
stale_agents = [agent for agent in status.agents if agent.is_stale]
```

## Testing Results

✅ All tests passed:
- Found 13 active agents in current tmux server
- Proper stale detection (>30min threshold)
- Agent type classification working
- Context estimation via output size
- Watch mode with configurable refresh intervals
- Module exports working correctly

## File Structure

```
forge_harness/fleet/
├── __init__.py          # Module exports
└── dashboard.py          # Main implementation (414 lines)
```

## Integration Notes

- Follows existing `forge_harness` logging patterns
- Compatible with existing tmux fleet management scripts
- Uses same agent type detection as other FORGE tools
- Integrates with `session_tracker.py` patterns for consistency

The implementation fully satisfies HRN-016 requirements and is ready for production use.
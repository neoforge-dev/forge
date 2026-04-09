# Agent Packet 02: Dispatch

Purpose: route work using canonical dispatch paths only.

## 1. Local Agent Dispatch

Preferred:

```bash
forge dispatch send forge:gemini "Task: .forge/dispatches/dispatch-X.md"
```

Note: `agent-message.sh` has been deleted. Use `forge dispatch send` exclusively.

## 2. Dispatch Rules

1. Never use raw `tmux send-keys` for task delivery.
2. Keep inline dispatch messages short; put detail in `.forge/dispatches/*.md`.
3. Require result files under `.forge/heartbeat/results/` before considering a task complete.

## 3. Completion Contract

Agent writes:

```bash
cat > .forge/heartbeat/results/gemini-TASK-123.md <<'EOF2'
# Result: gemini — TASK-123

**Status**: DONE
**Changes**: [summary]
**Tests**: [pass/fail details]
EOF2
```

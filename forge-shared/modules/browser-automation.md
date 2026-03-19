# Browser Automation Rules

**Use `agent-browser` CLI instead of Playwright MCP.**

## Why agent-browser CLI
- **Faster**: <100ms startup vs ~3s for MCP
- **Simpler**: Direct CLI, no protocol overhead
- **AI-optimized**: `snapshot` returns accessibility tree with element refs
- **Installed**: `/opt/homebrew/bin/agent-browser`

## Quick Reference
```bash
agent-browser open "https://example.com"
agent-browser snapshot                    # AI-readable page state
agent-browser click "@button-submit"      # Use @ref from snapshot
agent-browser type "@input-email" "user@example.com"
agent-browser fill "@input-password" "secret"
agent-browser select "@dropdown" "Option 1"
agent-browser wait "@loading" --gone      # Wait for element to disappear
agent-browser screenshot result.png
```

## Workflow
```bash
agent-browser open "https://app.example.com"  # 1. Open
agent-browser snapshot                         # 2. Get refs
agent-browser click "@login-button"            # 3. Interact
agent-browser snapshot                         # 4. Verify
```

## Rules
- **NEVER** use `mcp__playwright__*` tools for testing
- Delegate browser testing to agents with agent-browser configured
- **Exception**: Playwright MCP only for one-off automation, scraping, or doc screenshots

See `.forge/memories/browser-automation.md` for full documentation.

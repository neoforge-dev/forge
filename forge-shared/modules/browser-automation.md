# Browser Automation

<!-- Last Updated: 2026-03-21 -->

> **DEPRECATION NOTICE**: Browser automation is not a standard FORGE pattern as of March 2026.
> The `agent-browser` CLI and Playwright MCP are not actively used in fleet operations.
> This document is retained for reference only. Do not adopt browser automation for new tasks
> without explicit orchestrator approval.

---

## Current Status

Browser automation is **not in active use** by FORGE fleet agents. Reasons:

1. Portfolio products are tested via unit/integration tests (pytest, vitest, go test)
2. E2E testing is handled by Playwright CLI during product development — not by fleet agents
3. The `agent-browser` CLI referenced below is macOS-only (`/opt/homebrew/bin/agent-browser`) and unavailable on Linux nodes (prya, sati, nova, vega)
4. Screenshot automation for iOS is handled by `harness/forge_harness/ios_harness/screenshot_automation.py`

---

## If You Need Browser Automation

For portfolio product E2E tests (React frontends), use Playwright directly:

```bash
npm run test:e2e               # Runs Playwright tests defined in playwright.config.ts
npx playwright test            # Direct invocation
npx playwright test --headed   # With visible browser
```

For iOS screenshots (App Store assets):
```bash
uv run python -m forge_harness.cli_v2 ios screenshot
```

---

## Legacy Reference: agent-browser CLI (macOS only)

The following was the previous standard. It is macOS-specific and not available on Linux nodes.

```bash
agent-browser open "https://example.com"
agent-browser snapshot                    # AI-readable page state
agent-browser click "@button-submit"      # Use @ref from snapshot
agent-browser type "@input-email" "user@example.com"
agent-browser screenshot result.png
```

---

## Rules

- **NEVER** use `mcp__playwright__*` tools — MCP is not used in FORGE (see CLAUDE.md rule 0)
- Do not add browser automation dependencies to fleet agent workflows
- Browser-based testing belongs in product repos, not in `cmd/forge/` or `cmd/forged/`
- For any new browser automation need, raise with the orchestrator first

---

## See Also

- iOS screenshot automation: `harness/forge_harness/ios_harness/screenshot_automation.py`
- iOS testing rules: `.claude/rules/ios-testing.md`
- Quality gates: `forge-shared/modules/human-gates.md`

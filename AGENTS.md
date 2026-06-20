# FORGE Agent Operations Guide

This file is the active onboarding surface for new agents.

If a command or workflow is not referenced here, in [README.md](README.md), or in [CANONICAL_WORKFLOW.md](docs/runbooks/CANONICAL_WORKFLOW.md), treat it as historical until verified.

## 0. Control-Plane Sanity

Before trusting fleet, task, or agent state, confirm which daemon the CLI is
targeting:

```bash
forge config list
forge daemon status
forge operator repair --sync-tmux --session forge
forge operator status --use-tmux-agents --session forge
forge operator status
forge attention
forge portfolio status
```

Use `FORGE_API_URL=http://localhost:8081` when the configured hub is stale or
unreachable but the local daemon is the intended working reality. Do not
dispatch work until `forge operator status` is `READY`, or until you explicitly
accept a `DEGRADED` state and route only to dispatch-ready agents.

## 1. Active Surfaces

Read these in order:

1. [README.md](README.md)
2. [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)
3. [docs/ACTIVE_SURFACES.md](docs/ACTIVE_SURFACES.md)
4. [docs/INFRASTRUCTURE_MAP.md](docs/INFRASTRUCTURE_MAP.md) — progressive disclosure (Tier 1-3)
5. [docs/STRATEGY.md](docs/STRATEGY.md) — strategy index and gradual disclosure entrypoint
6. [docs/runbooks/CANONICAL_WORKFLOW.md](docs/runbooks/CANONICAL_WORKFLOW.md)
7. [config/portfolio/portfolio-state.yaml](config/portfolio/portfolio-state.yaml) (copy from `examples/portfolio/sample-portfolio-state.yaml` if missing)

## 2. Core Rules

1. Use the `forge` CLI first.
2. **Dispatch hierarchy:** (a) `forge task create` for queue-based work (PRIMARY), (b) Task tool for code changes, (c) `forge dispatch send` for named agent override (SECONDARY). Never raw `tmux send-keys`.
3. Use the portfolio operating loop before creating new MVP scope.
4. If a doc references `forge-harness`, `CLI v2`, or wrapper-first workflows, treat it as legacy.
5. Do not broaden scope when an existing product is blocked at `validate`, `deploy`, or `measure`.

## 3. One-Command Node Join

Run this once on a new node:

```bash
cd /path/to/forge/cmd/forge
go build -o forge .
./forge node join
```

Override when needed:

```bash
./forge node join --node-id node-b --agents "agent-a agent-b"
./forge node join --check
```

## 4. First Commands

These are the commands every onboarded agent should know:

```bash
forge config list
forge daemon status
forge operator repair --sync-tmux --session forge
forge operator status --use-tmux-agents --session forge
forge status
forge agent list
forge attention
forge portfolio status
forge task list
forge dispatch send kimi --file .forge/dispatches/task.md
```

Several low-frequency nouns (`fleet`, `lead`, `message`, …) are hidden from default `forge --help`. Run `forge advanced` to print their names, then `forge <noun> --help` for details.

Useful follow-ups:

```bash
forge portfolio list
forge portfolio show <product-key>
forge node list
forge agent list
forge daemon status
```

## 5. Dispatch Convention

Write detailed briefs to `.forge/dispatches/` and send a short reference:

```bash
forge dispatch send gemini --file .forge/dispatches/my-task.md
```

Do not use:

```bash
tmux send-keys -t forge:agent "message" Enter
```

Interactive `tmux send-keys` is acceptable only for approvals or restarts when an agent is already waiting at its own prompt.

**Note:** §10’s bootstrap wake line is **bootstrap only** (wake an agent window). It is not a substitute for `forge task create` / `forge dispatch send` / the task queue (§2).

## 6. Portfolio Guardrail

FORGE is optimizing for shipped revenue, not raw activity.

The active operating loop is:

`idea -> validate -> build -> deploy -> measure -> monetize -> scale|kill`

Before taking on net-new work:

```bash
forge portfolio status
forge portfolio list
forge portfolio show <product>
```

The current working set is tracked in [portfolio-state.yaml](config/portfolio/portfolio-state.yaml).

## 7. Deprecated for New Work

Do not use these for new workflows:

- `forge-harness ...`
- old “CLI v2” references as a canonical surface
- raw `tmux send-keys` for task delivery
- wrapper-first flows in `bin/` when `forge` already has the command
- ad hoc `curl /api/...` for normal operator workflows

Historical material remains in the repo for now, but it is not an onboarding surface.

## 8. Open Source Boundary

The long-term split is:

- Public orchestration layer: CLI, control plane, dashboard, routing, approvals, Trinity interfaces
- Private portfolio layer: domains, MVPs, analytics, revenue-sensitive context

Until that split is complete, keep the active operational truth in the files listed under `Active Surfaces`.

## 9. If Unsure

Use this decision order:

1. `forge --help`
2. [ACTIVE_SURFACES.md](docs/ACTIVE_SURFACES.md)
3. [CANONICAL_WORKFLOW.md](docs/runbooks/CANONICAL_WORKFLOW.md)
4. [LEGACY_TOOLING_POLICY.md](docs/runbooks/LEGACY_TOOLING_POLICY.md)

If the command or workflow is not verifiable there, do not assume it is current.

## 10. Agent Start Commands

When starting fleet agents in tmux windows, use these exact commands:

| Agent | Start Command | Notes |
|-------|--------------|-------|
| `claude` | `claude --dangerously-skip-permissions` | Primary implementation agent |
| `kimi` | `kimi -y` | Auto-accept prompts |
| `gemini` | `gemini -y` | Auto-accept prompts |
| `pi` | `pi` | No flags needed |
| `minimax` | `minimax` | No flags needed |
| `opencode` | `opencode` | Heavy — sati/nova only |
| `kilo` | `kilo` | Heavy — sati/nova only |
| `cursor` | `cursor-agent -f` | Human-steered interactive |
| `amp` | `amp --dangerously-allow-all` | Autonomous mode |
| `codex` | `codex --dangerously-bypass-approvals-and-sandbox` | Research + strategy |

**Dispatch after start (bootstrap only — §2 still governs real task delivery):** Wait 5 seconds for agent initialization, then send a short wake line via:

```bash
tmux send-keys -t forge:AGENT -l "message" && sleep 0.1 && tmux send-keys -t forge:AGENT Enter
```

## 11. Skill Compatibility Matrix

`.claude/skills/` skills are Claude Code features. Non-Claude agents receive **natural language prompts** and don't invoke skills directly. The table below maps each skill to its compatibility and the equivalent approach for non-Claude agents.

### Direct Invocation: Claude Code Only

These skills require Claude Code infrastructure and cannot run in other agents:

| Skill | Why Claude Code Only |
|-------|---------------------|
| `auto-test-runner` | Hook-triggered, runs on file save events |
| `auto-security-scan` | Hook-triggered, invoked by Claude Code hooks |
| `compact` | Manages Claude Code context window |
| `handoff` | Saves Claude Code session state |
| `overnight-dispatch` | Chains `forge dispatch send` calls via Claude Code |

### Cross-Agent Skills (any agent can do the work with a text prompt)

For non-Claude agents, phrase the request as a natural language task — no slash command needed.

| Skill | Claude Code | kimi / pi / glm / minimax | gemini | Notes |
|-------|-------------|--------------------------|--------|-------|
| `content-library-producer` | `/content-library-producer` | "Write 5 blog post outlines for..." | ✅ | Any capable writer |
| `content-publisher` | `/content-publisher` | "Format this outline for WordPress..." | ✅ | Any capable writer |
| `niche-explorer` | `/niche-explorer` | "Research the [market] opportunity..." | ✅ best | gemini excels at research |
| `mvp-spec-writer` | `/mvp-spec-writer` | "Create feature backlog for [product]..." | ✅ best | gemini excels at planning |
| `human-review-gate` | `/human-review-gate` | "Score risk and recommend escalation..." | ✅ | |

### Code Generation Skills (Claude Code or capable code agents)

| Skill | Claude Code | opencode / kilo / glm | kimi / pi | Notes |
|-------|-------------|----------------------|-----------|-------|
| `frontend-design` | ✅ | ✅ | ❌ avoid | Needs strong UI taste |
| `pwa-frontend-lite` | ✅ | ✅ | ❌ avoid | Needs React/Vite knowledge |
| `fastapi-service-template` | ✅ | ✅ | ❌ avoid | Needs boilerplate generation |
| `ios-agent` | ✅ | ❌ | ❌ | iOS harness integration only |
| `ios-design` | ✅ | ❌ | ❌ | HIG-compliant SwiftUI patterns |
| `stripe-best-practices` | ✅ | ✅ | ✅ | Stripe integration guidance |
| `upgrade-stripe` | ✅ | ✅ | ❌ | Stripe API version upgrades |

### Quality / Testing Skills (require code execution context)

| Skill | Claude Code | kimi | pi | Notes |
|-------|-------------|------|-----|-------|
| `integration-tester` | ✅ | ✅ | ✅ | kimi: best for coverage loops |

### Quick Rule

> **Writing code or running tests?** Use Claude Code, opencode, kilo, kimi, or glm.
> **Research, planning, content, analysis?** Use gemini or any agent with a clear text prompt.
> **Skills?** Only available in Claude Code windows — other agents get the same result via a well-phrased task.

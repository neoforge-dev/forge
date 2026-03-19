# FORGE Agent Operations Guide

This file is the active onboarding surface for new agents.

If a command or workflow is not referenced here, in [README.md](README.md), or in [CANONICAL_WORKFLOW.md](docs/runbooks/CANONICAL_WORKFLOW.md), treat it as historical until verified.

## 1. Active Surfaces

Read these in order:

1. [README.md](README.md)
2. [CANONICAL_WORKFLOW.md](docs/runbooks/CANONICAL_WORKFLOW.md)
3. [ACTIVE_SURFACES.md](docs/ACTIVE_SURFACES.md)
4. [FORGE_BIG_PICTURE.md](docs/FORGE_BIG_PICTURE.md)
5. [FORGE_SIMPLIFICATION_PLAN.md](docs/FORGE_SIMPLIFICATION_PLAN.md)
6. [OPERATING_LOOP_V1.md](docs/portfolio/OPERATING_LOOP_V1.md)
7. [portfolio-state.yaml](config/portfolio/portfolio-state.yaml) (copy from `examples/portfolio/sample-portfolio-state.yaml` if missing)

## 2. Core Rules

1. Use the `forge` CLI first.
2. Use `forge dispatch send`, not raw `tmux send-keys`, for task delivery.
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
forge status
forge fleet status
forge portfolio status
forge task list
forge dispatch send forge:kimi "Read .forge/dispatches/task.md — EXECUTE now"
```

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
forge dispatch send forge:gemini "Read .forge/dispatches/my-task.md — EXECUTE now"
```

Do not use:

```bash
tmux send-keys -t forge:agent "message" Enter
```

Interactive `tmux send-keys` is acceptable only for approvals or restarts when an agent is already waiting at its own prompt.

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

## 10. Skill Compatibility Matrix

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
| `compliance-playbook-writer` | `/compliance-playbook-writer` | "Write COPPA compliance SOP for..." | ✅ | |
| `update-broadcaster` | `/update-broadcaster` | "Summarize these changes for stakeholders" | ✅ | |
| `niche-explorer` | `/niche-explorer` | "Research the [market] opportunity..." | ✅ best | gemini excels at research |
| `mvp-spec-writer` | `/mvp-spec-writer` | "Create feature backlog for [product]..." | ✅ best | gemini excels at planning |
| `mvp-bootstrap-orchestrator` | `/mvp-bootstrap-orchestrator` | "Plan MVP kickoff for [domain]..." | ✅ | Complex; gemini preferred |
| `human-review-gate` | `/human-review-gate` | "Score risk and recommend escalation..." | ✅ | |
| `llm-prompt-guardrails` | `/llm-prompt-guardrails` | "Design safe prompt for [use case]..." | ✅ | |

### Code Generation Skills (Claude Code or capable code agents)

| Skill | Claude Code | opencode / kilo / glm | kimi / pi | Notes |
|-------|-------------|----------------------|-----------|-------|
| `frontend-design` | ✅ | ✅ | ❌ avoid | Needs strong UI taste |
| `pwa-frontend-lite` | ✅ | ✅ | ❌ avoid | Needs React/Vite knowledge |
| `fastapi-service-template` | ✅ | ✅ | ❌ avoid | Needs boilerplate generation |
| `ios-agent` | ✅ | ❌ | ❌ | iOS harness integration only |

### Quality / Testing Skills (require code execution context)

| Skill | Claude Code | kimi | pi | Notes |
|-------|-------------|------|-----|-------|
| `integration-tester` | ✅ | ✅ | ✅ | kimi: best for coverage loops |
| `migration-assistant` | ✅ | ✅ | ❌ | Needs DB + code context |
| `performance-profiler` | ✅ | ❌ | ✅ | Analysis + profiling output |

### Quick Rule

> **Writing code or running tests?** Use Claude Code, opencode, kilo, kimi, or glm.
> **Research, planning, content, analysis?** Use gemini or any agent with a clear text prompt.
> **Skills?** Only available in Claude Code windows — other agents get the same result via a well-phrased task.

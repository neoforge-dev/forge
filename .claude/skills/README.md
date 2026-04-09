# FORGE Project Skills

Skills that require LLM reasoning. For deterministic operations, use `forge` CLI commands.

**Principle:** "There should be one-- and preferably only one --obvious way to do it."
- CLI commands (`forge <cmd>`) are portable across all agent decoders
- Skills are for tasks that need LLM creativity, synthesis, or judgment
- If a CLI command exists, use it — not a skill

## Active Skills (14)

### Model-Invoked (Claude Code only)
| Skill | Purpose |
|-------|---------|
| auto-test-runner | Map file changes to relevant tests |
| auto-security-scan | Analyze code for security patterns |

### Session Management
| Skill | Purpose | Notes |
|-------|---------|-------|
| compact | Emergency context compaction | Prompt-only, no CLI equivalent |
| overnight-dispatch | Multi-agent wave dispatch | Composite: chains dispatches |

### Content & Documentation
| Skill | Purpose |
|-------|---------|
| content-library-producer | Generate content outlines |
| content-publisher | Format content for platforms (includes stakeholder updates) |

### Code Generation (need LLM creativity)
| Skill | Purpose |
|-------|---------|
| frontend-design | Creative UI/UX design |
| pwa-frontend-lite | PWA scaffolding with design decisions |
| fastapi-service-template | API scaffolding |
| ios-agent | iOS build/test automation |
| ios-design | SwiftUI design system + HIG compliance |

### Analysis & Planning (need LLM reasoning)
| Skill | Purpose |
|-------|---------|
| niche-explorer | Market analysis |
| mvp-spec-writer | Feature backlog generation |
| human-review-gate | Risk scoring and escalation |

### Testing & Quality
| Skill | Purpose |
|-------|---------|
| integration-tester | Cross-service integration testing |

## Portable Workflows (forge-shared/)

These are harness-agnostic — usable by Claude Code, Codex, Cursor, Gemini, Amp, etc.

### Commands (forge-shared/commands/) — Session Playbooks
| Command | Purpose |
|---------|---------|
| continue | Resume from handoff (node-aware) |
| handoff | Save context for session continuity (node-aware) |
| plan | Research and create implementation plan |
| execute | Implement plan with pragmatic TDD |
| review | Code review |
| debug | Investigate and fix bugs |
| prime | Prime session with context |

### Skills (forge-shared/skills/) — Portable Skills
8 portable skills synced to `.codex/skills/` and `.gemini/skills/`.

### Modules (forge-shared/modules/) — Shared Context
13 shared modules including `dispatch-decision.md`, `royal-jelly.md`, `llm-prompt-guardrails.md`.

## Archived Skills (in .archive/skills/)

| Former Skill | Reason |
|-------------|--------|
| update-broadcaster | Merged into content-publisher (was 25-line stub) |
| mvp-bootstrap-orchestrator | Stale paths, overlaps niche-explorer + mvp-spec-writer |
| compliance-playbook-writer | Not used in revenue sprint (50-line stub) |
| migration-assistant | Theoretical, no evidence of use |
| performance-profiler | Theoretical, no evidence of use |
| llm-prompt-guardrails | Reclassified as reference doc → `forge-shared/modules/` |

## Deleted Skills (replaced by CLI)

| Former Skill | Use Instead |
|-------------|-------------|
| fleet, fleet-ops, fleet-watch | `forge fleet status/save/restore/heartbeat/monitor` |
| dispatch | `forge dispatch send AGENT "message"` |
| docs | `qmd search "query"` or `forge search` |
| command-center | `https://prya.queue-great.ts.net/` |
| heartbeat | `forge heartbeat` |
| checkpoint | `forge handoff create` |
| context-loader | SessionStart hook (automatic) |
| complete-task, ship-feature | `forge complete TASK-ID [--push]` |
| spawn-agent | `forge fleet spawn` |
| forge-harness | `forge loop run` |
| research-digest-compiler | niche-explorer skill + web search |
| handoff-clean | Merged into handoff command (clean variant section) |
| living-docs | Never existed (ghost reference removed) |

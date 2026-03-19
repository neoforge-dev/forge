# FORGE Project Skills

Skills that require LLM reasoning. For deterministic operations, use `forge` CLI commands.

**Principle:** "There should be one-- and preferably only one --obvious way to do it."
- CLI commands (`forge <cmd>`) are portable across all agent decoders
- Skills are for tasks that need LLM creativity, synthesis, or judgment
- If a CLI command exists, use it — not a skill

## Remaining Skills (23)

### Model-Invoked (Claude Code only)
| Skill | Purpose |
|-------|---------|
| auto-test-runner | Map file changes to relevant tests |
| auto-security-scan | Analyze code for security patterns |

### Session Management
| Skill | Purpose | Notes |
|-------|---------|-------|
| compact | Emergency context compaction | Prompt-only, no CLI equivalent |
| handoff-clean | Generate handoff + save context | Composite: chains CLI commands |
| overnight-dispatch | Multi-agent wave dispatch | Composite: chains dispatches |

### Content & Documentation
| Skill | Purpose |
|-------|---------|
| living-docs | Read/update/sync doc pyramid |
| content-library-producer | Generate content outlines |
| content-publisher | Format content for platforms |
| update-broadcaster | Prepare stakeholder updates |
| compliance-playbook-writer | Generate policy docs |

### Code Generation (need LLM creativity)
| Skill | Purpose |
|-------|---------|
| frontend-design | Creative UI/UX design |
| pwa-frontend-lite | PWA scaffolding with design decisions |
| fastapi-service-template | API scaffolding |
| ios-agent | iOS build/test automation |

### Analysis & Planning (need LLM reasoning)
| Skill | Purpose |
|-------|---------|
| niche-explorer | Market analysis |
| mvp-spec-writer | Feature backlog generation |
| mvp-bootstrap-orchestrator | Multi-step project kickoff |
| llm-prompt-guardrails | Prompt design and validation |
| human-review-gate | Risk scoring and escalation |

### Testing & Quality
| Skill | Purpose |
|-------|---------|
| integration-tester | Cross-service integration testing |
| migration-assistant | Database migration with safety checks |
| performance-profiler | Performance analysis |

## Deleted Skills (replaced by CLI)

| Former Skill | Use Instead |
|-------------|-------------|
| fleet, fleet-ops, fleet-watch | `forge fleet status/save/restore/heartbeat/monitor` |
| dispatch | `forge dispatch send AGENT "message"` |
| docs | `qmd search "query"` or `forge search` |
| command-center | `https://node-1.queue-great.ts.net/` |
| heartbeat | `forge heartbeat` |
| checkpoint | `forge handoff create` |
| context-loader | SessionStart hook (automatic) |
| complete-task, ship-feature | `forge complete TASK-ID [--push]` |
| spawn-agent | `forge fleet spawn` |
| forge-harness | `forge loop run` |
| research-digest-compiler | `living-docs` skill + web search |

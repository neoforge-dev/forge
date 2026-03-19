# FORGE Shared Resources

Single source of truth for cross-agent portable content. All coding agents (Claude, Gemini, Codex, OpenCode, Cursor, Amp, Kimi) reference these files.

## Directory Structure

```
forge-shared/
├── skills/          # Portable skill definitions (markdown)
├── commands/        # Agent-agnostic workflow playbooks
├── modules/         # Shared instruction fragments
└── README.md        # This file
```

## Skills

Portable skills that work across all agents:

| Skill | Description |
|-------|-------------|
| `compliance-playbook-writer` | Generate policy docs, SOPs, compliance checklists |
| `content-library-producer` | Create 50-piece content libraries |
| `fastapi-service-template` | Scaffold production-ready FastAPI backends |
| `frontend-design` | Create distinctive, production-grade UI |
| `llm-prompt-guardrails` | Design validated LLM prompts with schemas and safety |
| `mvp-spec-writer` | Generate features.json from exploration reports |
| `niche-explorer` | Structured market analysis and MVP recommendations |
| `pwa-frontend-lite` | Build lightweight PWA frontends |

## Commands (Workflow Playbooks)

Agent-agnostic workflows that each agent maps to its native command format:

| Command | Description |
|---------|-------------|
| `plan` | Research, design architecture, create implementation plan |
| `review` | Code review for security, performance, quality |
| `execute` | Implement plan with pragmatic TDD |
| `handoff` | Save context for session continuity |
| `debug` | Investigate and fix bugs systematically |
| `prime` | Prime session with context for focused work |
| `continue` | Resume work from a handoff prompt |

## Modules

Shared knowledge fragments imported by agent configs:

| Module | Description |
|--------|-------------|
| `tech-stack` | Core tech stack standards (FastAPI, React, uv) |
| `project-registry` | All 95 projects across 11 domains with tiers |
| `orchestrator-rules` | Orchestrator delegation rules |
| `code-quality` | Quality gates and standards |
| `git-workflow` | Git conventions and branching |
| `human-gates` | Human review escalation criteria |
| `fleet-dispatch` | Fleet agent dispatch patterns |
| `fleet-management` | Fleet architecture and management |
| `browser-automation` | Browser automation patterns |

## How Agents Use These

### Claude Code
- `.claude/skills/` references these via symlinks or imports
- `.claude/commands/` contains Claude-native versions
- `.claude/modules/` symlinks to `forge-shared/modules/`

### Gemini CLI
- `.gemini/skills/` symlinks to `forge-shared/skills/`

### Codex CLI
- `.codex/skills/` symlinks to `forge-shared/skills/`

### OpenCode
- `.opencode/skills/` symlinks to `forge-shared/skills/`

### Cursor
- `.cursor/rules/forge.mdc` references `AGENTS.md` which imports modules

### Amp
- Reads `AGENTS.md` which references `forge-shared/`

## Syncing

Run `scripts/sync-skills.sh` to update all agent directories from this source:

```bash
./scripts/sync-skills.sh
```

## Adding New Portable Skills

1. Create the skill in `forge-shared/skills/new-skill.md`
2. Run `./scripts/sync-skills.sh` to propagate to all agents
3. Update this README

## Memory Layer

All agents should read `.forge/memories/INDEX.md` at session start for shared context.
This is separate from skills - memories are runtime state, skills are static knowledge.

---

> **Note**: The Python package documentation for `forge-shared` has been preserved in `README.python-package.md`.

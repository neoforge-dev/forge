---
name: handoff
description: Save context and create handoff for session continuity
---

# Handoff

Save current state and generate a prompt for continuing in a new session.

## Use When
- At end of session
- Before context gets too large
- When switching tasks
- As a checkpoint during long work

## Gather Current State

1. **Project Context**: name, tech stack, branch, recent commits, status
2. **Work Status**: completed, in progress, blocked
3. **Key Files**: entry points, config, focus files, tests
4. **Active Plan**: plan file, current phase/task, next steps
5. **Important Context**: decisions, gotchas, patterns, things to avoid

## Output: docs/PROMPT.md

Generate a continuation prompt including:
- Project overview (name, purpose, tech stack, repo)
- Current state (branch, progress, focus, blockers)
- Active plan (file, phase, task, next steps)
- Key context (files, decisions, gotchas, patterns)
- Commands to run (verify, test, start dev)
- Instructions for new agent (mindset, workflow, quality gates)
- Resume command

## Save Location
Save to `docs/PROMPT.md`

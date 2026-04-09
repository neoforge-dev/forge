---
name: compact
description: Emergency context compaction when context window exceeds 75%. Summarizes session, saves distilled state to .forge/memories/, then prompts user to run /clear. User must clear context manually.
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write, Bash, Glob]
---

# Compact Skill

Emergency context compaction to prevent agent failure when context window approaches capacity. Creates a distilled summary for agent restart while preserving critical information.

## When to Use

| Trigger | Threshold | Purpose |
|---------|-----------|---------|
| **Auto** | Context > 75% | Preemptive compaction before critical |
| **Critical** | Context > 85% | Emergency compaction with aggressive pruning |
| **User** | `/compact` command | Manual compaction before long operation |
| **Pre-handoff** | Before agent switch | Clean state for next agent |
| **Rate Limit** | Token budget warning | Reduce context to continue working |

## Warning Signs

```
⚠️  Context Compaction Recommended When:

1. Response latency increases (>10 seconds)
2. Tool calls start failing intermittently
3. Context size > 150K tokens (75% of 200K)
4. Multiple large outputs completed (>30KB each)
5. Agent has been active > 30 minutes continuously
6. Rate limit warnings appear
```

## Workflow

### 1. Assess Context Composition

```python
def analyze_context_composition():
    """Analyze what's consuming context space."""
    
    composition = {
        "file_reads": count_file_contents(),
        "previous_outputs": estimate_output_tokens(),
        "conversation_history": count_chat_tokens(),
        "tool_results": count_tool_output_tokens(),
        "system_prompt": get_system_prompt_size(),
    }
    
    total = sum(composition.values())
    utilization = (total / CONTEXT_LIMIT) * 100
    
    return {
        "total_tokens": total,
        "utilization_percent": utilization,
        "breakdown": composition,
        "can_compact": utilization > 75,
    }
```

**Example Analysis:**
```
Context Analysis:
═══════════════════════════════════════════════════════════════
Total Tokens: 165,000 / 200,000 (82.5% utilization)

Breakdown:
  📁 File Reads:        45,000 tokens (27%)
  📝 Previous Outputs:  80,000 tokens (48%) ← COMPACT TARGET
  💬 Conversation:      25,000 tokens (15%)
  🛠️  Tool Results:     12,000 tokens (7%)
  ⚙️  System:           3,000 tokens (2%)

Status: 🔴 CRITICAL - Compaction required
═══════════════════════════════════════════════════════════════
```

### 2. Summarize Session Decisions

```python
def create_distilled_summary():
    """Create compact summary of session for restart."""
    
    summary = {
        "compact_version": "1.0",
        "timestamp": datetime.utcnow().isoformat(),
        "session_metadata": {
            "agent": get_current_agent(),
            "session_duration": calculate_session_duration(),
            "original_context_tokens": get_current_token_count(),
            "compaction_reason": get_trigger_reason(),
        },
        
        # Critical: What was accomplished
        "deliverables": extract_deliverables_summary(),
        
        # Critical: Active work
        "in_progress": extract_in_progress_work(),
        
        # Critical: Decisions that affect future work
        "key_decisions": extract_key_decisions(),
        
        # Critical: What to do next
        "next_actions": get_prioritized_todos(),
        
        # Important: Cross-references
        "dependencies": extract_cross_references(),
        
        # Nice to have: Files modified
        "files_modified": get_essential_files_only(),
        
        # Nice to have: Patterns identified
        "patterns_identified": extract_pattern_names(),
    }
    
    return summary
```

**Distilled Summary Structure:**
```markdown
# Session Compact Summary
**Generated:** 2026-02-04T08:45:00Z  
**Agent:** kimi  
**Reason:** Context at 82% (165K/200K tokens)  
**Session Duration:** 45 minutes

## ✅ Deliverables Completed (Last 5)
1. **DATABASE_PATTERNS.md** (44KB) - Complete
   - Connection pooling, migrations, query patterns
   - Key sources: adguild-platform, voice-coach
   
2. **TESTING_PATTERNS.md** (32KB) - Complete
   - pytest fixtures, async testing, factories
   - Key sources: interview-simulator
   
3. **LOGGING_PATTERNS.md** (32KB) - Complete
   - structlog, formatters, context management
   - Key sources: forge-shared, pkm-ai

## 🔨 In Progress
- **MONITORING_PATTERNS.md** (41KB) - 90% complete
  - Remaining: Sentry integration section, final review
  - Current section: Frontend Performance Monitoring

## 🎯 Key Decisions
| Decision | Rationale | Impact |
|----------|-----------|--------|
| Use structlog for structured logging | Better JSON output, context binding | All backend projects |
| Prometheus metrics pattern | Industry standard, K8s native | Deployment guides |
| Token limit 5-6 guides/hour | Empirical from marathon analysis | Future planning |

## 📋 Next Actions (Priority Order)
1. Complete MONITORING_PATTERNS.md (est. 10 min)
2. Update ARCHITECTURE_INDEX.md with all new guides
3. Create DEPLOYMENT_PATTERNS.md (if time permits)
4. Run consistency check across all guides

## 🔗 Critical Dependencies
- ARCHITECTURE_INDEX.md links all pattern guides
- SECRETS_PATTERNS.md depends on DATABASE_PATTERNS (config examples)
- All guides reference portfolio projects in leanvibe-dev/, brandfocus-ai/

## 📁 Essential Files (Read if continuing)
Must re-read:
- `.forge/ARCHITECTURE_INDEX.md` (for cross-references)
- Current work-in-progress file
- Any portfolio files needed for remaining tasks

Can skip:
- Previously completed pattern guides (summarized above)
- Intermediate analysis files
- Tool output history

## 🧩 Patterns Identified
- Pydantic Settings with SecretStr
- Token encryption at rest with Fernet
- API key generation with secrets module
- Database migration safety patterns
```

### 3. Save State to .forge/memories/

```python
def save_compact_state(summary: dict):
    """Save distilled state for agent restart."""
    
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"session-compact-{timestamp}.md"
    filepath = f".forge/memories/{filename}"
    
    # Write compact summary
    write_markdown(filepath, format_summary_as_markdown(summary))
    
    # Also save machine-readable JSON
    json_filepath = filepath.replace(".md", ".json")
    write_json(json_filepath, summary)
    
    # Update index
    update_compact_index(filepath, summary)
    
    return {
        "markdown_path": filepath,
        "json_path": json_filepath,
        "original_tokens": summary["session_metadata"]["original_context_tokens"],
        "summary_tokens": estimate_tokens(summary),
        "compression_ratio": calculate_compression(summary),
    }
```

**Output Files:**
```
.forge/memories/
├── session-compact-20260204-084500.md    # Human-readable
├── session-compact-20260204-084500.json  # Machine-readable
└── compact-index.json                     # Index of all compactions
```

**JSON Format:**
```json
{
  "compact_version": "1.0",
  "timestamp": "2026-02-04T08:45:00Z",
  "session_metadata": {
    "agent": "kimi",
    "session_duration_minutes": 45,
    "original_context_tokens": 165000,
    "compaction_reason": "context_threshold_75_percent"
  },
  "deliverables": [
    {
      "name": "DATABASE_PATTERNS.md",
      "size_kb": 44,
      "status": "complete",
      "key_topics": ["connection pooling", "migrations", "query patterns"],
      "sources": ["adguild-platform", "voice-coach"]
    }
  ],
  "in_progress": {
    "name": "MONITORING_PATTERNS.md",
    "completion_percent": 90,
    "current_section": "Frontend Performance Monitoring",
    "remaining_sections": ["Sentry Integration", "Final Review"]
  },
  "key_decisions": [
    {
      "decision": "Use structlog for structured logging",
      "rationale": "Better JSON output, context binding",
      "impact": "All backend projects"
    }
  ],
  "next_actions": [
    {"priority": 1, "task": "Complete MONITORING_PATTERNS.md", "estimate": "10 min"},
    {"priority": 2, "task": "Update ARCHITECTURE_INDEX.md", "estimate": "5 min"}
  ],
  "dependencies": [
    "ARCHITECTURE_INDEX.md links all guides",
    "SECRETS_PATTERNS depends on DATABASE_PATTERNS"
  ],
  "essential_files": [
    ".forge/ARCHITECTURE_INDEX.md",
    ".forge/MONITORING_PATTERNS.md"
  ],
  "patterns_identified": [
    "Pydantic Settings with SecretStr",
    "Token encryption at rest with Fernet"
  ]
}
```

### 4. Prompt User to Clear Context

**Claude Code cannot clear its own context.** After saving state, prompt the user:

```python
def prompt_context_clear(compact_result: dict):
    """Prompt user to clear context and provide resume instructions."""

    prompt = f"""
═══════════════════════════════════════════════════════════════
⚠️  CONTEXT CLEAR REQUIRED (User Action)
═══════════════════════════════════════════════════════════════

Session state saved to:
  📄 {compact_result['markdown_path']}
  📊 {compact_result['json_path']}

To complete compaction, run:

  /clear

Then resume with:

  Resume from compact. Read {compact_result['markdown_path']}

Or simply run:

  /continue

═══════════════════════════════════════════════════════════════
"""
    return prompt
```

**What Gets Cleared (after user runs /clear):**
```
Before /clear:
  Total: 165,000 tokens (82.5%)

After /clear:
  Total: ~5,000 tokens (2.5%) - only system prompt

After Resume:
  Total: ~15,000 tokens (7.5%) - system + compact summary + essential files
```

**What's Preserved in Files (not context):**
- ✅ Compact summary with deliverables, decisions, next actions
- ✅ JSON state for programmatic access
- ✅ Git history of all changes
- ✅ All files in `.forge/` directory

### 5. Provide Restart Guidance

```python
def generate_restart_guidance(compact_result: dict):
    """Generate instructions for restarting agent with compacted state."""
    
    guidance = f"""
═══════════════════════════════════════════════════════════════
🔄 AGENT RESTART GUIDANCE
═══════════════════════════════════════════════════════════════

Context has been compacted due to high utilization.

📊 Compaction Results:
   Original: {compact_result['original_tokens']:,} tokens
   Current:  {compact_result['new_context_size']:,} tokens
   Saved:    {compact_result['tokens_saved']:,} tokens ({compact_result['compression_ratio']}%)

📄 Compact State Saved:
   Markdown: {compact_result['markdown_path']}
   JSON:     {compact_result['json_path']}

═══════════════════════════════════════════════════════════════
🚀 TO CONTINUE WORK:
═══════════════════════════════════════════════════════════════

1. READ THE COMPACT SUMMARY:
   ```
   ReadFile: {{"path": "{compact_result['markdown_path']}"}}
   ```

2. LOAD ESSENTIAL CONTEXT:
   Based on summary, re-read:
   {format_essential_files(compact_result['essential_files'])}

3. VERIFY CURRENT STATE:
   - Check git status: `git status`
   - Confirm last output: `cat {compact_result['last_output']}`
   - Review todo list in compact summary

4. RESUME WORK:
   - Next action from summary: {compact_result['next_action']}
   - Current focus: {compact_result['current_focus']}

═══════════════════════════════════════════════════════════════
⚠️  IMPORTANT NOTES:
═══════════════════════════════════════════════════════════════

- Previous outputs are SAVED but not in context
- Use ARCHITECTURE_INDEX.md for cross-references
- Re-read portfolio files as needed for remaining work
- Completed work is preserved in git and .forge/

- If starting FRESH agent:
  1. Read compact summary first
  2. Read ARCHITECTURE_INDEX.md
  3. Read only files needed for current task
  4. Continue from "Next Actions" in summary

═══════════════════════════════════════════════════════════════
    """
    
    return guidance
```

## Compact Report

```
═══════════════════════════════════════════════════════════════
🗜️  CONTEXT COMPACTION - STATE SAVED
═══════════════════════════════════════════════════════════════

⏱️  Time: 2026-02-04 08:45:00 UTC
🤖 Agent: kimi
🎯 Trigger: Context at 82% (165K/200K tokens)

💾 State Saved:
   📄 .forge/memories/session-compact-20260204-084500.md
   📊 .forge/memories/session-compact-20260204-084500.json

✅ What's Preserved (in files):
   - 3 deliverables summarized (DATABASE, TESTING, LOGGING patterns)
   - 1 in-progress item (MONITORING patterns at 90%)
   - 4 key decisions documented
   - 4 next actions prioritized
   - Essential file references

📋 Next Actions (from compact):
   1. Complete MONITORING_PATTERNS.md
   2. Update ARCHITECTURE_INDEX.md

═══════════════════════════════════════════════════════════════
⚠️  ACTION REQUIRED: Clear context to complete compaction
═══════════════════════════════════════════════════════════════

Run this command to clear context:

   /clear

Then resume with:

   Resume from compact. Read .forge/memories/session-compact-20260204-084500.md

Or simply:

   /continue

═══════════════════════════════════════════════════════════════
```

## Integration with Checkpoint Skill

```python
# Compact vs Checkpoint - When to use each:

def choose_preservation_strategy():
    """Decide between checkpoint and compact."""
    
    context = get_context_utilization()
    
    if context < 50:
        return "checkpoint", "Normal progress save"
    
    elif context < 75:
        return "checkpoint", "Regular checkpoint before growth"
    
    elif context < 85:
        return "compact", "Context compaction recommended"
    
    else:
        return "compact_emergency", "Emergency compaction required"
```

| Feature | Checkpoint | Compact |
|---------|------------|---------|
| **When** | Every 10 tasks, natural breaks | Context > 75%, emergency |
| **Preserves** | Full context summary | Distilled summary only |
| **Git Commit** | Yes | Optional (if dirty state) |
| **Context Cleared** | No | Yes (aggressive) |
| **Use Case** | Recovery, handoffs | Continue same agent, restart |
| **Output Size** | ~5-10KB | ~2-5KB |
| **Files** | JSON + optional commit | Markdown + JSON |

## Auto-Trigger Configuration

```python
# Add to agent configuration
COMPACT_CONFIG = {
    "warning_threshold": 0.75,      # Warn at 75%
    "critical_threshold": 0.85,     # Force at 85%
    "emergency_threshold": 0.95,    # Aggressive at 95%
    
    "auto_compact": True,
    "preserve_commits": True,       # Git commit before compact
    "min_session_duration": 300,    # 5 minutes minimum
    
    "notification": {
        "console": True,
        "fleet_events": True,
        "user_prompt": False,        # Don't interrupt flow
    }
}
```

## Command Usage

```bash
# Run compact (saves state, provides clear instructions)
/compact

# Force immediate compact even if context < 75%
/compact --force

# Compact with custom reason
/compact "Before attempting complex refactoring"

# Compact and create checkpoint
/compact --with-checkpoint

# View last compact summary
/compact --show-last
```

## ⚠️ CRITICAL: Context Clear is a USER Action

**Claude Code cannot clear its own context programmatically.** The compact skill:

1. ✅ Saves session state to `.forge/memories/`
2. ✅ Creates restart guidance
3. ❌ **CANNOT** clear context automatically

**After running `/compact`, the USER must run `/clear` to actually clear the context.**

### Complete Workflow

```
1. Agent detects high context (>75%) or user runs /compact
2. Agent saves state → .forge/memories/session-compact-{timestamp}.md
3. Agent outputs: "Context saved. Run /clear to clear context."
4. USER runs /clear
5. USER pastes resume prompt OR runs /continue
6. Agent reads compact summary and resumes work
```

### Resume Prompt Template

After `/clear`, paste this to resume:

```
Resume from compact. Read .forge/memories/session-compact-{timestamp}.md and continue work.
```

Or use the `/continue` skill which auto-detects the latest compact file.

## Error Handling

| Scenario | Response |
|----------|----------|
| Context < 75% | Warn but allow if `--force` |
| Write permission denied | Log to stdout, retry with alternate path |
| Git dirty state | Auto-commit with compact message |
| Compaction fails | Emergency clear (keep only todos) |
| Restart fails to load compact | Fall back to ARCHITECTURE_INDEX |

## Best Practices

1. **Compact Before Critical Operations**
   - Before running tests on large codebase
   - Before multi-file refactoring
   - Before complex merge/rebase operations

2. **Don't Compact Too Early**
   - Wait until > 75% to avoid thrashing
   - Let context build for efficiency
   - Use checkpoint for normal saves

3. **Verify After Compact**
   - Confirm essential context retained
   - Test that compact summary is readable
   - Check that next actions are clear

4. **Fresh Agent Protocol**
   - Always read compact summary first
   - Read ARCHITECTURE_INDEX for cross-references
   - Re-read only files needed for current task
   - Don't try to load full previous context

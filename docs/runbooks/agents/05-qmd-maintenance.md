# Agent Packet 05: QMD Maintenance

Purpose: keep QMD semantic search fresh so humans and agents get current workflow answers.

Preferred wrapper (recommended):

```bash
python scripts/qmd_maintenance.py --skip-embed
```

## 1. Refresh Lexical Index First

```bash
qmd update
```

Do this before semantic maintenance so new/renamed canonical docs are discoverable.

## 2. Baseline Check

```bash
qmd status
```

If `pending` is high or vectors are stale, refresh embeddings.

## 3. Refresh Embeddings

```bash
qmd embed --auto
```

Run after:
1. canonical workflow/runbook changes
2. major architecture docs updates
3. archiving or replacing operational docs

If model downloads stall, retry later and keep using `qmd search` after `qmd update` until vectors are rebuilt.

## 4. Verify Query Quality

```bash
qmd search "forge lead send --strict"
qmd search --files "CANONICAL_WORKFLOW"
```

Expected:
1. current runbooks rank above archived plans
2. strict cross-node command examples are discoverable

## 5. Failure Recovery

If `qmd query` or `qmd search` is stale or empty:
1. run `qmd update`
2. run `qmd status`
3. run `qmd embed --auto`
4. retry with `qmd search --files` first, then semantic query

Concurrency rule:
1. do not run parallel `qmd search/query` commands from orchestrator loops
2. serialize QMD calls to avoid `SQLITE_BUSY_RECOVERY` lock failures

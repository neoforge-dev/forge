# ADR-035 Council Brief — Fluid Per-Node Fleet Scaling

**Date:** 2026-03-07
**Read first:** `docs/adr/ADR-035-fluid-per-node-fleet-scaling.md`

---

## Your Mission

You are a council agent reviewing ADR-035, which proposes a hybrid fleet autoscaling system for
FORGE. The system must keep AI agents busy with meaningful work while respecting RAM and token
budget limits across 5 nodes (prya 16GB, sati 64GB, nova 48GB, vega 16GB, gaea 16GB).

Read the full ADR-035 first. Then answer all 8 strategic questions below with concrete
recommendations. Your output goes into `.forge/heartbeat/results/AGENTNAME-adr035-council.md`.

**DO NOT COMMIT. DO NOT PUSH. Write results to results file only.**

---

## The 8 Strategic Questions — Your Verdict on Each

**Q1. Per-node daemon deployment priority**
ADR-025 says each node should run its own forge-v3 daemon, but only prya is deployed.
For Phase 2 node-local scaling, should sati get its own daemon first (before cross-node
XNode ADR-023 is complete)? Or stay prya-only until XNode is ready?
→ Concrete recommendation: which node gets daemon first, and what is the precondition?

**Q2. Token budget initialization**
We have real numbers: anthropic ~18% weekly used, openai ~96% monthly used.
Should we seed `.forge/heartbeat/token-budgets-prya.json` now so `forge fleet inventory`
shows live data immediately?
→ YES or NO, and if YES: what other fields to seed today?

**Q3. prya agent ceiling: 2 or 3?**
prya hard ceiling is currently 2 agents (16GB node). If min floor = 1 kimi (100MB),
only 1 more lightweight can auto-inflate before hitting the ceiling.
Should we raise to 3 for lightweight-only agents (300MB total overhead)?
→ Specific number with RAM math justification.

**Q4. Council integration timing**
Should `forge council start` be in Phase 1 (simple dispatch wrapper, no cleanup patrol)
or Phase 2 (full TTL + cleanup + manual-spawn markers)?
→ Phase 1 or Phase 2, with rationale.

**Q5. Overnight scale-to-zero**
After queue empty ≥ 2h: kill all agents including min floor. Cold-start cost = 30–60s
when next task arrives. Acceptable?
→ YES/NO. If NO: what is the correct overnight behavior?

**Q6. OpenAI at 96% monthly**
opencode and kilo are effectively unavailable until April 1.
Should we set `cooldown_until = 2026-04-01T00:00:00Z` for openai provider in the
initial budget seed so the scaler never attempts them?
→ YES or NO. What should happen to tasks that require heavy code execution (opencode
capability) during this cooldown?

**Q7. Token ledger: per-node JSON vs single merged file**
Per-node JSON files (ADR-034 council decision, keeps SQLite as aggregate view) vs
single `.forge/token-ledger.json` written by all nodes.
→ Which approach? Defend with the merge-conflict and partition-tolerance arguments.

**Q8. Medium-tier auto-approve on sati**
Currently claude/cursor/amp require human approval everywhere.
On sati (64GB), 800MB per agent is trivial. Should medium-tier auto-approve on sati
but stay manual on prya?
→ Node-differentiated approval policy or uniform? Exact rule.

---

## Bonus: Wildcard Finding

If you find a gap, risk, or opportunity in ADR-035 that the 8 questions don't cover,
add a "Wildcard" section to your results. This is where you add the most value.

---

## Result Format

Write to: `.forge/heartbeat/results/AGENTNAME-adr035-council.md`

```markdown
# Council Verdict — [Your Agent Name]
**Date:** 2026-03-07

## Q1: Per-node daemon priority
[Your answer]

## Q2: Token budget init
[Your answer]

## Q3: prya ceiling
[Your answer]

## Q4: Council timing
[Your answer]

## Q5: Overnight scale-to-zero
[Your answer]

## Q6: OpenAI cooldown
[Your answer]

## Q7: Token ledger design
[Your answer]

## Q8: Medium-tier sati
[Your answer]

## Wildcard
[Optional — your most important finding not covered above]
```

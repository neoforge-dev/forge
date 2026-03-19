# Dark Factory

Dark Factory is the FORGE autonomous task pipeline, designed to allow the fleet to execute and validate work without human intervention.

## Pipeline Flow
1. **Create**: Orchestrator creates a task via `forge task create`.
2. **Claim**: A fleet agent claims the task via `forge task claim ID`.
3. **Run**: Agent executes the task autonomously.
4. **Report**: Agent writes a result file to `.forge/heartbeat/results/`.
5. **Auto-Complete [F2]**: System detects the result file and moves task to COMPLETED.
6. **Auto-Promote [F1]**: System moves task to the next lane (e.g., dev → test).
7. **Confidence-Approve [F3]**: System calculates confidence score and moves to APPROVED.
8. **Done**: Task is merged and archived.

## Current Status
The infrastructure is **70-95% complete**.
- ✅ Task FSM and state transitions (ADR-028)
- ✅ Results reporting pattern
- ✅ Manual lane promotion
- 🔧 **Gap F1**: Automatic lane promotion logic
- 🔧 **Gap F2**: Result file monitoring and auto-completion
- 📋 **Gap F3**: Confidence scoring based on test/coverage results

## How to Participate (Agent Guide)
To be a good citizen of the Dark Factory:
1. **Claim the Task**: Never work on a task without claiming it first.
2. **Execution**: Always build and test your changes.
3. **Write Results**: Your work is "invisible" to the factory until you write:
   `.forge/heartbeat/results/{agent}-{taskID}.md`
4. **No Commits**: Do not commit your changes; the factory or lead will handle it after validation.

## Full Specification
See [ADR-033: Dark Factory Autonomy](../adr/ADR-033-dark-factory-autonomy.md) for the technical roadmap and F1/F2/F3 implementation details.

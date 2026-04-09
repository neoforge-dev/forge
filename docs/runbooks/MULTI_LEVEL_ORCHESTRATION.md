# Runbook: Multi-Level Orchestration Patterns

This runbook codifies the patterns used for orchestrating autonomous work across the FORGE monorepo, spanning from high-level portfolio oversight to domain-specific logic and agent-level implementation.

---

## 1. Orchestration Architecture

Orchestration is divided into two primary layers: **Infrastructure** (process isolation) and **Logical** (task management).

### 1.1 Infrastructure Tier (tmux Hierarchy)
We utilize a three-tier `tmux` hierarchy to isolate concerns and prevent context leakage.

- **Tier 1: `forge` (Meta-Orchestrator):** Portfolio-wide monitoring and strategic cross-domain coordination.
- **Tier 2: `forge-<domain>` (Domain Orchestrator):** Venture-specific management (e.g., `forge-codeswiftr`). Each domain has its own session to isolate virtual environments.
- **Tier 3: `forge-agent` (Execution Agent):** Atomic task execution windows, often ephemeral.

### 1.2 Logical Tier (The Orchestrator Component)
The software `Orchestrator` manages the lifecycle of a task through structured planning and execution.

- **Planning Mode:** Focuses on decomposing the user's objective into a multi-step plan.
  - *Cooperative Planning:* The orchestrator discusses the plan with domain experts before finalizing.
  - *Autonomous Planning:* The orchestrator generates and validates the plan internally.
- **Execution Mode:** Steps through the plan, dispatching sub-tasks to specialized agents.
- **Replanning:** Triggered when execution results deviate from expected outcomes or new information is discovered.

---

## 2. Dispatch & Execution Patterns

### 2.1 The Dispatch Flow
1. **Core Dispatch:** Meta-orchestrator identifies a priority.
2. **Logical Planning:** The `Orchestrator` generates an `OrchestratorState` containing the initial `Plan`.
3. **Step Execution:** The `Orchestrator` iterates through rounds, selecting the next step and the appropriate `speaker` (agent).
4. **Verification:** Each step result is analyzed. If successful, the orchestrator moves to the next step; otherwise, it triggers a `replan`.

### 2.2 Orchestrator Configuration (`OrchestratorConfig`)
Standard configurations for dispatching:
- **`max_turns`:** Maximum rounds of agent interaction (default: 20).
- **`max_replans`:** Maximum number of times the orchestrator can revise the plan (default: 3).
- **`autonomous_execution`:** Whether to proceed without human confirmation for each step.
- **`cooperative_planning`:** Whether to consult agents during the planning phase.

---

## 3. State & Persistence (`OrchestratorState`)

The orchestrator's state is preserved to enable recovery and auditability.

- **Task & Plan:** The root objective and the sequence of steps.
- **Rounds:** A history of every interaction, including prompts, agent responses, and tool calls.
- **Collected Info:** A structured store of facts discovered during execution.
- **Status:** Current phase (`planning`, `executing`, `replanning`, `completed`, `failed`).

---

## 4. Quota Handling & Fallbacks

### 4.1 Rate Limit Strategy
- **Token Budgeting:** Use `max_tokens_per_turn` in `OrchestratorConfig` to prevent runaway sessions.
- **Throttling:** Domain sessions implement a cooldown (e.g., 60s) between major iterations.

### 4.2 Provider Fallbacks
1. **Tier 1 (Planning):** `claude-3-5-sonnet-latest` or `gpt-4o` (High reasoning for complex plans).
2. **Tier 2 (Execution):** Project-specific models (e.g., `gemini-1-5-flash` for high-volume analysis).
3. **Tier 3 (Recovery):** `claude-3-haiku` (Fast, low-cost for state cleanup and log parsing).

---

## 5. Recovery Procedures

### 5.1 State Reconciliation
If an orchestration session crashes:
1. **Locate Checkpoint:** Find the latest `.orchestrator_state/*.json`.
2. **Inspect Rounds:** Determine the last successful step completed.
3. **Resume Execution:** Re-instantiate the `Orchestrator` with the saved `OrchestratorState`.

### 5.2 Emergency Kill-Switch
- **Software:** Setting `state.status = 'failed'` in the orchestrator control loop.
- **Infrastructure:** `tmux kill-session -t forge-<domain>` to stop all underlying agent processes.

### 5.3 Replanning Logic
If an agent returns a "Blocker" or "Unknown Error":
1. Orchestrator captures the error in `collected_info`.
2. Status is set to `replanning`.
3. Orchestrator model is prompted with the current state and the error to generate a `Revised Plan`.
4. Execution resumes from the first new step of the revised plan.
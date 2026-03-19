//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 163: task_state_machine.go pure-logic functions
// Targets:
//   IsTerminalState        (task_state_machine.go:402) — terminal vs non-terminal states
//   GetTransitionAction    (task_state_machine.go:408) — known/unknown transitions
//   StateMachine.IsValidTransition (task_state_machine.go:160) — valid/invalid/self
//   StateMachine.GetAllowedTransitions (task_state_machine.go:397) — returns slice
//   DefaultStateMachineConfig (task_state_machine.go:425) — returns struct
// ---------------------------------------------------------------------------

// newTestStateMachine163 builds a StateMachine with a real test DB.
func newTestStateMachine163(t *testing.T) *StateMachine {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	store := NewTaskStore(db)
	return NewStateMachine(store, db)
}

// ---------------------------------------------------------------------------
// IsTerminalState
// ---------------------------------------------------------------------------

func TestWave163_IsTerminalState_Approved(t *testing.T) {
	if !IsTerminalState(StateApproved) {
		t.Error("expected StateApproved to be terminal")
	}
}

func TestWave163_IsTerminalState_Queued(t *testing.T) {
	if IsTerminalState(StateQueued) {
		t.Error("expected StateQueued to NOT be terminal")
	}
}

func TestWave163_IsTerminalState_Running(t *testing.T) {
	if IsTerminalState(StateRunning) {
		t.Error("expected StateRunning to NOT be terminal")
	}
}

func TestWave163_IsTerminalState_Failed(t *testing.T) {
	// Failed has a retry path to QUEUED — not terminal
	if IsTerminalState(StateFailed) {
		t.Error("expected StateFailed to NOT be terminal (retry path exists)")
	}
}

// ---------------------------------------------------------------------------
// GetTransitionAction
// ---------------------------------------------------------------------------

func TestWave163_GetTransitionAction_KnownTransition(t *testing.T) {
	got := GetTransitionAction(StateRunning, StateCompleted)
	want := "Task completed successfully"
	if got != want {
		t.Errorf("GetTransitionAction(Running→Completed) = %q, want %q", got, want)
	}
}

func TestWave163_GetTransitionAction_QueuedToDispatched(t *testing.T) {
	got := GetTransitionAction(StateQueued, StateDispatched)
	want := "Lease acquired, worktree created"
	if got != want {
		t.Errorf("GetTransitionAction(Queued→Dispatched) = %q, want %q", got, want)
	}
}

func TestWave163_GetTransitionAction_UnknownTransition(t *testing.T) {
	got := GetTransitionAction(StateApproved, StateQueued)
	// Unknown transition returns a formatted fallback string
	if got == "" {
		t.Error("expected non-empty fallback for unknown transition")
	}
}

// ---------------------------------------------------------------------------
// StateMachine.IsValidTransition
// ---------------------------------------------------------------------------

func TestWave163_IsValidTransition_ValidQueuedToDispatched(t *testing.T) {
	sm := newTestStateMachine163(t)
	if !sm.IsValidTransition(StateQueued, StateDispatched) {
		t.Error("expected QUEUED→DISPATCHED to be valid")
	}
}

func TestWave163_IsValidTransition_InvalidQueuedToCompleted(t *testing.T) {
	sm := newTestStateMachine163(t)
	if sm.IsValidTransition(StateQueued, StateCompleted) {
		t.Error("expected QUEUED→COMPLETED to be invalid")
	}
}

func TestWave163_IsValidTransition_SelfRunning(t *testing.T) {
	sm := newTestStateMachine163(t)
	// RUNNING→RUNNING is a valid heartbeat self-transition
	if !sm.IsValidTransition(StateRunning, StateRunning) {
		t.Error("expected RUNNING→RUNNING self-transition to be valid")
	}
}

func TestWave163_IsValidTransition_ApprovedToAnything(t *testing.T) {
	sm := newTestStateMachine163(t)
	// APPROVED is terminal — no transitions allowed
	if sm.IsValidTransition(StateApproved, StateQueued) {
		t.Error("expected no transitions from APPROVED state")
	}
}

func TestWave163_IsValidTransition_FailedToQueued(t *testing.T) {
	sm := newTestStateMachine163(t)
	// FAILED→QUEUED is the retry path
	if !sm.IsValidTransition(StateFailed, StateQueued) {
		t.Error("expected FAILED→QUEUED (retry) to be valid")
	}
}

// ---------------------------------------------------------------------------
// StateMachine.GetAllowedTransitions
// ---------------------------------------------------------------------------

func TestWave163_GetAllowedTransitions_Queued(t *testing.T) {
	sm := newTestStateMachine163(t)
	transitions := sm.GetAllowedTransitions(StateQueued)
	if len(transitions) == 0 {
		t.Error("expected at least 1 transition from QUEUED")
	}
	found := false
	for _, s := range transitions {
		if s == StateDispatched {
			found = true
		}
	}
	if !found {
		t.Error("expected StateDispatched in QUEUED transitions")
	}
}

func TestWave163_GetAllowedTransitions_Approved(t *testing.T) {
	sm := newTestStateMachine163(t)
	transitions := sm.GetAllowedTransitions(StateApproved)
	if len(transitions) != 0 {
		t.Errorf("expected 0 transitions from APPROVED (terminal), got %v", transitions)
	}
}

// ---------------------------------------------------------------------------
// DefaultStateMachineConfig
// ---------------------------------------------------------------------------

func TestWave163_DefaultStateMachineConfig_EnabledHooks(t *testing.T) {
	cfg := DefaultStateMachineConfig()
	if cfg == nil {
		t.Fatal("expected non-nil config")
	}
	if !cfg.EnableHooks {
		t.Error("expected EnableHooks=true by default")
	}
	if cfg.SkipValidation {
		t.Error("expected SkipValidation=false by default")
	}
	if cfg.HookTimeout <= 0 {
		t.Error("expected positive HookTimeout by default")
	}
}

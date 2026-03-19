//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 203: task_state_machine.go pure functions + StateMachine basics
// Targets:
//   NewStateMachine              (task_state_machine.go:62)
//   StateMachine.IsValidTransition(task_state_machine.go:160) — self/valid/invalid
//   StateMachine.GetAllowedTransitions(task_state_machine.go:397) — QUEUED/unknown
//   IsTerminalState              (task_state_machine.go:402) — terminal/non-terminal
//   GetTransitionAction          (task_state_machine.go:408) — known/unknown
//   DefaultStateMachineConfig    (task_state_machine.go:425)
//   RegisterOnEnter/OnExit/OnTransition (task_state_machine.go:144-157)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewStateMachine
// ---------------------------------------------------------------------------

func TestWave203_NewStateMachine_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)
	if sm == nil {
		t.Error("expected non-nil StateMachine")
	}
}

// ---------------------------------------------------------------------------
// IsValidTransition
// ---------------------------------------------------------------------------

func TestWave203_IsValidTransition_SelfTransitionRunning(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	// RUNNING->RUNNING is explicitly allowed (heartbeats)
	if !sm.IsValidTransition(StateRunning, StateRunning) {
		t.Error("expected RUNNING->RUNNING to be valid (self-transition)")
	}
}

func TestWave203_IsValidTransition_ValidTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	// QUEUED -> DISPATCHED is a standard valid transition
	if !sm.IsValidTransition(StateQueued, StateDispatched) {
		t.Error("expected QUEUED->DISPATCHED to be valid")
	}
}

func TestWave203_IsValidTransition_InvalidTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	// COMPLETED -> QUEUED should not be valid (terminal state)
	if sm.IsValidTransition(StateCompleted, StateQueued) {
		t.Error("expected COMPLETED->QUEUED to be invalid")
	}
}

func TestWave203_IsValidTransition_UnknownFromState(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	// Unknown state has no transitions
	if sm.IsValidTransition(TaskState("UNKNOWN_STATE"), StateQueued) {
		t.Error("expected UNKNOWN_STATE->QUEUED to be invalid")
	}
}

// ---------------------------------------------------------------------------
// GetAllowedTransitions
// ---------------------------------------------------------------------------

func TestWave203_GetAllowedTransitions_FromQueued(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	transitions := sm.GetAllowedTransitions(StateQueued)
	if len(transitions) == 0 {
		t.Error("expected non-empty allowed transitions from QUEUED")
	}
}

func TestWave203_GetAllowedTransitions_FromUnknown(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	transitions := sm.GetAllowedTransitions(TaskState("UNKNOWN_STATE"))
	// Unknown state returns nil/empty
	_ = transitions
}

// ---------------------------------------------------------------------------
// IsTerminalState
// ---------------------------------------------------------------------------

func TestWave203_IsTerminalState_Terminal(t *testing.T) {
	// APPROVED is the only true terminal state (empty transitions)
	if !IsTerminalState(StateApproved) {
		t.Error("expected APPROVED to be terminal")
	}
}

func TestWave203_IsTerminalState_NonTerminal(t *testing.T) {
	// QUEUED should not be terminal
	if IsTerminalState(StateQueued) {
		t.Error("expected QUEUED to be non-terminal")
	}
}

// ---------------------------------------------------------------------------
// GetTransitionAction
// ---------------------------------------------------------------------------

func TestWave203_GetTransitionAction_KnownTransition(t *testing.T) {
	// QUEUED->DISPATCHED has a registered action
	action := GetTransitionAction(StateQueued, StateDispatched)
	if action == "" {
		t.Error("expected non-empty action for QUEUED->DISPATCHED")
	}
}

func TestWave203_GetTransitionAction_UnknownTransition(t *testing.T) {
	// Unknown transition returns fallback string
	action := GetTransitionAction(TaskState("FOO"), TaskState("BAR"))
	if action == "" {
		t.Error("expected non-empty fallback action for unknown transition")
	}
}

// ---------------------------------------------------------------------------
// DefaultStateMachineConfig
// ---------------------------------------------------------------------------

func TestWave203_DefaultStateMachineConfig_NonNil(t *testing.T) {
	cfg := DefaultStateMachineConfig()
	if cfg == nil {
		t.Error("expected non-nil StateMachineConfig")
	}
	if !cfg.EnableHooks {
		t.Error("expected EnableHooks=true by default")
	}
	if cfg.HookTimeout == 0 {
		t.Error("expected non-zero HookTimeout")
	}
}

// ---------------------------------------------------------------------------
// RegisterOnEnter / RegisterOnExit / RegisterOnTransition
// ---------------------------------------------------------------------------

func TestWave203_RegisterOnEnter_NoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	called := false
	sm.RegisterOnEnter(StateQueued, func(taskID string, from, to TaskState, reason string) error {
		called = true
		return nil
	})
	_ = called // hook registered — just no panic
}

func TestWave203_RegisterOnExit_NoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	sm.RegisterOnExit(StateRunning, func(taskID string, from, to TaskState, reason string) error {
		return nil
	})
}

func TestWave203_RegisterOnTransition_NoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	sm.RegisterOnTransition(StateQueued, StateDispatched, func(taskID string, from, to TaskState, reason string) error {
		return nil
	})
}

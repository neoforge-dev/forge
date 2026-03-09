//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"fmt"
	"log"
	"time"

	"github.com/google/uuid"
)

// StateTransitionRule defines a valid state transition
type StateTransitionRule struct {
	From   TaskState
	To     TaskState
	Action string
}

// ValidTransitions defines all allowed state transitions per ADR-028
// Format: map[FromState][]ToState
var ValidTransitions = map[TaskState][]TaskState{
	StateQueued:     {StateDispatched},
	StateDispatched: {StateRunning, StateFailed},
	StateRunning:    {StateRunning, StateBlocked, StateCompleted, StateFailed},
	StateBlocked:    {StateRunning},
	StateCompleted:  {StateApproved, StateFailed},
	StateApproved:   {}, // Terminal state - no outgoing transitions
	StateFailed:     {StateQueued}, // Retry path
}

// TransitionActions maps transitions to human-readable actions
var TransitionActions = map[string]string{
	fmt.Sprintf("%s->%s", StateQueued, StateDispatched):     "Lease acquired, worktree created",
	fmt.Sprintf("%s->%s", StateDispatched, StateRunning):    "Agent started execution",
	fmt.Sprintf("%s->%s", StateDispatched, StateFailed):     "Agent failed to start",
	fmt.Sprintf("%s->%s", StateRunning, StateRunning):       "Heartbeat received",
	fmt.Sprintf("%s->%s", StateRunning, StateBlocked):       "Agent blocked, needs attention",
	fmt.Sprintf("%s->%s", StateRunning, StateCompleted):     "Task completed successfully",
	fmt.Sprintf("%s->%s", StateRunning, StateFailed):        "Task execution failed",
	fmt.Sprintf("%s->%s", StateBlocked, StateRunning):       "Block resolved, resuming",
	fmt.Sprintf("%s->%s", StateCompleted, StateApproved):    "Changes approved, merging",
	fmt.Sprintf("%s->%s", StateCompleted, StateFailed):      "Changes rejected",
	fmt.Sprintf("%s->%s", StateFailed, StateQueued):         "Retrying failed task",
}

// StateHook is a function called during state transitions
type StateHook func(taskID string, from, to TaskState, reason string) error

// StateMachine manages task state transitions with validation and hooks
type StateMachine struct {
	store     *TaskStore
	db        *sql.DB
	onEnter   map[TaskState][]StateHook
	onExit    map[TaskState][]StateHook
	onTransit map[string][]StateHook // key: "FROM->TO"
}

// NewStateMachine creates a new state machine with the given store and database
func NewStateMachine(store *TaskStore, db *sql.DB) *StateMachine {
	sm := &StateMachine{
		store:     store,
		db:        db,
		onEnter:   make(map[TaskState][]StateHook),
		onExit:    make(map[TaskState][]StateHook),
		onTransit: make(map[string][]StateHook),
	}

	// Register default hooks
	sm.registerDefaultHooks()

	return sm
}

// registerDefaultHooks sets up the default state transition hooks per ADR-028
func (sm *StateMachine) registerDefaultHooks() {
	// OnEnter DISPATCHED: Create worktree
	sm.RegisterOnEnter(StateDispatched, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s entering DISPATCHED - worktree should be created", taskID)
		// Worktree creation is handled by the lease manager / agent spawner
		return nil
	})

	// OnEnter RUNNING: Update lease started_at
	sm.RegisterOnEnter(StateRunning, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s entering RUNNING - execution started", taskID)
		// Lease timing updates handled by lease manager
		return nil
	})

	// OnEnter BLOCKED: Emit NeedsAttention event
	sm.RegisterOnEnter(StateBlocked, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s BLOCKED - emitting NeedsAttention: %s", taskID, reason)
		// Event emission would be handled here
		return nil
	})

	// OnEnter COMPLETED: Calculate confidence score
	sm.RegisterOnEnter(StateCompleted, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s COMPLETED - calculating confidence score", taskID)
		// Confidence score calculation per ADR-012
		return nil
	})

	// OnEnter APPROVED: Trigger merge sequence
	sm.RegisterOnEnter(StateApproved, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s APPROVED - starting merge sequence", taskID)
		// Merge sequence handled by approval system
		return nil
	})

	// OnEnter FAILED: Log failure
	sm.RegisterOnEnter(StateFailed, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s FAILED: %s", taskID, reason)
		return nil
	})

	// OnExit hooks for cleanup
	sm.RegisterOnExit(StateRunning, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s exiting RUNNING state", taskID)
		return nil
	})

	// OnExit APPROVED: Cleanup worktree
	sm.RegisterOnExit(StateApproved, func(taskID string, from, to TaskState, reason string) error {
		log.Printf("[StateMachine] Task %s exiting APPROVED - cleanup worktree", taskID)
		// Worktree cleanup handled here or by post-merge hook
		return nil
	})

	// OnExit FAILED: Cleanup worktree (when terminal)
	sm.RegisterOnExit(StateFailed, func(taskID string, from, to TaskState, reason string) error {
		// Only cleanup if not retrying (not going to QUEUED)
		if to != StateQueued {
			log.Printf("[StateMachine] Task %s FAILED terminal - cleanup worktree", taskID)
		}
		return nil
	})
}

// RegisterOnEnter registers a hook to be called when entering a state
func (sm *StateMachine) RegisterOnEnter(state TaskState, hook StateHook) {
	sm.onEnter[state] = append(sm.onEnter[state], hook)
}

// RegisterOnExit registers a hook to be called when exiting a state
func (sm *StateMachine) RegisterOnExit(state TaskState, hook StateHook) {
	sm.onExit[state] = append(sm.onExit[state], hook)
}

// RegisterOnTransition registers a hook for a specific transition
func (sm *StateMachine) RegisterOnTransition(from, to TaskState, hook StateHook) {
	key := fmt.Sprintf("%s->%s", from, to)
	sm.onTransit[key] = append(sm.onTransit[key], hook)
}

// IsValidTransition checks if a transition is allowed
func (sm *StateMachine) IsValidTransition(from, to TaskState) bool {
	// Allow self-transitions for heartbeats (RUNNING->RUNNING)
	if from == to && from == StateRunning {
		return true
	}

	allowed, exists := ValidTransitions[from]
	if !exists {
		return false
	}

	for _, state := range allowed {
		if state == to {
			return true
		}
	}
	return false
}

// ErrInvalidTransition is returned when a state transition is not allowed
var ErrInvalidTransition = fmt.Errorf("invalid state transition")

// Transition performs a state transition with validation and hooks
// It uses an atomic SQLite transaction for consistency
func (sm *StateMachine) Transition(taskID string, from, to TaskState, reason string) error {
	// Validate transition
	if !sm.IsValidTransition(from, to) {
		return fmt.Errorf("%w: cannot transition from %s to %s", ErrInvalidTransition, from, to)
	}

	// Start transaction
	tx, err := sm.db.Begin()
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Verify current state matches expected
	var currentState TaskState
	err = tx.QueryRow("SELECT state FROM tasks WHERE id = ?", taskID).Scan(&currentState)
	if err == sql.ErrNoRows {
		return fmt.Errorf("task not found: %s", taskID)
	}
	if err != nil {
		return fmt.Errorf("query task state: %w", err)
	}

	if currentState != from {
		return fmt.Errorf("%w: expected state %s but found %s", ErrInvalidTransition, from, currentState)
	}

	// Execute OnExit hooks for the from state
	if hooks, ok := sm.onExit[from]; ok {
		for _, hook := range hooks {
			if err := hook(taskID, from, to, reason); err != nil {
				log.Printf("[StateMachine] OnExit hook error for %s: %v", from, err)
				// Continue despite hook error - don't block transition
			}
		}
	}

	// Execute OnTransition hooks
	transitionKey := fmt.Sprintf("%s->%s", from, to)
	if hooks, ok := sm.onTransit[transitionKey]; ok {
		for _, hook := range hooks {
			if err := hook(taskID, from, to, reason); err != nil {
				log.Printf("[StateMachine] OnTransition hook error for %s: %v", transitionKey, err)
				// Continue despite hook error
			}
		}
	}

	// Update task state
	_, err = tx.Exec(
		"UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
		to, time.Now().Format(time.RFC3339), taskID,
	)
	if err != nil {
		return fmt.Errorf("update task state: %w", err)
	}

	// Log transition (inline to use same transaction)
	transitionID := uuid.New().String()
	_, err = tx.Exec(
		`INSERT INTO task_state_transitions (id, task_id, from_state, to_state, reason, transitioned_at)
		 VALUES (?, ?, ?, ?, ?, ?)`,
		transitionID, taskID, from, to, reason, time.Now().Format(time.RFC3339),
	)
	if err != nil {
		return fmt.Errorf("insert transition log: %w", err)
	}

	// Commit transaction
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit transaction: %w", err)
	}

	// Execute OnEnter hooks for the to state (after commit for side effects)
	if hooks, ok := sm.onEnter[to]; ok {
		for _, hook := range hooks {
			if err := hook(taskID, from, to, reason); err != nil {
				log.Printf("[StateMachine] OnEnter hook error for %s: %v", to, err)
				// Log but don't fail - state is already committed
			}
		}
	}

	action := TransitionActions[transitionKey]
	if action == "" {
		action = "state transition"
	}
	log.Printf("[StateMachine] Task %s: %s -> %s (%s)", taskID, from, to, action)

	return nil
}

// ClaimTransition atomically transitions a task from QUEUED to DISPATCHED
// while also updating the legacy status, assigned_to, and started_at fields in
// the same SQL transaction.  This replaces the two-step pattern of calling
// taskQueue.ClaimTask() (raw SQL) followed by stateMachine.Transition().
//
// Returns ErrInvalidTransition (wrapped) if the task is not in QUEUED state,
// which maps to HTTP 409 in the caller.
func (sm *StateMachine) ClaimTransition(taskID, agentID string) error {
	from := StateQueued
	to := StateDispatched
	reason := "claimed by agent " + agentID

	// Validate transition rule first (no DB round-trip needed — rule is static).
	if !sm.IsValidTransition(from, to) {
		return fmt.Errorf("%w: cannot transition from %s to %s", ErrInvalidTransition, from, to)
	}

	tx, err := sm.db.Begin()
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Verify current state AND that the task is not already claimed.
	// We check both state and assigned_to so we surface a clear 409 on races.
	var currentState TaskState
	var assignedTo sql.NullString
	err = tx.QueryRow(
		"SELECT state, assigned_to FROM tasks WHERE id = ?", taskID,
	).Scan(&currentState, &assignedTo)
	if err == sql.ErrNoRows {
		return fmt.Errorf("task not found: %s", taskID)
	}
	if err != nil {
		return fmt.Errorf("query task state: %w", err)
	}

	if currentState != from {
		return fmt.Errorf("%w: expected state %s but found %s", ErrInvalidTransition, from, currentState)
	}
	if assignedTo.Valid && assignedTo.String != "" && assignedTo.String != agentID {
		return fmt.Errorf("%w: task already assigned to %s", ErrInvalidTransition, assignedTo.String)
	}

	// Execute OnExit hooks for QUEUED state.
	if hooks, ok := sm.onExit[from]; ok {
		for _, hook := range hooks {
			if err := hook(taskID, from, to, reason); err != nil {
				log.Printf("[StateMachine] OnExit hook error for %s: %v", from, err)
			}
		}
	}

	// Execute OnTransition hooks for QUEUED->DISPATCHED.
	transitionKey := fmt.Sprintf("%s->%s", from, to)
	if hooks, ok := sm.onTransit[transitionKey]; ok {
		for _, hook := range hooks {
			if err := hook(taskID, from, to, reason); err != nil {
				log.Printf("[StateMachine] OnTransition hook error for %s: %v", transitionKey, err)
			}
		}
	}

	now := time.Now().Format(time.RFC3339)

	// Atomically update FSM state AND legacy status/assigned_to/started_at.
	_, err = tx.Exec(
		`UPDATE tasks SET state = ?, status = 'assigned', assigned_to = ?, started_at = ?, updated_at = ? WHERE id = ?`,
		to, agentID, now, now, taskID,
	)
	if err != nil {
		return fmt.Errorf("update task state: %w", err)
	}

	// Log the FSM transition.
	transitionID := uuid.New().String()
	_, err = tx.Exec(
		`INSERT INTO task_state_transitions (id, task_id, from_state, to_state, reason, transitioned_at)
		 VALUES (?, ?, ?, ?, ?, ?)`,
		transitionID, taskID, from, to, reason, now,
	)
	if err != nil {
		return fmt.Errorf("insert transition log: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit transaction: %w", err)
	}

	// Execute OnEnter hooks after commit (side effects only).
	if hooks, ok := sm.onEnter[to]; ok {
		for _, hook := range hooks {
			if err := hook(taskID, from, to, reason); err != nil {
				log.Printf("[StateMachine] OnEnter hook error for %s: %v", to, err)
			}
		}
	}

	action := TransitionActions[transitionKey]
	if action == "" {
		action = "state transition"
	}
	log.Printf("[StateMachine] Task %s: %s -> %s (%s) agent=%s", taskID, from, to, action, agentID)

	return nil
}

// GetState returns the current state of a task
func (sm *StateMachine) GetState(taskID string) (TaskState, error) {
	var state TaskState
	err := sm.db.QueryRow("SELECT state FROM tasks WHERE id = ?", taskID).Scan(&state)
	if err == sql.ErrNoRows {
		return "", fmt.Errorf("task not found: %s", taskID)
	}
	if err != nil {
		return "", fmt.Errorf("query task state: %w", err)
	}
	return state, nil
}

// GetAllowedTransitions returns the list of states that can be transitioned to from the given state
func (sm *StateMachine) GetAllowedTransitions(from TaskState) []TaskState {
	return ValidTransitions[from]
}

// IsTerminalState returns true if the state is terminal (no outgoing transitions)
func IsTerminalState(state TaskState) bool {
	transitions, exists := ValidTransitions[state]
	return exists && len(transitions) == 0
}

// GetTransitionAction returns a human-readable description of the transition
func GetTransitionAction(from, to TaskState) string {
	key := fmt.Sprintf("%s->%s", from, to)
	if action, ok := TransitionActions[key]; ok {
		return action
	}
	return fmt.Sprintf("Transition from %s to %s", from, to)
}

// StateMachineConfig allows customization of state machine behavior
type StateMachineConfig struct {
	EnableHooks      bool
	HookTimeout      time.Duration
	SkipValidation   bool
	OnInvalidTransit func(taskID string, from, to TaskState, reason string)
}

// DefaultStateMachineConfig returns the default configuration
func DefaultStateMachineConfig() *StateMachineConfig {
	return &StateMachineConfig{
		EnableHooks:    true,
		HookTimeout:    30 * time.Second,
		SkipValidation: false,
	}
}

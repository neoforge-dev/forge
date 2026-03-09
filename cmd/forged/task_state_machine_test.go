//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"fmt"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// setupStateMachineTestDB creates a temporary SQLite database for testing
func setupStateMachineTestDB(t *testing.T) (*sql.DB, func()) {
	tmpFile, err := os.CreateTemp("", "state_machine_test_*.db")
	if err != nil {
		t.Fatalf("Failed to create temp file: %v", err)
	}
	tmpFile.Close()

	db, err := sql.Open("sqlite3", tmpFile.Name()+"?_journal_mode=WAL")
	if err != nil {
		os.Remove(tmpFile.Name())
		t.Fatalf("Failed to open database: %v", err)
	}

	// Create tasks table
	_, err = db.Exec(`
		CREATE TABLE tasks (
			id TEXT PRIMARY KEY,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			type TEXT NOT NULL,
			priority INTEGER DEFAULT 0,
			status TEXT DEFAULT 'queued',
			state TEXT DEFAULT 'QUEUED',
			lane TEXT,
			assigned_to TEXT,
			plan_version INTEGER DEFAULT 0,
			plan_id TEXT,
			envelope_id TEXT,
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		)
	`)
	if err != nil {
		db.Close()
		os.Remove(tmpFile.Name())
		t.Fatalf("Failed to create tasks table: %v", err)
	}

	// Create task_state_transitions table
	_, err = db.Exec(`
		CREATE TABLE task_state_transitions (
			id TEXT PRIMARY KEY,
			task_id TEXT NOT NULL,
			from_state TEXT NOT NULL,
			to_state TEXT NOT NULL,
			reason TEXT,
			transitioned_at TEXT NOT NULL,
			FOREIGN KEY (task_id) REFERENCES tasks(id)
		)
	`)
	if err != nil {
		db.Close()
		os.Remove(tmpFile.Name())
		t.Fatalf("Failed to create transitions table: %v", err)
	}

	cleanup := func() {
		db.Close()
		os.Remove(tmpFile.Name())
	}

	return db, cleanup
}

// createTestTask creates a test task in the given state
func createTestTask(t *testing.T, db *sql.DB, taskID string, state TaskState) {
	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO tasks (id, domain, project, type, state, created_at, updated_at) 
		 VALUES (?, 'test-domain', 'test-project', 'feature', ?, ?, ?)`,
		taskID, state, now, now,
	)
	if err != nil {
		t.Fatalf("Failed to create test task: %v", err)
	}
}

func TestTaskStateConstants(t *testing.T) {
	tests := []struct {
		state TaskState
		want  string
	}{
		{StateQueued, "QUEUED"},
		{StateDispatched, "DISPATCHED"},
		{StateRunning, "RUNNING"},
		{StateBlocked, "BLOCKED"},
		{StateCompleted, "COMPLETED"},
		{StateApproved, "APPROVED"},
		{StateFailed, "FAILED"},
	}

	for _, tt := range tests {
		t.Run(string(tt.state), func(t *testing.T) {
			if string(tt.state) != tt.want {
				t.Errorf("State = %v, want %v", tt.state, tt.want)
			}
		})
	}
}

func TestIsValidTransition(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	tests := []struct {
		from    TaskState
		to      TaskState
		allowed bool
	}{
		// Valid transitions from QUEUED
		{StateQueued, StateDispatched, true},
		{StateQueued, StateRunning, false},
		{StateQueued, StateFailed, false},

		// Valid transitions from DISPATCHED
		{StateDispatched, StateRunning, true},
		{StateDispatched, StateFailed, true},
		{StateDispatched, StateQueued, false},
		{StateDispatched, StateCompleted, false},

		// Valid transitions from RUNNING
		{StateRunning, StateRunning, true}, // Heartbeat
		{StateRunning, StateBlocked, true},
		{StateRunning, StateCompleted, true},
		{StateRunning, StateFailed, true},
		{StateRunning, StateQueued, false},
		{StateRunning, StateApproved, false},

		// Valid transitions from BLOCKED
		{StateBlocked, StateRunning, true},
		{StateBlocked, StateCompleted, false},
		{StateBlocked, StateFailed, false},

		// Valid transitions from COMPLETED
		{StateCompleted, StateApproved, true},
		{StateCompleted, StateFailed, true},
		{StateCompleted, StateRunning, false},

		// Transitions from APPROVED (terminal)
		{StateApproved, StateQueued, false},
		{StateApproved, StateFailed, false},
		{StateApproved, StateRunning, false},

		// Valid transitions from FAILED
		{StateFailed, StateQueued, true}, // Retry
		{StateFailed, StateRunning, false},
		{StateFailed, StateCompleted, false},
	}

	for _, tt := range tests {
		t.Run(fmt.Sprintf("%s->%s", tt.from, tt.to), func(t *testing.T) {
			got := sm.IsValidTransition(tt.from, tt.to)
			if got != tt.allowed {
				t.Errorf("IsValidTransition(%s, %s) = %v, want %v", tt.from, tt.to, got, tt.allowed)
			}
		})
	}
}

func TestTransition_ValidTransitions(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	tests := []struct {
		name   string
		from   TaskState
		to     TaskState
		reason string
	}{
		{"QUEUED to DISPATCHED", StateQueued, StateDispatched, "Lease acquired"},
		{"DISPATCHED to RUNNING", StateDispatched, StateRunning, "Agent started"},
		{"DISPATCHED to FAILED", StateDispatched, StateFailed, "Failed to start"},
		{"RUNNING heartbeat", StateRunning, StateRunning, "Heartbeat"},
		{"RUNNING to BLOCKED", StateRunning, StateBlocked, "Needs attention"},
		{"RUNNING to COMPLETED", StateRunning, StateCompleted, "Success"},
		{"RUNNING to FAILED", StateRunning, StateFailed, "Execution error"},
		{"BLOCKED to RUNNING", StateBlocked, StateRunning, "Resolved"},
		{"COMPLETED to APPROVED", StateCompleted, StateApproved, "Approved"},
		{"COMPLETED to FAILED", StateCompleted, StateFailed, "Rejected"},
		{"FAILED to QUEUED (retry)", StateFailed, StateQueued, "Retry"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			taskID := fmt.Sprintf("task-%s", tt.name)
			createTestTask(t, db, taskID, tt.from)

			err := sm.Transition(taskID, tt.from, tt.to, tt.reason)
			if err != nil {
				t.Errorf("Transition() error = %v, want nil", err)
			}

			// Verify state was updated
			currentState, err := sm.GetState(taskID)
			if err != nil {
				t.Errorf("GetState() error = %v", err)
			}
			if currentState != tt.to {
				t.Errorf("State = %v, want %v", currentState, tt.to)
			}

			// Verify transition was logged
			history, err := store.GetTransitionHistory(taskID)
			if err != nil {
				t.Errorf("GetTransitionHistory() error = %v", err)
			}
			if len(history) != 1 {
				t.Errorf("Expected 1 transition, got %d", len(history))
			}
			if len(history) > 0 {
				if history[0].FromState != tt.from || history[0].ToState != tt.to {
					t.Errorf("Transition log mismatch: got %s->%s, want %s->%s",
						history[0].FromState, history[0].ToState, tt.from, tt.to)
				}
			}
		})
	}
}

func TestTransition_InvalidTransitions(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	tests := []struct {
		name   string
		from   TaskState
		to     TaskState
		reason string
	}{
		{"QUEUED cannot go to RUNNING", StateQueued, StateRunning, "Invalid"},
		{"QUEUED cannot go to COMPLETED", StateQueued, StateCompleted, "Invalid"},
		{"APPROVED is terminal", StateApproved, StateQueued, "Invalid"},
		{"APPROVED cannot go to FAILED", StateApproved, StateFailed, "Invalid"},
		{"COMPLETED cannot go to RUNNING", StateCompleted, StateRunning, "Invalid"},
		{"BLOCKED cannot go to COMPLETED", StateBlocked, StateCompleted, "Invalid"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			taskID := fmt.Sprintf("task-%s", tt.name)
			createTestTask(t, db, taskID, tt.from)

			err := sm.Transition(taskID, tt.from, tt.to, tt.reason)
			if err == nil {
				t.Errorf("Transition() expected error for invalid transition %s->%s", tt.from, tt.to)
			}

			if err != nil && err.Error() != fmt.Sprintf("invalid state transition: cannot transition from %s to %s", tt.from, tt.to) {
				// It's an invalid transition error, which is what we expect
				if err.Error()[:24] != "invalid state transition" {
					t.Errorf("Expected invalid transition error, got: %v", err)
				}
			}
		})
	}
}

func TestTransition_WrongCurrentState(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	taskID := "task-wrong-state"
	createTestTask(t, db, taskID, StateRunning) // Actually in RUNNING

	// Try to transition from QUEUED (wrong expected state)
	err := sm.Transition(taskID, StateQueued, StateDispatched, "Lease acquired")
	if err == nil {
		t.Error("Transition() expected error for wrong current state")
	}

	if err != nil && len(err.Error()) >= 24 && err.Error()[:24] == "invalid state transition" {
		// This is expected
	} else if err != nil {
		t.Errorf("Expected state mismatch error, got: %v", err)
	}
}

func TestTransition_TaskNotFound(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	err := sm.Transition("non-existent-task", StateQueued, StateDispatched, "Test")
	if err == nil {
		t.Error("Transition() expected error for non-existent task")
	}
}

func TestHooks(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	var enterCalled, exitCalled, transitCalled bool
	var capturedTaskID string
	var capturedFrom, capturedTo TaskState

	// Register test hooks
	sm.RegisterOnEnter(StateRunning, func(taskID string, from, to TaskState, reason string) error {
		enterCalled = true
		capturedTaskID = taskID
		capturedFrom = from
		capturedTo = to
		return nil
	})

	sm.RegisterOnExit(StateQueued, func(taskID string, from, to TaskState, reason string) error {
		exitCalled = true
		return nil
	})

	sm.RegisterOnTransition(StateQueued, StateDispatched, func(taskID string, from, to TaskState, reason string) error {
		transitCalled = true
		return nil
	})

	taskID := "task-hooks"
	createTestTask(t, db, taskID, StateQueued)

	err := sm.Transition(taskID, StateQueued, StateDispatched, "Test")
	if err != nil {
		t.Errorf("Transition() error = %v", err)
	}

	if !exitCalled {
		t.Error("OnExit hook for QUEUED was not called")
	}
	if !transitCalled {
		t.Error("OnTransition hook for QUEUED->DISPATCHED was not called")
	}

	// Now transition to RUNNING to test OnEnter
	err = sm.Transition(taskID, StateDispatched, StateRunning, "Started")
	if err != nil {
		t.Errorf("Transition() error = %v", err)
	}

	if !enterCalled {
		t.Error("OnEnter hook for RUNNING was not called")
	}
	if capturedTaskID != taskID {
		t.Errorf("Hook captured wrong taskID: got %s, want %s", capturedTaskID, taskID)
	}
	if capturedFrom != StateDispatched || capturedTo != StateRunning {
		t.Errorf("Hook captured wrong states: got %s->%s, want %s->%s",
			capturedFrom, capturedTo, StateDispatched, StateRunning)
	}
}

func TestIsTerminalState(t *testing.T) {
	tests := []struct {
		state    TaskState
		terminal bool
	}{
		{StateQueued, false},
		{StateDispatched, false},
		{StateRunning, false},
		{StateBlocked, false},
		{StateCompleted, false}, // Can go to APPROVED or FAILED
		{StateApproved, true},   // Terminal - no outgoing transitions
		{StateFailed, false},    // Can go to QUEUED for retry
	}

	for _, tt := range tests {
		t.Run(string(tt.state), func(t *testing.T) {
			got := IsTerminalState(tt.state)
			if got != tt.terminal {
				t.Errorf("IsTerminalState(%s) = %v, want %v", tt.state, got, tt.terminal)
			}
		})
	}
}

func TestGetTransitionAction(t *testing.T) {
	tests := []struct {
		from string
		to   string
		want string
	}{
		{string(StateQueued), string(StateDispatched), "Lease acquired, worktree created"},
		{string(StateRunning), string(StateRunning), "Heartbeat received"},
		{string(StateRunning), string(StateCompleted), "Task completed successfully"},
		{string(StateRunning), string(StateFailed), "Task execution failed"},
		{string(StateCompleted), string(StateApproved), "Changes approved, merging"},
		{"UNKNOWN", "STATE", "Transition from UNKNOWN to STATE"},
	}

	for _, tt := range tests {
		t.Run(fmt.Sprintf("%s->%s", tt.from, tt.to), func(t *testing.T) {
			got := GetTransitionAction(TaskState(tt.from), TaskState(tt.to))
			if got != tt.want {
				t.Errorf("GetTransitionAction() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestGetAllowedTransitions(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	tests := []struct {
		from     TaskState
		expected []TaskState
	}{
		{StateQueued, []TaskState{StateDispatched}},
		{StateDispatched, []TaskState{StateRunning, StateFailed}},
		{StateRunning, []TaskState{StateRunning, StateBlocked, StateCompleted, StateFailed}},
		{StateBlocked, []TaskState{StateRunning}},
		{StateCompleted, []TaskState{StateApproved, StateFailed}},
		{StateApproved, []TaskState{}},
		{StateFailed, []TaskState{StateQueued}},
	}

	for _, tt := range tests {
		t.Run(string(tt.from), func(t *testing.T) {
			got := sm.GetAllowedTransitions(tt.from)
			if len(got) != len(tt.expected) {
				t.Errorf("GetAllowedTransitions(%s) returned %d states, want %d",
					tt.from, len(got), len(tt.expected))
			}
			for i, state := range tt.expected {
				if i >= len(got) || got[i] != state {
					t.Errorf("GetAllowedTransitions(%s)[%d] = %v, want %v",
						tt.from, i, got[i], state)
				}
			}
		})
	}
}

func TestStateMachine_TransitionSequence(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	taskID := "task-sequence"
	createTestTask(t, db, taskID, StateQueued)

	// Full lifecycle: QUEUED -> DISPATCHED -> RUNNING -> COMPLETED -> APPROVED
	sequence := []struct {
		from   TaskState
		to     TaskState
		reason string
	}{
		{StateQueued, StateDispatched, "Lease acquired"},
		{StateDispatched, StateRunning, "Agent started"},
		{StateRunning, StateRunning, "Heartbeat 1"},
		{StateRunning, StateRunning, "Heartbeat 2"},
		{StateRunning, StateBlocked, "Needs input"},
		{StateBlocked, StateRunning, "Input received"},
		{StateRunning, StateCompleted, "Task done"},
		{StateCompleted, StateApproved, "Approved by human"},
	}

	for _, step := range sequence {
		err := sm.Transition(taskID, step.from, step.to, step.reason)
		if err != nil {
			t.Errorf("Transition %s->%s failed: %v", step.from, step.to, err)
		}
	}

	// Verify final state
	finalState, err := sm.GetState(taskID)
	if err != nil {
		t.Errorf("GetState() error = %v", err)
	}
	if finalState != StateApproved {
		t.Errorf("Final state = %v, want %v", finalState, StateApproved)
	}

	// Verify all transitions logged
	history, err := store.GetTransitionHistory(taskID)
	if err != nil {
		t.Errorf("GetTransitionHistory() error = %v", err)
	}
	if len(history) != len(sequence) {
		t.Errorf("Expected %d transitions, got %d", len(sequence), len(history))
	}
}

func TestStateMachine_RetryFlow(t *testing.T) {
	db, cleanup := setupStateMachineTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	taskID := "task-retry"
	createTestTask(t, db, taskID, StateQueued)

	// First attempt fails
	err := sm.Transition(taskID, StateQueued, StateDispatched, "Attempt 1")
	if err != nil {
		t.Fatalf("First dispatch failed: %v", err)
	}

	err = sm.Transition(taskID, StateDispatched, StateRunning, "Running")
	if err != nil {
		t.Fatalf("First run failed: %v", err)
	}

	err = sm.Transition(taskID, StateRunning, StateFailed, "Error occurred")
	if err != nil {
		t.Fatalf("First failure failed: %v", err)
	}

	// Retry
	err = sm.Transition(taskID, StateFailed, StateQueued, "Retry scheduled")
	if err != nil {
		t.Fatalf("Retry failed: %v", err)
	}

	// Second attempt succeeds
	err = sm.Transition(taskID, StateQueued, StateDispatched, "Attempt 2")
	if err != nil {
		t.Fatalf("Second dispatch failed: %v", err)
	}

	err = sm.Transition(taskID, StateDispatched, StateRunning, "Running again")
	if err != nil {
		t.Fatalf("Second run failed: %v", err)
	}

	err = sm.Transition(taskID, StateRunning, StateCompleted, "Success!")
	if err != nil {
		t.Fatalf("Completion failed: %v", err)
	}

	// Verify final state
	finalState, err := sm.GetState(taskID)
	if err != nil {
		t.Errorf("GetState() error = %v", err)
	}
	if finalState != StateCompleted {
		t.Errorf("Final state = %v, want %v", finalState, StateCompleted)
	}

	// Should have 7 transitions
	history, err := store.GetTransitionHistory(taskID)
	if err != nil {
		t.Errorf("GetTransitionHistory() error = %v", err)
	}
	if len(history) != 7 {
		t.Errorf("Expected 7 transitions, got %d", len(history))
	}
}

func TestTaskStateMachine(t *testing.T) {
	// This is the main test runner that can be invoked with `go test -run TestTaskStateMachine`
	// It runs all the sub-tests
	t.Run("Constants", TestTaskStateConstants)
	t.Run("ValidTransitions", TestIsValidTransition)
	t.Run("TransitionSuccess", TestTransition_ValidTransitions)
	t.Run("TransitionFailure", TestTransition_InvalidTransitions)
	t.Run("WrongState", TestTransition_WrongCurrentState)
	t.Run("NotFound", TestTransition_TaskNotFound)
	t.Run("Hooks", TestHooks)
	t.Run("TerminalStates", TestIsTerminalState)
	t.Run("Actions", TestGetTransitionAction)
	t.Run("AllowedTransitions", TestGetAllowedTransitions)
	t.Run("Sequence", TestStateMachine_TransitionSequence)
	t.Run("Retry", TestStateMachine_RetryFlow)
}

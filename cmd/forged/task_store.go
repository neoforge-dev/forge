//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// StateTransition represents a log entry for a task state change
type StateTransition struct {
	ID             string    `json:"id"`
	TaskID         string    `json:"task_id"`
	FromState      TaskState `json:"from_state"`
	ToState        TaskState `json:"to_state"`
	Reason         string    `json:"reason"`
	TransitionedAt time.Time `json:"transitioned_at"`
}

// TaskStore manages task persistence and state transitions
type TaskStore struct {
	db *sql.DB
}

// NewTaskStore creates a new TaskStore
func NewTaskStore(db *sql.DB) *TaskStore {
	return &TaskStore{db: db}
}

// GetTasksByState returns all tasks in a given state
func (s *TaskStore) GetTasksByState(state TaskState) ([]Task, error) {
	rows, err := s.db.Query(`
		SELECT id, domain, project, type, priority, status, state, lane, assigned_to, 
		       plan_version, plan_id, envelope_id, created_at, updated_at 
		FROM tasks 
		WHERE state = ?`, state)
	if err != nil {
		return nil, fmt.Errorf("query tasks by state: %w", err)
	}
	defer rows.Close()

	var tasks []Task
	for rows.Next() {
		var t Task
		var createdAt, updatedAt string
		var lane, assignedTo, planID, envelopeID sql.NullString
		var planVersion sql.NullInt64
		err := rows.Scan(
			&t.ID, &t.Domain, &t.Project, &t.Type, &t.Priority, &t.Status, &t.State, &lane, &assignedTo,
			&planVersion, &planID, &envelopeID, &createdAt, &updatedAt,
		)
		if err != nil {
			return nil, fmt.Errorf("scan task: %w", err)
		}
		t.PlanVersion = int(planVersion.Int64)
		t.Lane = lane.String
		t.AssignedTo = assignedTo.String
		t.PlanID = planID.String
		t.EnvelopeID = envelopeID.String
		t.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
		t.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt)
		tasks = append(tasks, t)
	}
	return tasks, nil
}

// RecordTransition logs a state transition and updates the task state
func (s *TaskStore) RecordTransition(taskID string, from, to TaskState, reason string) error {
	tx, err := s.db.Begin()
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Update task state
	_, err = tx.Exec("UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?", 
		to, time.Now().Format(time.RFC3339), taskID)
	if err != nil {
		return fmt.Errorf("update task state: %w", err)
	}

	// Log transition
	transitionID := uuid.New().String()
	_, err = tx.Exec(`
		INSERT INTO task_state_transitions (id, task_id, from_state, to_state, reason, transitioned_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		transitionID, taskID, from, to, reason, time.Now().Format(time.RFC3339))
	if err != nil {
		return fmt.Errorf("insert transition log: %w", err)
	}

	return tx.Commit()
}

// ClaimTask assigns a task to an agent and transitions its state to DISPATCHED
// per ADR-028.  All writes (state, status, assigned_to, started_at, transition
// log) happen in a single atomic transaction so there is no window where state
// and status are inconsistent.
//
// When the global stateMachine is available its enter/exit hooks are fired
// after the commit (same behaviour as StateMachine.Transition).  When
// stateMachine is nil (CLI standalone mode) the transition is applied directly
// so the binary works without a running daemon.
func (s *TaskStore) ClaimTask(taskID, agentID string) error {
	if stateMachine != nil {
		// Delegate entirely to the FSM — one atomic transaction, hooks fire.
		return stateMachine.ClaimTransition(taskID, agentID)
	}

	// Standalone (no daemon / stateMachine is nil): single atomic transaction
	// with QUEUED-state guard to prevent races.
	tx, err := s.db.Begin()
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback()

	now := time.Now().Format(time.RFC3339)

	res, err := tx.Exec(
		`UPDATE tasks SET state = ?, assigned_to = ?, started_at = ?, updated_at = ? WHERE id = ? AND state = ?`,
		StateDispatched, agentID, now, now, taskID, StateQueued)
	if err != nil {
		return fmt.Errorf("update task: %w", err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("task %s not claimable (not in QUEUED state or does not exist)", taskID)
	}

	// Log transition in the same transaction.
	transitionID := uuid.New().String()
	_, err = tx.Exec(`
		INSERT INTO task_state_transitions (id, task_id, from_state, to_state, reason, transitioned_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		transitionID, taskID, StateQueued, StateDispatched,
		fmt.Sprintf("claimed by agent %s", agentID), now)
	if err != nil {
		return fmt.Errorf("insert transition log: %w", err)
	}

	return tx.Commit()
}

// GetTransitionHistory returns all transitions for a task
func (s *TaskStore) GetTransitionHistory(taskID string) ([]StateTransition, error) {
	rows, err := s.db.Query(`
		SELECT id, task_id, from_state, to_state, reason, transitioned_at 
		FROM task_state_transitions 
		WHERE task_id = ? 
		ORDER BY transitioned_at ASC`, taskID)
	if err != nil {
		return nil, fmt.Errorf("query transition history: %w", err)
	}
	defer rows.Close()

	var history []StateTransition
	for rows.Next() {
		var st StateTransition
		var transitionedAt string
		err := rows.Scan(&st.ID, &st.TaskID, &st.FromState, &st.ToState, &st.Reason, &transitionedAt)
		if err != nil {
			return nil, fmt.Errorf("scan transition: %w", err)
		}
		st.TransitionedAt, _ = time.Parse(time.RFC3339, transitionedAt)
		history = append(history, st)
	}
	return history, nil
}

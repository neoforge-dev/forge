//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/oklog/ulid/v2"
)

type Lease struct {
	ID        string
	TaskID    string
	AgentID   string
	ExpiresAt time.Time
}

var (
	ErrLeaseNotFound   = errors.New("lease not found")
	ErrLeaseConflict   = errors.New("task already leased by another agent")
	ErrStateTransition = errors.New("state transition failed")
)

// TaskStateMachine defines the interface for task state transitions
type TaskStateMachine interface {
	Transition(taskID string, from, to TaskState, reason string) error
	GetTaskState(taskID string) (TaskState, error)
}

// TaskStateMachineImpl wraps TaskStore to provide state machine functionality
type TaskStateMachineImpl struct {
	store *TaskStore
}

func NewTaskStateMachine(db *sql.DB) TaskStateMachine {
	return &TaskStateMachineImpl{
		store: NewTaskStore(db),
	}
}

func (sm *TaskStateMachineImpl) Transition(taskID string, from, to TaskState, reason string) error {
	return sm.store.RecordTransition(taskID, from, to, reason)
}

func (sm *TaskStateMachineImpl) GetTaskState(taskID string) (TaskState, error) {
	tasks, err := sm.store.GetTasksByState(StateQueued)
	if err != nil {
		return "", err
	}
	for _, t := range tasks {
		if t.ID == taskID {
			return t.State, nil
		}
	}

	// Check other states
	allTasks, err := sm.store.db.Query("SELECT id, state FROM tasks WHERE id = ?", taskID)
	if err != nil {
		return "", err
	}
	defer allTasks.Close()

	if allTasks.Next() {
		var id string
		var state TaskState
		if err := allTasks.Scan(&id, &state); err != nil {
			return "", err
		}
		return state, nil
	}

	return "", errors.New("task not found")
}

type LeaseManager interface {
	Claim(ctx context.Context, taskID, agentID string, ttl time.Duration) (*Lease, error)
	Renew(ctx context.Context, leaseID string, ttl time.Duration) error
	Release(ctx context.Context, leaseID string) error
	Recover(ctx context.Context) ([]Lease, error)
	RecordHeartbeat(ctx context.Context, leaseID string) error
	GetTaskForLease(ctx context.Context, leaseID string) (*Task, error)
	Close() error
}

type sqliteLeaseManager struct {
	db           *sql.DB
	stateMachine TaskStateMachine
}

func NewLeaseManager(dbPath string) (LeaseManager, error) {
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)

	return &sqliteLeaseManager{
		db:           db,
		stateMachine: NewTaskStateMachine(db),
	}, nil
}

func NewLeaseManagerWithStateMachine(dbPath string, sm TaskStateMachine) (LeaseManager, error) {
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)

	return &sqliteLeaseManager{
		db:           db,
		stateMachine: sm,
	}, nil
}

func (m *sqliteLeaseManager) Claim(ctx context.Context, taskID, agentID string, ttl time.Duration) (*Lease, error) {
	expiresAt := time.Now().UTC().Add(ttl)
	leaseID := ulid.Make().String()

	tx, err := m.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()

	var existingAgentID string
	err = tx.QueryRowContext(ctx,
		"SELECT agent_id FROM leases WHERE task_id = ? AND expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
		taskID,
	).Scan(&existingAgentID)

	if err == nil {
		return nil, ErrLeaseConflict
	}
	if err != sql.ErrNoRows {
		return nil, err
	}

	_, err = tx.ExecContext(ctx,
		"INSERT INTO leases (id, task_id, agent_id, expires_at) VALUES (?, ?, ?, ?)",
		leaseID, taskID, agentID, expiresAt.Format(time.RFC3339),
	)
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, err
	}

	// Transition task state to DISPATCHED
	if m.stateMachine != nil {
		err = m.stateMachine.Transition(taskID, StateQueued, StateDispatched,
			fmt.Sprintf("Lease acquired by agent %s", agentID))
		if err != nil {
			// Rollback lease on state transition failure
			log.Printf("[Lease] Warning: Failed to transition task %s to DISPATCHED: %v", taskID, err)
			_, rollbackErr := m.db.Exec("DELETE FROM leases WHERE id = ?", leaseID)
			if rollbackErr != nil {
				log.Printf("[Lease] Error: Failed to rollback lease %s: %v", leaseID, rollbackErr)
			}
			return nil, fmt.Errorf("%w: %v", ErrStateTransition, err)
		}
		log.Printf("[Lease] Task %s transitioned to DISPATCHED", taskID)
	}

	return &Lease{
		ID:        leaseID,
		TaskID:    taskID,
		AgentID:   agentID,
		ExpiresAt: expiresAt,
	}, nil
}

func (m *sqliteLeaseManager) Renew(ctx context.Context, leaseID string, ttl time.Duration) error {
	expiresAt := time.Now().UTC().Add(ttl)

	result, err := m.db.ExecContext(ctx,
		"UPDATE leases SET expires_at = ? WHERE id = ? AND expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
		expiresAt.Format(time.RFC3339), leaseID,
	)
	if err != nil {
		return err
	}

	rows, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if rows == 0 {
		return ErrLeaseNotFound
	}

	return nil
}

func (m *sqliteLeaseManager) Release(ctx context.Context, leaseID string) error {
	// Get task ID before deleting lease
	var taskID string
	err := m.db.QueryRowContext(ctx, "SELECT task_id FROM leases WHERE id = ?", leaseID).Scan(&taskID)
	if err == sql.ErrNoRows {
		return ErrLeaseNotFound
	}
	if err != nil {
		return err
	}

	result, err := m.db.ExecContext(ctx, "DELETE FROM leases WHERE id = ?", leaseID)
	if err != nil {
		return err
	}

	rows, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if rows == 0 {
		return ErrLeaseNotFound
	}

	// Transition task state to COMPLETED
	if m.stateMachine != nil && taskID != "" {
		// Get current state first
		currentState, err := m.stateMachine.GetTaskState(taskID)
		if err != nil {
			log.Printf("[Lease] Warning: Failed to get task %s state: %v", taskID, err)
		} else if currentState == StateRunning || currentState == StateDispatched {
			err = m.stateMachine.Transition(taskID, currentState, StateCompleted,
				"Lease released successfully")
			if err != nil {
				log.Printf("[Lease] Warning: Failed to transition task %s to COMPLETED: %v", taskID, err)
			} else {
				log.Printf("[Lease] Task %s transitioned to COMPLETED", taskID)
			}
		}
	}

	return nil
}

func (m *sqliteLeaseManager) Recover(ctx context.Context) ([]Lease, error) {
	// Get expired leases with task IDs
	rows, err := m.db.QueryContext(ctx,
		"SELECT id, task_id, agent_id, expires_at FROM leases WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var recovered []Lease
	var taskIDs []string
	for rows.Next() {
		var l Lease
		var expiresAtStr string
		if err := rows.Scan(&l.ID, &l.TaskID, &l.AgentID, &expiresAtStr); err != nil {
			return nil, err
		}
		recovered = append(recovered, l)
		taskIDs = append(taskIDs, l.TaskID)
	}

	if len(recovered) > 0 {
		_, err = m.db.ExecContext(ctx,
			"DELETE FROM leases WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
		)
		if err != nil {
			return nil, err
		}

		// Transition expired tasks to FAILED
		if m.stateMachine != nil {
			for _, taskID := range taskIDs {
				currentState, err := m.stateMachine.GetTaskState(taskID)
				if err != nil {
					log.Printf("[Lease] Warning: Failed to get task %s state: %v", taskID, err)
					continue
				}

				if currentState == StateDispatched || currentState == StateRunning {
					err = m.stateMachine.Transition(taskID, currentState, StateFailed,
						"Lease expired - task timeout")
					if err != nil {
						log.Printf("[Lease] Warning: Failed to transition task %s to FAILED: %v", taskID, err)
					} else {
						log.Printf("[Lease] Task %s transitioned to FAILED (expired lease)", taskID)
					}
					// Increment retry_count for auto-requeue
					_, _ = m.db.ExecContext(ctx, `
						UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ?
					`, taskID)

					// Auto-requeue retriable tasks (retry_count < 3)
					var retryCount int
					err = m.db.QueryRowContext(ctx, "SELECT retry_count FROM tasks WHERE id = ?", taskID).Scan(&retryCount)
					if err == nil && retryCount < 3 {
						err = m.stateMachine.Transition(taskID, StateFailed, StateQueued,
							fmt.Sprintf("Auto-requeue: attempt %d of 3", retryCount))
						if err != nil {
							log.Printf("[Lease] Warning: Failed to auto-requeue task %s: %v", taskID, err)
						} else {
							log.Printf("[Lease] Task %s auto-requeued (retry %d/3)", taskID, retryCount)
						}
					}
				}
			}
		}
	}

	return recovered, rows.Err()
}

func (m *sqliteLeaseManager) RecordHeartbeat(ctx context.Context, leaseID string) error {
	// Get task ID for this lease
	var taskID string
	err := m.db.QueryRowContext(ctx, "SELECT task_id FROM leases WHERE id = ?", leaseID).Scan(&taskID)
	if err == sql.ErrNoRows {
		return ErrLeaseNotFound
	}
	if err != nil {
		return err
	}

	// Transition to RUNNING if still DISPATCHED
	if m.stateMachine != nil && taskID != "" {
		currentState, err := m.stateMachine.GetTaskState(taskID)
		if err != nil {
			return fmt.Errorf("failed to get task state: %w", err)
		}

		if currentState == StateDispatched {
			err = m.stateMachine.Transition(taskID, StateDispatched, StateRunning,
				"Agent heartbeat received")
			if err != nil {
				return fmt.Errorf("failed to transition to RUNNING: %w", err)
			}
			log.Printf("[Lease] Task %s transitioned from DISPATCHED to RUNNING", taskID)
		}
	}

	return nil
}

func (m *sqliteLeaseManager) GetTaskForLease(ctx context.Context, leaseID string) (*Task, error) {
	var taskID string
	err := m.db.QueryRowContext(ctx, "SELECT task_id FROM leases WHERE id = ?", leaseID).Scan(&taskID)
	if err == sql.ErrNoRows {
		return nil, ErrLeaseNotFound
	}
	if err != nil {
		return nil, err
	}

	// Query task details
	row := m.db.QueryRowContext(ctx, `
		SELECT id, domain, project, type, priority, status, state, lane, assigned_to, 
		       plan_version, plan_id, envelope_id, created_at, updated_at 
		FROM tasks 
		WHERE id = ?`, taskID)

	var t Task
	var createdAt, updatedAt string
	var lane, assignedTo, planID, envelopeID sql.NullString
	var planVersion sql.NullInt64
	err = row.Scan(
		&t.ID, &t.Domain, &t.Project, &t.Type, &t.Priority, &t.Status, &t.State, &lane, &assignedTo,
		&planVersion, &planID, &envelopeID, &createdAt, &updatedAt,
	)
	if err != nil {
		return nil, err
	}
	t.PlanVersion = int(planVersion.Int64)
	t.Lane = lane.String
	t.AssignedTo = assignedTo.String
	t.PlanID = planID.String
	t.EnvelopeID = envelopeID.String
	t.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
	t.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt)

	return &t, nil
}

func (m *sqliteLeaseManager) Close() error {
	return m.db.Close()
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

type TaskType string

const (
	TaskTypeFeature  TaskType = "feature"
	TaskTypeBugfix   TaskType = "bugfix"
	TaskTypeResearch TaskType = "research"
	TaskTypeRefactor TaskType = "refactor"
)

type TaskStatus string

const (
	TaskStatusRequested TaskStatus = "requested"
	TaskStatusPlanned   TaskStatus = "planned"
	TaskStatusQueued    TaskStatus = "queued"
	TaskStatusAssigned  TaskStatus = "assigned"
	TaskStatusExecuting TaskStatus = "executing"
	TaskStatusPaused    TaskStatus = "paused"
	TaskStatusCompleted TaskStatus = "completed"
	TaskStatusFailed    TaskStatus = "failed"
)

type TaskState string

const (
	StateQueued     TaskState = "QUEUED"
	StateDispatched TaskState = "DISPATCHED"
	StateRunning    TaskState = "RUNNING"
	StateBlocked    TaskState = "BLOCKED"
	StateCompleted  TaskState = "COMPLETED"
	StateApproved   TaskState = "APPROVED"
	StateFailed     TaskState = "FAILED"
)

type Task struct {
	ID           string     `json:"id"`
	Title        string     `json:"title,omitempty"`
	Description  string     `json:"description,omitempty"`
	Domain       string     `json:"domain"`
	Project      string     `json:"project"`
	Type         TaskType   `json:"type"`
	Priority     int        `json:"priority"`
	Status       TaskStatus `json:"status"`
	State        TaskState  `json:"state"`
	Lane         string     `json:"lane,omitempty"`
	AssignedTo   string     `json:"assigned_to,omitempty"`
	PlanVersion  int        `json:"plan_version,omitempty"`
	PlanID       string     `json:"plan_id,omitempty"`
	EnvelopeID   string     `json:"envelope_id,omitempty"`
	RequiredTier string     `json:"required_tier,omitempty"`
	Dependencies []string   `json:"dependencies,omitempty"`
	Result       string     `json:"result,omitempty"`
	Error        string     `json:"error,omitempty"`
	CreatedAt    time.Time  `json:"created_at"`
	StartedAt    *time.Time `json:"started_at,omitempty"`
	UpdatedAt    time.Time  `json:"updated_at"`
}

// TaskEvent represents an event in the event sourcing log
type TaskEvent struct {
	ID        int64           `json:"id"`
	TaskID    string          `json:"task_id"`
	Domain    string          `json:"domain"`
	Project   string          `json:"project"`
	EventType string          `json:"event_type"`
	Payload   json.RawMessage `json:"payload"`
	CreatedAt time.Time       `json:"created_at"`
}

// PendingDispatch represents a dispatch waiting for agent to reconnect
type PendingDispatch struct {
	ID           int64           `json:"id"`
	TaskID       string          `json:"task_id"`
	AgentID      string          `json:"agent_id"`
	Payload      json.RawMessage `json:"payload"`
	CreatedAt    time.Time       `json:"created_at"`
	DispatchedAt *time.Time      `json:"dispatched_at,omitempty"`
}

var (
	ErrTaskNotFound    = errors.New("task not found")
	ErrTaskNotPending  = errors.New("task not in pending state")
	ErrCircularDeps    = errors.New("circular dependency detected")
	ErrDepsNotComplete = errors.New("dependencies not complete")
	ErrTaskNotAssigned = errors.New("task not assigned to this agent")
)

type TaskQueue interface {
	Enqueue(ctx context.Context, task Task) error
	AssignTask(ctx context.Context, taskID, agentID string) error
	Dequeue(ctx context.Context, agentID string) (*Task, error)
	Complete(ctx context.Context, taskID, result string) error
	Fail(ctx context.Context, taskID, errMsg string) error
	Pause(ctx context.Context, taskID string) error
	Resume(ctx context.Context, taskID string) error
	GetStatus(ctx context.Context, taskID string) (Task, error)
	GetTask(ctx context.Context, taskID string) (Task, error)
	UpdateTaskStatus(taskID, status, workerID string) error

	// Task listing and claiming
	ListAllTasks(ctx context.Context, limit int) ([]Task, error)
	ListTasksByStatus(ctx context.Context, status TaskStatus, limit int) ([]Task, error)
	GetClaimableTasks(ctx context.Context, limit int) ([]Task, error)
	ClaimTask(ctx context.Context, taskID, agentID string) error
	ReleaseTask(ctx context.Context, taskID, agentID, reason string) error
	ExtendLease(ctx context.Context, taskID, agentID string) error

	// Event sourcing
	GetTaskEvents(ctx context.Context, taskID string, limit int) ([]TaskEvent, error)

	// Pending dispatch queue (for offline agents)
	QueuePendingDispatch(ctx context.Context, taskID, agentID string, payload map[string]interface{}) error
	GetPendingDispatches(ctx context.Context, agentID string) ([]PendingDispatch, error)
	MarkDispatchSent(ctx context.Context, dispatchID int64) error

	Close() error
}

type sqliteTaskQueue struct {
	db  *sql.DB
	mu  sync.RWMutex
	bad map[string]bool
}

func NewTaskQueue(dbPath string) (TaskQueue, error) {
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	q := &sqliteTaskQueue{db: db, bad: make(map[string]bool)}
	return q, nil
}

// NewTaskQueueFromDB creates a TaskQueue from an existing database connection
func NewTaskQueueFromDB(db *sql.DB) (TaskQueue, error) {
	q := &sqliteTaskQueue{db: db, bad: make(map[string]bool)}
	return q, nil
}

func (q *sqliteTaskQueue) Enqueue(ctx context.Context, task Task) error {
	if len(task.Dependencies) > 0 {
		if err := q.validateDependencies(ctx, task.Dependencies, task.ID); err != nil {
			return err
		}
	}

	now := time.Now()
	status := task.Status
	if status == "" {
		status = TaskStatusQueued
	}

	_, err := q.db.ExecContext(ctx,
		`INSERT INTO tasks (id, domain, project, type, title, priority, status, assigned_to, plan_version, plan_id, envelope_id, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(id) DO UPDATE SET
		 domain=excluded.domain, project=excluded.project, type=excluded.type,
		 title=excluded.title, priority=excluded.priority, status=excluded.status, updated_at=excluded.updated_at,
		 plan_version=excluded.plan_version, plan_id=excluded.plan_id,
		 envelope_id=excluded.envelope_id`,
		task.ID, task.Domain, task.Project, task.Type, task.Title, task.Priority,
		status, task.AssignedTo, task.PlanVersion, task.PlanID, task.EnvelopeID, now.Format(time.RFC3339), now.Format(time.RFC3339),
	)
	if err != nil {
		return err
	}

	if len(task.Dependencies) > 0 {
		_, err = q.db.ExecContext(ctx,
			"INSERT OR IGNORE INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
			task.ID, task.Dependencies[0],
		)
		for _, dep := range task.Dependencies[1:] {
			_, err = q.db.ExecContext(ctx,
				"INSERT OR IGNORE INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
				task.ID, dep,
			)
		}
	}

	// Write event for all task creations
	q.writeEvent(ctx, task.ID, task.Domain, task.Project, "task.created", map[string]interface{}{
		"status":   status,
		"priority": task.Priority,
		"type":     task.Type,
	})

	return nil
}

func (q *sqliteTaskQueue) AssignTask(ctx context.Context, taskID, agentID string) error {
	// Get task info for event
	task, _ := q.GetTask(ctx, taskID)

	now := time.Now()
	_, err := q.db.ExecContext(ctx,
		`UPDATE tasks SET assigned_to = ?, status = ?, started_at = ?, updated_at = ? WHERE id = ?`,
		agentID, TaskStatusExecuting, now.Format(time.RFC3339), now.Format(time.RFC3339), taskID)

	if err == nil {
		q.writeEvent(ctx, taskID, task.Domain, task.Project, "task.assigned", map[string]interface{}{
			"agent_id": agentID,
			"status":   TaskStatusExecuting,
		})
	}

	return err
}

func (q *sqliteTaskQueue) Dequeue(ctx context.Context, agentID string) (*Task, error) {
	q.mu.Lock()
	defer q.mu.Unlock()

	// First, collect all candidate tasks
	rows, err := q.db.QueryContext(ctx,
		`SELECT t.id, t.domain, t.project, t.type, t.priority, t.status, t.assigned_to, 
			t.plan_version, t.plan_id, t.created_at, t.started_at, t.updated_at
		 FROM tasks t
		 WHERE t.status = ? 
		 ORDER BY t.priority DESC, t.created_at ASC
		 LIMIT 10`,
		TaskStatusQueued,
	)
	if err != nil {
		return nil, err
	}

	var candidates []Task
	for rows.Next() {
		var task Task
		var createdAt, updatedAt, startedAt, assignedTo, planID sql.NullString
		var planVersion sql.NullInt64
		err := rows.Scan(&task.ID, &task.Domain, &task.Project, &task.Type,
			&task.Priority, &task.Status, &assignedTo,
			&planVersion, &planID,
			&createdAt, &startedAt, &updatedAt)

		if err != nil {
			continue
		}

		if assignedTo.Valid {
			task.AssignedTo = assignedTo.String
		}
		if planID.Valid {
			task.PlanID = planID.String
		}
		if planVersion.Valid {
			task.PlanVersion = int(planVersion.Int64)
		}

		// Parse timestamps
		if createdAt.Valid {
			task.CreatedAt, _ = time.Parse(time.RFC3339, createdAt.String)
		}
		if updatedAt.Valid {
			task.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt.String)
		}
		if startedAt.Valid {
			started, _ := time.Parse(time.RFC3339, startedAt.String)
			task.StartedAt = &started
		}
		candidates = append(candidates, task)
	}
	rows.Close()

	// Now check each candidate's dependencies and assign the first ready one
	for _, task := range candidates {
		if q.bad[task.ID] {
			continue
		}

		// Check if all dependencies are complete
		var pendingDeps int
		err := q.db.QueryRowContext(ctx,
			`SELECT COUNT(*) FROM task_dependencies td 
			 JOIN tasks dep ON td.depends_on = dep.id 
			 WHERE td.task_id = ? AND dep.status != ?`,
			task.ID, TaskStatusCompleted,
		).Scan(&pendingDeps)
		if err != nil {
			return nil, err
		}
		if pendingDeps > 0 {
			continue
		}

		// Assign this task
		now := time.Now()
		_, err = q.db.ExecContext(ctx,
			"UPDATE tasks SET status = ?, assigned_to = ?, started_at = ?, updated_at = ? WHERE id = ?",
			TaskStatusAssigned, agentID, now.Format(time.RFC3339), now.Format(time.RFC3339), task.ID,
		)
		if err != nil {
			return nil, err
		}

		task.Status = TaskStatusAssigned
		task.AssignedTo = agentID
		task.StartedAt = &now
		task.UpdatedAt = now

		return &task, nil
	}

	return nil, errors.New("no tasks available")
}

func (q *sqliteTaskQueue) Complete(ctx context.Context, taskID, result string) error {
	// Get task info for event
	task, _ := q.GetTask(ctx, taskID)

	now := time.Now()
	_, err := q.db.ExecContext(ctx,
		"UPDATE tasks SET status = ?, result = ?, completed_at = ?, updated_at = ? WHERE id = ?",
		TaskStatusCompleted, result, now.Format(time.RFC3339), now.Format(time.RFC3339), taskID,
	)

	if err == nil {
		q.writeEvent(ctx, taskID, task.Domain, task.Project, "task.completed", map[string]interface{}{
			"result": result,
			"status": TaskStatusCompleted,
		})
	}

	return err
}

func (q *sqliteTaskQueue) Fail(ctx context.Context, taskID, errMsg string) error {
	// Get task info for event
	task, _ := q.GetTask(ctx, taskID)

	now := time.Now()
	_, err := q.db.ExecContext(ctx,
		"UPDATE tasks SET status = ?, error = ?, completed_at = ?, updated_at = ? WHERE id = ?",
		TaskStatusFailed, errMsg, now.Format(time.RFC3339), now.Format(time.RFC3339), taskID,
	)

	if err == nil {
		q.writeEvent(ctx, taskID, task.Domain, task.Project, "task.failed", map[string]interface{}{
			"error":  errMsg,
			"status": TaskStatusFailed,
		})
	}

	return err
}

// Pause marks a task as paused
func (q *sqliteTaskQueue) Pause(ctx context.Context, taskID string) error {
	// Get task info for event
	task, _ := q.GetTask(ctx, taskID)

	now := time.Now()
	result, err := q.db.ExecContext(ctx,
		"UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status NOT IN (?, ?, ?)",
		TaskStatusPaused, now.Format(time.RFC3339), taskID, TaskStatusCompleted, TaskStatusFailed, TaskStatusPaused,
	)
	if err != nil {
		return err
	}
	rows, _ := result.RowsAffected()
	if rows == 0 {
		return ErrTaskNotFound
	}

	q.writeEvent(ctx, taskID, task.Domain, task.Project, "task.paused", map[string]interface{}{
		"status": TaskStatusPaused,
	})

	return nil
}

// Resume marks a paused task back to executing
func (q *sqliteTaskQueue) Resume(ctx context.Context, taskID string) error {
	// Get task info for event
	task, _ := q.GetTask(ctx, taskID)

	now := time.Now()
	result, err := q.db.ExecContext(ctx,
		"UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
		TaskStatusExecuting, now.Format(time.RFC3339), taskID, TaskStatusPaused,
	)
	if err != nil {
		return err
	}
	rows, _ := result.RowsAffected()
	if rows == 0 {
		return ErrTaskNotFound
	}

	q.writeEvent(ctx, taskID, task.Domain, task.Project, "task.resumed", map[string]interface{}{
		"status": TaskStatusExecuting,
	})

	return nil
}

// UpdateTaskStatus updates a task's status and assigned worker, used by WebSocket events.
func (q *sqliteTaskQueue) UpdateTaskStatus(taskID, status, workerID string) error {
	ctx := context.Background()
	now := time.Now()

	// Get task info for event
	task, _ := q.GetTask(ctx, taskID)

	var mappedStatus TaskStatus
	var eventType string
	switch status {
	case "in_progress":
		mappedStatus = TaskStatusExecuting
		eventType = "task.started"
	case "completed":
		mappedStatus = TaskStatusCompleted
		eventType = "task.completed"
	case "failed":
		mappedStatus = TaskStatusFailed
		eventType = "task.failed"
	default:
		mappedStatus = TaskStatus(status)
		eventType = "task.status_changed"
	}

	_, err := q.db.ExecContext(
		ctx,
		"UPDATE tasks SET status = ?, assigned_to = ?, updated_at = ? WHERE id = ?",
		mappedStatus,
		workerID,
		now.Format(time.RFC3339),
		taskID,
	)

	if err == nil {
		q.writeEvent(ctx, taskID, task.Domain, task.Project, eventType, map[string]interface{}{
			"status":   mappedStatus,
			"agent_id": workerID,
		})
	}

	return err
}

func (q *sqliteTaskQueue) GetStatus(ctx context.Context, taskID string) (Task, error) {
	task, err := q.GetTask(ctx, taskID)
	if err != nil {
		return Task{}, err
	}
	return task, nil
}

func (q *sqliteTaskQueue) GetTask(ctx context.Context, taskID string) (Task, error) {
	var task Task
	var createdAt, updatedAt, startedAt, result, errorMsg, assignedTo, planID, lane, envelopeID sql.NullString
	var planVersion sql.NullInt64

	err := q.db.QueryRowContext(ctx,
		`SELECT id, domain, project, type, priority, status, lane, assigned_to, 
		                                        plan_version, plan_id, result, error, envelope_id, created_at, started_at, updated_at 
		                                        FROM tasks WHERE id = ?`, taskID,
	).Scan(&task.ID, &task.Domain, &task.Project, &task.Type,
		&task.Priority, &task.Status, &lane, &assignedTo,
		&planVersion, &planID, &result, &errorMsg, &envelopeID,
		&createdAt, &startedAt, &updatedAt)

	if err == sql.ErrNoRows {
		return Task{}, ErrTaskNotFound
	}
	if err != nil {
		return Task{}, err
	}

	if lane.Valid {
		task.Lane = lane.String
	}
	if assignedTo.Valid {
		task.AssignedTo = assignedTo.String
	}
	if planID.Valid {
		task.PlanID = planID.String
	}
	if planVersion.Valid {
		task.PlanVersion = int(planVersion.Int64)
	}
	if result.Valid {
		task.Result = result.String
	}
	if errorMsg.Valid {
		task.Error = errorMsg.String
	}
	if envelopeID.Valid {
		task.EnvelopeID = envelopeID.String
	}

	if createdAt.Valid {
		created, _ := time.Parse(time.RFC3339, createdAt.String)
		task.CreatedAt = created
	}
	if updatedAt.Valid {
		updated, _ := time.Parse(time.RFC3339, updatedAt.String)
		task.UpdatedAt = updated
	}
	if startedAt.Valid {
		started, _ := time.Parse(time.RFC3339, startedAt.String)
		task.StartedAt = &started
	}

	deps, _ := q.getDependencies(ctx, taskID)
	task.Dependencies = deps

	return task, nil
}

func (q *sqliteTaskQueue) getDependencies(ctx context.Context, taskID string) ([]string, error) {
	rows, err := q.db.QueryContext(ctx,
		"SELECT depends_on FROM task_dependencies WHERE task_id = ?", taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var deps []string
	for rows.Next() {
		var dep string
		if err := rows.Scan(&dep); err != nil {
			return nil, err
		}
		deps = append(deps, dep)
	}
	return deps, rows.Err()
}

func (q *sqliteTaskQueue) dependenciesComplete(ctx context.Context, taskID string) (bool, error) {
	rows, err := q.db.QueryContext(ctx,
		`SELECT t.status FROM tasks t
		 INNER JOIN task_dependencies td ON t.id = td.depends_on
		 WHERE td.task_id = ?`,
		taskID,
	)
	if err != nil {
		return false, err
	}
	defer rows.Close()

	for rows.Next() {
		var status TaskStatus
		if err := rows.Scan(&status); err != nil {
			return false, err
		}
		if status != TaskStatusCompleted {
			return false, nil
		}
	}

	return true, rows.Err()
}

func (q *sqliteTaskQueue) validateDependencies(ctx context.Context, deps []string, taskID string) error {
	visited := make(map[string]bool)
	for _, dep := range deps {
		if err := q.checkCircular(ctx, dep, taskID, visited); err != nil {
			return err
		}
	}
	return nil
}

func (q *sqliteTaskQueue) checkCircular(ctx context.Context, current, target string, visited map[string]bool) error {
	if current == target {
		return ErrCircularDeps
	}
	if visited[current] {
		return nil
	}
	visited[current] = true

	deps, err := q.getDependencies(ctx, current)
	if err != nil {
		return err
	}

	for _, dep := range deps {
		if err := q.checkCircular(ctx, dep, target, visited); err != nil {
			return err
		}
	}
	return nil
}

func (q *sqliteTaskQueue) MarkBad(taskID string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.bad[taskID] = true
}

func (q *sqliteTaskQueue) ListAllTasks(ctx context.Context, limit int) ([]Task, error) {
	rows, err := q.db.QueryContext(ctx,
		`SELECT id, domain, project, type, priority, status, lane, assigned_to,
					 plan_version, plan_id, result, error, envelope_id, created_at, started_at, updated_at 
 
		 FROM tasks ORDER BY created_at DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return q.scanTasks(rows)
}

func (q *sqliteTaskQueue) ListTasksByStatus(ctx context.Context, status TaskStatus, limit int) ([]Task, error) {
	rows, err := q.db.QueryContext(ctx,
		`SELECT id, domain, project, type, priority, status, lane, assigned_to,
					 plan_version, plan_id, result, error, envelope_id, created_at, started_at, updated_at 
 
		 FROM tasks WHERE status = ? ORDER BY priority DESC, created_at ASC LIMIT ?`,
		status, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return q.scanTasks(rows)
}

func (q *sqliteTaskQueue) GetClaimableTasks(ctx context.Context, limit int) ([]Task, error) {
	rows, err := q.db.QueryContext(ctx,
		`SELECT id, domain, project, type, priority, status, lane, assigned_to,
					 plan_version, plan_id, result, error, envelope_id, created_at, started_at, updated_at 
 
		 FROM tasks 
		 WHERE (status = ? OR status = ?) 
		 AND (assigned_to IS NULL OR assigned_to = '') 
		 ORDER BY priority DESC, created_at ASC 
		 LIMIT ?`,
		TaskStatusQueued, TaskStatusRequested, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return q.scanTasks(rows)
}

func (q *sqliteTaskQueue) scanTasks(rows *sql.Rows) ([]Task, error) {
	var tasks []Task
	for rows.Next() {
		var task Task
		var createdAt, updatedAt, startedAt, result, errorMsg, assignedTo, planID, lane, envelopeID sql.NullString
		var planVersion sql.NullInt64

		err := rows.Scan(&task.ID, &task.Domain, &task.Project, &task.Type,
			&task.Priority, &task.Status, &lane, &assignedTo,
			&planVersion, &planID, &result, &errorMsg, &envelopeID,
			&createdAt, &startedAt, &updatedAt)
		if err != nil {
			return nil, err
		}

		if lane.Valid {
			task.Lane = lane.String
		}
		if assignedTo.Valid {
			task.AssignedTo = assignedTo.String
		}
		if planID.Valid {
			task.PlanID = planID.String
		}
		if planVersion.Valid {
			task.PlanVersion = int(planVersion.Int64)
		}
		if result.Valid {
			task.Result = result.String
		}
		if errorMsg.Valid {
			task.Error = errorMsg.String
		}
		if envelopeID.Valid {
			task.EnvelopeID = envelopeID.String
		}

		if createdAt.Valid {
			created, _ := time.Parse(time.RFC3339, createdAt.String)
			task.CreatedAt = created
		}
		if updatedAt.Valid {
			updated, _ := time.Parse(time.RFC3339, updatedAt.String)
			task.UpdatedAt = updated
		}
		if startedAt.Valid {
			started, _ := time.Parse(time.RFC3339, startedAt.String)
			task.StartedAt = &started
		}

		tasks = append(tasks, task)
	}
	return tasks, rows.Err()
}

func (q *sqliteTaskQueue) ClaimTask(ctx context.Context, taskID, agentID string) error {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Check if task is claimable
	var currentStatus string
	var assignedTo sql.NullString
	err := q.db.QueryRowContext(ctx,
		"SELECT status, assigned_to FROM tasks WHERE id = ?",
		taskID).Scan(&currentStatus, &assignedTo)

	if err != nil {
		return fmt.Errorf("task not found: %w", err)
	}

	if TaskStatus(currentStatus) != TaskStatusQueued && TaskStatus(currentStatus) != TaskStatusRequested {
		return fmt.Errorf("task not claimable, status: %s", currentStatus)
	}

	if assignedTo.Valid && assignedTo.String != "" && assignedTo.String != agentID {
		return fmt.Errorf("task already assigned to %s", assignedTo.String)
	}

	// Update task
	now := time.Now()
	_, err = q.db.ExecContext(ctx,
		`UPDATE tasks SET status = ?, assigned_to = ?, updated_at = ? WHERE id = ?`,
		TaskStatusAssigned, agentID, now.Format(time.RFC3339), taskID)

	return err
}

func (q *sqliteTaskQueue) ReleaseTask(ctx context.Context, taskID, agentID, reason string) error {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Verify assignment
	var assignedTo string
	err := q.db.QueryRowContext(ctx,
		"SELECT assigned_to FROM tasks WHERE id = ?",
		taskID).Scan(&assignedTo)

	if err != nil {
		return fmt.Errorf("task not found: %w", err)
	}

	if assignedTo != agentID {
		return fmt.Errorf("task not assigned to this agent")
	}

	// Update task back to queued
	now := time.Now()
	_, err = q.db.ExecContext(ctx,
		`UPDATE tasks SET status = ?, assigned_to = NULL, updated_at = ? WHERE id = ?`,
		TaskStatusQueued, now.Format(time.RFC3339), taskID)

	return err
}

func (q *sqliteTaskQueue) ExtendLease(ctx context.Context, taskID, agentID string) error {
	// Try to extend the actual lease expiry in the leases table.
	newExpiry := time.Now().Add(30 * time.Minute).UTC().Format(time.RFC3339)
	result, err := q.db.ExecContext(ctx,
		`UPDATE leases SET expires_at = ? WHERE task_id = ? AND agent_id = ?`,
		newExpiry, taskID, agentID)
	if err != nil {
		return err
	}
	leaseRows, _ := result.RowsAffected()

	// Regardless of lease presence, verify the task is assigned to this agent
	// and update tasks.updated_at as a heartbeat record.
	now := time.Now()
	taskResult, err := q.db.ExecContext(ctx,
		`UPDATE tasks SET updated_at = ? WHERE id = ? AND assigned_to = ? AND status = ?`,
		now.Format(time.RFC3339), taskID, agentID, TaskStatusAssigned)
	if err != nil {
		return err
	}
	taskRows, _ := taskResult.RowsAffected()
	if taskRows == 0 && leaseRows == 0 {
		return fmt.Errorf("task not found or not assigned to this agent")
	}
	return nil
}

// GetTaskEvents retrieves events for a task from the event sourcing log
func (q *sqliteTaskQueue) GetTaskEvents(ctx context.Context, taskID string, limit int) ([]TaskEvent, error) {
	if limit <= 0 {
		limit = 50
	}

	if limit <= 0 || limit > 100 {
		limit = 50
	}

	// Use fmt.Sprintf for LIMIT to avoid SQLite parameter type issues
	query := fmt.Sprintf(`SELECT id, task_id, domain, project, event_type, payload, created_at
		 FROM task_events
		 WHERE task_id = ?
		 ORDER BY created_at DESC, id DESC
		 LIMIT %d`, limit)

	rows, err := q.db.QueryContext(ctx, query, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	events := []TaskEvent{}
	for rows.Next() {
		var event TaskEvent
		var createdAt string
		var payloadStr string
		err := rows.Scan(&event.ID, &event.TaskID, &event.Domain, &event.Project, &event.EventType, &payloadStr, &createdAt)
		if err != nil {
			log.Printf("[GetTaskEvents] Scan error: %v", err)
			continue
		}
		// Convert string payload to json.RawMessage
		event.Payload = json.RawMessage(payloadStr)
		// SQLite datetime format: "2006-01-02 15:04:05"
		parsedTime, parseErr := time.Parse("2006-01-02 15:04:05", createdAt)
		if parseErr != nil {
			log.Printf("[GetTaskEvents] Time parse error for '%s': %v", createdAt, parseErr)
		}
		event.CreatedAt = parsedTime
		events = append(events, event)
	}

	if err := rows.Err(); err != nil {
		log.Printf("[GetTaskEvents] Rows error: %v", err)
		return events, err
	}

	return events, nil
}

// writeEvent writes an event to the task_events table for event sourcing
func (q *sqliteTaskQueue) writeEvent(ctx context.Context, taskID, domain, project, eventType string, payload map[string]interface{}) {
	payloadJSON, _ := json.Marshal(payload)
	_, err := q.db.ExecContext(ctx,
		`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		 VALUES (?, ?, ?, ?, ?, datetime('now'))`,
		taskID, domain, project, eventType, string(payloadJSON),
	)
	if err != nil {
		// Log but don't fail - event sourcing is best-effort
		// In production, this should use proper logging
		_ = err
	}
}

// QueuePendingDispatch adds a dispatch to the queue for offline agents
func (q *sqliteTaskQueue) QueuePendingDispatch(ctx context.Context, taskID, agentID string, payload map[string]interface{}) error {
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	_, err = q.db.ExecContext(ctx,
		`INSERT INTO pending_dispatches (task_id, agent_id, payload, created_at)
		 VALUES (?, ?, ?, datetime('now'))`,
		taskID, agentID, string(payloadJSON),
	)
	return err
}

// GetPendingDispatches retrieves undispatched tasks for an agent
func (q *sqliteTaskQueue) GetPendingDispatches(ctx context.Context, agentID string) ([]PendingDispatch, error) {
	rows, err := q.db.QueryContext(ctx,
		`SELECT id, task_id, agent_id, payload, created_at, dispatched_at
		 FROM pending_dispatches
		 WHERE agent_id = ? AND dispatched_at IS NULL
		 ORDER BY created_at ASC`,
		agentID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var dispatches []PendingDispatch
	for rows.Next() {
		var d PendingDispatch
		var payloadStr string
		var createdAt string
		var dispatchedAt sql.NullString

		err := rows.Scan(&d.ID, &d.TaskID, &d.AgentID, &payloadStr, &createdAt, &dispatchedAt)
		if err != nil {
			continue
		}

		d.Payload = json.RawMessage(payloadStr)
		d.CreatedAt, _ = time.Parse("2006-01-02 15:04:05", createdAt)
		if dispatchedAt.Valid {
			parsed, _ := time.Parse("2006-01-02 15:04:05", dispatchedAt.String)
			d.DispatchedAt = &parsed
		}

		dispatches = append(dispatches, d)
	}

	return dispatches, rows.Err()
}

// MarkDispatchSent marks a pending dispatch as sent
func (q *sqliteTaskQueue) MarkDispatchSent(ctx context.Context, dispatchID int64) error {
	_, err := q.db.ExecContext(ctx,
		`UPDATE pending_dispatches SET dispatched_at = datetime('now') WHERE id = ?`,
		dispatchID,
	)
	return err
}

func (q *sqliteTaskQueue) Close() error {
	return q.db.Close()
}

// eventsHandler handles GET /api/events with optional filters:
//
//	?task_id=   — filter by task ID
//	?domain=    — filter by domain
//	?project=   — filter by project
//	?event_type= — filter by event type
//	?since=     — ISO-8601 or SQLite datetime lower bound (inclusive)
//	?limit=     — max rows to return (default 50, max 200)
//
// Response: {"count": N, "events": [{id, task_id, domain, project, event_type, payload, created_at}...]}
func eventsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	q := r.URL.Query()

	taskID := q.Get("task_id")
	domain := q.Get("domain")
	project := q.Get("project")
	eventType := q.Get("event_type")
	since := q.Get("since")

	limit := 50
	if l := q.Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}

	db := getDBConn()
	if db == nil {
		writeJSONError(w, http.StatusInternalServerError, "database unavailable")
		return
	}

	// Build query dynamically with optional filters.
	// LIMIT is inlined via fmt.Sprintf to avoid SQLite parameter type issues.
	baseQuery := `SELECT id, task_id, domain, project, event_type, payload, created_at
		FROM task_events
		WHERE 1=1`
	var args []interface{}

	if taskID != "" {
		baseQuery += " AND task_id = ?"
		args = append(args, taskID)
	}
	if domain != "" {
		baseQuery += " AND domain = ?"
		args = append(args, domain)
	}
	if project != "" {
		baseQuery += " AND project = ?"
		args = append(args, project)
	}
	if eventType != "" {
		baseQuery += " AND event_type = ?"
		args = append(args, eventType)
	}
	if since != "" {
		baseQuery += " AND created_at >= ?"
		args = append(args, since)
	}

	baseQuery += fmt.Sprintf(" ORDER BY created_at DESC, id DESC LIMIT %d", limit)

	rows, err := db.QueryContext(r.Context(), baseQuery, args...)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, "query failed: "+err.Error())
		return
	}
	defer rows.Close()

	events := []TaskEvent{}
	for rows.Next() {
		var ev TaskEvent
		var createdAt string
		var payloadStr string
		if scanErr := rows.Scan(&ev.ID, &ev.TaskID, &ev.Domain, &ev.Project, &ev.EventType, &payloadStr, &createdAt); scanErr != nil {
			log.Printf("[eventsHandler] scan error: %v", scanErr)
			continue
		}
		ev.Payload = json.RawMessage(payloadStr)
		if t, parseErr := time.Parse("2006-01-02 15:04:05", createdAt); parseErr == nil {
			ev.CreatedAt = t
		}
		events = append(events, ev)
	}
	if err := rows.Err(); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "rows error: "+err.Error())
		return
	}

	writeJSON(w, map[string]interface{}{
		"count":  len(events),
		"events": events,
	})
}

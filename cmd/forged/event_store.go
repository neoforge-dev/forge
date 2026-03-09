//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/oklog/ulid/v2"
)

// Event types
const (
	EventTaskCreated       = "TaskCreated"
	EventTaskAssigned      = "TaskAssigned"
	EventTaskStarted       = "TaskStarted"
	EventTaskCompleted     = "TaskCompleted"
	EventTaskFailed        = "TaskFailed"
	EventTaskPaused        = "TaskPaused"
	EventTaskResumed       = "TaskResumed"
	EventContextAdded      = "ContextAdded"
	EventApprovalRequested = "ApprovalRequested"
	EventApprovalGranted   = "ApprovalGranted"
)

// Event represents a single event in the sourcing log
type Event struct {
	ID          string          `json:"id"`
	AggregateID string          `json:"aggregate_id"` // Task ID
	Type        string          `json:"type"`         // TaskCreated, TaskAssigned, etc.
	Version     int             `json:"version"`      // Sequence number
	Payload     json.RawMessage `json:"payload"`
	Timestamp   time.Time       `json:"timestamp"`
	AgentID     string          `json:"agent_id"`
}

// EventStore defines the interface for persisting and retrieving events
type EventStore interface {
	Append(ctx context.Context, events []Event) error
	GetEvents(ctx context.Context, aggregateID string) ([]Event, error)
	GetEventsByType(ctx context.Context, eventType string) ([]Event, error)
	GetEventsSince(ctx context.Context, timestamp time.Time) ([]Event, error)
	GetAllEvents(ctx context.Context, limit int) ([]Event, error)
	GetCurrentVersion(ctx context.Context, aggregateID string) (int, error)
	RebuildTaskState(ctx context.Context, taskID string) (*Task, error)
}

type sqliteEventStore struct {
	db *sql.DB
}

// NewEventStore creates a new SQLite-backed event store
func NewEventStore(db *sql.DB) EventStore {
	return &sqliteEventStore{db: db}
}

func (s *sqliteEventStore) Append(ctx context.Context, events []Event) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	for _, e := range events {
		id := e.ID
		if id == "" {
			id = ulid.Make().String()
		}
		
		timestamp := e.Timestamp
		if timestamp.IsZero() {
			timestamp = time.Now()
		}

		_, err = tx.ExecContext(ctx, `
			INSERT INTO events (id, aggregate_id, type, version, payload, timestamp, agent_id)
			VALUES (?, ?, ?, ?, ?, ?, ?)`,
			id, e.AggregateID, e.Type, e.Version, e.Payload, timestamp.Format(time.RFC3339), e.AgentID)
		if err != nil {
			return fmt.Errorf("failed to append event %s: %w", e.Type, err)
		}
	}

	return tx.Commit()
}

func (s *sqliteEventStore) GetEvents(ctx context.Context, aggregateID string) ([]Event, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, aggregate_id, type, version, payload, timestamp, agent_id
		FROM events WHERE aggregate_id = ? ORDER BY version ASC`, aggregateID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanEvents(rows)
}

func (s *sqliteEventStore) GetEventsByType(ctx context.Context, eventType string) ([]Event, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, aggregate_id, type, version, payload, timestamp, agent_id
		FROM events WHERE type = ? ORDER BY timestamp DESC`, eventType)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanEvents(rows)
}

func (s *sqliteEventStore) GetEventsSince(ctx context.Context, timestamp time.Time) ([]Event, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, aggregate_id, type, version, payload, timestamp, agent_id
		FROM events WHERE timestamp > ? ORDER BY timestamp ASC`, timestamp.Format(time.RFC3339))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanEvents(rows)
}

func (s *sqliteEventStore) GetAllEvents(ctx context.Context, limit int) ([]Event, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, aggregate_id, type, version, payload, timestamp, agent_id
		FROM events ORDER BY timestamp DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanEvents(rows)
}

func (s *sqliteEventStore) GetCurrentVersion(ctx context.Context, aggregateID string) (int, error) {
	var version int
	err := s.db.QueryRowContext(ctx, "SELECT COALESCE(MAX(version), 0) FROM events WHERE aggregate_id = ?", aggregateID).Scan(&version)
	if err != nil && err != sql.ErrNoRows {
		return 0, err
	}
	return version, nil
}

func (s *sqliteEventStore) scanEvents(rows *sql.Rows) ([]Event, error) {
	var events []Event
	for rows.Next() {
		var e Event
		var ts string
		var agentID sql.NullString
		if err := rows.Scan(&e.ID, &e.AggregateID, &e.Type, &e.Version, &e.Payload, &ts, &agentID); err != nil {
			return nil, err
		}
		e.Timestamp, _ = time.Parse(time.RFC3339, ts)
		if agentID.Valid {
			e.AgentID = agentID.String
		}
		events = append(events, e)
	}
	return events, nil
}

// Apply applies an event to a task to update its state
func (t *Task) Apply(e Event) error {
	switch e.Type {
	case EventTaskCreated:
		var payload Task
		if err := json.Unmarshal(e.Payload, &payload); err != nil {
			return err
		}
		t.ID = payload.ID
		t.Domain = payload.Domain
		t.Project = payload.Project
		t.Type = payload.Type
		t.Priority = payload.Priority
		t.Status = payload.Status
		t.CreatedAt = e.Timestamp
		t.UpdatedAt = e.Timestamp

	case EventTaskAssigned:
		var payload struct {
			AgentID string `json:"agent_id"`
		}
		if err := json.Unmarshal(e.Payload, &payload); err != nil {
			return err
		}
		t.AssignedTo = payload.AgentID
		t.Status = TaskStatusExecuting // Or similar mapping
		t.UpdatedAt = e.Timestamp

	case EventTaskStarted:
		t.Status = TaskStatusExecuting
		now := e.Timestamp
		t.StartedAt = &now
		t.UpdatedAt = e.Timestamp

	case EventTaskCompleted:
		var payload struct {
			Result string `json:"result"`
		}
		json.Unmarshal(e.Payload, &payload)
		t.Status = TaskStatusCompleted
		t.Result = payload.Result
		t.UpdatedAt = e.Timestamp

	case EventTaskFailed:
		var payload struct {
			Error string `json:"error"`
		}
		json.Unmarshal(e.Payload, &payload)
		t.Status = TaskStatusFailed
		t.Error = payload.Error
		t.UpdatedAt = e.Timestamp
	}
	return nil
}

func (s *sqliteEventStore) RebuildTaskState(ctx context.Context, taskID string) (*Task, error) {
	events, err := s.GetEvents(ctx, taskID)
	if err != nil {
		return nil, err
	}

	if len(events) == 0 {
		return nil, fmt.Errorf("no events found for task %s", taskID)
	}

	task := &Task{}
	for _, event := range events {
		if err := task.Apply(event); err != nil {
			return nil, fmt.Errorf("failed to apply event %s to task %s: %w", event.ID, taskID, err)
		}
	}

	return task, nil
}

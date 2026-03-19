//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 220: event_store.go
// Targets:
//   NewEventStore          (event_store.go:57)  — non-nil
//   sqliteEventStore.Append (event_store.go:61) — stores events
//   GetEvents              (event_store.go:91)  — round-trip
//   GetEventsByType        (event_store.go:103) — filter by type
//   GetEventsSince         (event_store.go:115) — filter by time
//   GetAllEvents           (event_store.go:127) — limit
//   GetCurrentVersion      (event_store.go:142) — no events → 0
//   Task.Apply             (event_store.go:170) — all event types
//   RebuildTaskState       (event_store.go:224) — no events → error
// ---------------------------------------------------------------------------

// eventStoreTable ensures the event_store table exists on SQLite.
// The production table is created by migration — use CREATE IF NOT EXISTS.
func ensureEventStoreTable(t *testing.T) {
	t.Helper()
	db := getDBConn()
	if db == nil {
		t.Skip("no DB connection")
	}
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS event_store (
		id TEXT PRIMARY KEY,
		aggregate_id TEXT NOT NULL,
		type TEXT NOT NULL,
		version INTEGER NOT NULL,
		payload TEXT NOT NULL DEFAULT '{}',
		timestamp TEXT NOT NULL,
		agent_id TEXT
	)`)
	if err != nil {
		t.Skipf("cannot create event_store table: %v", err)
	}
}

// ---------------------------------------------------------------------------
// NewEventStore
// ---------------------------------------------------------------------------

func TestWave220_NewEventStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	es := NewEventStore(db)
	if es == nil {
		t.Error("expected non-nil EventStore")
	}
}

// ---------------------------------------------------------------------------
// Append + GetEvents round-trip
// ---------------------------------------------------------------------------

func TestWave220_Append_GetEvents_RoundTrip(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	ctx := context.Background()

	payload, _ := json.Marshal(map[string]string{"title": "test task"})
	evt := Event{
		ID:          "evt-001",
		AggregateID: "task-wave220",
		Type:        EventTaskCreated,
		Version:     1,
		Payload:     json.RawMessage(payload),
		Timestamp:   time.Now().UTC(),
	}

	if err := es.Append(ctx, []Event{evt}); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	events, err := es.GetEvents(ctx, "task-wave220")
	if err != nil {
		t.Fatalf("GetEvents failed: %v", err)
	}
	if len(events) != 1 {
		t.Errorf("expected 1 event, got %d", len(events))
	}
	if events[0].ID != "evt-001" {
		t.Errorf("expected event ID evt-001, got %q", events[0].ID)
	}
}

// ---------------------------------------------------------------------------
// GetEvents — no events for unknown aggregate
// ---------------------------------------------------------------------------

func TestWave220_GetEvents_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	events, err := es.GetEvents(context.Background(), "nonexistent-task-xyz")
	if err != nil {
		t.Fatalf("GetEvents failed: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("expected 0 events, got %d", len(events))
	}
}

// ---------------------------------------------------------------------------
// GetEventsByType
// ---------------------------------------------------------------------------

func TestWave220_GetEventsByType(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	ctx := context.Background()

	payload, _ := json.Marshal(map[string]string{})
	evt := Event{
		ID:          "evt-002",
		AggregateID: "task-wave220b",
		Type:        EventTaskStarted,
		Version:     1,
		Payload:     json.RawMessage(payload),
		Timestamp:   time.Now().UTC(),
	}
	if err := es.Append(ctx, []Event{evt}); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	events, err := es.GetEventsByType(ctx, EventTaskStarted)
	if err != nil {
		t.Fatalf("GetEventsByType failed: %v", err)
	}
	found := false
	for _, e := range events {
		if e.ID == "evt-002" {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected to find evt-002 in GetEventsByType results")
	}
}

// ---------------------------------------------------------------------------
// GetEventsSince
// ---------------------------------------------------------------------------

func TestWave220_GetEventsSince(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	ctx := context.Background()

	before := time.Now().UTC().Add(-time.Second)
	payload, _ := json.Marshal(map[string]string{})
	evt := Event{
		ID:          "evt-003",
		AggregateID: "task-wave220c",
		Type:        EventTaskCompleted,
		Version:     1,
		Payload:     json.RawMessage(payload),
		Timestamp:   time.Now().UTC(),
	}
	if err := es.Append(ctx, []Event{evt}); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	events, err := es.GetEventsSince(ctx, before)
	if err != nil {
		t.Fatalf("GetEventsSince failed: %v", err)
	}
	// Should include our event (and possibly others from this test run)
	_ = events
}

// ---------------------------------------------------------------------------
// GetAllEvents
// ---------------------------------------------------------------------------

func TestWave220_GetAllEvents_Limit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	ctx := context.Background()

	events, err := es.GetAllEvents(ctx, 10)
	if err != nil {
		t.Fatalf("GetAllEvents failed: %v", err)
	}
	if len(events) > 10 {
		t.Errorf("expected at most 10 events, got %d", len(events))
	}
}

// ---------------------------------------------------------------------------
// GetCurrentVersion — no events → version 0
// ---------------------------------------------------------------------------

func TestWave220_GetCurrentVersion_NoEvents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	v, err := es.GetCurrentVersion(context.Background(), "task-never-existed")
	if err != nil {
		t.Fatalf("GetCurrentVersion failed: %v", err)
	}
	if v != 0 {
		t.Errorf("expected version 0 for unknown task, got %d", v)
	}
}

// ---------------------------------------------------------------------------
// Task.Apply — all event types
// ---------------------------------------------------------------------------

func TestWave220_Task_Apply_TaskCreated(t *testing.T) {
	payload, _ := json.Marshal(Task{
		ID:      "task-apply-1",
		Domain:  "forge",
		Project: "test",
		Type:    "coverage",
		Status:  TaskStatusQueued,
	})
	e := Event{
		ID:          "e1",
		AggregateID: "task-apply-1",
		Type:        EventTaskCreated,
		Version:     1,
		Payload:     json.RawMessage(payload),
		Timestamp:   time.Now(),
	}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply failed: %v", err)
	}
	if task.ID != "task-apply-1" {
		t.Errorf("expected task ID task-apply-1, got %q", task.ID)
	}
}

func TestWave220_Task_Apply_TaskAssigned(t *testing.T) {
	payload, _ := json.Marshal(map[string]string{"agent_id": "agent-xyz"})
	e := Event{
		Type:      EventTaskAssigned,
		Payload:   json.RawMessage(payload),
		Timestamp: time.Now(),
	}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply TaskAssigned failed: %v", err)
	}
	if task.AssignedTo != "agent-xyz" {
		t.Errorf("expected AssignedTo agent-xyz, got %q", task.AssignedTo)
	}
}

func TestWave220_Task_Apply_TaskStarted(t *testing.T) {
	e := Event{
		Type:      EventTaskStarted,
		Payload:   json.RawMessage(`{}`),
		Timestamp: time.Now(),
	}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply TaskStarted failed: %v", err)
	}
	if task.Status != TaskStatusExecuting {
		t.Errorf("expected status executing, got %q", task.Status)
	}
}

func TestWave220_Task_Apply_TaskCompleted(t *testing.T) {
	payload, _ := json.Marshal(map[string]string{"result": "done"})
	e := Event{
		Type:      EventTaskCompleted,
		Payload:   json.RawMessage(payload),
		Timestamp: time.Now(),
	}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply TaskCompleted failed: %v", err)
	}
	if task.Status != TaskStatusCompleted {
		t.Errorf("expected status completed, got %q", task.Status)
	}
	if task.Result != "done" {
		t.Errorf("expected result 'done', got %q", task.Result)
	}
}

func TestWave220_Task_Apply_TaskFailed(t *testing.T) {
	payload, _ := json.Marshal(map[string]string{"error": "oops"})
	e := Event{
		Type:      EventTaskFailed,
		Payload:   json.RawMessage(payload),
		Timestamp: time.Now(),
	}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply TaskFailed failed: %v", err)
	}
	if task.Status != TaskStatusFailed {
		t.Errorf("expected status failed, got %q", task.Status)
	}
	if task.Error != "oops" {
		t.Errorf("expected error 'oops', got %q", task.Error)
	}
}

func TestWave220_Task_Apply_UnknownType_NoPanic(t *testing.T) {
	e := Event{
		Type:      "UnknownEventType",
		Payload:   json.RawMessage(`{}`),
		Timestamp: time.Now(),
	}
	task := &Task{}
	// Unknown event type — should return nil (no-op)
	if err := task.Apply(e); err != nil {
		t.Fatalf("unexpected error for unknown event type: %v", err)
	}
}

// ---------------------------------------------------------------------------
// RebuildTaskState — no events → error
// ---------------------------------------------------------------------------

func TestWave220_RebuildTaskState_NoEvents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)
	ensureEventStoreTable(t)

	es := NewEventStore(db)
	_, err := es.RebuildTaskState(context.Background(), "task-no-events-xyz")
	if err == nil {
		t.Error("expected error when no events found for task")
	}
}

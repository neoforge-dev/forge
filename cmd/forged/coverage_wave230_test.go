//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// ============================================================
// coverage_wave230_test.go — EventStore + generateULID/generateTaskID
// Target: event_store.go (Append, GetEvents, GetEventsByType, GetEventsSince,
//         GetAllEvents, GetCurrentVersion), context.go generateULID/generateTaskID
// ============================================================

// --- generateULID / generateTaskID ---

func TestWave230_GenerateULID(t *testing.T) {
	id1 := generateULID()
	id2 := generateULID()
	if id1 == "" || id2 == "" {
		t.Error("expected non-empty ULIDs")
	}
	if id1 == id2 {
		t.Error("expected unique ULIDs")
	}
	if len(id1) != 26 {
		t.Errorf("expected 26-char ULID, got len=%d", len(id1))
	}
}

func TestWave230_GenerateTaskID(t *testing.T) {
	id := generateTaskID()
	if !strings.HasPrefix(id, "TASK-") {
		t.Errorf("expected TASK- prefix, got: %s", id)
	}
	parts := strings.Split(id, "-")
	if len(parts) != 4 {
		t.Errorf("expected 4 parts (TASK-ADJ-NOUN-NUM), got %d: %s", len(parts), id)
	}
}

// --- EventStore ---

func setupEventStoreDB(t *testing.T) (context.Context, EventStore, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	store := NewEventStore(db)
	return context.Background(), store, cleanup
}

func makeEvent230(aggID, evType string, version int) Event {
	payload, _ := json.Marshal(map[string]string{"key": "value"})
	return Event{
		AggregateID: aggID,
		Type:        evType,
		Version:     version,
		Payload:     payload,
		Timestamp:   time.Now(),
		AgentID:     "agent-test",
	}
}

func TestWave230_Append_SingleEvent(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	ev := makeEvent230("task-e1", EventTaskCreated, 1)
	if err := store.Append(ctx, []Event{ev}); err != nil {
		t.Fatalf("Append: %v", err)
	}
}

func TestWave230_Append_MultipleEvents(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	events := []Event{
		makeEvent230("task-multi", EventTaskCreated, 1),
		makeEvent230("task-multi", EventTaskAssigned, 2),
		makeEvent230("task-multi", EventTaskStarted, 3),
	}
	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append multi: %v", err)
	}
}

func TestWave230_Append_EmptyList(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	if err := store.Append(ctx, []Event{}); err != nil {
		t.Fatalf("Append empty: %v", err)
	}
}

func TestWave230_Append_AutoID(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	// Event with no ID — should be auto-generated
	ev := Event{
		AggregateID: "task-auto-id",
		Type:        EventTaskCreated,
		Version:     1,
		Payload:     json.RawMessage(`{}`),
	}
	if err := store.Append(ctx, []Event{ev}); err != nil {
		t.Fatalf("Append auto-id: %v", err)
	}
}

func TestWave230_GetEvents_Empty(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	events, err := store.GetEvents(ctx, "nonexistent-task")
	if err != nil {
		t.Fatalf("GetEvents: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("expected 0 events, got %d", len(events))
	}
}

func TestWave230_GetEvents_WithEvents(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	store.Append(ctx, []Event{
		makeEvent230("task-ge", EventTaskCreated, 1),
		makeEvent230("task-ge", EventTaskAssigned, 2),
	})

	events, err := store.GetEvents(ctx, "task-ge")
	if err != nil {
		t.Fatalf("GetEvents: %v", err)
	}
	if len(events) != 2 {
		t.Errorf("expected 2 events, got %d", len(events))
	}
	if events[0].Version > events[1].Version {
		t.Error("expected events in ascending version order")
	}
}

func TestWave230_GetEventsByType(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	store.Append(ctx, []Event{
		makeEvent230("task-t1", EventTaskCreated, 1),
		makeEvent230("task-t2", EventTaskCreated, 1),
		makeEvent230("task-t1", EventTaskCompleted, 2),
	})

	events, err := store.GetEventsByType(ctx, EventTaskCreated)
	if err != nil {
		t.Fatalf("GetEventsByType: %v", err)
	}
	if len(events) < 2 {
		t.Errorf("expected at least 2 TaskCreated events, got %d", len(events))
	}
	for _, e := range events {
		if e.Type != EventTaskCreated {
			t.Errorf("expected all events to be TaskCreated, got %s", e.Type)
		}
	}
}

func TestWave230_GetEventsSince(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	before := time.Now().Add(-1 * time.Second)
	store.Append(ctx, []Event{
		makeEvent230("task-since", EventTaskCreated, 1),
	})

	events, err := store.GetEventsSince(ctx, before)
	if err != nil {
		t.Fatalf("GetEventsSince: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected at least 1 event since before")
	}
}

func TestWave230_GetEventsSince_Future(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	store.Append(ctx, []Event{makeEvent230("task-since-future", EventTaskCreated, 1)})

	future := time.Now().Add(24 * time.Hour)
	events, err := store.GetEventsSince(ctx, future)
	if err != nil {
		t.Fatalf("GetEventsSince future: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("expected 0 events in future, got %d", len(events))
	}
}

func TestWave230_GetAllEvents_DefaultLimit(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	store.Append(ctx, []Event{makeEvent230("task-all", EventTaskCreated, 1)})

	events, err := store.GetAllEvents(ctx, 0) // 0 → uses default 100
	if err != nil {
		t.Fatalf("GetAllEvents: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected at least 1 event")
	}
}

func TestWave230_GetAllEvents_WithLimit(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	for i := 1; i <= 5; i++ {
		store.Append(ctx, []Event{makeEvent230("task-all-lim", EventTaskCreated, i)})
	}

	events, err := store.GetAllEvents(ctx, 3)
	if err != nil {
		t.Fatalf("GetAllEvents limit: %v", err)
	}
	if len(events) > 3 {
		t.Errorf("expected at most 3 events, got %d", len(events))
	}
}

func TestWave230_GetCurrentVersion_NoEvents(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	v, err := store.GetCurrentVersion(ctx, "nonexistent")
	if err != nil {
		t.Fatalf("GetCurrentVersion: %v", err)
	}
	if v != 0 {
		t.Errorf("expected version=0 for no events, got %d", v)
	}
}

func TestWave230_GetCurrentVersion_WithEvents(t *testing.T) {
	ctx, store, cleanup := setupEventStoreDB(t)
	defer cleanup()

	store.Append(ctx, []Event{
		makeEvent230("task-ver", EventTaskCreated, 1),
		makeEvent230("task-ver", EventTaskAssigned, 2),
		makeEvent230("task-ver", EventTaskCompleted, 5),
	})

	v, err := store.GetCurrentVersion(ctx, "task-ver")
	if err != nil {
		t.Fatalf("GetCurrentVersion: %v", err)
	}
	if v != 5 {
		t.Errorf("expected version=5, got %d", v)
	}
}

func TestWave230_NewEventStore_NotNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	if store == nil {
		t.Error("expected non-nil EventStore")
	}
}

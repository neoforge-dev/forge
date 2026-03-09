//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func TestEventStore_AppendAndRetrieve(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewEventStore(db)
	ctx := context.Background()

	taskID := "task-123"
	payload := map[string]string{"foo": "bar"}
	payloadBytes, _ := json.Marshal(payload)

	events := []Event{
		{
			AggregateID: taskID,
			Type:        EventTaskCreated,
			Version:     1,
			Payload:     payloadBytes,
			AgentID:     "agent-1",
		},
	}

	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	retrieved, err := store.GetEvents(ctx, taskID)
	if err != nil {
		t.Fatalf("GetEvents failed: %v", err)
	}

	if len(retrieved) != 1 {
		t.Errorf("expected 1 event, got %d", len(retrieved))
	}

	if retrieved[0].AggregateID != taskID || retrieved[0].Type != EventTaskCreated {
		t.Errorf("retrieved event mismatch: %+v", retrieved[0])
	}

	version, err := store.GetCurrentVersion(ctx, taskID)
	if err != nil {
		t.Fatalf("GetCurrentVersion failed: %v", err)
	}
	if version != 1 {
		t.Errorf("expected version 1, got %d", version)
	}
}

func TestEventStore_EventOrdering(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewEventStore(db)
	ctx := context.Background()

	taskID := "task-456"
	
	events := []Event{
		{AggregateID: taskID, Type: EventTaskCreated, Version: 1, Payload: json.RawMessage(`{}`)},
		{AggregateID: taskID, Type: EventTaskAssigned, Version: 2, Payload: json.RawMessage(`{}`)},
	}

	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	// GetAllEvents should return in DESC order
	all, err := store.GetAllEvents(ctx, 10)
	if err != nil {
		t.Fatalf("GetAllEvents failed: %v", err)
	}
	if len(all) < 2 {
		t.Fatalf("expected at least 2 events, got %d", len(all))
	}
	if all[0].Version != 2 || all[1].Version != 1 {
		t.Errorf("expected descending order by timestamp/version, got versions %d and %d", all[0].Version, all[1].Version)
	}

	// GetEvents should return in ASC order
	retrieved, err := store.GetEvents(ctx, taskID)
	if err != nil {
		t.Fatalf("GetEvents failed: %v", err)
	}
	if len(retrieved) != 2 {
		t.Fatalf("expected 2 events, got %d", len(retrieved))
	}
	if retrieved[0].Version != 1 || retrieved[1].Version != 2 {
		t.Errorf("expected ascending order, got versions %d and %d", retrieved[0].Version, retrieved[1].Version)
	}
}

func TestEventStore_EmptyStoreBehavior(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewEventStore(db)
	ctx := context.Background()

	retrieved, err := store.GetEvents(ctx, "non-existent")
	if err != nil {
		t.Fatalf("GetEvents failed: %v", err)
	}
	if len(retrieved) != 0 {
		t.Errorf("expected 0 events, got %d", len(retrieved))
	}

	version, err := store.GetCurrentVersion(ctx, "non-existent")
	if err != nil {
		t.Fatalf("GetCurrentVersion failed: %v", err)
	}
	if version != 0 {
		t.Errorf("expected version 0, got %d", version)
	}

	_, err = store.RebuildTaskState(ctx, "non-existent")
	if err == nil {
		t.Error("expected error rebuilding non-existent task, got nil")
	}
}

func TestEventStore_GetByTypeAndSince(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewEventStore(db)
	ctx := context.Background()

	t1 := time.Now().Add(-1 * time.Hour)
	t2 := time.Now()

	events := []Event{
		{AggregateID: "t1", Type: EventTaskCreated, Timestamp: t1, Version: 1, Payload: json.RawMessage(`{}`)},
		{AggregateID: "t2", Type: EventTaskAssigned, Timestamp: t2, Version: 1, Payload: json.RawMessage(`{}`)},
	}

	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	byType, _ := store.GetEventsByType(ctx, EventTaskCreated)
	if len(byType) != 1 {
		t.Errorf("expected 1 event of type %s, got %d", EventTaskCreated, len(byType))
	}

	since, _ := store.GetEventsSince(ctx, time.Now().Add(-30*time.Minute))
	if len(since) != 1 {
		t.Errorf("expected 1 event since 30m ago, got %d", len(since))
	}
}

func TestTask_Apply(t *testing.T) {
	task := &Task{}
	
	// Test TaskCreated
	payload, _ := json.Marshal(Task{ID: "t1", Domain: "d1", Project: "p1", Type: "f1", Priority: 5, Status: "queued"})
	task.Apply(Event{Type: EventTaskCreated, Payload: payload, Timestamp: time.Now()})
	if task.ID != "t1" || task.Status != "queued" {
		t.Errorf("Apply TaskCreated failed: %+v", task)
	}

	// Test TaskAssigned
	payload, _ = json.Marshal(struct{ AgentID string `json:"agent_id"` }{"agent-1"})
	task.Apply(Event{Type: EventTaskAssigned, Payload: payload, Timestamp: time.Now()})
	if task.AssignedTo != "agent-1" || task.Status != TaskStatusExecuting {
		t.Errorf("Apply TaskAssigned failed: %+v", task)
	}

	// Test TaskStarted
	task.Apply(Event{Type: EventTaskStarted, Timestamp: time.Now()})
	if task.StartedAt == nil || task.Status != TaskStatusExecuting {
		t.Error("Apply TaskStarted failed")
	}

	// Test TaskCompleted
	payload, _ = json.Marshal(struct{ Result string `json:"result"` }{"success"})
	task.Apply(Event{Type: EventTaskCompleted, Payload: payload, Timestamp: time.Now()})
	if task.Status != TaskStatusCompleted || task.Result != "success" {
		t.Error("Apply TaskCompleted failed")
	}

	// Test TaskFailed
	payload, _ = json.Marshal(struct{ Error string `json:"error"` }{"oops"})
	task.Apply(Event{Type: EventTaskFailed, Payload: payload, Timestamp: time.Now()})
	if task.Status != TaskStatusFailed || task.Error != "oops" {
		t.Error("Apply TaskFailed failed")
	}
}

func TestEventStore_RebuildTaskState_Extended(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewEventStore(db)
	ctx := context.Background()
	taskID := "task-rebuild"

	payload, _ := json.Marshal(Task{ID: taskID, Domain: "d", Project: "p", Type: "f", Priority: 5, Status: "queued"})
	events := []Event{
		{AggregateID: taskID, Type: EventTaskCreated, Version: 1, Payload: payload, Timestamp: time.Now()},
		{AggregateID: taskID, Type: EventTaskAssigned, Version: 2, Payload: json.RawMessage(`{"agent_id":"a1"}`), Timestamp: time.Now()},
	}

	store.Append(ctx, events)

	task, err := store.RebuildTaskState(ctx, taskID)
	if err != nil {
		t.Fatalf("RebuildTaskState failed: %v", err)
	}

	if task.ID != taskID || task.AssignedTo != "a1" {
		t.Errorf("rebuilt task mismatch: %+v", task)
	}
}

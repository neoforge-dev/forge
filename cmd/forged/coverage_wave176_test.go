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
// Wave 176: event_store.go + parity_check.go + state_sync.go
// Targets:
//   NewEventStore                   (event_store.go:57)
//   sqliteEventStore.GetEvents      (event_store.go:91)  — empty
//   sqliteEventStore.GetAllEvents   (event_store.go:127) — zero limit defaults
//   sqliteEventStore.GetEventsByType(event_store.go:103)
//   sqliteEventStore.GetEventsSince (event_store.go:115)
//   sqliteEventStore.GetCurrentVersion (event_store.go:142)
//   sqliteEventStore.Append         (event_store.go:61)  — appends events
//   sqliteEventStore.RebuildTaskState (event_store.go:224) — no events = error
//   Task.Apply                      (event_store.go:170) — TaskCreated / TaskAssigned / etc.
//   timeNow                         (parity_check.go:167)
//   NewParityChecker                (parity_check.go:39) — empty/explicit paths
//   ParityChecker.countV2Tasks      (parity_check.go:98) — non-existent dir = 0
//   ParityChecker.countV2Agents     (parity_check.go:121)— no state file = 0
//   syncStatusState                 (state_sync.go:15)   — no desynced tasks
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// event_store.go — NewEventStore
// ---------------------------------------------------------------------------

func TestWave176_NewEventStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)
	if s == nil {
		t.Error("expected non-nil EventStore")
	}
}

// ---------------------------------------------------------------------------
// event_store.go — GetEvents (empty)
// ---------------------------------------------------------------------------

func TestWave176_GetEvents_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	events, err := s.GetEvents(context.Background(), "nonexistent-task")
	if err != nil {
		t.Errorf("GetEvents: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("expected empty events, got %d", len(events))
	}
}

// ---------------------------------------------------------------------------
// event_store.go — GetAllEvents (zero limit defaults to 100)
// ---------------------------------------------------------------------------

func TestWave176_GetAllEvents_ZeroLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	events, err := s.GetAllEvents(context.Background(), 0)
	if err != nil {
		t.Errorf("GetAllEvents zero limit: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// event_store.go — GetEventsByType (empty)
// ---------------------------------------------------------------------------

func TestWave176_GetEventsByType_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	events, err := s.GetEventsByType(context.Background(), EventTaskCreated)
	if err != nil {
		t.Errorf("GetEventsByType: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("expected empty, got %d", len(events))
	}
}

// ---------------------------------------------------------------------------
// event_store.go — GetEventsSince (empty)
// ---------------------------------------------------------------------------

func TestWave176_GetEventsSince_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	events, err := s.GetEventsSince(context.Background(), time.Now().Add(-1*time.Minute))
	if err != nil {
		t.Errorf("GetEventsSince: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// event_store.go — GetCurrentVersion (no events = 0)
// ---------------------------------------------------------------------------

func TestWave176_GetCurrentVersion_NoEvents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	v, err := s.GetCurrentVersion(context.Background(), "nonexistent-task")
	if err != nil {
		t.Errorf("GetCurrentVersion: %v", err)
	}
	if v != 0 {
		t.Errorf("expected 0, got %d", v)
	}
}

// ---------------------------------------------------------------------------
// event_store.go — Append
// ---------------------------------------------------------------------------

func TestWave176_Append_SingleEvent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	payload, _ := json.Marshal(map[string]string{"msg": "test"})
	evt := Event{
		AggregateID: "task-wave176",
		Type:        EventTaskCreated,
		Version:     1,
		Payload:     payload,
		Timestamp:   time.Now(),
	}
	if err := s.Append(context.Background(), []Event{evt}); err != nil {
		t.Errorf("Append: %v", err)
	}
}

func TestWave176_Append_EmptyAutoID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	payload, _ := json.Marshal(map[string]string{"key": "val"})
	evt := Event{
		// ID intentionally empty — should auto-generate
		AggregateID: "task-auto-id",
		Type:        EventTaskCreated,
		Version:     1,
		Payload:     payload,
	}
	if err := s.Append(context.Background(), []Event{evt}); err != nil {
		t.Errorf("Append with auto ID: %v", err)
	}
}

// ---------------------------------------------------------------------------
// event_store.go — RebuildTaskState (no events = error)
// ---------------------------------------------------------------------------

func TestWave176_RebuildTaskState_NoEvents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewEventStore(db)

	_, err := s.RebuildTaskState(context.Background(), "nonexistent-task")
	if err == nil {
		t.Error("expected error for task with no events")
	}
}

// ---------------------------------------------------------------------------
// event_store.go — Task.Apply
// ---------------------------------------------------------------------------

func TestWave176_TaskApply_TaskCreated(t *testing.T) {
	taskPayload := Task{
		ID:       "task-apply-1",
		Domain:   "forge",
		Project:  "test",
		Type:     "feature",
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	payloadBytes, _ := json.Marshal(taskPayload)
	evt := Event{
		Type:      EventTaskCreated,
		Payload:   payloadBytes,
		Timestamp: time.Now(),
	}

	task := &Task{}
	if err := task.Apply(evt); err != nil {
		t.Errorf("Apply TaskCreated: %v", err)
	}
	if task.ID != "task-apply-1" {
		t.Errorf("expected ID 'task-apply-1', got %q", task.ID)
	}
}

func TestWave176_TaskApply_TaskAssigned(t *testing.T) {
	payload, _ := json.Marshal(map[string]string{"agent_id": "kimi-1"})
	evt := Event{
		Type:      EventTaskAssigned,
		Payload:   payload,
		Timestamp: time.Now(),
	}

	task := &Task{}
	if err := task.Apply(evt); err != nil {
		t.Errorf("Apply TaskAssigned: %v", err)
	}
	if task.AssignedTo != "kimi-1" {
		t.Errorf("expected AssignedTo 'kimi-1', got %q", task.AssignedTo)
	}
}

func TestWave176_TaskApply_TaskStarted(t *testing.T) {
	evt := Event{
		Type:      EventTaskStarted,
		Payload:   json.RawMessage(`{}`),
		Timestamp: time.Now(),
	}

	task := &Task{}
	if err := task.Apply(evt); err != nil {
		t.Errorf("Apply TaskStarted: %v", err)
	}
	if task.StartedAt == nil {
		t.Error("expected StartedAt to be set")
	}
}

func TestWave176_TaskApply_TaskCompleted(t *testing.T) {
	payload, _ := json.Marshal(map[string]string{"result": "success"})
	evt := Event{
		Type:      EventTaskCompleted,
		Payload:   payload,
		Timestamp: time.Now(),
	}

	task := &Task{}
	if err := task.Apply(evt); err != nil {
		t.Errorf("Apply TaskCompleted: %v", err)
	}
	if task.Status != TaskStatusCompleted {
		t.Errorf("expected completed status, got %q", task.Status)
	}
	if task.Result != "success" {
		t.Errorf("expected result 'success', got %q", task.Result)
	}
}

func TestWave176_TaskApply_TaskFailed(t *testing.T) {
	payload, _ := json.Marshal(map[string]string{"error": "timeout"})
	evt := Event{
		Type:      EventTaskFailed,
		Payload:   payload,
		Timestamp: time.Now(),
	}

	task := &Task{}
	if err := task.Apply(evt); err != nil {
		t.Errorf("Apply TaskFailed: %v", err)
	}
	if task.Status != TaskStatusFailed {
		t.Errorf("expected failed status, got %q", task.Status)
	}
	if task.Error != "timeout" {
		t.Errorf("expected error 'timeout', got %q", task.Error)
	}
}

// ---------------------------------------------------------------------------
// parity_check.go — timeNow
// ---------------------------------------------------------------------------

func TestWave176_TimeNow_NonZero(t *testing.T) {
	ts := timeNow()
	if ts == 0 {
		t.Error("expected non-zero unix timestamp")
	}
}

// ---------------------------------------------------------------------------
// parity_check.go — NewParityChecker
// ---------------------------------------------------------------------------

func TestWave176_NewParityChecker_EmptyPaths(t *testing.T) {
	pc := NewParityChecker("", "")
	if pc == nil {
		t.Error("expected non-nil checker")
	}
	// Empty paths → defaults applied
	if pc.v2StatePath != ".forge" {
		t.Errorf("expected default v2StatePath '.forge', got %q", pc.v2StatePath)
	}
}

func TestWave176_NewParityChecker_ExplicitPaths(t *testing.T) {
	pc := NewParityChecker("/tmp/v2", "/tmp/v3.db")
	if pc.v2StatePath != "/tmp/v2" {
		t.Errorf("expected '/tmp/v2', got %q", pc.v2StatePath)
	}
}

// ---------------------------------------------------------------------------
// parity_check.go — countV2Tasks (non-existent dir = 0, no error)
// ---------------------------------------------------------------------------

func TestWave176_CountV2Tasks_NonExistentDir(t *testing.T) {
	pc := NewParityChecker("/tmp/nonexistent-wave176-forge", "")
	count, err := pc.countV2Tasks()
	if err != nil {
		t.Errorf("countV2Tasks non-existent: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// parity_check.go — countV2Agents (no state file = 0)
// ---------------------------------------------------------------------------

func TestWave176_CountV2Agents_NoStateFile(t *testing.T) {
	pc := NewParityChecker("/tmp/nonexistent-wave176-forge", "")
	count, err := pc.countV2Agents()
	if err != nil {
		t.Errorf("countV2Agents no file: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// state_sync.go — syncStatusState (no desynced tasks → no-op)
// ---------------------------------------------------------------------------

func TestWave176_SyncStatusState_NoDesync(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Should not panic or error on empty tasks table
	syncStatusState(db)
}

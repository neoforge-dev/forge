//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// token_budget.go — coverage wave 101
// ---------------------------------------------------------------------------

func TestCoverageWave101_TokenBudgetFilePath(t *testing.T) {
	tests := []struct {
		name   string
		root   string
		nodeID string
		want   string
	}{
		{"with root", "/tmp/forge", "prya", filepath.Join("/tmp/forge", ".forge", "heartbeat", "token-budgets-prya.json")},
		{"empty root defaults to dot", "", "sati", filepath.Join(".", ".forge", "heartbeat", "token-budgets-sati.json")},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := tokenBudgetFilePath(tt.root, tt.nodeID)
			if got != tt.want {
				t.Errorf("tokenBudgetFilePath(%q, %q) = %q, want %q", tt.root, tt.nodeID, got, tt.want)
			}
		})
	}
}

func TestCoverageWave101_ComputeBudgetStatus_InvalidCooldown(t *testing.T) {
	// Invalid RFC3339 string -> time.Parse fails -> we don't return COOLDOWN, derive from pcts
	invalid := "not-a-valid-date"
	got := computeBudgetStatus(10, 20, 30, &invalid)
	if got != "OK" {
		t.Errorf("computeBudgetStatus with invalid cooldown = %q, want OK", got)
	}
}

func TestCoverageWave101_ComputeBudgetStatus_AllBranches(t *testing.T) {
	tests := []struct {
		name       string
		monthly    int
		weekly     int
		h5         int
		cooldown   *string
		wantStatus string
	}{
		{"OK", 50, 50, 50, nil, "OK"},
		{"CAUTION", 82, 10, 10, nil, "CAUTION"},
		{"RED", 91, 10, 10, nil, "RED"},
		{"DEPLETED", 96, 10, 10, nil, "DEPLETED"},
		{"weekly worst", 10, 91, 10, nil, "RED"},
		{"h5 worst", 10, 10, 96, nil, "DEPLETED"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := computeBudgetStatus(tt.monthly, tt.weekly, tt.h5, tt.cooldown)
			if got != tt.wantStatus {
				t.Errorf("computeBudgetStatus(...) = %q, want %q", got, tt.wantStatus)
			}
		})
	}
}

func TestCoverageWave101_EventTypeFor_AllBranches(t *testing.T) {
	tests := []struct {
		old, new string
		want     string
	}{
		{"OK", "OK", "no_change"},
		{"RED", "COOLDOWN", "cooldown_set"},
		{"COOLDOWN", "OK", "cooldown_clear"},
		{"CAUTION", "OK", "reset"},
		{"OK", "CAUTION", "update"},
		{"DEPLETED", "RED", "update"},
	}
	for _, tt := range tests {
		got := eventTypeFor(tt.old, tt.new)
		if got != tt.want {
			t.Errorf("eventTypeFor(%q, %q) = %q, want %q", tt.old, tt.new, got, tt.want)
		}
	}
}

func TestCoverageWave101_EnsureTokenBudgetTables(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Errorf("ensureTokenBudgetTables idempotent: %v", err)
	}
}

func TestCoverageWave101_UpsertAndInsertTokenBudgetLog(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}
	now := time.Now().UTC()
	entry := tbLogEntry{
		nodeID:     "wave101",
		provider:   "test",
		eventType:  "update",
		monthlyPct: 50,
		weeklyPct:  50,
		h5Pct:      50,
		status:     "OK",
		agents:     []string{"a1"},
	}
	if err := upsertTokenBudgetSnapshot(ctx, db, entry, now); err != nil {
		t.Fatalf("upsertTokenBudgetSnapshot: %v", err)
	}
	if err := insertTokenBudgetLog(ctx, db, entry); err != nil {
		t.Fatalf("insertTokenBudgetLog: %v", err)
	}
}

// ---------------------------------------------------------------------------
// event_store.go — coverage wave 101
// ---------------------------------------------------------------------------

func TestCoverageWave101_NewEventStore(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	if store == nil {
		t.Fatal("NewEventStore returned nil")
	}
}

func TestCoverageWave101_EventStore_AppendEmptyID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	// Empty ID triggers ULID generation inside Append
	events := []Event{
		{
			ID:          "", // empty -> ulid.Make() used
			AggregateID: "task-wave101-empty-id",
			Type:        EventTaskCreated,
			Version:     1,
			Payload:     json.RawMessage(`{}`),
			AgentID:     "agent-1",
		},
	}
	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append with empty ID: %v", err)
	}
	retrieved, err := store.GetEvents(ctx, "task-wave101-empty-id")
	if err != nil {
		t.Fatalf("GetEvents: %v", err)
	}
	if len(retrieved) != 1 {
		t.Fatalf("expected 1 event, got %d", len(retrieved))
	}
	if retrieved[0].ID == "" {
		t.Error("expected non-empty ID after Append")
	}
}

func TestCoverageWave101_EventStore_AppendZeroTimestamp(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	events := []Event{
		{
			ID:          "ev-zero-ts",
			AggregateID: "task-wave101-zero-ts",
			Type:        EventTaskCreated,
			Version:     1,
			Payload:     json.RawMessage(`{}`),
			Timestamp:   time.Time{}, // zero -> time.Now() used
			AgentID:     "agent-1",
		},
	}
	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append with zero timestamp: %v", err)
	}
	retrieved, err := store.GetEvents(ctx, "task-wave101-zero-ts")
	if err != nil {
		t.Fatalf("GetEvents: %v", err)
	}
	if len(retrieved) != 1 {
		t.Fatalf("expected 1 event, got %d", len(retrieved))
	}
	if retrieved[0].Timestamp.IsZero() {
		t.Error("expected non-zero timestamp after Append")
	}
}

func TestCoverageWave101_EventStore_GetAllEvents_ZeroLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	// GetAllEvents(0) defaults limit to 100; empty DB may return nil or empty slice
	events, err := store.GetAllEvents(ctx, 0)
	if err != nil {
		t.Fatalf("GetAllEvents(0): %v", err)
	}
	// Just verify no error; slice may be nil or empty when no events
	if events != nil && len(events) > 0 {
		t.Logf("GetAllEvents(0) returned %d events (default limit 100)", len(events))
	}
}

func TestCoverageWave101_EventStore_GetAllEvents_NegativeLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	// limit <= 0 defaults to 100; empty DB may return nil or empty slice
	events, err := store.GetAllEvents(ctx, -1)
	if err != nil {
		t.Fatalf("GetAllEvents(-1): %v", err)
	}
	if events != nil && len(events) != 0 {
		t.Logf("GetAllEvents(-1) returned %d events", len(events))
	}
}

func TestCoverageWave101_EventStore_GetCurrentVersion_NoRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	version, err := store.GetCurrentVersion(ctx, "nonexistent-aggregate-wave101")
	if err != nil {
		t.Fatalf("GetCurrentVersion: %v", err)
	}
	if version != 0 {
		t.Errorf("GetCurrentVersion nonexistent = %d, want 0", version)
	}
}

func TestCoverageWave101_EventStore_RebuildTaskState_NoEvents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	_, err := store.RebuildTaskState(ctx, "no-events-wave101")
	if err == nil {
		t.Error("RebuildTaskState with no events expected error, got nil")
	}
}

func TestCoverageWave101_Task_Apply_UnknownType(t *testing.T) {
	task := &Task{}
	// Unknown event type -> Apply returns nil without changing task
	err := task.Apply(Event{Type: "UnknownEventType", Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Errorf("Apply unknown type: %v", err)
	}
}

func TestCoverageWave101_Task_Apply_AllEventTypes(t *testing.T) {
	task := &Task{}

	// TaskCreated
	payload, _ := json.Marshal(Task{ID: "t1", Domain: "d", Project: "p", Type: "f", Priority: 5, Status: "queued"})
	if err := task.Apply(Event{Type: EventTaskCreated, Payload: payload, Timestamp: time.Now()}); err != nil {
		t.Fatalf("Apply TaskCreated: %v", err)
	}
	if task.ID != "t1" {
		t.Errorf("task.ID = %q, want t1", task.ID)
	}

	// TaskAssigned
	payload, _ = json.Marshal(struct{ AgentID string `json:"agent_id"` }{"agent-1"})
	if err := task.Apply(Event{Type: EventTaskAssigned, Payload: payload, Timestamp: time.Now()}); err != nil {
		t.Fatalf("Apply TaskAssigned: %v", err)
	}
	if task.AssignedTo != "agent-1" || task.Status != TaskStatusExecuting {
		t.Errorf("after TaskAssigned: AssignedTo=%q Status=%s", task.AssignedTo, task.Status)
	}

	// TaskStarted
	if err := task.Apply(Event{Type: EventTaskStarted, Timestamp: time.Now()}); err != nil {
		t.Fatalf("Apply TaskStarted: %v", err)
	}
	if task.StartedAt == nil {
		t.Error("StartedAt not set after TaskStarted")
	}

	// TaskCompleted
	payload, _ = json.Marshal(struct{ Result string `json:"result"` }{"done"})
	if err := task.Apply(Event{Type: EventTaskCompleted, Payload: payload, Timestamp: time.Now()}); err != nil {
		t.Fatalf("Apply TaskCompleted: %v", err)
	}
	if task.Status != TaskStatusCompleted || task.Result != "done" {
		t.Errorf("after TaskCompleted: Status=%s Result=%q", task.Status, task.Result)
	}

	// Reset for TaskFailed
	task.Status = TaskStatusExecuting
	payload, _ = json.Marshal(struct{ Error string `json:"error"` }{"failed"})
	if err := task.Apply(Event{Type: EventTaskFailed, Payload: payload, Timestamp: time.Now()}); err != nil {
		t.Fatalf("Apply TaskFailed: %v", err)
	}
	if task.Status != TaskStatusFailed || task.Error != "failed" {
		t.Errorf("after TaskFailed: Status=%s Error=%q", task.Status, task.Error)
	}
}

func TestCoverageWave101_EventStore_RebuildTaskState_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	taskID := "task-rebuild-wave101"
	payload, _ := json.Marshal(Task{ID: taskID, Domain: "d", Project: "p", Type: "f", Priority: 5, Status: "queued"})
	events := []Event{
		{AggregateID: taskID, Type: EventTaskCreated, Version: 1, Payload: payload, Timestamp: time.Now()},
		{AggregateID: taskID, Type: EventTaskAssigned, Version: 2, Payload: json.RawMessage(`{"agent_id":"a1"}`), Timestamp: time.Now()},
		{AggregateID: taskID, Type: EventTaskCompleted, Version: 3, Payload: json.RawMessage(`{"result":"ok"}`), Timestamp: time.Now()},
	}
	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append: %v", err)
	}
	task, err := store.RebuildTaskState(ctx, taskID)
	if err != nil {
		t.Fatalf("RebuildTaskState: %v", err)
	}
	if task.ID != taskID || task.AssignedTo != "a1" || task.Status != TaskStatusCompleted || task.Result != "ok" {
		t.Errorf("rebuilt task: %+v", task)
	}
}

func TestCoverageWave101_EventStore_GetEventsByType_GetEventsSince(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewEventStore(db)
	ctx := context.Background()
	events := []Event{
		{AggregateID: "t1", Type: EventTaskCreated, Version: 1, Payload: json.RawMessage(`{}`), Timestamp: time.Now().Add(-2 * time.Hour), AgentID: "a1"},
		{AggregateID: "t2", Type: EventTaskAssigned, Version: 1, Payload: json.RawMessage(`{}`), Timestamp: time.Now(), AgentID: "a2"},
	}
	if err := store.Append(ctx, events); err != nil {
		t.Fatalf("Append: %v", err)
	}
	byType, err := store.GetEventsByType(ctx, EventTaskCreated)
	if err != nil {
		t.Fatalf("GetEventsByType: %v", err)
	}
	if len(byType) != 1 {
		t.Errorf("GetEventsByType(TaskCreated) = %d events, want 1", len(byType))
	}
	since, err := store.GetEventsSince(ctx, time.Now().Add(-1*time.Hour))
	if err != nil {
		t.Fatalf("GetEventsSince: %v", err)
	}
	if len(since) != 1 {
		t.Errorf("GetEventsSince(1h ago) = %d events, want 1", len(since))
	}
}

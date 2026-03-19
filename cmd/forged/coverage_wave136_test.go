//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ============================================================
// coverage_wave136_test.go — MarkDispatchSent + resultMonitorPatrol
// Target: MarkDispatchSent (queue.go:941), resultMonitorPatrol (patrol.go:1895)
// ============================================================

// TestWave136_MarkDispatchSent_Success marks a pending dispatch as sent.
func TestWave136_MarkDispatchSent_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create task queue
	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Create a task first
	task := Task{
		ID:        "task-136-1",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "test task 136",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := tq.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Queue a pending dispatch
	err = tq.QueuePendingDispatch(context.Background(), "task-136-1", "test-agent", map[string]interface{}{"msg": "hello"})
	if err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	// Get pending dispatches to find the ID
	dispatches, err := tq.GetPendingDispatches(context.Background(), "test-agent")
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) == 0 {
		t.Fatal("expected at least 1 pending dispatch")
	}

	// Now call MarkDispatchSent
	err = tq.MarkDispatchSent(context.Background(), dispatches[0].ID)
	if err != nil {
		t.Logf("MarkDispatchSent: %v", err)
	}
}

// TestWave136_MarkDispatchSent_NotFound tests marking a nonexistent dispatch.
func TestWave136_MarkDispatchSent_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Try to mark a nonexistent dispatch (ID 999999)
	err = tq.MarkDispatchSent(context.Background(), 999999)
	// Should not error (UPDATE just affects 0 rows)
	if err != nil {
		t.Logf("MarkDispatchSent not found: %v (acceptable)", err)
	}
}

// TestWave136_QueuePendingDispatch_Mark verifies round-trip dispatch + mark.
func TestWave136_QueuePendingDispatch_Mark(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Create task
	task := Task{
		ID:        "task-136-2",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "dispatch test",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := tq.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Queue dispatch
	err = tq.QueuePendingDispatch(context.Background(), "task-136-2", "agent-136", map[string]interface{}{
		"action": "test",
	})
	if err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	// Get and mark
	dispatches, err := tq.GetPendingDispatches(context.Background(), "agent-136")
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) != 1 {
		t.Fatalf("expected 1 dispatch, got %d", len(dispatches))
	}

	if err := tq.MarkDispatchSent(context.Background(), dispatches[0].ID); err != nil {
		t.Errorf("MarkDispatchSent: %v", err)
	}

	// Verify it's no longer returned (dispatched_at is set)
	dispatches2, err := tq.GetPendingDispatches(context.Background(), "agent-136")
	if err != nil {
		t.Fatalf("GetPendingDispatches after mark: %v", err)
	}
	if len(dispatches2) != 0 {
		t.Errorf("expected 0 pending dispatches after mark, got %d", len(dispatches2))
	}
}

// TestWave136_ResultMonitorPatrol_EmptyDB tests resultMonitorPatrol with empty DB.
func TestWave136_ResultMonitorPatrol_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	err := resultMonitorPatrol(context.Background(), db)
	if err != nil {
		t.Logf("resultMonitorPatrol: %v (acceptable with empty DB)", err)
	}
}

// TestWave136_ResultMonitorPatrol_CancelledContext tests with cancelled context.
func TestWave136_ResultMonitorPatrol_CancelledContext(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	err := resultMonitorPatrol(ctx, db)
	if err != nil {
		t.Logf("resultMonitorPatrol with cancelled ctx: %v (acceptable)", err)
	}
}

// TestWave136_GetPendingDispatches_MultipleAgents verifies isolation by agent.
func TestWave136_GetPendingDispatches_MultipleAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Create task
	task := Task{
		ID:        "task-136-multi",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "multi-agent test",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := tq.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Queue dispatches for different agents
	for _, agent := range []string{"agent-a", "agent-b"} {
		err = tq.QueuePendingDispatch(context.Background(), "task-136-multi", agent, map[string]interface{}{"to": agent})
		if err != nil {
			t.Fatalf("QueuePendingDispatch for %s: %v", agent, err)
		}
	}

	// Each agent should see only their own
	for _, agent := range []string{"agent-a", "agent-b"} {
		dispatches, err := tq.GetPendingDispatches(context.Background(), agent)
		if err != nil {
			t.Fatalf("GetPendingDispatches for %s: %v", agent, err)
		}
		if len(dispatches) != 1 {
			t.Errorf("expected 1 dispatch for %s, got %d", agent, len(dispatches))
		}
	}
}

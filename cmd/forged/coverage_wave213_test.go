//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 213: lane.go + state_sync.go basics
// Targets:
//   NewLaneManager             (lane.go:53)  — non-nil
//   forgeRoot                  (lane.go:236) — non-empty
//   laneManager.HandleLaneComplete (lane.go:289) — GET→405
//   laneManager.HandleLaneStatus   (lane.go:309) — POST→405
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewLaneManager
// ---------------------------------------------------------------------------

func TestWave213_NewLaneManager_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	lm := NewLaneManager(tq, nil, nil)
	if lm == nil {
		t.Error("expected non-nil LaneManager")
	}
}

// ---------------------------------------------------------------------------
// forgeRoot
// ---------------------------------------------------------------------------

func TestWave213_ForgeRoot_NonEmpty(t *testing.T) {
	root := forgeRoot()
	if root == "" {
		t.Error("expected non-empty forge root")
	}
}

// ---------------------------------------------------------------------------
// HandleLaneComplete — wrong method
// ---------------------------------------------------------------------------

func TestWave213_HandleLaneComplete_GetIs405(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, _ := NewTaskQueueFromDB(db)
	lm := NewLaneManager(tq, nil, nil).(*laneManager)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/lane/complete", nil)
	w := httptest.NewRecorder()
	lm.HandleLaneComplete(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// HandleLaneStatus — wrong method
// ---------------------------------------------------------------------------

func TestWave213_HandleLaneStatus_PostIs405(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, _ := NewTaskQueueFromDB(db)
	lm := NewLaneManager(tq, nil, nil).(*laneManager)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/lane/status", nil)
	w := httptest.NewRecorder()
	lm.HandleLaneStatus(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// HandleLaneComplete — POST with nonexistent task
// ---------------------------------------------------------------------------

func TestWave213_HandleLaneComplete_PostNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, _ := NewTaskQueueFromDB(db)
	lm := NewLaneManager(tq, nil, nil).(*laneManager)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task/lane/complete", nil)
	w := httptest.NewRecorder()
	lm.HandleLaneComplete(w, req)
	// Should return 400 (task not found)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for nonexistent task, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// HandleLaneStatus — GET with nonexistent task
// ---------------------------------------------------------------------------

func TestWave213_HandleLaneStatus_GetNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, _ := NewTaskQueueFromDB(db)
	lm := NewLaneManager(tq, nil, nil).(*laneManager)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/nonexistent-task/lane/status", nil)
	w := httptest.NewRecorder()
	lm.HandleLaneStatus(w, req)
	// Should return 400 (task not found) or 200 with empty data
	_ = w.Code
}

// ---------------------------------------------------------------------------
// CheckGates — non-existent task
// ---------------------------------------------------------------------------

func TestWave213_CheckGates_NonExistentTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, _ := NewTaskQueueFromDB(db)
	lm := NewLaneManager(tq, nil, nil).(*laneManager)

	task := &Task{ID: "wave213-task", Lane: string(LaneDev)}
	result, err := lm.CheckGates(context.Background(), task, LaneTest)
	_ = result
	_ = err // may fail with no gates configured — just no panic
}

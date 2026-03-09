//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestWave68_LaneByIDHandler_MethodNotAllowed verifies laneByIDHandler rejects non-GET.
func TestWave68_LaneByIDHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/lanes/lane-1", nil)
	rr := httptest.NewRecorder()
	laneByIDHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

// TestWave68_LaneByIDHandler_MissingID verifies laneByIDHandler returns 400 for missing ID.
func TestWave68_LaneByIDHandler_MissingID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	// Path without an ID — TrimPrefix leaves empty string.
	req := httptest.NewRequest(http.MethodGet, "/api/lanes/", nil)
	rr := httptest.NewRecorder()
	laneByIDHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

// TestWave68_LaneByIDHandler_NotFound verifies laneByIDHandler returns 404 for missing lane.
func TestWave68_LaneByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-lane-wave68", nil)
	rr := httptest.NewRecorder()
	laneByIDHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", rr.Code)
	}
}

// TestWave68_LaneByIDHandler_Found verifies laneByIDHandler returns 200 with lane data.
func TestWave68_LaneByIDHandler_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a lane directly into the DB.
	_, err := db.Exec(`
		INSERT INTO lanes (id, name, description, capabilities, auto_claim, max_parallel, timeout_minutes)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, "wave68-lane-1", "Test Lane", "Testing lane", "go,python", 0, 2, 30)
	if err != nil {
		t.Fatalf("insert lane: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/wave68-lane-1", nil)
	rr := httptest.NewRecorder()
	laneByIDHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	var resp struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.ID != "wave68-lane-1" {
		t.Errorf("expected ID=wave68-lane-1, got %s", resp.ID)
	}
	if resp.Name != "Test Lane" {
		t.Errorf("expected Name=Test Lane, got %s", resp.Name)
	}
}

// TestWave68_LanesHandler_MethodNotAllowed verifies lanesHandler rejects non-GET.
func TestWave68_LanesHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/lanes", nil)
	rr := httptest.NewRecorder()
	lanesHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

// TestWave68_LanesHandler_ReturnsJSON verifies lanesHandler returns 200 with JSON.
func TestWave68_LanesHandler_ReturnsJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	rr := httptest.NewRecorder()
	lanesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}

	var resp struct {
		Count int           `json:"count"`
		Lanes []interface{} `json:"lanes"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	// Migrations may insert default lanes — just verify the response structure is valid.
	if resp.Count < 0 {
		t.Errorf("expected non-negative count, got %d", resp.Count)
	}
}

// TestWave68_GetTask_NotFound verifies GetTask returns ErrTaskNotFound for missing task.
func TestWave68_GetTask_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	_, err = q.GetTask(context.Background(), "task-does-not-exist-wave68")
	if err != ErrTaskNotFound {
		t.Errorf("expected ErrTaskNotFound, got: %v", err)
	}
}

// TestWave68_GetTask_WithAllFields verifies GetTask returns optional fields correctly.
// Note: GetTask's SELECT does not include title — only verifiable fields are tested.
func TestWave68_GetTask_WithAllFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	task := Task{
		ID:          "wave68-full-task",
		Domain:      "backend",
		Project:     "forge-v3",
		Type:        TaskTypeFeature,
		Title:       "Full field test task",
		Priority:    7,
		Status:      TaskStatusQueued,
		PlanVersion: 2,
		PlanID:      "plan-abc",
		EnvelopeID:  "env-xyz",
	}
	if err := q.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	got, err := q.GetTask(context.Background(), "wave68-full-task")
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Domain != "backend" {
		t.Errorf("Domain mismatch: got %q", got.Domain)
	}
	if got.PlanID != "plan-abc" {
		t.Errorf("PlanID mismatch: got %q", got.PlanID)
	}
	if got.EnvelopeID != "env-xyz" {
		t.Errorf("EnvelopeID mismatch: got %q", got.EnvelopeID)
	}
	if got.PlanVersion != 2 {
		t.Errorf("PlanVersion mismatch: got %d", got.PlanVersion)
	}
	if got.Priority != 7 {
		t.Errorf("Priority mismatch: got %d", got.Priority)
	}
}

// TestWave68_GetTask_AfterComplete verifies GetTask returns result and completed status.
func TestWave68_GetTask_AfterComplete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	task := Task{
		ID:       "wave68-complete-task",
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.Complete(context.Background(), "wave68-complete-task", "done successfully"); err != nil {
		t.Fatalf("Complete: %v", err)
	}

	got, err := q.GetTask(context.Background(), "wave68-complete-task")
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Status != TaskStatusCompleted {
		t.Errorf("expected status=completed, got %s", got.Status)
	}
	if got.Result != "done successfully" {
		t.Errorf("expected result='done successfully', got %q", got.Result)
	}
}

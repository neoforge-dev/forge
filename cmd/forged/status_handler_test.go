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

// TestUpdateTaskStatus_Valid verifies that a normal status update succeeds and
// persists the mapped status and assigned worker.
func TestUpdateTaskStatus_Valid(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "uts-valid-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus(task.ID, "completed", "agent-1"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	updated, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if updated.Status != TaskStatusCompleted {
		t.Errorf("status = %s, want %s", updated.Status, TaskStatusCompleted)
	}
	if updated.AssignedTo != "agent-1" {
		t.Errorf("assigned_to = %s, want agent-1", updated.AssignedTo)
	}
}

// TestUpdateTaskStatus_InvalidStatus documents current behaviour for arbitrary
// status strings: they are stored as-is and do not error.
func TestUpdateTaskStatus_InvalidStatus(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "uts-invalid-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus(task.ID, "!!invalid-status!!", "agent-x"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	updated, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if updated.Status != TaskStatus("!!invalid-status!!") {
		t.Errorf("status = %s, want %s", updated.Status, "!!invalid-status!!")
	}
}

// TestUpdateTaskStatus_NotFound verifies behaviour when the task ID does not
// exist: the method currently returns nil and does not create a new task row.
func TestUpdateTaskStatus_NotFound(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus("missing-task-id", "completed", "agent-x"); err != nil {
		t.Fatalf("unexpected error for missing task id: %v", err)
	}

	// Ensure task was not created implicitly.
	if _, err := q.GetTask(context.Background(), "missing-task-id"); err == nil {
		t.Fatalf("expected GetTask to fail for missing task id")
	}
}

// TestStatusHandler_OK exercises the /api/status handler and validates the
// static payload.
func TestStatusHandler_OK(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	rr := httptest.NewRecorder()

	statusHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body: %s", rr.Code, http.StatusOK, rr.Body.String())
	}

	var resp StatusResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if resp.Status != "running" {
		t.Errorf("Status = %s, want running", resp.Status)
	}
	if resp.Version == "" {
		t.Error("expected non-empty Version")
	}
	if resp.Phase == "" {
		t.Error("expected non-empty Phase")
	}
}

// TestHandleGitAndFleetStatus exercise small CLI status stubs to ensure they
// return the expected NotImplemented status code.
func TestHandleGitAndFleetStatus_NotImplemented(t *testing.T) {
	for name, fn := range map[string]func(http.ResponseWriter, *http.Request){
		"git":   handleGitStatus,
		"fleet": handleFleetStatus,
	} {
		t.Run(name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/cli/"+name+"/status", nil)
			rr := httptest.NewRecorder()

			fn(rr, req)

			if rr.Code != http.StatusNotImplemented {
				t.Fatalf("status = %d, want %d; body: %s", rr.Code, http.StatusNotImplemented, rr.Body.String())
			}
		})
	}
}


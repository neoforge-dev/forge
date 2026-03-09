//go:build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// setupUpdateDeleteTest initializes a test database with a sample task
func setupUpdateDeleteTest(t *testing.T) (taskID string, cleanup func()) {
	dbPath := "/tmp/test_update_delete_" + time.Now().Format("20060102150405.999999999") + ".db"

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("failed to run migrations: %v", err)
	}

	// Save originals
	oldDB := getDBConn()
	oldQueue := taskQueue
	oldAuth := authManager
	oldHub := hub

	setDBConn(db)
	authManager = NewAuthManager("local")

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		authManager = oldAuth
		os.Remove(dbPath)
		t.Fatalf("failed to create task queue: %v", err)
	}
	taskQueue = q

	hub = NewHub()
	go hub.Run()

	// Create a test task
	task := Task{
		ID:       "test-update-task-001",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Title:    "Original Title",
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		authManager = oldAuth
		hub = oldHub
		os.Remove(dbPath)
		t.Fatalf("failed to create test task: %v", err)
	}

	cleanup = func() {
		hub.Stop()
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		authManager = oldAuth
		hub = oldHub
		os.Remove(dbPath)
	}

	return task.ID, cleanup
}

// TestTaskUpdateHandler_MissingTaskID tests that missing task ID returns 400
// (gap not covered by handlers_test.go)
func TestTaskUpdateHandler_MissingTaskID(t *testing.T) {
	_, cleanup := setupUpdateDeleteTest(t)
	defer cleanup()

	payload := map[string]interface{}{"status": "done"}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPut, "/api/tasks/", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	taskUpdateHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status %d, got %d: %s", http.StatusBadRequest, w.Code, w.Body.String())
	}
}

// TestTaskUpdateHandler_TaskNotFound tests that non-existent task returns 404
// (gap not covered by handlers_test.go)
func TestTaskUpdateHandler_TaskNotFound(t *testing.T) {
	_, cleanup := setupUpdateDeleteTest(t)
	defer cleanup()

	payload := map[string]interface{}{"status": "done"}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPut, "/api/tasks/nonexistent-task-id", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	taskUpdateHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected status %d, got %d: %s", http.StatusNotFound, w.Code, w.Body.String())
	}
}

// TestTaskUpdateHandler_UpdateBothFields tests updating both status and assigned_to
// (gap not covered by handlers_test.go)
func TestTaskUpdateHandler_UpdateBothFields(t *testing.T) {
	taskID, cleanup := setupUpdateDeleteTest(t)
	defer cleanup()

	payload := map[string]interface{}{
		"status":      "completed",
		"assigned_to": "test-agent-007",
	}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPut, "/api/tasks/"+taskID, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	taskUpdateHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d: %s", http.StatusOK, w.Code, w.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to parse response: %v", err)
	}

	if response["status"] != "completed" {
		t.Errorf("expected status 'completed', got %v", response["status"])
	}
	if response["assigned_to"] != "test-agent-007" {
		t.Errorf("expected assigned_to 'test-agent-007', got %v", response["assigned_to"])
	}
}

// TestTaskDeleteHandler_MissingTaskID tests that missing task ID returns 400
// (gap not covered by handlers_test.go)
func TestTaskDeleteHandler_MissingTaskID(t *testing.T) {
	_, cleanup := setupUpdateDeleteTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/", nil)
	w := httptest.NewRecorder()

	taskDeleteHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status %d, got %d: %s", http.StatusBadRequest, w.Code, w.Body.String())
	}
}

// TestTaskDeleteHandler_VerifiesSoftDelete verifies deletion is soft (status=deleted, task still exists)
// (gap not covered by handlers_test.go)
func TestTaskDeleteHandler_VerifiesSoftDelete(t *testing.T) {
	taskID, cleanup := setupUpdateDeleteTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/"+taskID, nil)
	w := httptest.NewRecorder()

	taskDeleteHandler(w, req)

	if w.Code != http.StatusNoContent {
		t.Errorf("expected status %d, got %d: %s", http.StatusNoContent, w.Code, w.Body.String())
	}

	// Verify task still exists with status "deleted"
	task, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("task should still exist after soft delete: %v", err)
	}
	if task.Status != "deleted" {
		t.Errorf("expected status 'deleted', got %v", task.Status)
	}
}

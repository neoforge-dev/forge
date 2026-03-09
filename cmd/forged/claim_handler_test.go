//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

func setupClaimTestDB(t *testing.T) (*sql.DB, func()) {
	// Create a temporary SQLite database.
	dbPath := fmt.Sprintf("/tmp/test_claim_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB failed: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp failed: %v", err)
	}

	// Save original global variables to restore after test.
	oldDB := getDBConn()
	setDBConn(db)

	oldQueue := taskQueue
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB failed: %v", err)
	}
	taskQueue = q

	oldHub := hub
	hub = NewHub()

	// Save and replace taskStore and stateMachine so FSM transitions use the test DB.
	oldTaskStore := taskStore
	oldStateMachine := stateMachine
	taskStore = NewTaskStore(db)
	stateMachine = NewStateMachine(taskStore, db)

	cleanup := func() {
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		hub = oldHub
		taskStore = oldTaskStore
		stateMachine = oldStateMachine
		os.Remove(dbPath)
	}

	return db, cleanup
}

func TestClaimTaskHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "test-task-success"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	payload := map[string]string{"agent_id": "forge:gemini"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Errorf("expected status 201, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	updated, _ := taskQueue.GetTask(context.Background(), taskID)
	if updated.Status != TaskStatusAssigned {
		t.Errorf("expected status assigned, got %s", updated.Status)
	}
	if updated.AssignedTo != "forge:gemini" {
		t.Errorf("expected assigned_to %s, got %s", "forge:gemini", updated.AssignedTo)
	}

	var state string
	err := db.QueryRow("SELECT state FROM tasks WHERE id = ?", taskID).Scan(&state)
	if err != nil {
		t.Fatalf("failed to query state: %v", err)
	}
	if state != string(StateDispatched) {
		t.Errorf("expected state %s, got %s", StateDispatched, state)
	}
}

func TestClaimTaskHandler_InvalidMethod(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/123/claim", nil)
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestClaimTaskHandler_TaskNotClaimable(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "test-task-not-claimable"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusCompleted, // Not claimable
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	payload := map[string]string{"agent_id": "forge:gemini"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusConflict {
		t.Errorf("expected status 409, got %d", rr.Code)
	}
}

func TestClaimTaskHandler_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/123/claim", bytes.NewReader([]byte("invalid json")))
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestClaimTaskHandler_AlreadyClaimed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "test-task-already-claimed"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusAssigned, // Already claimed
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Manually set assigned_to because Enqueue might not do it if status is QUEUED by default
	if _, err := getDBConn().Exec("UPDATE tasks SET assigned_to = 'forge:other' WHERE id = ?", taskID); err != nil {
		t.Fatalf("manual update failed: %v", err)
	}

	payload := map[string]string{"agent_id": "forge:gemini"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusConflict {
		t.Errorf("expected status 409, got %d", rr.Code)
	}
}

func TestClaimTaskHandler_TaskNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "non-existent-task"
	payload := map[string]string{"agent_id": "forge:gemini"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}
}

func TestClaimTaskHandler_MissingAgentID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "test-task-no-agent"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	payload := map[string]string{"agent_id": ""}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

// --- releaseTaskHandler ---

func TestReleaseTaskHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "test-task-release"
	agentID := "forge:gemini"
	task := Task{
		ID:         taskID,
		Domain:     "test",
		Project:    "test-proj",
		Type:       TaskTypeFeature,
		Priority:   10,
		Status:     TaskStatusAssigned,
		AssignedTo: agentID,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	payload := map[string]string{"agent_id": agentID, "reason": "test release"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/release", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	releaseTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	updated, _ := taskQueue.GetTask(context.Background(), taskID)
	if updated.Status != TaskStatusQueued {
		t.Errorf("expected status queued, got %s", updated.Status)
	}
	if updated.AssignedTo != "" {
		t.Errorf("expected assigned_to empty, got %s", updated.AssignedTo)
	}
}

func TestReleaseTaskHandler_WrongAgent(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "test-task-release-wrong"
	task := Task{
		ID:         taskID,
		Domain:     "test",
		Project:    "test-proj",
		Type:       TaskTypeFeature,
		Priority:   10,
		Status:     TaskStatusAssigned,
		AssignedTo: "forge:gemini",
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	payload := map[string]string{"agent_id": "forge:other", "reason": "wrong agent"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/release", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	releaseTaskHandler(rr, req)

	if rr.Code != http.StatusForbidden {
		t.Errorf("expected status 403, got %d", rr.Code)
	}
}

func TestUpdateTaskStatus(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	taskID := "test-update-status"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 10,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Test mapping
	cases := []struct {
		inputStatus string
		wantStatus  TaskStatus
	}{
		{"in_progress", TaskStatusExecuting},
		{"completed", TaskStatusCompleted},
		{"failed", TaskStatusFailed},
		{"custom", TaskStatus("custom")},
	}

	for _, tc := range cases {
		err := taskQueue.UpdateTaskStatus(taskID, tc.inputStatus, "forge:gemini")
		if err != nil {
			t.Errorf("UpdateTaskStatus(%s) failed: %v", tc.inputStatus, err)
		}
		updated, _ := taskQueue.GetTask(ctx, taskID)
		if updated.Status != tc.wantStatus {
			t.Errorf("UpdateTaskStatus(%s) got status %s, want %s", tc.inputStatus, updated.Status, tc.wantStatus)
		}
		if updated.AssignedTo != "forge:gemini" {
			t.Errorf("UpdateTaskStatus(%s) got assigned_to %s, want %s", tc.inputStatus, updated.AssignedTo, "forge:gemini")
		}
	}
}

func TestClaimableTasksHandler(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Add a claimable task
	task := Task{
		ID: "claimable-1", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	_ = taskQueue.Enqueue(context.Background(), task)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/claimable?limit=5", nil)
	rr := httptest.NewRecorder()

	claimableTasksHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	var resp struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Count != 1 {
		t.Errorf("expected count 1, got %d", resp.Count)
	}
	if len(resp.Tasks) > 0 && resp.Tasks[0].ID != "claimable-1" {
		t.Errorf("expected task ID claimable-1, got %s", resp.Tasks[0].ID)
	}
}

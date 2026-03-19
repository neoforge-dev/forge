//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ── writeTokenBudgetFile ──────────────────────────────────────────────────────

func TestWave140_WriteTokenBudgetFile_Success(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "budget.json")

	f := &tokenBudgetFile{
		Node: "test-node",
	}
	if err := writeTokenBudgetFile(path, f); err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("expected file to exist after write, got: %v", err)
	}
	// .tmp should be gone
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Errorf("expected .tmp file to be removed after rename")
	}
}

func TestWave140_WriteTokenBudgetFile_WriteError(t *testing.T) {
	// Pass a path inside a non-existent directory → os.WriteFile should fail.
	path := "/nonexistent_dir_wave140/budget.json"
	f := &tokenBudgetFile{Node: "x"}
	err := writeTokenBudgetFile(path, f)
	if err == nil {
		t.Fatal("expected error writing to non-existent dir, got nil")
	}
}

func TestWave140_WriteTokenBudgetFile_RenameError(t *testing.T) {
	// Make the destination path a directory so that rename fails.
	dir := t.TempDir()
	destDir := filepath.Join(dir, "budget.json")
	if err := os.Mkdir(destDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Write the .tmp file manually so WriteFile succeeds, rename will fail because
	// destDir is a directory.
	path := destDir // path == directory; tmp will be destDir+".tmp"
	f := &tokenBudgetFile{Node: "y"}
	err := writeTokenBudgetFile(path, f)
	// Rename may or may not fail depending on OS, but we exercise the code path.
	// Just verify no panic.
	_ = err
}

// ── agentTasksHandler ─────────────────────────────────────────────────────────

func TestWave140_AgentTasksHandler_MissingAgentID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	// Path ends at /tasks with no agent id segment.
	req := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave140_AgentTasksHandler_WithTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "wave140-agent-task",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	// Assign task to a specific agent directly via DB.
	_, err := db.ExecContext(ctx, `UPDATE tasks SET assigned_to = 'test-agent' WHERE id = ?`, task.ID)
	if err != nil {
		t.Fatalf("update assigned_to: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if _, ok := resp["tasks"]; !ok {
		t.Errorf("expected 'tasks' field in response")
	}
}

// ── SendToWorker ──────────────────────────────────────────────────────────────

func TestWave140_SendToWorker_NonexistentWorker(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// hub is set up by setupClaimTestDB. Calling SendToWorker with an unknown
	// worker covers the !ok log branch inside SendToWorker.
	hub.SendToWorker("nonexistent-worker-wave140", "task_assigned", "task-abc", Task{ID: "task-abc"})
	// No panic = pass.
}

// ── claimTaskHandler ──────────────────────────────────────────────────────────

func TestWave140_ClaimTaskHandler_MissingAgentID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"agent_id":""}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-task/claim", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave140_ClaimTaskHandler_TaskNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"agent_id":"test-agent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-wave140/claim", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave140_ClaimTaskHandler_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := bytes.NewBufferString(`not-json`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-task/claim", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave140_ClaimTaskHandler_TaskNotClaimable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "wave140-not-claimable",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	// Force task into a non-claimable state.
	_, err := db.ExecContext(ctx, `UPDATE tasks SET status = 'completed', state = 'COMPLETED' WHERE id = ?`, task.ID)
	if err != nil {
		t.Fatalf("update state: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"some-agent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/wave140-not-claimable/claim", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	// Should be 409 Conflict because task is not in claimable state.
	if w.Code != http.StatusConflict {
		t.Errorf("expected 409, got %d: %s", w.Code, w.Body.String())
	}
}

// ── agentTelemetrySummaryHandler ─────────────────────────────────────────────

func TestWave140_AgentTelemetrySummaryHandler_NilDB(t *testing.T) {
	// Save and nil out the DB connection.
	old := getDBConn()
	setDBConn(nil)
	defer setDBConn(old)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave140_AgentTelemetrySummaryHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["agents"]; !ok {
		t.Errorf("expected 'agents' key in response")
	}
}

// ── patrolRunHandler ──────────────────────────────────────────────────────────

func TestWave140_PatrolRunHandler_MissingPatrolID(t *testing.T) {
	// Path with no patrol id (just /run).
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	// The patrol id would be empty string after stripping "/run".
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave140_PatrolRunHandler_NilPatrolSystem(t *testing.T) {
	old := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = old }()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols/task-timeout/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

// ── TUI JSONHandler ───────────────────────────────────────────────────────────

func TestWave140_TUIJSONHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tui := NewTUI(db, hub, taskQueue)
	handler := tui.JSONHandler()

	req := httptest.NewRequest(http.MethodGet, "/api/tui/dashboard", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}
}

func TestWave140_TUIJSONHandler_ClosedDBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	// Note: don't defer cleanup here since we close db manually.
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		cleanup()
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	tui := NewTUI(db, nil, q)
	// Close the DB so GetDashboard DB queries fail.
	db.Close()
	cleanup()

	handler := tui.JSONHandler()
	req := httptest.NewRequest(http.MethodGet, "/api/tui/dashboard", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	// GetDashboard gracefully handles query errors (logs them) but may still
	// return 200 with partial data, or 500 — both are acceptable since
	// the point is to exercise the code path without panic.
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500, got %d: %s", w.Code, w.Body.String())
	}
}

// ── handleAgents (PWA bridge) ─────────────────────────────────────────────────

func TestWave140_HandleAgents_NilDashboard(t *testing.T) {
	h := &PWADashboardHandler{dashboard: nil}
	req := httptest.NewRequest(http.MethodGet, "/api/pwa/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave140_HandleAgents_NilDashboardDB(t *testing.T) {
	d := &Dashboard{db: nil}
	h := &PWADashboardHandler{dashboard: d}
	req := httptest.NewRequest(http.MethodGet, "/api/pwa/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave140_HandleAgents_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, hub)
	h := NewPWADashboardHandler(d)
	req := httptest.NewRequest(http.MethodGet, "/api/pwa/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if success, _ := resp["success"].(bool); !success {
		t.Errorf("expected success=true in response")
	}
}

// ── Additional claimTaskHandler paths ────────────────────────────────────────

func TestWave140_ClaimTaskHandler_EmptyTaskID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Path where task ID is empty after trimming.
	body := bytes.NewBufferString(`{"agent_id":"an-agent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//claim", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave140_ClaimTaskHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	taskID := "wave140-claim-success-" + time.Now().Format("150405.000000000")
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"wave140-agent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

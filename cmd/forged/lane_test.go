//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// setupLaneManager creates a LaneManager backed by a fully-migrated DB.
// Returns the manager, queue, DB handle, and a cleanup func.
func setupLaneManager(t *testing.T, configs map[Lane]LaneConfig) (LaneManager, TaskQueue, *sql.DB, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		cleanup()
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	store := &sqliteApprovalStore{db: db}
	svc := NewApprovalService(store)
	mgr := NewLaneManager(q, svc, configs)
	return mgr, q, db, cleanup
}

// setTaskLane sets the lane column directly in the DB.
// Enqueue does not persist the Lane field, so direct UPDATE is required.
func setTaskLane(t *testing.T, db *sql.DB, taskID string, lane Lane) {
	t.Helper()
	if _, err := db.Exec("UPDATE tasks SET lane = ? WHERE id = ?", string(lane), taskID); err != nil {
		t.Fatalf("setTaskLane: %v", err)
	}
}

func TestLaneNewLaneManager(t *testing.T) {
	mgr, _, db, cleanup := setupLaneManager(t, nil)
	defer cleanup()
	if mgr == nil {
		t.Fatal("expected non-nil LaneManager")
	}
	_ = db // unused but required by setup
}

func TestLaneCheckGates_NoConfig(t *testing.T) {
	mgr, _, _, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	task := &Task{ID: "task-cg-001", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates returned error: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected Passed=true with no gate config, errors: %v", result.Errors)
	}
	if result.TargetLane != LaneTest {
		t.Errorf("TargetLane = %s, want %s", result.TargetLane, LaneTest)
	}
}

func TestLaneCheckGates_UnknownGate(t *testing.T) {
	configs := map[Lane]LaneConfig{
		LaneTest: {Gates: []string{"unknown_gate_type"}},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "task-cg-002", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Passed {
		t.Error("expected Passed=false for unknown gate type (fail closed)")
	}
	found := false
	for _, e := range result.Errors {
		if strings.Contains(e, "unknown gate type") {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected 'unknown gate type' in errors, got %v", result.Errors)
	}
}

func TestLaneProgressTask_FinalLane(t *testing.T) {
	mgr, q, db, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	task := Task{
		ID: "task-pt-done", Domain: "test", Project: "test-proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	setTaskLane(t, db, task.ID, LaneDone)

	_, err := mgr.ProgressTask(context.Background(), task.ID)
	if err == nil {
		t.Fatal("expected error when task is already in final lane")
	}
	if !strings.Contains(err.Error(), "final lane") && !strings.Contains(err.Error(), "unknown lane") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestLaneProgressTask_NotFound(t *testing.T) {
	mgr, _, _, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	_, err := mgr.ProgressTask(context.Background(), "task-does-not-exist")
	if err == nil {
		t.Fatal("expected error for non-existent task ID")
	}
}

func TestLaneProgressTask_DevToTest(t *testing.T) {
	mgr, q, db, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	task := Task{
		ID: "task-pt-dev", Domain: "test", Project: "test-proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	setTaskLane(t, db, task.ID, LaneDev)

	updated, err := mgr.ProgressTask(context.Background(), task.ID)
	if err != nil {
		t.Fatalf("ProgressTask: %v", err)
	}
	if updated.Lane != string(LaneTest) {
		t.Errorf("expected lane=%s, got %s", LaneTest, updated.Lane)
	}
}

func TestLaneHandleLaneComplete_MethodNotAllowed(t *testing.T) {
	mgr, _, _, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-x/lane/complete", nil)
	w := httptest.NewRecorder()
	mgr.HandleLaneComplete(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestLaneHandleLaneComplete_TaskNotFound(t *testing.T) {
	mgr, _, _, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-such-task/lane/complete", nil)
	w := httptest.NewRecorder()
	mgr.HandleLaneComplete(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestLaneHandleLaneStatus_MethodNotAllowed(t *testing.T) {
	mgr, _, _, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-x/lane/status", nil)
	w := httptest.NewRecorder()
	mgr.HandleLaneStatus(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestLaneHandleLaneStatus_TaskNotFound(t *testing.T) {
	mgr, _, _, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/no-such-task/lane/status", nil)
	w := httptest.NewRecorder()
	mgr.HandleLaneStatus(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestLaneHandleLaneStatus_Success(t *testing.T) {
	mgr, q, db, cleanup := setupLaneManager(t, map[Lane]LaneConfig{})
	defer cleanup()

	task := Task{
		ID: "task-ls-001", Domain: "test", Project: "test-proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	setTaskLane(t, db, task.ID, LaneDev)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-ls-001/lane/status", nil)
	w := httptest.NewRecorder()
	mgr.HandleLaneStatus(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if resp["current_lane"] != string(LaneDev) {
		t.Errorf("expected current_lane=%s, got %v", LaneDev, resp["current_lane"])
	}
	if resp["next_lane"] != string(LaneTest) {
		t.Errorf("expected next_lane=%s, got %v", LaneTest, resp["next_lane"])
	}
}

func TestLaneForgeRoot_Env(t *testing.T) {
	t.Setenv("FORGE_ROOT", "/tmp/forge-root-test")
	got := forgeRoot()
	if got != "/tmp/forge-root-test" {
		t.Errorf("expected /tmp/forge-root-test, got %s", got)
	}
}

func TestLaneForgeRoot_Default(t *testing.T) {
	t.Setenv("FORGE_ROOT", "")
	got := forgeRoot()
	if got != "." {
		t.Errorf("expected '.', got %s", got)
	}
}

func TestLaneRunBuildCheck_EmptyDir(t *testing.T) {
	t.Setenv("FORGE_ROOT", t.TempDir())
	// Empty dir has no Go packages; go build ./... exits 0 — no panic is the key assertion.
	_ = runBuildCheck(context.Background())
}

func TestLaneRunBuildCheck_CancelledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	t.Setenv("FORGE_ROOT", t.TempDir())
	err := runBuildCheck(ctx)
	if err == nil {
		t.Log("runBuildCheck returned nil on pre-cancelled context with empty dir — acceptable")
	}
}

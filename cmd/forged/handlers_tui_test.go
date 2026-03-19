//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestTuiLogsHandler_WithWorker exercises the for-range body over workers
// (handlers_tui.go:46.33,53.3) by injecting a live worker into the hub.
func TestTuiLogsHandler_WithWorker(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	if hub == nil {
		t.Skip("hub not initialized")
	}

	// Inject a fake worker with a fresh heartbeat so ListWorkers() returns it.
	fakeWorker := &WebSocketWorker{
		AgentID: "tui-test-worker-1",
		Node:    "test-node",
		Send:    make(chan WSMessage, 10),
	}
	fakeWorker.touchPong()

	hub.mu.Lock()
	hub.workers["tui-test-worker-1"] = fakeWorker
	hub.mu.Unlock()
	defer func() {
		hub.mu.Lock()
		delete(hub.workers, "tui-test-worker-1")
		hub.mu.Unlock()
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}

	logs, ok := resp["logs"].([]interface{})
	if !ok {
		t.Fatal("response missing 'logs' array")
	}

	// At least one entry should mention our fake agent.
	found := false
	for _, entry := range logs {
		m := entry.(map[string]interface{})
		if msg, _ := m["message"].(string); strings.Contains(msg, "tui-test-worker-1") {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected log entry for tui-test-worker-1, got logs: %v", logs)
	}
}

// TestTuiLogsHandler_LimitTruncation exercises the limit truncation path
// (handlers_tui.go:75.26,77.3). We inject a worker to get >1 log entry,
// then pass limit=1 to force the slice to be truncated.
func TestTuiLogsHandler_LimitTruncation(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	if hub == nil {
		t.Skip("hub not initialized")
	}

	// Inject multiple fake workers so we have >1 agent log entry (plus "Server running").
	for i := 0; i < 3; i++ {
		id := "tui-limit-worker-" + string(rune('a'+i))
		fakeWorker := &WebSocketWorker{
			AgentID: id,
			Node:    "test-node",
			Send:    make(chan WSMessage, 10),
		}
		fakeWorker.touchPong()
		hub.mu.Lock()
		hub.workers[id] = fakeWorker
		hub.mu.Unlock()
	}
	defer func() {
		hub.mu.Lock()
		for i := 0; i < 3; i++ {
			delete(hub.workers, "tui-limit-worker-"+string(rune('a'+i)))
		}
		hub.mu.Unlock()
	}()

	// With limit=1, the entries slice must be truncated to the last 1 entry.
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs?limit=1", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}

	logs, _ := resp["logs"].([]interface{})
	if len(logs) > 1 {
		t.Errorf("expected at most 1 log entry with limit=1, got %d", len(logs))
	}

	limitVal, _ := resp["limit"].(float64)
	if int(limitVal) != 1 {
		t.Errorf("expected limit=1 in response, got %v", limitVal)
	}
}

// TestPlanTaskHandler_CreatePlanError exercises the planManager.CreatePlan
// error path (handlers_tui.go:113.16,116.3) by providing a planManager
// backed by a closed DB.
func TestPlanTaskHandler_CreatePlanError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close the DB immediately to force CreatePlan to fail.
	db.Close()
	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	body := bytes.NewBufferString(`{"plan":"test plan","reason":"test reason"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-task-id/plan", body)
	w := httptest.NewRecorder()
	planTaskHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed DB planManager, got %d: %s", w.Code, w.Body.String())
	}
}

// TestGetPlanHistoryHandler_DBError exercises the planManager.GetPlanHistory
// error path (handlers_tui.go:160.16,163.3) by providing a planManager
// backed by a closed DB.
func TestGetPlanHistoryHandler_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close the DB immediately to force GetPlanHistory to fail.
	db.Close()
	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/some-task-id/plans", nil)
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed DB planManager, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTuiLogsHandler_LimitOverCap verifies that limit values above 100 are
// clamped to 100 (handlers_tui.go:29.19,31.5).
func TestTuiLogsHandler_LimitOverCap(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs?limit=999", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	limitVal, _ := resp["limit"].(float64)
	if int(limitVal) != 100 {
		t.Errorf("expected limit clamped to 100, got %v", limitVal)
	}
}

// TestReplanTaskHandler_RevisePlanError exercises the planManager.RevisePlan
// error path (handlers_tui.go:142.16,145.3) by providing a planManager
// backed by a closed DB.
func TestReplanTaskHandler_RevisePlanError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close the DB immediately to force RevisePlan to fail.
	db.Close()
	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	body := bytes.NewBufferString(`{"plan":"revised plan","reason":"replan reason"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-task-id/replan", body)
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed DB planManager, got %d: %s", w.Code, w.Body.String())
	}
}

// ensure time import is used
var _ = time.Now

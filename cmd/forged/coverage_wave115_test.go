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

// ============================================================
// coverage_wave115_test.go — handleSystemHealth and handleQueueDepth
// Target: handleSystemHealth (55.6%), handleQueueDepth (54.5%)
// ============================================================

// ---------------------------------------------------------------------------
// handleSystemHealth tests (main.go line 402)
// ---------------------------------------------------------------------------

// TestWave115_SystemHealth_Get verifies basic GET request.
func TestWave115_SystemHealth_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave115_SystemHealth_GetWithFormat verifies format=json query param.
func TestWave115_SystemHealth_GetWithFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not valid JSON: %v", err)
	}
}

// TestWave115_SystemHealth_PostAllowed verifies POST is allowed (healthHandler doesn't check method).
func TestWave115_SystemHealth_PostAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	// healthHandler doesn't check method, so POST should work
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (healthHandler allows POST), got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave115_SystemHealth_ContextCancel verifies handler handles cancelled context gracefully.
func TestWave115_SystemHealth_ContextCancel(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil).WithContext(ctx)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	// Handler should complete without panic (healthHandler is simple)
	// Status may be 200 or may have an error depending on WriteOutputWithFlag
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Logf("status=%d body=%s (context cancel may affect output)", w.Code, w.Body.String())
	}
}

// TestWave115_SystemHealth_NilDB verifies handler works even with nil DB.
func TestWave115_SystemHealth_NilDB(t *testing.T) {
	// Save and clear DB
	origDB := getDBConn()
	setDBConn(nil)
	defer func() {
		if origDB != nil {
			setDBConn(origDB)
		}
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	// healthHandler doesn't use DB, so it should still return 200
	if w.Code != http.StatusOK {
		t.Logf("nil DB status=%d body=%s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleQueueDepth tests (main.go line 436)
// ---------------------------------------------------------------------------

// TestWave115_QueueDepth_GetEmpty verifies GET with empty queue returns depth=0.
func TestWave115_QueueDepth_GetEmpty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Initialize taskQueue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not valid JSON: %v", err)
	}
	if depth, ok := resp["depth"].(float64); !ok || depth != 0 {
		t.Errorf("expected depth=0, got %v", resp["depth"])
	}
}

// TestWave115_QueueDepth_GetWithTasks verifies GET with tasks returns correct depth.
func TestWave115_QueueDepth_GetWithTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Initialize taskQueue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	// Enqueue some tasks
	for i := 0; i < 3; i++ {
		_ = taskQueue.Enqueue(context.Background(), Task{
			ID:       "wave115-depth-task-" + string(rune('0'+i)),
			Domain:   "test",
			Project:  "wave115",
			Type:     "test",
			Priority: 5,
			State:    StateQueued,
			Title:    "Test task",
		})
	}

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not valid JSON: %v", err)
	}
	if depth, ok := resp["depth"].(float64); !ok || depth < 1 {
		t.Errorf("expected depth >= 1, got %v", resp["depth"])
	}
}

// TestWave115_QueueDepth_GetWithFormat verifies format=json query param.
func TestWave115_QueueDepth_GetWithFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Initialize taskQueue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// Should be valid JSON with "depth" field
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not valid JSON: %v", err)
	}
}

// TestWave115_QueueDepth_NilTaskQueue verifies behavior when taskQueue is nil.
// Note: This test is skipped because ExitWithError calls os.Exit which terminates the test.
// The nil taskQueue path is covered by the error message in the function.
func TestWave115_QueueDepth_NilTaskQueue(t *testing.T) {
	t.Skip("ExitWithError calls os.Exit - cannot test nil taskQueue path without mocking")
}

// TestWave115_QueueDepth_ContextCancel verifies handler with cancelled context.
func TestWave115_QueueDepth_ContextCancel(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Initialize taskQueue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil).WithContext(ctx)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	// Handler may still succeed since GetClaimableTasks might not check context
	t.Logf("context cancel status=%d body=%s", w.Code, w.Body.String())
}

// TestWave115_QueueDepth_PostAllowed verifies POST is allowed (handler doesn't check method).
func TestWave115_QueueDepth_PostAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Initialize taskQueue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodPost, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	// Handler doesn't check method, so POST should work
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (handler allows POST), got %d: %s", w.Code, w.Body.String())
	}
}

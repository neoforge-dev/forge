//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave244_test.go — claimTaskHandler, announceNodeToPeer, writeEvent
// ============================================================

// --- claimTaskHandler ---

func TestWave244_ClaimTaskHandler_EmptyTaskID(t *testing.T) {
	body := bytes.NewBufferString(`{"agent_id":"agent-244"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", w.Code)
	}
}

func TestWave244_ClaimTaskHandler_InvalidJSON(t *testing.T) {
	body := bytes.NewBufferString(`{not json}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-244/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

func TestWave244_ClaimTaskHandler_MissingAgentID(t *testing.T) {
	body := bytes.NewBufferString(`{"agent_id":""}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-244/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent_id, got %d", w.Code)
	}
}

func TestWave244_ClaimTaskHandler_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	body := bytes.NewBufferString(`{"agent_id":"agent-244"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-244/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent task, got %d: %s", w.Code, w.Body.String())
	}
}

// --- announceNodeToPeer ---

func TestWave244_AnnounceNodeToPeer_Success(t *testing.T) {
	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	self := Node{ID: "node-244", Hostname: "prya", Address: srv.URL, Status: "online"}
	announceNodeToPeer(srv.URL, self)
	if !called {
		t.Error("expected peer handler to be called")
	}
}

func TestWave244_AnnounceNodeToPeer_UnreachableServer(t *testing.T) {
	// Port 1 is unreachable — covers the Do error path
	self := Node{ID: "node-244-err", Hostname: "prya", Address: "http://127.0.0.1:1", Status: "online"}
	// Should log and return without panic
	announceNodeToPeer("http://127.0.0.1:1", self)
}

func TestWave244_AnnounceNodeToPeer_InvalidURL(t *testing.T) {
	self := Node{ID: "node-244-bad", Hostname: "prya", Status: "online"}
	// Bad URL scheme should cause NewRequestWithContext to fail
	announceNodeToPeer("://bad-url", self)
}

// --- writeEvent (via sqliteTaskQueue) ---

func TestWave244_WriteEvent_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	// writeEvent is best-effort (silently ignores errors)
	sq.writeEvent(ctx, "task-244-evt", "test-domain", "test-project", "test.event",
		map[string]interface{}{"key": "value"})
	// No assertion needed — just verifying no panic and the call completes
}

func TestWave244_WriteEvent_WithNilPayload(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()
	sq.writeEvent(ctx, "task-244-nil", "domain", "project", "event.nil", nil)
}

// --- handleQueueStatus ---

func TestWave244_HandleQueueStatus_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&result); err != nil {
		t.Errorf("invalid JSON response: %v", err)
	}
}

func TestWave244_HandleQueueStatus_NilQueue(t *testing.T) {
	origQueue := taskQueue
	taskQueue = nil
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// --- createTaskHandler validation paths ---

func TestWave244_ClaimTask_WhitespaceAgentID(t *testing.T) {
	body := bytes.NewBufferString(`{"agent_id":"   "}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-244-ws/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for whitespace agent_id, got %d", w.Code)
	}
}

// --- handleQueueDepth with format param ---

func TestWave244_HandleQueueDepth_JsonFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

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
		t.Errorf("expected 200, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "depth") {
		t.Errorf("expected 'depth' in response: %s", w.Body.String())
	}
}

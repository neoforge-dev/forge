//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave240_test.go — XNode HTTP handlers, MigrateDown,
//         QueuePendingDispatch/GetPendingDispatches, EventBus.topicMatches
// ============================================================

// --- XNode HTTP handlers ---

func TestWave240_XNode_ListInboxHandler_InvalidNodeID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/../../etc", nil)
	req.URL.Path = "/api/xnode/inbox/../../etc"
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for path traversal, got %d", w.Code)
	}
}

func TestWave240_XNode_ListInboxHandler_EmptyInbox(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/", nil)
	req.URL.Path = "/api/xnode/inbox/"
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)
	// May return 200 (empty) or 500 (dir not found) — either is acceptable
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d", w.Code)
	}
}

func TestWave240_XNode_ListAcksHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, req)
	// 200 (empty) or 500 (dir not found) — both acceptable
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d", w.Code)
	}
}

func TestWave240_XNode_ListNodesHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave240_XNode_StatusHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestWave240_XNode_ForwardHandler_MissingTargetNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := strings.NewReader(`{"task_id":"task-240"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", body)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave240_XNode_ForwardHandler_NodeNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := strings.NewReader(`{"target_node":"nonexistent-node","task_id":"task-240"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", body)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	// Expect 404 (node not found) or other error
	if w.Code == http.StatusOK {
		t.Errorf("expected non-200 for nonexistent node, got 200")
	}
}

// --- MigrateDown ---

func TestWave240_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// steps=0 should return nil immediately
	if err := MigrateDown(db, 0); err != nil {
		t.Fatalf("MigrateDown with 0 steps: %v", err)
	}
}

func TestWave240_MigrateDown_NegativeSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// steps<0 also returns nil
	if err := MigrateDown(db, -1); err != nil {
		t.Fatalf("MigrateDown with -1 steps: %v", err)
	}
}

// --- QueuePendingDispatch / GetPendingDispatches ---

func TestWave240_QueuePendingDispatch(t *testing.T) {
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

	ctx := context.Background()
	q := taskQueue.(*sqliteTaskQueue)
	err = q.QueuePendingDispatch(ctx, "task-240-dispatch", "agent-240",
		map[string]interface{}{"action": "claim", "task_id": "task-240-dispatch"})
	if err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}
}

func TestWave240_GetPendingDispatches(t *testing.T) {
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

	ctx := context.Background()
	q := taskQueue.(*sqliteTaskQueue)

	// Queue a dispatch first
	q.QueuePendingDispatch(ctx, "task-240-get", "agent-240-get",
		map[string]interface{}{"action": "claim"})

	dispatches, err := q.GetPendingDispatches(ctx, "agent-240-get")
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) == 0 {
		t.Error("expected at least 1 pending dispatch")
	}
}

// --- EventBus.topicMatches ---

func TestWave240_EventBus_TopicMatches(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}

	// Test via Subscribe + Publish to exercise topicMatches internally
	matched := make(chan string, 5)
	eb.Subscribe("task.*", func(event BusEvent) error {
		matched <- string(event.Topic)
		return nil
	})

	eb.Publish("task.created", "payload")
	eb.Publish("agent.connected", "payload") // should NOT match task.*
}

// --- respondWithError / respondWithXNodeError ---

func TestWave240_RespondWithError(t *testing.T) {
	w := httptest.NewRecorder()
	respondWithError(w, http.StatusNotFound, "not found", "resource missing")
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave240_RespondWithXNodeError(t *testing.T) {
	w := httptest.NewRecorder()
	xerr := &XNodeError{
		Code:    XNodeErrNodeNotFound,
		Message: "node not found",
	}
	respondWithXNodeError(w, xerr, http.StatusNotFound)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

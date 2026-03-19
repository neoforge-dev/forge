//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 49: XNodeController paths, handleQueueDepth, openclawEventsHandler,
//          CoordinationDashboard ServeHTTP

// ─── NewXNodeController ───────────────────────────────────────────────────

func TestNewXNodeController_NilDB_W49(t *testing.T) {
	_, err := NewXNodeController(nil, "test-node")
	if err == nil {
		t.Error("expected error for nil DB, got nil")
	}
}

func TestNewXNodeController_WithDB_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	if xc == nil {
		t.Error("expected non-nil controller")
	}
}

func TestNewXNodeController_EmptyNodeID_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty nodeID → uses hostname (no error expected).
	xc, err := NewXNodeController(db, "")
	if err != nil {
		t.Fatalf("NewXNodeController with empty nodeID: %v", err)
	}
	if xc == nil {
		t.Error("expected non-nil controller")
	}
}

// ─── XNodeController.ListNodesHandler ────────────────────────────────────

func TestXNodeListNodesHandler_Empty_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
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

func TestXNodeListNodesHandler_WithData_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_, _ = db.ExecContext(ctx, `
		INSERT INTO nodes (id, hostname, address, status, last_heartbeat)
		VALUES ('node-w49', 'test-host', 'http://localhost:8081', 'online', datetime('now'))
	`)

	xc, err := NewXNodeController(db, "test-node-w49")
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

// ─── XNodeController.StatusHandler ───────────────────────────────────────

func TestXNodeStatusHandler_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── XNodeController.ForwardHandler ──────────────────────────────────────

func TestXNodeForwardHandler_MissingTargetNode_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body, _ := json.Marshal(map[string]string{"task_id": "TASK-W49"})
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", bytes.NewReader(body))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_MissingTaskID_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body, _ := json.Marshal(map[string]string{"target_node": "sati"})
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", bytes.NewReader(body))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_InvalidJSON_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// ─── handleQueueDepth ─────────────────────────────────────────────────────

func TestHandleQueueDepth_WithQueue_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueDepth_FormatText_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth?format=text", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── CoordinationDashboard ServeHTTP ─────────────────────────────────────

func TestCoordinationDashboard_ServeHTTP_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	cd.ServeHTTP(w, req)
	// Should serve HTML or redirect — just no crash.
	_ = w.Code
}

func TestCoordinationDashboard_ServeHTTP_APIPath_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)

	req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	w := httptest.NewRecorder()
	cd.ServeHTTP(w, req)
	_ = w.Code
}

// ─── openclawEventsHandler ────────────────────────────────────────────────

func TestOpenclawEventsHandler_SSE_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// SSE handler loops until context cancelled — use immediate cancellation.
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately so the SSE loop exits after initial message

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	req = req.WithContext(ctx)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	// httptest.ResponseRecorder doesn't implement http.Flusher → 500 expected,
	// or handler exits via non-flusher path.
	_ = w.Code
}

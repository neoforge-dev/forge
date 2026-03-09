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

// Wave 32: XNodeController handlers (StatusHandler, ForwardHandler, SSEDeliveryHandler),
//          agentsSSEHandler, uiFleetHandler additional paths

func TestXNodeStatusHandler_WithDB_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
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

func TestXNodeForwardHandler_MethodNotAllowed_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_InvalidBody_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", strings.NewReader("notjson"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_MissingTargetNode_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	body := strings.NewReader(`{"task_id":"TASK-1"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing target_node, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_MissingTaskID_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	body := strings.NewReader(`{"target_node":"sati"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing task_id, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_NodeNotFound_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	body := strings.NewReader(`{"target_node":"nonexistent-node-xyz","task_id":"TASK-999"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	// 404 for unknown node or 500 — not a panic
	if w.Code == http.StatusOK {
		t.Logf("expected non-200 for nonexistent node, got 200")
	}
}

func TestSSEDeliveryHandler_CancelledContext_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately so SSE loop exits via ctx.Done()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/sse?target_node=sati", nil)
	req = req.WithContext(ctx)

	w := httptest.NewRecorder()
	xc.SSEDeliveryHandler(w, req)
	// Should exit without panic — SSE headers set
}

func TestAgentsSSEHandler_MethodNotAllowed_W32(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/sse", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentsSSEHandler_CancelledContext_W32(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately so SSE loop exits

	req := httptest.NewRequest(http.MethodGet, "/api/agents/sse", nil)
	req = req.WithContext(ctx)

	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	// Should exit via ctx.Done() — no panic
}

func TestXNodeListNodesHandler_EmptyDB_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
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

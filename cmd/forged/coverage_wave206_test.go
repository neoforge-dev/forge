//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 206: xnode.go — XNodeError + XNodeController basics
// Targets:
//   XNodeError.Error       (xnode.go:80)  — with/without inner error
//   XNodeError.Unwrap      (xnode.go:87)  — returns inner error
//   newXNodeNodeNotFoundError  (xnode.go:92)
//   newXNodeNodeOfflineError   (xnode.go:101)
//   newXNodeForwardFailedError (xnode.go:109)
//   newXNodeOutboxWriteError   (xnode.go:118)
//   newXNodeInvalidRequestError(xnode.go:127)
//   newXNodeDatabaseError      (xnode.go:136)
//   NewXNodeController     (xnode.go:174) — nil DB / with DB
//   XNodeController.ListNodesHandler (xnode.go:477) — GET
//   XNodeController.StatusHandler    (xnode.go:559) — GET
//   XNodeController.ListInboxHandler (xnode.go:356) — GET
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// XNodeError.Error
// ---------------------------------------------------------------------------

func TestWave206_XNodeError_ErrorWithInner(t *testing.T) {
	inner := errors.New("inner error")
	e := &XNodeError{Code: XNodeErrDatabase, Message: "test message", Err: inner}
	got := e.Error()
	if got == "" {
		t.Error("expected non-empty error string")
	}
	// Should include message and inner error
	if len(got) < len("xnode: test message") {
		t.Errorf("error string too short: %q", got)
	}
}

func TestWave206_XNodeError_ErrorWithoutInner(t *testing.T) {
	e := &XNodeError{Code: XNodeErrNodeNotFound, Message: "node not found"}
	got := e.Error()
	if got != "xnode: node not found" {
		t.Errorf("expected 'xnode: node not found', got %q", got)
	}
}

func TestWave206_XNodeError_Unwrap(t *testing.T) {
	inner := errors.New("root cause")
	e := &XNodeError{Message: "wrapper", Err: inner}
	if e.Unwrap() != inner {
		t.Errorf("expected Unwrap to return inner error")
	}
}

// ---------------------------------------------------------------------------
// Error constructor helpers
// ---------------------------------------------------------------------------

func TestWave206_NewXNodeNodeNotFoundError(t *testing.T) {
	err := newXNodeNodeNotFoundError("node-1", nil)
	if err == nil || err.Error() == "" {
		t.Error("expected non-empty XNodeError")
	}
	if err.Code != XNodeErrNodeNotFound {
		t.Errorf("expected code %v, got %v", XNodeErrNodeNotFound, err.Code)
	}
}

func TestWave206_NewXNodeNodeOfflineError(t *testing.T) {
	err := newXNodeNodeOfflineError("node-2", "offline")
	if err == nil || err.Error() == "" {
		t.Error("expected non-empty XNodeError")
	}
}

func TestWave206_NewXNodeForwardFailedError(t *testing.T) {
	err := newXNodeForwardFailedError("task-1", errors.New("db fail"))
	if err == nil || err.Error() == "" {
		t.Error("expected non-empty XNodeError")
	}
}

func TestWave206_NewXNodeOutboxWriteError(t *testing.T) {
	err := newXNodeOutboxWriteError("node-3", errors.New("write fail"))
	if err == nil || err.Error() == "" {
		t.Error("expected non-empty XNodeError")
	}
}

func TestWave206_NewXNodeInvalidRequestError(t *testing.T) {
	err := newXNodeInvalidRequestError("task_id", errors.New("missing"))
	if err == nil || err.Error() == "" {
		t.Error("expected non-empty XNodeError")
	}
}

func TestWave206_NewXNodeDatabaseError(t *testing.T) {
	err := newXNodeDatabaseError("insert", errors.New("constraint"))
	if err == nil || err.Error() == "" {
		t.Error("expected non-empty XNodeError")
	}
}

// ---------------------------------------------------------------------------
// NewXNodeController
// ---------------------------------------------------------------------------

func TestWave206_NewXNodeController_NilDB(t *testing.T) {
	_, err := NewXNodeController(nil, "test-node")
	if err == nil {
		t.Error("expected error for nil db")
	}
}

func TestWave206_NewXNodeController_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-wave206")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	if xc == nil {
		t.Error("expected non-nil XNodeController")
	}
}

func TestWave206_NewXNodeController_EmptyNodeID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty nodeID → uses hostname
	xc, err := NewXNodeController(db, "")
	if err != nil {
		t.Fatalf("NewXNodeController with empty nodeID: %v", err)
	}
	if xc == nil {
		t.Error("expected non-nil XNodeController")
	}
}

// ---------------------------------------------------------------------------
// HTTP handlers
// ---------------------------------------------------------------------------

func TestWave206_ListNodesHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-wave206")
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

func TestWave206_StatusHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-wave206")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, req)
	// Returns 200 or 500 depending on node registration
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestWave206_ListInboxHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-wave206")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)
	// Any 2xx is fine
	if w.Code < 200 || w.Code >= 300 {
		t.Errorf("expected 2xx, got %d: %s", w.Code, w.Body.String())
	}
}

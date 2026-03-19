//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"errors"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 164: xnode.go — XNodeError constructors + Error/Unwrap methods
// Targets:
//   XNodeError.Error()               (xnode.go:80) — with/without wrapped error
//   XNodeError.Unwrap()              (xnode.go:87) — returns inner error
//   newXNodeNodeNotFoundError        (xnode.go:92)
//   newXNodeNodeOfflineError         (xnode.go:101)
//   newXNodeForwardFailedError       (xnode.go:109)
//   newXNodeOutboxWriteError         (xnode.go:118)
//   newXNodeInvalidRequestError      (xnode.go:127)
//   newXNodeDatabaseError            (xnode.go:136)
//   NewXNodeController               (xnode.go:174) — nil DB→error, empty nodeID
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// XNodeError.Error / Unwrap
// ---------------------------------------------------------------------------

func TestWave164_XNodeError_Error_WithWrappedErr(t *testing.T) {
	inner := errors.New("db closed")
	e := &XNodeError{
		Code:    XNodeErrDatabase,
		Message: "test message",
		Err:     inner,
	}
	got := e.Error()
	if !strings.Contains(got, "test message") || !strings.Contains(got, "db closed") {
		t.Errorf("unexpected Error() output: %q", got)
	}
}

func TestWave164_XNodeError_Error_NoWrappedErr(t *testing.T) {
	e := &XNodeError{
		Code:    XNodeErrNodeNotFound,
		Message: "node gone",
	}
	got := e.Error()
	if !strings.Contains(got, "node gone") {
		t.Errorf("unexpected Error() output: %q", got)
	}
}

func TestWave164_XNodeError_Unwrap(t *testing.T) {
	inner := errors.New("inner error")
	e := &XNodeError{Err: inner}
	if e.Unwrap() != inner {
		t.Error("Unwrap() should return inner error")
	}
}

func TestWave164_XNodeError_Unwrap_Nil(t *testing.T) {
	e := &XNodeError{}
	if e.Unwrap() != nil {
		t.Error("Unwrap() should return nil when no inner error")
	}
}

// ---------------------------------------------------------------------------
// newXNode* error constructors
// ---------------------------------------------------------------------------

func TestWave164_NewXNodeNodeNotFoundError(t *testing.T) {
	err := newXNodeNodeNotFoundError("sati", errors.New("lookup failed"))
	if err.Code != XNodeErrNodeNotFound {
		t.Errorf("expected code %s, got %s", XNodeErrNodeNotFound, err.Code)
	}
	if !strings.Contains(err.Message, "sati") {
		t.Errorf("expected nodeID in message, got %q", err.Message)
	}
	if err.Unwrap() == nil {
		t.Error("expected wrapped error")
	}
}

func TestWave164_NewXNodeNodeOfflineError(t *testing.T) {
	err := newXNodeNodeOfflineError("nova", "unreachable")
	if err.Code != XNodeErrNodeOffline {
		t.Errorf("expected code %s, got %s", XNodeErrNodeOffline, err.Code)
	}
	if !strings.Contains(err.Message, "nova") || !strings.Contains(err.Message, "unreachable") {
		t.Errorf("unexpected message: %q", err.Message)
	}
	if err.Unwrap() != nil {
		t.Error("offline error should not wrap an error")
	}
}

func TestWave164_NewXNodeForwardFailedError(t *testing.T) {
	err := newXNodeForwardFailedError("task-123", errors.New("db write"))
	if err.Code != XNodeErrForwardFailed {
		t.Errorf("expected code %s, got %s", XNodeErrForwardFailed, err.Code)
	}
	if !strings.Contains(err.Message, "task-123") {
		t.Errorf("expected taskID in message, got %q", err.Message)
	}
}

func TestWave164_NewXNodeOutboxWriteError(t *testing.T) {
	err := newXNodeOutboxWriteError("vega", errors.New("fs error"))
	if err.Code != XNodeErrOutboxWrite {
		t.Errorf("expected code %s, got %s", XNodeErrOutboxWrite, err.Code)
	}
	if !strings.Contains(err.Message, "vega") {
		t.Errorf("expected nodeID in message, got %q", err.Message)
	}
}

func TestWave164_NewXNodeInvalidRequestError(t *testing.T) {
	err := newXNodeInvalidRequestError("target_node", errors.New("missing"))
	if err.Code != XNodeErrInvalidRequest {
		t.Errorf("expected code %s, got %s", XNodeErrInvalidRequest, err.Code)
	}
	if !strings.Contains(err.Message, "target_node") {
		t.Errorf("expected field in message, got %q", err.Message)
	}
}

func TestWave164_NewXNodeDatabaseError(t *testing.T) {
	err := newXNodeDatabaseError("insert", errors.New("constraint"))
	if err.Code != XNodeErrDatabase {
		t.Errorf("expected code %s, got %s", XNodeErrDatabase, err.Code)
	}
	if !strings.Contains(err.Message, "insert") {
		t.Errorf("expected operation in message, got %q", err.Message)
	}
}

// ---------------------------------------------------------------------------
// NewXNodeController
// ---------------------------------------------------------------------------

func TestWave164_NewXNodeController_NilDB(t *testing.T) {
	_, err := NewXNodeController(nil, "prya")
	if err == nil {
		t.Error("expected error for nil DB")
	}
}

func TestWave164_NewXNodeController_ValidDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if xc == nil {
		t.Error("expected non-nil controller")
	}
}

func TestWave164_NewXNodeController_EmptyNodeID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	// Empty nodeID falls back to hostname — should not error
	xc, err := NewXNodeController(db, "")
	if err != nil {
		t.Fatalf("unexpected error for empty nodeID: %v", err)
	}
	if xc == nil {
		t.Error("expected non-nil controller")
	}
}

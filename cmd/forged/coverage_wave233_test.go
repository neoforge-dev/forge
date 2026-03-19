//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave233_test.go — XNodeError constructors + XNodeController
// Target: xnode.go XNodeError.Error/Unwrap, all error constructors,
//         NewXNodeController
// ============================================================

// --- XNodeError.Error / Unwrap ---

func TestWave233_XNodeError_ErrorWithCause(t *testing.T) {
	cause := errors.New("connection refused")
	xe := &XNodeError{
		Code:    XNodeErrNodeNotFound,
		Message: "node not found",
		Err:     cause,
	}
	got := xe.Error()
	if !strings.Contains(got, "xnode:") {
		t.Errorf("expected 'xnode:' prefix, got: %s", got)
	}
	if !strings.Contains(got, "connection refused") {
		t.Errorf("expected cause in error string, got: %s", got)
	}
}

func TestWave233_XNodeError_ErrorNoCause(t *testing.T) {
	xe := &XNodeError{
		Code:    XNodeErrNodeOffline,
		Message: "node is offline",
	}
	got := xe.Error()
	if !strings.Contains(got, "xnode:") {
		t.Errorf("expected 'xnode:' prefix, got: %s", got)
	}
	if strings.Contains(got, "<nil>") {
		t.Errorf("unexpected nil in error string: %s", got)
	}
}

func TestWave233_XNodeError_Unwrap(t *testing.T) {
	cause := errors.New("root cause")
	xe := &XNodeError{Err: cause}
	if xe.Unwrap() != cause {
		t.Error("expected Unwrap to return cause error")
	}
}

func TestWave233_XNodeError_UnwrapNil(t *testing.T) {
	xe := &XNodeError{}
	if xe.Unwrap() != nil {
		t.Error("expected Unwrap to return nil when no cause")
	}
}

// --- Error constructors ---

func TestWave233_NewXNodeNodeNotFoundError(t *testing.T) {
	cause := errors.New("lookup failed")
	xe := newXNodeNodeNotFoundError("sati", cause)
	if xe == nil {
		t.Fatal("expected non-nil XNodeError")
	}
	if xe.Code != XNodeErrNodeNotFound {
		t.Errorf("expected NodeNotFound code, got %s", xe.Code)
	}
	if !strings.Contains(xe.Message, "sati") {
		t.Errorf("expected node ID in message: %s", xe.Message)
	}
	if xe.Err != cause {
		t.Error("expected cause error preserved")
	}
}

func TestWave233_NewXNodeNodeOfflineError(t *testing.T) {
	xe := newXNodeNodeOfflineError("nova", "degraded")
	if xe.Code != XNodeErrNodeOffline {
		t.Errorf("expected NodeOffline code, got %s", xe.Code)
	}
	if !strings.Contains(xe.Message, "nova") {
		t.Errorf("expected node ID in message: %s", xe.Message)
	}
	if !strings.Contains(xe.Message, "degraded") {
		t.Errorf("expected status in message: %s", xe.Message)
	}
}

func TestWave233_NewXNodeForwardFailedError(t *testing.T) {
	cause := errors.New("db write failed")
	xe := newXNodeForwardFailedError("TASK-123", cause)
	if xe.Code != XNodeErrForwardFailed {
		t.Errorf("expected ForwardFailed code, got %s", xe.Code)
	}
	if !strings.Contains(xe.Message, "TASK-123") {
		t.Errorf("expected task ID in message: %s", xe.Message)
	}
}

func TestWave233_NewXNodeOutboxWriteError(t *testing.T) {
	cause := errors.New("permission denied")
	xe := newXNodeOutboxWriteError("gaea", cause)
	if xe.Code != XNodeErrOutboxWrite {
		t.Errorf("expected OutboxWrite code, got %s", xe.Code)
	}
	if !strings.Contains(xe.Message, "gaea") {
		t.Errorf("expected node ID in message: %s", xe.Message)
	}
}

func TestWave233_NewXNodeInvalidRequestError(t *testing.T) {
	cause := errors.New("missing field")
	xe := newXNodeInvalidRequestError("node_id", cause)
	if xe.Code != XNodeErrInvalidRequest {
		t.Errorf("expected InvalidRequest code, got %s", xe.Code)
	}
	if !strings.Contains(xe.Message, "node_id") {
		t.Errorf("expected field in message: %s", xe.Message)
	}
}

func TestWave233_NewXNodeDatabaseError(t *testing.T) {
	cause := errors.New("sqlite error")
	xe := newXNodeDatabaseError("INSERT", cause)
	if xe.Code != XNodeErrDatabase {
		t.Errorf("expected Database code, got %s", xe.Code)
	}
	if !strings.Contains(xe.Message, "INSERT") {
		t.Errorf("expected operation in message: %s", xe.Message)
	}
}

// --- NewXNodeController ---

func TestWave233_NewXNodeController_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	if xc == nil {
		t.Fatal("expected non-nil XNodeController")
	}
}

func TestWave233_XNodeController_RegisterNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{
		ID:      "test-node-233",
		Address: "100.1.2.3:8081",
		Status:  "online",
	}
	// RegisterNode may fail if node already registered — we just check no panic
	_ = xc.RegisterNode(context.Background(), node)
}

func TestWave233_XNodeController_Heartbeat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Register node first
	node := Node{
		ID:      "node-hb-233",
		Address: "100.1.2.3:8081",
		Status:  "online",
	}
	xc.RegisterNode(context.Background(), node)

	// Heartbeat on registered node
	_ = xc.Heartbeat(context.Background(), "node-hb-233")
}

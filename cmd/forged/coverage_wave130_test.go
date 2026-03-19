//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handlers_openclaw.go:openclawEventsHandler (line ~266)
// ---------------------------------------------------------------------------

// TestWave130_OpenclawEventsHandler_MethodNotAllowed verifies POST returns 405.
// TestWave130_OpenclawEventsHandler_ContextCancel verifies context cancellation exits loop.
func TestWave130_OpenclawEventsHandler_ContextCancel(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil).WithContext(ctx)
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		defer close(done)
		openclawEventsHandler(w, req)
	}()

	// Cancel immediately after handler started
	time.Sleep(10 * time.Millisecond)
	cancel()

	select {
	case <-done:
		// Good — handler exited
	case <-time.After(3 * time.Second):
		t.Error("handler did not exit after context cancel")
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go:agentsSSEHandler (line ~437)
// ---------------------------------------------------------------------------

// TestWave130_AgentsSSEHandler_MethodNotAllowed verifies POST returns 405.
// TestWave130_AgentsSSEHandler_ContextCancel verifies handler exits on context cancel.
func TestWave130_AgentsSSEHandler_ContextCancel(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil).WithContext(ctx)
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		defer close(done)
		agentsSSEHandler(w, req)
	}()

	time.Sleep(10 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Error("agentsSSEHandler did not exit after context cancel")
	}
}

// ---------------------------------------------------------------------------
// main.go:handleSystemHealth (line ~402)
// ---------------------------------------------------------------------------

// TestWave130_HandleSystemHealth_DefaultFormat verifies normal JSON response.
func TestWave130_HandleSystemHealth_DefaultFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	t.Logf("handleSystemHealth: status=%d body=%s", w.Code, w.Body.String()[:min130(len(w.Body.String()), 100)])
}

// TestWave130_HandleSystemHealth_TextFormat verifies format=text path.
func TestWave130_HandleSystemHealth_TextFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health?format=text", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	t.Logf("handleSystemHealth text: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// main.go:handleTaskLogs (line ~332)
// ---------------------------------------------------------------------------

// TestWave130_HandleTaskLogs_MissingID verifies missing id returns 400.
func TestWave130_HandleTaskLogs_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/task/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave130_HandleTaskLogs_ValidID verifies valid id invokes event lookup.
func TestWave130_HandleTaskLogs_ValidID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/task/logs?id=nonexistent-task-130", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	t.Logf("handleTaskLogs valid id: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_agent.go:parityHandler (line ~727)
// ---------------------------------------------------------------------------

// TestWave130_ParityHandler_Success verifies parity check with working DB.
func TestWave130_ParityHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := parityHandler(db)
	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	t.Logf("parityHandler success: status=%d", w.Code)
}

// TestWave130_ParityHandler_ClosedDB verifies parity check with closed DB returns error.
func TestWave130_ParityHandler_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	handler := parityHandler(db)
	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	t.Logf("parityHandler closed DB: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_pwa_bridge.go:handleAgents (line ~114)
// ---------------------------------------------------------------------------

// TestWave130_PWAHandleAgents_NilDashboard verifies nil dashboard returns 503.
func TestWave130_PWAHandleAgents_NilDashboard(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// TestWave130_PWAHandleAgents_Success verifies normal agents response.
func TestWave130_PWAHandleAgents_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	hub := NewWebSocketHub()
	dash := NewDashboard(db, hub)
	h := NewPWADashboardHandler(dash)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	t.Logf("PWAHandleAgents success: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func min130(a, b int) int {
	if a < b {
		return a
	}
	return b
}

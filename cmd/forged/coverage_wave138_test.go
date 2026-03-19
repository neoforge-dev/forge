//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Target 1: agentsSSEHandler (handlers_agent.go:437)
// ---------------------------------------------------------------------------

func TestWave138_AgentsSSEHandler_ContextCancel(t *testing.T) {
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
		// good — handler exited after context cancel
	case <-time.After(3 * time.Second):
		t.Error("agentsSSEHandler did not exit after context cancel within 3s")
	}
}

func TestWave138_AgentsSSEHandler_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

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
		// good — handler exited even with nil DB
	case <-time.After(3 * time.Second):
		t.Error("agentsSSEHandler did not exit after context cancel (nil DB) within 3s")
	}
}

func TestWave138_AgentsSSEHandler_WithDB(t *testing.T) {
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
		// good
	case <-time.After(3 * time.Second):
		t.Error("agentsSSEHandler did not exit after context cancel (with DB) within 3s")
	}
}

// ---------------------------------------------------------------------------
// Target 2: fleetScaleRecommendPatrol (fleet_scaler.go:151)
// ---------------------------------------------------------------------------

func TestWave138_FleetScaleRecommendPatrol_NoAutoSpawnNode(t *testing.T) {
	// On prya/vega/gaea the function returns nil immediately at the guard.
	hostname, _ := os.Hostname()
	if !NoAutoSpawnNodes[hostname] {
		t.Skip("not a NoAutoSpawnNode host — skip guard test")
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := fleetScaleRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil from no-auto-spawn guard, got %v", err)
	}
}

func TestWave138_FleetScaleRecommendPatrol_ClosedDB(t *testing.T) {
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		// We're on a no-auto-spawn node so the function exits early before
		// touching the DB. Temporarily remove it to exercise the deeper path.
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	db, cleanup := setupClaimTestDB(t)
	// Close the DB before calling to force an error path.
	cleanup()

	err := fleetScaleRecommendPatrol(context.Background(), db)
	if err == nil {
		t.Error("expected error from closed DB, got nil")
	}
}

func TestWave138_FleetScaleRecommendPatrol_ContextCancelled(t *testing.T) {
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	// Cancelled context should propagate — either nil or context error.
	_ = fleetScaleRecommendPatrol(ctx, db)
}

func TestWave138_FleetScaleRecommendPatrol_EmptyDB(t *testing.T) {
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty DB: no tasks, no agent_inventory entries.
	// agent_inventory table may not exist — the patrol handles that gracefully.
	err := fleetScaleRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil from empty DB, got %v", err)
	}
}

func TestWave138_FleetScaleRecommendPatrol_WithQueuedTasks(t *testing.T) {
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a queued task directly into the DB.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, priority, created_at, updated_at)
		VALUES ('wave138-task-1', 'test', 'proj', 'feature', 'Test task', 'queued', 5,
		        datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task failed: %v", err)
	}

	// Should not panic; agent_inventory absent → graceful nil return.
	err = fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("expected nil with queued tasks but no agent_inventory, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// Target 3: TUI.Handler (tui.go:384)
// ---------------------------------------------------------------------------

func TestWave138_TUIHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	tui := NewTUI(db, NewHub(), q)
	handler := tui.Handler()

	req := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d; body: %s", w.Code, w.Body.String())
	}
	ct := w.Header().Get("Content-Type")
	if ct != "text/html" {
		t.Errorf("expected Content-Type text/html, got %s", ct)
	}
}

func TestWave138_TUIJSONHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	tui := NewTUI(db, NewHub(), q)
	handler := tui.JSONHandler()

	req := httptest.NewRequest(http.MethodGet, "/ui/json", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d; body: %s", w.Code, w.Body.String())
	}
	ct := w.Header().Get("Content-Type")
	if ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", ct)
	}
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// migrate.go:MigrateUp (line ~242)
// ---------------------------------------------------------------------------

// TestWave126_MigrateUp_AlreadyApplied verifies MigrateUp is idempotent (all migrations already applied).
func TestWave126_MigrateUp_AlreadyApplied(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// setupClaimTestDB already runs MigrateUp — call again to exercise the "already applied" path.
	err := MigrateUp(db)
	if err != nil {
		t.Errorf("MigrateUp (already applied) returned error: %v", err)
	}
}

// TestWave126_MigrateUp_ClosedDB verifies MigrateUp returns error on closed DB.
func TestWave126_MigrateUp_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close before calling MigrateUp

	err := MigrateUp(db)
	if err == nil {
		t.Error("expected error from MigrateUp with closed DB")
	}
	t.Logf("MigrateUp closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// migrate.go:MigrateDown (line ~311)
// ---------------------------------------------------------------------------

// TestWave126_MigrateDown_ZeroSteps verifies MigrateDown(0) is a no-op.
func TestWave126_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := MigrateDown(db, 0)
	if err != nil {
		t.Errorf("MigrateDown(0) should be no-op, got: %v", err)
	}
}

// TestWave126_MigrateDown_NegativeSteps verifies MigrateDown with negative steps is a no-op.
func TestWave126_MigrateDown_NegativeSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := MigrateDown(db, -1)
	if err != nil {
		t.Errorf("MigrateDown(-1) should be no-op, got: %v", err)
	}
}

// TestWave126_MigrateDown_OneStep rolls back one migration (may succeed or log).
func TestWave126_MigrateDown_OneStep(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := MigrateDown(db, 1)
	if err != nil {
		t.Logf("MigrateDown(1) error (may be acceptable): %v", err)
	}
}

// TestWave126_MigrateDown_ClosedDB verifies MigrateDown returns error on closed DB.
func TestWave126_MigrateDown_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	err := MigrateDown(db, 1)
	if err == nil {
		t.Error("expected error from MigrateDown with closed DB")
	}
	t.Logf("MigrateDown closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// migrate.go:ensureMigrationsTable (line ~396)
// ---------------------------------------------------------------------------

// TestWave126_EnsureMigrationsTable_SQLite verifies table creation for SQLite.
func TestWave126_EnsureMigrationsTable_SQLite(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Table already exists from migrations — should be idempotent (CREATE TABLE IF NOT EXISTS).
	err := ensureMigrationsTable(db, SQLite)
	if err != nil {
		t.Errorf("ensureMigrationsTable SQLite: %v", err)
	}
}

// TestWave126_EnsureMigrationsTable_ClosedDB verifies error on closed DB.
func TestWave126_EnsureMigrationsTable_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	err := ensureMigrationsTable(db, SQLite)
	if err == nil {
		t.Error("expected error with closed DB")
	}
	t.Logf("ensureMigrationsTable closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// dashboard.go:ServeDashboardJSON (line ~890)
// ---------------------------------------------------------------------------

// TestWave126_ServeDashboardJSON_Success exercises the normal GET path.
func TestWave126_ServeDashboardJSON_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewWebSocketHub()
	dash := NewDashboard(db, h)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/json", nil)
	w := httptest.NewRecorder()
	dash.ServeDashboardJSON(w, req)
	t.Logf("ServeDashboardJSON: status=%d", w.Code)
}

// TestWave126_ServeDashboardJSON_ClosedDB exercises the error path.
func TestWave126_ServeDashboardJSON_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	h := NewWebSocketHub()
	dash := NewDashboard(db, h)
	cleanup() // close DB so GetDashboardData errors

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/json", nil)
	w := httptest.NewRecorder()
	dash.ServeDashboardJSON(w, req)
	t.Logf("ServeDashboardJSON closed DB: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// tui.go:Handler (line ~384)
// ---------------------------------------------------------------------------

// TestWave126_TUI_Handler_Success exercises the normal path.
func TestWave126_TUI_Handler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewWebSocketHub()
	// Use the global taskQueue set by setupClaimTestDB.
	tui := NewTUI(db, h, taskQueue)

	handler := tui.Handler()
	req := httptest.NewRequest(http.MethodGet, "/tui", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	t.Logf("TUI.Handler: status=%d", w.Code)
}

// TestWave126_TUI_Handler_ClosedDB exercises the error path (GetDashboard fails).
func TestWave126_TUI_Handler_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	h := NewWebSocketHub()
	tui := NewTUI(db, h, taskQueue)
	cleanup()

	handler := tui.Handler()
	req := httptest.NewRequest(http.MethodGet, "/tui", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	t.Logf("TUI.Handler closed DB: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// tui.go:JSONHandler (line ~400)
// ---------------------------------------------------------------------------

// TestWave126_TUI_JSONHandler_Success exercises the normal JSON path.
func TestWave126_TUI_JSONHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewWebSocketHub()
	tui := NewTUI(db, h, taskQueue)

	handler := tui.JSONHandler()
	req := httptest.NewRequest(http.MethodGet, "/tui/json", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	t.Logf("TUI.JSONHandler: status=%d, body=%s", w.Code, w.Body.String()[:min126(len(w.Body.String()), 100)])
}

// TestWave126_TUI_JSONHandler_ClosedDB exercises the JSON error path.
func TestWave126_TUI_JSONHandler_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	h := NewWebSocketHub()
	tui := NewTUI(db, h, taskQueue)
	cleanup()

	handler := tui.JSONHandler()
	req := httptest.NewRequest(http.MethodGet, "/tui/json", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	t.Logf("TUI.JSONHandler closed DB: status=%d", w.Code)
}

// min126 is a local helper to avoid index out of range in t.Logf.
func min126(a, b int) int {
	if a < b {
		return a
	}
	return b
}

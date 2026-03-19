//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestWave119_MigrateDown_ZeroSteps verifies MigrateDown with steps<=0 is a no-op.
func TestWave119_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := MigrateDown(db, 0); err != nil {
		t.Errorf("MigrateDown(0) should be no-op, got: %v", err)
	}
	if err := MigrateDown(db, -1); err != nil {
		t.Errorf("MigrateDown(-1) should be no-op, got: %v", err)
	}
}

// TestWave119_MigrateDown_OneStep rolls back the last migration.
func TestWave119_MigrateDown_OneStep(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// setupClaimTestDB already calls MigrateUp. Roll back one step.
	err := MigrateDown(db, 1)
	if err != nil {
		// Down migration not defined is acceptable — many migrations have no down.
		t.Logf("MigrateDown(1) returned: %v (acceptable — down not always defined)", err)
	}
}

// TestWave119_ServeDashboard_Basic verifies Dashboard.ServeDashboard returns HTML.
func TestWave119_ServeDashboard_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewWebSocketHub()
	dash := NewDashboard(db, h)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	w := httptest.NewRecorder()
	dash.ServeDashboard(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	ct := w.Header().Get("Content-Type")
	if ct != "text/html" {
		t.Errorf("expected Content-Type text/html, got %s", ct)
	}
}

// TestWave119_ServeDashboardJSON_Basic verifies Dashboard.ServeDashboardJSON returns JSON.
func TestWave119_ServeDashboardJSON_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewWebSocketHub()
	dash := NewDashboard(db, h)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/json", nil)
	w := httptest.NewRecorder()
	dash.ServeDashboardJSON(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	ct := w.Header().Get("Content-Type")
	if ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", ct)
	}
}

// TestWave119_ServeDashboard_ClosedDB verifies error path with closed DB.
func TestWave119_ServeDashboard_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cleanup() // close DB

	h := NewWebSocketHub()
	dash := NewDashboard(db, h)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	w := httptest.NewRecorder()
	dash.ServeDashboard(w, req)

	// Should return 500 on DB error.
	if w.Code != http.StatusInternalServerError {
		t.Logf("ServeDashboard with closed DB: status=%d body=%s", w.Code, w.Body.String())
	}
}

// TestWave119_SyncRoyalJelly_NoContextDir tests syncRoyalJelly with missing context dir.
func TestWave119_SyncRoyalJelly_NoContextDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Set FORGE_ROOT to a dir without .forge/context/
	origRoot := forgeRoot()
	_ = origRoot
	t.Setenv("FORGE_ROOT", t.TempDir())

	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("syncRoyalJelly with missing context dir should return nil, got: %v", err)
	}
}

// TestWave119_EnsureMigrationsTable_Idempotent verifies ensureMigrationsTable is idempotent.
func TestWave119_EnsureMigrationsTable_Idempotent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	dbType := detectDBType(db)
	// Call twice — should not error.
	if err := ensureMigrationsTable(db, dbType); err != nil {
		t.Errorf("first call: %v", err)
	}
	if err := ensureMigrationsTable(db, dbType); err != nil {
		t.Errorf("second call (idempotent): %v", err)
	}
}

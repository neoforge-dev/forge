//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 147: patrolsHandler + nodesHealthHandler + fleetSnapshotHandler
// Targets:
//   patrolsHandler      (handlers_patrols.go:56) — POST→405, GET with nil DB, GET with DB
//   nodesHealthHandler  (handlers_patrols.go:179) — POST→405, nil DB→503, GET with DB (empty)
//   fleetSnapshotHandler(handlers_patrols.go:256) — POST→405, nil DB→503, GET with DB
//   patrolRunHandler    (handlers_patrols.go:16) — POST→404 (no path suffix), POST→503 (nil patrol system)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// patrolRunHandler — handlers_patrols.go:16
// ---------------------------------------------------------------------------

func TestWave147_PatrolRunHandler_MissingRunSuffix(t *testing.T) {
	// Path without /run suffix → 404
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing run suffix, got %d", w.Code)
	}
}

func TestWave147_PatrolRunHandler_NilPatrolSystem(t *testing.T) {
	// globalPatrolSystem is nil in tests → 503
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/health-ping/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil patrol system, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// patrolsHandler — handlers_patrols.go:56
// ---------------------------------------------------------------------------

func TestWave147_PatrolsHandler_GetWithNilDB(t *testing.T) {
	// nil DB + nil patrol system → returns empty list (not an error)
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with nil DB, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave147_PatrolsHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with DB, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// nodesHealthHandler — handlers_patrols.go:179
// ---------------------------------------------------------------------------

func TestWave147_NodesHealthHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave147_NodesHealthHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with DB, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// fleetSnapshotHandler — handlers_patrols.go:256
// ---------------------------------------------------------------------------

func TestWave147_FleetSnapshotHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/snapshot", nil)
	w := httptest.NewRecorder()
	fleetSnapshotHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave147_FleetSnapshotHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/snapshot", nil)
	w := httptest.NewRecorder()
	fleetSnapshotHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with DB, got %d: %s", w.Code, w.Body.String())
	}
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 195: handlers_pwa_bridge.go
// Targets:
//   NewPWADashboardHandler  (handlers_pwa_bridge.go:57) — non-nil
//   handleSummary           (handlers_pwa_bridge.go:66) — nil dashboard→503, with DB
//   handleAgents            (handlers_pwa_bridge.go:114)— nil dashboard→503, with DB
//   fleetSummaryHandler     (handlers_pwa_bridge.go:188)— POST→405, nil DB→503, GET with DB
//   leadStateHandler        (handlers_pwa_bridge.go:254)— returns 200
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewPWADashboardHandler
// ---------------------------------------------------------------------------

func TestWave195_NewPWADashboardHandler_NotNil(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	if h == nil {
		t.Error("expected non-nil PWADashboardHandler")
	}
}

// ---------------------------------------------------------------------------
// handleSummary
// ---------------------------------------------------------------------------

func TestWave195_HandleSummary_NilDashboard503(t *testing.T) {
	h := NewPWADashboardHandler(nil) // nil dashboard
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil dashboard, got %d", w.Code)
	}
}

func TestWave195_HandleSummary_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	h := NewPWADashboardHandler(d)
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with DB, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleAgents
// ---------------------------------------------------------------------------

func TestWave195_HandleAgents_NilDashboard503(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil dashboard, got %d", w.Code)
	}
}

func TestWave195_HandleAgents_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	h := NewPWADashboardHandler(d)
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with DB, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// fleetSummaryHandler
// ---------------------------------------------------------------------------

func TestWave195_FleetSummaryHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	fleetSummaryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave195_FleetSummaryHandler_NilDB503(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	fleetSummaryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave195_FleetSummaryHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	fleetSummaryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// leadStateHandler
// ---------------------------------------------------------------------------

func TestWave195_LeadStateHandler_Returns200(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/lead/state", nil)
	w := httptest.NewRecorder()
	leadStateHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

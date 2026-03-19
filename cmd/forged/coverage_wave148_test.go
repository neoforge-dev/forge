//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 148: fleetMetricsHandler + nodeMetricsPushPatrol + fleetAggregateHandler
// Targets:
//   fleetMetricsHandler     (handlers_node_metrics.go:154) — POST→405, nil DB→503, GET with DB
//   fleetAggregateHandler   (handlers_node_metrics.go:259) — POST→405, nil DB→503
//   nodeMetricsPushPatrol   (handlers_node_metrics.go:452) — empty FORGE_LEAD_URL → nil
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// fleetMetricsHandler — handlers_node_metrics.go:154
// Note: TestWave148_FleetMetricsHandler_MethodNotAllowed moved to handler_method_validation_test.go
// ---------------------------------------------------------------------------

func TestWave148_FleetMetricsHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave148_FleetMetricsHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with DB, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// fleetAggregateHandler — handlers_node_metrics.go:259
// Note: TestWave148_FleetAggregateHandler_MethodNotAllowed moved to handler_method_validation_test.go
// ---------------------------------------------------------------------------

func TestWave148_FleetAggregateHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// nodeMetricsPushPatrol — handlers_node_metrics.go:452
// ---------------------------------------------------------------------------

func TestWave148_NodeMetricsPushPatrol_NoLeadURL(t *testing.T) {
	// When FORGE_LEAD_URL is empty, the patrol is a no-op and returns nil.
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	t.Setenv("FORGE_LEAD_URL", "")
	err := nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil when FORGE_LEAD_URL empty, got: %v", err)
	}
}

func TestWave148_NodeMetricsPushPatrol_UnreachableLead(t *testing.T) {
	// When lead URL is set but unreachable, patrol soft-fails (returns nil).
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	t.Setenv("FORGE_LEAD_URL", "http://127.0.0.1:19999")
	t.Setenv("NODE_ID", "test-node")
	err := nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil for unreachable lead (soft-fail), got: %v", err)
	}
}

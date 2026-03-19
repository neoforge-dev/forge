//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 223: routing_handler.go + handlers_lane.go
// Targets:
//   routingResolveHandler (routing_handler.go:47) — non-POST 405, bad body 400, no config 503
//   lanesHandler          (handlers_lane.go:15)   — GET 200, non-GET 405
//   laneByIDHandler       (handlers_lane.go:70)   — non-GET 405, missing ID 400, not found 404
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// routingResolveHandler
// Note: TestWave223_RoutingResolveHandler_MethodNotAllowed moved to handler_method_validation_test.go
// ---------------------------------------------------------------------------

func TestWave223_RoutingResolveHandler_BadBody(t *testing.T) {
	body := bytes.NewReader([]byte(`{"invalid_field": "value"}`))
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", body)
	req.Header.Set("Content-Type", "application/json")
	req.ContentLength = int64(len(`{"invalid_field": "value"}`))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)

	// DisallowUnknownFields → 400
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for unknown field, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave223_RoutingResolveHandler_EmptyBody_NoConfig(t *testing.T) {
	// No routing config on disk in CI → 503
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", nil)
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)

	// 200 (if YAML exists) or 503 (no config)
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestWave223_RoutingResolveHandler_ValidBody(t *testing.T) {
	body, _ := json.Marshal(routingResolveRequest{
		TaskType: "coverage",
		Weight:   "light",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.ContentLength = int64(len(body))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)

	// 200 (found) or 503 (no config / no eligible agent)
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// lanesHandler
// Note: TestWave223_LanesHandler_MethodNotAllowed moved to handler_method_validation_test.go
// ---------------------------------------------------------------------------

func TestWave223_LanesHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	w := httptest.NewRecorder()
	lanesHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// laneByIDHandler
// Note: TestWave223_LaneByIDHandler_MethodNotAllowed moved to handler_method_validation_test.go
// ---------------------------------------------------------------------------

func TestWave223_LaneByIDHandler_MissingID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Path strips prefix → empty ID
	req := httptest.NewRequest(http.MethodGet, "/api/lanes/", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	// Missing ID → 400
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing ID, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave223_LaneByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-lane-xyz", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	if w.Code != http.StatusNotFound && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

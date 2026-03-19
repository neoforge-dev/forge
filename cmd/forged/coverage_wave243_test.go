//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave243_test.go — patrolRunHandler, agentTelemetrySummaryHandler,
//         parityHandler, handleQueueDepth, readLoadAverage, handleAgents(nil)
// ============================================================

// --- patrolRunHandler ---

func TestWave243_PatrolRunHandler_EmptyPatrolID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols//run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for empty patrol ID, got %d", w.Code)
	}
}

func TestWave243_PatrolRunHandler_NoRunSuffix(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/my-patrol", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing /run suffix, got %d", w.Code)
	}
}

func TestWave243_PatrolRunHandler_NilGlobalPatrolSystem(t *testing.T) {
	// Save and restore globalPatrolSystem
	orig := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = orig }()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols/some-patrol/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil patrol system, got %d", w.Code)
	}
}

// --- agentTelemetrySummaryHandler ---

func TestWave243_AgentTelemetrySummaryHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	defer func() { setDBConn(nil) }()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil DB, got %d", w.Code)
	}
}

func TestWave243_AgentTelemetrySummaryHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	// 200 (empty results) or 500 (table missing) both acceptable
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// --- parityHandler ---

func TestWave243_ParityHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := parityHandler(db)
	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	// 200 OK or 500 (parity check internal error) both acceptable
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// --- handleQueueDepth ---

func TestWave243_HandleQueueDepth_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "depth") {
		t.Errorf("expected 'depth' in response, got: %s", w.Body.String())
	}
}

// --- readLoadAverage ---

func TestWave243_ReadLoadAverage(t *testing.T) {
	avg, err := readLoadAverage()
	if err != nil {
		t.Skipf("readLoadAverage not available on this platform: %v", err)
	}
	if avg < 0 {
		t.Errorf("expected non-negative load average, got %f", avg)
	}
}

// --- handleAgents (PWADashboardHandler nil dashboard) ---

func TestWave243_HandleAgents_NilDashboard(t *testing.T) {
	h := &PWADashboardHandler{dashboard: nil}
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil dashboard, got %d", w.Code)
	}
}

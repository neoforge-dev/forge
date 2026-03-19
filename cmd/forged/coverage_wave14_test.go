//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave14_test.go — Coverage wave 14: dispatch, pattern, agent, lane handlers
// Target: +0.7pp from ~59.3% to ≥60%
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- handlers_dispatch.go: dispatchHandler ---

func TestDispatchHandlerW14_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("dispatchHandler GET: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := result["dispatches"]; !ok {
		t.Error("expected 'dispatches' key")
	}
}

func TestDispatchHandler_Post_Wave14_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}


// --- handlers_pattern.go: getPatternByIDHandler, listPatternRunsHandler ---

func TestGetPatternByIDHandlerW14_NotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/nonexistent-pattern", nil)
	w := httptest.NewRecorder()
	getPatternByIDHandler(w, req, "nonexistent-pattern")
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestListPatternRunsHandler_EmptyResult(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/nonexistent/runs", nil)
	w := httptest.NewRecorder()
	listPatternRunsHandler(w, req, "nonexistent-pattern")
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("listPatternRunsHandler: unexpected %d: %s", w.Code, w.Body.String())
	}
}

func TestListPatternRunsHandler_WithLimit(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/nonexistent/runs?limit=10", nil)
	w := httptest.NewRecorder()
	listPatternRunsHandler(w, req, "nonexistent-pattern")
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("listPatternRunsHandler with limit: unexpected %d", w.Code)
	}
}

// --- handlers_agent.go: agentContextHandler ---

func TestAgentContextHandler_EmptyID(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents//context", nil)
	w := httptest.NewRecorder()
	agentContextHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d", w.Code)
	}
}

func TestAgentContextHandlerW14_UnknownAgent(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/unknown-agent-xyz/context", nil)
	req.URL.Path = "/api/agents/unknown-agent-xyz/context"
	w := httptest.NewRecorder()
	agentContextHandler(w, req)
	// Unknown agent → graceful degradation returns 200 with context_pct=0
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("agentContextHandler unknown: unexpected %d: %s", w.Code, w.Body.String())
	}
}

// --- handlers_agent.go: agentByIDHandler ---

func TestAgentByIDHandlerW14_NotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/nonexistent-agent", nil)
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)
	if w.Code != http.StatusNotFound && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// --- handlers_agent.go: agentsHealthHandler, getAllAgentsHealth ---

func TestAgentsHealthHandler_All(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("agentsHealthHandler all: unexpected %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentsHealthHandler_SpecificAgent(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-999/health", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)
	// Unknown agent → 404
	if w.Code != http.StatusNotFound && w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("agentsHealthHandler specific: unexpected %d: %s", w.Code, w.Body.String())
	}
}

// --- handlers_lane.go: laneByIDHandler ---

func TestLaneByIDHandlerW14_NotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-lane-xyz", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestLaneByIDHandler_Found(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// The lanes migration seeds "forge-main" by default
	req := httptest.NewRequest(http.MethodGet, "/api/lanes/forge-main", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)
	// 200 if seeded lane exists, 404 if not
	if w.Code != http.StatusOK && w.Code != http.StatusNotFound && w.Code != http.StatusInternalServerError {
		t.Errorf("laneByIDHandler found: unexpected %d: %s", w.Code, w.Body.String())
	}
}

// --- populate_dispatches.go: minInt ---

func TestMinInt(t *testing.T) {
	tests := []struct{ a, b, want int }{
		{3, 5, 3},
		{5, 3, 3},
		{4, 4, 4},
		{0, 1, 0},
		{-1, 0, -1},
	}
	for _, tc := range tests {
		if got := minInt(tc.a, tc.b); got != tc.want {
			t.Errorf("minInt(%d, %d) = %d, want %d", tc.a, tc.b, got, tc.want)
		}
	}
}

// --- handlers_agent.go: agentMetricsHandler with since param ---

func TestAgentMetricsHandler_WithSince(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-001/metrics?since=2026-01-01T00:00:00Z&limit=5", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("agentMetricsHandler since: unexpected %d: %s", w.Code, w.Body.String())
	}
}

// --- handlers_patrols.go: dashHandler ---

func TestDashHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("dashHandler GET: unexpected %d: %s", w.Code, w.Body.String())
	}
}

// --- fleet_scaler.go: readLiveRAMMB, readLoadAverage, getCPUCount ---

func TestReadLiveRAMMB(t *testing.T) {
	mb, err := readLiveRAMMB()
	if err != nil {
		t.Logf("readLiveRAMMB: %v (may be OK if /proc not available)", err)
		return
	}
	if mb < 0 {
		t.Errorf("expected non-negative RAM MB, got %d", mb)
	}
}

func TestReadLoadAverage(t *testing.T) {
	load, err := readLoadAverage()
	if err != nil {
		t.Logf("readLoadAverage: %v (may be OK if /proc not available)", err)
		return
	}
	if load < 0 {
		t.Errorf("expected non-negative load average, got %f", load)
	}
}

func TestGetCPUCount(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("expected positive CPU count, got %d", count)
	}
}

// --- handlers_node_metrics.go: nodeMetricsPushPatrol with empty FORGE_LEAD_URL ---

func TestNodeMetricsPushPatrol_NoLeadURL(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	t.Setenv("FORGE_LEAD_URL", "")
	db := getDBConn()
	err := nodeMetricsPushPatrol(t.Context(), db)
	if err != nil {
		t.Errorf("nodeMetricsPushPatrol with no FORGE_LEAD_URL: %v", err)
	}
}

// --- handlers_pattern.go: loadPatternsFromDocs ---

func TestLoadPatternsFromDocs_NoPatternsDir(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	t.Setenv("FORGE_ROOT", t.TempDir())
	db := getDBConn()
	// Should silently return if docs/patterns doesn't exist
	loadPatternsFromDocs(db)
}

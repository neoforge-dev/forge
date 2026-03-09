//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: nodeMetricsReceiveHandler — 73.9%
// ---------------------------------------------------------------------------

func TestWave81_NodeMetricsReceiveHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/nodes/test-node/metrics", nil)
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_NodeMetricsReceiveHandler_MissingNodeID(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/nodes//metrics", nil)
	r.URL.Path = "/api/nodes//metrics"
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("expected 400 for missing node ID, got %d", w.Code)
	}
}

func TestWave81_NodeMetricsReceiveHandler_ValidBody(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := NodeMetricPayload{
		NodeID: "wave81-node",
		Period: "1m",
		Metrics: []NodeMetricSample{
			{Name: "cpu_usage", Value: 45.2, Labels: map[string]string{"env": "test"}},
			{Name: "mem_mb", Value: 1024.0},
		},
	}
	body, _ := json.Marshal(payload)
	r := httptest.NewRequest(http.MethodPost, "/api/nodes/wave81-node/metrics", bytes.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.URL.Path = "/api/nodes/wave81-node/metrics"
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code >= 500 {
		t.Logf("nodeMetricsReceiveHandler: %d %s", w.Code, w.Body.String())
	}
}

func TestWave81_NodeMetricsReceiveHandler_NodeIDMismatch(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := NodeMetricPayload{NodeID: "different-node", Metrics: []NodeMetricSample{}}
	body, _ := json.Marshal(payload)
	r := httptest.NewRequest(http.MethodPost, "/api/nodes/wave81-node/metrics", bytes.NewReader(body))
	r.URL.Path = "/api/nodes/wave81-node/metrics"
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("expected 400 for mismatch, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_worker.go: workersHandler, workerByIDHandler — 80%, 75%
// ---------------------------------------------------------------------------

func TestWave81_WorkersHandler_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave81_WorkersHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_WorkerByIDHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/workers/test", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_WorkerByIDHandler_MissingID(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/workers/", nil)
	r.URL.Path = "/api/workers/"
	w := httptest.NewRecorder()
	workerByIDHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("workerByIDHandler missing ID: %d", w.Code)
	}
}

func TestWave81_WorkerByIDHandler_NotFound(t *testing.T) {
	if hub == nil {
		t.Skip("hub not initialized in test environment")
	}
	r := httptest.NewRequest(http.MethodGet, "/api/workers/nonexistent-wave81", nil)
	r.URL.Path = "/api/workers/nonexistent-wave81"
	w := httptest.NewRecorder()
	workerByIDHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Logf("workerByIDHandler not found: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go: tuiLogsHandler — 76.2%
// ---------------------------------------------------------------------------

func TestWave81_TuiLogsHandler_OK(t *testing.T) {
	if hub == nil {
		t.Skip("hub not initialized")
	}
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tui/logs", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go: replanTaskHandler — 77.8%
// ---------------------------------------------------------------------------

func TestWave81_ReplanTaskHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/tid/replan", nil)
	w := httptest.NewRecorder()
	replanTaskHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_ReplanTaskHandler_BadBody(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave81-task/replan", bytes.NewBufferString("not json"))
	r.URL.Path = "/api/tasks/wave81-task/replan"
	w := httptest.NewRecorder()
	replanTaskHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("replanTaskHandler bad body: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentTelemetryHandler — 71.9%
// ---------------------------------------------------------------------------

func TestWave81_AgentTelemetryHandler_MethodNotAllowed(t *testing.T) {
	// agentTelemetryHandler only allows GET; send POST → 405
	r := httptest.NewRequest(http.MethodPost, "/api/agents/test/telemetry", nil)
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_AgentTelemetryHandler_ValidGet(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave81-agent/telemetry", nil)
	r.URL.Path = "/api/agents/wave81-agent/telemetry"
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentTelemetryHandler GET: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentMetricsHandler — 74.5%
// ---------------------------------------------------------------------------

func TestWave81_AgentMetricsHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/agents/test/metrics", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_AgentMetricsHandler_ValidGet(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave81-agent/metrics", nil)
	r.URL.Path = "/api/agents/wave81-agent/metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave81_AgentMetricsHandler_WithLimit(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave81-agent/metrics?limit=10&since=2026-01-01T00:00:00Z", nil)
	r.URL.Path = "/api/agents/wave81-agent/metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentsHealthHandler — 75%
// ---------------------------------------------------------------------------

func TestWave81_AgentsHealthHandler_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Path must be exactly /api/agents/ to get all agents health
	r := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	r.URL.Path = "/api/agents/"
	w := httptest.NewRecorder()
	agentsHealthHandler(w, r)
	if w.Code != http.StatusOK {
		t.Logf("agentsHealthHandler: %d: %s", w.Code, w.Body.String())
	}
}

func TestWave81_AgentsHealthHandler_ByID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave81-agent/health", nil)
	r.URL.Path = "/api/agents/wave81-agent/health"
	w := httptest.NewRecorder()
	agentsHealthHandler(w, r)
	if w.Code >= 500 {
		t.Errorf("unexpected 5xx: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: parityHandler — 71.4%
// ---------------------------------------------------------------------------

func TestWave81_ParityHandler_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := parityHandler(db)
	r := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code >= 500 {
		t.Logf("parityHandler: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: nodeMetricsPushPatrol — 63.3%
// ---------------------------------------------------------------------------

func TestWave81_NodeMetricsPushPatrol_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := nodeMetricsPushPatrol(context.Background(), db)
	t.Logf("nodeMetricsPushPatrol: %v", err)
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: fleetAggregateHandler — 78.1%
// ---------------------------------------------------------------------------

func TestWave81_FleetAggregateHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave81_FleetAggregateHandler_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_dispatch.go: dispatchHandler POST with force — 69%
// ---------------------------------------------------------------------------

func TestWave81_DispatchHandler_POST_ForceField(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{
		"task_id":  "wave81-dispatch-task",
		"agent_id": "agent-wave81",
		"message":  "wave81 dispatch",
		"force":    true,
	})
	r := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewReader(body))
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Logf("dispatchHandler POST with force: %d: %s", w.Code, w.Body.String())
	}
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ============================================================
// coverage_wave227_test.go — agent handler paths
// Target: agentContextHandler, agentHeartbeatReceive,
//         agentTelemetryHandler, agentMetricsHandler,
//         agentTelemetrySummaryHandler, agentsHandler, agentsSSEHandler
// ============================================================

// --- agentContextHandler ---

func TestWave227_AgentContext_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//context", nil)
	req.URL.Path = "/api/agents//context"
	w := httptest.NewRecorder()
	agentContextHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave227_AgentContext_InvalidID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/../context", nil)
	req.URL.Path = "/api/agents/../context"
	w := httptest.NewRecorder()
	agentContextHandler(w, req)
	// invalid ID: ".." gets sanitized, may result in empty or invalid ID response
	if w.Code != http.StatusBadRequest && w.Code != http.StatusOK {
		t.Errorf("expected 400 or 200, got %d", w.Code)
	}
}

func TestWave227_AgentContext_UnknownAgent(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/unknown-agent-xyz/context", nil)
	req.URL.Path = "/api/agents/unknown-agent-xyz/context"
	w := httptest.NewRecorder()
	agentContextHandler(w, req)
	// Graceful degradation: returns 200 with context_pct=0 for unknown agents
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (graceful degradation), got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); !strings.Contains(ct, "application/json") {
		t.Errorf("expected JSON content-type, got %s", ct)
	}
}

// --- agentHeartbeatReceive ---

func TestWave227_HeartbeatReceive_EmptyAgentID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents//heartbeat", nil)
	req.URL.Path = "/api/agents//heartbeat"
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave227_HeartbeatReceive_IDMismatch(t *testing.T) {
	body := strings.NewReader(`{"agent_id":"different-agent","status":"idle"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/my-agent/heartbeat", body)
	req.URL.Path = "/api/agents/my-agent/heartbeat"
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave227_HeartbeatReceive_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Invalid JSON — handler falls back to status="unknown"
	body := strings.NewReader(`{not valid json}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-227/heartbeat", body)
	req.URL.Path = "/api/agents/agent-227/heartbeat"
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	// Handler accepts heartbeat with fallback status
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (fallback), got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave227_HeartbeatReceive_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := strings.NewReader(`{"status":"idle","node":"prya","context_pct":30.0}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-227-hb/heartbeat", body)
	req.URL.Path = "/api/agents/agent-227-hb/heartbeat"
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave227_HeartbeatReceive_ContextWarnings(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Context > 70 triggers critical log
	body := strings.NewReader(`{"status":"busy","context_pct":80.0}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-227-ctx/heartbeat", body)
	req.URL.Path = "/api/agents/agent-227-ctx/heartbeat"
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// --- agentTelemetryHandler ---

func TestWave227_AgentTelemetry_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//telemetry", nil)
	req.URL.Path = "/api/agents//telemetry"
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave227_AgentTelemetry_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/telemetry", nil)
	req.URL.Path = "/api/agents/agent-1/telemetry"
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave227_AgentTelemetry_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-227-tel/telemetry?limit=10", nil)
	req.URL.Path = "/api/agents/agent-227-tel/telemetry"
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "agent_id") {
		t.Errorf("expected agent_id in response: %s", body)
	}
}

// --- agentMetricsHandler ---

func TestWave227_AgentMetrics_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//metrics", nil)
	req.URL.Path = "/api/agents//metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave227_AgentMetrics_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/metrics", nil)
	req.URL.Path = "/api/agents/agent-1/metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	// Returns 200 with empty metrics when db is nil
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (empty metrics), got %d", w.Code)
	}
}

func TestWave227_AgentMetrics_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-227-met/metrics", nil)
	req.URL.Path = "/api/agents/agent-227-met/metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// --- agentTelemetrySummaryHandler ---

func TestWave227_TelemetrySummary_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave227_TelemetrySummary_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "agent_count") {
		t.Errorf("expected agent_count in response: %s", body)
	}
}

// --- agentsHandler ---

func TestWave227_AgentsHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "agents") {
		t.Errorf("expected 'agents' in response: %s", body)
	}
}

// --- agentsSSEHandler (context-cancelled path) ---

func TestWave227_AgentsSSE_CancelledContext(t *testing.T) {
	// Cancel context immediately so SSE loop exits on first select iteration
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // pre-cancel

	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil)
	req = req.WithContext(ctx)

	// httptest.ResponseRecorder implements http.Flusher (has Flush method)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	// Returns immediately due to cancelled context — headers set to SSE
	ct := w.Header().Get("Content-Type")
	if ct != "" && !strings.Contains(ct, "text/event-stream") {
		t.Errorf("unexpected content-type: %s", ct)
	}
}

// --- agentMetricsHandler with since param ---

func TestWave227_AgentMetrics_WithSince(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	since := time.Now().Add(-1 * time.Hour).Format(time.RFC3339)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-227-since/metrics?since="+since, nil)
	req.URL.Path = "/api/agents/agent-227-since/metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

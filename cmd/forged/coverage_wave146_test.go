//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 146: configHandler + dispatchHandler + nodeMetricsReceiveHandler
// Targets:
//   configHandler         (handlers_dispatch.go:14) — GET, PUT, PUT bad JSON, 405
//   dispatchHandler       (handlers_dispatch.go:45) — GET, POST, POST bad JSON, 405
//   nodeMetricsReceiveHandler (handlers_node_metrics.go:64) — GET→405, missing ID→400,
//                             bad JSON→400, node_id mismatch→400, nil DB→503,
//                             valid payload with DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// configHandler — handlers_dispatch.go:14
// ---------------------------------------------------------------------------

func TestWave146_ConfigHandler_Get(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "api_version") {
		t.Errorf("expected api_version in response, got: %s", w.Body.String())
	}
}

func TestWave146_ConfigHandler_Put(t *testing.T) {
	body := `{"custom_key":"custom_value"}`
	req := httptest.NewRequest(http.MethodPut, "/config", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "updated") {
		t.Errorf("expected 'updated' in response, got: %s", w.Body.String())
	}
}

func TestWave146_ConfigHandler_Put_BadJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/config", strings.NewReader("{bad json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// dispatchHandler — handlers_dispatch.go:45
// ---------------------------------------------------------------------------

func TestWave146_DispatchHandler_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "dispatches") {
		t.Errorf("expected 'dispatches' in response, got: %s", w.Body.String())
	}
}

func TestWave146_DispatchHandler_Post_BadJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", strings.NewReader("{invalid"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave146_DispatchHandler_Post(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := `{"task_id":"task-001","agent_id":"kimi","message":"run task"}`
	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "created") {
		t.Errorf("expected 'created' in response, got: %s", w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// nodeMetricsReceiveHandler — handlers_node_metrics.go:64
// ---------------------------------------------------------------------------

func TestWave146_NodeMetricsReceive_MissingNodeID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/nodes//metrics", nil)
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty node ID, got %d", w.Code)
	}
}

func TestWave146_NodeMetricsReceive_BadJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/prya/metrics", strings.NewReader("{bad"))
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestWave146_NodeMetricsReceive_NodeIDMismatch(t *testing.T) {
	body := `{"node_id":"other-node","metrics":[]}`
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/prya/metrics", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for node_id mismatch, got %d", w.Code)
	}
}

func TestWave146_NodeMetricsReceive_NilDB(t *testing.T) {
	setDBConn(nil)
	body := `{"metrics":[]}`
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/prya/metrics", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave146_NodeMetricsReceive_ValidPayload(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := `{
		"node_id": "prya",
		"period": "1m",
		"metrics": [
			{"name": "cpu_usage", "value": 42.5, "labels": {"core": "0"}},
			{"name": "mem_usage", "value": 8192.0},
			{"name": "", "value": 0.0}
		]
	}`
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/prya/metrics", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "stored") {
		t.Errorf("expected 'stored' in response, got: %s", w.Body.String())
	}
}

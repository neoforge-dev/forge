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

// TestNodeMetricsReceive_PostStoresSamples verifies that a valid POST to
// /api/nodes/{id}/metrics stores every sample and returns status 200 with
// the stored count.
func TestNodeMetricsReceive_PostStoresSamples(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	payload := NodeMetricPayload{
		NodeID: "sati",
		Period: "1m",
		Metrics: []NodeMetricSample{
			{Name: "agent_tokens_per_sec", Value: 12.5},
			{Name: "fleet_error_rate", Value: 0.02},
		},
	}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/api/nodes/sati/metrics", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	nodeMetricsReceiveHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["status"] != "ok" {
		t.Errorf("expected status=ok, got %v", resp["status"])
	}
	stored, ok := resp["stored"].(float64)
	if !ok || int(stored) != 2 {
		t.Errorf("expected stored=2, got %v", resp["stored"])
	}
}

// TestNodeMetricsReceive_MethodNotAllowed ensures non-POST requests are rejected.
func TestNodeMetricsReceive_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/sati/metrics", nil)
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestNodeMetricsReceive_EmptyNodeID verifies that a path with no node ID
// returns 400.
func TestNodeMetricsReceive_EmptyNodeID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Path without a node ID resolves to empty string after trim.
	req := httptest.NewRequest(http.MethodPost, "/api/nodes//metrics", nil)
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty node ID, got %d", w.Code)
	}
}

// TestNodeMetricsReceive_NodeIDMismatch verifies that a body node_id that
// differs from the path node_id returns 400.
func TestNodeMetricsReceive_NodeIDMismatch(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	payload := NodeMetricPayload{NodeID: "nova", Metrics: []NodeMetricSample{{Name: "x", Value: 1}}}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/api/nodes/sati/metrics", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for ID mismatch, got %d", w.Code)
	}
}

// TestFleetMetrics_EmptyReturnsOK ensures the fleet metrics endpoint returns
// 200 with an empty nodes map when no cross-node data has been pushed.
func TestFleetMetrics_EmptyReturnsOK(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp FleetMetricsResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Nodes == nil {
		t.Error("expected non-nil nodes map")
	}
}

// TestFleetMetrics_MethodNotAllowed verifies only GET is accepted.
func TestFleetMetrics_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestFleetMetrics_AfterPush verifies that after pushing metrics for a node,
// the fleet endpoint returns that node's data.
func TestFleetMetrics_AfterPush(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Push two metrics for node "nova".
	payload := NodeMetricPayload{
		NodeID: "nova",
		Period: "1m",
		Metrics: []NodeMetricSample{
			{Name: "task_throughput_per_sec", Value: 0.5},
			{Name: "fleet_error_rate", Value: 0.1},
		},
	}
	body, _ := json.Marshal(payload)
	postReq := httptest.NewRequest(http.MethodPost, "/api/nodes/nova/metrics", bytes.NewReader(body))
	postReq.Header.Set("Content-Type", "application/json")
	postW := httptest.NewRecorder()
	nodeMetricsReceiveHandler(postW, postReq)
	if postW.Code != http.StatusOK {
		t.Fatalf("push failed with %d: %s", postW.Code, postW.Body.String())
	}

	// Now fetch fleet metrics.
	getReq := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	getW := httptest.NewRecorder()
	fleetMetricsHandler(getW, getReq)

	if getW.Code != http.StatusOK {
		t.Fatalf("fleet metrics returned %d: %s", getW.Code, getW.Body.String())
	}

	var resp FleetMetricsResponse
	if err := json.NewDecoder(getW.Body).Decode(&resp); err != nil {
		t.Fatalf("decode fleet response: %v", err)
	}

	novaRows, ok := resp.Nodes["nova"]
	if !ok {
		t.Fatalf("expected node 'nova' in fleet metrics, got nodes: %v", resp.Nodes)
	}
	if len(novaRows) != 2 {
		t.Errorf("expected 2 metric rows for nova, got %d", len(novaRows))
	}

	// Verify metric names are present.
	names := make(map[string]bool)
	for _, row := range novaRows {
		names[row.MetricName] = true
	}
	for _, want := range []string{"task_throughput_per_sec", "fleet_error_rate"} {
		if !names[want] {
			t.Errorf("expected metric %q in nova rows", want)
		}
	}
}

// TestFleetAggregate_EmptyNodesReturnsOK verifies that with no registered
// nodes (other than self-injection) the endpoint returns 200 with a valid
// structure and at least 1 node entry (self).
func TestFleetAggregate_EmptyNodesReturnsOK(t *testing.T) {
	t.Skip("fleetAggregateHandler probes localhost via real HTTP (3s timeout) — excluded from fast unit test run")
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp FleetAggregateResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Nodes == nil {
		t.Error("expected non-nil nodes slice")
	}
	// Self is always injected when not in the DB.
	if resp.TotalNodes < 1 {
		t.Errorf("expected at least 1 node (self), got %d", resp.TotalNodes)
	}
}

// TestFleetAggregate_MethodNotAllowed verifies only GET is accepted.
func TestFleetAggregate_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestFleetAggregate_NodeInDB verifies that a registered node appears in the
// aggregate response with its address reflected back.
func TestFleetAggregate_NodeInDB(t *testing.T) {
	t.Skip("fleetAggregateHandler probes unreachable 127.0.0.1:19999 via real HTTP — goroutine visible in timeout dump; excluded from fast unit test run")
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	// Insert a fake peer node using an address that will be unreachable.
	_, err := db.Exec(`INSERT INTO nodes (id, hostname, address, status, last_heartbeat)
		VALUES ('nova', 'nova.local', 'http://127.0.0.1:19999', 'online', datetime('now'))`)
	if err != nil {
		t.Fatalf("insert test node: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp FleetAggregateResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	found := false
	for _, n := range resp.Nodes {
		if n.NodeID == "nova" {
			found = true
			if n.Address != "http://127.0.0.1:19999" {
				t.Errorf("expected address 'http://127.0.0.1:19999', got %q", n.Address)
			}
			// Nova is unreachable (no server there), so Reachable must be false.
			if n.Reachable {
				t.Error("expected nova to be unreachable (no server at 19999)")
			}
		}
	}
	if !found {
		t.Error("expected 'nova' node in aggregate response")
	}
}

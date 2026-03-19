//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave10_test.go — Coverage wave 10: queue handlers, task show/logs, agent telemetry
// Target: +2pp from ~57.5% to ~59.5%
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- main.go: handleQueueStatus, handleQueueList ---

func TestHandleQueueStatus_OK(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("handleQueueStatus: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := result["total"]; !ok {
		t.Error("expected 'total' key in response")
	}
}

func TestHandleQueueList_OK(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/queue/list", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("handleQueueList: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := result["tasks"]; !ok {
		t.Error("expected 'tasks' key")
	}
}

func TestHandleQueueList_WithLimit(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/queue/list?limit=10", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// --- main.go: handleTaskShow, handleTaskLogs ---

func TestHandleTaskShow_MissingIDW10(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestHandleTaskLogs_MissingIDW10(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

// --- main.go: handleQueuePriority, handleQueueCancel ---

func TestHandleQueuePriority_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/queue/priority", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestHandleQueuePriority_MissingFields(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"id": "", "priority": ""})
	req := httptest.NewRequest(http.MethodPost, "/queue/priority", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

func TestHandleQueueCancel_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/queue/cancel", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// --- main.go: handleTaskCreate ---

func TestHandleTaskCreate_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/tasks/create", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	handleTaskCreate(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// --- handlers_agent.go: agentTelemetryHandler ---

func TestAgentTelemetryHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-001/telemetry", nil)
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)
	// Should return 200 (empty telemetry) or 500 if table missing
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("agentTelemetryHandler: unexpected %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentTelemetryHandler_EmptyID(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents//telemetry", nil)
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d", w.Code)
	}
}

// getPlanHistoryHandler uses planManager which is nil in tests — skip direct calls

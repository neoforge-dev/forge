//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestWave253_CreateTaskHandler_BadBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader([]byte("not-json")))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave253_CreateTaskHandler_MissingFields(t *testing.T) {
	reqPayload := map[string]interface{}{
		"domain": "test-domain",
		"type":   "feature",
		"title":  "",
	}
	body, _ := json.Marshal(reqPayload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave253_CreateTaskHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	payload := map[string]interface{}{
		"domain":   "test-domain",
		"project":  "test-project",
		"type":     "feature",
		"title":    "wave253 task",
		"priority": 5,
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Domain != "test-domain" || resp.Project != "test-project" {
		t.Fatalf("unexpected task data: %+v", resp)
	}
}

func TestWave253_AgentsHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	rr := httptest.NewRecorder()

	agentsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave253_AgentContextHandler_InvalidID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//context", nil)
	rr := httptest.NewRecorder()

	agentContextHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing agent ID, got %d", rr.Code)
	}
}

func TestWave253_AgentContextHandler_WithLeadContext(t *testing.T) {
	tmpDir := t.TempDir()
	origRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Setenv("FORGE_ROOT", origRoot)

	contextDir := filepath.Join(tmpDir, ".forge", "context", "test-domain")
	if err := os.MkdirAll(contextDir, 0o755); err != nil {
		t.Fatalf("failed to create context dir: %v", err)
	}
	leadContent := "- Context: 42%\n**Lead:** agent-1\n"
	if err := os.WriteFile(filepath.Join(contextDir, "lead-context.md"), []byte(leadContent), 0o644); err != nil {
		t.Fatalf("failed to write lead-context: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/context", nil)
	rr := httptest.NewRecorder()

	agentContextHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var resp AgentContextResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.AgentID != "agent-1" {
		t.Fatalf("unexpected agent id: %s", resp.AgentID)
	}
	if resp.ContextPct != 42 {
		t.Fatalf("unexpected context pct: %f", resp.ContextPct)
	}
}

func insertTelemetryRow(t *testing.T, db *sql.DB, agentID string) {
	t.Helper()
	_, err := db.Exec(`
		INSERT INTO agent_telemetry (agent_id, node, context_pct, task_id, status, tool, metadata, timestamp)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, agentID, "node-42", 42.0, "task-1", "idle", "http", "{}", time.Now().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("insert telemetry row: %v", err)
	}
}

func TestWave253_AgentTelemetryHandler_DBUnavailable(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/telemetry", nil)
	rr := httptest.NewRecorder()

	agentTelemetryHandler(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when DB missing, got %d", rr.Code)
	}
}

func TestWave253_AgentTelemetryHandler_WithRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	insertTelemetryRow(t, db, "agent-1")

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/telemetry?limit=1", nil)
	rr := httptest.NewRecorder()

	agentTelemetryHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var resp struct {
		AgentID string        `json:"agent_id"`
		Count   int           `json:"count"`
		Rows    []interface{} `json:"rows"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.AgentID != "agent-1" {
		t.Fatalf("unexpected agent id: %s", resp.AgentID)
	}
	if resp.Count != 1 || len(resp.Rows) != 1 {
		t.Fatalf("expected 1 row, got %d", resp.Count)
	}
}

func TestWave253_AgentMetricsHandler_NoDB(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/metrics?limit=1", nil)
	rr := httptest.NewRecorder()

	agentMetricsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	var resp struct {
		AgentID string        `json:"agent_id"`
		Count   int           `json:"count"`
		Metrics []interface{} `json:"metrics"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.AgentID != "agent-1" || resp.Count != 0 {
		t.Fatalf("expected empty metrics result, got %+v", resp)
	}
}

func TestWave253_AgentMetricsHandler_WithRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	insertTelemetryRow(t, db, "agent-1")

	since := time.Now().Add(-time.Minute).Format(time.RFC3339)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/metrics?limit=1&since="+since, nil)
	rr := httptest.NewRecorder()

	agentMetricsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var resp struct {
		AgentID string                   `json:"agent_id"`
		Count   int                      `json:"count"`
		Metrics []map[string]interface{} `json:"metrics"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.AgentID != "agent-1" || resp.Count != 1 {
		t.Fatalf("unexpected metrics response: %+v", resp)
	}
}

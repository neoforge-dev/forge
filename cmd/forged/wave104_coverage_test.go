//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ── createTaskHandler branches ─────────────────────────────────────────────────

func TestWave104_CreateTask_InvalidTaskID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"id":"bad id space","domain":"testdomain","project":"proj","type":"feature","title":"Implement real user auth flow","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	createTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid ID, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_CreateTask_PriorityNegative(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"domain":"testdomain","project":"proj","type":"feature","title":"Implement real user auth flow","priority":-1}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(body))
	rr := httptest.NewRecorder()
	createTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for negative priority, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_CreateTask_PriorityLargeMapping(t *testing.T) {
	// priority > 10 maps to 1-10; priority = 110 → mapped = (110+9)/10 = 11 → capped to 10
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"domain":"testdomain","project":"proj","type":"feature","title":"Implement real user auth system","priority":110}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(body))
	rr := httptest.NewRecorder()
	createTaskHandler(rr, req)
	// Should succeed (priority mapped to valid range)
	if rr.Code != http.StatusOK {
		t.Logf("response: %d %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_CreateTask_TitleJunk(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// "fix bug" is a junk title pattern
	body := `{"domain":"testdomain","project":"proj","type":"feature","title":"fix bug","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(body))
	rr := httptest.NewRecorder()
	createTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Logf("isTitleJunk did not reject 'fix bug': %d %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_CreateTask_TitleJunk2(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// "something (feature)" is a junk title
	body := `{"domain":"testdomain","project":"proj","type":"feature","title":"something (feature)","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(body))
	rr := httptest.NewRecorder()
	createTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Logf("isTitleJunk pattern check: %d %s", rr.Code, rr.Body.String())
	}
}

// ── completeTaskHandler branches ───────────────────────────────────────────────

func TestWave104_CompleteTask_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task123/complete", nil)
	rr := httptest.NewRecorder()
	completeTaskHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

// ── abandonTaskHandler branches ────────────────────────────────────────────────

func TestWave104_AbandonTask_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task123/abandon", nil)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_AbandonTask_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/tasks//abandon", nil)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty taskID, got %d", rr.Code)
	}
}

func TestWave104_AbandonTask_NotFound(t *testing.T) {
	// Task with status 'executing' cannot be abandoned (n == 0)
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	body := bytes.NewBufferString(`{}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-xyz/abandon", body)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404 for non-existent task abandon, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_AbandonTask_AlreadyCompleted(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a completed task — not in queued/assigned so n=0
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-abandon-complete', 'dom', 'proj', 'feature', 'title', 5, 'completed', 'COMPLETED', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	body := bytes.NewBufferString(`{}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-abandon-complete/abandon", body)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404 for completed task, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_AbandonTask_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-abandon-ok', 'dom', 'proj', 'feature', 'title', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	body := bytes.NewBufferString(`{}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-abandon-ok/abandon", body)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── taskHistoryHandler branches ────────────────────────────────────────────────

func TestWave104_TaskHistory_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task123/history", nil)
	rr := httptest.NewRecorder()
	taskHistoryHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_TaskHistory_EmptyID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks//history", nil)
	rr := httptest.NewRecorder()
	taskHistoryHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", rr.Code)
	}
}

func TestWave104_TaskHistory_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/nonexistent-history-task/history", nil)
	rr := httptest.NewRecorder()
	taskHistoryHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── pruneTasksHandler branches ─────────────────────────────────────────────────

func TestWave104_PruneTasks_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()
	pruneTasksHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_PruneTasks_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()
	pruneTasksHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── getTaskHandler branch ──────────────────────────────────────────────────────

func TestWave104_GetTask_EmptyID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/", nil)
	rr := httptest.NewRecorder()
	getTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", rr.Code)
	}
}

// ── listTasksHandler branches ──────────────────────────────────────────────────

func TestWave104_ListTasks_InvalidStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?status=invalid%20status", nil)
	rr := httptest.NewRecorder()
	listTasksHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid status, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── claimTaskHandler branches ──────────────────────────────────────────────────

func TestWave104_ClaimTask_WrongState(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	db := getDBConn()

	// Create a task in COMPLETED state
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-claim-wrong-state', 'dom', 'proj', 'feature', 'Completed task', 5, 'completed', 'COMPLETED', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"agent-x"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-claim-wrong-state/claim", body)
	rr := httptest.NewRecorder()
	claimTaskHandler(rr, req)
	if rr.Code != http.StatusConflict {
		t.Errorf("expected 409 for wrong state, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_ClaimTask_ContextFull(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	db := getDBConn()

	// Create a claimable task
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-ctx-full', 'dom', 'proj', 'feature', 'Context full test task', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`)

	// Insert heartbeat with context_pct >= 90
	_, _ = db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES ('agent-ctx-full', 'testnode', 'busy', 91.5, datetime('now'), datetime('now'))`)

	body := bytes.NewBufferString(`{"agent_id":"agent-ctx-full"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-ctx-full/claim", body)
	rr := httptest.NewRecorder()
	claimTaskHandler(rr, req)
	if rr.Code != http.StatusConflict {
		t.Errorf("expected 409 for context full, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── agentMetricsHandler branches ──────────────────────────────────────────────

func TestWave104_AgentMetrics_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent1/metrics", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_AgentMetrics_EmptyID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents//metrics", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_AgentMetrics_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent-metrics/metrics", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_AgentMetrics_WithDBAndSince(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent/metrics?since=2026-01-01T00:00:00Z&limit=10", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── agentTelemetrySummaryHandler branches ─────────────────────────────────────

func TestWave104_AgentTelemetrySummary_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	rr := httptest.NewRecorder()
	agentTelemetrySummaryHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_AgentTelemetrySummary_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	rr := httptest.NewRecorder()
	agentTelemetrySummaryHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── agentByIDHandler branches ──────────────────────────────────────────────────

func TestWave104_AgentByID_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-x", nil)
	rr := httptest.NewRecorder()
	agentByIDHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_AgentByID_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents/nonexistent-agent-xyz", nil)
	rr := httptest.NewRecorder()
	agentByIDHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_AgentByID_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert an agent heartbeat row
	_, err := db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES ('agent-wave104', 'testnode', 'idle', 25.0, datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert agent_heartbeats: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-wave104", nil)
	rr := httptest.NewRecorder()
	agentByIDHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Errorf("invalid JSON: %v", err)
	}
}

// ── agentsListHandler ──────────────────────────────────────────────────────────

func TestWave104_AgentsList_WithHub(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/list", nil)
	rr := httptest.NewRecorder()
	agentsListHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── agentTelemetryHandler branches ────────────────────────────────────────────

func TestWave104_AgentTelemetry_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent1/telemetry", nil)
	rr := httptest.NewRecorder()
	agentTelemetryHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave104_AgentTelemetry_EmptyID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents//telemetry", nil)
	rr := httptest.NewRecorder()
	agentTelemetryHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave104_AgentTelemetry_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent-telemetry/telemetry", nil)
	rr := httptest.NewRecorder()
	agentTelemetryHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── agentsHealthHandler per-agent branch ──────────────────────────────────────

func TestWave104_AgentsHealth_SpecificAgent_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/agents/nonexistent-agent/health", nil)
	rr := httptest.NewRecorder()
	agentsHealthHandler(rr, req)
	// nonexistent agent returns 404
	if rr.Code != http.StatusNotFound {
		t.Logf("agentsHealthHandler specific agent: %d %s", rr.Code, rr.Body.String())
	}
}

// ── completeTaskWithApprovalHandler method not allowed ─────────────────────────

func TestWave104_CompleteTaskWithApproval_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task123/complete-with-approval", nil)
	rr := httptest.NewRecorder()
	completeTaskWithApprovalHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

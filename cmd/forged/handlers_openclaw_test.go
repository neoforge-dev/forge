//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenClawChat_CreateTask(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := map[string]string{
		"from":    "telegram:user",
		"text":    "create task: fix login bug",
		"chat_id": "123",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawChatResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.TaskID == "" {
		t.Error("expected task_id to be set")
	}
	if !strings.Contains(resp.Message, "Task created") {
		t.Errorf("expected message to contain 'Task created', got: %s", resp.Message)
	}

	// Verify task was actually created (GetTask doesn't read title, so we verify domain/status)
	task, err := taskQueue.GetTask(context.Background(), resp.TaskID)
	if err != nil {
		t.Fatalf("failed to get created task: %v", err)
	}
	if task.Domain != "openclaw" {
		t.Errorf("expected domain 'openclaw', got '%s'", task.Domain)
	}
	if task.Project != "bridge" {
		t.Errorf("expected project 'bridge', got '%s'", task.Project)
	}
	if task.Status != TaskStatusQueued {
		t.Errorf("expected status 'queued', got '%s'", task.Status)
	}
	if task.Type != TaskTypeFeature {
		t.Errorf("expected type 'feature', got '%s'", task.Type)
	}
}

func TestOpenClawChat_ParsePrefixes(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	testCases := []struct {
		name string
		text string
	}{
		{
			name: "create task prefix",
			text: "create task: fix the login bug",
		},
		{
			name: "task prefix",
			text: "task: update documentation",
		},
		{
			name: "add task prefix",
			text: "add task: refactor auth module",
		},
		{
			name: "bare text no prefix",
			text: "just a simple task description",
		},
		{
			name: "mixed case prefix",
			text: "CREATE TASK: handle edge case",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			payload := map[string]string{
				"from": "telegram:user",
				"text": tc.text,
			}
			body, _ := json.Marshal(payload)
			req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
			rr := httptest.NewRecorder()

			openclawHandler(rr, req)

			if rr.Code != http.StatusOK {
				t.Errorf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
			}

			var resp OpenClawChatResponse
			if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
				t.Fatalf("failed to decode response: %v", err)
			}

			// Verify task was created with expected domain/project
			task, err := taskQueue.GetTask(context.Background(), resp.TaskID)
			if err != nil {
				t.Fatalf("failed to get created task: %v", err)
			}
			if task.Domain != "openclaw" {
				t.Errorf("expected domain 'openclaw', got '%s'", task.Domain)
			}
			if task.Project != "bridge" {
				t.Errorf("expected project 'bridge', got '%s'", task.Project)
			}
		})
	}
}

func TestOpenClawChat_MissingText(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := map[string]string{
		"from":    "telegram:user",
		"chat_id": "123",
		// No text field
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d. Body: %s", rr.Code, rr.Body.String())
	}
}

func TestOpenClawChat_EmptyText(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := map[string]string{
		"from": "telegram:user",
		"text": "   ", // Only whitespace
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d. Body: %s", rr.Code, rr.Body.String())
	}
}

func TestOpenClawStatus_Returns200(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert some test agents using the actual schema from migration 010/041
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, last_seen, connected_at) 
		VALUES ('agent1', 'node1', 'online', 'task1', datetime('now'), datetime('now'))`)
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, last_seen, connected_at) 
		VALUES ('agent2', 'node1', 'online', '', datetime('now'), datetime('now'))`)

	// Insert some test tasks
	task1 := Task{
		ID:       "test-task-queued",
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	task2 := Task{
		ID:       "test-task-assigned",
		Domain:   "test",
		Project:  "test-proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusAssigned,
	}
	taskQueue.Enqueue(context.Background(), task1)
	taskQueue.Enqueue(context.Background(), task2)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawStatusResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Daemon != "ok" {
		t.Errorf("expected daemon 'ok', got '%s'", resp.Daemon)
	}
	if resp.Agents != 2 {
		t.Errorf("expected 2 agents, got %d", resp.Agents)
	}
	if resp.Busy != 1 {
		t.Errorf("expected 1 busy agent, got %d", resp.Busy)
	}
	if resp.TasksQueued != 1 {
		t.Errorf("expected 1 queued task, got %d", resp.TasksQueued)
	}
	if resp.TasksAssigned != 1 {
		t.Errorf("expected 1 assigned task, got %d", resp.TasksAssigned)
	}
}

func TestOpenClawStatus_EmptyDB(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// No agents or tasks - just verify it returns 200 with zero counts
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawStatusResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Daemon != "ok" {
		t.Errorf("expected daemon 'ok', got '%s'", resp.Daemon)
	}
	if resp.Agents != 0 {
		t.Errorf("expected 0 agents, got %d", resp.Agents)
	}
	if resp.Busy != 0 {
		t.Errorf("expected 0 busy agents, got %d", resp.Busy)
	}
}

func TestOpenClawChat_WrongMethod(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// GET to /chat returns 404 (not routed to the handler)
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/chat", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	// The handler routes based on URL suffix and method
	// GET /chat doesn't match any route, returns 404
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}
}

func TestOpenClawStatus_WrongMethod(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/status", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	// POST to /status returns 404 (not routed, goes to default not found)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}
}

func TestOpenClawChat_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader([]byte("invalid json")))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestOpenClawIngest_PersistsResultAndEvent(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpRoot := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpRoot)

	payload := map[string]interface{}{
		"task_id":     "TRINITY-INGEST-1",
		"status":      "ok",
		"agent":       "kimi",
		"node":        "sati",
		"summary":     "routing completed",
		"result_path": ".forge/results/TRINITY-INGEST-1.json",
		"artifacts":   []string{"harness/forge_harness/trinity.py"},
	}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	resultFile := filepath.Join(tmpRoot, ".forge", "results", "TRINITY-INGEST-1.json")
	if _, err := os.Stat(resultFile); err != nil {
		t.Fatalf("expected result file to exist: %v", err)
	}

	db := getDBConn()
	if db == nil {
		t.Fatal("expected test DB connection")
	}
	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM task_events WHERE task_id = ? AND event_type = 'task.completed'`, "TRINITY-INGEST-1").Scan(&count); err != nil {
		t.Fatalf("count task_events: %v", err)
	}
	if count != 1 {
		t.Fatalf("expected 1 completion event, got %d", count)
	}
}

func TestOpenClawIngest_RequiresFields(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader([]byte(`{"task_id":"x"}`)))
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rr.Code)
	}
}

func TestOpenClaw_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Test GET to unknown path
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/unknown", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}

	// Test POST to unknown path
	req = httptest.NewRequest(http.MethodPost, "/api/openclaw/unknown", nil)
	rr = httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}
}

func TestOpenClaw_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Test PUT to /chat (method not allowed at top level)
	req := httptest.NewRequest(http.MethodPut, "/api/openclaw/chat", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}

	// Test DELETE to /status
	req = httptest.NewRequest(http.MethodDelete, "/api/openclaw/status", nil)
	rr = httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestParseTaskFromMessage(t *testing.T) {
	testCases := []struct {
		input    string
		expected string
	}{
		{"create task: test", "test"},
		{"CREATE TASK: test", "test"},
		{"task: test", "test"},
		{"TASK: test", "test"},
		{"add task: test", "test"},
		{"ADD TASK: test", "test"},
		{"  create task:  test  ", "test"},
		{"no prefix here", "no prefix here"},
		{"", ""},
		{"   ", ""},
	}

	for _, tc := range testCases {
		result := parseTaskFromMessage(tc.input)
		if result != tc.expected {
			t.Errorf("parseTaskFromMessage(%q) = %q, expected %q", tc.input, result, tc.expected)
		}
	}
}

func TestExtractDomainAndTypeFromMessage(t *testing.T) {
	tests := []struct {
		name           string
		text           string
		wantDomain     string
		wantTypeHint   string
	}{
		{
			name:         "no hints — defaults",
			text:         "fix the login bug",
			wantDomain:   "",
			wantTypeHint: "feature",
		},
		{
			name:         "domain: prefix",
			text:         "domain: interview-simulator fix auth",
			wantDomain:   "interview-simulator",
			wantTypeHint: "feature",
		},
		{
			name:         "for domain: prefix",
			text:         "for domain: voice-coach add retry logic",
			wantDomain:   "voice-coach",
			wantTypeHint: "feature",
		},
		{
			name:         "project: prefix",
			text:         "project: code-atlas add graph view",
			wantDomain:   "code-atlas",
			wantTypeHint: "feature",
		},
		{
			name:         "type: prefix",
			text:         "type: bug fix the crash",
			wantDomain:   "",
			wantTypeHint: "bug",
		},
		{
			name:         "task type: prefix",
			text:         "task type: research investigate caching",
			wantDomain:   "",
			wantTypeHint: "research",
		},
		{
			name:         "domain and type together",
			text:         "domain: study-flow type: bug user cannot log in",
			wantDomain:   "study-flow",
			wantTypeHint: "bug",
		},
		{
			name:         "domain with trailing punctuation",
			text:         "domain: babybites, add COPPA gate",
			wantDomain:   "babybites",
			wantTypeHint: "feature",
		},
		{
			name:         "uppercase domain value normalised in type only",
			text:         "type: BUG uppercase type",
			wantDomain:   "",
			wantTypeHint: "bug",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			domain, typeHint := extractDomainAndTypeFromMessage(tc.text)
			if domain != tc.wantDomain {
				t.Errorf("domain: got %q, want %q", domain, tc.wantDomain)
			}
			if typeHint != tc.wantTypeHint {
				t.Errorf("typeHint: got %q, want %q", typeHint, tc.wantTypeHint)
			}
		})
	}
}

func TestOpenClawChat_WithDomainHint(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Message with a domain hint — exercises extractDomainAndTypeFromMessage +
	// the stage gate advisory path inside openclawChatHandler.
	payload := map[string]string{
		"from": "telegram:user",
		"text": "domain: interview-simulator type: bug fix auth refresh",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	// Should always succeed — stage gate is advisory only
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawChatResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.TaskID == "" {
		t.Error("expected non-empty task_id")
	}
}

func TestOpenClawIngest_FailStatus(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpRoot := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpRoot)

	payload := map[string]interface{}{
		"task_id": "INGEST-FAIL-1",
		"status":  "fail",
		"agent":   "kimi",
		"node":    "sati",
		"summary": "task failed due to build error",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	// Verify task.failed event was inserted
	db := getDBConn()
	if db == nil {
		t.Skip("no db")
	}
	var eventType string
	err := db.QueryRow(`SELECT event_type FROM task_events WHERE task_id = ?`, "INGEST-FAIL-1").Scan(&eventType)
	if err != nil {
		t.Fatalf("query event: %v", err)
	}
	if eventType != "task.failed" {
		t.Errorf("expected event_type 'task.failed', got %q", eventType)
	}
}

func TestOpenClawIngest_NeedsApprovalStatus(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpRoot := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpRoot)

	payload := map[string]interface{}{
		"task_id": "INGEST-APPROVAL-1",
		"status":  "needs_approval",
		"agent":   "glm",
		"node":    "sati",
		"summary": "task needs human approval",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	db := getDBConn()
	if db == nil {
		t.Skip("no db")
	}
	var eventType string
	err := db.QueryRow(`SELECT event_type FROM task_events WHERE task_id = ?`, "INGEST-APPROVAL-1").Scan(&eventType)
	if err != nil {
		t.Fatalf("query event: %v", err)
	}
	if eventType != "task.needs_approval" {
		t.Errorf("expected event_type 'task.needs_approval', got %q", eventType)
	}
}

// TestOpenClawChatHandler_EnqueueFails exercises the enqueue error path
// (handlers_openclaw.go:218.70,222.3) when the DB is closed so Enqueue fails.
func TestOpenClawChatHandler_EnqueueFails(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close DB so taskQueue.Enqueue fails.
	db := getDBConn()
	db.Close()

	body, _ := json.Marshal(OpenClawMessage{
		Text: "Fix the authentication bug in the login service",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawChatHandler(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for enqueue failure in openclawChatHandler, got %d: %s", rr.Code, rr.Body.String())
	}
}

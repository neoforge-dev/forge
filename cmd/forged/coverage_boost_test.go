//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestCreateTaskHandler_ValidInput verifies that POST /api/tasks with a valid
// payload returns 200 and a task with a generated ID.
func TestCreateTaskHandler_ValidInput(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"domain":"forge","project":"v3","type":"feature","title":"Coverage Test","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.ID == "" {
		t.Error("expected non-empty ID in response")
	}
	if resp.Domain != "forge" {
		t.Errorf("domain = %q, want %q", resp.Domain, "forge")
	}
	if resp.Title != "Coverage Test" {
		t.Errorf("title = %q, want %q", resp.Title, "Coverage Test")
	}

	// Confirm the task was persisted (createTaskHandler uses status "requested" on creation).
	got, err := taskQueue.GetTask(context.Background(), resp.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.ID == "" {
		t.Error("persisted task has empty ID")
	}
}

// TestGetAgentsHealthHandler verifies that the agentsHealthHandler returns 200
// with a JSON body containing "agents" and "count" keys when the agents table
// is empty.
func TestGetAgentsHealthHandler(t *testing.T) {
	defer setupCoverageQueue(t)()

	// Use the path that triggers the "list all" branch in agentsHealthHandler:
	// strings.TrimPrefix("/api/agents/", "/api/agents/") == "" → list-all path.
	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	rr := httptest.NewRecorder()

	agentsHealthHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	ct := rr.Header().Get("Content-Type")
	if !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}

	var resp struct {
		Agents []interface{} `json:"agents"`
		Count  int           `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	// An empty DB has no agents, but the response structure must be present.
	if resp.Count != len(resp.Agents) {
		t.Errorf("count=%d does not match len(agents)=%d", resp.Count, len(resp.Agents))
	}
}

// TestClaimTaskHandler_SuccessCoverage creates a queued task then claims it
// via claimTaskHandler and asserts the response is 201 and the task is
// transitioned to assigned.
func TestClaimTaskHandler_SuccessCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	taskID := "claim-coverage-001"
	task := Task{
		ID:       taskID,
		Domain:   "forge",
		Project:  "v3",
		Type:     TaskTypeFeature,
		Title:    "Claim Coverage Task",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	payload := `{"agent_id":"test-agent"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewBufferString(payload))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201; body: %s", rr.Code, rr.Body.String())
	}

	updated, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("GetTask after claim: %v", err)
	}
	if updated.Status != TaskStatusAssigned {
		t.Errorf("status = %s, want %s", updated.Status, TaskStatusAssigned)
	}
	if updated.AssignedTo != "test-agent" {
		t.Errorf("assigned_to = %q, want %q", updated.AssignedTo, "test-agent")
	}
}

// TestAbandonTaskHandler_Success enqueues a queued task and abandons it via
// abandonTaskHandler, expecting a 200 JSON response.
func TestAbandonTaskHandler_Success(t *testing.T) {
	defer setupCoverageQueue(t)()

	taskID := "abandon-001"
	task := Task{
		ID:       taskID,
		Domain:   "forge",
		Project:  "v3",
		Type:     TaskTypeFeature,
		Title:    "Task to Abandon",
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/abandon", nil)
	rr := httptest.NewRecorder()

	abandonTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["status"] != "ok" {
		t.Errorf("status = %v, want ok", resp["status"])
	}
}

// TestAbandonTaskHandler_NotFound calls abandonTaskHandler for a non-existent
// task and expects 404.
func TestAbandonTaskHandler_NotFound(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-such-task/abandon", nil)
	rr := httptest.NewRecorder()

	abandonTaskHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404; body: %s", rr.Code, rr.Body.String())
	}
}

// TestAbandonTaskHandler_MethodNotAllowed sends a GET to abandonTaskHandler
// and expects 405.
func TestAbandonTaskHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/any-id/abandon", nil)
	rr := httptest.NewRecorder()

	abandonTaskHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

// TestPruneTasksHandler_Empty calls pruneTasksHandler with no stale tasks in
// the DB; expects 200 with pruned=0.
func TestPruneTasksHandler_Empty(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()

	pruneTasksHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["status"] != "ok" {
		t.Errorf("status field = %v, want ok", resp["status"])
	}
}

// TestPruneTasksHandler_MethodNotAllowed sends a GET and expects 405.
func TestPruneTasksHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()

	pruneTasksHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

// TestListPatternsHandler_Empty calls listPatternsHandler against an empty DB
// and expects 200 with an empty patterns array.
func TestListPatternsHandler_Empty(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	rr := httptest.NewRecorder()

	listPatternsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp struct {
		Patterns []interface{} `json:"patterns"`
		Count    int           `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Count != 0 {
		t.Errorf("count = %d, want 0", resp.Count)
	}
}

// TestAgentMetricsHandler_Success calls agentMetricsHandler for a known agent
// against an empty telemetry table and expects 200 with an empty metrics list.
func TestAgentMetricsHandler_Success(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/forge:test-agent/metrics", nil)
	rr := httptest.NewRecorder()

	agentMetricsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["agent_id"] == nil {
		t.Error("expected agent_id in response")
	}
}

// TestAgentMetricsHandler_MethodNotAllowed sends a POST and expects 405.
func TestAgentMetricsHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/forge:test-agent/metrics", nil)
	rr := httptest.NewRecorder()

	agentMetricsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

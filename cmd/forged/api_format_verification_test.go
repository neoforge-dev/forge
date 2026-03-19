package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"github.com/stretchr/testify/assert"
)

func TestVerifyCreateTaskAPIFormat(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Use valid fields and types according to Task struct and createTaskHandler validation
	reqBody, _ := json.Marshal(map[string]interface{}{
		"domain":   "test-domain",
		"project":  "test-project",
		"type":     "feature",
		"title":    "API Format Verification Test",
		"priority": 5,
	})
	req := httptest.NewRequest("POST", "/api/tasks", bytes.NewBuffer(reqBody))
	w := httptest.NewRecorder()

	createTaskHandler(w, req)

	if w.Code != http.StatusCreated && w.Code != http.StatusOK {
		t.Errorf("expected status 201 or 200, got %d. Body: %s", w.Code, w.Body.String())
	}
	assert.Equal(t, "application/json", w.Header().Get("Content-Type"))

	var resp map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &resp)
	if err != nil {
		t.Fatalf("failed to unmarshal response: %v. Body: %s", err, w.Body.String())
	}

	// Verify required fields for a Task object
	// TASK-SURE-WAVE-741: Verify response format
	assert.Contains(t, resp, "id", "Response missing 'id'")
	assert.Contains(t, resp, "title", "Response missing 'title'")
	assert.Contains(t, resp, "status", "Response missing 'status'")
	assert.Contains(t, resp, "domain", "Response missing 'domain'")
	assert.Contains(t, resp, "project", "Response missing 'project'")
	assert.Contains(t, resp, "type", "Response missing 'type'")
	assert.Contains(t, resp, "priority", "Response missing 'priority'")
	assert.Contains(t, resp, "state", "Response missing 'state'")
}

func TestVerifyClaimTaskAPIFormat(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// 1. Create a task first
	taskID := "test-task-" + t.Name()
	db := getDBConn()
	
	// Ensure table exists and insert with correct types
	_, err := db.Exec("INSERT INTO tasks (id, title, status, priority, domain, project, type, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		taskID, "Test Task", "queued", 5, "test-domain", "test-project", "feature", "QUEUED")
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	// 2. Claim the task
	reqBody, _ := json.Marshal(map[string]interface{}{
		"agent_id": "test-agent",
	})
	req := httptest.NewRequest("POST", "/api/tasks/"+taskID+"/claim", bytes.NewBuffer(reqBody))
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected status 201 Created, got %d. Body: %s", w.Code, w.Body.String())
	}
	assert.Equal(t, "application/json", w.Header().Get("Content-Type"))

	var resp map[string]interface{}
	err = json.Unmarshal(w.Body.Bytes(), &resp)
	if err != nil {
		t.Fatalf("failed to unmarshal response: %v. Body: %s", err, w.Body.String())
	}

	// Verify the specific claim response format (TASK-SURE-WAVE-741)
	assert.Equal(t, "claimed", resp["status"], "Field 'status' should be 'claimed'")
	assert.Equal(t, "test-agent", resp["agent_id"], "Field 'agent_id' should match")
	assert.Contains(t, resp, "task", "Response missing 'task' object")
	
	taskPart, ok := resp["task"].(map[string]interface{})
	if !ok {
		t.Fatalf("response 'task' field missing or not a map. Body: %s", w.Body.String())
	}
	assert.Equal(t, taskID, taskPart["id"])
	assert.Equal(t, "assigned", taskPart["status"])
	assert.Equal(t, "test-agent", taskPart["assigned_to"])
}

func TestVerifyGetTaskAPIFormat(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	taskID := "test-get-task"
	db := getDBConn()
	_, err := db.Exec("INSERT INTO tasks (id, title, status, priority, domain, project, type, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		taskID, "Get Task Test", "queued", 5, "test-domain", "test-project", "feature", "QUEUED")
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	req := httptest.NewRequest("GET", "/api/tasks/"+taskID, nil)
	w := httptest.NewRecorder()

	getTaskHandler(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, "application/json", w.Header().Get("Content-Type"))

	var resp map[string]interface{}
	err = json.Unmarshal(w.Body.Bytes(), &resp)
	if err != nil {
		t.Fatalf("failed to unmarshal response: %v. Body: %s", err, w.Body.String())
	}

	assert.Equal(t, taskID, resp["id"])
	assert.Equal(t, "test-domain", resp["domain"])
	assert.Equal(t, "test-project", resp["project"])
}

func TestVerifyDebugAPIFormat(t *testing.T) {
	// TASK-WISE-FLUX-950: API format debug test
	// This test intentionally triggers an error to verify the error response format.
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Missing title should trigger 400
	reqBody, _ := json.Marshal(map[string]interface{}{
		"domain": "test",
		"project": "test",
		"type": "feature",
	})
	req := httptest.NewRequest("POST", "/api/tasks", bytes.NewBuffer(reqBody))
	w := httptest.NewRecorder()

	createTaskHandler(w, req)

	assert.Equal(t, http.StatusBadRequest, w.Code)
	
	var resp map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &resp)
	assert.NoError(t, err)
	
	// Check error response format (typically {"error": "..."})
	assert.Contains(t, resp, "error")
	t.Logf("Debug: received expected error response: %s", w.Body.String())
}

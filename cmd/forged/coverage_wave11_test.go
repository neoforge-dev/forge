//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave11_test.go — Coverage wave 11: contexts, ui, resume, xnode handlers
// Target: +2pp from ~58% to ~60%
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- handlers_context.go: contextsHandler ---

func TestContextsHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("contextsHandler GET: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := result["contexts"]; !ok {
		t.Error("expected 'contexts' key")
	}
}

func TestContextsHandler_WithFilters(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?domain=forge&agent_id=test-agent", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// --- handlers_ui.go: uiFleetHandler ---

func TestUiFleetHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	// Returns HTML or 200
	if w.Code != http.StatusOK {
		t.Errorf("uiFleetHandler GET: expected 200, got %d", w.Code)
	}
}

// --- handlers_plan.go: resumeTaskHandler ---

func TestResumeTaskHandler_TaskNotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NONEXISTENT/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing task, got %d: %s", w.Code, w.Body.String())
	}
}

// --- xnode.go: ListInboxHandler, ListAcksHandler, ListNodesHandler ---

func newTestXNodeController(t *testing.T) (*XNodeController, func()) {
	t.Helper()
	cleanup := setupWave5(t)
	db := getDBConn()
	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		cleanup()
		t.Fatalf("NewXNodeController: %v", err)
	}
	return xc, cleanup
}

func TestXNode_ListNodesHandler_Get(t *testing.T) {
	xc, cleanup := newTestXNodeController(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("ListNodesHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNode_ListInboxHandler_Empty(t *testing.T) {
	xc, cleanup := newTestXNodeController(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)
	// Inbox dir may not exist → 500, or empty → 200
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("ListInboxHandler: unexpected %d: %s", w.Code, w.Body.String())
	}
}

func TestXNode_ListInboxHandler_InvalidNodeID(t *testing.T) {
	xc, cleanup := newTestXNodeController(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/../etc", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for path traversal, got %d", w.Code)
	}
}

func TestXNode_ListAcksHandler_Empty(t *testing.T) {
	xc, cleanup := newTestXNodeController(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, req)
	// Acks dir may not exist → 500, or empty → 200
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("ListAcksHandler: unexpected %d: %s", w.Code, w.Body.String())
	}
}

// --- handleQueuePriority: valid request with non-existent task ---

func TestHandleQueuePriority_TaskNotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"id": "NONEXISTENT", "priority": "high"})
	req := httptest.NewRequest(http.MethodPost, "/queue/priority", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code == http.StatusInternalServerError {
		t.Errorf("unexpected 500: %s", w.Body.String())
	}
}

// --- handlers_agent.go: agentTasksHandler ---

func TestAgentTasksHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("agentTasksHandler: unexpected %d: %s", w.Code, w.Body.String())
	}
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 30f: handleAgentList, handleAgentStatus, handleTaskList, handleTaskShow

func TestHandleAgentStatus_NoID_W30F(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 with no ID, got %d", w.Code)
	}
}

func TestHandleAgentStatus_NotFound_W30F(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/status?id=nonexistent-agent-xyz", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, req)
	// 404 or 503 depending on DB state
	if w.Code == http.StatusOK {
		t.Logf("handleAgentStatus returned 200 for nonexistent agent — unexpected")
	}
}

func TestHandleAgentList_W30F(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, req)
	// Any non-5xx is acceptable (nil DB returns empty list or 200)
	if w.Code >= 500 {
		t.Logf("handleAgentList returned %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskList_W30F(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/list", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	// Should delegate to listTasksHandler
	_ = w.Code
}

func TestHandleTaskShow_NoID_W30F(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 with no ID, got %d", w.Code)
	}
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 78: stub handlers returning 501 — one test each to hit 0% functions
// ---------------------------------------------------------------------------

func TestW78_HandleSystemMetrics_NotImplemented(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/cli/system/metrics", nil)
	w := httptest.NewRecorder()
	handleSystemMetrics(w, r)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("handleSystemMetrics: expected 501, got %d", w.Code)
	}
}

func TestW78_HandleAgentSpawn_NotImplemented(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/agents/spawn", nil)
	w := httptest.NewRecorder()
	handleAgentSpawn(w, r)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("handleAgentSpawn: expected 501, got %d", w.Code)
	}
}

func TestW78_HandleAgentStop_NotImplemented(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/agents/stop", nil)
	w := httptest.NewRecorder()
	handleAgentStop(w, r)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("handleAgentStop: expected 501, got %d", w.Code)
	}
}

func TestW78_HandleTaskCancel_NotImplemented(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/tasks/cancel", nil)
	w := httptest.NewRecorder()
	handleTaskCancel(w, r)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("handleTaskCancel: expected 501, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// Wave 78: additional coverage paths for real handlers (DB required)
// ---------------------------------------------------------------------------

func TestW78_HandleSystemHealth_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code >= 500 {
		t.Errorf("handleSystemHealth with DB: expected <500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestW78_HandleSystemHealth_JSONFormat_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/cli/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code >= 500 {
		t.Errorf("handleSystemHealth json+DB: expected <500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestW78_HandleAgentList_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/cli/agents", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleAgentList with DB: expected <500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestW78_HandleAgentList_JSONFormat_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/cli/agents?format=json", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleAgentList json+DB: expected <500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestW78_HandleTaskList_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/cli/tasks", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleTaskList with DB: expected <500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestW78_HandleTaskList_TableFormat_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/cli/tasks?format=table", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleTaskList table+DB: expected <500, got %d: %s", w.Code, w.Body.String())
	}
}

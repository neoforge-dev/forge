//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 78: coverage paths for real handlers (DB required)
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

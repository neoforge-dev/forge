//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 190: handlers_dispatch.go + handlers_pattern.go + handlers_agent.go easy paths
// Targets:
//   configHandler          (handlers_dispatch.go:14) — GET / PUT / POST→405
//   patternsHandler        (handlers_pattern.go:47)  — DELETE→405
//   listPatternsHandler    (handlers_pattern.go:59)  — nil DB → 503
//   agentsListHandler      (handlers_agent.go:540)   — nil DB → 503
//   agentsHealthHandler    (handlers_agent.go:562)   — nil DB
//   nodeCapabilitiesHandler(handlers_agent.go:687)   — GET with nil DB
//   agentHeartbeatReceive  (handlers_agent.go:86)    — GET→405, nil DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// configHandler
// ---------------------------------------------------------------------------

func TestWave190_ConfigHandler_Get(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); !strings.Contains(ct, "json") {
		t.Errorf("expected JSON content-type, got %q", ct)
	}
}

func TestWave190_ConfigHandler_Put(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/config",
		strings.NewReader(`{"key":"value"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for PUT, got %d", w.Code)
	}
}

func TestWave190_ConfigHandler_Put_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/config",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

func TestWave190_ConfigHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// patternsHandler
// ---------------------------------------------------------------------------

func TestWave190_PatternsHandler_Delete405(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/patterns", nil)
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// listPatternsHandler
// ---------------------------------------------------------------------------

func TestWave190_ListPatternsHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	listPatternsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// agentsListHandler
// ---------------------------------------------------------------------------

func TestWave190_AgentsListHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsListHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// agentsHealthHandler
// ---------------------------------------------------------------------------

func TestWave190_AgentsHealthHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// nodeCapabilitiesHandler
// ---------------------------------------------------------------------------

func TestWave190_NodeCapabilitiesHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/node/capabilities", nil)
	w := httptest.NewRecorder()
	nodeCapabilitiesHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("unexpected status: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// agentHeartbeatReceive
// ---------------------------------------------------------------------------

func TestWave190_AgentHeartbeatReceive_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi-1/heartbeat", nil)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave190_AgentHeartbeatReceive_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/agents/kimi-1/heartbeat",
		strings.NewReader(`{"status":"idle","context_pct":50}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

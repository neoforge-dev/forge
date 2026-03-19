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

func TestPWADashboardHandlers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	hub := NewHub()
	defer hub.Stop()

	d := NewDashboard(db, hub)
	h := NewPWADashboardHandler(d)

	// Test Summary
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("summary handler returned status %d", w.Code)
	}

	var summaryResp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &summaryResp)
	if !summaryResp["success"].(bool) {
		t.Error("summary response success is false")
	}

	// Test Agents
	req = httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w = httptest.NewRecorder()
	h.handleAgents(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("agents handler returned status %d", w.Code)
	}
}

// TestPWADashboardHandlers_HandleAgents_WithAgents verifies that handleAgents
// correctly populates the pwaAgents slice when agents are in the DB.
// This covers the for-range loop body in handleAgents.
func TestPWADashboardHandlers_HandleAgents_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a heartbeat so the loop body is exercised.
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
		VALUES ('pwa-test-agent', 'test-node', 'active', 0.5, 'task,review', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	h := NewPWADashboardHandler(NewDashboard(db, nil))
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPWADashboardHandlers_HandleAgents_NilDashboard verifies that handleAgents
// returns 503 when the dashboard field is nil.
func TestPWADashboardHandlers_HandleAgents_NilDashboard(t *testing.T) {
	h := &PWADashboardHandler{dashboard: nil}
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil dashboard, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPWADashboardHandlers_HandleSummary_WithAgents verifies that handleSummary
// populates the agents list in the response when agents are present.
// This covers the for-range body at handlers_pwa_bridge.go:88.
func TestPWADashboardHandlers_HandleSummary_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
		VALUES ('summary-agent', 'node1', 'active', 0.3, 'task', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	h := NewPWADashboardHandler(NewDashboard(db, nil))
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPWADashboardHandlers_HandleSummary_NilDashboard verifies that handleSummary
// returns 503 when the dashboard field is nil.
func TestPWADashboardHandlers_HandleSummary_NilDashboard(t *testing.T) {
	h := &PWADashboardHandler{dashboard: nil}
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil dashboard, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPWADashboardHandlers_Error(t *testing.T) {
	// Test with valid DB — verify handler returns 200
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	d := NewDashboard(db, nil)
	h := NewPWADashboardHandler(d)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPWAFleetSummaryHandler_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	fleetSummaryHandler(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestPWAMessagesHandlers_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodPut, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPWAMessagesHandlers_InvalidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodPost, "/api/messages", strings.NewReader("invalid json"))
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestPWAMessagesHandlers_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodPost, "/api/messages", strings.NewReader("{}"))
	w := httptest.NewRecorder()
	messagesHandler(w, req)
}

func TestMessageByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/messages/nonexistent", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestPWARegisterRoutes(t *testing.T) {
	mux := http.NewServeMux()
	
	// Dashboard
	d := NewDashboard(nil, nil)
	h := NewPWADashboardHandler(d)
	h.RegisterRoutes(mux)

	// Handoffs
	store := NewHandoffStore(nil)
	service := NewHandoffService(store)
	hh := NewHandoffHandler(service)
	hh.RegisterRoutes(mux)
}

func TestPWAFleetSummaryHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	fleetSummaryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("fleet summary handler returned status %d", w.Code)
	}
}

func TestPWALeadStateHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/lead-state", nil)
	w := httptest.NewRecorder()
	leadStateHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("lead state handler returned status %d", w.Code)
	}
}

func TestPWAMessagesHandlers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	// Test Create
	payload := map[string]string{
		"from_agent": "agent-1",
		"to_agent":   "agent-2",
		"content":    "hello world",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/messages", bytes.NewReader(body))
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("create message returned status %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	data := resp["data"].(map[string]interface{})
	msgID := data["id"].(string)

	// Test List
	req = httptest.NewRequest(http.MethodGet, "/api/messages", nil)
	w = httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("list messages returned status %d", w.Code)
	}

	// Test Get
	req = httptest.NewRequest(http.MethodGet, "/api/messages/"+msgID, nil)
	w = httptest.NewRecorder()
	messageByIDHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("get message returned status %d", w.Code)
	}

	// Test Read
	req = httptest.NewRequest(http.MethodPost, "/api/messages/"+msgID+"/read", nil)
	w = httptest.NewRecorder()
	messageByIDHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("mark read returned status %d", w.Code)
	}
}

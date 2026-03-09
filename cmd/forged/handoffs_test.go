//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHandoffHandlers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	store := NewHandoffStore(db)
	service := NewHandoffService(store)
	h := NewHandoffHandler(service)

	// Test Create
	payload := map[string]interface{}{
		"from_agent":       "agent-1",
		"to_agent":         "agent-2",
		"task_description": "test handoff",
		"files":            []string{"file1.txt"},
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleHandoffs(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("create handoff returned status %d", w.Code)
	}

	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	data := resp["data"].(map[string]interface{})
	handoffID := data["id"].(string)

	// Test List
	req = httptest.NewRequest(http.MethodGet, "/api/handoffs", nil)
	w = httptest.NewRecorder()
	h.handleHandoffs(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("list handoffs returned status %d", w.Code)
	}

	// Test Accept
	req = httptest.NewRequest(http.MethodPost, "/api/handoffs/"+handoffID+"/accept", nil)
	w = httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("accept handoff returned status %d", w.Code)
	}

	// Test Complete
	req = httptest.NewRequest(http.MethodPost, "/api/handoffs/"+handoffID+"/complete", nil)
	w = httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("complete handoff returned status %d", w.Code)
	}
}

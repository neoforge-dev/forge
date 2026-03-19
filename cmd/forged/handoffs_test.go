//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
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

// ── Error-path coverage ───────────────────────────────────────────────────────

func TestHandoffService_Create_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	db.Close()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	_, err := svc.Create(context.Background(), "a", "b", "desc", nil, "high")
	if err == nil {
		t.Error("expected error from Create with closed DB")
	}
}

func TestHandoffService_Reject_WrongStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Create and accept a handoff, then try to reject it (should fail - not pending)
	h, err := svc.Create(ctx, "from-agent", "to-agent", "task", nil, "medium")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if _, err := svc.Accept(ctx, h.ID, "to-agent"); err != nil {
		t.Fatalf("Accept: %v", err)
	}
	_, err = svc.Reject(ctx, h.ID, "too late")
	if err == nil {
		t.Error("expected error rejecting accepted handoff")
	}
}

func TestHandoffStore_ClosedDB_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	db.Close()
	store := NewHandoffStore(db)
	_, err := store.Get(context.Background(), "any-id")
	if err == nil {
		t.Error("expected error from Get with closed DB")
	}
}

func TestHandoffStore_ClosedDB_List(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	db.Close()
	store := NewHandoffStore(db)
	_, err := store.List(context.Background(), "", "", "", 10)
	if err == nil {
		t.Error("expected error from List with closed DB")
	}
}

// TestHandoffService_Complete_WrongStatus covers the status != Accepted check.
func TestHandoffService_Complete_WrongStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Create a handoff (status=Pending) and try to complete it directly.
	h, err := svc.Create(ctx, "from-x", "to-x", "desc", nil, "medium")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	_, err = svc.Complete(ctx, h.ID, "notes")
	if err == nil {
		t.Error("expected error completing a pending handoff (not accepted)")
	}
}

// TestHandoffService_Accept_EmptyAgent covers the `acceptingAgent == ""` branch.
func TestHandoffService_Accept_EmptyAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	h, err := svc.Create(ctx, "from-y", "to-y", "desc", nil, "low")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	// Pass empty acceptingAgent → uses h.ToAgent as the accepting agent.
	accepted, err := svc.Accept(ctx, h.ID, "")
	if err != nil {
		t.Fatalf("Accept with empty agent: %v", err)
	}
	if accepted.AcceptingAgent == nil || *accepted.AcceptingAgent != "to-y" {
		t.Errorf("expected AcceptingAgent='to-y', got %v", accepted.AcceptingAgent)
	}
}

// TestHandoffHandler_ListHandoffs_WithLimit covers the `l != ""` limit query param branch.
func TestHandoffHandler_ListHandoffs_WithLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	handler := NewHandoffHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/handoffs?limit=5", nil)
	w := httptest.NewRecorder()
	handler.listHandoffs(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandoffHandler_ListHandoffs_StoreError covers the List error path in listHandoffs.
func TestHandoffHandler_ListHandoffs_StoreError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	db.Close()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	handler := NewHandoffHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/handoffs", nil)
	w := httptest.NewRecorder()
	handler.listHandoffs(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}
}

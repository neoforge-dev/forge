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
	"time"
)

// ---------------------------------------------------------------------------
// handoffs.go — full lifecycle now that time scan bug is fixed
// ---------------------------------------------------------------------------

func TestHandoffStore_GetExisting(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Second)
	h := Handoff{
		ID:              "hoff-get-w18",
		FromAgent:       "a",
		ToAgent:         "b",
		TaskDescription: "task",
		Files:           []string{"x.go"},
		Priority:        "high",
		Status:          HandoffPending,
		CreatedAt:       now,
		UpdatedAt:       now,
	}

	if err := store.Create(ctx, h); err != nil {
		t.Fatalf("Create: %v", err)
	}

	got, err := store.Get(ctx, h.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != h.ID {
		t.Errorf("ID = %s, want %s", got.ID, h.ID)
	}
	if got.FromAgent != "a" {
		t.Errorf("FromAgent = %s, want a", got.FromAgent)
	}
	if len(got.Files) != 1 {
		t.Errorf("Files = %v, want 1 item", got.Files)
	}
	if got.Status != HandoffPending {
		t.Errorf("Status = %s, want pending", got.Status)
	}
}

func TestHandoffStore_Update(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Second)
	h := Handoff{
		ID:              "hoff-upd-w18",
		FromAgent:       "a",
		ToAgent:         "b",
		TaskDescription: "task",
		Files:           nil,
		Priority:        "medium",
		Status:          HandoffPending,
		CreatedAt:       now,
		UpdatedAt:       now,
	}
	if err := store.Create(ctx, h); err != nil {
		t.Fatalf("Create: %v", err)
	}

	h.Status = HandoffAccepted
	h.UpdatedAt = time.Now().UTC().Truncate(time.Second)
	if err := store.Update(ctx, h); err != nil {
		t.Fatalf("Update: %v", err)
	}

	got, err := store.Get(ctx, h.ID)
	if err != nil {
		t.Fatalf("Get after update: %v", err)
	}
	if got.Status != HandoffAccepted {
		t.Errorf("Status after update = %s, want accepted", got.Status)
	}
}

func TestHandoffStore_List_WithResults(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Second)

	for _, id := range []string{"lr1", "lr2"} {
		h := Handoff{
			ID: id, FromAgent: "fa", ToAgent: "ta",
			TaskDescription: "t", Priority: "low",
			Status: HandoffPending, CreatedAt: now, UpdatedAt: now,
		}
		store.Create(ctx, h)
	}

	list, err := store.List(ctx, "pending", "fa", "", 10)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(list) != 2 {
		t.Errorf("List len = %d, want 2", len(list))
	}
}

func TestHandoffService_Accept(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	ctx := context.Background()
	h, err := svc.Create(ctx, "a", "b", "work", nil, "")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}

	accepted, err := svc.Accept(ctx, h.ID, "b")
	if err != nil {
		t.Fatalf("Accept: %v", err)
	}
	if accepted.Status != HandoffAccepted {
		t.Errorf("Status = %s, want accepted", accepted.Status)
	}
	if accepted.AcceptingAgent == nil || *accepted.AcceptingAgent != "b" {
		t.Errorf("AcceptingAgent = %v, want b", accepted.AcceptingAgent)
	}

	// Second accept should fail
	_, err = svc.Accept(ctx, h.ID, "c")
	if err == nil {
		t.Error("expected error accepting non-pending handoff")
	}
}

func TestHandoffService_Accept_DefaultsToToAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	ctx := context.Background()
	h, _ := svc.Create(ctx, "a", "default-target", "work", nil, "")

	accepted, err := svc.Accept(ctx, h.ID, "")
	if err != nil {
		t.Fatalf("Accept with empty agent: %v", err)
	}
	if accepted.AcceptingAgent == nil || *accepted.AcceptingAgent != "default-target" {
		t.Errorf("AcceptingAgent = %v, want default-target", accepted.AcceptingAgent)
	}
}

func TestHandoffService_Reject(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	ctx := context.Background()
	h, _ := svc.Create(ctx, "a", "b", "work", nil, "")

	rejected, err := svc.Reject(ctx, h.ID, "not ready")
	if err != nil {
		t.Fatalf("Reject: %v", err)
	}
	if rejected.Status != HandoffRejected {
		t.Errorf("Status = %s, want rejected", rejected.Status)
	}
	if rejected.Reason == nil || *rejected.Reason != "not ready" {
		t.Errorf("Reason = %v, want 'not ready'", rejected.Reason)
	}

	// Second reject should fail
	_, err = svc.Reject(ctx, h.ID, "again")
	if err == nil {
		t.Error("expected error rejecting non-pending handoff")
	}
}

func TestHandoffService_Complete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	ctx := context.Background()
	h, _ := svc.Create(ctx, "a", "b", "work", nil, "")
	svc.Accept(ctx, h.ID, "b")

	completed, err := svc.Complete(ctx, h.ID, "all done")
	if err != nil {
		t.Fatalf("Complete: %v", err)
	}
	if completed.Status != HandoffCompleted {
		t.Errorf("Status = %s, want completed", completed.Status)
	}
	if completed.Notes == nil || *completed.Notes != "all done" {
		t.Errorf("Notes = %v, want 'all done'", completed.Notes)
	}

	// Complete on non-accepted should fail
	h2, _ := svc.Create(ctx, "a", "b", "work2", nil, "")
	_, err = svc.Complete(ctx, h2.ID, "early")
	if err == nil {
		t.Error("expected error completing non-accepted handoff")
	}
}

func TestHandoffHandler_Action_Accept(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	handler := NewHandoffHandler(svc)
	ctx := context.Background()
	h, _ := svc.Create(ctx, "a", "b", "work", nil, "")

	body, _ := json.Marshal(map[string]string{"accepting_agent": "b"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+h.ID+"/accept", bytes.NewReader(body))
	req.URL.Path = "/api/handoffs/" + h.ID + "/accept"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_Action_Reject(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	handler := NewHandoffHandler(svc)
	ctx := context.Background()
	h, _ := svc.Create(ctx, "a", "b", "work", nil, "")

	body, _ := json.Marshal(map[string]string{"reason": "not ready"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+h.ID+"/reject", bytes.NewReader(body))
	req.URL.Path = "/api/handoffs/" + h.ID + "/reject"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_Action_Complete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	handler := NewHandoffHandler(svc)
	ctx := context.Background()
	h, _ := svc.Create(ctx, "a", "b", "work", nil, "")
	svc.Accept(ctx, h.ID, "b")

	body, _ := json.Marshal(map[string]string{"notes": "done"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+h.ID+"/complete", bytes.NewReader(body))
	req.URL.Path = "/api/handoffs/" + h.ID + "/complete"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}


func TestHandleAgentStatus_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandleAgentStatus_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/status?id=nonexistent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// main.go — withTimeout wrapper
// ---------------------------------------------------------------------------

func TestWithTimeout_PassesRequest(t *testing.T) {
	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusOK)
	})

	handler := withTimeout(inner, 5*time.Second)
	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if !called {
		t.Error("expected inner handler to be called")
	}
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// main.go — CLIRouter route registration
// ---------------------------------------------------------------------------

func TestCLIRouter_Registers(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	mux := http.NewServeMux()
	// Just ensure CLIRouter registers routes without panicking
	CLIRouter(mux)

	// Spot-check: a registered CLI route should return something (not 404)
	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)
	// handleTaskList should return 200 or 4xx — not 404
	if w.Code == http.StatusNotFound {
		t.Errorf("CLIRouter did not register /cli/task/list (got 404)")
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — RegisterRoutes
// ---------------------------------------------------------------------------

func TestHandoffHandler_RegisterRoutesW18(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	mux := http.NewServeMux()
	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	handler.RegisterRoutes(mux)

	// Verify /api/handoffs is registered — GET should return 200
	req := httptest.NewRequest(http.MethodGet, "/api/handoffs", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 from registered /api/handoffs, got %d", w.Code)
	}
}

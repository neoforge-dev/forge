//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func bytesReader(b []byte) *bytes.Reader { return bytes.NewReader(b) }

// setupApprovalsTestDB creates a fully migrated DB and ApprovalStore for tests.
func setupApprovalsTestDB(t *testing.T) (*sqliteApprovalStore, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	store := &sqliteApprovalStore{db: db}
	return store, cleanup
}

func makeApproval(id string) Approval {
	return Approval{
		ID:              id,
		Type:            ApprovalTaskCompletion,
		AgentID:         "agent-test",
		Domain:          "test-domain",
		Title:           "Test Approval " + id,
		Description:     "test description",
		RiskScore:       0.5,
		ConfidenceScore: 0.8,
		Tier:            TierWatch,
		Status:          StatusPending,
		CreatedAt:       time.Now().UTC(),
		ExpiresAt:       time.Now().UTC().Add(24 * time.Hour),
	}
}

func TestApprovalStore_CreateAndGet(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("approval-001")

	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	got, err := store.Get(ctx, a.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != a.ID {
		t.Errorf("ID = %s, want %s", got.ID, a.ID)
	}
	if got.Status != StatusPending {
		t.Errorf("Status = %s, want pending", got.Status)
	}
}

func TestApprovalStore_Get_NotFound(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	_, err := store.Get(context.Background(), "nonexistent")
	if err == nil {
		t.Error("expected error for nonexistent approval")
	}
}

func TestApprovalStore_ListByStatus(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	for i, id := range []string{"ls-001", "ls-002", "ls-003"} {
		a := makeApproval(id)
		if i == 2 {
			a.Status = StatusApproved
		}
		if err := store.Create(ctx, a); err != nil {
			t.Fatalf("Create %s: %v", id, err)
		}
	}

	pending, err := store.ListByStatus(ctx, StatusPending, 10)
	if err != nil {
		t.Fatalf("ListByStatus: %v", err)
	}
	if len(pending) != 2 {
		t.Errorf("expected 2 pending, got %d", len(pending))
	}
}

func TestApprovalStore_ListPending(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	if err := store.Create(ctx, makeApproval("lp-001")); err != nil {
		t.Fatalf("Create: %v", err)
	}

	pending, err := store.ListPending(ctx, 10)
	if err != nil {
		t.Fatalf("ListPending: %v", err)
	}
	if len(pending) == 0 {
		t.Error("expected at least 1 pending approval")
	}
}

func TestApprovalStore_Approve(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("approve-001")
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	if err := store.Approve(ctx, a.ID, "human-1"); err != nil {
		t.Fatalf("Approve: %v", err)
	}

	got, err := store.Get(ctx, a.ID)
	if err != nil {
		t.Fatalf("Get after Approve: %v", err)
	}
	if got.Status != StatusApproved {
		t.Errorf("Status = %s, want approved", got.Status)
	}
}

func TestApprovalStore_Reject(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("reject-001")
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	if err := store.Reject(ctx, a.ID, "human-1"); err != nil {
		t.Fatalf("Reject: %v", err)
	}

	got, err := store.Get(ctx, a.ID)
	if err != nil {
		t.Fatalf("Get after Reject: %v", err)
	}
	if got.Status != StatusRejected {
		t.Errorf("Status = %s, want rejected", got.Status)
	}
}

func TestApprovalStore_Update(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("update-001")
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	a.Title = "Updated Title"
	a.RiskScore = 0.9
	if err := store.Update(ctx, a); err != nil {
		t.Fatalf("Update: %v", err)
	}

	got, err := store.Get(ctx, a.ID)
	if err != nil {
		t.Fatalf("Get after Update: %v", err)
	}
	if got.Title != "Updated Title" {
		t.Errorf("Title = %s, want 'Updated Title'", got.Title)
	}
}

func TestApprovalStore_ListByAgent(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("lba-001")
	a.AgentID = "specific-agent"
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	results, err := store.ListByAgent(ctx, "specific-agent", 10)
	if err != nil {
		t.Fatalf("ListByAgent: %v", err)
	}
	if len(results) == 0 {
		t.Error("expected at least 1 approval for specific-agent")
	}
}

func TestApprovalStore_ListByTier(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("lbt-001")
	a.Tier = TierDesktop
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	results, err := store.ListByTier(ctx, TierDesktop, 10)
	if err != nil {
		t.Fatalf("ListByTier: %v", err)
	}
	if len(results) == 0 {
		t.Error("expected at least 1 desktop tier approval")
	}
}

func TestApprovalStore_ExpireOldApprovals(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("expire-001")
	a.ExpiresAt = time.Now().UTC().Add(-2 * time.Hour) // already expired
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	count, err := store.ExpireOldApprovals(ctx, time.Now().UTC())
	if err != nil {
		t.Fatalf("ExpireOldApprovals: %v", err)
	}
	if count == 0 {
		t.Error("expected at least 1 expired approval")
	}
}

func TestApprovalStore_ListByTask(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	taskID := "task-for-approval"
	a := makeApproval("lbtask-001")
	a.TaskID = &taskID
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	results, err := store.ListByTask(ctx, taskID, 10)
	if err != nil {
		t.Fatalf("ListByTask: %v", err)
	}
	if len(results) == 0 {
		t.Error("expected at least 1 approval for task")
	}
}

// --- NewApprovalStore + ApprovalService.ExpireOld + ApprovalHandler ---

func TestNewApprovalStore(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	if store == nil {
		t.Error("expected non-nil store")
	}
}

func TestApprovalService_ExpireOld(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	svc := NewApprovalService(store)

	// Create an already-expired approval
	a := makeApproval("svc-expire-001")
	a.ExpiresAt = time.Now().UTC().Add(-2 * time.Hour)
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	count, err := svc.ExpireOld(ctx)
	if err != nil {
		t.Fatalf("ExpireOld: %v", err)
	}
	if count == 0 {
		t.Error("expected at least 1 expired")
	}
}

func TestApprovalHandler_RegisterRoutes(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	mux := http.NewServeMux()
	h.RegisterRoutes(mux) // should not panic
}

func TestApprovalHandler_Action_InvalidPath(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/bad-path", nil)
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad path, got %d: %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandler_Action_InvalidMethod(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/some-id/approve", nil)
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandler_Action_UnknownAction(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	body := []byte(`{"user":"tester"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/some-id/foobar", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for unknown action, got %d: %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandler_Action_Approve(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("action-approve-001")
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	body := []byte(`{"user":"reviewer"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/action-approve-001/approve", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandler_Action_Reject(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("action-reject-001")
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	body := []byte(`{"user":"reviewer"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/action-reject-001/reject", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- authTokensHandler ---

func TestAuthTokensHandler_List(t *testing.T) {
	am := NewAuthManager("local")
	handler := authTokensHandler(am)

	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAuthTokensHandler_GenerateToken(t *testing.T) {
	am := NewAuthManager("api")
	handler := authTokensHandler(am)

	body := []byte(`{"description":"test","scopes":["tasks:read"]}`)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytesReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAuthTokensHandler_InvalidMethod(t *testing.T) {
	am := NewAuthManager("local")
	handler := authTokensHandler(am)

	req := httptest.NewRequest(http.MethodDelete, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

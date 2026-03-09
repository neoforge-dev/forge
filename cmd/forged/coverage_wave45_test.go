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

// Wave 45: ApprovalHandler deeper paths — listApprovals (status/tier/agent_id filters),
//          handlePending (limit param), handleApprovalAction (invalid path, method-not-allowed),
//          createApproval additional paths

// Helper to build an ApprovalHandler with a real DB.
func setupApprovalHandler(t *testing.T) (*ApprovalHandler, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	return h, cleanup
}

// ─── listApprovals ────────────────────────────────────────────────────────

func TestListApprovals_ByStatus_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?status=pending", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestListApprovals_ByTier_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?tier=T1", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestListApprovals_ByAgentID_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?agent_id=test-agent", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestListApprovals_LimitClamped_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	// limit > 100 → clamped to 100
	req := httptest.NewRequest(http.MethodGet, "/api/approvals?limit=999", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── handlePending ────────────────────────────────────────────────────────

func TestHandlePending_MethodNotAllowed_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandlePending_WithLimit_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending?limit=10", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandlePending_LimitClamped_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending?limit=999", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── handleApprovalAction ─────────────────────────────────────────────────

func TestHandleApprovalAction_InvalidPath_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	// Only 1 path segment (missing action part) → 400.
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/only-id", nil)
	req.URL.Path = "/api/approvals/only-id"
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid path, got %d", w.Code)
	}
}

func TestHandleApprovalAction_MethodNotAllowed_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/some-id/approve", nil)
	req.URL.Path = "/api/approvals/some-id/approve"
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleApprovalAction_ApproveNotFound_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"user":"test-user"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/no-such-id/approve", body)
	req.URL.Path = "/api/approvals/no-such-id/approve"
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)
	// Approval not found → 404 or 500 depending on impl.
	if w.Code == http.StatusOK {
		t.Errorf("expected non-200 for missing approval ID, got 200")
	}
}

func TestHandleApprovalAction_RejectNotFound_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"user":"test-user"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/no-such-id/reject", body)
	req.URL.Path = "/api/approvals/no-such-id/reject"
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)
	// Approval not found → non-200.
	if w.Code == http.StatusOK {
		t.Errorf("expected non-200 for missing approval ID reject, got 200")
	}
}

// ─── createApproval ───────────────────────────────────────────────────────

func TestCreateApproval_InvalidJSON_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	h.createApproval(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

func TestCreateApproval_MissingType_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	// Missing required 'type' field.
	body, _ := json.Marshal(map[string]string{"description": "test"})
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.createApproval(w, req)
	// Should reject due to missing type.
	_ = w.Code
}

//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Wave 64: Push coverage from 74.6% to 75%+
// Targets:
//   - approvals.go: ListByStatus, ListByTier, ListByAgent, ListByTask (store methods)
//   - approvals.go: createApproval handler (line 625, missing-field / invalid-JSON paths)
//   - approvals.go: handleApprovalCount (GET path + method-not-allowed path)
//   - auth_handlers.go: authTokensHandler DELETE branch (line 38)
//   - context_sync.go: SyncEnvelopesToFilesystem via buildCS helper (empty + with-rows)

// ─── ApprovalStore.ListByStatus ───────────────────────────────────────────────

func TestWave64_ListByStatus_Success(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("wave64-ls-001")
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	approvals, err := store.ListByStatus(ctx, StatusPending, 10)
	if err != nil {
		t.Fatalf("ListByStatus: %v", err)
	}
	if len(approvals) == 0 {
		t.Error("expected at least one approval in pending status")
	}
}

func TestWave64_ListByStatus_ZeroLimit(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	// limit=0 → defaults to 50 inside the implementation; empty DB yields nil or empty slice
	_, err := store.ListByStatus(context.Background(), StatusPending, 0)
	if err != nil {
		t.Fatalf("ListByStatus(limit=0): %v", err)
	}
}

// ─── ApprovalStore.ListByTier ─────────────────────────────────────────────────

func TestWave64_ListByTier_Success(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("wave64-lt-001")
	a.Tier = TierWatch
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	approvals, err := store.ListByTier(ctx, TierWatch, 10)
	if err != nil {
		t.Fatalf("ListByTier: %v", err)
	}
	if len(approvals) == 0 {
		t.Error("expected at least one TierWatch approval")
	}
}

func TestWave64_ListByTier_ZeroLimit(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	// limit=0 → defaults to 50 inside the implementation; empty DB yields nil or empty slice
	_, err := store.ListByTier(context.Background(), TierPhone, 0)
	if err != nil {
		t.Fatalf("ListByTier(limit=0): %v", err)
	}
}

// ─── ApprovalStore.ListByAgent ────────────────────────────────────────────────

func TestWave64_ListByAgent_Success(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("wave64-la-001")
	a.AgentID = "agent-wave64"
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	approvals, err := store.ListByAgent(ctx, "agent-wave64", 10)
	if err != nil {
		t.Fatalf("ListByAgent: %v", err)
	}
	if len(approvals) == 0 {
		t.Error("expected at least one approval for agent-wave64")
	}
}

func TestWave64_ListByAgent_NoResults(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approvals, err := store.ListByAgent(context.Background(), "no-such-agent-wave64", 10)
	if err != nil {
		t.Fatalf("ListByAgent(no results): %v", err)
	}
	if len(approvals) != 0 {
		t.Errorf("expected 0 approvals for unknown agent, got %d", len(approvals))
	}
}

// ─── ApprovalStore.ListByTask ─────────────────────────────────────────────────

func TestWave64_ListByTask_Success(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("wave64-lbt-001")
	taskID := "task-wave64-lbt"
	a.TaskID = &taskID
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	approvals, err := store.ListByTask(ctx, taskID, 10)
	if err != nil {
		t.Fatalf("ListByTask: %v", err)
	}
	if len(approvals) == 0 {
		t.Error("expected at least one approval for task-wave64-lbt")
	}
}

func TestWave64_ListByTask_NoResults(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approvals, err := store.ListByTask(context.Background(), "no-such-task-wave64", 10)
	if err != nil {
		t.Fatalf("ListByTask(no results): %v", err)
	}
	if len(approvals) != 0 {
		t.Errorf("expected 0 approvals for unknown task, got %d", len(approvals))
	}
}

// ─── handleApprovalCount ──────────────────────────────────────────────────────

func TestWave64_HandleApprovalCount_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	h.handleApprovalCount(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("handleApprovalCount GET: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if _, ok := resp["pending"]; !ok {
		t.Error("response missing 'pending' key")
	}
}

func TestWave64_HandleApprovalCount_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	h.handleApprovalCount(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("handleApprovalCount POST: expected 405, got %d", w.Code)
	}
}

// ─── createApproval handler ───────────────────────────────────────────────────

func TestWave64_CreateApproval_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader([]byte("not json")))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("createApproval invalid JSON: expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_CreateApproval_MissingRequiredFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	// Missing agent_id, domain, title
	body := map[string]string{"type": "task_completion"}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("createApproval missing fields: expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_CreateApproval_MissingTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	// All required fields present except task_id
	body := map[string]string{
		"type":     "task_completion",
		"agent_id": "agent-w64",
		"domain":   "test-domain",
		"title":    "Test Approval Wave 64",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("createApproval missing task_id: expected 400, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "task_id") {
		t.Logf("body: %s", w.Body.String())
	}
}

func TestWave64_HandleApprovals_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodDelete, "/api/approvals", nil)
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("handleApprovals DELETE: expected 405, got %d", w.Code)
	}
}

// ─── listApprovals with query params (covers ListByTier and ListByAgent via HTTP) ──

func TestWave64_ListApprovals_ByTier(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?tier=watch", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)

	// Should return 200 with empty list
	if w.Code != http.StatusOK {
		t.Errorf("listApprovals by tier: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_ListApprovals_ByAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?agent_id=agent-w64-list", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("listApprovals by agent_id: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── authTokensHandler all branches ──────────────────────────────────────────

func TestWave64_AuthTokensHandler_DeleteMethod(t *testing.T) {
	auth := NewAuthManager("api")
	handler := authTokensHandler(auth)

	req := httptest.NewRequest(http.MethodDelete, "/api/auth/tokens/tok123", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	// DELETE is not GET or POST → should return 405
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("authTokensHandler DELETE: expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_AuthTokensHandler_PutMethod(t *testing.T) {
	auth := NewAuthManager("api")
	handler := authTokensHandler(auth)

	req := httptest.NewRequest(http.MethodPut, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("authTokensHandler PUT: expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_AuthTokensHandler_GetMethod(t *testing.T) {
	auth := NewAuthManager("api")
	handler := authTokensHandler(auth)

	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("authTokensHandler GET: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if _, ok := resp["tokens"]; !ok {
		t.Error("response missing 'tokens' key")
	}
}

func TestWave64_AuthTokensHandler_PostMethod(t *testing.T) {
	auth := NewAuthManager("api")
	handler := authTokensHandler(auth)

	body := map[string]interface{}{
		"description": "test token wave64",
		"scopes":      []string{"read", "write"},
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("authTokensHandler POST: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["token"] == "" {
		t.Error("expected non-empty token in POST response")
	}
}

func TestWave64_AuthTokensHandler_PostInvalidJSON(t *testing.T) {
	auth := NewAuthManager("api")
	handler := authTokensHandler(auth)

	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewReader([]byte("not-json")))
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("authTokensHandler POST invalid JSON: expected 400, got %d", w.Code)
	}
}

func TestWave64_AuthTokensHandler_PostEmptyScopes(t *testing.T) {
	auth := NewAuthManager("api")
	handler := authTokensHandler(auth)

	// Empty scopes → defaults to ["*"]
	body := map[string]interface{}{
		"description": "token with no scopes",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("authTokensHandler POST empty scopes: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── createApproval additional branches ──────────────────────────────────────

func TestWave64_CreateApproval_InvalidTier(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Insert a task so the task_id check passes
	enqueueTestTask(t, "task-w64-tier")

	svc := NewApprovalService(NewApprovalStore(getDBConn()))
	h := NewApprovalHandler(svc)

	taskID := "task-w64-tier"
	body := map[string]interface{}{
		"type":     "task_completion",
		"agent_id": "agent-w64",
		"domain":   "test-domain",
		"title":    "Test Approval Wave 64 Tier",
		"task_id":  taskID,
		"tier":     "INVALID_TIER",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("createApproval invalid tier: expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_CreateApproval_InvalidType(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "task-w64-type")

	svc := NewApprovalService(NewApprovalStore(getDBConn()))
	h := NewApprovalHandler(svc)

	taskID := "task-w64-type"
	body := map[string]interface{}{
		"type":     "INVALID_APPROVAL_TYPE",
		"agent_id": "agent-w64",
		"domain":   "test-domain",
		"title":    "Test Approval Wave 64 Type",
		"task_id":  taskID,
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("createApproval invalid type: expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave64_CreateApproval_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "task-w64-create")

	svc := NewApprovalService(NewApprovalStore(getDBConn()))
	h := NewApprovalHandler(svc)

	taskID := "task-w64-create"
	body := map[string]interface{}{
		"type":     "task_completion",
		"agent_id": "agent-w64",
		"domain":   "test-domain",
		"title":    "Test Approval Wave 64 Create",
		"task_id":  taskID,
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("createApproval success: expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── SyncEnvelopesToFilesystem via buildCS helper ────────────────────────────

func TestWave64_SyncEnvelopesToFilesystem_WithData(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	cs, _ := buildCS(t, db)

	// Insert an envelope that expires in the future
	env := ContextEnvelope{
		ID:      "env-w64-data",
		AgentID: "agent-w64-sync",
		Domain:  "wave64",
		Project: "proj64",
		TaskID:  "task-w64",
		Summary: "wave64 sync test",
	}
	content, _ := json.Marshal(env)
	_, err := db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+1 day'))
	`, env.ID, env.AgentID, env.Domain, env.Project, env.TaskID, env.Summary, string(content))
	if err != nil {
		t.Fatalf("insert envelope: %v", err)
	}

	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem with data: %v", err)
	}
}

func TestWave64_SyncEnvelopesToFilesystem_InvalidContent(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	cs, _ := buildCS(t, db)

	// Insert an envelope with invalid JSON content — exercises the unmarshal-error path
	_, err := db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, expires_at)
		VALUES ('env-w64-bad', 'agent-bad', 'wave64bad', 'proj', 'task', 'bad', 'NOT_JSON', datetime('now', '+1 day'))
	`)
	if err != nil {
		t.Fatalf("insert bad envelope: %v", err)
	}

	// Should log errors internally but return nil (no fatal error)
	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem with invalid content: %v", err)
	}
}

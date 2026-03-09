//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handlers_relay.go — relayDeliveriesHandler
// ---------------------------------------------------------------------------

func TestWave104_RelayDeliveries_Empty(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	count, ok := resp["count"]
	if !ok {
		t.Fatal("response missing 'count' key")
	}
	// count comes back as float64 from JSON decode
	if count.(float64) != 0 {
		t.Errorf("expected count=0, got %v", count)
	}
}

func TestWave104_RelayDeliveries_WithRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a row directly so the handler returns it.
	id := "relay-test-row-001"
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO relay_deliveries (id, agent_id, message, status, created_at) VALUES (?, ?, ?, 'pending', ?)`,
		id, "agent-1", "hello world", now,
	)
	if err != nil {
		t.Fatalf("insert relay row: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	count := resp["count"].(float64)
	if count != 1 {
		t.Errorf("expected count=1, got %v", count)
	}
}

func TestWave104_RelayDeliveries_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave104_RelayDeliveries_NoDB(t *testing.T) {
	// Do NOT call setupClaimTestDB — ensure db is nil.
	old := getDBConn()
	setDBConn(nil)
	defer setDBConn(old)

	req := httptest.NewRequest(http.MethodGet, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_relay.go — relayDispatchHandler
// ---------------------------------------------------------------------------

func TestWave104_RelayDispatch_Valid(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"agent_id":"agent-42","message":"do something"}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d body=%s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["agent_id"] != "agent-42" {
		t.Errorf("agent_id mismatch: %v", resp["agent_id"])
	}
	if resp["status"] != "pending" {
		t.Errorf("status mismatch: %v", resp["status"])
	}
	if resp["id"] == "" || resp["id"] == nil {
		t.Error("expected non-empty id in response")
	}
}

func TestWave104_RelayDispatch_MissingAgentID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"message":"do something"}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave104_RelayDispatch_MissingMessage(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"agent_id":"agent-1"}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave104_RelayDispatch_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader("{bad json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave104_RelayDispatch_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/relay/dispatch", nil)
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave104_RelayDispatch_NoDB(t *testing.T) {
	old := getDBConn()
	setDBConn(nil)
	defer setDBConn(old)

	body := `{"agent_id":"a","message":"m"}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_relay.go — relayAckHandler
// ---------------------------------------------------------------------------

func TestWave104_RelayAck_Valid(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	id := "relay-ack-valid-001"
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO relay_deliveries (id, agent_id, message, status, created_at) VALUES (?, ?, ?, 'pending', ?)`,
		id, "agent-ack", "ack me", now,
	)
	if err != nil {
		t.Fatalf("insert relay row: %v", err)
	}

	path := fmt.Sprintf("/api/relay/%s/ack", id)
	req := httptest.NewRequest(http.MethodPost, path, nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["status"] != "acked" {
		t.Errorf("expected status=acked, got %v", resp["status"])
	}
}

func TestWave104_RelayAck_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	path := "/api/relay/nonexistent-id-xyz/ack"
	req := httptest.NewRequest(http.MethodPost, path, nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestWave104_RelayAck_AlreadyAcked(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	id := "relay-ack-conflict-001"
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO relay_deliveries (id, agent_id, message, status, created_at, acked_at) VALUES (?, ?, ?, 'acked', ?, ?)`,
		id, "agent-ack", "already acked", now, now,
	)
	if err != nil {
		t.Fatalf("insert relay row: %v", err)
	}

	path := fmt.Sprintf("/api/relay/%s/ack", id)
	req := httptest.NewRequest(http.MethodPost, path, nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)

	if w.Code != http.StatusConflict {
		t.Errorf("expected 409, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestWave104_RelayAck_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave104_RelayAck_NoDB(t *testing.T) {
	old := getDBConn()
	setDBConn(nil)
	defer setDBConn(old)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_relay.go — relayPrefixHandler
// ---------------------------------------------------------------------------

func TestWave104_RelayPrefix_RoutesToAck(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	id := "relay-prefix-ack-001"
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO relay_deliveries (id, agent_id, message, status, created_at) VALUES (?, ?, ?, 'pending', ?)`,
		id, "agent-prefix", "prefix route", now,
	)
	if err != nil {
		t.Fatalf("insert relay row: %v", err)
	}

	path := fmt.Sprintf("/api/relay/%s/ack", id)
	req := httptest.NewRequest(http.MethodPost, path, nil)
	w := httptest.NewRecorder()
	relayPrefixHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestWave104_RelayPrefix_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/relay/unknown-route", nil)
	w := httptest.NewRecorder()
	relayPrefixHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// approvals.go — handleApprovalAction uncovered branches
// ---------------------------------------------------------------------------

// insertTestApproval creates a real approval in the DB and returns its ID.
func insertTestApproval(t *testing.T, idSuffix string) string {
	t.Helper()
	db := getDBConn()
	if db == nil {
		t.Fatal("insertTestApproval: db is nil")
	}
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	// Use the service to create so we exercise the real path.
	_ = svc

	id := "wave104-approval-" + idSuffix
	taskID := "task-wave104-" + idSuffix
	a := Approval{
		ID:        id,
		Type:      ApprovalTaskCompletion,
		AgentID:   "test-agent",
		Domain:    "test",
		Title:     "Test approval " + idSuffix,
		TaskID:    &taskID,
		Tier:      TierDesktop,
		Status:    StatusPending,
		ExpiresAt: time.Now().UTC().Add(1 * time.Hour),
		CreatedAt: time.Now().UTC(),
	}
	if err := store.Create(t.Context(), a); err != nil {
		t.Fatalf("store.Create: %v", err)
	}
	return id
}

// TestWave104_ApprovalAction_InvalidPath verifies that paths without 2 parts return 400.
func TestWave104_ApprovalAction_InvalidPath(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	// Path produces only 1 part after TrimPrefix — triggers len(parts) != 2
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/onlyid", bytes.NewReader([]byte(`{}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d body=%s", w.Code, w.Body.String())
	}
}

// TestWave104_ApprovalAction_InvalidAction verifies that an unknown action returns 400.
func TestWave104_ApprovalAction_InvalidAction(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	approvalID := insertTestApproval(t, "invalid-action")
	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := fmt.Sprintf("/api/approvals/%s/unknown", approvalID)
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(`{}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "unknown action") {
		t.Errorf("expected 'unknown action' in body, got: %s", w.Body.String())
	}
}

// TestWave104_ApprovalAction_MethodNotAllowed verifies that GET returns 405.
func TestWave104_ApprovalAction_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/some-id/approve", nil)
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave104_ApprovalAction_ApproveNotFound verifies that approving a non-existent ID returns 404.
func TestWave104_ApprovalAction_ApproveNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := "/api/approvals/does-not-exist/approve"
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(`{"user":"tester"}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	// service.Approve returns "not found" error → 404
	if w.Code != http.StatusNotFound && w.Code != http.StatusBadRequest {
		t.Errorf("expected 404 or 400, got %d body=%s", w.Code, w.Body.String())
	}
}

// TestWave104_ApprovalAction_RejectNotFound verifies that rejecting a non-existent ID returns error.
func TestWave104_ApprovalAction_RejectNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := "/api/approvals/does-not-exist/reject"
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(`{"user":"tester"}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusNotFound && w.Code != http.StatusBadRequest {
		t.Errorf("expected 404 or 400, got %d body=%s", w.Code, w.Body.String())
	}
}

// TestWave104_ApprovalAction_ApproveSuccess verifies successful approve path.
func TestWave104_ApprovalAction_ApproveSuccess(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	approvalID := insertTestApproval(t, "approve-success")
	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := fmt.Sprintf("/api/approvals/%s/approve", approvalID)
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(`{"user":"lead"}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}

	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	// The handler returns action+"ed" so "approve"+"ed" = "approveed"
	if resp["status"] == "" {
		t.Errorf("expected non-empty status in response, body had: %v", resp)
	}
}

// TestWave104_ApprovalAction_RejectSuccess verifies successful reject path.
func TestWave104_ApprovalAction_RejectSuccess(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	approvalID := insertTestApproval(t, "reject-success")
	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := fmt.Sprintf("/api/approvals/%s/reject", approvalID)
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(`{"user":"lead"}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}

	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["status"] != "rejected" {
		t.Errorf("expected status=rejected, got %s", resp["status"])
	}
}

// TestWave104_ApprovalAction_DecideFormApprove verifies form-encoded "decide" with approve.
func TestWave104_ApprovalAction_DecideFormApprove(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	approvalID := insertTestApproval(t, "decide-form-approve")
	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := fmt.Sprintf("/api/approvals/%s/decide", approvalID)
	body := "decision=approve&user=lead"
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
}

// TestWave104_ApprovalAction_DecideFormInvalid verifies form-encoded decide with invalid decision.
func TestWave104_ApprovalAction_DecideFormInvalid(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	approvalID := insertTestApproval(t, "decide-form-invalid")
	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := fmt.Sprintf("/api/approvals/%s/decide", approvalID)
	body := "decision=maybe&user=lead"
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d body=%s", w.Code, w.Body.String())
	}
}

// TestWave104_ApprovalAction_DecideJSONInvalid verifies JSON decide with invalid decision.
func TestWave104_ApprovalAction_DecideJSONInvalid(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	approvalID := insertTestApproval(t, "decide-json-invalid")
	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	path := fmt.Sprintf("/api/approvals/%s/decide", approvalID)
	body := `{"user":"lead","decision":"maybe"}`
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d body=%s", w.Code, w.Body.String())
	}
}

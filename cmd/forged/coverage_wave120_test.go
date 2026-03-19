//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// --- gitguard_idempotency.go pure logic ---

func TestWave120_ComputeIdempotencyHash_Deterministic(t *testing.T) {
	key := IdempotencyKey{ActionType: "git_commit", Payload: []byte(`{"msg":"hello"}`)}
	h1 := computeIdempotencyHash(key)
	h2 := computeIdempotencyHash(key)
	if h1 != h2 {
		t.Errorf("hash is not deterministic: %s != %s", h1, h2)
	}
	if len(h1) != 64 {
		t.Errorf("expected 64-char SHA256 hex, got len=%d: %s", len(h1), h1)
	}
}

func TestWave120_ComputeIdempotencyHash_DifferentActionTypes(t *testing.T) {
	payload := []byte(`{"x":1}`)
	h1 := computeIdempotencyHash(IdempotencyKey{ActionType: "type_a", Payload: payload})
	h2 := computeIdempotencyHash(IdempotencyKey{ActionType: "type_b", Payload: payload})
	if h1 == h2 {
		t.Error("different action types should produce different hashes")
	}
}

func TestWave120_ComputeIdempotencyHash_WithResourceID(t *testing.T) {
	base := IdempotencyKey{ActionType: "push", Payload: []byte("data")}
	withRes := IdempotencyKey{ActionType: "push", Payload: []byte("data"), ResourceID: "repo-123"}
	h1 := computeIdempotencyHash(base)
	h2 := computeIdempotencyHash(withRes)
	if h1 == h2 {
		t.Error("key with ResourceID should differ from key without")
	}
}

func TestWave120_ComputeIdempotencyHash_EmptyPayload(t *testing.T) {
	key := IdempotencyKey{ActionType: "noop", Payload: []byte{}}
	h := computeIdempotencyHash(key)
	if len(h) != 64 {
		t.Errorf("empty payload hash should still be 64 chars, got %d", len(h))
	}
}

func TestWave120_HashPayload_ConsistentWithComputeIdempotencyHash(t *testing.T) {
	actionType := "git_push"
	payload := []byte(`{"ref":"main"}`)
	direct := hashPayload(actionType, payload)
	via := computeIdempotencyHash(IdempotencyKey{ActionType: actionType, Payload: payload})
	if direct != via {
		t.Errorf("hashPayload and computeIdempotencyHash diverged: %s vs %s", direct, via)
	}
}

func TestWave120_HashPayload_DifferentPayloads(t *testing.T) {
	h1 := hashPayload("commit", []byte("payload_a"))
	h2 := hashPayload("commit", []byte("payload_b"))
	if h1 == h2 {
		t.Error("different payloads should produce different hashes")
	}
}

// --- token_budget.go pure logic ---

func TestWave120_ComputeBudgetStatus_OK(t *testing.T) {
	status := computeBudgetStatus(10, 20, 5, nil)
	if status != "OK" {
		t.Errorf("expected OK, got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_Caution(t *testing.T) {
	status := computeBudgetStatus(80, 50, 30, nil)
	if status != "CAUTION" {
		t.Errorf("expected CAUTION at 80%%, got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_Red(t *testing.T) {
	status := computeBudgetStatus(90, 50, 30, nil)
	if status != "RED" {
		t.Errorf("expected RED at 90%%, got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_Depleted(t *testing.T) {
	status := computeBudgetStatus(95, 50, 30, nil)
	if status != "DEPLETED" {
		t.Errorf("expected DEPLETED at 95%%, got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_WorstWins(t *testing.T) {
	// h5 is the worst at 91 — should yield RED
	status := computeBudgetStatus(10, 20, 91, nil)
	if status != "RED" {
		t.Errorf("expected RED (worst=91), got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_WeeklyWorst(t *testing.T) {
	status := computeBudgetStatus(10, 96, 50, nil)
	if status != "DEPLETED" {
		t.Errorf("expected DEPLETED (weekly=96), got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_Cooldown_Active(t *testing.T) {
	future := time.Now().Add(2 * time.Hour).UTC().Format(time.RFC3339)
	status := computeBudgetStatus(5, 5, 5, &future)
	if status != "COOLDOWN" {
		t.Errorf("expected COOLDOWN, got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_Cooldown_Expired(t *testing.T) {
	past := time.Now().Add(-2 * time.Hour).UTC().Format(time.RFC3339)
	// Expired cooldown — should fall through to OK
	status := computeBudgetStatus(5, 5, 5, &past)
	if status != "OK" {
		t.Errorf("expected OK after expired cooldown, got %s", status)
	}
}

func TestWave120_ComputeBudgetStatus_Cooldown_EmptyString(t *testing.T) {
	empty := ""
	status := computeBudgetStatus(5, 5, 5, &empty)
	if status != "OK" {
		t.Errorf("expected OK for empty cooldown string, got %s", status)
	}
}

func TestWave120_EventTypeFor_NoChange(t *testing.T) {
	et := eventTypeFor("OK", "OK")
	if et != "no_change" {
		t.Errorf("expected no_change, got %s", et)
	}
}

func TestWave120_EventTypeFor_CooldownSet(t *testing.T) {
	et := eventTypeFor("OK", "COOLDOWN")
	if et != "cooldown_set" {
		t.Errorf("expected cooldown_set, got %s", et)
	}
}

func TestWave120_EventTypeFor_CooldownClear(t *testing.T) {
	et := eventTypeFor("COOLDOWN", "OK")
	if et != "cooldown_clear" {
		t.Errorf("expected cooldown_clear, got %s", et)
	}
}

func TestWave120_EventTypeFor_Reset(t *testing.T) {
	et := eventTypeFor("RED", "OK")
	if et != "reset" {
		t.Errorf("expected reset, got %s", et)
	}
}

func TestWave120_EventTypeFor_Update(t *testing.T) {
	et := eventTypeFor("OK", "RED")
	if et != "update" {
		t.Errorf("expected update, got %s", et)
	}
}

func TestWave120_TokenBudgetFilePath_DefaultRoot(t *testing.T) {
	got := tokenBudgetFilePath("", "prya")
	expected := filepath.Join(".", ".forge", "heartbeat", "token-budgets-prya.json")
	if got != expected {
		t.Errorf("expected %s, got %s", expected, got)
	}
}

func TestWave120_TokenBudgetFilePath_CustomRoot(t *testing.T) {
	got := tokenBudgetFilePath("/opt/forge", "sati")
	expected := filepath.Join("/opt/forge", ".forge", "heartbeat", "token-budgets-sati.json")
	if got != expected {
		t.Errorf("expected %s, got %s", expected, got)
	}
}

func TestWave120_NodeIDFromEnv_EnvSet(t *testing.T) {
	t.Setenv("NODE_ID", "testnode")
	id := nodeIDFromEnv()
	if id != "testnode" {
		t.Errorf("expected testnode, got %s", id)
	}
}

func TestWave120_NodeIDFromEnv_EnvUnset(t *testing.T) {
	t.Setenv("NODE_ID", "")
	id := nodeIDFromEnv()
	// Should return hostname (or "unknown") — just verify non-empty
	if id == "" {
		t.Error("nodeIDFromEnv should never return empty string")
	}
}

func TestWave120_NodeIDFromEnv_NoDomain(t *testing.T) {
	t.Setenv("NODE_ID", "")
	// nodeIDFromEnv strips domain suffix — test with a dotted hostname by
	// checking the return value doesn't contain a dot (if hostname has one).
	id := nodeIDFromEnv()
	for _, c := range id {
		if c == '.' {
			t.Errorf("nodeIDFromEnv should strip domain suffix, got: %s", id)
			break
		}
	}
}

// --- event_store.go: Task.Apply pure state machine ---

func TestWave120_TaskApply_Created(t *testing.T) {
	ts := time.Now().UTC()
	payload, _ := json.Marshal(Task{
		ID: "t1", Domain: "eng", Project: "forge", Type: "feature",
		Priority: 5, Status: "queued",
	})
	e := Event{Type: EventTaskCreated, Payload: payload, Timestamp: ts}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply EventTaskCreated: %v", err)
	}
	if task.ID != "t1" {
		t.Errorf("expected ID=t1, got %s", task.ID)
	}
	if task.Domain != "eng" {
		t.Errorf("expected Domain=eng, got %s", task.Domain)
	}
	if !task.CreatedAt.Equal(ts) {
		t.Errorf("expected CreatedAt=%v, got %v", ts, task.CreatedAt)
	}
}

func TestWave120_TaskApply_Assigned(t *testing.T) {
	ts := time.Now().UTC()
	payload, _ := json.Marshal(map[string]string{"agent_id": "kimi"})
	e := Event{Type: EventTaskAssigned, Payload: payload, Timestamp: ts}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply EventTaskAssigned: %v", err)
	}
	if task.AssignedTo != "kimi" {
		t.Errorf("expected AssignedTo=kimi, got %s", task.AssignedTo)
	}
	if task.Status != TaskStatusExecuting {
		t.Errorf("expected Status=%s, got %s", TaskStatusExecuting, task.Status)
	}
}

func TestWave120_TaskApply_Started(t *testing.T) {
	ts := time.Now().UTC()
	e := Event{Type: EventTaskStarted, Payload: json.RawMessage(`{}`), Timestamp: ts}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply EventTaskStarted: %v", err)
	}
	if task.Status != TaskStatusExecuting {
		t.Errorf("expected Status=%s, got %s", TaskStatusExecuting, task.Status)
	}
	if task.StartedAt == nil {
		t.Error("expected StartedAt to be set")
	}
}

func TestWave120_TaskApply_Completed(t *testing.T) {
	ts := time.Now().UTC()
	payload, _ := json.Marshal(map[string]string{"result": "success"})
	e := Event{Type: EventTaskCompleted, Payload: payload, Timestamp: ts}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply EventTaskCompleted: %v", err)
	}
	if task.Status != TaskStatusCompleted {
		t.Errorf("expected Status=%s, got %s", TaskStatusCompleted, task.Status)
	}
	if task.Result != "success" {
		t.Errorf("expected Result=success, got %s", task.Result)
	}
}

func TestWave120_TaskApply_Failed(t *testing.T) {
	ts := time.Now().UTC()
	payload, _ := json.Marshal(map[string]string{"error": "timeout"})
	e := Event{Type: EventTaskFailed, Payload: payload, Timestamp: ts}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Fatalf("Apply EventTaskFailed: %v", err)
	}
	if task.Status != TaskStatusFailed {
		t.Errorf("expected Status=%s, got %s", TaskStatusFailed, task.Status)
	}
	if task.Error != "timeout" {
		t.Errorf("expected Error=timeout, got %s", task.Error)
	}
}

func TestWave120_TaskApply_UnknownEvent_NoError(t *testing.T) {
	e := Event{Type: "UnknownEventType", Payload: json.RawMessage(`{}`)}
	task := &Task{}
	if err := task.Apply(e); err != nil {
		t.Errorf("unknown event type should not error, got: %v", err)
	}
}

func TestWave120_TaskApply_Created_InvalidJSON(t *testing.T) {
	e := Event{Type: EventTaskCreated, Payload: json.RawMessage(`not-json`)}
	task := &Task{}
	err := task.Apply(e)
	if err == nil {
		t.Error("expected error for invalid JSON in EventTaskCreated payload")
	}
}

func TestWave120_TaskApply_Assigned_InvalidJSON(t *testing.T) {
	e := Event{Type: EventTaskAssigned, Payload: json.RawMessage(`not-json`)}
	task := &Task{}
	err := task.Apply(e)
	if err == nil {
		t.Error("expected error for invalid JSON in EventTaskAssigned payload")
	}
}

// --- TASK-SHARP-FLASH-007: /ui/patrol/{id} drill-down and POST /api/patrols/{id}/run ---

func TestWave120_uiPatrolDrillDownHandler_ReturnsHTML(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/ui/patrol/agent-health", nil)
	w := httptest.NewRecorder()
	uiPatrolDrillDownHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "agent-health") {
		t.Errorf("expected body to contain patrol id, got: %s", body[:minInt(200, len(body))])
	}
	if !strings.Contains(body, "Back to Fleet") {
		t.Errorf("expected back link in body")
	}
	if ct := w.Header().Get("Content-Type"); ct != "text/html; charset=utf-8" {
		t.Errorf("expected text/html, got %s", ct)
	}
}

func TestWave120_uiPatrolDrillDownHandler_EmptyID_RedirectsToUI(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/ui/patrol/", nil)
	w := httptest.NewRecorder()
	uiPatrolDrillDownHandler(w, req)
	if w.Code != http.StatusFound {
		t.Errorf("expected 302 redirect, got %d", w.Code)
	}
	if loc := w.Header().Get("Location"); loc != "/ui" {
		t.Errorf("expected Location /ui, got %s", loc)
	}
}

func TestWave120_patrolRunHandler_NotFound_Returns404(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/nonexistent-patrol-id-xyz/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	// With nil globalPatrolSystem we get 503; with system but unknown id we get 404
	if w.Code != http.StatusNotFound && w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 404 or 503, got %d: %s", w.Code, w.Body.String())
	}
}

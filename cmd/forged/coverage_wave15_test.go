//go:build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// setupHandoffTest creates a test DB with the handoffs table migrated.
func setupHandoffTest(t *testing.T) (store HandoffStore, service *HandoffService, cleanup func()) {
	t.Helper()
	dbPath := "/tmp/test_handoffs_" + time.Now().Format("20060102150405.999999999") + ".db"

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("failed to migrate: %v", err)
	}

	store = NewHandoffStore(db)
	service = NewHandoffService(store)
	cleanup = func() {
		db.Close()
		os.Remove(dbPath)
	}
	return store, service, cleanup
}

// ---------------------------------------------------------------------------
// handlers_proxy.go — harnessFallbackHandler
// ---------------------------------------------------------------------------

// TestHarnessFallbackHandler_NoProxy verifies that without a proxy the handler
// returns 404 with a JSON hint.
func TestHarnessFallbackHandler_NoProxy(t *testing.T) {
	// Ensure no proxy is set.
	saved := harnessProxy
	harnessProxy = nil
	defer func() { harnessProxy = saved }()

	req := httptest.NewRequest(http.MethodGet, "/unknown/route", nil)
	w := httptest.NewRecorder()
	harnessFallbackHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "not_found") {
		t.Errorf("expected not_found in body, got: %s", body)
	}
}

// TestHarnessFallbackHandler_WithProxy verifies the handler forwards to a real
// upstream when harnessProxy is configured.
func TestHarnessFallbackHandler_WithProxy(t *testing.T) {
	// Start a minimal upstream server.
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"proxied":true}`))
	}))
	defer upstream.Close()

	// Save and restore global proxy.
	saved := harnessProxy
	defer func() { harnessProxy = saved }()

	// Configure proxy to upstream.
	os.Setenv("FORGE_HARNESS_URL", upstream.URL)
	initHarnessProxy()
	defer func() {
		os.Unsetenv("FORGE_HARNESS_URL")
	}()

	req := httptest.NewRequest(http.MethodGet, "/some/path", nil)
	w := httptest.NewRecorder()
	harnessFallbackHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 from proxy, got %d: %s", w.Code, w.Body.String())
	}
}

// TestInitHarnessProxy_EmptyURL verifies no-op when FORGE_HARNESS_URL is unset.
func TestInitHarnessProxy_EmptyURL(t *testing.T) {
	saved := harnessProxy
	defer func() { harnessProxy = saved }()

	os.Unsetenv("FORGE_HARNESS_URL")
	harnessProxy = nil
	initHarnessProxy()

	if harnessProxy != nil {
		t.Error("expected harnessProxy to remain nil when FORGE_HARNESS_URL is empty")
	}
}

// TestInitHarnessProxy_InvalidURL_W15 verifies graceful handling of a malformed URL.
func TestInitHarnessProxy_InvalidURL_W15(t *testing.T) {
	saved := harnessProxy
	defer func() { harnessProxy = saved }()

	os.Setenv("FORGE_HARNESS_URL", "://bad-url")
	defer os.Unsetenv("FORGE_HARNESS_URL")
	harnessProxy = nil
	initHarnessProxy()

	// The proxy should NOT be set if the URL is invalid.
	if harnessProxy != nil {
		t.Error("expected harnessProxy to remain nil for invalid URL")
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — HandoffStore CRUD
// ---------------------------------------------------------------------------

func TestHandoffStore_CreateAndGet(t *testing.T) {
	store, _, cleanup := setupHandoffTest(t)
	defer cleanup()

	h := Handoff{
		ID:              "handoff-001",
		FromAgent:       "kimi",
		ToAgent:         "glm",
		TaskDescription: "Test task handoff",
		Files:           []string{"main.go", "queue.go"},
		Priority:        "high",
		Status:          HandoffPending,
		CreatedAt:       time.Now().UTC(),
		UpdatedAt:       time.Now().UTC(),
	}

	if err := store.Create(context.Background(), h); err != nil {
		t.Fatalf("Create failed: %v", err)
	}

	got, err := store.Get(context.Background(), "handoff-001")
	if err != nil {
		t.Fatalf("Get failed: %v", err)
	}
	if got.FromAgent != "kimi" {
		t.Errorf("expected FromAgent 'kimi', got %q", got.FromAgent)
	}
	if got.Status != HandoffPending {
		t.Errorf("expected status pending, got %q", got.Status)
	}
}

func TestHandoffStore_GetNotFound(t *testing.T) {
	store, _, cleanup := setupHandoffTest(t)
	defer cleanup()

	_, err := store.Get(context.Background(), "nonexistent-id")
	if err == nil {
		t.Error("expected error for missing handoff, got nil")
	}
}

func TestHandoffStore_List(t *testing.T) {
	store, _, cleanup := setupHandoffTest(t)
	defer cleanup()

	for i, id := range []string{"h1", "h2", "h3"} {
		_ = store.Create(context.Background(), Handoff{
			ID:              id,
			FromAgent:       "kimi",
			ToAgent:         "glm",
			TaskDescription: "task",
			Priority:        "normal",
			Status:          HandoffPending,
			CreatedAt:       time.Now().Add(time.Duration(i) * time.Second).UTC(),
			UpdatedAt:       time.Now().UTC(),
		})
	}

	handoffs, err := store.List(context.Background(), "", "", "", 10)
	if err != nil {
		t.Fatalf("List failed: %v", err)
	}
	if len(handoffs) != 3 {
		t.Errorf("expected 3 handoffs, got %d", len(handoffs))
	}

	// Filter by status
	filtered, err := store.List(context.Background(), "pending", "", "", 10)
	if err != nil {
		t.Fatalf("List(pending) failed: %v", err)
	}
	if len(filtered) != 3 {
		t.Errorf("expected 3 pending handoffs, got %d", len(filtered))
	}

	// Filter by from_agent
	byFrom, err := store.List(context.Background(), "", "kimi", "", 10)
	if err != nil {
		t.Fatalf("List(from_agent) failed: %v", err)
	}
	if len(byFrom) != 3 {
		t.Errorf("expected 3 from kimi, got %d", len(byFrom))
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — HandoffService
// ---------------------------------------------------------------------------

func TestHandoffService_CreateAndAccept(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	h, err := service.Create(context.Background(), "kimi", "glm", "implement feature X", []string{"main.go"}, "high")
	if err != nil {
		t.Fatalf("Create failed: %v", err)
	}
	if h.Status != HandoffPending {
		t.Errorf("expected pending, got %q", h.Status)
	}

	accepted, err := service.Accept(context.Background(), h.ID, "glm-instance-1")
	if err != nil {
		t.Fatalf("Accept failed: %v", err)
	}
	if accepted.Status != HandoffAccepted {
		t.Errorf("expected accepted, got %q", accepted.Status)
	}
}

func TestHandoffService_Reject_W15(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	h, err := service.Create(context.Background(), "kimi", "glm", "task", nil, "normal")
	if err != nil {
		t.Fatalf("Create failed: %v", err)
	}

	rejected, err := service.Reject(context.Background(), h.ID, "agent busy")
	if err != nil {
		t.Fatalf("Reject failed: %v", err)
	}
	if rejected.Status != HandoffRejected {
		t.Errorf("expected rejected, got %q", rejected.Status)
	}
}

func TestHandoffService_Complete_W15(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	h, err := service.Create(context.Background(), "kimi", "glm", "task", nil, "normal")
	if err != nil {
		t.Fatalf("Create failed: %v", err)
	}

	// Accept first, then complete.
	_, err = service.Accept(context.Background(), h.ID, "glm")
	if err != nil {
		t.Fatalf("Accept failed: %v", err)
	}

	completed, err := service.Complete(context.Background(), h.ID, "done and merged")
	if err != nil {
		t.Fatalf("Complete failed: %v", err)
	}
	if completed.Status != HandoffCompleted {
		t.Errorf("expected completed, got %q", completed.Status)
	}
}

func TestHandoffService_AcceptNotFound(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	_, err := service.Accept(context.Background(), "no-such-id", "agent")
	if err == nil {
		t.Error("expected error for missing handoff, got nil")
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — HTTP handlers via HandoffHandler
// ---------------------------------------------------------------------------

func TestHandoffHandler_CreateAndList(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	// Create via POST.
	payload := map[string]interface{}{
		"from_agent":       "kimi",
		"to_agent":         "glm",
		"task_description": "build feature Y",
		"files":            []string{"main.go"},
		"priority":         "high",
	}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}

	// List via GET.
	req2 := httptest.NewRequest(http.MethodGet, "/api/handoffs", nil)
	w2 := httptest.NewRecorder()
	handler.handleHandoffs(w2, req2)

	if w2.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w2.Code, w2.Body.String())
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w2.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to parse list response: %v", err)
	}
}

func TestHandoffHandler_CreateMissingFields(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	// Missing to_agent and task_description.
	payload := map[string]interface{}{"from_agent": "kimi"}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_ActionInvalidPath(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/bad-path-no-action", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandoffHandler_AcceptAction(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	// Create a handoff first.
	h, err := service.Create(context.Background(), "kimi", "glm", "task", nil, "normal")
	if err != nil {
		t.Fatalf("setup failed: %v", err)
	}

	body, _ := json.Marshal(map[string]string{"accepting_agent": "glm-1"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+h.ID+"/accept", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_RejectActionMissingReason(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	h, err := service.Create(context.Background(), "kimi", "glm", "task", nil, "normal")
	if err != nil {
		t.Fatalf("setup failed: %v", err)
	}

	// Missing reason field.
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+h.ID+"/reject", bytes.NewReader([]byte(`{}`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_CompleteAction(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	h, err := service.Create(context.Background(), "kimi", "glm", "task", nil, "normal")
	if err != nil {
		t.Fatalf("setup failed: %v", err)
	}
	// Accept before completing.
	_, err = service.Accept(context.Background(), h.ID, "glm")
	if err != nil {
		t.Fatalf("Accept failed: %v", err)
	}

	body, _ := json.Marshal(map[string]string{"notes": "shipped"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+h.ID+"/complete", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_UnknownAction(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/some-id/bogus", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandoffHandler_ActionNotFound(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	body, _ := json.Marshal(map[string]string{"accepting_agent": "glm"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/no-such-id/accept", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// util_misc.go — Utility function coverage
// ---------------------------------------------------------------------------

func TestMinInt_Wave15(t *testing.T) {
	if minInt(1, 2) != 1 {
		t.Error("minInt(1, 2) should return 1")
	}
	if minInt(5, 3) != 3 {
		t.Error("minInt(5, 3) should return 3")
	}
	if minInt(-1, 1) != -1 {
		t.Error("minInt(-1, 1) should return -1")
	}
}

func TestPriorityToInt_Wave15(t *testing.T) {
	tests := []struct {
		input    string
		expected int
	}{
		{"critical", 1},
		{"high", 2},
		{"medium", 3},
		{"low", 4},
		{"unknown", 5},
		{"", 5},
	}
	for _, tc := range tests {
		got := priorityToInt(tc.input)
		if got != tc.expected {
				t.Errorf("priorityToInt(%q) = %d, want %d", tc.input, got, tc.expected)
		}
	}
}

func TestPopulateTasksFromFeatures_Stub(t *testing.T) {
	err := PopulateTasksFromFeatures("/tmp/db.db", "/tmp/features.json")
	if err == nil {
		t.Error("expected error from stub")
	}
	if !strings.Contains(err.Error(), "removed") {
		t.Errorf("expected 'removed' in error, got: %v", err)
	}
}

func TestPopulateTasksFromDispatches_Stub(t *testing.T) {
	err := PopulateTasksFromDispatches("/tmp/db.db", "/tmp/dispatches")
	if err == nil {
		t.Error("expected error from stub")
	}
	if !strings.Contains(err.Error(), "removed") {
		t.Errorf("expected 'removed' in error, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go — agentFleetSummaryHandler_V2
// ---------------------------------------------------------------------------

func TestAgentFleetSummaryHandler_V2_NoDB(t *testing.T) {
	// Save and nil the DB
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// Additional handler edge cases
// ---------------------------------------------------------------------------

func TestContextByIDHandler_Wave15(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/context/nonexistent", nil)
	w := httptest.NewRecorder()
	contextByIDHandler(w, req)
	// Returns 400 for missing ID path parameter
	if w.Code != http.StatusBadRequest {
		t.Errorf("unexpected status: %d", w.Code)
	}
}

func TestOpenclawEventsHandler_Post_Wave15(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawStatusHandler_Delete_Wave15(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawStatusHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// Task State Machine edge cases - these require complex setup, // ---------------------------------------------------------------------------
// Skipping for now - NewStateMachine requires *TaskStore and *sql.DB

// ---------------------------------------------------------------------------
// Queue edge cases
// ---------------------------------------------------------------------------

func TestTaskQueue_Dequeue_Empty_Wave15(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Dequeue from empty queue should return error
	_, err := taskQueue.Dequeue(context.Background(), "test-agent")
	if err == nil {
		t.Error("expected error dequeuing from empty queue")
	}
}

// ---------------------------------------------------------------------------
// Additional simple tests for priorityToInt
// ---------------------------------------------------------------------------

func TestPriorityToInt_AllValues_Wave15(t *testing.T) {
	tests := []struct {
		input    string
		expected int
	}{
		{"critical", 1},
		{"high", 2},
		{"medium", 3},
		{"low", 4},
		{"unknown", 5},
		{"", 5},
	}
	for _, tc := range tests {
		got := priorityToInt(tc.input)
		if got != tc.expected {
			t.Errorf("priorityToInt(%q) = %d, want %d", tc.input, got, tc.expected)
		}
	}
}

// ---------------------------------------------------------------------------
// Additional handler method checks
// ---------------------------------------------------------------------------

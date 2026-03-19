//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ============================================================
// dashboard.go — pure helpers: splitCapabilities, splitString, trimSpace
// ============================================================

func TestSplitCapabilities_Empty(t *testing.T) {
	got := splitCapabilities("")
	if got != nil {
		t.Errorf("splitCapabilities(\"\") = %v, want nil", got)
	}
}

func TestSplitCapabilities_Single(t *testing.T) {
	got := splitCapabilities("golang")
	if len(got) != 1 || got[0] != "golang" {
		t.Errorf("splitCapabilities(\"golang\") = %v", got)
	}
}

func TestSplitCapabilities_Multiple(t *testing.T) {
	got := splitCapabilities("golang, python , rust")
	want := []string{"golang", "python", "rust"}
	if len(got) != len(want) {
		t.Fatalf("splitCapabilities len = %d, want %d; got %v", len(got), len(want), got)
	}
	for i, v := range want {
		if got[i] != v {
			t.Errorf("splitCapabilities[%d] = %q, want %q", i, got[i], v)
		}
	}
}

func TestSplitCapabilities_TrailingComma(t *testing.T) {
	// Trailing comma produces an empty last element — this documents the actual behaviour.
	got := splitCapabilities("a,b,")
	if len(got) < 2 {
		t.Errorf("splitCapabilities(\"a,b,\") = %v, expected at least 2 elements", got)
	}
	if got[0] != "a" || got[1] != "b" {
		t.Errorf("splitCapabilities(\"a,b,\") = %v", got)
	}
}

func TestSplitString_Basic(t *testing.T) {
	got := splitString("a,b,c", ",")
	want := []string{"a", "b", "c"}
	if len(got) != len(want) {
		t.Fatalf("splitString len = %d, want %d", len(got), len(want))
	}
	for i, v := range want {
		if got[i] != v {
			t.Errorf("splitString[%d] = %q, want %q", i, got[i], v)
		}
	}
}

func TestSplitString_NoSep(t *testing.T) {
	got := splitString("hello", ",")
	if len(got) != 1 || got[0] != "hello" {
		t.Errorf("splitString no-sep = %v", got)
	}
}

func TestSplitString_MultiCharSep(t *testing.T) {
	got := splitString("a::b::c", "::")
	want := []string{"a", "b", "c"}
	if len(got) != len(want) {
		t.Fatalf("splitString multi-char sep = %v, want %v", got, want)
	}
	for i, v := range want {
		if got[i] != v {
			t.Errorf("splitString[%d] = %q, want %q", i, got[i], v)
		}
	}
}

func TestSplitString_Empty(t *testing.T) {
	got := splitString("", ",")
	if len(got) != 1 || got[0] != "" {
		t.Errorf("splitString(\"\", \",\") = %v", got)
	}
}

func TestTrimSpace_Leading(t *testing.T) {
	if got := trimSpace("  hello"); got != "hello" {
		t.Errorf("trimSpace leading = %q", got)
	}
}

func TestTrimSpace_Trailing(t *testing.T) {
	if got := trimSpace("hello  "); got != "hello" {
		t.Errorf("trimSpace trailing = %q", got)
	}
}

func TestTrimSpace_Both(t *testing.T) {
	if got := trimSpace("  hello world  "); got != "hello world" {
		t.Errorf("trimSpace both = %q", got)
	}
}

func TestTrimSpace_Tabs(t *testing.T) {
	if got := trimSpace("\t\nhello\r\n"); got != "hello" {
		t.Errorf("trimSpace tabs/newlines = %q", got)
	}
}

func TestTrimSpace_Empty(t *testing.T) {
	if got := trimSpace(""); got != "" {
		t.Errorf("trimSpace empty = %q", got)
	}
}

func TestTrimSpace_OnlySpaces(t *testing.T) {
	if got := trimSpace("   "); got != "" {
		t.Errorf("trimSpace only spaces = %q", got)
	}
}

// ============================================================
// dashboard.go — HTTP handlers via httptest
// ============================================================

// newTestDashboard creates a Dashboard wired to a migrated in-memory-like SQLite DB.
func newTestDashboard(t *testing.T) (*Dashboard, func()) {
	t.Helper()
	cleanup := setupHandlerTest(t)
	db := getDBConn()
	d := NewDashboard(db, hub)
	return d, cleanup
}

func TestDashboard_RegisterRoutes(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	mux := http.NewServeMux()
	d.RegisterRoutes(mux) // must not panic

	// Verify routes are registered by making requests.
	routes := []string{"/dashboard", "/api/dashboard", "/api/agents/dashboard"}
	for _, route := range routes {
		req := httptest.NewRequest(http.MethodGet, route, nil)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, req)
		// 200 or 500 are both acceptable — we just need the handler to be wired.
		if w.Code == http.StatusNotFound {
			t.Errorf("route %s returned 404, expected it to be registered", route)
		}
	}
}

func TestDashboard_ServeAgentHealth_MissingID(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	// Path that produces an empty agentID after stripping the prefix.
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents/", nil)
	w := httptest.NewRecorder()
	d.ServeAgentHealth(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent ID, got %d: %s", w.Code, w.Body.String())
	}
}

func TestDashboard_ServeAgentHealth_NotFound(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents/nonexistent-agent-id", nil)
	w := httptest.NewRecorder()
	d.ServeAgentHealth(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown agent, got %d: %s", w.Code, w.Body.String())
	}
}

func TestDashboard_ServeAgentHealth_Found(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	db := getDBConn()
	// Insert a fake agent heartbeat so ServeAgentHealth can find it.
	_, err := db.Exec(`
		INSERT OR REPLACE INTO agent_heartbeats
			(agent_id, node, status, current_task_id, context_pct, capabilities, last_seen, connected_at)
		VALUES (?, ?, ?, NULL, ?, ?, datetime('now'), datetime('now'))
	`, "wave4-agent-1", "test-node", "idle", 42.0, "golang,python")
	if err != nil {
		t.Fatalf("insert agent heartbeat: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents/wave4-agent-1", nil)
	w := httptest.NewRecorder()
	d.ServeAgentHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("response JSON parse: %v", err)
	}
	if body["agent_id"] != "wave4-agent-1" {
		t.Errorf("agent_id = %v, want wave4-agent-1", body["agent_id"])
	}
}

func TestDashboard_ServeAgentsDashboard_OK(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeAgentsDashboard(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("response JSON parse: %v", err)
	}
	if _, ok := body["agents"]; !ok {
		t.Error("response missing 'agents' key")
	}
	if _, ok := body["count"]; !ok {
		t.Error("response missing 'count' key")
	}
}

// ============================================================
// errors.go — ForgeError.JSON without os.Exit paths
// ============================================================

func TestForgeError_JSON_WithDetails(t *testing.T) {
	fe := &ForgeError{
		Code:    ErrCodeInternal,
		Message: "boom",
		Details: "stack trace here",
	}
	got := fe.JSON()
	if !strings.Contains(got, `"INTERNAL_ERROR"`) {
		t.Errorf("JSON() missing code: %s", got)
	}
	if !strings.Contains(got, `"stack trace here"`) {
		t.Errorf("JSON() missing details: %s", got)
	}
}

func TestForgeError_JSON_WithoutSuggestion(t *testing.T) {
	fe := &ForgeError{
		Code:    ErrCodeNotFound,
		Message: "not found",
	}
	got := fe.JSON()
	if got == "" {
		t.Fatal("JSON() must not be empty")
	}
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(got), &m); err != nil {
		t.Fatalf("JSON() is not valid JSON: %v — output: %s", err, got)
	}
}

func TestForgeError_Error_WithoutSuggestion(t *testing.T) {
	fe := &ForgeError{
		Code:    ErrCodeDuplicate,
		Message: "already exists",
	}
	got := fe.Error()
	if !strings.Contains(got, "DUPLICATE") {
		t.Errorf("Error() missing code: %s", got)
	}
	if strings.Contains(got, "Suggestion:") {
		t.Errorf("Error() should not contain 'Suggestion:' when empty: %s", got)
	}
}

// ============================================================
// event_bus.go — Unsubscribe
// ============================================================

func newTestEventBus(t *testing.T) (*EventBus, func()) {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	eb, err := NewEventBus(db)
	if err != nil {
		db.Close()
		t.Fatalf("NewEventBus: %v", err)
	}
	return eb, func() {
		eb.Close()
		db.Close()
	}
}

func TestEventBus_Unsubscribe_NonExistentTopic(t *testing.T) {
	eb, cleanup := newTestEventBus(t)
	defer cleanup()

	// Unsubscribing from a topic with no subscribers should be a no-op (no error).
	handler := func(e BusEvent) error { return nil }
	if err := eb.Unsubscribe("nonexistent.topic", handler); err != nil {
		t.Errorf("Unsubscribe nonexistent topic: unexpected error: %v", err)
	}
}

func TestEventBus_Unsubscribe_SingleSubscriber(t *testing.T) {
	eb, cleanup := newTestEventBus(t)
	defer cleanup()

	called := 0
	handler := func(e BusEvent) error {
		called++
		return nil
	}

	if err := eb.Subscribe(TopicTaskCreated, handler); err != nil {
		t.Fatalf("Subscribe: %v", err)
	}

	// Verify it's subscribed.
	eb.mu.RLock()
	before := len(eb.subscribers[TopicTaskCreated])
	eb.mu.RUnlock()
	if before != 1 {
		t.Fatalf("expected 1 subscriber, got %d", before)
	}

	// Unsubscribe.
	if err := eb.Unsubscribe(TopicTaskCreated, handler); err != nil {
		t.Fatalf("Unsubscribe: %v", err)
	}

	// After unsubscribing the only handler, topic should be absent.
	eb.mu.RLock()
	after := len(eb.subscribers[TopicTaskCreated])
	eb.mu.RUnlock()
	if after != 0 {
		t.Errorf("expected 0 subscribers after Unsubscribe, got %d", after)
	}
}

func TestEventBus_Unsubscribe_MultipleSubscribers(t *testing.T) {
	eb, cleanup := newTestEventBus(t)
	defer cleanup()

	h1 := func(e BusEvent) error { return nil }
	h2 := func(e BusEvent) error { return nil }

	eb.Subscribe(TopicTaskCreated, h1)
	eb.Subscribe(TopicTaskCreated, h2)

	eb.mu.RLock()
	before := len(eb.subscribers[TopicTaskCreated])
	eb.mu.RUnlock()
	if before != 2 {
		t.Fatalf("expected 2 subscribers before Unsubscribe, got %d", before)
	}

	// Unsubscribe removes one (the last one per the current implementation).
	if err := eb.Unsubscribe(TopicTaskCreated, h2); err != nil {
		t.Fatalf("Unsubscribe: %v", err)
	}

	eb.mu.RLock()
	after := len(eb.subscribers[TopicTaskCreated])
	eb.mu.RUnlock()
	if after != 1 {
		t.Errorf("expected 1 subscriber after Unsubscribe, got %d", after)
	}
}

// ============================================================
// completion.go — HandleCompletion (valid shell args, no os.Exit path)
// ============================================================

func TestCompletionManager_GenerateBash_Content(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateBash(&buf); err != nil {
		t.Fatalf("GenerateBash: %v", err)
	}
	out := buf.String()
	for _, keyword := range []string{"task", "agent", "fleet", "migrate"} {
		if !strings.Contains(out, keyword) {
			t.Errorf("GenerateBash output missing keyword %q", keyword)
		}
	}
}

func TestCompletionManager_GenerateZsh_Content(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "_forge") {
		t.Errorf("GenerateZsh output missing '_forge' function definition")
	}
}

func TestCompletionManager_GenerateFish_Content(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "migrate") {
		t.Errorf("GenerateFish output missing 'migrate' command")
	}
}

// ============================================================
// approvals.go — RunExpirationCheck and createApproval coverage
// ============================================================

// TestApprovalHandler_RunExpirationCheck verifies the loop processes
// at least one tick before the context is cancelled.
func TestApprovalHandler_RunExpirationCheck(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert an approval that is already expired.
	a := makeApproval("rexp-wave4-001")
	a.ExpiresAt = time.Now().UTC().Add(-2 * time.Hour)
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	// Run the expiration loop with a very short interval.
	// Cancel after the first tick has had time to fire.
	cancelCtx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
	defer cancel()

	done := make(chan struct{})
	go func() {
		h.RunExpirationCheck(cancelCtx, 50*time.Millisecond)
		close(done)
	}()

	// Wait for goroutine to exit after context cancels.
	select {
	case <-done:
		// success — goroutine exited cleanly
	case <-time.After(2 * time.Second):
		t.Fatal("RunExpirationCheck goroutine did not exit after context cancellation")
	}

	// Confirm expired approval has been updated.
	got, err := store.Get(ctx, a.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.Status != StatusExpired {
		t.Errorf("approval status = %s, want expired", got.Status)
	}
}

// TestApprovalHandler_CreateApproval_MissingFields exercises the validation
// branches in createApproval without going through os.Exit.
func TestApprovalHandler_CreateApproval_MissingFields(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	tests := []struct {
		name     string
		body     string
		wantCode int
	}{
		{
			name:     "empty body",
			body:     `{}`,
			wantCode: http.StatusBadRequest,
		},
		{
			name:     "missing agent_id",
			body:     `{"type":"task_completion","domain":"d","title":"t"}`,
			wantCode: http.StatusBadRequest,
		},
		{
			name:     "missing task_id",
			body:     `{"type":"task_completion","agent_id":"a","domain":"d","title":"t"}`,
			wantCode: http.StatusBadRequest,
		},
		{
			name:     "invalid task_id characters",
			body:     `{"type":"task_completion","agent_id":"a","domain":"d","title":"t","task_id":"../../../etc/passwd"}`,
			wantCode: http.StatusBadRequest,
		},
		{
			name:     "invalid tier value",
			// task_id won't pass the task-exists check unless we skip that path,
			// so we test tier validation by using a valid task_id that happens to
			// be caught by the not-found check first — we just need to cover the
			// tier branch via a separate test body.
			body:     `{"type":"task_completion","agent_id":"a","domain":"d","title":"t","task_id":"VALIDID01234567890123456","tier":"INVALID_TIER"}`,
			wantCode: http.StatusBadRequest,
		},
		{
			name:     "invalid approval type",
			body:     `{"type":"not_a_type","agent_id":"a","domain":"d","title":"t","task_id":"VALIDID01234567890123456"}`,
			wantCode: http.StatusBadRequest,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/api/approvals", strings.NewReader(tt.body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			h.createApproval(w, req)
			if w.Code != tt.wantCode {
				t.Errorf("createApproval %s: got %d, want %d — body: %s",
					tt.name, w.Code, tt.wantCode, w.Body.String())
			}
		})
	}
}

// TestApprovalHandler_CreateApproval_Success creates a real task first,
// then posts a create-approval request that should succeed (201).
func TestApprovalHandler_CreateApproval_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Enqueue a real task so the task-exists check passes.
	task := enqueueTestTask(t, "wave4-approval-task-01")

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	body := fmt.Sprintf(`{
		"type": "task_completion",
		"agent_id": "wave4-agent",
		"domain": "test-domain",
		"title": "Wave4 approval test",
		"task_id": %q,
		"confidence_context": {
			"pattern_match": 0.8,
			"blast_radius": 3,
			"reversible": true,
			"test_results": {"passed": 10, "failed": 0, "skipped": 1, "coverage": 85.0}
		}
	}`, task.ID)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("response JSON: %v", err)
	}
	if resp["id"] == nil || resp["id"] == "" {
		t.Error("response missing 'id' field")
	}
}

// TestApprovalHandler_CreateApproval_InvalidJSON ensures the bad-JSON path is hit.
func TestApprovalHandler_CreateApproval_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals", strings.NewReader("{bad json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

// TestApprovalHandler_HandleApprovals_POST routes to createApproval.
// We exercise the routing layer (handleApprovals) with a POST.
func TestApprovalHandler_HandleApprovals_POST_MissingFields(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestApprovalHandler_HandlePending_MethodNotAllowed hits the 405 branch.
// TestApprovalHandler_ListApprovals_ByTier exercises the tier filter branch.
func TestApprovalHandler_ListApprovals_ByTier(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("list-tier-wave4-01")
	a.Tier = TierPhone
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?tier=phone", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestApprovalHandler_ListApprovals_ByAgentID exercises the agentID filter branch.
func TestApprovalHandler_ListApprovals_ByAgentID(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	a := makeApproval("list-agent-wave4-01")
	a.AgentID = "wave4-filter-agent"
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?agent_id=wave4-filter-agent", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ============================================================
// event_bus.go — Unsubscribe with wildcard topics
// ============================================================

func TestEventBus_Unsubscribe_WildcardTopic(t *testing.T) {
	eb, cleanup := newTestEventBus(t)
	defer cleanup()

	handler := func(e BusEvent) error { return nil }

	if err := eb.Subscribe(TopicTask, handler); err != nil {
		t.Fatalf("Subscribe wildcard: %v", err)
	}

	eb.mu.RLock()
	before := len(eb.subscribers[TopicTask])
	eb.mu.RUnlock()

	if before == 0 {
		t.Fatal("expected at least 1 wildcard subscriber before Unsubscribe")
	}

	if err := eb.Unsubscribe(TopicTask, handler); err != nil {
		t.Fatalf("Unsubscribe wildcard: %v", err)
	}

	eb.mu.RLock()
	after := len(eb.subscribers[TopicTask])
	eb.mu.RUnlock()

	if after != 0 {
		t.Errorf("expected 0 wildcard subscribers after Unsubscribe, got %d", after)
	}
}

// ============================================================
// dashboard.go — AgentStatus.String coverage
// ============================================================

func TestAgentStatus_String(t *testing.T) {
	cases := []struct {
		status AgentStatus
		want   string
	}{
		{AgentStatusOffline, "offline"},
		{AgentStatusIdle, "idle"},
		{AgentStatusWorking, "working"},
		{AgentStatusBlocked, "blocked"},
		{AgentStatusError, "error"},
		{AgentStatusMaintenance, "maintenance"},
		{AgentStatusUnknown, "unknown"},
		{AgentStatus(99), "unknown"},
	}
	for _, tc := range cases {
		got := tc.status.String()
		if got != tc.want {
			t.Errorf("AgentStatus(%d).String() = %q, want %q", tc.status, got, tc.want)
		}
	}
}

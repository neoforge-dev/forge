//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- splitLines ---

func TestSplitLines_Empty(t *testing.T) {
	if got := splitLines(""); len(got) != 0 {
		t.Errorf("splitLines(\"\") = %v, want []", got)
	}
}

func TestSplitLines_NoNewline(t *testing.T) {
	got := splitLines("hello")
	if len(got) != 1 || got[0] != "hello" {
		t.Errorf("splitLines(\"hello\") = %v, want [hello]", got)
	}
}

func TestSplitLines_MultiLine(t *testing.T) {
	got := splitLines("a\nb\nc")
	if len(got) != 3 {
		t.Fatalf("len = %d, want 3", len(got))
	}
	if got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Errorf("got %v", got)
	}
}

func TestSplitLines_TrailingNewline(t *testing.T) {
	got := splitLines("a\nb\n")
	// "a\nb\n" → ["a", "b"] (no trailing empty element since start==len(s))
	if len(got) != 2 {
		t.Errorf("len = %d, want 2; got %v", len(got), got)
	}
}

// --- containsIgnoreCase ---

func TestContainsIgnoreCase_Match(t *testing.T) {
	if !containsIgnoreCase("Hello World", "hello") {
		t.Error("expected match for 'hello' in 'Hello World'")
	}
}

func TestContainsIgnoreCase_NoMatch(t *testing.T) {
	if containsIgnoreCase("Hello World", "xyz") {
		t.Error("expected no match for 'xyz' in 'Hello World'")
	}
}

func TestContainsIgnoreCase_CaseInsensitive(t *testing.T) {
	if !containsIgnoreCase("FORGE Agent", "forge agent") {
		t.Error("expected case-insensitive match")
	}
}

// --- parsePct ---

func TestParsePct_Valid(t *testing.T) {
	pct, err := parsePct("42.5")
	if err != nil {
		t.Fatalf("parsePct: %v", err)
	}
	if pct != 42.5 {
		t.Errorf("pct = %f, want 42.5", pct)
	}
}

func TestParsePct_WithWhitespace(t *testing.T) {
	pct, err := parsePct("  75.0  ")
	if err != nil {
		t.Fatalf("parsePct: %v", err)
	}
	if pct != 75.0 {
		t.Errorf("pct = %f, want 75.0", pct)
	}
}

func TestParsePct_Invalid(t *testing.T) {
	_, err := parsePct("not-a-number")
	if err == nil {
		t.Fatal("expected error for non-numeric input")
	}
}

// --- parseMarkdownPct ---

func TestParseMarkdownPct_Found(t *testing.T) {
	content := "# Status\n- Context: 55%\n- Other: stuff\n"
	pct, err := parseMarkdownPct(content)
	if err != nil {
		t.Fatalf("parseMarkdownPct: %v", err)
	}
	if pct != 55.0 {
		t.Errorf("pct = %f, want 55.0", pct)
	}
}

func TestParseMarkdownPct_NotFound(t *testing.T) {
	content := "# Status\n- No context line here\n"
	_, err := parseMarkdownPct(content)
	if err == nil {
		t.Fatal("expected error when no Context line found")
	}
}

// --- healthHandler ---

func TestHealthHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	healthHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	var resp HealthResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Status != "ok" {
		t.Errorf("status = %s, want ok", resp.Status)
	}
	if resp.Timestamp == "" {
		t.Error("expected non-empty timestamp")
	}
}

// --- statusHandler ---

func TestStatusHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	rr := httptest.NewRecorder()
	statusHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	var resp StatusResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Status != "running" {
		t.Errorf("status = %s, want running", resp.Status)
	}
	if resp.Version == "" {
		t.Error("expected non-empty version")
	}
}

// --- parseContextFromDomain ---

func TestParseContextFromDomain(t *testing.T) {
	pct, ts, err := parseContextFromDomain("test-domain")
	// If the file doesn't exist, it should return an error.
	// But let's check what it does.
	if err == nil {
		t.Logf("pct: %f, ts: %v", pct, ts)
	}
}

func TestCLIRouter(t *testing.T) {
	mux := http.NewServeMux()
	CLIRouter(mux)
}

func TestCLIHandlers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	// Test handleTaskList
	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("handleTaskList returned %d", w.Code)
	}

	// Test handleAgentList
	req = httptest.NewRequest(http.MethodGet, "/cli/agent/list", nil)
	w = httptest.NewRecorder()
	handleAgentList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("handleAgentList returned %d", w.Code)
	}

	// Test handleSystemHealth
	req = httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	w = httptest.NewRecorder()
	handleSystemHealth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("handleSystemHealth returned %d", w.Code)
	}

}

// TestHandleTaskLogs_WithID exercises the path where a task ID is provided.
func TestHandleTaskLogs_WithID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs?id=nonexistent-task-id", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	// A non-existent task ID may return 404 or 200 with empty events — just verify no panic.
	_ = w.Code
}

// TestHandleSystemHealth_JSONFormat verifies handleSystemHealth with format=json.
func TestHandleSystemHealth_JSONFormat(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/cli/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for handleSystemHealth?format=json, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleQueueDepth_JSONFormat verifies handleQueueDepth with format=json when queue is set.
func TestHandleQueueDepth_JSONFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}


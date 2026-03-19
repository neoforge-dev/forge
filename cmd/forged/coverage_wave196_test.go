//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 196: validation.go + routing_handler.go + royal_jelly.go
// Targets:
//   sanitizeID            (validation.go:21)   — trims whitespace
//   isValidID             (validation.go:26)   — empty/too-long/valid/invalid
//   sanitizeText          (validation.go:34)   — trims whitespace
//   isValidText           (validation.go:41)   — too-long/empty/valid
//   writeJSONError        (validation.go:52)   — status/body
//   isTitleJunk           (validation.go:59)   — short/junk/valid
//   routingResolveHandler (routing_handler.go:47) — GET→405, invalid body, missing config
//   NewRoyalJelly         (royal_jelly.go:20)  — non-nil
//   RegisterHook          (royal_jelly.go:42)  — adds hook
//   TriggerHooks          (royal_jelly.go:47)  — no hook / with hook
//   ContextThresholdHook.Name(royal_jelly.go:70) — returns name
//   ContextThresholdHook.Trigger(royal_jelly.go:74) — below threshold / nil cm at threshold
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// sanitizeID
// ---------------------------------------------------------------------------

func TestWave196_SanitizeID_TrimsWhitespace(t *testing.T) {
	if got := sanitizeID("  hello  "); got != "hello" {
		t.Errorf("expected 'hello', got %q", got)
	}
}

func TestWave196_SanitizeID_NoOp(t *testing.T) {
	if got := sanitizeID("agent-1"); got != "agent-1" {
		t.Errorf("expected 'agent-1', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// isValidID
// ---------------------------------------------------------------------------

func TestWave196_IsValidID_Empty(t *testing.T) {
	if isValidID("") {
		t.Error("expected false for empty ID")
	}
}

func TestWave196_IsValidID_TooLong(t *testing.T) {
	if isValidID(strings.Repeat("a", 129)) {
		t.Error("expected false for 129-char ID")
	}
}

func TestWave196_IsValidID_Valid(t *testing.T) {
	if !isValidID("agent-1_ok:yes") {
		t.Error("expected true for valid ID")
	}
}

func TestWave196_IsValidID_InvalidChars(t *testing.T) {
	if isValidID("agent 1!") {
		t.Error("expected false for ID with spaces/special chars")
	}
}

// ---------------------------------------------------------------------------
// sanitizeText
// ---------------------------------------------------------------------------

func TestWave196_SanitizeText_TrimsWhitespace(t *testing.T) {
	if got := sanitizeText("  hello world  "); got != "hello world" {
		t.Errorf("expected 'hello world', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// isValidText
// ---------------------------------------------------------------------------

func TestWave196_IsValidText_TooLong(t *testing.T) {
	if isValidText(strings.Repeat("a", 2049)) {
		t.Error("expected false for 2049-char text")
	}
}

func TestWave196_IsValidText_Empty(t *testing.T) {
	if !isValidText("") {
		t.Error("expected true for empty text (allowed)")
	}
}

func TestWave196_IsValidText_ValidText(t *testing.T) {
	if !isValidText("Hello, world! (test)") {
		t.Error("expected true for valid text")
	}
}

// ---------------------------------------------------------------------------
// writeJSONError
// ---------------------------------------------------------------------------

func TestWave196_WriteJSONError_SetsStatusAndBody(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSONError(w, http.StatusBadRequest, "test error")
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "test error") {
		t.Errorf("expected error in body, got %q", w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// isTitleJunk
// ---------------------------------------------------------------------------

func TestWave196_IsTitleJunk_ShortTitle(t *testing.T) {
	if !isTitleJunk("fix") {
		t.Error("expected true for short title")
	}
}

func TestWave196_IsTitleJunk_JunkTitle(t *testing.T) {
	if !isTitleJunk("forge/dispatch") {
		t.Error("expected true for known junk title")
	}
}

func TestWave196_IsTitleJunk_ValidTitle(t *testing.T) {
	if isTitleJunk("Implement user authentication via OAuth 2.0") {
		t.Error("expected false for valid title")
	}
}

func TestWave196_IsTitleJunk_FeatureSuffix(t *testing.T) {
	if !isTitleJunk("add feature (feature)") {
		t.Error("expected true for (feature) suffix")
	}
}

// ---------------------------------------------------------------------------
// routingResolveHandler
// ---------------------------------------------------------------------------

func TestWave196_RoutingResolveHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/routing/resolve", nil)
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave196_RoutingResolveHandler_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve",
		strings.NewReader("not-json"))
	req.Header.Set("Content-Type", "application/json")
	req.ContentLength = int64(len("not-json"))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

func TestWave196_RoutingResolveHandler_NoBody(t *testing.T) {
	// Empty body → tries to load routing config → may 503 if config missing
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", nil)
	req.ContentLength = 0
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	// Either 200 (config found) or 503 (no config) — both valid
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("unexpected status: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// NewRoyalJelly
// ---------------------------------------------------------------------------

func TestWave196_NewRoyalJelly_NotNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	rj := NewRoyalJelly(db, nil)
	if rj == nil {
		t.Error("expected non-nil RoyalJelly")
	}
}

// ---------------------------------------------------------------------------
// RegisterHook + TriggerHooks
// ---------------------------------------------------------------------------

type testHook struct {
	name    string
	called  bool
}

func (h *testHook) Name() string { return h.name }
func (h *testHook) Trigger(_ context.Context, _ HookEvent) error {
	h.called = true
	return nil
}

func TestWave196_RoyalJelly_TriggerHooks_NoHook(t *testing.T) {
	rj := NewRoyalJelly(nil, nil)
	err := rj.TriggerHooks(context.Background(), HookEvent{Type: "nonexistent"})
	if err != nil {
		t.Errorf("expected no error for missing hook type: %v", err)
	}
}

func TestWave196_RoyalJelly_TriggerHooks_WithHook(t *testing.T) {
	rj := NewRoyalJelly(nil, nil)
	h := &testHook{name: "test-hook"}
	rj.RegisterHook("my-event", h)
	rj.TriggerHooks(context.Background(), HookEvent{Type: "my-event"})
	if !h.called {
		t.Error("expected hook to be called")
	}
}

// ---------------------------------------------------------------------------
// ContextThresholdHook
// ---------------------------------------------------------------------------

func TestWave196_ContextThresholdHook_Name(t *testing.T) {
	h := &ContextThresholdHook{}
	if h.Name() != "context_threshold_envelope" {
		t.Errorf("expected 'context_threshold_envelope', got %q", h.Name())
	}
}

func TestWave196_ContextThresholdHook_Trigger_BelowThreshold(t *testing.T) {
	h := &ContextThresholdHook{}
	err := h.Trigger(context.Background(), HookEvent{ContextPct: 30.0})
	if err != nil {
		t.Errorf("expected no error below threshold: %v", err)
	}
}

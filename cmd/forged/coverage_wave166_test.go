//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 166: validation.go + token_budget.go pure functions
// Targets:
//   sanitizeID         (validation.go:21) — trims spaces
//   isValidID          (validation.go:26) — empty/too long/safe chars/unsafe chars
//   sanitizeText       (validation.go:34) — trims spaces
//   isValidText        (validation.go:41) — empty/too long/safe/unsafe
//   writeJSONError     (validation.go:52) — writes JSON error response
//   isTitleJunk        (validation.go:59) — short/exact junk/feature suffix/clean
//   tokenBudgetFilePath(token_budget.go:37) — path formatting
//   readTokenBudgetFile(token_budget.go:47) — not exists, valid JSON, malformed
//   computeBudgetStatus(token_budget.go:120)— thresholds + cooldown
//   eventTypeFor       (token_budget.go:252)— all 5 event type branches
//   nodeIDFromEnv      (token_budget.go:312)— NODE_ID env, hostname fallback
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// sanitizeID
// ---------------------------------------------------------------------------

func TestWave166_SanitizeID_TrimsSpaces(t *testing.T) {
	if got := sanitizeID("  agent-1  "); got != "agent-1" {
		t.Errorf("expected %q, got %q", "agent-1", got)
	}
}

func TestWave166_SanitizeID_NoChange(t *testing.T) {
	if got := sanitizeID("agent-123"); got != "agent-123" {
		t.Errorf("unexpected: %q", got)
	}
}

// ---------------------------------------------------------------------------
// isValidID
// ---------------------------------------------------------------------------

func TestWave166_IsValidID_Empty(t *testing.T) {
	if isValidID("") {
		t.Error("expected false for empty ID")
	}
}

func TestWave166_IsValidID_TooLong(t *testing.T) {
	id := strings.Repeat("a", 129)
	if isValidID(id) {
		t.Error("expected false for ID > 128 chars")
	}
}

func TestWave166_IsValidID_ValidChars(t *testing.T) {
	if !isValidID("agent-123_kimi:2") {
		t.Error("expected true for safe ID chars")
	}
}

func TestWave166_IsValidID_UnsafeChars(t *testing.T) {
	if isValidID("agent@host") {
		t.Error("expected false for @ in ID")
	}
}

// ---------------------------------------------------------------------------
// sanitizeText
// ---------------------------------------------------------------------------

func TestWave166_SanitizeText_TrimsSpaces(t *testing.T) {
	if got := sanitizeText("  hello  "); got != "hello" {
		t.Errorf("expected %q, got %q", "hello", got)
	}
}

// ---------------------------------------------------------------------------
// isValidText
// ---------------------------------------------------------------------------

func TestWave166_IsValidText_Empty(t *testing.T) {
	if !isValidText("") {
		t.Error("expected true for empty text")
	}
}

func TestWave166_IsValidText_TooLong(t *testing.T) {
	text := strings.Repeat("a", 2049)
	if isValidText(text) {
		t.Error("expected false for text > 2048 chars")
	}
}

func TestWave166_IsValidText_SafeText(t *testing.T) {
	if !isValidText("Hello, world! [test] (ok)") {
		t.Error("expected true for safe text")
	}
}

func TestWave166_IsValidText_UnsafeText(t *testing.T) {
	if isValidText("<script>alert(1)</script>") {
		t.Error("expected false for unsafe text with HTML")
	}
}

// ---------------------------------------------------------------------------
// writeJSONError
// ---------------------------------------------------------------------------

func TestWave166_WriteJSONError(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSONError(w, 400, "bad request")
	if w.Code != 400 {
		t.Errorf("expected 400, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %q", ct)
	}
	if !strings.Contains(w.Body.String(), "bad request") {
		t.Errorf("expected 'bad request' in body, got %q", w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// isTitleJunk
// ---------------------------------------------------------------------------

func TestWave166_IsTitleJunk_TooShort(t *testing.T) {
	if !isTitleJunk("short") {
		t.Error("expected true for short title (<10 chars)")
	}
}

func TestWave166_IsTitleJunk_ExactJunk(t *testing.T) {
	if !isTitleJunk("fix bug") {
		t.Error("expected true for 'fix bug'")
	}
}

func TestWave166_IsTitleJunk_FeatureSuffix(t *testing.T) {
	if !isTitleJunk("some thing (feature)") {
		t.Error("expected true for title ending in ' (feature)'")
	}
}

func TestWave166_IsTitleJunk_BugSuffix(t *testing.T) {
	if !isTitleJunk("some thing (bug)") {
		t.Error("expected true for title ending in ' (bug)'")
	}
}

func TestWave166_IsTitleJunk_CleanTitle(t *testing.T) {
	if isTitleJunk("Implement coverage wave 166 for validation module") {
		t.Error("expected false for clean title")
	}
}

func TestWave166_IsTitleJunk_ForgeDispatch(t *testing.T) {
	if !isTitleJunk("forge/dispatch") {
		t.Error("expected true for 'forge/dispatch' junk title")
	}
}

// ---------------------------------------------------------------------------
// tokenBudgetFilePath
// ---------------------------------------------------------------------------

func TestWave166_TokenBudgetFilePath_WithRoot(t *testing.T) {
	got := tokenBudgetFilePath("/home/user/FORGE", "prya")
	want := "/home/user/FORGE/.forge/heartbeat/token-budgets-prya.json"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestWave166_TokenBudgetFilePath_EmptyRoot(t *testing.T) {
	got := tokenBudgetFilePath("", "prya")
	if !strings.Contains(got, "token-budgets-prya.json") {
		t.Errorf("expected token-budgets-prya.json in path, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// readTokenBudgetFile
// ---------------------------------------------------------------------------

func TestWave166_ReadTokenBudgetFile_NotExists(t *testing.T) {
	f, err := readTokenBudgetFile("/tmp/nonexistent-budget-wave166.json")
	if err != nil {
		t.Errorf("expected nil error for non-existent file, got: %v", err)
	}
	if f != nil {
		t.Error("expected nil file for non-existent path")
	}
}

func TestWave166_ReadTokenBudgetFile_MalformedJSON(t *testing.T) {
	path := "/tmp/budget-wave166-malformed.json"
	os.WriteFile(path, []byte("not json"), 0644)
	t.Cleanup(func() { os.Remove(path) })

	_, err := readTokenBudgetFile(path)
	if err == nil {
		t.Error("expected error for malformed JSON")
	}
}

// ---------------------------------------------------------------------------
// computeBudgetStatus
// ---------------------------------------------------------------------------

func TestWave166_ComputeBudgetStatus_OK(t *testing.T) {
	if got := computeBudgetStatus(50, 50, 50, nil); got != "OK" {
		t.Errorf("expected OK, got %q", got)
	}
}

func TestWave166_ComputeBudgetStatus_CAUTION(t *testing.T) {
	if got := computeBudgetStatus(85, 50, 50, nil); got != "CAUTION" {
		t.Errorf("expected CAUTION, got %q", got)
	}
}

func TestWave166_ComputeBudgetStatus_RED(t *testing.T) {
	if got := computeBudgetStatus(90, 50, 50, nil); got != "RED" {
		t.Errorf("expected RED, got %q", got)
	}
}

func TestWave166_ComputeBudgetStatus_DEPLETED(t *testing.T) {
	if got := computeBudgetStatus(95, 50, 50, nil); got != "DEPLETED" {
		t.Errorf("expected DEPLETED, got %q", got)
	}
}

func TestWave166_ComputeBudgetStatus_COOLDOWN_Active(t *testing.T) {
	future := time.Now().Add(1 * time.Hour).Format(time.RFC3339)
	if got := computeBudgetStatus(50, 50, 50, &future); got != "COOLDOWN" {
		t.Errorf("expected COOLDOWN, got %q", got)
	}
}

func TestWave166_ComputeBudgetStatus_COOLDOWN_Expired(t *testing.T) {
	past := time.Now().Add(-1 * time.Hour).Format(time.RFC3339)
	// Expired cooldown → falls through to usage thresholds
	if got := computeBudgetStatus(50, 50, 50, &past); got != "OK" {
		t.Errorf("expected OK for expired cooldown, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// eventTypeFor
// ---------------------------------------------------------------------------

func TestWave166_EventTypeFor_NoChange(t *testing.T) {
	if got := eventTypeFor("OK", "OK"); got != "no_change" {
		t.Errorf("expected no_change, got %q", got)
	}
}

func TestWave166_EventTypeFor_CooldownSet(t *testing.T) {
	if got := eventTypeFor("OK", "COOLDOWN"); got != "cooldown_set" {
		t.Errorf("expected cooldown_set, got %q", got)
	}
}

func TestWave166_EventTypeFor_CooldownClear(t *testing.T) {
	if got := eventTypeFor("COOLDOWN", "OK"); got != "cooldown_clear" {
		t.Errorf("expected cooldown_clear, got %q", got)
	}
}

func TestWave166_EventTypeFor_Reset(t *testing.T) {
	if got := eventTypeFor("CAUTION", "OK"); got != "reset" {
		t.Errorf("expected reset, got %q", got)
	}
}

func TestWave166_EventTypeFor_Update(t *testing.T) {
	if got := eventTypeFor("OK", "CAUTION"); got != "update" {
		t.Errorf("expected update, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// nodeIDFromEnv
// ---------------------------------------------------------------------------

func TestWave166_NodeIDFromEnv_EnvSet(t *testing.T) {
	t.Setenv("NODE_ID", "test-node-wave166")
	if got := nodeIDFromEnv(); got != "test-node-wave166" {
		t.Errorf("expected test-node-wave166, got %q", got)
	}
}

func TestWave166_NodeIDFromEnv_Hostname(t *testing.T) {
	t.Setenv("NODE_ID", "")
	got := nodeIDFromEnv()
	if got == "" {
		t.Error("expected non-empty nodeID from hostname")
	}
}

// ---------------------------------------------------------------------------
// ensureTokenBudgetTables (idempotent)
// ---------------------------------------------------------------------------

func TestWave166_EnsureTokenBudgetTables_Idempotent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("first call: %v", err)
	}
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("second call: %v", err)
	}
}

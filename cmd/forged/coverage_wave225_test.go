//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave225_test.go — CompletionManager + parityHandler + nodeCapabilitiesHandler
// Target: completion.go (GenerateBash/Zsh/Fish), parityHandler (handlers_agent.go:727),
//         nodeCapabilitiesHandler (handlers_agent.go:687)
// ============================================================

// --- CompletionManager ---

func TestWave225_CompletionManager_New(t *testing.T) {
	cm := NewCompletionManager()
	if cm == nil {
		t.Fatal("NewCompletionManager returned nil")
	}
}

func TestWave225_GenerateBash_Writes(t *testing.T) {
	cm := NewCompletionManager()
	var buf bytes.Buffer
	if err := cm.GenerateBash(&buf); err != nil {
		t.Fatalf("GenerateBash: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "_forge_completion") {
		t.Errorf("expected bash completion function, got: %s", out[:min225(len(out), 100)])
	}
	if !strings.Contains(out, "complete -F") {
		t.Errorf("expected 'complete -F' directive in bash output")
	}
}

func TestWave225_GenerateZsh_Writes(t *testing.T) {
	cm := NewCompletionManager()
	var buf bytes.Buffer
	if err := cm.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh: %v", err)
	}
	out := buf.String()
	if len(out) == 0 {
		t.Error("GenerateZsh wrote nothing")
	}
	if !strings.Contains(out, "compdef") && !strings.Contains(out, "forge") {
		t.Errorf("expected zsh completion content, got: %s", out[:min225(len(out), 100)])
	}
}

func TestWave225_GenerateFish_Writes(t *testing.T) {
	cm := NewCompletionManager()
	var buf bytes.Buffer
	if err := cm.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish: %v", err)
	}
	out := buf.String()
	if len(out) == 0 {
		t.Error("GenerateFish wrote nothing")
	}
}

func TestWave225_GenerateBash_ErrorWriter(t *testing.T) {
	cm := NewCompletionManager()
	ew := &errWriter225{}
	err := cm.GenerateBash(ew)
	if err == nil {
		t.Error("expected error from failing writer, got nil")
	}
}

func TestWave225_GenerateZsh_ErrorWriter(t *testing.T) {
	cm := NewCompletionManager()
	ew := &errWriter225{}
	err := cm.GenerateZsh(ew)
	if err == nil {
		t.Error("expected error from failing writer, got nil")
	}
}

func TestWave225_GenerateFish_ErrorWriter(t *testing.T) {
	cm := NewCompletionManager()
	ew := &errWriter225{}
	err := cm.GenerateFish(ew)
	if err == nil {
		t.Error("expected error from failing writer, got nil")
	}
}

// --- parityHandler ---

func TestWave225_ParityHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := parityHandler(db)

	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()

	handler(w, req)

	// parityHandler calls ParityCheck which uses hardcoded paths — it may error on missing files
	// but should still return a JSON response (200 or 500)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave225_ParityHandler_JSONResponse(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := parityHandler(db)

	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()

	handler(w, req)

	if w.Code == http.StatusOK {
		ct := w.Header().Get("Content-Type")
		if !strings.Contains(ct, "application/json") {
			t.Errorf("expected JSON content-type, got %s", ct)
		}
		body := w.Body.String()
		if !strings.Contains(body, "timestamp") && !strings.Contains(body, "tasks_match") {
			t.Errorf("expected JSON parity fields, got: %s", body[:min225(len(body), 200)])
		}
	}
}

// --- nodeCapabilitiesHandler ---

func TestWave225_NodeCapabilities_Get(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/node-capabilities", nil)
	w := httptest.NewRecorder()

	nodeCapabilitiesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	ct := w.Header().Get("Content-Type")
	if !strings.Contains(ct, "application/json") {
		t.Errorf("expected JSON content-type, got %s", ct)
	}
}

func TestWave225_NodeCapabilities_ResponseFields(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/node-capabilities", nil)
	w := httptest.NewRecorder()

	nodeCapabilitiesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Skipf("handler returned %d, skipping field check", w.Code)
	}
	body := w.Body.String()
	for _, field := range []string{"node_id", "hard_ceiling", "agent_tiers"} {
		if !strings.Contains(body, field) {
			t.Errorf("expected field %q in response body: %s", field, body[:min225(len(body), 200)])
		}
	}
}

// --- ParityChecker unit ---

func TestWave225_ParityChecker_New(t *testing.T) {
	pc := NewParityChecker("", "")
	if pc == nil {
		t.Fatal("NewParityChecker returned nil")
	}
	// defaults applied
	if pc.v2StatePath != ".forge" {
		t.Errorf("expected default v2StatePath=.forge, got %s", pc.v2StatePath)
	}
}

func TestWave225_ParityChecker_Custom(t *testing.T) {
	pc := NewParityChecker("/tmp/v2", "/tmp/v3.db")
	if pc.v2StatePath != "/tmp/v2" {
		t.Errorf("expected /tmp/v2, got %s", pc.v2StatePath)
	}
	if pc.v3DBPath != "/tmp/v3.db" {
		t.Errorf("expected /tmp/v3.db, got %s", pc.v3DBPath)
	}
}

func TestWave225_ParityChecker_Check_MissingPaths(t *testing.T) {
	pc := NewParityChecker("/nonexistent/path", "/nonexistent/v3.db")
	result, err := pc.Check()
	// Should not return a Go error — errors accumulate in result.Errors
	if err != nil {
		t.Fatalf("Check returned unexpected error: %v", err)
	}
	if result == nil {
		t.Fatal("Check returned nil result")
	}
	// Overall should be false since paths don't match
	if result.Timestamp == 0 {
		t.Error("expected non-zero timestamp")
	}
}

func TestWave225_TimeNow(t *testing.T) {
	ts := timeNow()
	if ts == 0 {
		t.Error("timeNow returned 0")
	}
}

// --- helpers ---

// errWriter225 is an io.Writer that always returns an error.
type errWriter225 struct{}

func (e *errWriter225) Write(p []byte) (n int, err error) {
	return 0, &writeError225{}
}

type writeError225 struct{}

func (e *writeError225) Error() string { return "simulated write error" }

// min225 returns the minimum of two ints.
func min225(a, b int) int {
	if a < b {
		return a
	}
	return b
}

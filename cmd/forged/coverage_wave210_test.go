//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 210: token_budget.go + handoffs.go pure functions
// Targets:
//   tokenBudgetFilePath     (token_budget.go:37) — empty forgeRoot / normal
//   readTokenBudgetFile     (token_budget.go:47) — not found / valid JSON
//   writeTokenBudgetFile    (token_budget.go:63) — success
//   computeBudgetStatus     (token_budget.go:120)— OK/CAUTION/RED/DEPLETED/COOLDOWN
//   eventTypeFor            (token_budget.go:252)— same/cooldown/reset/update
//   nodeIDFromEnv           (token_budget.go:312)— non-empty
//   NewHandoffStore         (handoffs.go:62)   — non-nil
//   NewHandoffService       (handoffs.go:249)  — non-nil
//   parseHandoffTime        (handoffs.go:85)   — empty / valid
//   HandoffService.Create   (handoffs.go:254)  — success
//   HandoffService.List via store (handoffs.go:168) — empty
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// tokenBudgetFilePath
// ---------------------------------------------------------------------------

func TestWave210_TokenBudgetFilePath_Normal(t *testing.T) {
	path := tokenBudgetFilePath("/tmp/forge", "prya")
	if path == "" {
		t.Error("expected non-empty path")
	}
	if filepath.Base(path) != "token-budgets-prya.json" {
		t.Errorf("unexpected filename: %q", filepath.Base(path))
	}
}

func TestWave210_TokenBudgetFilePath_EmptyForgeRoot(t *testing.T) {
	path := tokenBudgetFilePath("", "prya")
	if path == "" {
		t.Error("expected non-empty path for empty forgeRoot")
	}
}

// ---------------------------------------------------------------------------
// readTokenBudgetFile
// ---------------------------------------------------------------------------

func TestWave210_ReadTokenBudgetFile_NotFound(t *testing.T) {
	f, err := readTokenBudgetFile("/nonexistent/path/budget.json")
	if err != nil {
		t.Errorf("expected nil error for not-found, got %v", err)
	}
	if f != nil {
		t.Error("expected nil budget for not-found file")
	}
}

func TestWave210_ReadTokenBudgetFile_ValidJSON(t *testing.T) {
	tmpFile := filepath.Join(t.TempDir(), "budget.json")
	if err := os.WriteFile(tmpFile, []byte(`{"providers":{},"updated_at":""}`), 0644); err != nil {
		t.Fatal(err)
	}

	f, err := readTokenBudgetFile(tmpFile)
	if err != nil {
		t.Errorf("readTokenBudgetFile: %v", err)
	}
	_ = f
}

// ---------------------------------------------------------------------------
// writeTokenBudgetFile
// ---------------------------------------------------------------------------

func TestWave210_WriteTokenBudgetFile_Success(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "budget.json")

	budget := &tokenBudgetFile{}
	if err := writeTokenBudgetFile(path, budget); err != nil {
		t.Errorf("writeTokenBudgetFile: %v", err)
	}

	// File should exist now
	if _, err := os.Stat(path); err != nil {
		t.Errorf("expected file to exist after write: %v", err)
	}
}

// ---------------------------------------------------------------------------
// computeBudgetStatus
// ---------------------------------------------------------------------------

func TestWave210_ComputeBudgetStatus_OK(t *testing.T) {
	got := computeBudgetStatus(50, 60, 40, nil)
	if got != "OK" {
		t.Errorf("expected OK, got %q", got)
	}
}

func TestWave210_ComputeBudgetStatus_Caution(t *testing.T) {
	got := computeBudgetStatus(85, 70, 50, nil)
	if got != "CAUTION" {
		t.Errorf("expected CAUTION, got %q", got)
	}
}

func TestWave210_ComputeBudgetStatus_Red(t *testing.T) {
	got := computeBudgetStatus(90, 70, 50, nil)
	if got != "RED" {
		t.Errorf("expected RED, got %q", got)
	}
}

func TestWave210_ComputeBudgetStatus_Depleted(t *testing.T) {
	got := computeBudgetStatus(95, 70, 50, nil)
	if got != "DEPLETED" {
		t.Errorf("expected DEPLETED, got %q", got)
	}
}

func TestWave210_ComputeBudgetStatus_Cooldown(t *testing.T) {
	future := time.Now().Add(1 * time.Hour).UTC().Format(time.RFC3339)
	got := computeBudgetStatus(50, 50, 50, &future)
	if got != "COOLDOWN" {
		t.Errorf("expected COOLDOWN, got %q", got)
	}
}

func TestWave210_ComputeBudgetStatus_ExpiredCooldown(t *testing.T) {
	past := time.Now().Add(-1 * time.Hour).UTC().Format(time.RFC3339)
	got := computeBudgetStatus(50, 50, 50, &past)
	if got != "OK" {
		t.Errorf("expected OK for expired cooldown, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// eventTypeFor
// ---------------------------------------------------------------------------

func TestWave210_EventTypeFor_Same(t *testing.T) {
	if got := eventTypeFor("OK", "OK"); got != "no_change" {
		t.Errorf("expected 'no_change', got %q", got)
	}
}

func TestWave210_EventTypeFor_CooldownSet(t *testing.T) {
	if got := eventTypeFor("OK", "COOLDOWN"); got != "cooldown_set" {
		t.Errorf("expected 'cooldown_set', got %q", got)
	}
}

func TestWave210_EventTypeFor_CooldownClear(t *testing.T) {
	if got := eventTypeFor("COOLDOWN", "OK"); got != "cooldown_clear" {
		t.Errorf("expected 'cooldown_clear', got %q", got)
	}
}

func TestWave210_EventTypeFor_Reset(t *testing.T) {
	if got := eventTypeFor("RED", "OK"); got != "reset" {
		t.Errorf("expected 'reset', got %q", got)
	}
}

func TestWave210_EventTypeFor_Update(t *testing.T) {
	if got := eventTypeFor("OK", "RED"); got != "update" {
		t.Errorf("expected 'update', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// nodeIDFromEnv
// ---------------------------------------------------------------------------

func TestWave210_NodeIDFromEnv_NonEmpty(t *testing.T) {
	id := nodeIDFromEnv()
	if id == "" {
		t.Error("expected non-empty node ID")
	}
}

// ---------------------------------------------------------------------------
// NewHandoffStore
// ---------------------------------------------------------------------------

func TestWave210_NewHandoffStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	if store == nil {
		t.Error("expected non-nil HandoffStore")
	}
}

// ---------------------------------------------------------------------------
// NewHandoffService
// ---------------------------------------------------------------------------

func TestWave210_NewHandoffService_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	if svc == nil {
		t.Error("expected non-nil HandoffService")
	}
}

// ---------------------------------------------------------------------------
// parseHandoffTime
// ---------------------------------------------------------------------------

func TestWave210_ParseHandoffTime_Empty(t *testing.T) {
	// Empty string → zero time
	got := parseHandoffTime("")
	if !got.IsZero() {
		t.Errorf("expected zero time for empty string, got %v", got)
	}
}

func TestWave210_ParseHandoffTime_Valid(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	s := now.Format(time.RFC3339)
	got := parseHandoffTime(s)
	if got.IsZero() {
		t.Errorf("expected non-zero time for valid RFC3339 string %q", s)
	}
}

// ---------------------------------------------------------------------------
// HandoffService.Create
// ---------------------------------------------------------------------------

func TestWave210_HandoffService_Create(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)

	h, err := svc.Create(context.Background(), "agent-a", "agent-b", "testing wave 210", []string{"file.go"}, "normal")
	if err != nil {
		t.Errorf("HandoffService.Create: %v", err)
	}
	if h.ID == "" {
		t.Error("expected non-empty handoff ID")
	}
}

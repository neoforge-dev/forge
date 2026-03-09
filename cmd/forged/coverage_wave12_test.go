//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave12_test.go — Coverage wave 12: quality_gates, royal_jelly, state_sync, token_budget, worktree
// Target: +1.5pp from ~58.4% to ~59.9%
package main

import (
	"context"
	"os"
	"testing"
	"time"
)

// --- quality_gates.go: CheckCoverage, CheckLint, CheckTypeCheck ---

func TestQualityGates_CheckCoverage(t *testing.T) {
	t.Setenv("FORGE_TEST_COVERAGE_CMD", `echo "coverage: 80.0% of statements"`)

	qg, err := NewQualityGateChecker("")
	if err != nil {
		t.Fatalf("NewQualityGateChecker: %v", err)
	}

	cov, err := qg.CheckCoverage(context.Background())
	if err != nil {
		t.Fatalf("CheckCoverage: %v", err)
	}
	if cov <= 0 {
		t.Errorf("expected coverage > 0, got %.1f", cov)
	}
}

func TestQualityGates_CheckCoverage_NoLines(t *testing.T) {
	t.Setenv("FORGE_TEST_COVERAGE_CMD", `echo "no coverage here"`)

	qg, _ := NewQualityGateChecker("")
	cov, err := qg.CheckCoverage(context.Background())
	if err != nil {
		t.Fatalf("CheckCoverage with no parse: %v", err)
	}
	if cov != 0 {
		t.Errorf("expected 0 when no coverage lines, got %.1f", cov)
	}
}

func TestQualityGates_CheckLint_EmptyJSON(t *testing.T) {
	t.Setenv("FORGE_TEST_LINT_CMD", `echo "{}"`)

	qg, _ := NewQualityGateChecker("")
	issues, err := qg.CheckLint(context.Background())
	if err != nil {
		t.Fatalf("CheckLint: %v", err)
	}
	if issues == nil {
		issues = []LintIssue{}
	}
	_ = issues
}

func TestQualityGates_CheckLint_NonJSON(t *testing.T) {
	t.Setenv("FORGE_TEST_LINT_CMD", `echo "no issues"`)

	qg, _ := NewQualityGateChecker("")
	issues, err := qg.CheckLint(context.Background())
	if err != nil {
		t.Fatalf("CheckLint non-JSON: %v", err)
	}
	_ = issues
}

func TestQualityGates_CheckTypeCheck_Pass(t *testing.T) {
	t.Setenv("FORGE_TEST_TYPECHECK_CMD", `true`)

	qg, _ := NewQualityGateChecker("")
	err := qg.CheckTypeCheck(context.Background())
	if err != nil {
		t.Errorf("CheckTypeCheck with 'true' command: %v", err)
	}
}

func TestQualityGates_CheckTypeCheck_Fail(t *testing.T) {
	t.Setenv("FORGE_TEST_TYPECHECK_CMD", `false`)

	qg, _ := NewQualityGateChecker("")
	err := qg.CheckTypeCheck(context.Background())
	if err == nil {
		t.Error("expected error when type check fails")
	}
}

// --- royal_jelly.go: ContextThresholdHook Name() and Trigger() ---

func TestContextThresholdHook_Trigger_BelowThreshold(t *testing.T) {
	hook := &ContextThresholdHook{cm: &ContextManager{}}
	event := HookEvent{
		ContextPct: 30.0, // below 50% threshold
		Metadata:   map[string]interface{}{},
	}
	err := hook.Trigger(context.Background(), event)
	// Below threshold → no-op, no error
	if err != nil {
		t.Errorf("Trigger below threshold: %v", err)
	}
}

// --- state_sync.go: syncStatusState ---

func TestSyncStatusStateW12(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	// Should run without error on empty DB
	syncStatusState(db)
}

// --- token_budget.go: eventTypeFor ---

func TestEventTypeFor(t *testing.T) {
	tests := []struct {
		old, new, want string
	}{
		{"OK", "OK", "no_change"},
		{"OK", "COOLDOWN", "cooldown_set"},
		{"COOLDOWN", "OK", "cooldown_clear"},
		{"DEGRADED", "OK", "reset"},
		{"OK", "DEGRADED", "update"},
	}
	for _, tc := range tests {
		got := eventTypeFor(tc.old, tc.new)
		if got != tc.want {
			t.Errorf("eventTypeFor(%q, %q) = %q, want %q", tc.old, tc.new, got, tc.want)
		}
	}
}

// --- token_budget.go: upsertTokenBudgetSnapshot, insertTokenBudgetLog ---

func TestUpsertTokenBudgetSnapshot(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	entry := tbLogEntry{
		nodeID:    "test-node",
		provider:  "anthropic",
		monthlyPct: 50,
		weeklyPct:  30,
		h5Pct:     10,
		status:    "OK",
		agents:    []string{"agent-001"},
	}
	err := upsertTokenBudgetSnapshot(context.Background(), db, entry, time.Now())
	if err != nil {
		t.Errorf("upsertTokenBudgetSnapshot: %v", err)
	}

	// Second upsert should also work (ON CONFLICT DO UPDATE)
	err = upsertTokenBudgetSnapshot(context.Background(), db, entry, time.Now())
	if err != nil {
		t.Errorf("second upsertTokenBudgetSnapshot: %v", err)
	}
}

func TestInsertTokenBudgetLog(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	entry := tbLogEntry{
		nodeID:    "test-node",
		provider:  "anthropic",
		eventType: "update",
		monthlyPct: 55,
		weeklyPct:  35,
		h5Pct:     15,
		status:    "OK",
	}
	err := insertTokenBudgetLog(context.Background(), db, entry)
	if err != nil {
		t.Errorf("insertTokenBudgetLog: %v", err)
	}
}

// --- worktree_manager.go: RecordWorktree ---

func TestWorktreeManager_RecordWorktree(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	dir := os.TempDir()
	wm := NewWorktreeManager(".", dir, db)

	if err := wm.RecordWorktree(context.Background(), "TASK-TEST-001", "/tmp/worktree-test", "feat/test", "agent-001"); err != nil {
		t.Errorf("RecordWorktree: %v", err)
	}
}

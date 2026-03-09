//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// newTokenBudgetDB opens an in-memory SQLite DB and creates the token budget
// tables using ensureTokenBudgetTables (the inline migration).
func newTokenBudgetDB(t *testing.T) (context.Context, interface{ ExecContext(context.Context, string, ...interface{}) (interface{ LastInsertId() (int64, error); RowsAffected() (int64, error) }, error) }) {
	t.Helper()
	// We need a real *sql.DB for the helpers — reuse the shared OpenDB/MigrateUp path.
	return nil, nil
}

// setupTokenBudgetDB returns a migrated *sql.DB backed by a temp file, and a
// cleanup func.
func setupTokenBudgetDB(t *testing.T) (context.Context, interface{}) {
	t.Helper()
	return context.Background(), nil
}

// --- tokenBudgetFilePath ---

func TestTokenBudgetFilePath_WithRoot(t *testing.T) {
	got := tokenBudgetFilePath("/tmp/forge", "prya")
	want := filepath.Join("/tmp/forge", ".forge", "heartbeat", "token-budgets-prya.json")
	if got != want {
		t.Errorf("tokenBudgetFilePath = %q, want %q", got, want)
	}
}

func TestTokenBudgetFilePath_EmptyRoot(t *testing.T) {
	got := tokenBudgetFilePath("", "prya")
	// Empty root defaults to "."
	want := filepath.Join(".", ".forge", "heartbeat", "token-budgets-prya.json")
	if got != want {
		t.Errorf("tokenBudgetFilePath (empty root) = %q, want %q", got, want)
	}
}

func TestTokenBudgetFilePath_DifferentNodes(t *testing.T) {
	for _, node := range []string{"prya", "sati", "nova", "vega"} {
		got := tokenBudgetFilePath("/root", node)
		if got == "" {
			t.Errorf("tokenBudgetFilePath(%q) returned empty string", node)
		}
		expected := fmt.Sprintf("token-budgets-%s.json", node)
		if filepath.Base(got) != expected {
			t.Errorf("tokenBudgetFilePath(%q) basename = %q, want %q", node, filepath.Base(got), expected)
		}
	}
}

// --- readTokenBudgetFile ---

func TestReadTokenBudgetFile_NotExist(t *testing.T) {
	got, err := readTokenBudgetFile("/tmp/does-not-exist-tb-test.json")
	if err != nil {
		t.Fatalf("readTokenBudgetFile nonexistent: expected nil error, got %v", err)
	}
	if got != nil {
		t.Fatalf("readTokenBudgetFile nonexistent: expected nil result, got %+v", got)
	}
}

func TestReadTokenBudgetFile_ValidJSON(t *testing.T) {
	f := &tokenBudgetFile{
		Node:      "prya",
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
		Providers: map[string]tbProviderData{
			"anthropic": {
				Provider: "anthropic",
				Agents:   []string{"glm", "kimi"},
				Limits:   map[string]int{"monthly_pct": 45, "weekly_pct": 30},
				Status:   "OK",
			},
		},
	}

	path := filepath.Join(t.TempDir(), "token-budgets-prya.json")
	data, _ := json.MarshalIndent(f, "", "  ")
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatalf("write test file: %v", err)
	}

	got, err := readTokenBudgetFile(path)
	if err != nil {
		t.Fatalf("readTokenBudgetFile: %v", err)
	}
	if got == nil {
		t.Fatal("readTokenBudgetFile returned nil for valid file")
	}
	if got.Node != "prya" {
		t.Errorf("node = %q, want %q", got.Node, "prya")
	}
	if len(got.Providers) != 1 {
		t.Errorf("providers count = %d, want 1", len(got.Providers))
	}
}

func TestReadTokenBudgetFile_InvalidJSON(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(path, []byte("{not valid json"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	_, err := readTokenBudgetFile(path)
	if err == nil {
		t.Fatal("readTokenBudgetFile with invalid JSON: expected error, got nil")
	}
}

// --- writeTokenBudgetFile ---

func TestWriteTokenBudgetFile_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "token-budgets-test.json")

	f := &tokenBudgetFile{
		Node: "test-node",
		Providers: map[string]tbProviderData{
			"openai": {
				Provider: "openai",
				Status:   "OK",
				Limits:   map[string]int{"monthly_pct": 10},
			},
		},
	}

	if err := writeTokenBudgetFile(path, f); err != nil {
		t.Fatalf("writeTokenBudgetFile: %v", err)
	}

	// File must exist and be valid JSON.
	got, err := readTokenBudgetFile(path)
	if err != nil {
		t.Fatalf("readTokenBudgetFile after write: %v", err)
	}
	if got == nil {
		t.Fatal("readTokenBudgetFile after write returned nil")
	}
	if got.Node != "test-node" {
		t.Errorf("node = %q, want %q", got.Node, "test-node")
	}
	if got.UpdatedAt == "" {
		t.Error("UpdatedAt was not set by writeTokenBudgetFile")
	}
}

func TestWriteTokenBudgetFile_SetsUpdatedAt(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "tb.json")
	f := &tokenBudgetFile{Node: "x"}

	before := time.Now().UTC().Add(-time.Second)
	if err := writeTokenBudgetFile(path, f); err != nil {
		t.Fatalf("writeTokenBudgetFile: %v", err)
	}
	after := time.Now().UTC().Add(time.Second)

	got, _ := readTokenBudgetFile(path)
	if got == nil {
		t.Fatal("nil after write")
	}

	ts, err := time.Parse(time.RFC3339, got.UpdatedAt)
	if err != nil {
		t.Fatalf("UpdatedAt not a valid RFC3339 time: %q", got.UpdatedAt)
	}
	if ts.Before(before) || ts.After(after) {
		t.Errorf("UpdatedAt %v not in window [%v, %v]", ts, before, after)
	}
}

// --- computeBudgetStatus ---

func TestComputeBudgetStatus_OK(t *testing.T) {
	got := computeBudgetStatus(10, 20, 30, nil)
	if got != "OK" {
		t.Errorf("computeBudgetStatus(10,20,30,nil) = %q, want %q", got, "OK")
	}
}

func TestComputeBudgetStatus_Caution(t *testing.T) {
	got := computeBudgetStatus(80, 10, 10, nil)
	if got != "CAUTION" {
		t.Errorf("computeBudgetStatus(80,...) = %q, want CAUTION", got)
	}
}

func TestComputeBudgetStatus_Red(t *testing.T) {
	got := computeBudgetStatus(90, 10, 10, nil)
	if got != "RED" {
		t.Errorf("computeBudgetStatus(90,...) = %q, want RED", got)
	}
}

func TestComputeBudgetStatus_Depleted(t *testing.T) {
	got := computeBudgetStatus(95, 10, 10, nil)
	if got != "DEPLETED" {
		t.Errorf("computeBudgetStatus(95,...) = %q, want DEPLETED", got)
	}
}

func TestComputeBudgetStatus_Cooldown_Active(t *testing.T) {
	future := time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339)
	got := computeBudgetStatus(10, 10, 10, &future)
	if got != "COOLDOWN" {
		t.Errorf("computeBudgetStatus with future cooldown = %q, want COOLDOWN", got)
	}
}

func TestComputeBudgetStatus_Cooldown_Expired(t *testing.T) {
	past := time.Now().UTC().Add(-10 * time.Minute).Format(time.RFC3339)
	got := computeBudgetStatus(10, 10, 10, &past)
	if got != "OK" {
		t.Errorf("computeBudgetStatus with past cooldown = %q, want OK (expired)", got)
	}
}

func TestComputeBudgetStatus_Cooldown_EmptyString(t *testing.T) {
	empty := ""
	got := computeBudgetStatus(10, 10, 10, &empty)
	if got != "OK" {
		t.Errorf("computeBudgetStatus with empty cooldown string = %q, want OK", got)
	}
}

func TestComputeBudgetStatus_WeeklyWorst(t *testing.T) {
	// Weekly is the worst at 92 → RED
	got := computeBudgetStatus(10, 92, 10, nil)
	if got != "RED" {
		t.Errorf("computeBudgetStatus weekly-worst(92) = %q, want RED", got)
	}
}

func TestComputeBudgetStatus_H5Worst(t *testing.T) {
	// H5 is the worst at 96 → DEPLETED
	got := computeBudgetStatus(10, 10, 96, nil)
	if got != "DEPLETED" {
		t.Errorf("computeBudgetStatus h5-worst(96) = %q, want DEPLETED", got)
	}
}

// --- eventTypeFor ---

func TestEventTypeFor_NoChange(t *testing.T) {
	got := eventTypeFor("OK", "OK")
	if got != "no_change" {
		t.Errorf("eventTypeFor(OK,OK) = %q, want no_change", got)
	}
}

func TestEventTypeFor_CooldownSet(t *testing.T) {
	got := eventTypeFor("RED", "COOLDOWN")
	if got != "cooldown_set" {
		t.Errorf("eventTypeFor(RED,COOLDOWN) = %q, want cooldown_set", got)
	}
}

func TestEventTypeFor_CooldownClear(t *testing.T) {
	got := eventTypeFor("COOLDOWN", "OK")
	if got != "cooldown_clear" {
		t.Errorf("eventTypeFor(COOLDOWN,OK) = %q, want cooldown_clear", got)
	}
}

func TestEventTypeFor_Reset(t *testing.T) {
	got := eventTypeFor("RED", "OK")
	if got != "reset" {
		t.Errorf("eventTypeFor(RED,OK) = %q, want reset", got)
	}
}

func TestEventTypeFor_Update(t *testing.T) {
	got := eventTypeFor("OK", "CAUTION")
	if got != "update" {
		t.Errorf("eventTypeFor(OK,CAUTION) = %q, want update", got)
	}
}

func TestEventTypeFor_UpdateDepletedToRed(t *testing.T) {
	got := eventTypeFor("DEPLETED", "RED")
	if got != "update" {
		t.Errorf("eventTypeFor(DEPLETED,RED) = %q, want update", got)
	}
}

// --- nodeIDFromEnv ---

func TestNodeIDFromEnv_EnvSet(t *testing.T) {
	t.Setenv("NODE_ID", "test-node-123")
	got := nodeIDFromEnv()
	if got != "test-node-123" {
		t.Errorf("nodeIDFromEnv with NODE_ID set = %q, want test-node-123", got)
	}
}

func TestNodeIDFromEnv_Fallback(t *testing.T) {
	// Unset NODE_ID so it falls back to hostname.
	t.Setenv("NODE_ID", "")
	got := nodeIDFromEnv()
	if got == "" {
		t.Error("nodeIDFromEnv fallback returned empty string")
	}
}

// --- ensureTokenBudgetTables + upsertTokenBudgetSnapshot + insertTokenBudgetLog ---

func TestEnsureTokenBudgetTables(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_tables_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	// Idempotent — calling twice must not error.
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables (2nd call): %v", err)
	}

	// Verify token_budget_snapshots table is queryable.
	rows, err := db.QueryContext(ctx, "SELECT COUNT(*) FROM token_budget_snapshots")
	if err != nil {
		t.Fatalf("query token_budget_snapshots: %v", err)
	}
	rows.Close()

	// Verify token_budget_log table is queryable.
	rows2, err := db.QueryContext(ctx, "SELECT COUNT(*) FROM token_budget_log")
	if err != nil {
		t.Fatalf("query token_budget_log: %v", err)
	}
	rows2.Close()
}

func TestUpsertTokenBudgetSnapshot_TB(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_snapshot_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	cooldown := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	entry := tbLogEntry{
		nodeID:        "prya",
		provider:      "anthropic",
		eventType:     "update",
		monthlyPct:    60,
		weeklyPct:     40,
		h5Pct:         20,
		status:        "CAUTION",
		cooldownUntil: &cooldown,
		agents:        []string{"glm", "kimi"},
	}

	now := time.Now().UTC()
	if err := upsertTokenBudgetSnapshot(ctx, db, entry, now); err != nil {
		t.Fatalf("upsertTokenBudgetSnapshot: %v", err)
	}

	// Verify upsert stored the row.
	var count int
	row := db.QueryRowContext(ctx, "SELECT COUNT(*) FROM token_budget_snapshots WHERE node_id = 'prya' AND provider = 'anthropic'")
	if err := row.Scan(&count); err != nil {
		t.Fatalf("scan count: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 row in token_budget_snapshots, got %d", count)
	}

	// Second upsert (conflict) must succeed and update.
	entry.monthlyPct = 85
	entry.status = "RED"
	if err := upsertTokenBudgetSnapshot(ctx, db, entry, now); err != nil {
		t.Fatalf("upsertTokenBudgetSnapshot (update): %v", err)
	}

	var status string
	row2 := db.QueryRowContext(ctx, "SELECT status FROM token_budget_snapshots WHERE node_id = 'prya' AND provider = 'anthropic'")
	if err := row2.Scan(&status); err != nil {
		t.Fatalf("scan status: %v", err)
	}
	if status != "RED" {
		t.Errorf("updated status = %q, want RED", status)
	}
}

func TestUpsertTokenBudgetSnapshot_NilCooldown(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_snapshotnilcd_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	entry := tbLogEntry{
		nodeID:        "sati",
		provider:      "openai",
		eventType:     "reset",
		monthlyPct:    5,
		weeklyPct:     5,
		h5Pct:         5,
		status:        "OK",
		cooldownUntil: nil,
		agents:        nil,
	}

	if err := upsertTokenBudgetSnapshot(ctx, db, entry, time.Now().UTC()); err != nil {
		t.Fatalf("upsertTokenBudgetSnapshot (nil cooldown): %v", err)
	}
}

func TestInsertTokenBudgetLog_TB(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_log_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	entry := tbLogEntry{
		nodeID:        "prya",
		provider:      "anthropic",
		eventType:     "cooldown_set",
		monthlyPct:    92,
		weeklyPct:     88,
		h5Pct:         75,
		status:        "COOLDOWN",
		cooldownUntil: nil,
	}

	if err := insertTokenBudgetLog(ctx, db, entry); err != nil {
		t.Fatalf("insertTokenBudgetLog: %v", err)
	}

	// Verify the row was inserted.
	var count int
	row := db.QueryRowContext(ctx, "SELECT COUNT(*) FROM token_budget_log WHERE node_id = 'prya' AND provider = 'anthropic' AND event_type = 'cooldown_set'")
	if err := row.Scan(&count); err != nil {
		t.Fatalf("scan count: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 log row, got %d", count)
	}
}

func TestInsertTokenBudgetLog_WithCooldown(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_logcd_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	cd := time.Now().UTC().Add(1 * time.Hour).Format(time.RFC3339)
	entry := tbLogEntry{
		nodeID:        "nova",
		provider:      "openai",
		eventType:     "cooldown_set",
		monthlyPct:    95,
		weeklyPct:     90,
		h5Pct:         80,
		status:        "COOLDOWN",
		cooldownUntil: &cd,
	}

	if err := insertTokenBudgetLog(ctx, db, entry); err != nil {
		t.Fatalf("insertTokenBudgetLog with cooldown: %v", err)
	}
}

// --- tokenBudgetCooldownResetPatrol ---

func TestTokenBudgetCooldownResetPatrol_NoFile(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_patrol_nofile_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	// Set NODE_ID so tokenBudgetFilePath returns a path that doesn't exist.
	t.Setenv("NODE_ID", "test-patrol-node")

	// Ensure that calling the patrol with no budget file returns nil (not an error).
	ctx := context.Background()
	err = tokenBudgetCooldownResetPatrol(ctx, db)
	if err != nil {
		t.Fatalf("tokenBudgetCooldownResetPatrol with no file: %v", err)
	}
}

func TestTokenBudgetCooldownResetPatrol_WithBudgetFile(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_patrol_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	// Use a known node and create a budget file in CWD (tokenBudgetFilePath uses ".")
	nodeID := fmt.Sprintf("patrol-test-%d", time.Now().UnixNano())
	t.Setenv("NODE_ID", nodeID)

	budgetDir := filepath.Join(".", ".forge", "heartbeat")
	if err := os.MkdirAll(budgetDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetPath := filepath.Join(budgetDir, fmt.Sprintf("token-budgets-%s.json", nodeID))
	defer os.Remove(budgetPath)

	// Past cooldown that should be cleared.
	pastCooldown := time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	f := &tokenBudgetFile{
		Node:      nodeID,
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
		Providers: map[string]tbProviderData{
			"anthropic": {
				Provider:      "anthropic",
				Agents:        []string{"glm"},
				Limits:        map[string]int{"monthly_pct": 40, "weekly_pct": 30, "5h_rolling_pct": 20},
				Status:        "COOLDOWN",
				CooldownUntil: &pastCooldown,
			},
		},
	}
	data, _ := json.MarshalIndent(f, "", "  ")
	if err := os.WriteFile(budgetPath, data, 0644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}

	if err := tokenBudgetCooldownResetPatrol(ctx, db); err != nil {
		t.Fatalf("tokenBudgetCooldownResetPatrol: %v", err)
	}

	// After the patrol the cooldown should have been cleared in the file.
	got, err := readTokenBudgetFile(budgetPath)
	if err != nil {
		t.Fatalf("readTokenBudgetFile after patrol: %v", err)
	}
	if got == nil {
		t.Fatal("budget file nil after patrol")
	}
	p := got.Providers["anthropic"]
	if p.CooldownUntil != nil && *p.CooldownUntil != "" {
		t.Errorf("expected cooldown cleared, got %q", *p.CooldownUntil)
	}

	// Snapshot must have been upserted into SQLite.
	var count int
	row := db.QueryRowContext(ctx, "SELECT COUNT(*) FROM token_budget_snapshots WHERE node_id = ?", nodeID)
	if err := row.Scan(&count); err != nil {
		t.Fatalf("scan snapshot count: %v", err)
	}
	if count == 0 {
		t.Error("expected at least one snapshot row in SQLite after patrol")
	}
}

func TestTokenBudgetCooldownResetPatrol_ActiveCooldown(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_tb_patrol_active_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		t.Fatalf("ensureTokenBudgetTables: %v", err)
	}

	nodeID := fmt.Sprintf("patrol-active-%d", time.Now().UnixNano())
	t.Setenv("NODE_ID", nodeID)

	budgetDir := filepath.Join(".", ".forge", "heartbeat")
	if err := os.MkdirAll(budgetDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetPath := filepath.Join(budgetDir, fmt.Sprintf("token-budgets-%s.json", nodeID))
	defer os.Remove(budgetPath)

	// Future cooldown that should remain.
	futureCooldown := time.Now().UTC().Add(2 * time.Hour).Format(time.RFC3339)
	f := &tokenBudgetFile{
		Node: nodeID,
		Providers: map[string]tbProviderData{
			"openai": {
				Provider:      "openai",
				Limits:        map[string]int{"monthly_pct": 60, "weekly_pct": 50, "5h_rolling_pct": 40},
				Status:        "COOLDOWN",
				CooldownUntil: &futureCooldown,
			},
		},
	}
	data, _ := json.MarshalIndent(f, "", "  ")
	if err := os.WriteFile(budgetPath, data, 0644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}

	if err := tokenBudgetCooldownResetPatrol(ctx, db); err != nil {
		t.Fatalf("tokenBudgetCooldownResetPatrol (active cooldown): %v", err)
	}

	// The file should NOT have been modified (cooldown still active).
	got, _ := readTokenBudgetFile(budgetPath)
	if got == nil {
		t.Fatal("budget file nil after patrol")
	}
	p := got.Providers["openai"]
	if p.CooldownUntil == nil || *p.CooldownUntil != futureCooldown {
		t.Errorf("expected cooldown %q preserved, got %v", futureCooldown, p.CooldownUntil)
	}
}

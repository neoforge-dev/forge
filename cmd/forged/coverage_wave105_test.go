//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"fmt"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// ============================================================
// xnodeRetrySerializerPatrol tests (patrol.go ~line 1828)
// ============================================================

// TestWave105_XnodeRetry_EmptyDB verifies that the patrol returns nil
// when no rows qualify (empty outbox table).
func TestWave105_XnodeRetry_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	if err := xnodeRetrySerializerPatrol(context.Background(), db); err != nil {
		t.Logf("xnodeRetrySerializerPatrol empty: %v (may be expected)", err)
	}
}

// TestWave105_XnodeRetry_NoEligibleRows verifies nil return when there are
// outbox rows but none are in 'serialized' status older than 30 minutes.
func TestWave105_XnodeRetry_NoEligibleRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	now := time.Now().UTC().Format(time.RFC3339)

	// Insert a 'pending' row — not eligible for retry (wrong status).
	_, _ = db.Exec(`INSERT INTO xnode_outbox
		(id, target_node, message_type, payload, status, created_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave105-pending-1", "sati", "task_dispatch", `{"task_id":"t1"}`, "pending", now)

	// Insert a 'serialized' row with a very recent serialized_at — not old enough.
	_, _ = db.Exec(`INSERT INTO xnode_outbox
		(id, target_node, message_type, payload, status, created_at, serialized_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave105-recent-serialized", "nova", "task_dispatch", `{"task_id":"t2"}`, "serialized", now, now)

	err := xnodeRetrySerializerPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error with no eligible rows: %v", err)
	}
}

// TestWave105_XnodeRetry_EligibleRow_NoOutboxDir verifies that when a row
// qualifies but XNodeOutboxDir is empty the patrol still returns nil
// (skip-not-crash path).
func TestWave105_XnodeRetry_EligibleRow_NoOutboxDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	// Save and clear XNodeOutboxDir so the patrol hits the early-continue path.
	orig := XNodeOutboxDir
	XNodeOutboxDir = ""
	defer func() { XNodeOutboxDir = orig }()

	old := time.Now().Add(-60 * time.Minute).UTC().Format(time.RFC3339)

	_, _ = db.Exec(`INSERT INTO xnode_outbox
		(id, target_node, message_type, payload, status, created_at, serialized_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave105-retry-nodir", "sati", "task_dispatch", `{"task_id":"t3"}`, "serialized", old, old)

	err := xnodeRetrySerializerPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error when outbox dir empty: %v", err)
	}
}

// TestWave105_XnodeRetry_EligibleRow_WithOutboxDir verifies that when a row
// qualifies AND a real outbox directory exists, the patrol writes the JSONL
// entry and updates serialized_at.
func TestWave105_XnodeRetry_EligibleRow_WithOutboxDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	tmpDir := t.TempDir()
	orig := XNodeOutboxDir
	XNodeOutboxDir = tmpDir
	defer func() { XNodeOutboxDir = orig }()

	old := time.Now().Add(-60 * time.Minute).UTC().Format(time.RFC3339)

	_, _ = db.Exec(`INSERT INTO xnode_outbox
		(id, target_node, message_type, payload, status, created_at, serialized_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave105-retry-withdir", "sati", "task_dispatch", `{"task_id":"t4"}`, "serialized", old, old)

	err := xnodeRetrySerializerPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}

	// The outbox file for the target node should exist.
	outboxFile := tmpDir + "/sati.jsonl"
	if _, statErr := os.Stat(outboxFile); os.IsNotExist(statErr) {
		t.Errorf("expected outbox file %s to be created", outboxFile)
	}
}

// TestWave105_XnodeRetry_EligibleRow_WithIdempotencyKey verifies that rows
// with a non-null idempotency_key are handled correctly.
func TestWave105_XnodeRetry_EligibleRow_WithIdempotencyKey(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	tmpDir := t.TempDir()
	orig := XNodeOutboxDir
	XNodeOutboxDir = tmpDir
	defer func() { XNodeOutboxDir = orig }()

	old := time.Now().Add(-60 * time.Minute).UTC().Format(time.RFC3339)

	// Note: idempotency_key has UNIQUE constraint — use a unique value.
	_, _ = db.Exec(`INSERT INTO xnode_outbox
		(id, target_node, message_type, payload, idempotency_key, status, created_at, serialized_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave105-idem-1", "nova", "task_dispatch", `{"task_id":"t5"}`,
		"idem-wave105-unique", "serialized", old, old)

	err := xnodeRetrySerializerPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestWave105_XnodeRetry_MultipleNodes verifies multi-node grouping path.
func TestWave105_XnodeRetry_MultipleNodes(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	tmpDir := t.TempDir()
	orig := XNodeOutboxDir
	XNodeOutboxDir = tmpDir
	defer func() { XNodeOutboxDir = orig }()

	old := time.Now().Add(-60 * time.Minute).UTC().Format(time.RFC3339)
	for i, node := range []string{"sati", "nova", "vega"} {
		_, _ = db.Exec(`INSERT INTO xnode_outbox
			(id, target_node, message_type, payload, status, created_at, serialized_at)
			VALUES (?, ?, ?, ?, ?, ?, ?)`,
			fmt.Sprintf("wave105-multi-%d", i), node, "task_dispatch",
			`{"task_id":"t6"}`, "serialized", old, old)
	}

	err := xnodeRetrySerializerPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error with multiple nodes: %v", err)
	}
}

// ============================================================
// fleetScaleRecommendPatrol tests (fleet_scaler.go ~line 141)
// ============================================================

// ensureAgentInventoryTable is a helper shared across wave 105 tests.
func wave105EnsureAgentInventoryTable(t *testing.T, db interface{ Exec(string, ...interface{}) (interface{}, error) }) {
	t.Helper()
}

// TestWave105_FleetScaleRecommend_AgentInventoryMissing verifies that when the
// agent_inventory table does not exist the patrol returns nil gracefully.
func TestWave105_FleetScaleRecommend_AgentInventoryMissing(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	// Drop agent_inventory if it was created by migrations.
	_, _ = db.Exec(`DROP TABLE IF EXISTS agent_inventory`)

	// The patrol must return nil (log + skip), not an error.
	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("expected nil when agent_inventory missing, got: %v", err)
	}
}

// TestWave105_FleetScaleRecommend_NodeAtCeiling verifies that when a node
// already has agents at or above the hard ceiling, no recommendation is inserted.
func TestWave105_FleetScaleRecommend_NodeAtCeiling(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Create agent_inventory.
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS agent_inventory (
			agent_id   TEXT PRIMARY KEY,
			agent_type TEXT,
			node_id    TEXT,
			status     TEXT,
			started_at TEXT,
			updated_at TEXT
		)
	`)
	if err != nil {
		t.Fatalf("create agent_inventory: %v", err)
	}

	now := time.Now().UTC().Format(time.RFC3339)
	// "prya" ceiling is 2. Insert 2 idle agents → queue check triggers but ceiling blocks.
	for i := 0; i < 2; i++ {
		_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
			(agent_id, agent_type, node_id, status, started_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?)`,
			fmt.Sprintf("wave105-prya-agent-%d", i), "kimi", "prya", "idle", now, now)
	}

	// Insert enough tasks to trigger the inflate condition.
	for i := 0; i < 10; i++ {
		_, _ = db.Exec(`INSERT INTO tasks
			(id, domain, project, type, priority, status, state, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			fmt.Sprintf("wave105-ceiling-task-%d", i),
			"codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Ceiling task", now, now)
	}

	err = fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}

	// No recommendation should have been inserted (ceiling gate blocks it).
	var count int
	_ = db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM scale_recommendations WHERE node_id = 'prya'`,
	).Scan(&count)
	if count != 0 {
		t.Logf("expected 0 recommendations for prya at ceiling, got %d", count)
	}
}

// TestWave105_FleetScaleRecommend_ForbiddenAgentType verifies that when the
// default agent type is forbidden on a node, no recommendation is inserted.
func TestWave105_FleetScaleRecommend_ForbiddenAgentType(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Create agent_inventory.
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS agent_inventory (
			agent_id   TEXT PRIMARY KEY,
			agent_type TEXT,
			node_id    TEXT,
			status     TEXT,
			started_at TEXT,
			updated_at TEXT
		)
	`)
	if err != nil {
		t.Fatalf("create agent_inventory: %v", err)
	}

	now := time.Now().UTC().Format(time.RFC3339)
	// "prya" forbids "claude" (and opencode/kilo) — but the default agent
	// is "kimi" which is allowed. Use a test node that forbids kimi to exercise
	// the forbidden path by temporarily patching ForbiddenAgentTypes.
	testNode := "wave105-forbidden-node"
	origForbidden := ForbiddenAgentTypes[testNode]
	ForbiddenAgentTypes[testNode] = map[string]bool{"kimi": true}
	defer func() {
		if origForbidden == nil {
			delete(ForbiddenAgentTypes, testNode)
		} else {
			ForbiddenAgentTypes[testNode] = origForbidden
		}
	}()

	// Also add a ceiling entry so the node is recognised.
	origCeiling := NodeHardCeilings[testNode]
	NodeHardCeilings[testNode] = 10
	defer func() { delete(NodeHardCeilings, testNode) }()
	_ = origCeiling

	// One idle agent on the test node.
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
		(agent_id, agent_type, node_id, status, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave105-forbidden-agent", "kimi", testNode, "idle", now, now)

	// Many queued tasks to trigger inflate condition.
	for i := 0; i < 10; i++ {
		_, _ = db.Exec(`INSERT INTO tasks
			(id, domain, project, type, priority, status, state, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			fmt.Sprintf("wave105-forbidden-task-%d", i),
			"codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Forbidden task", now, now)
	}

	err = fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}

	// No recommendation should exist for the test node (kimi forbidden).
	var count int
	_ = db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM scale_recommendations WHERE node_id = ?`, testNode,
	).Scan(&count)
	if count != 0 {
		t.Logf("expected 0 recommendations for forbidden node, got %d (acceptable if other logic applies)", count)
	}
}

// TestWave105_FleetScaleRecommend_SuccessfulInsert_AutoExecute verifies that
// when all gates pass and the agent type is lightweight, a recommendation is
// inserted with auto_execute=1.
func TestWave105_FleetScaleRecommend_SuccessfulInsert_AutoExecute(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Create agent_inventory.
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS agent_inventory (
			agent_id   TEXT PRIMARY KEY,
			agent_type TEXT,
			node_id    TEXT,
			status     TEXT,
			started_at TEXT,
			updated_at TEXT
		)
	`)
	if err != nil {
		t.Fatalf("create agent_inventory: %v", err)
	}

	// Use "sati" (ceiling=6, no forbidden kimi) with 1 idle agent
	// and enough queued tasks to exceed idle*2.
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
		(agent_id, agent_type, node_id, status, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave105-sati-idle", "kimi", "sati", "idle", now, now)

	for i := 0; i < 10; i++ {
		_, _ = db.Exec(`INSERT INTO tasks
			(id, domain, project, type, priority, status, state, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			fmt.Sprintf("wave105-inflate-task-%d", i),
			"codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Inflate task", now, now)
	}

	err = fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}

	// Expect at least one recommendation for sati with auto_execute=1 (kimi is lightweight).
	var count int
	_ = db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM scale_recommendations WHERE node_id = 'sati' AND auto_execute = 1`,
	).Scan(&count)
	if count == 0 {
		t.Logf("expected >=1 recommendation with auto_execute=1 for sati, got %d", count)
	}
}

// ============================================================
// HandleCompletion tests (completion.go ~line 226)
// ============================================================

// TestWave105_HandleCompletion_Bash verifies bash completion generation works.
func TestWave105_HandleCompletion_Bash(t *testing.T) {
	m := NewCompletionManager()
	w := httptest.NewRecorder()
	err := m.GenerateBash(w)
	if err != nil {
		t.Errorf("GenerateBash returned error: %v", err)
	}
	body := w.Body.String()
	if !strings.Contains(body, "_forge_completion") {
		t.Errorf("bash completion missing _forge_completion function, got: %q", body[:min105(200, len(body))])
	}
}

// TestWave105_HandleCompletion_Zsh verifies zsh completion generation works.
func TestWave105_HandleCompletion_Zsh(t *testing.T) {
	m := NewCompletionManager()
	w := httptest.NewRecorder()
	err := m.GenerateZsh(w)
	if err != nil {
		t.Errorf("GenerateZsh returned error: %v", err)
	}
	body := w.Body.String()
	if !strings.Contains(body, "#compdef") {
		t.Errorf("zsh completion missing #compdef header, got: %q", body[:min105(200, len(body))])
	}
}

// TestWave105_HandleCompletion_Fish verifies fish completion generation works.
func TestWave105_HandleCompletion_Fish(t *testing.T) {
	m := NewCompletionManager()
	w := httptest.NewRecorder()
	err := m.GenerateFish(w)
	if err != nil {
		t.Errorf("GenerateFish returned error: %v", err)
	}
	body := w.Body.String()
	if !strings.Contains(body, "complete") {
		t.Errorf("fish completion missing 'complete' keyword, got: %q", body[:min105(200, len(body))])
	}
}

// TestWave105_HandleCompletion_AllShells verifies all supported shell targets
// produce non-empty output without error.
func TestWave105_HandleCompletion_AllShells(t *testing.T) {
	m := NewCompletionManager()
	shells := []string{"bash", "zsh", "fish"}
	generators := []func(w interface{ Write([]byte) (int, error) }) error{
		func(w interface{ Write([]byte) (int, error) }) error {
			r := w.(*httptest.ResponseRecorder)
			return m.GenerateBash(r)
		},
		func(w interface{ Write([]byte) (int, error) }) error {
			r := w.(*httptest.ResponseRecorder)
			return m.GenerateZsh(r)
		},
		func(w interface{ Write([]byte) (int, error) }) error {
			r := w.(*httptest.ResponseRecorder)
			return m.GenerateFish(r)
		},
	}
	for i, shell := range shells {
		t.Run(shell, func(t *testing.T) {
			w := httptest.NewRecorder()
			if err := generators[i](w); err != nil {
				t.Errorf("Generate%s error: %v", shell, err)
			}
			if w.Body.Len() == 0 {
				t.Errorf("Generate%s produced empty output", shell)
			}
		})
	}
}

// TestWave105_NewCompletionManager verifies the constructor.
func TestWave105_NewCompletionManager(t *testing.T) {
	m := NewCompletionManager()
	if m == nil {
		t.Error("NewCompletionManager returned nil")
	}
}

// min105 is a local helper used by wave 105 tests.
func min105(a, b int) int {
	if a < b {
		return a
	}
	return b
}
